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
  6. At most once every HEARTBEAT_INTERVAL_S (five minutes, throttle persisted
     in the state file), post the roster itself INTO the room as a
     kind="receipt" turn — the Model Room observatory's single source for desk
     state, cursor position and cycle age. The panel reads only the wire, so
     anything it needs to know about local desks has to arrive as a turn.

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

import auth_control  # noqa: E402
import desks  # noqa: E402
import dispatch  # noqa: E402
import execution_contract  # noqa: E402
import grammar  # noqa: E402
import kanban_adapter  # noqa: E402
import queue_dispatch  # noqa: E402
import queue_projection  # noqa: E402
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
# THE OBSERVATORY HEARTBEAT (Joe's ruling 0892c539). The Model Room panel reads
# ONLY the room wire — no second API for desk state, no Worker reach into a
# local file — so the one fact the wire could not otherwise carry, "which desks
# exist and were they alive at this cycle", is published INTO the wire as a
# machine-readable receipt. Throttled, and the throttle is persisted in the
# state file rather than inferred from the room, because launchd fires this
# process far more often than five minutes and an unthrottled heartbeat would
# bury the conversation it is supposed to annotate.
HEARTBEAT_INTERVAL_S = float(os.environ.get("CARR_ROOM_BRIDGE_HEARTBEAT_INTERVAL", "300"))


def publish_job_passport_fact(kind: str, payload: dict, *, add_room_turn) -> dict:
    """Publish one controller-validated, redacted Job Passport fact to the wire.

    This is intentionally narrower than a dispatcher: it does not select a
    model, derive identity, create authority, or persist any transcript. The
    bridge is merely the existing server-attributed wire transport. The fixed
    Hermes seat says that a deterministic orchestration host relayed the fact;
    it does not turn Hermes into an authority source.
    """
    wire = execution_contract.job_passport_wire_receipt(kind, payload)
    result = add_room_turn(
        body=json.dumps(wire, separators=(",", ":")), seat="hermes", kind="receipt",
        msg_id=str(uuid.uuid4()),
    )
    return {"kind": kind, "attempt_id": wire["job_passport"]["payload"].get("attempt_id"),
            "result": result}


