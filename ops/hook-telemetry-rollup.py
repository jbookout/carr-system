#!/usr/bin/env python3
"""hook-telemetry-rollup.py — what the enforcement stack cost, from live firings only.

    ./.venv/bin/python ops/hook-telemetry-rollup.py [--days 7] [--json] [--strict]

WHAT THIS ANSWERS, and it is the question the 2026-08-23 process-audit council
could not answer from the evidence that existed: 13 hook processes fire per Bash
tool call and 11 per turn on a 16GB machine — what does that actually cost, and
which of those gates has caught anything real? out/hook-guard.log could not say,
because live firings and selftest fixtures were written to the same file; its
134,595 DENY events were an upper bound with an unknown amount of test traffic
in it. hooks/hook_meter.py now splits those streams and hooks/hook-meter-run.py
records one structured line per firing. This reads the LIVE stream only.

REPORT, NEVER ACT. Nothing here deletes, disables or rewires a gate. It prints
four things per gate — p95 latency, live denies, turn reopens, and the 7-day
trend — plus the council's retire-candidate rule evaluated against real numbers.
A candidate is a thing to look at, and it is printed WITH the reason the gate
exists and any caveat recorded against it, because "zero denies this week" is
not by itself evidence that a gate is useless: a gate whose deterrent works
looks exactly like a gate nobody needs.

THE RETIRE-CANDIDATE RULE, verbatim from the council: zero live denies in 7 days
AND catch-class owned by pre-commit or ops/ci.sh AND on the hot path. Two of the
three are measured here — denies from the live stream, hot-path membership
derived from the wiring in ops/config/hooks.json rather than hand-declared, so
it cannot drift away from what is installed. The third is a judgment that has to
be checked by a person and recorded in ops/config/hook-catch-classes.json; until
it is, a gate is not a candidate. That is deliberate: the failure mode this
system keeps hitting is a control removed on an assumption about who else was
watching.

REOPENS ARE COUNTED SEPARATELY FROM DENIES, per gate per day, because they are
the expensive kind. A Stop gate that blocks hands the turn back to the model and
spends LLM tokens — so a stack that reopens turns on false positives violates
the standing no-more-tokens constraint by way of the thing enforcing it.

NO NEW SCHEDULED JOB. This is one step in bin/nightly.sh, which already runs.

Fixtures: ops/hook-telemetry-rollup-selftest.py
"""

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

REPO = os.environ.get("CARR_ROLLUP_REPO") or os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(REPO, "out")
WIRING = os.path.join(REPO, "ops", "config", "hooks.json")
CATCH = os.path.join(REPO, "ops", "config", "hook-catch-classes.json")

LIVE_STREAM = os.path.join(OUT, "hook-telemetry.jsonl")
FIXTURE_STREAM = os.path.join(OUT, "fixtures", "hook-telemetry-fixture.jsonl")
UNCLASSIFIED_STREAM = os.path.join(OUT, "hook-telemetry-unclassified.jsonl")

# Provisional, from the council. Starting SLOs to be confirmed or moved by the
# first weeks of data — printed as a comparison, never enforced.
BUDGET_MS = {"Bash": 150.0, "Stop": 300.0}
METER_SHARE_BUDGET = 2.0        # percent of hook time the instrument may cost

# Events that fire on ordinary work. A gate wired to one of these is on the hot
# path; a SessionStart gate is not, however slow it is.
HOT_EVENTS = ("PreToolUse", "PostToolUse", "Stop", "SubagentStop", "UserPromptSubmit")


