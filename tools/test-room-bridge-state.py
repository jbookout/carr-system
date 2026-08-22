#!/usr/bin/env python3
"""Unit proof for tools/room-bridge/state.py — seq persistence and echo
suppression, the two things the brief calls out by name. Pure logic, no
socket, no network, no live desk.

Run:  python3 tools/test-room-bridge-state.py
Exit 0 = every assertion held. Picked up automatically by ops/ci.sh's
`tools/test-*.py` glob.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "room-bridge"))

import state as state_mod  # noqa: E402

FAILURES: list[str] = []


def check(label: str, fn) -> None:
    try:
        fn()
    except AssertionError as e:
        FAILURES.append(f"{label}: {e}")
        print(f"  FAIL  {label}\n          {e}")
    except Exception as e:  # noqa: BLE001
        FAILURES.append(f"{label}: unexpected {e!r}")
        print(f"  FAIL  {label}\n          unexpected {e!r}")
    else:
        print(f"  ok    {label}")


def turn(seq: int, seat: str, body: str = "hello", msg_id: str | None = None) -> dict:
    return {"seq": seq, "seat": seat, "body": body, "sponsor": "joe",
            "msg_id": msg_id or f"msg-{seq}", "kind": "turn"}


# ---------------------------------------------------------------------------
# seq persistence
# ---------------------------------------------------------------------------

def default_state_is_seq_zero():
    s = state_mod.default_state()
    assert s["last_seq"] == 0, s


check("a fresh state starts at seq 0", default_state_is_seq_zero)


def advance_seq_takes_the_max():
    s = state_mod.default_state()
    got = state_mod.advance_seq(s, [turn(3, "human"), turn(7, "claude"), turn(5, "codex")])
    assert got == 7, got
    assert s["last_seq"] == 7, s


check("advance_seq moves the cursor to the highest seq seen", advance_seq_takes_the_max)


def advance_seq_never_goes_backward():
    s = state_mod.default_state()
    state_mod.advance_seq(s, [turn(10, "human")])
    state_mod.advance_seq(s, [turn(4, "human")])  # a stale/replayed batch
    assert s["last_seq"] == 10, s["last_seq"]


check("advance_seq never rewinds the cursor", advance_seq_never_goes_backward)


def advance_seq_with_no_turns_is_a_no_op():
    s = state_mod.default_state()
    s["last_seq"] = 42
    state_mod.advance_seq(s, [])
    assert s["last_seq"] == 42, s["last_seq"]


check("advance_seq with an empty batch leaves the cursor alone",
      advance_seq_with_no_turns_is_a_no_op)


def state_round_trips_through_disk():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "state.json"
        s = state_mod.default_state()
        state_mod.advance_seq(s, [turn(99, "human")])
        state_mod.mark_delivered(s, "joe-desk", "msg-99")
        state_mod.save_state(path, s)
        reloaded = state_mod.load_state(path)
        assert reloaded["last_seq"] == 99, reloaded
        assert "msg-99" in reloaded["desks"]["joe-desk"]["delivered"], reloaded


check("state survives a save/load round trip, seq and delivered set intact",
      state_round_trips_through_disk)


def missing_state_file_reads_as_default():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "does-not-exist.json"
        s = state_mod.load_state(path)
        assert s == state_mod.default_state(), s


check("a state file that has never been written reads as the honest default",
      missing_state_file_reads_as_default)


def corrupt_state_file_reads_as_default_not_a_crash():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "state.json"
        path.write_text("{not json")
        s = state_mod.load_state(path)
        assert s == state_mod.default_state(), s


check("a corrupt state file is treated as absent, never a crash",
      corrupt_state_file_reads_as_default_not_a_crash)


def delivered_set_is_capped():
    s = state_mod.default_state()
    for i in range(state_mod.DELIVERED_CAP + 50):
        state_mod.mark_delivered(s, "joe-desk", f"msg-{i}")
    delivered = s["desks"]["joe-desk"]["delivered"]
    assert len(delivered) == state_mod.DELIVERED_CAP, len(delivered)
    assert f"msg-{state_mod.DELIVERED_CAP + 49}" in delivered  # the newest survives
    assert "msg-0" not in delivered  # the oldest was trimmed


check("the per-desk delivered set is bounded, newest kept", delivered_set_is_capped)


# ---------------------------------------------------------------------------
# echo suppression by msg_id + seat
# ---------------------------------------------------------------------------

def a_desks_own_seat_is_never_routed_to_it():
    assert state_mod.is_echo(turn(1, "claude"), "claude") is True
    assert state_mod.is_echo(turn(1, "codex"), "claude") is False


check("is_echo: a turn is only its own desk's echo when seats match", a_desks_own_seat_is_never_routed_to_it)


def route_turn_skips_the_authoring_seat():
    s = state_mod.default_state()
    t = turn(1, "claude")
    onto = state_mod.route_turn(s, t, {"joe-desk": "claude", "codex-desk": "codex"})
    assert onto == ["codex-desk"], onto
    assert s["desks"]["codex-desk"]["queue"] == [
        {"msg_id": "msg-1", "seat": "claude", "body": "hello", "seq": 1}
    ], s["desks"]["codex-desk"]["queue"]
    # the authoring desk never even gets a slot created for it — nothing was
    # ever queued onto it, so there is nothing to assert about its "queue"
    assert "joe-desk" not in s["desks"] or s["desks"]["joe-desk"]["queue"] == []


check("route_turn queues onto every desk except the one that spoke it",
      route_turn_skips_the_authoring_seat)


def route_turn_never_delivers_the_same_msg_id_twice_to_the_same_desk():
    s = state_mod.default_state()
    t = turn(1, "human", msg_id="dup-1")
    seats = {"joe-desk": "claude"}
    first = state_mod.route_turn(s, t, seats)
    second = state_mod.route_turn(s, t, seats)  # e.g. a retried/replayed read-room page
    assert first == ["joe-desk"], first
    assert second == [], second
    assert len(s["desks"]["joe-desk"]["queue"]) == 1


check("route_turn never re-queues a msg_id already delivered to a desk",
      route_turn_never_delivers_the_same_msg_id_twice_to_the_same_desk)


def route_turn_treats_a_desks_own_reply_as_its_own_echo_even_later():
    # The bridge posts a desk's captured reply back under that desk's own
    # seat. When the room is re-read, that reply must never be routed back
    # to the very desk that said it.
    s = state_mod.default_state()
    reply = turn(2, "claude", body="here is my answer", msg_id="reply-1")
    onto = state_mod.route_turn(s, reply, {"joe-desk": "claude", "codex-desk": "codex"})
    assert "joe-desk" not in onto, onto
    assert onto == ["codex-desk"], onto


check("a desk's own captured reply is never routed back to itself",
      route_turn_treats_a_desks_own_reply_as_its_own_echo_even_later)


def desks_with_no_room_seat_are_simply_not_offered_a_turn():
    s = state_mod.default_state()
    t = turn(1, "human")
    onto = state_mod.route_turn(s, t, {})  # bridge.run_once builds this dict from
                                            # only the desks that HAVE a room_seat
    assert onto == [], onto


check("a turn with no eligible desks queues onto nothing (never an error)",
      desks_with_no_room_seat_are_simply_not_offered_a_turn)


def system_and_receipt_turns_are_never_routed_to_a_desk():
    # Found live 2026-08-22: a kind=receipt turn posted under seat="hermes"
    # (nobody's room_seat) used to route to every desk exactly like an
    # ordinary turn — including the desk the receipt was reporting a failure
    # ABOUT, which then burned a real dispatch "answering" a report of its
    # own prior failure.
    s = state_mod.default_state()
    seats = {"joe-desk": "claude", "codex-desk": "codex"}
    receipt = turn(1, "hermes", body="{\"desk\":\"codex-desk\",\"status\":\"failed\"}",
                   msg_id="r1")
    receipt["kind"] = "receipt"
    system_row = turn(2, "hermes", body="board operational", msg_id="s1")
    system_row["kind"] = "system"
    assert state_mod.route_turn(s, receipt, seats) == []
    assert state_mod.route_turn(s, system_row, seats) == []
    assert s["desks"] == {}, s["desks"]


check("kind=receipt and kind=system turns are never queued onto a desk",
      system_and_receipt_turns_are_never_routed_to_a_desk)


# ---------------------------------------------------------------------------
# pending (in-flight reply) tracking
# ---------------------------------------------------------------------------

def pending_lifecycle():
    s = state_mod.default_state()
    assert state_mod.has_pending(s, "joe-desk") is False
    state_mod.set_pending(s, "joe-desk", dispatch_msg_id="d1", log_offset=120,
                          injected_at="2026-08-22T00:00:00+00:00",
                          source_msg_id="m1", source_seq=1)
    assert state_mod.has_pending(s, "joe-desk") is True
    got = state_mod.get_pending(s, "joe-desk")
    assert got["log_offset"] == 120, got
    state_mod.clear_pending(s, "joe-desk")
    assert state_mod.has_pending(s, "joe-desk") is False


check("pending set/get/clear round-trips cleanly", pending_lifecycle)


def a_desk_with_pending_is_not_offered_the_next_queued_turn_by_pop_alone():
    # pop_next_queued always pops — it is bridge.run_once's job to check
    # has_pending() FIRST and only pop when it is False. This test pins the
    # contract of pop_next_queued itself: FIFO, and None on empty.
    s = state_mod.default_state()
    state_mod.route_turn(s, turn(1, "human", msg_id="a"), {"joe-desk": "claude"})
    state_mod.route_turn(s, turn(2, "human", msg_id="b"), {"joe-desk": "claude"})
    first = state_mod.pop_next_queued(s, "joe-desk")
    second = state_mod.pop_next_queued(s, "joe-desk")
    third = state_mod.pop_next_queued(s, "joe-desk")
    assert first["msg_id"] == "a", first
    assert second["msg_id"] == "b", second
    assert third is None, third


check("pop_next_queued is FIFO and returns None once drained",
      a_desk_with_pending_is_not_offered_the_next_queued_turn_by_pop_alone)


print()
if FAILURES:
    print(f"room-bridge state unit: {len(FAILURES)} FAILED")
    sys.exit(1)
print("room-bridge state unit: DONE — every assertion held")
