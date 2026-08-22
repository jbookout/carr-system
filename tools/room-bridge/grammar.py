#!/usr/bin/env python3
"""grammar.py — the assignment bridge's one deterministic decision.

Joe's ruled design test: CODE DECIDES, MODELS ONLY AT JUDGMENT BOUNDARIES. A
room turn either matches this grammar exactly or it does not; nothing here
ever asks a model to interpret intent. Two forms, both one line:

    @<seat> assign <ref> <one-line title>
    @<seat> claim <ref>

VERSION 1 SCOPE (orchestrator addendum, 2026-08-22 — read before touching this
file): no queue-write verb is bound yet. claim-card is the lead-pool candidate
reservoir, not work-item claiming, and current-work-requests / work-request-
card are explicitly read-only. A separate, in-flight build is consolidating
the work queue; THAT is the verb this grammar should eventually call. Until it
lands, apply_assignment() is the one seam: it validates, records the intent
locally, and posts a machine-readable receipt — never a queue mutation. See
loop #508 (blocker other_lane) for the re-pointing follow-up.

AUTHORIZATION. Only a turn whose `seat` is "human" may trigger an assignment.
A model seat (claude, codex, grok, sol, hermes) can echo or paraphrase a
command in the room, and this bridge must not treat that as instruction —
only a person typing it counts. This is deliberately narrower than "any
seat", and is the judgment-boundary the ruled test draws: routing turns
between desks is mechanical and open to every seat; deciding to mutate a
queue is not, and stays behind a person until the record layer itself gates
it more precisely.

THREE OUTCOMES, so a near-miss is never silently ignored:
  "not_a_command"  — body does not look like an attempt at this grammar at
                      all; do nothing, no receipt (an ordinary chat turn that
                      happens to start with '@' is not spammed).
  "malformed"       — body opens with "@<word> assign|claim" but fails the
                      strict grammar (missing ref, missing title, extra
                      lines, bad ref shape). Rejected, with a receipt saying
                      why — the sender typed a command and deserves to know
                      it did not parse.
  "unauthorized"    — the grammar matched perfectly but the sending seat is
                      not authorized. Rejected, with a receipt naming the
                      seat.
  "ok"              — matched and authorized; apply_assignment() proceeds.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

AUTHORIZED_SEATS = frozenset({"human"})

# a seat name, same shape partner-room.js enforces server-side (SLUG)
_SEAT = r"[a-z][a-z0-9-]{0,31}"
# a ref token: letters/digits/dash/underscore, must start with a letter —
# deliberately generic (WR-123, L-508, C-9, ...) since this bridge does not
# yet know which record family will end up owning it (see module docstring)
_REF = r"[A-Za-z][A-Za-z0-9_-]{0,40}"

_LOOSE_TRIGGER = re.compile(rf"^@\S+\s+(assign|claim)\b", re.IGNORECASE)
_ASSIGN_RE = re.compile(rf"^@(?P<seat>{_SEAT})\s+assign\s+(?P<ref>{_REF})\s+(?P<title>\S.{{0,199}})$")
_CLAIM_RE = re.compile(rf"^@(?P<seat>{_SEAT})\s+claim\s+(?P<ref>{_REF})$")

DEFAULT_ASSIGNMENTS_LOG = Path.home() / ".config" / "carr" / "room-bridge-assignments.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def classify(turn: dict) -> tuple[str, dict | None, str | None]:
    """(outcome, parsed, reason). parsed is {"verb","target_seat","ref","title"}."""
    body = str(turn.get("body") or "")
    # strict grammar: the WHOLE turn is one line matching the pattern exactly —
    # a multi-line body never qualifies, deliberately, so a command cannot
    # hide inside a longer message a model would have to interpret.
    line = body.strip()
    if "\n" in body.strip("\n"):
        if _LOOSE_TRIGGER.match(line.splitlines()[0] if line else ""):
            return "malformed", None, "a command must be exactly one line; this turn has more than one"
        return "not_a_command", None, None
    if not _LOOSE_TRIGGER.match(line):
        return "not_a_command", None, None

    m = _ASSIGN_RE.match(line)
    if m:
        parsed = {"verb": "assign", "target_seat": m.group("seat"),
                  "ref": m.group("ref"), "title": m.group("title").strip()}
    else:
        m = _CLAIM_RE.match(line)
        if m:
            parsed = {"verb": "claim", "target_seat": m.group("seat"),
                      "ref": m.group("ref"), "title": None}
        else:
            return "malformed", None, (
                "looked like '@<seat> assign|claim ...' but did not match the strict "
                "grammar — expected '@<seat> assign <ref> <one-line title>' or "
                "'@<seat> claim <ref>'"
            )

    sender_seat = str(turn.get("seat") or "")
    if sender_seat not in AUTHORIZED_SEATS:
        return "unauthorized", parsed, (
            f"seat {sender_seat!r} is not authorized to trigger an assignment "
            f"(authorized: {sorted(AUTHORIZED_SEATS)})"
        )
    return "ok", parsed, None


def _load_log(path: Path) -> list[dict]:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _save_log(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(rows, indent=2) + "\n")
    tmp.replace(path)


def apply_assignment(turn: dict, parsed: dict, *, add_room_turn,
                      log_path: Path = DEFAULT_ASSIGNMENTS_LOG) -> dict:
    """THE SEAM (orchestrator's word for it). Everything this function does is
    disposable except its signature: when the consolidated work queue's write
    verb exists, this is the one place that changes — call it instead of (or
    alongside) recording locally. Today it:
      1. appends the assignment intent to a local JSON log (an audit trail,
         not a queue — nothing here claims or assigns anything real yet);
      2. posts a kind=receipt turn whose body is machine-readable JSON, so a
         future reconciliation pass (or a human) can replay every intent
         recorded before the real verb existed.
    `add_room_turn` is injected (body, seat, kind, msg_id) -> dict, exactly
    verb_io.add_room_turn's signature, so this is unit-testable with a
    recording fake and no network.
    """
    record = {
        "assignment": {
            "seat": parsed["target_seat"],
            "verb": parsed["verb"],
            "ref": parsed["ref"],
            "title": parsed["title"],
            "by": turn.get("sponsor"),
            "at_seq": turn.get("seq"),
            "source_msg_id": turn.get("msg_id"),
            "recorded_at": _now(),
        },
        "status": "recorded_no_queue_verb_yet",
        "note": "loop #508 — no consolidated-queue write verb bound yet; this is a "
                "local record only, not a real claim or assignment.",
    }
    rows = _load_log(log_path)
    rows.append(record)
    _save_log(log_path, rows)

    receipt = add_room_turn(
        body=json.dumps(record, separators=(",", ":")),
        seat="hermes",
        kind="receipt",
    )
    return {"record": record, "receipt": receipt}


def reject(turn: dict, outcome: str, reason: str, *, add_room_turn) -> dict:
    """Post the 'why nothing happened' receipt for malformed/unauthorized
    attempts. Never called for not_a_command — that is not an attempt."""
    body = json.dumps({
        "assignment_rejected": {
            "outcome": outcome,
            "reason": reason,
            "source_seat": turn.get("seat"),
            "source_seq": turn.get("seq"),
            "source_msg_id": turn.get("msg_id"),
        },
    }, separators=(",", ":"))
    return add_room_turn(body=body, seat="hermes", kind="receipt")