def rehearse_job_passport(envelope: dict, receipt: dict, events: list[dict], profile: dict, *, evaluation_kernel: dict | None = None, spatial_surface: dict | None = None, telemetry_measurements: list[dict] | None = None, add_room_turn) -> dict:
    """Exercise the full typed wire path using only synthetic, read-only facts.

    This is a rehearsal helper, never an autonomous runtime: caller-supplied
    material must already be a synthetic v1 envelope with server-derived
    read-only authority. It emits the exact envelope, progress, receipt, and
    deterministic Observatory projection shapes the Model Room consumes. An
    optional shared evaluation kernel is relayed as evidence, never promoted.
    """
    bound = execution_contract.validate_execution_envelope(envelope)
    if bound["request"]["data_class"] != "synthetic_only" or bound["server_binding"]["authority"]["read_only"] is not True:
        raise execution_contract.ContractError("Job Passport rehearsal is synthetic and read-only only")
    completed = execution_contract.validate_attempt_receipt(receipt, bound)
    projection = execution_contract.project_observatory_attempt(bound, completed, events, profile)
    published = [publish_job_passport_fact("execution_envelope", bound, add_room_turn=add_room_turn)]
    published.extend(publish_job_passport_fact("progress_event", event, add_room_turn=add_room_turn) for event in events)
    published.append(publish_job_passport_fact("attempt_receipt", completed, add_room_turn=add_room_turn))
    published.append(publish_job_passport_fact("observatory_projection", projection, add_room_turn=add_room_turn))
    if evaluation_kernel is not None:
        published.append(publish_job_passport_fact("evaluation_kernel", evaluation_kernel, add_room_turn=add_room_turn))
    if spatial_surface is not None:
        from spatial_surface import validate_spatial_surface
        published.append(publish_job_passport_fact("spatial_surface", validate_spatial_surface(spatial_surface, projection), add_room_turn=add_room_turn))
    from spatial_surface import measurements_from_attempt_receipt, validate_telemetry_measurement
    for measurement in telemetry_measurements if telemetry_measurements is not None else measurements_from_attempt_receipt(completed):
        validated = validate_telemetry_measurement(measurement)
        if validated["attribution"]["attempt_id"] != completed["attempt_id"]:
            raise execution_contract.ContractError("telemetry measurement does not bind rehearsal attempt")
        published.append(publish_job_passport_fact("telemetry_measurement", validated, add_room_turn=add_room_turn))
    return {"mode": "synthetic_read_only_rehearsal", "work_request_id": bound["work_request_id"],
            "attempt_id": completed["attempt_id"], "projection": projection, "published": published}


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
                    scan=scan_for_result, now: str | None = None,
                    queue_executor: queue_dispatch.QueueDeskExecutor | None = None) -> dict | None:
    pending = state_mod.get_pending(state, name)
    if pending is None:
        return None
    result_text = scan(log_path, pending["log_offset"])
    if result_text is not None:
        if pending.get("origin_kind") == "queue":
            if queue_executor is None:
                return {"desk": name, "outcome": "queue_executor_unavailable"}
            terminal = queue_executor.finish_pending(pending, result_text)
            state_mod.clear_pending(state, name)
            return {"desk": name, **terminal}
        add_room_turn(body=result_text.strip() or "(empty reply)", seat=seat, kind="turn",
                      msg_id=str(uuid.uuid4()))
        state_mod.clear_pending(state, name)
        return {"desk": name, "outcome": "replied"}
    if _elapsed_seconds(pending.get("injected_at"), now=now) > pending_timeout_s:
        if pending.get("origin_kind") == "queue":
            if queue_executor is None:
                return {"desk": name, "outcome": "queue_executor_unavailable"}
            terminal = queue_executor.fail_pending(pending, "desk_result_timeout", now=now)
            if terminal.get("outcome") == "retry_scheduled":
                state_mod.set_queue_retry_at(state, terminal["task_id"], terminal["retry_at"])
            else:
                state_mod.clear_queue_retry_at(state, terminal.get("task_id"))
            state_mod.clear_pending(state, name)
            return {"desk": name, **terminal}
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


def heartbeat_due(state: dict, *, now: str | None = None,
                  interval_s: float = HEARTBEAT_INTERVAL_S) -> bool:
    """True when this cycle should publish a heartbeat receipt. Never posted
    means due; otherwise due once `interval_s` has elapsed since the last one.
    A clock that has gone backwards (or an unparseable stamp) reads as 0
    elapsed and therefore NOT due — the throttle fails closed, so a bad
    timestamp cannot turn the heartbeat into a flood."""
    last = state_mod.get_heartbeat_at(state)
    if not last:
        return True
    return _elapsed_seconds(last, now=now) >= interval_s


def heartbeat_body(desk_entries: dict, cursor: int, cycle_at: str,
                   profiles: list | None = None) -> str:
    """The compact JSON the panel parses. `desks` carries every REGISTERED desk,
    seated or not: a desk with no room_seat is exactly the panel's dormant
    case, and omitting it would make an unwired desk indistinguishable from a
    desk that was never registered.

    `auth` is this cycle's own sign-in probe (spec section 17): true, false, or
    null when the vendor CLI could not answer. Null is NOT signed-out — see
    auth_control.probe_auth — and the panel renders it as unknown.

    `profiles` is the named-agent roster (loop 520): the NAME persists and the
    model behind it is staffing detail, and any feed window must contain
    current profile truth, so the roster is REPUBLISHED here rather than
    assumed from older receipts. None means the roster could not be read this
    cycle, and the key is then ABSENT — never an empty list, which would read
    as "no profiles exist". Each desk row also names the profile bound to it,
    when the local registry carries one."""
    rows = [
        {
            "name": name,
            "seat": entry.get("room_seat"),
            "live": bool(entry.get("last_live")),
            "last_seen": entry.get("last_seen"),
            "auth": entry.get("last_auth") if isinstance(entry.get("last_auth"), bool) else None,
            "profile": entry.get("profile") if isinstance(entry.get("profile"), str) else None,
        }
        for name, entry in sorted(desk_entries.items())
    ]
    heartbeat: dict = {"desks": rows, "cursor": cursor, "cycle_at": cycle_at}
    if profiles is not None:
        heartbeat["profiles"] = profiles
    return json.dumps({"heartbeat": heartbeat}, separators=(",", ":"))


