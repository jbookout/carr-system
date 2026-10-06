#!/usr/bin/env python3
"""gate_verdict.py — label gate decisions right or wrong, and grade each gate.

    ./run.sh gate-verdict list [--gate G] [--days N] [--unlabelled] [--limit N]
    ./run.sh gate-verdict label <decision-id> right|wrong --reason "why" [--by NAME]
    ./run.sh gate-verdict report [--days N] [--json]
    ./run.sh gate-verdict backfill out/hook-telemetry.jsonl.1 out/hook-telemetry.jsonl

The ledger is out/gate-decisions.jsonl, written by hooks/gate_ledger.py from
inside hook-meter-run.py: one `decision` line per block, hold or reopen, and
`verdict` lines labelling them. This tool adds human verdicts and reads both.

A HUMAN LABEL BEATS AN AUTOMATIC ONE whatever the order; among labels of the
same kind the latest wins. The false-alarm rate is wrong / labelled, never
wrong / blocks: an unlabelled block is unknown, not right, and a gate with no
labels has no rate at all rather than a flattering zero.

No CARR verb fits this (checked 2026-10-05: no verb records a verdict on a
local hook decision), and the ledger is per-machine evidence, so it stays a file.
"""
import argparse
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

WINDOW_DAYS = 7
NOISY_MIN_WRONG = 3
NOISY_RATE = 0.25



def default_ledger():
    sys.path.insert(0, os.path.join(REPO, "hooks"))
    import gate_ledger
    return gate_ledger.ledger_path(REPO, "live")


LOOP_STATE = os.path.join(os.path.dirname(default_ledger()), "gate-precision-loops.json")


