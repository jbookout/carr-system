#!/usr/bin/env python3
"""The room-bridge unit suites, run where CI actually looks.

WHY THIS WRAPPER EXISTS. ops/ci.sh's gates class executes ops/*-selftest.py by
glob on every push; nothing anywhere ran tools/room-bridge/test_*_unit.py. That
made every control implemented in the room bridge unverifiable by the honest
standard ops/sync_control_catalog.py enforces: a control whose test nothing
runs is a control with no enforcement point (rule ab814a26 — recitation is not
enforcement). The first control that needed this was the delegation
model-and-effort deny gate (PR #556): dispatch refuses a delegation whose desk
does not name its specific model and reasoning effort. Its fixtures live in
tools/room-bridge/test_dispatch_unit.py; this file is how CI runs them.

NOT A SECOND HOME. The suites stay in tools/room-bridge/ next to the code they
test, runnable directly during development; this wrapper only subprocesses
them. Adding a new room-bridge unit suite means adding it to SUITES here, and
the missing-file refusal below makes a renamed suite a loud failure rather than
a silently shrunk test bed (rule a9ecd5b4 — a success signal must derive from
the artifact, and a missing artifact is not a passing one).

Live-socket and real-binary suites (test_*_live*.py) are deliberately absent:
they need a bound app server or the real codex binary, which the CI runner
does not have. The unit suites use stand-ins and run anywhere.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SUITES = (
    "tools/room-bridge/test_dispatch_unit.py",
    "tools/room-bridge/test_desk_start_unit.py",
    "tools/room-bridge/test_codex_live_unit.py",
    "tools/room-bridge/test_queue_unit.py",
    "tools/room-bridge/test_queue_projection_unit.py",
    "tools/room-bridge/test_queue_dispatch_unit.py",
)


def main() -> int:
    failed = []
    for rel in SUITES:
        path = REPO / rel
        if not path.is_file():
            print(f"FAIL  {rel}: suite file is MISSING — a renamed or deleted "
                  f"suite must be renamed here too, never dropped silently")
            failed.append(rel)
            continue
        r = subprocess.run([sys.executable, str(path)], cwd=REPO,
                           capture_output=True, text=True)
        tail = (r.stdout or r.stderr or "").strip().splitlines()
        last = tail[-1] if tail else "(no output)"
        if r.returncode == 0:
            print(f"PASS  {rel}: {last}")
        else:
            print(f"FAIL  {rel} (exit {r.returncode}): {last}")
            for line in tail[-15:-1]:
                print(f"      {line}")
            failed.append(rel)
    print(f"room-bridge-selftest: {len(SUITES) - len(failed)}/{len(SUITES)} suites passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
