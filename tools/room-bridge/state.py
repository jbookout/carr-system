#!/usr/bin/env python3
"""state.py — the room bridge's own memory: how far it has read the room, and
what each desk has already seen or is waiting on. Pure logic, no socket, no
subprocess, no network — the acceptance bar carried over from
tools/partner-line/watch.py's poll_once() design (fetch/injector/notifier as
plain parameters), extended here to state itself: every function below takes
a state dict and returns a new fact about it, so bridge.py's run_once() is the
only place that touches a file.

WHY A STATE FILE AND NOT JUST THE ROOM'S OWN CURSOR. read-room already gives a
monotonic seq and a `latest_seq` cursor — that is enough to never re-READ a
turn. It is not enough to know whether a turn has already been DELIVERED to a
given desk: a claude-session desk answers asynchronously (dispatch.py's own
docstring: "the desk answers in its own window, and this file does not carry
that back"), so a turn can be delivered in one poll cycle and its reply not
land until several cycles later. The bridge is invoked periodically by
launchd (a short poll-and-exit cycle, matching every other launchd service in
this repo — see ops/config/services.json), so this state has to survive
between invocations or a restart mid-wait would silently drop the desk's
answer.

SHAPE, persisted as JSON:
{
  "last_seq": 0,                # the room cursor — every turn up to and
                                 # including this seq has been considered
  "desks": {
    "<name>": {
      "delivered": ["<msg_id>", ...],   # bounded — turns already queued or
                                         # delivered to THIS desk; the echo/
                                         # duplicate guard
      "queue": [ {"msg_id","seat","body","seq"}, ... ],  # not yet injected
      "pending": null | {
        "dispatch_msg_id": "...",   # dispatch.py's own msg_id for the
                                     # delivered turn — the correlation key
        "log_offset": 0,            # byte offset into the desk's log BEFORE
                                     # injection; where reply-scanning starts
        "injected_at": "iso",
        "source_msg_id": "...",     # the room turn's msg_id this answers
        "source_seq": 0
      }
    }
  }
}
"""

from __future__ import annotations

import json
from pathlib import Path

DELIVERED_CAP = 4000  # per desk; oldest dropped first — a dedup memory, not an audit log


def default_state() -> dict:
    return {"last_seq": 0, "desks": {}}


def load_state(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default_state()
    data.setdefault("last_seq", 0)
    data.setdefault("desks", {})
    return data


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2) + "\n")
    tmp.replace(path)


def _desk_slot(state: dict, name: str) -> dict:
    return state["desks"].setdefault(
        name, {"delivered": [], "queue": [], "pending": None}
    )


def advance_seq(state: dict, turns: list[dict]) -> int:
    """The room cursor advances past every turn considered, delivered or not —
    same stance as partner-line/watch.py's next_watermark(): a skipped row is
    still successfully accounted for, or it is re-fetched and re-classified
    forever."""
    seqs = [int(t.get("seq", state["last_seq"])) for t in turns]
    state["last_seq"] = max([state["last_seq"], *seqs]) if seqs else state["last_seq"]
    return state["last_seq"]


def is_echo(turn: dict, desk_seat: str) -> bool:
    """A turn IS this desk's own echo when the seat that spoke it is the same
    seat this desk speaks for — never re-inject a desk's own words back at it.
    """
    return str(turn.get("seat") or "") == str(desk_seat or "")


def already_delivered(state: dict, desk_name: str, msg_id: str) -> bool:
    return msg_id in _desk_slot(state, desk_name)["delivered"]


def mark_delivered(state: dict, desk_name: str, msg_id: str) -> None:
    slot = _desk_slot(state, desk_name)
    if msg_id in slot["delivered"]:
        return
    slot["delivered"].append(msg_id)
    if len(slot["delivered"]) > DELIVERED_CAP:
        slot["delivered"] = slot["delivered"][-DELIVERED_CAP:]


def route_turn(state: dict, turn: dict, desk_seats: dict[str, str]) -> list[str]:
    """Queue one new room turn onto every desk it is eligible for.

    desk_seats maps desk name -> the room seat that desk speaks for. A turn is
    queued onto a desk when: the turn is not that desk's own echo (is_echo),
    and this bridge has not already delivered/queued this exact msg_id to that
    desk before (already_delivered) — the two-part guard the brief calls for:
    "echo suppression by msg_id + seat". Returns the names of desks the turn
    was queued onto, for logging.
    """
    msg_id = str(turn.get("msg_id") or "")
    if not msg_id:
        return []
    queued_onto: list[str] = []
    for name, seat in desk_seats.items():
        if is_echo(turn, seat):
            continue
        if already_delivered(state, name, msg_id):
            continue
        slot = _desk_slot(state, name)
        slot["queue"].append({
            "msg_id": msg_id,
            "seat": turn.get("seat"),
            "body": turn.get("body"),
            "seq": turn.get("seq"),
        })
        mark_delivered(state, name, msg_id)
        queued_onto.append(name)
    return queued_onto


def has_pending(state: dict, desk_name: str) -> bool:
    return _desk_slot(state, desk_name)["pending"] is not None


def pop_next_queued(state: dict, desk_name: str) -> dict | None:
    slot = _desk_slot(state, desk_name)
    if not slot["queue"]:
        return None
    return slot["queue"].pop(0)


def set_pending(state: dict, desk_name: str, *, dispatch_msg_id: str,
                 log_offset: int, injected_at: str, source_msg_id: str,
                 source_seq) -> None:
    slot = _desk_slot(state, desk_name)
    slot["pending"] = {
        "dispatch_msg_id": dispatch_msg_id,
        "log_offset": log_offset,
        "injected_at": injected_at,
        "source_msg_id": source_msg_id,
        "source_seq": source_seq,
    }


def clear_pending(state: dict, desk_name: str) -> None:
    _desk_slot(state, desk_name)["pending"] = None


def get_pending(state: dict, desk_name: str) -> dict | None:
    return _desk_slot(state, desk_name)["pending"]
