#!/usr/bin/env python3
"""bridge.py — the partner room's live wire to local desks.

ONE POLL CYCLE, RUN PERIODICALLY BY LAUNCHD. This is the house pattern every
other unattended service in this repo already uses (StartInterval + a short,
bounded invocation — see ops/config/services.json's capture-poll/partner-ping
entries and bin/run-scheduled.sh's own header) rather than a hand-rolled
always-on daemon: launchd IS the supervisor, each firing is one receipted
"run", and a desk's asynchronous reply is carried across firings in the state
file (state.py) rather than by holding a process open between polls. This is
what "supervised daemon loop" means here — the loop is the repeated firing,
supervised by launchd, not a long-lived process this file manages itself.

WHAT ONE CYCLE DOES, run_once() below, in order:
  1. read-room since the stored cursor (verb_io.read_room, through
     tools/call-verb.py — the deployed Worker, never a direct DB connection).
  2. For every new turn: queue it onto every LOCAL DESK that has a room_seat
     registered and whose seat did not author the turn (state.route_turn —
     echo suppression by msg_id + seat). Also run the assignment grammar
     (grammar.classify) on it, independently of desk routing.
  3. For every desk with a room_seat: if it has a turn in flight
     (state.get_pending), check whether it has answered yet — a claude-
     session desk answers asynchronously into its own log (dispatch.py's own
     words: "the desk answers in its own window, and this file does not carry
     that back"), so this scans the log for the next `type:"result"` line
     past the byte offset recorded before injection. A codex-session desk's
     dispatch is already synchronous (dispatch.dispatch() blocks and returns
     the answer directly), so it never has a pending state — deliver and
     reply-capture happen in the same call. Either way, a captured reply is
     posted back with add-room-turn under the DESK'S seat, with a fresh
     msg_id (never the msg_id it replied to — a fresh id is what makes the
     reply itself a new, dedup-able room turn other desks can be routed).
  4. If a desk has no turn in flight and something queued, deliver the oldest
     queued turn (dispatch.dispatch(), reused unmodified from the promoted
     spike — this file does not reimplement the wire).
  5. Stamp every seated desk's heartbeat (registry_ext.stamp_heartbeat) —
     "checked this cycle, found it live/dead" — regardless of whether it had
     traffic, so a desk that goes quiet is visible within one polling
     interval instead of only the next time work happens to route to it.

CONSENT POSTURE (decision 351b9995) IS UNCHANGED BY THIS FILE. Every turn this
bridge injects lands through dispatch.py's claude_wire, exactly the socket
protocol a live `claude --messaging-socket-path` session already renders as a
visible, abortable cross-session message — nothing here adds a second,
silent channel, and nothing here can suppress the human's own Ctrl-C/abort in
that session. If an injected turn is aborted, no `type:"result"` line for it
ever appears, and PENDING_TIMEOUT_S below is what keeps the queue moving
instead of waiting on it forever.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import desks  # noqa: E402
import dispatch  # noqa: E402
import grammar  # noqa: E402
import registry_ext  # noqa: E402
import state as state_mod  # noqa: E402
import verb_io  # noqa: E402

DEFAULT_STATE = Path(
    os.environ.get("CARR_ROOM_BRIDGE_STATE",
                    Path.home() / ".config" / "carr" / "room-bridge-state.json")
)
DEFAULT_ROOM = os.environ.get("CARR_ROOM_BRIDGE_ROOM", "partner-line")
PENDING_TIMEOUT_S = float(os.environ.get("CARR_ROOM_BRIDGE_PENDING_TIMEOUT", "1800"))
READ_LIMIT = int(os.environ.get("CARR_ROOM_BRIDGE_READ_LIMIT", "50"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _elapsed_seconds(iso_ts: str | None, *, now: str | None = None) -> float:
    if iso_ts is None:
        return 0.0
    try:
        t = datetime.fromisoformat(iso_ts)
    except (ValueError, TypeError):
        return 0.0
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    ref = datetime.fromisoformat(now) if now else datetime.now(timezone.utc)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=timezone.utc)
    return (ref - t).total_seconds()


def format_for_desk(turn: dict) -> str:
    """"[partner-room · claude] <body>" — mirrors partner-line/watch.py's
    format_body(), so a human watching a desk's own window can see who a
    routed turn is from without opening the room itself."""
    seat = turn.get("seat") or "?"
    body = turn.get("body") or ""
    return f"[partner-room · {seat}] {body}"


def scan_for_result(log_path: Path, offset: int) -> str | None:
    """The first `type:"result"` event's `result` field appearing after
    `offset` in a claude-session desk's own --output-format stream-json log —
    the final answer text for one completed turn. Returns None if nothing
    complete has landed yet; never raises on a partial trailing line (it just
    fails json.loads and is skipped, and gets re-read whole next cycle since
    `offset` is not advanced until a result is actually found)."""
    if not log_path.exists():
        return None
    with log_path.open("rb") as fh:
        fh.seek(offset)
        data = fh.read()
    if not data:
        return None
    for raw in data.decode("utf-8", errors="replace").split("\n"):
        line = raw.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("type") == "result":
            return obj.get("result")
    return None


def probe_live(entry: dict) -> bool:
    kind = entry.get("kind")
    if kind in ("claude-session", "codex-live"):
        return desks.is_live(entry.get("socket", ""))
    # codex-session is durable rather than live (dispatch.py's own framing) —
    # there is no process to probe between dispatches, so "live" here means
    # "usable", which delivery itself is what actually proves each cycle.
    return True


def handle_pending(name: str, seat: str, state: dict, *, add_room_turn,
                    log_path: Path, pending_timeout_s: float,
                    scan=scan_for_result, now: str | None = None) -> dict | None:
    pending = state_mod.get_pending(state, name)
    if pending is None:
        return None
    result_text = scan(log_path, pending["log_offset"])
    if result_text is not None:
        add_room_turn(body=result_text.strip() or "(empty reply)", seat=seat, kind="turn",
                      msg_id=str(uuid.uuid4()))
        state_mod.clear_pending(state, name)
        return {"desk": name, "outcome": "replied"}
    if _elapsed_seconds(pending.get("injected_at"), now=now) > pending_timeout_s:
        add_room_turn(
            body=json.dumps({
                "desk": name, "timed_out_after_s": pending_timeout_s,
                "source_msg_id": pending.get("source_msg_id"),
            }, separators=(",", ":")),
            seat="hermes", kind="receipt", msg_id=str(uuid.uuid4()),
        )
        state_mod.clear_pending(state, name)
        return {"desk": name, "outcome": "timed_out"}
    return {"desk": name, "outcome": "still_pending"}


def deliver(name: str, entry: dict, seat: str, queued_turn: dict, *, state: dict,
            registry, results_path: Path, add_room_turn, dispatch_fn=dispatch.dispatch,
            desk_state_dir: Path = dispatch.DESK_STATE) -> dict:
    text = format_for_desk(queued_turn)
    kind = entry.get("kind")

    if kind == "claude-session":
        log_path = desk_state_dir / f"{name}.log"
        offset = log_path.stat().st_size if log_path.exists() else 0
        row = dispatch_fn(name, text, registry=registry, results_path=results_path)
        state_mod.set_pending(
            state, name, dispatch_msg_id=row["msg_id"], log_offset=offset,
            injected_at=row["dispatched_at"], source_msg_id=queued_turn["msg_id"],
            source_seq=queued_turn["seq"],
        )
        return {"desk": name, "outcome": "delivered_async"}

    if kind in ("codex-session", "codex-live"):
        row = dispatch_fn(name, text, registry=registry, results_path=results_path)
        status = row.get("status")
        if status == "completed":
            add_room_turn(body=(row.get("result") or "").strip() or "(empty reply)",
                          seat=seat, kind="turn", msg_id=str(uuid.uuid4()))
            return {"desk": name, "outcome": "replied_sync"}
        add_room_turn(
            body=json.dumps({"desk": name, "status": status,
                             "detail": row.get("detail")}, separators=(",", ":")),
            seat="hermes", kind="receipt", msg_id=str(uuid.uuid4()),
        )
        return {"desk": name, "outcome": f"failed:{status}"}

    return {"desk": name, "outcome": f"unsupported_kind:{kind}"}


def run_once(*, registry: desks.Registry | None = None, state_path: Path = DEFAULT_STATE,
             room: str = DEFAULT_ROOM, results_path: Path | None = None,
             pending_timeout_s: float = PENDING_TIMEOUT_S,
             read_room=verb_io.read_room, add_room_turn=verb_io.add_room_turn,
             dispatch_fn=dispatch.dispatch, desk_state_dir: Path = dispatch.DESK_STATE,
             log=print) -> dict:
    registry = registry or desks.Registry()
    results_path = Path(results_path or dispatch.DEFAULT_RESULTS)
    state = state_mod.load_state(state_path)

    desk_entries = registry.entries()
    desk_seats = {n: e["room_seat"] for n, e in desk_entries.items() if e.get("room_seat")}

    result = read_room(state["last_seq"], room=room)
    turns = [t for t in result.get("turns", []) if int(t.get("seq", 0)) > state["last_seq"]]

    routed: dict[str, list[str]] = {}
    assignments: list[dict] = []
    for t in turns:
        routed[str(t.get("msg_id"))] = state_mod.route_turn(state, t, desk_seats)
        outcome, parsed, reason = grammar.classify(t)
        if outcome == "ok":
            assert parsed is not None  # guaranteed by classify() whenever outcome is "ok"
            res = grammar.apply_assignment(t, parsed, add_room_turn=add_room_turn)
            assignments.append({"seq": t.get("seq"), "outcome": outcome, **res})
        elif outcome in ("malformed", "unauthorized"):
            assert reason is not None  # guaranteed by classify() for these two outcomes
            grammar.reject(t, outcome, reason, add_room_turn=add_room_turn)
            assignments.append({"seq": t.get("seq"), "outcome": outcome, "reason": reason})
    state_mod.advance_seq(state, turns)

    delivered: list[dict] = []
    errors: list[dict] = []
    for name, entry in desk_entries.items():
        seat = entry.get("room_seat")
        if not seat:
            continue
        try:
            pending_outcome = handle_pending(
                name, seat, state, add_room_turn=add_room_turn,
                log_path=desk_state_dir / f"{name}.log",
                pending_timeout_s=pending_timeout_s,
            )
            if pending_outcome:
                delivered.append(pending_outcome)
            if state_mod.get_pending(state, name) is None:
                queued = state_mod.pop_next_queued(state, name)
                if queued is not None:
                    delivered.append(deliver(
                        name, entry, seat, queued, state=state, registry=registry,
                        results_path=results_path, add_room_turn=add_room_turn,
                        dispatch_fn=dispatch_fn, desk_state_dir=desk_state_dir,
                    ))
            registry_ext.stamp_heartbeat(name, live=probe_live(entry), path=registry.path)
        except desks.DeskError as e:
            errors.append({"desk": name, "error": e.code, "detail": str(e)})
            registry_ext.stamp_heartbeat(name, live=False, path=registry.path)

    state_mod.save_state(state_path, state)

    summary = {
        "turns_read": len(turns), "last_seq": state["last_seq"], "routed": routed,
        "assignments": assignments, "delivered": delivered, "errors": errors,
    }
    log(f"room-bridge: {len(turns)} turn(s), {len(delivered)} desk action(s), "
        f"{len(assignments)} assignment event(s), {len(errors)} error(s); "
        f"cursor now {state['last_seq']}")
    return summary


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--room", default=DEFAULT_ROOM)
    p.add_argument("--state", default=None)
    p.add_argument("--registry", default=None)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run-once", help="one poll cycle — what launchd invokes")
    a = p.parse_args(argv)

    reg = desks.Registry(a.registry) if a.registry else desks.Registry()
    state_path = Path(a.state) if a.state else DEFAULT_STATE

    if a.cmd == "run-once":
        try:
            summary = run_once(registry=reg, state_path=state_path, room=a.room)
        except RuntimeError as e:
            print(f"room-bridge: FAILED — {e}", file=sys.stderr)
            return 1
        if summary["errors"]:
            print(f"room-bridge: completed with {len(summary['errors'])} desk error(s)",
                  file=sys.stderr)
            for err in summary["errors"]:
                print(f"  {err['desk']}: {err['error']} — {err['detail']}", file=sys.stderr)
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
