#!/usr/bin/env python3
"""Contract tests for state.route_turn's desk-to-desk loop guard.

Found live 2026-09-24: with two conversational desks seated (flash and codex),
every desk reply was routed to the other desk, whose reply was routed back, about
once a minute ("No further response." / "No action taken.") until one seat was
stopped. A desk's turn now reaches another desk only when it names that desk's
seat with an @-mention; turns from people still fan out to every desk.
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import state as state_mod  # noqa: E402

SEATS = {"flash": "flash", "codex-desk": "codex"}


def turn(msg_id, seat, body="hello"):
    return {"kind": "turn", "msg_id": msg_id, "seat": seat, "body": body, "seq": 1}


def test_human_turn_fans_out_to_every_desk():
    st = state_mod.default_state()
    assert sorted(state_mod.route_turn(st, turn("m1", "joe"), SEATS)) == ["codex-desk", "flash"]


def test_desk_reply_does_not_reach_other_desk():
    st = state_mod.default_state()
    assert state_mod.route_turn(st, turn("m2", "codex", "No action taken."), SEATS) == []
    assert state_mod.route_turn(st, turn("m3", "flash", "No further response."), SEATS) == []


def test_ping_pong_cannot_start():
    """Simulate the live loop: each desk's reply becomes the next room turn."""
    st = state_mod.default_state()
    routed = state_mod.route_turn(st, turn("h1", "joe"), SEATS)
    replies = [turn(f"r-{name}", SEATS[name], "ack") for name in routed]
    second_hop = [d for r in replies for d in state_mod.route_turn(st, r, SEATS)]
    assert second_hop == []


def test_explicit_mention_still_reaches_named_desk_only():
    st = state_mod.default_state()
    assert state_mod.route_turn(st, turn("m4", "codex", "@flash please review"), SEATS) == ["flash"]


def test_mention_must_be_a_whole_seat_name():
    st = state_mod.default_state()
    assert state_mod.route_turn(st, turn("m5", "codex", "@flashy thing"), SEATS) == []


def test_own_echo_still_suppressed():
    st = state_mod.default_state()
    assert state_mod.route_turn(st, turn("m6", "flash", "@flash note to self"), SEATS) == []


def main() -> int:
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"ok   {name}")
            except AssertionError as exc:
                failures += 1
                print(f"FAIL {name}: {exc!r}", file=sys.stderr)
    if failures:
        print(f"{failures} route_turn test(s) failed", file=sys.stderr)
        return 1
    print("all route_turn unit tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
