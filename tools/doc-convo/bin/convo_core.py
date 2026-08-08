#!/usr/bin/env python3
"""Shared conversation pipeline for the terminal loop and panel engine."""

import json
import os
import pathlib
import socket as socketlib
import subprocess
import time

TOOL = pathlib.Path(__file__).resolve().parent.parent
RIG = TOOL.parent / "dictation-rig"
WHISPER = "/opt/homebrew/bin/whisper-cli"
MODEL = pathlib.Path.home() / ".cache/whisper-cpp/models/ggml-large-v3-turbo.bin"
VOCAB = RIG / "vocab-prompt.txt"
EARCON = TOOL / "assets" / "earcon-ack.wav"
CONTEXT = TOOL / "assets" / "hot-context.md"
PREAMBLE = TOOL / "prompt" / "preamble.md"
SESSION_FILE = TOOL / "assets" / ".brain-session-id"
SPEAK = TOOL / "bin" / "speak.py"
VENV_PY = TOOL / ".venv-tts" / "bin" / "python"
RENDER_DAEMON = TOOL / "bin" / "render-daemon.py"
RENDER_SOCKET = TOOL / "assets" / ".render.sock"
RENDER_LOG = TOOL / "assets" / "render-daemon.log"
BRAIN_MODEL = os.environ.get("DOC_BRAIN_MODEL", "sonnet")
MIN_BYTES = 20_000  # ~0.6s at 16kHz mono s16 — shorter is a misfire, not speech


def pick_mic() -> str:
    """Find the real microphone by name. Device :0 is NOT safe as a default —
    on Joe's Mac it's "Microsoft Teams Audio", a virtual device that records
    pure silence outside a call (found live, 2026-08-08, -91dB)."""
    if "DOC_MIC_DEVICE" in os.environ:
        return os.environ["DOC_MIC_DEVICE"]
    out = subprocess.run(
        ["ffmpeg", "-f", "avfoundation", "-list_devices", "true", "-i", ""],
        capture_output=True, text=True,
    ).stderr
    devices = []
    in_audio = False
    for line in out.splitlines():
        if "audio devices" in line:
            in_audio = True
            continue
        if in_audio and "] [" in line:
            idx = line.split("] [")[1].split("]")[0]
            name = line.rsplit("]", 1)[1].strip()
            devices.append((idx, name))
    for idx, name in devices:  # the built-in mic is the desk default
        if "MacBook" in name and "Microphone" in name:
            return f":{idx}"
    for idx, name in devices:  # else: any real microphone, never virtual audio
        if "Microphone" in name and "Teams" not in name:
            return f":{idx}"
    return ":0"


def mean_volume(wav: pathlib.Path) -> float | None:
    out = subprocess.run(
        ["ffmpeg", "-i", str(wav), "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, text=True,
    ).stderr
    for line in out.splitlines():
        if "mean_volume:" in line:
            try:
                return float(line.split("mean_volume:")[1].split("dB")[0])
            except ValueError:
                return None
    return None


def warm_voice() -> None:
    if not VENV_PY.exists():
        return
    if RENDER_SOCKET.exists():
        # A socket FILE is not a live daemon (crash leaves it behind, and then
        # nothing would ever respawn). Probe; unlink the corpse on refusal.
        try:
            with socketlib.socket(socketlib.AF_UNIX, socketlib.SOCK_STREAM) as s:
                s.settimeout(1)
                s.connect(str(RENDER_SOCKET))
            return  # alive — nothing to do
        except OSError:
            RENDER_SOCKET.unlink(missing_ok=True)
    RENDER_LOG.parent.mkdir(parents=True, exist_ok=True)
    with RENDER_LOG.open("a") as log:
        subprocess.Popen(
            [str(VENV_PY), str(RENDER_DAEMON)],
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    print("· warming Doc's voice (background)")


def refresh_hot_context() -> str | None:
    age = time.time() - CONTEXT.stat().st_mtime if CONTEXT.exists() else 1e9
    if age > 1800:
        print("· refreshing hot context ...")
        if subprocess.run([str(TOOL / "bin" / "refresh-context.sh")]).returncode != 0:
            print("· refresh failed — using last snapshot if any")
    if not CONTEXT.exists():
        print("no hot-context snapshot and refresh failed")
        return None
    return PREAMBLE.read_text() + "\n" + CONTEXT.read_text()


def transcribe(wav: pathlib.Path) -> str:
    return subprocess.run(
        [WHISPER, "-m", str(MODEL), "-f", str(wav),
         "--prompt", VOCAB.read_text(), "-nt", "-np"],
        capture_output=True, text=True,
    ).stdout.replace("\n", " ").strip()


def ask_brain(text: str, system_prompt: str) -> tuple[str, subprocess.CompletedProcess]:
    cmd = ["claude", "-p", text, "--model", BRAIN_MODEL,
           "--append-system-prompt", system_prompt,
           "--output-format", "json"]
    if SESSION_FILE.exists():
        cmd += ["--resume", SESSION_FILE.read_text().strip()]
    brain = subprocess.run(cmd, capture_output=True, text=True)
    reply, sid = "", ""
    try:
        data = json.loads(brain.stdout)
        reply = (data.get("result") or "").strip()
        sid = data.get("session_id") or ""
    except json.JSONDecodeError:
        pass
    if sid:
        SESSION_FILE.write_text(sid)
    return reply, brain