def post_heartbeat(state: dict, desk_entries: dict, *, add_room_turn, cursor: int,
                   now: str | None = None,
                   interval_s: float = HEARTBEAT_INTERVAL_S,
                   profiles: list | None = None) -> dict | None:
    """Publish the roster receipt if the throttle allows, and record that it
    went out. Returns None when throttled, so run_once's summary says honestly
    whether this cycle spoke."""
    if not heartbeat_due(state, now=now, interval_s=interval_s):
        return None
    stamp = now or _now()
    body = heartbeat_body(desk_entries, cursor, stamp, profiles=profiles)
    add_room_turn(body=body, seat="hermes", kind="receipt", msg_id=str(uuid.uuid4()))
    state_mod.set_heartbeat_at(state, stamp)
    return {"posted_at": stamp, "desks": len(desk_entries), "cursor": cursor}


def handle_control(turn: dict, control: dict, desk_entries: dict, state: dict, *,
                    add_room_turn, registry, now: str | None = None,
                    launch=auth_control.launch_login,
                    throttle_s: float = auth_control.LOGIN_THROTTLE_S) -> dict:
    """One control turn from the observatory's RECONNECT button, allowlisted
    and then executed or refused — never silently dropped, because a control
    nobody answers is indistinguishable from a bridge that is down.

    Every refusal posts its reason back onto the wire, which is also how the
    panel learns that a second click was throttled rather than lost."""
    outcome, reason = auth_control.classify_control(
        turn, control, registered=set(desk_entries), state=state, now=now, throttle_s=throttle_s)
    desk = str(control.get("desk") or "")
    if outcome != "ok":
        add_room_turn(
            body=json.dumps({"control_refused": {
                "action": control.get("action"), "desk": desk, "reason": reason,
                "source_seq": turn.get("seq"), "source_msg_id": turn.get("msg_id"),
            }}, separators=(",", ":")),
            seat="hermes", kind="receipt", msg_id=str(uuid.uuid4()),
        )
        return {"seq": turn.get("seq"), "outcome": "refused", "desk": desk, "reason": reason}

    stamp = now or _now()
    launched = launch(desk, desk_entries[desk])
    if not launched.get("launched"):
        add_room_turn(
            body=json.dumps({"control_refused": {
                "action": "login", "desk": desk, "reason": launched.get("reason"),
                "source_seq": turn.get("seq"),
            }}, separators=(",", ":")),
            seat="hermes", kind="receipt", msg_id=str(uuid.uuid4()),
        )
        return {"seq": turn.get("seq"), "outcome": "launch_failed", "desk": desk,
                "reason": launched.get("reason")}

    auth_control.note_login_launched(state, desk, stamp)
    add_room_turn(
        body=json.dumps({"control_executed": {
            "action": "login", "desk": desk, "at": stamp,
            "note": "the vendor's own sign-in flow was opened on this Mac; approval "
                    "happens in the browser and no credential passes through the bridge. "
                    "The desk process restarts automatically once the probe reads signed in.",
            "source_seq": turn.get("seq"),
        }}, separators=(",", ":")),
        seat="hermes", kind="receipt", msg_id=str(uuid.uuid4()),
    )
    return {"seq": turn.get("seq"), "outcome": "executed", "desk": desk}


