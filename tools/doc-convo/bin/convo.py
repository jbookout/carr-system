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

import os
import pathlib
import queue
import select
import signal
import subprocess
import sys
import termios
import threading
import time
import tty

import speak
from convo_core import (EARCON, MIN_BYTES, MODEL, SPEAK, TOOL, WHISPER,
                        ask_brain_streaming, mean_volume, pick_mic,
                        refresh_hot_context, transcribe, warm_voice)

LISTEN_ARM = 0.8    # ignore keys this long after listen starts (space autorepeat)


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


def main() -> int:
    warm_voice()
    for path, msg in [(pathlib.Path(WHISPER), "whisper-cli missing"),
                      (MODEL, "whisper model missing")]:
        if not path.exists():
            print(f"{msg}: {path}")
            return 2
    if not EARCON.exists():
        subprocess.run([str(TOOL / "bin" / "make-earcon.sh")], check=True)

    system_prompt = refresh_hot_context()
    if system_prompt is None:
        return 2

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

            text = transcribe(utt)
            if not text:
                print("· heard nothing")
                continue
            print(f"you: {text}")

            subprocess.Popen(
                [sys.executable, str(SPEAK), "--cache-only", "Checking the record."],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )

            speech_queue: queue.Queue = queue.Queue()
            speech_done = object()
            printed = False

            def speak_sentences() -> None:
                while True:
                    sentence = speech_queue.get()
                    if sentence is speech_done:
                        return
                    speak.stream(sentence)

            def on_sentence(sentence: str) -> None:
                nonlocal printed
                print(f"{'doc:' if not printed else '    '} {sentence}")
                if not printed:
                    print("· voice coming")
                    printed = True
                speech_queue.put(sentence)

            speech_thread = threading.Thread(target=speak_sentences, daemon=True)
            speech_thread.start()
            reply, brain = ask_brain_streaming(
                text, system_prompt, on_sentence=on_sentence,
            )
            speech_queue.put(speech_done)
            speech_thread.join()
            if brain.returncode != 0:
                print("· brain error:")
                print("\n".join(brain.stderr.strip().splitlines()[-3:]))
                continue
            for line in reply.splitlines():
                if line.startswith("CARD: "):
                    print(line)
    except KeyboardInterrupt:
        print()
    finally:
        subprocess.run(["rm", "-rf", str(workdir)])
    return 0


if __name__ == "__main__":
    sys.exit(main())