def _epoch(ts):
    if isinstance(ts, (int, float)):
        return float(ts)
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def read(path):
    """(decisions in file order, the winning verdict per decision id)."""
    decisions, verdicts = [], {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            try:
                row = json.loads(line)
            except ValueError as exc:
                raise ValueError("unreadable decision ledger") from exc
            if not isinstance(row, dict) or row.get("type") not in {"decision", "verdict"}:
                raise ValueError("invalid decision ledger record")
            if row.get("type") == "decision":
                if not row.get("id") or not row.get("gate") or not _epoch(row.get("ts")):
                    raise ValueError("incomplete decision ledger record")
                decisions.append(row)
            elif row.get("type") == "verdict":
                if (not row.get("decision_id") or row.get("label") not in {"right", "wrong"}
                        or not row.get("by") or not _epoch(row.get("ts"))):
                    raise ValueError("incomplete verdict record")
                held = verdicts.get(row["decision_id"])
                if held and held.get("by") != "auto" and row.get("by") == "auto":
                    continue
                verdicts[row["decision_id"]] = row
    return decisions, verdicts


def precision(path, days=WINDOW_DAYS, now=None):
    now = time.time() if now is None else now
    decisions, verdicts = read(path)
    if not decisions:
        raise ValueError("ledger has no decision evidence")
    stats = {}
    for d in decisions:
        if now - _epoch(d.get("ts")) > days * 86400:
            continue
        g = stats.setdefault(d.get("gate") or "?", {"blocks": 0, "labelled": 0, "wrong": 0,
                                                     "right": 0, "_wrong_rules": {}})
        g["blocks"] += 1
        label = (verdicts.get(d.get("id")) or {}).get("label")
        if label in ("right", "wrong"):
            g["labelled"] += 1
            g[label] += 1
            if label == "wrong":
                rule = d.get("rule") or "?"
                g["_wrong_rules"][rule] = g["_wrong_rules"].get(rule, 0) + 1
    for g in stats.values():
        rules = g.pop("_wrong_rules")
        g["fa_rate"] = (g["wrong"] / g["labelled"]) if g["labelled"] else None
        g["top_wrong"] = sorted(rules.items(), key=lambda kv: (-kv[1], kv[0]))[:3]
    return stats


def noisy_gates(stats):
    return [{"gate": gate, **g} for gate, g in sorted(stats.items())
            if g["wrong"] >= NOISY_MIN_WRONG and (g["fa_rate"] or 0) >= NOISY_RATE]


ACTION = (f"on breach: open/update one deduplicated loop per noisy gate (owner claude, "
          f"the orchestrator) · remediation fix the gate's top wrong rule with a selftest "
          f"case that fails first, then re-bless · verify ./run.sh gate-verdict report shows "
          f"the gate under {NOISY_RATE:.0%} or below {NOISY_MIN_WRONG} wrong in {WINDOW_DAYS}d · "
          f"auto-clear: the loop closes when the gate drops off this row")


def _fmt_rate(rate):
    return "—" if rate is None else f"{rate:.0%}"


def health_row(path, days=WINDOW_DAYS):
    """(the row, the noisy gates). A missing ledger is never an all-clear."""
    try:
        stats = precision(path, days)
    except (OSError, ValueError) as exc:
        return (f"⚠︎ {'gate precision':<18} evidence unknown ({type(exc).__name__}); "
                f"restore the decision ledger before reconciliation · {ACTION}", None)
    noisy = noisy_gates(stats)
    blocks = sum(g["blocks"] for g in stats.values())
    labelled = sum(g["labelled"] for g in stats.values())
    wrong = sum(g["wrong"] for g in stats.values())
    summary = (f"{blocks} block(s) by {len(stats)} gate(s) in {days}d, {labelled} labelled, "
               f"{wrong} wrong")
    if not noisy:
        return f"OK {'gate precision':<18} {summary}; no gate over threshold · {ACTION}", []
    named = "; ".join(
        f"{n['gate']} {_fmt_rate(n['fa_rate'])} false alarms ({n['wrong']}/{n['labelled']})"
        + (", top: " + ", ".join(f"{r} ×{c}" for r, c in n["top_wrong"]) if n["top_wrong"] else "")
        for n in noisy)
    return f"⚠︎ {'gate precision':<18} {summary}; NOISY: {named} · {ACTION}", noisy


def _loop_body(n):
    top = ", ".join(f"'{r}' ×{c}" for r, c in n["top_wrong"]) or "none named"
    return (f"Gate {n['gate']} is noisy: {_fmt_rate(n['fa_rate'])} of its labelled refusals "
            f"in the last {WINDOW_DAYS} days were false alarms ({n['wrong']} wrong of "
            f"{n['labelled']} labelled, {n['blocks']} blocks). Top wrong rules: {top}. "
            f"Remediation: fix those rules in hooks/{n['gate']} with a selftest case that "
            f"fails first, keep a case proving the real danger is still refused, re-bless "
            f"the gate baseline. Verify: ./run.sh gate-verdict report shows {n['gate']} under "
            f"{NOISY_RATE:.0%} or below {NOISY_MIN_WRONG} wrong. This loop auto-closes when "
            f"the gate precision health row stops naming the gate.")


def _save_state(path, state):
    from pathlib import Path
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + f".{os.getpid()}.tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, target)


def _perform(state, gate, verb, path):
    entry = state[gate]
    intent = entry["intent"]
    try:
        response = verb(intent["verb"], intent["payload"])
    except Exception:
        return "error"
    if not isinstance(response, dict) or response.get("ok") is not True:
        if isinstance(response, dict) and response.get("error") in {"version_conflict", "loop_not_open"}:
            entry.pop("intent")
            _save_state(path, state)
        return "error"
    action = intent["verb"]
    if action == "add-loop":
        if not isinstance(response.get("loop_id"), str):
            return "error"
        entry["loop_id"] = response["loop_id"]
        result = "opened"
    elif action == "update-loop":
        result = "updated"
    else:
        state.pop(gate)
        _save_state(path, state)
        return "closed"
    entry.pop("intent")
    _save_state(path, state)
    return result


def _intend(state, gate, name, payload, path):
    state[gate]["intent"] = {"verb": name, "payload": {
        "idempotency_key": str(uuid.uuid4()), **payload}}
    _save_state(path, state)


def reconcile_loops(noisy, verb, state_path=LOOP_STATE):
    """Own the locked transition, including intent persistence before each write.

    None means unreadable evidence and permits no external effects. Empty valid
    evidence can prove recovery. Ambiguous calls retry the persisted payload.
    """
    import gate_ledger
    if noisy is None:
        return {"evidence": "error"}
    with gate_ledger.locked(state_path):
        try:
            with open(state_path, encoding="utf-8") as handle:
                state = json.load(handle)
            if not isinstance(state, dict) or any(not isinstance(v, dict) for v in state.values()):
                raise ValueError("invalid loop state")
        except FileNotFoundError:
            state = {}
        except (OSError, ValueError):
            return {"state": "error"}
        current = {n["gate"]: n for n in noisy}
        outcome = {}
        for gate in sorted(set(state) | set(current)):
            entry = state.setdefault(gate, {})
            if entry.get("intent"):
                result = _perform(state, gate, verb, state_path)
                outcome[gate] = result
                if result == "error":
                    continue
                entry = state.setdefault(gate, {})
            elif entry.get("open_key") and not entry.get("loop_id"):
                # Adopt an existing intent from the previous state format.
                if gate not in current:
                    outcome[gate] = "error"
                    continue
                _intend(state, gate, "add-loop", _open_payload(current[gate]), state_path)
                entry["intent"]["payload"]["idempotency_key"] = entry["open_key"]
                _save_state(state_path, state)
                outcome[gate] = _perform(state, gate, verb, state_path)
                if outcome[gate] == "error":
                    continue
            if entry.get("loop_id"):
                try:
                    remote = verb("read-loop", {"loop_id": entry["loop_id"]})
                except Exception:
                    remote = {}
                if (not isinstance(remote, dict) or remote.get("error")
                        or not isinstance(remote.get("version"), int) or not remote.get("status")):
                    outcome[gate] = "error"
                    continue
                if remote["status"] != "open":
                    if gate not in current:
                        state.pop(gate)
                        _save_state(state_path, state)
                        outcome[gate] = "closed"
                        continue
                    entry.clear()
                elif gate in current:
                    body = _loop_body(current[gate])
                    if remote.get("body") != body:
                        _intend(state, gate, "update-loop", {"loop_id": entry["loop_id"],
                                "base_version": remote["version"], "body": body}, state_path)
                        outcome[gate] = _perform(state, gate, verb, state_path)
                    else:
                        outcome.setdefault(gate, "open")
                    continue
                else:
                    _intend(state, gate, "close-loop", {"loop_id": entry["loop_id"],
                            "base_version": remote["version"], "resolution": "done",
                            "outcome": f"Gate {gate} dropped below the false-alarm threshold on a valid gate precision read."}, state_path)
                    outcome[gate] = _perform(state, gate, verb, state_path)
                    continue
            if gate in current:
                _intend(state, gate, "add-loop", _open_payload(current[gate]), state_path)
                outcome[gate] = _perform(state, gate, verb, state_path)
            elif gate in state:
                state.pop(gate)
                _save_state(state_path, state)
        return outcome


def _open_payload(n):
    return {"kind": "open_loop", "owner": "claude", "domain": "system", "blocker": "other_lane",
            "blocker_detail": "the gates lane (an orchestrator-dispatched platform session) owns the fix",
            "body": _loop_body(n)}


def call_verb(name, payload):
    from lib.record_call import call_verb as record_call
    result = record_call(name, payload, timeout=35)
    return result.reply if isinstance(result.reply, dict) else {"ok": False, "error": result.kind}


def _cmd_list(args, path):
    decisions, verdicts = read(path)
    now = time.time()
    shown = 0
    for d in reversed(decisions):
        if now - _epoch(d.get("ts")) > args.days * 86400:
            continue
        if args.gate and d.get("gate") != args.gate:
            continue
        verdict = verdicts.get(d.get("id"))
        if args.unlabelled and verdict:
            continue
        label = f"{verdict['label']} ({verdict.get('by')})" if verdict else "unlabelled"
        print(f"{d.get('id')}  {d.get('ts')}  {d.get('gate')}  {d.get('kind')}  "
              f"{d.get('rule')}  · {label}")
        shown += 1
        if shown >= args.limit:
            break
    return 0


def _cmd_label(args, path):
    if args.label not in ("right", "wrong"):
        print("gate-verdict: label must be right or wrong", file=sys.stderr)
        return 2
    if not (args.reason or "").strip():
        print("gate-verdict: --reason is required", file=sys.stderr)
        return 2
    decisions, _ = read(path)
    if not any(d.get("id") == args.decision_id for d in decisions):
        print(f"gate-verdict: unknown decision id {args.decision_id}", file=sys.stderr)
        return 2
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "type": "verdict", "decision_id": args.decision_id, "label": args.label,
            "by": args.by, "reason": args.reason.strip()[:300],
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}) + "\n")
    print(f"labelled {args.decision_id} {args.label}")
    return 0


