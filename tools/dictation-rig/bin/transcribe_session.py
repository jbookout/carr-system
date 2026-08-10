#!/usr/bin/env python3
"""
transcribe_session.py — CARR dictation rig transcription step (Phase A).

Spec: specs/dictation-rig-build-plan-2026-08-07.md, Phase A step 5.

Invoked by transcribe-session.sh with one argument, the quill session
directory (e.g. ~/Recordings/2026.08.07-1430/), which holds:

    mic.caf       the user's mic (AAC-in-CAF, mono)
    system.caf    system audio = the other side of a call
    meta.json     {"started", "ended", "duration_seconds",
                   "files": {"mic", "system"},
                   "start_offset_ms": {"mic", "system"}}
    announcement.json   optional, written by consent-watch.sh:
                   {"announcement_fired_at", "clip", "played_by"}
    call-context.json   optional, written by Deal Room Call Mode. Carries
                   mic/system speaker labels based on separate audio channels,
                   never a third-party voiceprint.

For each track present, this script:
  1. Converts CAF -> 16kHz mono WAV via /usr/bin/afconvert into a temp dir.
  2. Runs /opt/homebrew/bin/whisper-cli against the WAV with the CARR
     vocabulary prompt (tools/dictation-rig/vocab-prompt.txt) and JSON output.
  3. Shifts every segment's offsets by that track's start_offset_ms so both
     tracks land on one shared clock, and tags the track's speaker.
Segments from both tracks are then merged, sorted by start time, and written
to the session directory as transcript.json (machine-readable) and
transcript.md (a readable "[m:ss] speaker: text" render).

A missing track file is tolerated: whatever tracks exist get transcribed,
and the gap is logged and noted in transcript.md. A missing model falls back
from CARR_WHISPER_MODEL (or the large-v3-turbo default) to ggml-small.en.bin,
with a logged warning. Every step, including any failure, is appended to
<session_dir>/transcribe.log — this script is a leaf of the on_stop hook
chain and must never crash without a trace.

Pure standard library by design (repo convention, see requirements.txt):
no third-party imports, so no venv is needed to run it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, TypedDict

# --- constants ---------------------------------------------------------

AFCONVERT_BIN = "/usr/bin/afconvert"
WHISPER_CLI_BIN = "/opt/homebrew/bin/whisper-cli"

DEFAULT_MODEL_PATH = Path(
    "/Users/booko/.cache/whisper-cpp/models/ggml-large-v3-turbo.bin"
)
FALLBACK_MODEL_PATH = Path(
    "/Users/booko/.cache/whisper-cpp/models/ggml-small.en.bin"
)

ENGINE_LABEL = "whisper.cpp large-v3-turbo (carr dictation-rig)"

DEFAULT_SPEAKER_LABELS: dict[str, str] = {"mic": "Me", "system": "Other participant"}

LogFunc = Callable[[str], None]


# --- typed shapes --------------------------------------------------------


class FilesMeta(TypedDict):
    mic: str
    system: str


class MetaJson(TypedDict):
    started: str
    ended: str
    duration_seconds: int
    files: FilesMeta
    start_offset_ms: dict[str, int]


class Segment(TypedDict):
    start_ms: int
    end_ms: int
    speaker: str
    text: str


class TranscriptJson(TypedDict):
    engine: str
    model: str
    vocab_prompt: bool
    generated: str
    consent: Any
    speaker_labels: dict[str, str]
    speaker_method: str
    segments: list[Segment]


@dataclass(frozen=True)
class RawSegment:
    """One whisper-cli segment, offsets still relative to its own track."""

    start_ms: int
    end_ms: int
    text: str


class TranscribeError(Exception):
    """A recoverable per-track failure (bad audio, whisper-cli error, ...)."""


# --- logging ---------------------------------------------------------------


class Logger:
    """Appends timestamped lines to <session_dir>/transcribe.log."""

    def __init__(self, log_path: Path) -> None:
        self._log_path = log_path

    def __call__(self, message: str) -> None:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        line = f"{ts} {message}\n"
        try:
            with self._log_path.open("a", encoding="utf-8") as f:
                f.write(line)
        except OSError:
            pass
        sys.stderr.write(line)


# --- meta.json / announcement.json ------------------------------------


def read_meta(session_dir: Path) -> MetaJson:
    meta_path = session_dir / "meta.json"
    with meta_path.open("r", encoding="utf-8") as f:
        raw: dict[str, Any] = json.load(f)

    files_raw: dict[str, Any] = raw.get("files", {})
    offsets_raw: dict[str, Any] = raw.get("start_offset_ms", {})

    files: FilesMeta = {
        "mic": str(files_raw.get("mic", "mic.caf")),
        "system": str(files_raw.get("system", "system.caf")),
    }
    start_offset_ms: dict[str, int] = {
        str(key): int(value) for key, value in offsets_raw.items()
    }

    meta: MetaJson = {
        "started": str(raw.get("started", "")),
        "ended": str(raw.get("ended", "")),
        "duration_seconds": int(raw.get("duration_seconds", 0)),
        "files": files,
        "start_offset_ms": start_offset_ms,
    }
    return meta


def read_consent(session_dir: Path) -> Any:
    """Contents of announcement.json if present, else None. Untyped by
    design: the consent-watch.sh schema is owned elsewhere, and this script
    only needs to pass it through (transcript.json) and read one field back
    out of it for the markdown header."""
    announcement_path = session_dir / "announcement.json"
    if not announcement_path.exists():
        return None
    try:
        with announcement_path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def read_speaker_labels(session_dir: Path) -> dict[str, str]:
    """Read the channel labels Call Mode resolved at recording start.

    The only accepted keys are the two physical capture channels. Missing or
    malformed context falls back to human-readable generic labels. This is
    channel attribution, not biometric speaker recognition.
    """
    context_path = session_dir / "call-context.json"
    try:
        context = json.loads(context_path.read_text(encoding="utf-8"))
        raw = context.get("speaker_labels", {}) if isinstance(context, dict) else {}
    except (OSError, json.JSONDecodeError):
        raw = {}
    labels = dict(DEFAULT_SPEAKER_LABELS)
    for channel in ("mic", "system"):
        value = raw.get(channel)
        if isinstance(value, str) and value.strip() and len(value.strip()) <= 80:
            labels[channel] = value.strip()
    return labels


# --- model + prompt resolution ------------------------------------------


def resolve_model(log: LogFunc) -> Path:
    env_value = os.environ.get("CARR_WHISPER_MODEL")
    candidate = Path(env_value) if env_value else DEFAULT_MODEL_PATH
    if candidate.exists():
        return candidate
    log(
        f"WARN model not found at {candidate}, falling back to {FALLBACK_MODEL_PATH}"
    )
    if FALLBACK_MODEL_PATH.exists():
        return FALLBACK_MODEL_PATH
    raise TranscribeError(
        f"no whisper model found: tried {candidate} and {FALLBACK_MODEL_PATH}"
    )


def load_prompt() -> str:
    prompt_path = Path(__file__).resolve().parent.parent / "vocab-prompt.txt"
    return prompt_path.read_text(encoding="utf-8").strip()


# --- audio pipeline ------------------------------------------------------


def convert_caf_to_wav(caf_path: Path, out_dir: Path, log: LogFunc) -> Path:
    wav_path = out_dir / (caf_path.stem + ".wav")
    cmd = [
        AFCONVERT_BIN,
        "-f",
        "WAVE",
        "-d",
        "LEI16@16000",
        "-c",
        "1",
        str(caf_path),
        str(wav_path),
    ]
    log(f"CONVERT {caf_path.name} -> {wav_path.name}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0 or not wav_path.exists():
        raise TranscribeError(
            f"afconvert failed for {caf_path.name} (rc={result.returncode}): "
            f"{result.stderr.strip()[-500:]}"
        )
    return wav_path


def parse_whisper_json(json_path: Path) -> list[RawSegment]:
    with json_path.open("r", encoding="utf-8") as f:
        data: dict[str, Any] = json.load(f)

    raw_segments: list[RawSegment] = []
    for item in data.get("transcription", []):
        offsets: dict[str, Any] = item.get("offsets", {})
        text = str(item.get("text", "")).strip()
        if not text:
            continue
        raw_segments.append(
            RawSegment(
                start_ms=int(offsets.get("from", 0)),
                end_ms=int(offsets.get("to", 0)),
                text=text,
            )
        )
    return raw_segments


def run_whisper(
    wav_path: Path, model_path: Path, prompt: str, out_dir: Path, log: LogFunc
) -> list[RawSegment]:
    out_stem = out_dir / wav_path.stem
    cmd = [
        WHISPER_CLI_BIN,
        "-m",
        str(model_path),
        "--prompt",
        prompt,
        "-oj",
        "-of",
        str(out_stem),
        "-f",
        str(wav_path),
    ]
    log(f"WHISPER {wav_path.name} model={model_path.name}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    json_path = out_stem.with_suffix(".json")
    if result.returncode != 0 or not json_path.exists():
        raise TranscribeError(
            f"whisper-cli failed for {wav_path.name} (rc={result.returncode}): "
            f"{result.stderr.strip()[-500:]}"
        )
    return parse_whisper_json(json_path)


def shift_and_tag(
    raw_segments: list[RawSegment], offset_ms: int, speaker: str
) -> list[Segment]:
    return [
        {
            "start_ms": raw.start_ms + offset_ms,
            "end_ms": raw.end_ms + offset_ms,
            "speaker": speaker,
            "text": raw.text,
        }
        for raw in raw_segments
    ]


# --- rendering -------------------------------------------------------------


def format_mmss(ms: int) -> str:
    total_seconds = max(ms, 0) // 1000
    minutes, seconds = divmod(total_seconds, 60)
    return f"{minutes}:{seconds:02d}"


def consent_line(consent: Any) -> str:
    if isinstance(consent, dict) and consent.get("announcement_fired_at"):
        return f"Recording announcement fired at {consent['announcement_fired_at']}"
    if consent is not None:
        return "Recording announcement fired (announcement.json present, no timestamp field)"
    return "WARNING: no announcement record"


def render_markdown(
    meta: MetaJson,
    consent: Any,
    segments: list[Segment],
    missing_tracks: list[str],
) -> str:
    lines: list[str] = []
    lines.append(f"# CARR dictation-rig transcript — {meta['started']}")
    lines.append("")
    lines.append(f"Duration: {meta['duration_seconds']}s")
    lines.append(consent_line(consent))
    if missing_tracks:
        lines.append(
            "Note: missing track(s): "
            + ", ".join(missing_tracks)
            + " — transcribed the available audio only."
        )
    lines.append("")
    for seg in segments:
        lines.append(f"[{format_mmss(seg['start_ms'])}] {seg['speaker']}: {seg['text']}")
    lines.append("")
    return "\n".join(lines)


def write_outputs(session_dir: Path, transcript: TranscriptJson, markdown: str) -> None:
    json_path = session_dir / "transcript.json"
    markdown_path = session_dir / "transcript.md"
    json_path.write_text(
        json.dumps(transcript, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    markdown_path.write_text(markdown, encoding="utf-8")
    json_path.chmod(0o600)
    markdown_path.chmod(0o600)


# --- orchestration -------------------------------------------------------


def process_session(session_dir: Path, log: LogFunc) -> None:
    log(f"START transcribe_session.py session_dir={session_dir}")

    meta = read_meta(session_dir)
    consent = read_consent(session_dir)
    speaker_labels = read_speaker_labels(session_dir)
    model_path = resolve_model(log)
    prompt = load_prompt()

    all_segments: list[Segment] = []
    missing_tracks: list[str] = []

    with tempfile.TemporaryDirectory(prefix="carr-transcribe-") as tmp_str:
        tmp_dir = Path(tmp_str)
        for label in ("mic", "system"):
            speaker = speaker_labels[label]
            filename = meta["files"]["mic"] if label == "mic" else meta["files"]["system"]
            caf_path = session_dir / filename
            offset_ms = meta["start_offset_ms"].get(label, 0)

            if not caf_path.exists():
                log(f"WARN missing track file: {caf_path.name} (label={label})")
                missing_tracks.append(label)
                continue

            try:
                wav_path = convert_caf_to_wav(caf_path, tmp_dir, log)
                raw_segments = run_whisper(wav_path, model_path, prompt, tmp_dir, log)
            except TranscribeError as exc:
                log(f"ERROR track {label} failed: {exc}")
                missing_tracks.append(label)
                continue

            tagged = shift_and_tag(raw_segments, offset_ms, speaker)
            log(f"OK track {label}: {len(tagged)} segment(s), offset_ms={offset_ms}")
            all_segments.extend(tagged)

    merged_segments = sorted(all_segments, key=lambda seg: seg["start_ms"])

    transcript: TranscriptJson = {
        "engine": ENGINE_LABEL,
        "model": str(model_path),
        "vocab_prompt": True,
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "consent": consent,
        "speaker_labels": speaker_labels,
        "speaker_method": "separate audio channels; no third-party voiceprint",
        "segments": merged_segments,
    }
    markdown = render_markdown(meta, consent, merged_segments, missing_tracks)
    write_outputs(session_dir, transcript, markdown)

    log(
        f"FINISH transcribe_session.py segments={len(merged_segments)} "
        f"missing_tracks={missing_tracks or 'none'}"
    )


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        sys.stderr.write("usage: transcribe_session.py <session_dir>\n")
        return 2

    session_dir = Path(argv[1]).resolve()
    session_dir.mkdir(parents=True, exist_ok=True)
    log = Logger(session_dir / "transcribe.log")

    try:
        process_session(session_dir, log)
    except Exception as exc:  # top-level guard: never crash without a trace
        log(f"ERROR fatal: {type(exc).__name__}: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
