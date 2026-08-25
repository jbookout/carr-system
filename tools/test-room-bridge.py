#!/usr/bin/env python3
"""CI entry point for the room-bridge's own offline suites (dispatch, desk
start/stop, and the codex-live wire protocol), promoted here from
spikes/hermes-dispatch/. Those suites live under tools/room-bridge/ rather
than directly in tools/ (they are a matched set with the modules they test,
and a subdirectory keeps them out of ops/ci.sh's flat `tools/test-*.py` glob
by construction) — this file is the one thing directly in tools/ that runs
them, so CI actually exercises what it did not before the promotion: none of
spikes/hermes-dispatch/test_*_unit.py sat under any path ops/ci.sh globs.

Deliberately excludes test_claude_desk_live.py and test_codex_live_live.py —
both need a real, running Claude Code session or Codex app-server and are for
a human to run by hand (see their own docstrings), never for an unattended
CI runner.

Seq persistence, echo suppression, the assignment grammar, and the full
poll-cycle orchestration have their own top-level suites (tools/test-room-
bridge-state.py, -grammar.py, -cycle.py) — this file only re-homes what the
spike already proved.

Run:  python3 tools/test-room-bridge.py
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "room-bridge"))

import test_dispatch_unit  # noqa: E402
import test_desk_start_unit  # noqa: E402
import test_codex_live_unit  # noqa: E402
import test_queue_dispatch_unit  # noqa: E402
import test_queue_unit  # noqa: E402

SUITES = [
    ("dispatch (hermes desk wire)", test_dispatch_unit.main),
    ("desk start/stop lifecycle", test_desk_start_unit.main),
    ("codex-live wire protocol", test_codex_live_unit.main),
    ("Observatory queue ingress", test_queue_unit.main),
    ("Observatory queue dispatch", test_queue_dispatch_unit.main),
]


def main() -> int:
    failed = []
    for name, fn in SUITES:
        print(f"--- {name} ---")
        rc = fn()
        print()
        if rc != 0:
            failed.append(name)
    if failed:
        print(f"room-bridge promoted suites: {len(failed)} FAILED: {', '.join(failed)}")
        return 1
    print(f"room-bridge promoted suites: all {len(SUITES)} passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