def settle_restarts(desk_entries: dict, auth_by_desk: dict, state: dict, *,
                    add_room_turn, registry,
                    stop=dispatch.desk_stop, start=dispatch.desk_start) -> list[dict]:
    """A desk that was awaiting a login and now probes signed in gets its
    PROCESS restarted, because a running desk holds its token in memory
    (measured 2026-08-22). Restarting is what makes the reconnect button
    actually reconnect rather than merely open a browser tab."""
    settled: list[dict] = []
    for desk in list(auth_control.awaiting_login(state)):
        if desk not in desk_entries:
            auth_control.clear_awaiting_login(state, desk)
            continue
        if auth_by_desk.get(desk) is not True:
            continue
        result = auth_control.restart_desk(desk, registry=registry, stop=stop, start=start)
        auth_control.clear_awaiting_login(state, desk)
        add_room_turn(
            body=json.dumps({"desk_restarted": {
                "desk": desk, "after": "login",
                "restarted": bool(result.get("restarted")),
                "reason": result.get("reason"),
            }}, separators=(",", ":")),
            seat="hermes", kind="receipt", msg_id=str(uuid.uuid4()),
        )
        settled.append({"desk": desk, **{k: v for k, v in result.items() if k != "detail"}})
    return settled


def run_once(*, registry: desks.Registry | None = None, state_path: Path = DEFAULT_STATE,
             room: str = DEFAULT_ROOM, results_path: Path | None = None,
             pending_timeout_s: float = PENDING_TIMEOUT_S,
             read_room=verb_io.read_room, add_room_turn=verb_io.add_room_turn,
             dispatch_fn=dispatch.dispatch, desk_state_dir: Path = dispatch.DESK_STATE,
             probe_auth=auth_control.probe_auth, launch_login=auth_control.launch_login,
             desk_stop=dispatch.desk_stop, desk_start=dispatch.desk_start,
             read_profiles=verb_io.read_profiles,
             queue_service: kanban_adapter.QueueService | None = None,
             queue_executor: queue_dispatch.QueueDeskExecutor | None = None,
             queue_projector=queue_projection.project_once,
             now_fn=_now,
             log=print) -> dict:
    registry = registry or desks.Registry()
    results_path = Path(results_path or dispatch.DEFAULT_RESULTS)
    state = state_mod.load_state(state_path)

    desk_entries = registry.entries()
    desk_seats = {n: e["room_seat"] for n, e in desk_entries.items() if e.get("room_seat")}

    # The target catalog is configuration for command ingress, not a reason to
    # stop the conversational bridge.  When it cannot be loaded, only an
    # attempted @queue command is refused; ordinary turns still route.
    queue_load_error = None
    if queue_service is None:
        try:
            queue_service = kanban_adapter.QueueService()
        except kanban_adapter.QueueError as exc:
            queue_load_error = exc

    if (queue_executor is None and queue_service is not None
            and hasattr(queue_service, "catalog")
            and hasattr(getattr(queue_service, "adapter", None), "ready_for")):
        queue_executor = queue_dispatch.QueueDeskExecutor(
            catalog=queue_service.catalog, adapter=queue_service.adapter)
    desk_queue_targets = {}
    if queue_executor is not None:
        desk_queue_targets = {
            target["desk"]: alias
            for alias, target in queue_executor.catalog["targets"].items()
            if target.get("enabled") and target.get("adapter") == "desk"
        }

    result = read_room(state["last_seq"], room=room)
    turns = [t for t in result.get("turns", []) if int(t.get("seq", 0)) > state["last_seq"]]

    routed: dict[str, list[str]] = {}
    assignments: list[dict] = []
    controls: list[dict] = []
    queue_events: list[dict] = []
    for t in turns:
        # Queue commands are a control plane, never conversational work.  This
        # check deliberately precedes route_turn: every @queue attempt is
        # consumed, including malformed and unauthorized requests.
        if queue_service is not None:
            queued = queue_service.handle(t, room=room)
        elif str(t.get("body") or "").strip().startswith("@queue"):
            queued = {
                "handled": True, "kind": "rejected", "receipt": {"queue_rejected": {
                    "source_seq": t.get("seq"), "source_msg_id": t.get("msg_id"),
                    "code": queue_load_error.code if queue_load_error else "queue_unavailable",
                    "reason": queue_load_error.reason if queue_load_error else "queue is unavailable",
                    "hint": "Try again after Hermes is available",
                }},
            }
        else:
            queued = {"handled": False}
        if queued.get("handled"):
            receipt = queued.get("receipt")
            if receipt:
                add_room_turn(body=json.dumps(receipt, separators=(",", ":")), seat="hermes",
                              kind="receipt", msg_id=str(uuid.uuid4()))
            routed[str(t.get("msg_id"))] = []
            queue_events.append({"seq": t.get("seq"), "kind": queued.get("kind")})
            continue
        routed[str(t.get("msg_id"))] = state_mod.route_turn(state, t, desk_seats)
        control = auth_control.parse_control(t)
        if control is not None:
            controls.append(handle_control(
                t, control, desk_entries, state, add_room_turn=add_room_turn,
                registry=registry, launch=launch_login))
            continue
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

    # SIGN-IN STATE, probed once per cycle for EVERY registered desk — seated or
    # not, since an unseated desk can be signed out too and Joe's panel shows
    # it. Stamped onto the registry so the heartbeat below carries this cycle's
    # own answer rather than a stale one (spec section 17: no probe result older
    # than one cycle is shown as current).
    auth_by_desk: dict[str, bool | None] = {}
    for name, entry in desk_entries.items():
        auth_by_desk[name] = probe_auth(entry)
        registry_ext.stamp_auth(name, auth=auth_by_desk[name], path=registry.path)

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
                now=now_fn(),
                queue_executor=queue_executor,
            )
            if pending_outcome:
                delivered.append(pending_outcome)
            if state_mod.get_pending(state, name) is None:
                next_queued = state_mod.pop_next_queued(state, name)
                if next_queued is not None:
                    delivered.append(deliver(
                        name, entry, seat, next_queued, state=state, registry=registry,
                        results_path=results_path, add_room_turn=add_room_turn,
                        dispatch_fn=dispatch_fn, desk_state_dir=desk_state_dir,
                    ))
                elif queue_executor is not None and name in desk_queue_targets:
                    log_path = desk_state_dir / f"{name}.log"
                    offset = log_path.stat().st_size if log_path.exists() else 0

                    def dispatch_queue(prompt: str) -> dict:
                        return dispatch_fn(
                            name, prompt, registry=registry, results_path=results_path)

                    queue_outcome = queue_executor.start(
                        desk_queue_targets[name], dispatch_call=dispatch_queue,
                        desk_busy=state_mod.has_queued(state, name),
                        retry_at=state["queue_retry_at"], now=now_fn(),
                    )
                    if queue_outcome.get("outcome") == "retry_scheduled":
                        state_mod.set_queue_retry_at(
                            state, queue_outcome["task_id"], queue_outcome["retry_at"])
                    elif queue_outcome.get("outcome") not in {"retry_wait", "idle"}:
                        state_mod.clear_queue_retry_at(state, queue_outcome.get("task_id"))
                    pending = queue_outcome.get("pending")
                    if isinstance(pending, dict):
                        state_mod.set_pending(
                            state, name,
                            dispatch_msg_id=str(pending.get("dispatch_msg_id") or ""),
                            log_offset=offset,
                            injected_at=str(pending.get("injected_at") or _now()),
                            source_msg_id=f"queue:{pending['kanban_task_id']}",
                            source_seq=None,
                            origin_kind="queue",
                            kanban_task_id=pending["kanban_task_id"],
                            target=pending["target"],
                            finish=pending["finish"],
                            cap=pending.get("cap"),
                        )
                    if queue_outcome.get("outcome") != "idle":
                        delivered.append({"desk": name, **queue_outcome})
            registry_ext.stamp_heartbeat(name, live=probe_live(entry), path=registry.path)
        except desks.DeskError as e:
            code = e.code
            errors.append({"desk": name, "error": code, "detail": str(e)[:500]})
            registry_ext.stamp_heartbeat(name, live=False, path=registry.path)
        except (kanban_adapter.QueueError, queue_dispatch.QueueDispatchError) as e:
            code = getattr(e, "code", "queue_dispatch_failed")
            errors.append({"desk": name, "error": code, "detail": str(e)[:500]})
            # A queue outage says nothing about whether the named desk is live.
            registry_ext.stamp_heartbeat(name, live=probe_live(entry), path=registry.path)

    restarts = settle_restarts(desk_entries, auth_by_desk, state,
                                add_room_turn=add_room_turn, registry=registry,
                                stop=desk_stop, start=desk_start)

    projection_events: list[dict] = []
    if queue_service is not None:
        state["queue_projection_checked_at"] = now_fn()
        try:
            projection_events = queue_projector(
                state=state, add_room_turn=add_room_turn,
                target_catalog=queue_service.catalog.get("targets", {}),
            )
            state["queue_projection_last_success_at"] = now_fn()
            state["queue_projection_error"] = None
        except Exception as exc:  # projection failure must be visible, never a live-looking board
            state["queue_projection_error"] = "queue_projection_failed"
            errors.append({"desk": "(queue-projector)", "error": "queue_projection_failed",
                           "detail": "queue projector failed"})

    # Read the registry back AFTER the heartbeat stamps above, so the roster the
    # observatory sees carries this cycle's own liveness rather than the values
    # this cycle started with.
    heartbeat = None
    try:
        # The named-agent roster (loop 520) rides the throttled heartbeat so
        # any feed window carries current profile truth. A roster read that
        # fails must cost the roster, never the heartbeat: profiles=None makes
        # the key honestly absent, and the failure is a reportable cycle error.
        profiles = None
        if heartbeat_due(state):
            try:
                profiles = read_profiles()
            except Exception as e:  # noqa: BLE001 — any fetch failure degrades the same way
                errors.append({"desk": "(profiles)", "error": "profile_roster_unread",
                               "detail": str(e)[:500]})
        heartbeat = post_heartbeat(
            state, registry_ext.all_desks(registry.path),
            add_room_turn=add_room_turn, cursor=state["last_seq"],
            profiles=profiles,
        )
    except RuntimeError as e:
        # A heartbeat that cannot be posted is a reportable cycle error, never a
        # reason to lose the routing and delivery work this cycle already did.
        errors.append({"desk": "(heartbeat)", "error": "heartbeat_post_failed", "detail": str(e)})

    state_mod.save_state(state_path, state)

    summary = {
        "turns_read": len(turns), "last_seq": state["last_seq"], "routed": routed,
        "assignments": assignments, "delivered": delivered, "errors": errors,
        "heartbeat": heartbeat, "controls": controls, "restarts": restarts,
        "auth": auth_by_desk, "queue": queue_events,
        "queue_projection": projection_events,
    }
    log(f"room-bridge: {len(turns)} turn(s), {len(delivered)} desk action(s), "
        f"{len(assignments)} assignment event(s), {len(queue_events)} queue event(s), {len(controls)} control(s), "
        f"{len(restarts)} restart(s), {len(errors)} error(s), "
        f"heartbeat {'posted' if heartbeat else 'throttled'}; "
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
