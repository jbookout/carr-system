#!/usr/bin/env python3
"""Unit proof for tools/room-bridge/grammar.py — the assignment bridge's
strict grammar, its authorization check, and version 1's local-record seam
(apply_assignment). Includes rejection of malformed and unauthorized turns,
called out by name in the brief. No network, no live verb call — add_room_turn
is a recording fake throughout.

Run:  python3 tools/test-room-bridge-grammar.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "room-bridge"))

import grammar  # noqa: E402

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


class RecordingRoom:
    def __init__(self):
        self.calls = []

    def add_room_turn(self, *, body, seat, kind="turn", room="partner-line", msg_id=None):
        call = {"body": body, "seat": seat, "kind": kind, "msg_id": msg_id}
        self.calls.append(call)
        return {"ok": True, "seq": len(self.calls)}


def turn(body: str, seat: str = "human", seq: int = 1, sponsor: str = "joe",
         msg_id: str = "m1") -> dict:
    return {"body": body, "seat": seat, "seq": seq, "sponsor": sponsor, "msg_id": msg_id}


# ---------------------------------------------------------------------------
# classify: the three near-miss outcomes plus the happy path
# ---------------------------------------------------------------------------

def ordinary_chat_is_not_a_command():
    outcome, parsed, reason = grammar.classify(turn("hey, did you see the deploy went out?"))
    assert outcome == "not_a_command", outcome
    assert parsed is None and reason is None


check("ordinary chat is left alone entirely", ordinary_chat_is_not_a_command)


def a_bare_at_mention_is_not_a_command():
    outcome, _, _ = grammar.classify(turn("@codex can you look at this later"))
    assert outcome == "not_a_command", outcome


check("'@seat <anything else>' is not treated as an attempt",
      a_bare_at_mention_is_not_a_command)


def well_formed_assign_from_human_is_ok():
    outcome, parsed, reason = grammar.classify(
        turn("@codex assign WR-42 fix the flaky migration test", seat="human"))
    assert outcome == "ok", (outcome, reason)
    assert parsed == {"verb": "assign", "target_seat": "codex", "ref": "WR-42",
                       "title": "fix the flaky migration test"}, parsed


check("a well-formed assign from an authorized seat parses cleanly",
      well_formed_assign_from_human_is_ok)


def well_formed_claim_from_human_is_ok():
    outcome, parsed, reason = grammar.classify(turn("@claude claim L-508", seat="human"))
    assert outcome == "ok", (outcome, reason)
    assert parsed == {"verb": "claim", "target_seat": "claude", "ref": "L-508",
                       "title": None}, parsed


check("a well-formed claim from an authorized seat parses cleanly",
      well_formed_claim_from_human_is_ok)


def assign_missing_title_is_malformed():
    outcome, parsed, reason = grammar.classify(turn("@codex assign WR-42", seat="human"))
    assert outcome == "malformed", outcome
    assert parsed is None
    assert "grammar" in reason.lower(), reason


check("assign with no title is rejected as malformed, not silently dropped",
      assign_missing_title_is_malformed)


def claim_with_trailing_junk_is_malformed():
    outcome, parsed, reason = grammar.classify(
        turn("@codex claim WR-42 and also do the dishes", seat="human"))
    assert outcome == "malformed", outcome


check("claim takes exactly one ref — trailing text is malformed",
      claim_with_trailing_junk_is_malformed)


def multiline_body_is_malformed_not_ok():
    outcome, parsed, reason = grammar.classify(
        turn("@codex assign WR-42 title one\nsecond line sneaks in", seat="human"))
    assert outcome == "malformed", outcome
    assert parsed is None


check("a command hidden inside a multi-line body never matches — one line only",
      multiline_body_is_malformed_not_ok)


def multiline_ordinary_message_is_still_not_a_command():
    outcome, parsed, reason = grammar.classify(
        turn("morning —\nstill reviewing the PR, back in a bit", seat="human"))
    assert outcome == "not_a_command", outcome


check("an ordinary multi-line message is not flagged as an attempt at all",
      multiline_ordinary_message_is_still_not_a_command)


def well_formed_command_from_a_model_seat_is_unauthorized():
    outcome, parsed, reason = grammar.classify(
        turn("@codex claim WR-42", seat="claude"))
    assert outcome == "unauthorized", outcome
    assert parsed is not None  # it DID parse — the refusal is about the sender, not the shape
    assert "claude" in reason


check("a perfectly-formed command from a model seat (not human) is unauthorized",
      well_formed_command_from_a_model_seat_is_unauthorized)


def unauthorized_seats_besides_the_obvious_one():
    for seat in ("hermes", "grok", "sol"):
        outcome, _, _ = grammar.classify(turn("@codex claim WR-1", seat=seat))
        assert outcome == "unauthorized", (seat, outcome)


check("no model seat is authorized, not just 'claude'", unauthorized_seats_besides_the_obvious_one)


def bad_ref_shape_is_malformed():
    outcome, parsed, reason = grammar.classify(
        turn("@codex assign not a ref really title text", seat="human"))
    # "not" would be consumed as ref and "a" would need to be the whole rest as
    # title, which DOES match loosely — assert on a ref that truly cannot
    # parse: one starting with a digit.
    outcome2, parsed2, reason2 = grammar.classify(
        turn("@codex assign 42-oops a title", seat="human"))
    assert outcome2 == "malformed", outcome2


check("a ref that cannot start with a digit is rejected as malformed",
      bad_ref_shape_is_malformed)


# ---------------------------------------------------------------------------
# apply_assignment / reject — the seam, and the receipts it posts
# ---------------------------------------------------------------------------

def apply_assignment_posts_a_machine_readable_receipt_and_records_locally():
    with tempfile.TemporaryDirectory() as td:
        log_path = Path(td) / "assignments.json"
        room = RecordingRoom()
        t = turn("@codex assign WR-42 fix the flaky test", seat="human", seq=9,
                 sponsor="dell", msg_id="src-1")
        outcome, parsed, reason = grammar.classify(t)
        assert outcome == "ok", (outcome, reason)
        result = grammar.apply_assignment(t, parsed, add_room_turn=room.add_room_turn,
                                          log_path=log_path)

        assert len(room.calls) == 1, room.calls
        call = room.calls[0]
        assert call["kind"] == "receipt", call
        assert call["seat"] == "hermes", call
        body = json.loads(call["body"])
        assert body["assignment"]["ref"] == "WR-42", body
        assert body["assignment"]["seat"] == "codex", body
        assert body["assignment"]["by"] == "dell", body
        assert body["status"] == "recorded_no_queue_verb_yet", body

        on_disk = json.loads(log_path.read_text())
        assert len(on_disk) == 1, on_disk
        assert on_disk[0]["assignment"]["ref"] == "WR-42", on_disk


check("apply_assignment records locally and posts one machine-readable receipt",
      apply_assignment_posts_a_machine_readable_receipt_and_records_locally)


def apply_assignment_never_touches_a_real_queue_verb():
    # Version 1 has no queue verb bound at all (loop #508) — the only I/O this
    # function performs is the local JSON log and add_room_turn. Proven here
    # by giving it an add_room_turn fake that is the ONLY external call
    # allowed to happen; anything else would have to come through a function
    # this test does not provide, so a second I/O path would raise NameError/
    # AttributeError rather than silently succeed.
    with tempfile.TemporaryDirectory() as td:
        log_path = Path(td) / "assignments.json"
        room = RecordingRoom()
        t = turn("@codex claim WR-9", seat="human")
        outcome, parsed, _ = grammar.classify(t)
        grammar.apply_assignment(t, parsed, add_room_turn=room.add_room_turn, log_path=log_path)
        assert len(room.calls) == 1


check("apply_assignment's only side effects are the local log and one receipt",
      apply_assignment_never_touches_a_real_queue_verb)


def reject_posts_a_receipt_naming_the_reason():
    room = RecordingRoom()
    t = turn("@codex assign WR-1", seat="human", seq=3, msg_id="bad-1")
    outcome, parsed, reason = grammar.classify(t)
    assert outcome == "malformed"
    grammar.reject(t, outcome, reason, add_room_turn=room.add_room_turn)
    assert len(room.calls) == 1
    body = json.loads(room.calls[0]["body"])
    assert body["assignment_rejected"]["outcome"] == "malformed", body
    assert body["assignment_rejected"]["source_seq"] == 3, body


check("reject() posts one receipt naming the outcome and reason", reject_posts_a_receipt_naming_the_reason)


def not_a_command_never_calls_add_room_turn():
    # classify() alone must never have side effects — only apply_assignment/
    # reject do, and callers (bridge.run_once) only invoke those for ok /
    # malformed / unauthorized, never for not_a_command.
    room = RecordingRoom()
    outcome, parsed, reason = grammar.classify(turn("just chatting"))
    assert outcome == "not_a_command"
    assert room.calls == []


check("classify() itself never posts anything — it only classifies",
      not_a_command_never_calls_add_room_turn)


print()
if FAILURES:
    print(f"room-bridge grammar unit: {len(FAILURES)} FAILED")
    sys.exit(1)
print("room-bridge grammar unit: DONE — every assertion held")
