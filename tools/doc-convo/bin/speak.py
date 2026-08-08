#!/usr/bin/env python3
"""speak.py — Doc's mouth, three tiers (loop #250). Pure stdlib on purpose,
same rule as the dictation rig: plain python3 must always be enough to RUN the
loop; only rendering NEW audio needs the heavy .venv-tts.

    speak.py "text"            speak (cache -> live render -> text-only)
    speak.py --cache-only "t"  exit 3 instead of rendering on a cache miss

Tiers:
  1. cache  assets/phrases/<sha1>.wav — pre-rendered frozen voice, instant.
  2. live   .venv-tts present: prefer the warm daemon, fall back to
            render_phrase.py, then master, cache, play. The caller prints text
            BEFORE this.
  3. none   no venv: print-only marker on stderr, exit 3. Conversation
            continues in text; nothing pretends to have spoken.

Speakable forms (doc-voice/RECIPE.md) are applied to the RENDER text only;
the cache key uses the normalized form so both spellings hit one file.
"""

import hashlib
import json
import pathlib
import socket
import subprocess
import sys

TOOL = pathlib.Path(__file__).resolve().parent.parent
PHRASES = TOOL / "assets" / "phrases"
VENV_PY = TOOL / ".venv-tts" / "bin" / "python"
RENDERER = pathlib.Path(__file__).resolve().parent / "render_phrase.py"
RENDER_SOCKET = TOOL / "assets" / ".render.sock"

# doc-voice/RECIPE.md — the frozen mastering chain, verbatim. Runs at render
# time; the cached wav is already mastered.
MASTER_CHAIN = (
    "asetrate=24000*0.97,aresample=24000,atempo=0.965,highpass=f=90,"
    "lowpass=f=7400,aresample=16000,aresample=24000,"
    "acrusher=bits=14:mode=log:mix=0.08,equalizer=f=185:t=q:w=1.4:g=6.5,"
    "equalizer=f=4400:t=q:w=1.2:g=3,"
    "acompressor=threshold=-16dB:ratio=2.4:attack=8:release=115:makeup=2dB,"
    "aecho=0.75:0.23:21|36:0.10|0.07,alimiter=limit=0.9"
)

SPEAKABLE = [("real estate", "realestate"), ("CARR", "car")]


def normalize(text: str) -> str:
    out = " ".join(text.split())
    for src, dst in SPEAKABLE:
        out = out.replace(src, dst)
    return out


def play(wav: pathlib.Path) -> None:
    subprocess.run(["afplay", str(wav)], check=False)


def render_live(spoken: str, raw: pathlib.Path) -> bool:
    if not RENDER_SOCKET.exists():
        return False
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(120)
            client.connect(str(RENDER_SOCKET))
            request = {"text": spoken, "out": str(raw.resolve())}
            client.sendall(json.dumps(request).encode("utf-8"))
            client.shutdown(socket.SHUT_WR)
            chunks = []
            while True:
                chunk = client.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
        reply = json.loads(b"".join(chunks).decode("utf-8"))
        return reply.get("ok") is True and raw.exists()
    except (OSError, ValueError, TypeError, AttributeError):
        return False


def main() -> int:
    args = sys.argv[1:]
    cache_only = "--cache-only" in args
    text = " ".join(a for a in args if a != "--cache-only").strip()
    if not text:
        return 0
    spoken = normalize(text)
    PHRASES.mkdir(parents=True, exist_ok=True)
    wav = PHRASES / (hashlib.sha1(spoken.encode()).hexdigest()[:16] + ".wav")

    if wav.exists():
        play(wav)
        return 0
    if cache_only:
        return 3
    if not VENV_PY.exists():
        print("speak: no TTS env (.venv-tts) — text-only", file=sys.stderr)
        return 3

    raw = wav.with_suffix(".raw.wav")
    if not render_live(spoken, raw):
        r = subprocess.run(
            [str(VENV_PY), str(RENDERER), spoken, str(raw)],
            capture_output=True, text=True,
        )
        if r.returncode != 0 or not raw.exists():
            print(f"speak: render failed — text-only\n{r.stderr[-500:]}", file=sys.stderr)
            return 3
    m = subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(raw),
         "-af", MASTER_CHAIN, "-ar", "24000", "-ac", "1", str(wav)],
        capture_output=True, text=True,
    )
    raw.unlink(missing_ok=True)
    if m.returncode != 0 or not wav.exists():
        print(f"speak: mastering failed — text-only\n{m.stderr[-300:]}", file=sys.stderr)
        return 3
    play(wav)
    return 0


if __name__ == "__main__":
    sys.exit(main())