def read_stream(path, days, now=None):
    """Records newer than `days`, from the stream and its one rotation."""
    now = now or datetime.now(timezone.utc)
    floor = now - timedelta(days=days)
    rows = []
    for candidate in (path + ".1", path):
        if not os.path.exists(candidate):
            continue
        with open(candidate, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue                    # a torn line is not a reason to stop
                stamp = parse_ts(rec.get("ts"))
                if stamp is None or stamp < floor:
                    continue
                rows.append(rec)
    return rows


def parse_ts(value):
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except Exception:
        return None


def count_lines(path):
    total = 0
    for candidate in (path + ".1", path):
        try:
            with open(candidate, "rb") as fh:
                total += sum(1 for _ in fh)
        except Exception:
            continue
    return total


def wiring():
    """{hook filename: {"events": set, "matchers": [..]}} straight from the wiring.

    Derived, not declared: a hand-kept list of which gates are hot would be one
    more thing to drift out of agreement with what is actually installed.
    """
    out = defaultdict(lambda: {"events": set(), "matchers": []})
    try:
        with open(WIRING, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return out
    for event, blocks in data.items():
        if not isinstance(blocks, list):
            continue
        for block in blocks:
            matcher = block.get("matcher") or ""
            for hook in block.get("hooks", []):
                cmd = hook.get("command", "")
                names = [part.rsplit("/", 1)[-1] for part in cmd.split()
                         if part.endswith(".py") and
                         part.rsplit("/", 1)[-1] != "hook-meter-run.py"]
                if not names:
                    continue
                # The wrapper is not a gate; the gate is the first .py it wraps,
                # and anything after that is that gate's ARGUMENT, not a second
                # gate. run-record-gate.py is wired twice and told which claim to
                # check — so the key here has to be the same composite the
                # telemetry records use, or the argument's name gets reported
                # every night as a gate that never fires.
                key = names[0] if len(names) == 1 else f"{names[0]} {names[1]}"
                out[key]["events"].add(event)
                out[key]["matchers"].append(matcher)
    return out


def catch_classes():
    try:
        with open(CATCH, "r", encoding="utf-8") as fh:
            return json.load(fh).get("hooks", {})
    except Exception:
        return {}


def pct(values, q):
    """Nearest-rank percentile — the ceil(q*n)th value. Explicit because n is
    often small here, and because an averaged 'p95' over 40 firings would be a
    number with no owner."""
    if not values:
        return None
    ordered = sorted(values)
    n = len(ordered)
    rank = int(q * n)
    if rank < q * n:                    # ceil, without importing math
        rank += 1
    return ordered[max(0, min(n - 1, rank - 1))]


def hot(entry):
    return bool(entry["events"] & set(HOT_EVENTS))


def owned_elsewhere(meta):
    owner = (meta or {}).get("also_caught_by") or "unverified"
    return owner.startswith("pre-commit:") or owner.startswith("ops-ci:")


def build(days, now=None):
    now = now or datetime.now(timezone.utc)
    rows = read_stream(LIVE_STREAM, days, now)
    wired = wiring()
    meta = catch_classes()

    per_hook = defaultdict(lambda: {
        "fires": 0, "ms": [], "denies": 0, "asks": 0, "errors": 0,
        "reopens": 0, "days": defaultdict(list), "deny_classes": defaultdict(int),
    })
    per_event_ms = defaultdict(list)
    # THE BUDGET IS ABOUT AN EVENT, NOT A HOOK, and confusing the two would have
    # made this report a false green. The council's ~150ms is what a Bash tool
    # call pays for its hooks — and a Bash call fires THIRTEEN of them. A table
    # of per-hook p95s all reading 80ms says "OK" while the event they belong to
    # costs an order of magnitude more. So firings are also grouped back into
    # the event that caused them.
    #
    # THE GROUPING IS APPROXIMATE AND MUST BE READ AS SUCH: the harness gives a
    # hook no per-event correlation id, so the key is session + event + the
    # second the firing was stamped with. Two tool calls inside one second in
    # one session merge into one apparent event, which overstates that event.
    # Both aggregates are reported because they bound the truth from either
    # side: SUM is the CPU the event cost, and MAX is the floor on its wall time
    # if the harness runs the hooks concurrently. The real wall cost sits
    # between them.
    per_event_group = defaultdict(list)
    reopen_by_day = defaultdict(lambda: defaultdict(int))
    meter_ms = []
    total_ms = 0.0

    for rec in rows:
        name = rec.get("hook") or "?"
        # run-record-gate.py is wired twice with different arguments and they are
        # different catches; keep them apart the way the wiring does.
        arg = rec.get("arg")
        if arg:
            name = f"{name} {arg}"
        bucket = per_hook[name]
        bucket["fires"] += 1
        elapsed = rec.get("elapsed_ms")
        day = (rec.get("ts") or "")[:10]
        if isinstance(elapsed, (int, float)):
            bucket["ms"].append(elapsed)
            bucket["days"][day].append(elapsed)
            total_ms += elapsed
            event = rec.get("event")
            if event:
                per_event_ms[event].append(elapsed)
                per_event_group[(rec.get("session"), event, rec.get("ts"))].append(elapsed)
        outcome = rec.get("outcome")
        if outcome == "deny":
            bucket["denies"] += 1
            klass = rec.get("deny_class") or rec.get("deny_headline") or "(no class declared)"
            bucket["deny_classes"][klass] += 1
        elif outcome == "ask":
            bucket["asks"] += 1
        elif outcome == "error":
            bucket["errors"] += 1
        if rec.get("reopen"):
            bucket["reopens"] += 1
            reopen_by_day[day][name] += 1
        if isinstance(rec.get("meter_ms"), (int, float)):
            meter_ms.append(rec["meter_ms"])

    events = {}
    for (_, event, _), group in per_event_group.items():
        slot = events.setdefault(event, {"sum": [], "max": [], "hooks": []})
        slot["sum"].append(sum(group))
        slot["max"].append(max(group))
        slot["hooks"].append(len(group))
    event_cost = {
        event: {
            "events_seen": len(slot["sum"]),
            "hooks_per_event": round(sum(slot["hooks"]) / max(1, len(slot["hooks"])), 1),
            "sum_p95_ms": pct(slot["sum"], 0.95),
            "max_p95_ms": pct(slot["max"], 0.95),
        }
        for event, slot in sorted(events.items())
    }

    hooks = {}
    for name, bucket in per_hook.items():
        base = name.split(" ")[0]
        entry = wired.get(name) or wired.get(base) or {"events": set(), "matchers": []}
        by_day = {d: pct(v, 0.95) for d, v in sorted(bucket["days"].items())}
        recent = list(by_day.values())
        trend = None
        if len(recent) >= 2:
            latest = recent[-1]
            earlier = [v for v in recent[:-1] if v]
            if latest and earlier:
                mid = sorted(earlier)[len(earlier) // 2]
                if mid:
                    trend = 100.0 * (latest - mid) / mid
        info = meta.get(base, {})
        hooks[name] = {
            "fires": bucket["fires"],
            "fires_per_day": round(bucket["fires"] / max(1, days), 1),
            "p50_ms": pct(bucket["ms"], 0.50),
            "p95_ms": pct(bucket["ms"], 0.95),
            "denies": bucket["denies"],
            "asks": bucket["asks"],
            "errors": bucket["errors"],
            "reopens": bucket["reopens"],
            "deny_classes": dict(bucket["deny_classes"]),
            "p95_trend_pct": trend,
            "events": sorted(entry["events"]),
            "hot_path": hot(entry),
            "catch_class": info.get("catch_class"),
            "also_caught_by": info.get("also_caught_by", "unverified"),
            "note": info.get("note"),
            "retire_candidate": bool(
                bucket["denies"] == 0 and owned_elsewhere(info) and hot(entry)),
        }

    unverified = [name for name, entry in hooks.items()
                  if entry["also_caught_by"] == "unverified"]
    wired_never_fired = sorted(key for key in wired if key not in hooks)

    meter_share = None
    if meter_ms and total_ms:
        meter_share = 100.0 * sum(meter_ms) / total_ms

    # A ROTATED-AWAY WINDOW MUST NOT PASS AS A FULL ONE. Only two generations of
    # the live stream survive, so on a busy enough week a "7-day trend" could
    # quietly be a four-day trend with a seven-day label — the dated-artifact
    # failure this system logs more than any other. Rather than guess a bigger
    # cap, say what the data actually covers, which stays correct at any cap.
    truncation = None
    stamps = [parse_ts(rec.get("ts")) for rec in rows]
    stamps = [s for s in stamps if s]
    if stamps and os.path.exists(LIVE_STREAM + ".1"):
        covered = (now - min(stamps)).total_seconds() / 86400.0
        if covered < days - 0.5:
            truncation = {"covered_days": round(covered, 1), "requested_days": days}

    return {
        "days": days,
        "generated": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "live_records": len(rows),
        "streams": {
            "live": count_lines(LIVE_STREAM),
            "fixture": count_lines(FIXTURE_STREAM),
            "unclassified": count_lines(UNCLASSIFIED_STREAM),
        },
        "hook_p95_ms": {ev: pct(v, 0.95) for ev, v in sorted(per_event_ms.items())},
        "event_cost": event_cost,
        "window_truncated_by_rotation": truncation,
        "budgets": BUDGET_MS,
        "meter_share_pct": meter_share,
        "meter_p50_ms": pct(meter_ms, 0.50),
        "meter_p95_ms": pct(meter_ms, 0.95),
        "hooks": hooks,
        "reopens_by_day": {d: dict(v) for d, v in sorted(reopen_by_day.items())},
        "unverified_owner_count": len(unverified),
        "wired_but_never_fired": wired_never_fired,
    }


def ms(value):
    return "—" if value is None else f"{value:.0f}"


def render(report):
    lines = []
    add = lines.append
    days = report["days"]
    add(f"hook-telemetry rollup — live firings only, {days} days to {report['generated']}")

    streams = report["streams"]
    unclassified_note = ""
    if streams["unclassified"]:
        unclassified_note = ("   <- should be 0; a gate is firing through a door that is "
                             "not hooks/hook-meter-run.py")
    add(f"  streams   live {streams['live']:,} · fixture {streams['fixture']:,} · "
        f"unclassified {streams['unclassified']:,}{unclassified_note}")

    if not report["live_records"]:
        add("")
        add("  NO LIVE TELEMETRY YET in this window. Either the wiring has not been")
        add("  installed on this machine (ops/config-as-code.py apply) or no session has")
        add("  run since it was. Nothing is wrong with the gates; there is simply nothing")
        add("  to roll up.")
        return "\n".join(lines)

    trunc = report.get("window_truncated_by_rotation")
    if trunc:
        add(f"  WINDOW    only {trunc['covered_days']} of the requested "
            f"{trunc['requested_days']} days survive in the live stream — the rest "
            f"rotated away.")
        add("            Raise ROTATE_BYTES in hooks/hook_meter.py or ask for fewer "
            "days; the trend below covers the shorter window, not the label.")

    for event, budget in sorted(BUDGET_MS.items()):
        measured = event if event != "Bash" else "PreToolUse"
        cost = report["event_cost"].get(measured)
        if not cost or cost["sum_p95_ms"] is None:
            continue
        verdict = "OK" if cost["sum_p95_ms"] <= budget else "OVER"
        label = "PreToolUse (Bash budget)" if event == "Bash" else event
        # The event, not one of its hooks — that distinction is the whole point.
        add(f"  budget    {label} p95 {ms(cost['sum_p95_ms'])}ms of hook CPU across "
            f"{cost['hooks_per_event']} hooks vs {budget:.0f}ms  {verdict}")
        add(f"            slowest single hook in one event, p95 "
            f"{ms(cost['max_p95_ms'])}ms — wall cost sits between the two")

    if report["meter_share_pct"] is not None:
        share = report["meter_share_pct"]
        verdict = "OK" if share <= METER_SHARE_BUDGET else "OVER"
        add(f"  instrument  meter p50 {ms(report['meter_p50_ms'])}ms "
            f"p95 {ms(report['meter_p95_ms'])}ms = {share:.2f}% of hook time "
            f"vs {METER_SHARE_BUDGET:.0f}%  {verdict}")

    add("")
    add(f"  {'gate':44} {'fires/d':>8} {'p50':>5} {'p95':>6} {'deny':>5} "
        f"{'reopen':>7} {'err':>4}  {'p95 7d':>7}")
    ordered = sorted(report["hooks"].items(),
                     key=lambda kv: (kv[1]["p95_ms"] or 0), reverse=True)
    for name, entry in ordered:
        trend = entry["p95_trend_pct"]
        trend_text = "—" if trend is None else f"{trend:+.0f}%"
        add(f"  {name[:44]:44} {entry['fires_per_day']:>8} {ms(entry['p50_ms']):>5} "
            f"{ms(entry['p95_ms']):>6} {entry['denies']:>5} {entry['reopens']:>7} "
            f"{entry['errors']:>4}  {trend_text:>7}")

    reopens = report["reopens_by_day"]
    add("")
    if reopens:
        add("  TURN REOPENS — a blocked Stop hands the turn back to the model and spends")
        add("  tokens. These are the firings that cost more than CPU.")
        for day, gates in reopens.items():
            detail = " · ".join(f"{g} {n}" for g, n in sorted(
                gates.items(), key=lambda kv: -kv[1]))
            add(f"    {day}  {detail}")
    else:
        add("  TURN REOPENS  none in this window.")

    candidates = [(n, e) for n, e in ordered if e["retire_candidate"]]
    add("")
    add("  RETIRE CANDIDATES (report only — zero live denies + catch-class owned")
    add("  elsewhere + on the hot path):")
    if candidates:
        for name, entry in candidates:
            add(f"    {name} — 0 denies in {days}d, class owned by "
                f"{entry['also_caught_by']}, fires {entry['fires_per_day']}/day")
            add(f"      catches: {entry['catch_class']}")
            if entry.get("note"):
                add(f"      CAVEAT: {entry['note']}")
    else:
        add("    none.")
    add(f"    {report['unverified_owner_count']} of {len(report['hooks'])} firing gates have no "
        "verified second owner recorded in")
    add("    ops/config/hook-catch-classes.json, so the rule cannot be evaluated for")
    add("    them at all. That is a research gap, not a clean bill.")

    if report["wired_but_never_fired"]:
        add("")
        add(f"  WIRED BUT SILENT in {days}d (fired zero times — its matcher may never")
        add("  match, or the window is short):")
        for name in report["wired_but_never_fired"]:
            add(f"    {name}")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--json", action="store_true", help="machine-readable report")
    ap.add_argument("--strict", action="store_true",
                    help="exit 1 when a provisional budget is exceeded "
                         "(off by default: the nightly chain reports, it does not fail)")
    args = ap.parse_args()

    report = build(max(1, args.days))
    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(render(report))

    if args.strict:
        over = []
        for event, budget in (("PreToolUse", BUDGET_MS["Bash"]), ("Stop", BUDGET_MS["Stop"])):
            cost = report["event_cost"].get(event) or {}
            observed = cost.get("sum_p95_ms")
            if observed is not None and observed > budget:
                over.append(f"{event} p95 {observed:.0f}ms > {budget:.0f}ms")
        share = report["meter_share_pct"]
        if share is not None and share > METER_SHARE_BUDGET:
            over.append(f"meter {share:.2f}% > {METER_SHARE_BUDGET:.0f}%")
        if over:
            sys.stderr.write("hook-telemetry-rollup: over budget — " + "; ".join(over) + "\n")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
