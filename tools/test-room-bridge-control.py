#!/usr/bin/env python3
"""Unit proof for the observatory's RECONNECT control path — the allowlist that
decides whether the bridge may launch a vendor sign-in flow, and the sign-in
probe that tells the panel which desks are signed out (complete spec section
17).

WHY THIS IS THE MOST SECURITY-RELEVANT FILE IN THE BRIDGE. A control turn is
the only thing on the wire that causes the bridge to RUN something rather than
route a message. Four clauses stand between a turn and a launched process, and
each one gets its own assertion here because each one is the whole defence on
its own axis:

  action     only "login" is executable at all
  seat       only a person may command — a model seat echoing a control is not
             an instruction (the same judgment boundary grammar.py draws)
  sponsor    server-derived, so the panel cannot claim to be someone else
  desk       must already be registered on this machine
  throttle   one launch per desk per ten minutes, persisted across the
             poll-and-exit process boundary

Plus the two facts that make the feature honest rather than merely safe: an
unrecognised probe answer is UNKNOWN, never "signed out" (a missing CLI must
not paint the panel red), and a desk that signs in gets its PROCESS restarted,
because a running desk holds its token in memory.

No subprocess, no network, no live desk, no vendor CLI: every collaborator is
injected.

Run:  python3 tools/test-room-bridge-control.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "room-bridge"))

import auth_control  # noqa: E402
import bridge  # noqa: E402
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


DESKS = {
    "joe-desk": {"kind": "claude-session", "room_seat": "claude"},
    "codex-desk": {"kind": "codex-session", "room_seat": "codex"},
}
NOW = "2026-08-22T15:00:00+00:00"


class RecordingRoom:
    def __init__(self):
        self.calls = []

    def add_room_turn(self, *, body, seat, kind="turn", room="partner-line", msg_id=None):
        self.calls.append({"body": body, "seat": seat, "kind": kind})
        return {"ok": True, "seq": 800 + len(self.calls)}


class RecordingLauncher:
    def __init__(self, launched=True):
        self.calls = []
        self.launched = launched

    def __call__(self, name, entry):
        self.calls.append(name)
        return {"launched": self.launched, "command": "claude",
                "reason": None if self.launched else "could not start 'claude'"}


def control_turn(desk="joe-desk", action="login", seat="human", sponsor="joe", seq=100):
    return {"seq": seq, "seat": seat, "sponsor": sponsor, "kind": "receipt",
            "msg_id": f"ctl-{seq}",
            "body": json.dumps({"control": {"action": action, "desk": desk}})}


def run(turn, state=None, *, launcher=None, now=NOW):
    state = state if state is not None else state_mod.default_state()
    room = RecordingRoom()
    launcher = launcher or RecordingLauncher()
    control = auth_control.parse_control(turn)
    assert control is not None, "the fixture is not a control turn"
    result = bridge.handle_control(turn, control, DESKS, state,
                                   add_room_turn=room.add_room_turn, registry=None,
                                   now=now, launch=launcher)
    return result, room, launcher, state


# ------------------------------------------------------------------ shape

def only_a_receipt_carrying_a_control_object_is_a_control_turn():
    assert auth_control.parse_control(control_turn()) == {"action": "login", "desk": "joe-desk"}
    # an ordinary conversational turn, even one that TALKS about a control
    assert auth_control.parse_control(
        {"kind": "turn", "seat": "human", "body": '{"control":{"action":"login","desk":"joe-desk"}}'}) is None
    # a receipt that is not JSON, and a receipt with no control key
    assert auth_control.parse_control({"kind": "receipt", "body": "just words"}) is None
    assert auth_control.parse_control(
        {"kind": "receipt", "body": json.dumps({"assignment": {"ref": "WR-1"}})}) is None
    assert auth_control.parse_control(
        {"kind": "receipt", "body": json.dumps({"control": "login"})}) is None


# -------------------------------------------------------------- allowlist

def an_action_outside_the_allowlist_launches_nothing_and_says_why():
    for action in ("logout", "shell", "restart", "login ", "LOGIN", ""):
        result, room, launcher, _ = run(control_turn(action=action))
        assert result["outcome"] == "refused", (action, result)
        assert launcher.calls == [], (action, launcher.calls)
        assert len(room.calls) == 1 and room.calls[0]["kind"] == "receipt", room.calls
        refusal = json.loads(room.calls[0]["body"])["control_refused"]
        assert "not executable" in refusal["reason"], refusal


def a_model_seat_may_not_issue_a_control_even_under_a_real_partner():
    for seat in ("claude", "codex", "hermes", "grok", "opus"):
        result, room, launcher, _ = run(control_turn(seat=seat))
        assert result["outcome"] == "refused", (seat, result)
        assert launcher.calls == [], launcher.calls
        assert "only a person may" in json.loads(room.calls[0]["body"])["control_refused"]["reason"]


def a_sponsor_who_is_not_a_human_partner_is_refused():
    for sponsor in ("hermes", "shared", "", "joe-local", "someone-else"):
        result, room, launcher, _ = run(control_turn(sponsor=sponsor))
        assert result["outcome"] == "refused", (sponsor, result)
        assert launcher.calls == [], launcher.calls
        assert "not a human partner" in json.loads(room.calls[0]["body"])["control_refused"]["reason"]


def both_partners_are_authorized_because_the_room_is_shared():
    for sponsor in ("joe", "dell"):
        result, _room, launcher, _ = run(control_turn(sponsor=sponsor))
        assert result["outcome"] == "executed", (sponsor, result)
        assert launcher.calls == ["joe-desk"], launcher.calls


def an_unregistered_desk_can_never_be_named_into_existence():
    for desk in ("ghost-desk", "../etc", "joe-desk-2", ""):
        result, room, launcher, _ = run(control_turn(desk=desk))
        assert result["outcome"] == "refused", (desk, result)
        assert launcher.calls == [], launcher.calls
        assert "not registered" in json.loads(room.calls[0]["body"])["control_refused"]["reason"]


def a_second_click_inside_ten_minutes_is_throttled_and_says_so_on_the_wire():
    state = state_mod.default_state()
    first, _room, launcher, state = run(control_turn(seq=100), state, now=NOW)
    assert first["outcome"] == "executed", first
    assert launcher.calls == ["joe-desk"], launcher.calls

    again, room2, launcher2, state = run(control_turn(seq=101), state,
                                          now="2026-08-22T15:05:00+00:00")
    assert again["outcome"] == "refused", again
    assert launcher2.calls == [], launcher2.calls
    reason = json.loads(room2.calls[0]["body"])["control_refused"]["reason"]
    assert "at most one per 600s" in reason, reason

    # ten minutes later the same desk may be tried again
    later, _room3, launcher3, state = run(control_turn(seq=102), state,
                                           now="2026-08-22T15:11:00+00:00")
    assert later["outcome"] == "executed", later
    assert launcher3.calls == ["joe-desk"], launcher3.calls


def the_throttle_is_per_desk_not_global():
    state = state_mod.default_state()
    run(control_turn(desk="joe-desk", seq=100), state, now=NOW)
    other, _room, launcher, _ = run(control_turn(desk="codex-desk", seq=101), state, now=NOW)
    assert other["outcome"] == "executed", other
    assert launcher.calls == ["codex-desk"], launcher.calls


def the_throttle_survives_the_process_exiting_between_cycles():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "state.json"
        first = state_mod.load_state(path)
        run(control_turn(seq=100), first, now=NOW)
        state_mod.save_state(path, first)

        second = state_mod.load_state(path)
        outcome, reason = auth_control.classify_control(
            control_turn(seq=101), {"action": "login", "desk": "joe-desk"},
            registered=set(DESKS), state=second, now="2026-08-22T15:04:00+00:00")
        assert outcome == "refused" and "at most one per 600s" in reason, (outcome, reason)


def a_launch_that_fails_is_reported_and_does_not_burn_the_throttle():
    state = state_mod.default_state()
    result, room, _launcher, state = run(control_turn(), state,
                                          launcher=RecordingLauncher(launched=False))
    assert result["outcome"] == "launch_failed", result
    assert json.loads(room.calls[0]["body"])["control_refused"]["reason"], room.calls
    # nothing was recorded, so the human can press the button again immediately
    assert state.get("control_logins", {}) == {}, state
    assert auth_control.awaiting_login(state) == {}, state


# ---------------------------------------------------------------- restart

def a_desk_that_signs_in_after_a_launch_gets_its_process_restarted():
    state = state_mod.default_state()
    run(control_turn(), state, now=NOW)
    assert "joe-desk" in auth_control.awaiting_login(state), state

    room = RecordingRoom()
    stopped, started = [], []
    # still signed out on the next cycle: nothing restarts, the desk keeps waiting
    settled = bridge.settle_restarts(DESKS, {"joe-desk": False}, state,
                                      add_room_turn=room.add_room_turn, registry=None,
                                      stop=stopped.append,
                                      start=lambda n, registry=None: started.append(n))
    assert settled == [] and stopped == [] and started == [], (settled, stopped, started)
    assert "joe-desk" in auth_control.awaiting_login(state), state

    # the human approves in the browser; the probe flips, and the process cycles
    settled = bridge.settle_restarts(DESKS, {"joe-desk": True}, state,
                                      add_room_turn=room.add_room_turn, registry=None,
                                      stop=stopped.append,
                                      start=lambda n, registry=None: started.append(n))
    assert [s["desk"] for s in settled] == ["joe-desk"], settled
    assert stopped == ["joe-desk"] and started == ["joe-desk"], (stopped, started)
    assert auth_control.awaiting_login(state) == {}, state
    receipt = json.loads(room.calls[-1]["body"])["desk_restarted"]
    assert receipt["desk"] == "joe-desk" and receipt["restarted"] is True, receipt


def a_desk_never_awaiting_a_login_is_never_restarted_by_a_passing_probe():
    state = state_mod.default_state()
    room = RecordingRoom()
    stopped = []
    settled = bridge.settle_restarts(DESKS, {"joe-desk": True, "codex-desk": True}, state,
                                      add_room_turn=room.add_room_turn, registry=None,
                                      stop=stopped.append, start=lambda n, registry=None: None)
    assert settled == [] and stopped == [] and room.calls == [], (settled, stopped, room.calls)


# ------------------------------------------------------------------ probe

def an_unreadable_probe_answer_is_unknown_and_never_signed_out():
    assert auth_control.parse_auth_output("") is None
    assert auth_control.parse_auth_output("some future output nobody has seen") is None
    # a missing binary is "not possible", not "not authorized" (rule 88e9b5eb)
    def explode(*a, **k):
        raise FileNotFoundError("claude")
    assert auth_control.probe_auth({"kind": "claude-session"}, run=explode) is None
    # a desk kind with no known status command answers unknown rather than guessing
    assert auth_control.probe_auth({"kind": "some-new-kind"}) is None


def the_probe_reads_both_json_and_prose_and_never_confuses_the_two():
    assert auth_control.parse_auth_output('{"loggedIn":true}') is True
    assert auth_control.parse_auth_output('{"loggedIn":false}') is False
    assert auth_control.parse_auth_output("Logged in as joe@example.com") is True
    # the trap: "not logged in" contains "logged in"
    assert auth_control.parse_auth_output("You are not logged in.") is False
    assert auth_control.parse_auth_output("Please run `claude auth login` first") is False

    class Proc:
        stdout = '{"loggedIn": false}'
        stderr = ""
    assert auth_control.probe_auth({"kind": "claude-session"}, run=lambda *a, **k: Proc()) is False


def the_bridge_never_has_a_channel_a_credential_could_travel_down():
    # The launcher is handed a desk name and its registry entry, and returns a
    # dict with no output fields: launch_login does not capture stdout at all.
    source = (HERE / "room-bridge" / "auth_control.py").read_text()
    launch = source.split("def launch_login", 1)[1].split("\ndef ", 1)[0]
    for forbidden in ("capture_output", "stdout", "communicate", "input=", "password", "token"):
        assert forbidden not in launch, f"launch_login must not mention {forbidden!r}"


def main() -> int:
    check("only a receipt carrying a control object is a control turn",
          only_a_receipt_carrying_a_control_object_is_a_control_turn)
    check("an action outside the allowlist launches nothing and says why",
          an_action_outside_the_allowlist_launches_nothing_and_says_why)
    check("a model seat may not issue a control, even under a real partner",
          a_model_seat_may_not_issue_a_control_even_under_a_real_partner)
    check("a sponsor who is not a human partner is refused",
          a_sponsor_who_is_not_a_human_partner_is_refused)
    check("both partners are authorized, because the room is shared",
          both_partners_are_authorized_because_the_room_is_shared)
    check("an unregistered desk can never be named into existence",
          an_unregistered_desk_can_never_be_named_into_existence)
    check("a second click inside ten minutes is throttled and says so on the wire",
          a_second_click_inside_ten_minutes_is_throttled_and_says_so_on_the_wire)
    check("the throttle is per desk, not global", the_throttle_is_per_desk_not_global)
    check("the throttle survives the process exiting between cycles",
          the_throttle_survives_the_process_exiting_between_cycles)
    check("a launch that fails is reported and does not burn the throttle",
          a_launch_that_fails_is_reported_and_does_not_burn_the_throttle)
    check("a desk that signs in after a launch gets its process restarted",
          a_desk_that_signs_in_after_a_launch_gets_its_process_restarted)
    check("a desk never awaiting a login is never restarted by a passing probe",
          a_desk_never_awaiting_a_login_is_never_restarted_by_a_passing_probe)
    check("an unreadable probe answer is unknown, never signed out",
          an_unreadable_probe_answer_is_unknown_and_never_signed_out)
    check("the probe reads both JSON and prose and never confuses the two",
          the_probe_reads_both_json_and_prose_and_never_confuses_the_two)
    check("the sign-in launcher has no channel a credential could travel down",
          the_bridge_never_has_a_channel_a_credential_could_travel_down)

    print()
    if FAILURES:
        print(f"room-bridge control path: {len(FAILURES)} FAILED")
        return 1
    print("room-bridge control path: DONE — every assertion held")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
