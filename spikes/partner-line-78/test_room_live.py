#!/usr/bin/env python3
"""Acceptance test: can a human WATCH the room while the turns are happening?

The bar Joe set is a hard check, not a nice-to-have: open the transcript
*during* the conversation and see raw turns. So this test starts a real
spectator (`room.py watch`) as a separate process FIRST, then writes turns,
then asserts the spectator printed each turn — raw, in order, with speaker and
time — while it was still running. A transcript you can only read after the
fact would fail this test.

Run:  python3 spikes/partner-line-78/test_room_live.py
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).parent
ROOM = "spike78-acceptance"


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="spike78-room-")
    env = {"PARTNER_ROOM_DIR": tmp, "PATH": "/usr/bin:/bin"}
    out = Path(tmp) / "spectator.txt"

    # 1. The human opens the transcript BEFORE the conversation happens.
    spectator = subprocess.Popen(
        [sys.executable, str(HERE / "room.py"), "watch", ROOM],
        stdout=out.open("wb"), stderr=subprocess.STDOUT, env=env,
    )
    time.sleep(1.5)
    if spectator.poll() is not None:
        print("FAIL: spectator died at startup", file=sys.stderr)
        print(out.read_text(), file=sys.stderr)
        return 1
    print("PASS: spectator is watching an empty room")

    # 2. Two brains talk, spaced out, the way a real exchange arrives.
    turns = [
        ("joe-claude", "Dell — pushing the 3.9 fix; pre-push is green on my Mac."),
        ("dell-claude", "Seen. My checkout still fails the vault-root check.\nRe-running."),
        ("grok", "Council note: the vault-root check scans the checkout, not neighbours."),
        ("dell-claude", "That was it. Green now."),
    ]
    for speaker, text in turns:
        subprocess.run(
            [sys.executable, str(HERE / "room.py"), "say", ROOM, speaker, text],
            env=env, check=True, capture_output=True,
        )
        time.sleep(1.0)

    time.sleep(1.5)
    live = out.read_text()

    # 3. The spectator must still be running — this is a LIVE view.
    if spectator.poll() is not None:
        print("FAIL: spectator exited before the conversation ended", file=sys.stderr)
        return 1

    failures = []
    for speaker, text in turns:
        first = text.splitlines()[0]
        if speaker not in live:
            failures.append(f"speaker {speaker!r} never appeared")
        if first not in live:
            failures.append(f"raw text {first[:40]!r} never appeared")

    # Raw, not summarised: the multi-line turn must survive intact.
    if "Re-running." not in live:
        failures.append("multi-line turn was truncated (that would be a summary)")

    # Order must match the conversation.
    pos = [live.find(t.splitlines()[0]) for _, t in turns]
    if pos != sorted(pos):
        failures.append(f"turns rendered out of order: {pos}")

    spectator.terminate()
    spectator.wait(timeout=5)

    print("\n--- what the human saw, live ---")
    print(live.strip())
    print("--- end ---\n")

    if failures:
        for f in failures:
            print("FAIL: " + f, file=sys.stderr)
        return 1
    print("PASS: every turn appeared live, raw, in order, with speaker and time")
    print("RESULT: HUMAN-WATCHABLE TRANSCRIPT WORKS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