def _cmd_backfill(args, path):
    """Seed the ledger from the meter's telemetry, which already holds every
    live refusal's gate, session and headline (not its input, so no digest)."""
    sys.path.insert(0, os.path.join(REPO, "hooks"))
    import gate_ledger
    added = 0
    for source in args.telemetry:
        with open(source, encoding="utf-8") as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("source") != "live" or r.get("outcome") not in gate_ledger.KIND:
                    continue
                if r.get("session") in args.skip_session:
                    continue
                _, wrote = gate_ledger.write_decision(path, r, backfilled=True)
                added += int(wrote)
    print(f"backfilled {added} decision(s)")
    return 0


def _cmd_report(args, path):
    stats = precision(path, args.days)
    if args.json:
        print(json.dumps(stats, indent=2, sort_keys=True))
        return 0
    print(f"gate precision, last {args.days} days (false alarms = wrong / labelled)")
    print(f"{'gate':<34} {'blocks':>6} {'labelled':>8} {'wrong':>5} {'false-alarm':>11}  top wrong rules")
    for gate, g in sorted(stats.items(), key=lambda kv: (-(kv[1]["wrong"]), kv[0])):
        top = ", ".join(f"{r} ×{c}" for r, c in g["top_wrong"])
        print(f"{gate:<34} {g['blocks']:>6} {g['labelled']:>8} {g['wrong']:>5} "
              f"{_fmt_rate(g['fa_rate']):>11}  {top}")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="gate-verdict")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_list = sub.add_parser("list")
    p_list.add_argument("--gate")
    p_list.add_argument("--days", type=float, default=WINDOW_DAYS)
    p_list.add_argument("--unlabelled", action="store_true")
    p_list.add_argument("--limit", type=int, default=40)
    p_label = sub.add_parser("label")
    p_label.add_argument("decision_id")
    p_label.add_argument("label")
    p_label.add_argument("--reason")
    p_label.add_argument("--by", default=os.environ.get("CARR_GATE_VERDICT_BY", "session"))
    p_report = sub.add_parser("report")
    p_report.add_argument("--days", type=float, default=WINDOW_DAYS)
    p_report.add_argument("--json", action="store_true")
    p_backfill = sub.add_parser("backfill")
    p_backfill.add_argument("telemetry", nargs="+")
    p_backfill.add_argument("--skip-session", action="append", default=[],
                            help="a session id whose rows are test traffic mislabelled live")
    args = parser.parse_args(argv)
    path = default_ledger()
    if args.cmd == "backfill":
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return _cmd_backfill(args, path)
    if not os.path.exists(path):
        print(f"gate-verdict: no decision ledger yet at {path}", file=sys.stderr)
        return 1
    return {"list": _cmd_list, "label": _cmd_label, "report": _cmd_report}[args.cmd](args, path)


if __name__ == "__main__":
    sys.exit(main())
