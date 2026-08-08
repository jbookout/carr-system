#!/usr/bin/env python3
"""convo.py — the push-to-talk front end (loop #250). Pure stdlib.

Replaces the bash interaction after the first live run (2026-08-08): terminal
Enter autorepeat flooded `read`, stopping recordings after a split second.
Python gives raw single-key input with instant buffer draining, which bash 3.2
cannot do (no fractional read timeouts).

    SPACE (or Enter) = start talking · SPACE = done · q = leave

Pipeline unchanged: ffmpeg mic capture -> whisper + CARR vocab -> claude -p
resumed session on the hot-context snapshot -> speak.py tiers.
"""

import json
import os
import pathlib
import select
import signal
import subprocess
import sys
import termios
import time
import tty

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
BRAIN_MODEL = os.environ.get("DOC_BRAIN_MODEL", "sonnet")
MIN_BYTES = 20_000  # ~0.6s at 16kHz mono s16 — shorter is a misfire, not speech
LISTEN_ARM = 0.8    # ignore keys this long after listen starts (space autorepeat)


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


def drain_stdin() -> None:
    while select.select([sys.stdin], [], [], 0)[0]:
        os.read(sys.stdin.fileno(), 4096)


def read_key(prompt: str) -> str:
    """One raw keypress, buffer drained first so autorepeat can't skip turns."""
    print(prompt, end="", flush=True)
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        drain_stdin()
        key = os.read(fd, 1).decode(errors="replace")
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
    print()
    return key


def wait_stop_key(prompt: str) -> None:
    """Wait for the stop tap, IGNORING the held-key autorepeat flood from the
    start tap: every key inside LISTEN_ARM seconds is consumed and dropped, so
    only a deliberate second tap ends the recording."""
    print(prompt, end="", flush=True)
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    t0 = time.time()
    try:
        tty.setcbreak(fd)
        while True:
            if select.select([sys.stdin], [], [], 0.05)[0]:
                os.read(fd, 4096)
                if time.time() - t0 >= LISTEN_ARM:
                    return
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        print()


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


def main() -> int:
    for path, msg in [(pathlib.Path(WHISPER), "whisper-cli missing"),
                      (MODEL, "whisper model missing")]:
        if not path.exists():
            print(f"{msg}: {path}")
            return 2
    if not EARCON.exists():
        subprocess.run([str(TOOL / "bin" / "make-earcon.sh")], check=True)

    age = time.time() - CONTEXT.stat().st_mtime if CONTEXT.exists() else 1e9
    if age > 1800:
        print("· refreshing hot context ...")
        if subprocess.run([str(TOOL / "bin" / "refresh-context.sh")]).returncode != 0:
            print("· refresh failed — using last snapshot if any")
    if not CONTEXT.exists():
        print("no hot-context snapshot and refresh failed")
        return 2
    system_prompt = PREAMBLE.read_text() + "\n" + CONTEXT.read_text()

    mic = pick_mic()
    workdir = pathlib.Path("/tmp") / f"doc-convo.{os.getpid()}"
    print(f"Doc is at the desk (mic {mic}). Tap SPACE to talk, tap again when done, q to leave.")
    turn = 0
    try:
        while True:
            key = read_key("you [space] ")
            if key.lower() == "q":
                break
            turn += 1
            workdir.mkdir(parents=True, exist_ok=True)
            utt = workdir / f"utt-{turn}.wav"

            rec = subprocess.Popen(
                ["ffmpeg", "-y", "-loglevel", "error", "-f", "avfoundation",
                 "-i", mic, "-ac", "1", "-ar", "16000", str(utt)],
                stderr=subprocess.PIPE,
            )
            time.sleep(0.35)  # let the device open before claiming to listen
            wait_stop_key("· listening — tap [space] when done ")
            rec.send_signal(signal.SIGINT)
            try:
                rec.wait(timeout=5)
            except subprocess.TimeoutExpired:
                rec.kill()
            subprocess.Popen(["afplay", str(EARCON)])

            if not utt.exists() or utt.stat().st_size < MIN_BYTES:
                err = (rec.stderr.read().decode(errors="replace")[:300]
                       if rec.stderr else "")
                if err.strip():
                    print("· mic error — check System Settings → Privacy & Security")
                    print("  → Microphone → Terminal ON. Or wrong device: list with")
                    print('  ffmpeg -f avfoundation -list_devices true -i ""')
                    print("  then rerun as: DOC_MIC_DEVICE=':1' convo.sh")
                    print(f"  ({err.strip().splitlines()[0]})")
                else:
                    print("· too short — tap space once, talk, then tap it again")
                continue

            vol = mean_volume(utt)
            if vol is not None and vol < -45:
                print(f"· heard only silence (mic level {vol}dB) — right mic selected?")
                continue

            text = subprocess.run(
                [WHISPER, "-m", str(MODEL), "-f", str(utt),
                 "--prompt", VOCAB.read_text(), "-nt", "-np"],
                capture_output=True, text=True,
            ).stdout.replace("\n", " ").strip()
            if not text:
                print("· heard nothing")
                continue
            print(f"you: {text}")

            subprocess.Popen(
                [sys.executable, str(SPEAK), "--cache-only", "Checking the record."],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )

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
            if not reply:
                print("· brain error:")
                print("\n".join(brain.stderr.strip().splitlines()[-3:]))
                continue

            print(f"doc: {reply}")
            subprocess.run([sys.executable, str(SPEAK), reply])
    except KeyboardInterrupt:
        print()
    finally:
        subprocess.run(["rm", "-rf", str(workdir)])
    return 0


if __name__ == "__main__":
    sys.exit(main())
