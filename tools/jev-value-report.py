#!/usr/bin/env python3
"""Jev value report: what Jev costs against the outcomes it can prove it changed.

Read-only and deterministic: no model calls, no record-layer calls. Reads
out/jev-calls.jsonl (the billed-call log), out/jev-judge.jsonl (per-check
verdicts), out/jev-required-actions-gate.jsonl, local git history, the price
in ops/config/jev-cost-guard.v1.json, and an optional GitHub CI snapshot that
`--fetch-ci` saves through `gh api` (REST) to out/jev-value-ci-snapshot.json.

THE COUNTING RULES, each one conservative on purpose:
  * A call is billed unless the vendor or our own cap refused it before any
    work (HTTP 402/403/429, daily cap, network never reached). Timeouts, 5xx
    and 400s stay in "paid" with no token count: they may have billed.
  * Dollars are input tokens at the repo's configured rate. The config has no
    output-token rate, so output dollars are UNKNOWN, never guessed.
  * Value is counted only for a verdict linked to an outcome: a commit whose
    message names Jev as the finder of what it fixes, or a gate block that
    stopped a write. A gate that only forces another Jev call is reported but
    is not value. Every other site prints "insufficient evidence".
  * Avoided time is priced from measured CI history: low = one median CI
    round per proven finding, high = one median PR's worth of CI rounds.
    Agent tokens saved are not measured anywhere in the repo and say so.

Usage: tools/jev-value-report.py [--days 7 | --since D --until D] [--json]
       tools/jev-value-report.py --fetch-ci [--days 14]
"""
from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parent.parent
PRICE_CONFIG = SOURCE_ROOT / "ops" / "config" / "jev-cost-guard.v1.json"
CI_SNAPSHOT = "jev-value-ci-snapshot.json"
FETCH_HINT = "run tools/jev-value-report.py --fetch-ci to save GitHub CI run history"
OUTPUT_PRICE_SOURCE = ("ops/config/jev-cost-guard.v1.json carries only "
                       "price_usd_per_million_input_tokens; the output rate belongs beside it, "
                       "taken from the TypeSafe invoice or its price_source_url")
TOKENS_SAVED_SOURCE = ("not measured: needs per-session Claude usage from transcripts, "
                       "compared across the Jev-off holdout")

# Errors that prove no billable work happened.
NOT_BILLED_CALL_ERRORS = frozenset({"HTTP 402", "HTTP 403", "HTTP 429", "daily_paid_call_cap", "network"})
NOT_BILLED_JUDGE_ERROR = re.compile(
    r"HTTP 40[23]|HTTP 429|billing|daily paid call cap|rate_limited|synthetic network|could not reach")

# What each call site's question is, which decides the recommendation when
# no outcome can be proven. mechanical: the inputs already hold the answer and
# code can compute it. per_turn: fires on routine turns. fixture: benchmark or
# selftest subjects billed on the live path. judgment_point: asked at a real
# decision. Unlisted names fall into review / ad_hoc_named by name.
NATURE = {
    "judge:supervise.stuck_and_drift": "mechanical",
    "judge:supervise.test_picker": "mechanical",
    "judge:supervise.path_repair": "mechanical",
    "judge:supervise.planted_instruction": "mechanical",
    "judge:supervise.review_triage": "mechanical",
    "judge:supervise.bug_locator": "mechanical",
    "judge:command_handoff": "mechanical",
    "judge:build_advisory": "per_turn",
    "judge:rule_select": "per_turn",
    "judge:supervise.tool_boundary": "per_turn",
    "judge:supervise.failure_triage": "per_turn",
    "judge:supervise.done_claim": "per_turn",
    "judge:supervise.stop_boundary": "per_turn",
    "judge:supervise.test_quality": "per_turn",
    "judge:requirement_checklist": "per_turn",
    "judge:executor_tier": "per_turn",
    "judge:post_write_task_fit": "per_turn",
    "judge:post_write_task_fit_shadow": "per_turn",
    "judge:unattributed": "per_turn",
    "jev_build_advisory": "per_turn",
    "jev_change_tolls": "per_turn",
    "legacy_unattributed": "per_turn",
    "judge:supervise.escalation_router": "fixture",
    "judge:supervise.effort_picker": "fixture",
    "judge:supervise.best_of": "fixture",
    "judge:supervise.ambiguity_stop": "fixture",
    "judge:supervise.example_picker": "fixture",
    "judge:supervise.context_picker": "fixture",
    "judge:supervise.plan_split": "fixture",
    "judge:supervise.notebook_recall": "fixture",
    "judge:supervise.runaway_thinking": "fixture",
    "review": "judgment_point",
    "jev_deal_read": "judgment_point",
    "ad_hoc_named": "judgment_point",
    "gate:jev_required_actions": "gate",
}
REVIEWISH = re.compile(r"review|diagnos|evidence|confirm|triage|defect", re.I)
JEV_FINDER = re.compile(r"\bJev(?:'s\s+[\w-]+)?\s+(?:flagged|caught|found|spotted|identified|surfaced|noticed)\b")
Z_ALPHA, Z_BETA = 1.959964, 0.841621  # two-sided 5%, 80% power
EFFECT_SHARE = 0.25  # detect a 25% change in mean CI rounds per PR


def site_for(name, judge_kind=False):
    if judge_kind and f"judge:{name}" in NATURE:
        return f"judge:{name}"
    if not judge_kind:
        if name is None:
            return "legacy_unattributed"
        if name == "jev_code_review":
            return "review"
        if name in NATURE:
            return name
    return "review" if REVIEWISH.search(name) else "ad_hoc_named"


def parse_time(value):
    if not isinstance(value, str):
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


def in_window(value, start, end):
    stamp = parse_time(value)
    return stamp is not None and start <= stamp < end


def _int(value):
    return value if type(value) is int and value >= 0 else None


def _new_site():
    return {"attempts": 0, "cache_hits": 0, "not_billed": 0, "paid": 0, "usable": 0,
            "unmetered_paid": 0, "input_tokens": 0, "output_tokens": 0, "wait_ms": 0,
            "outcomes_verified": 0, "blocks": 0, "evidence": [],
            "note": None}


def _tally(site, *, cache_hit, not_billed, usable, tokens_in, tokens_out, wait_ms=None):
    if cache_hit:
        site["cache_hits"] += 1
        return
    site["attempts"] += 1
    site["wait_ms"] += wait_ms or 0
    if not_billed:
        site["not_billed"] += 1
        return
    site["paid"] += 1
    site["usable"] += bool(usable)
    if tokens_in is None:
        site["unmetered_paid"] += 1
    else:
        site["input_tokens"] += tokens_in
        site["output_tokens"] += tokens_out or 0


def _call_fields(row):
    usage = row.get("usage") if isinstance(row.get("usage"), dict) else {}
    tokens_in = _int(row.get("input_tokens"))
    tokens_out = _int(row.get("output_tokens"))
    if tokens_in is None:
        tokens_in, tokens_out = _int(usage.get("input_tokens")), _int(usage.get("output_tokens"))
    usable = row.get("usable") if "usable" in row else row.get("ok")
    return {"cache_hit": row.get("cache_hit") is True,
            "not_billed": row.get("error") in NOT_BILLED_CALL_ERRORS,
            "usable": usable is True, "tokens_in": tokens_in, "tokens_out": tokens_out}


def _judge_fields(row):
    usage = row.get("usage") if isinstance(row.get("usage"), dict) else {}
    error = row.get("error") or ""
    return {"cache_hit": row.get("cache_hit") is True,
            "not_billed": bool(error) and bool(NOT_BILLED_JUDGE_ERROR.search(error)),
            "usable": not error and bool(row.get("answers")),
            "tokens_in": _int(usage.get("input_tokens")), "tokens_out": _int(usage.get("output_tokens")),
            "wait_ms": _int(row.get("elapsed_ms"))}


def baseline(ci, commits, start, end):
    base = {"ci_round_minutes_median": None, "ci_rounds_per_pr_median": None,
            "ci_rounds_per_pr_mean": None, "ci_rounds_per_pr_sd": None, "ci_failed_rounds_per_pr_mean": None,
            "prs_with_ci": 0, "merged_prs": None, "time_to_merge_hours_median": None,
            "reverts": sum(1 for c in commits if (c.get("subject") or "").startswith("Revert")),
            "main_canary_failures": None, "holdout_n_per_arm": None}
    if not ci:
        return base
    runs = [r for r in ci.get("runs") or [] if in_window(r.get("run_started_at"), start, end)]
    rounds, minutes, failed = defaultdict(int), [], defaultdict(int)
    for run in runs:
        if run.get("name") != "CI" or run.get("event") != "pull_request":
            continue
        if run.get("conclusion") not in ("success", "failure"):
            continue
        began, ended = parse_time(run.get("run_started_at")), parse_time(run.get("updated_at"))
        if began and ended:
            minutes.append((ended - began).total_seconds() / 60)
        rounds[run.get("head_branch")] += 1
        failed[run.get("head_branch")] += run["conclusion"] == "failure"
    counts = list(rounds.values())
    if minutes:
        base["ci_round_minutes_median"] = round(statistics.median(minutes), 1)
    if counts:
        base["prs_with_ci"] = len(counts)
        base["ci_rounds_per_pr_median"] = statistics.median(counts)
        base["ci_rounds_per_pr_mean"] = statistics.fmean(counts)
        base["ci_failed_rounds_per_pr_mean"] = round(statistics.fmean(failed.values()), 2)
    if len(counts) >= 2:
        sd = statistics.stdev(counts)
        base["ci_rounds_per_pr_sd"] = round(sd, 3)
        delta = EFFECT_SHARE * base["ci_rounds_per_pr_mean"]
        if sd > 0 and delta > 0:
            base["holdout_n_per_arm"] = math.floor(2 * (Z_ALPHA + Z_BETA) ** 2 * sd ** 2 / delta ** 2) + 1
    base["main_canary_failures"] = sum(1 for r in runs if "canary" in (r.get("name") or "").lower()
                                       and r.get("conclusion") == "failure")
    merged = [p for p in ci.get("pulls") or [] if in_window(p.get("merged_at"), start, end)]
    base["merged_prs"] = len(merged)
    hours = [(parse_time(p["merged_at"]) - parse_time(p["created_at"])).total_seconds() / 3600
             for p in merged if parse_time(p.get("created_at"))]
    if hours:
        base["time_to_merge_hours_median"] = round(statistics.median(hours), 2)
    return base


def recommend(key, site):
    if site["outcomes_verified"]:
        return "keep"
    nature = NATURE.get(key, "judgment_point")
    return {"fixture": "remove", "mechanical": "replace with code",
            "per_turn": "move to a judgment point", "gate": "move to a judgment point"
            }.get(nature, "keep (unproven; holdout decides)")


def build_report(sources, start, end):
    sites = defaultdict(_new_site)
    totals, hub_calls, hub_judge = _new_site(), _new_site(), _new_site()
    calls = sources.get("calls") or []
    # Before the call log named its callers, jev_judge traffic landed in
    # legacy_unattributed; judge rows from then are already billed there.
    hub_start = min((parse_time(r.get("ts")) for r in calls
                     if r.get("caller") == "jev_judge" and parse_time(r.get("ts"))), default=None)
    pre_hub = 0
    for row in calls:
        if not in_window(row.get("ts"), start, end):
            continue
        fields = _call_fields(row)
        _tally(totals, **fields)
        target = hub_calls if row.get("caller") == "jev_judge" else sites[site_for(row.get("caller"))]
        _tally(target, **fields)
    for row in sources.get("judge") or []:
        if not in_window(row.get("at"), start, end) or not row.get("kind"):
            continue
        site = sites[site_for(row["kind"], judge_kind=True)]
        ref = row.get("subject_ref")
        if row["kind"] == "post_write_task_fit" and isinstance(ref, dict) and ref.get("would_block") is True:
            site["blocks"] += 1
        if hub_start and parse_time(row["at"]) < hub_start:
            pre_hub += 1
            site["wait_ms"] += _int(row.get("elapsed_ms")) or 0  # the wait was real either way
            continue
        fields = _judge_fields(row)
        _tally(site, **fields)
        _tally(hub_judge, **fields)
    if hub_calls["attempts"]:
        # jev-judge.jsonl names the check for only part of what the call log
        # bills to jev_judge; the remainder is a site of its own, so the table
        # sums to the bill instead of quietly under-reporting it.
        residual = sites["judge:unattributed"]
        for field in ("paid", "usable", "unmetered_paid", "input_tokens", "output_tokens"):
            residual[field] = hub_calls[field] - hub_judge[field]
        # The judge log counts its own deadline misses as paid-unusable while
        # the call log may hold no row for them, so the usable difference can
        # exceed the paid one; cap it rather than print a rate above 100%.
        residual["usable"] = max(0, min(residual["usable"], residual["paid"]))
        residual["note"] = "billed to jev_judge in jev-calls.jsonl with no matching check row in jev-judge.jsonl"
    hub = {"calls_log_input_tokens": hub_calls["input_tokens"], "judge_log_input_tokens": hub_judge["input_tokens"],
           "pre_hub_judge_rows": pre_hub}
    gate = sites["gate:jev_required_actions"] if sources.get("gate") else None
    for row in sources.get("gate") or []:
        if in_window(row.get("ts"), start, end) and row.get("status") == "required" and row.get("missing"):
            gate["blocks"] += 1
    if gate is not None:
        gate["note"] = ("each block forces a Jev call before the turn may end; the outcome is Jev "
                        "usage itself, so it is excluded from value")
    commits = [c for c in sources.get("commits") or [] if in_window(c.get("date"), start, end)]
    for commit in commits:
        match = JEV_FINDER.search(commit.get("body") or "")
        if match:
            review = sites["review"]
            review["outcomes_verified"] += 1
            review["evidence"].append({"ref": commit["sha"], "quote": match.group(0)})
    base = baseline(sources.get("ci"), commits, start, end)
    price = sources.get("price") or {}
    rate_in = price.get("price_usd_per_million_input_tokens")
    rate_in = float(rate_in) if isinstance(rate_in, (int, float)) else None
    round_min, rounds_pr = base["ci_round_minutes_median"], base["ci_rounds_per_pr_median"]
    for key, site in list(sites.items()) + [("total", totals)]:
        site["usd_input"] = None if rate_in is None else site["input_tokens"] * rate_in / 1_000_000
        site["usable_rate"] = site["usable"] / site["paid"] if site["paid"] else None
        verified = site["outcomes_verified"]
        site["minutes_saved"] = (None if not verified or round_min is None or rounds_pr is None
                                 else (verified * round_min, verified * rounds_pr * round_min))
        site["minutes_per_usd"] = (None if not site["minutes_saved"] or not site["usd_input"]
                                   else tuple(m / site["usd_input"] for m in site["minutes_saved"]))
        if key != "total":
            site["recommendation"] = recommend(key, site)
    return {"window": {"start": start.isoformat(), "end": end.isoformat()},
            "price": {"usd_per_million_input": rate_in, "usd_per_million_output": None,
                      "input_source": str(price.get("price_source_url") or "absent"),
                      "output_source": OUTPUT_PRICE_SOURCE},
            "sites": dict(sites), "totals": totals, "judge_hub": hub, "baseline": base,
            "unreadable": sources.get("unreadable") or {}}


def _fmt_int(n):
    return f"{n:,}" if isinstance(n, int) else "-"


def _fmt_usd(v):
    return "UNKNOWN" if v is None else f"${v:,.3f}"


def _fmt_range(pair):
    return "-" if not pair else f"{pair[0]:,.0f}–{pair[1]:,.0f}"


def render(report):
    out = []
    w, price, totals, base = report["window"], report["price"], report["totals"], report["baseline"]
    out.append(f"JEV VALUE REPORT  {w['start'][:16]} → {w['end'][:16]} UTC")
    if price["usd_per_million_input"] is None:
        out.append("dollars: UNKNOWN — no price_usd_per_million_input_tokens in ops/config/jev-cost-guard.v1.json")
    else:
        out.append(f"input-token price: ${price['usd_per_million_input']}/M ({price['input_source']})")
    out.append(f"output-token price: UNKNOWN — {price['output_source']}")
    for path, n in sorted(report["unreadable"].items()):
        out.append(f"{path}: {n} unreadable line{'s' if n != 1 else ''} skipped")
    out.append("")
    out.append("COST (billed-call log, all callers)")
    rate = totals["usable_rate"]
    out.append(f"  attempts {_fmt_int(totals['attempts'])} · not billed (refused/cap) {_fmt_int(totals['not_billed'])}"
               f" · paid {_fmt_int(totals['paid'])} · usable {_fmt_int(totals['usable'])}"
               f" ({'-' if rate is None else f'{rate:.0%}'}) · cache hits {_fmt_int(totals['cache_hits'])}")
    out.append(f"  tokens in {_fmt_int(totals['input_tokens'])} · out {_fmt_int(totals['output_tokens'])}"
               f" · paid with no token count {_fmt_int(totals['unmetered_paid'])}")
    out.append(f"  dollars (input only) {_fmt_usd(totals['usd_input'])} · output dollars UNKNOWN")
    hub = report["judge_hub"]
    out.append(f"  jev_judge hub: {_fmt_int(hub['calls_log_input_tokens'])} input tokens in the call log vs "
               f"{_fmt_int(hub['judge_log_input_tokens'])} attributed by check in jev-judge.jsonl"
               f" ({_fmt_int(hub['pre_hub_judge_rows'])} earlier check rows already billed as legacy_unattributed)")
    out.append("")
    out.append("VERDICT TABLE (per call site; value counted only where linked to an outcome)")
    header = (f"  {'site':<36} {'paid':>7} {'usable':>7} {'tok in':>11} {'$ in':>8} {'wait h':>7}"
              f" {'proven':>6} {'min saved':>11} {'min/$':>13}  recommendation")
    out.append(header)
    order = sorted(report["sites"].items(), key=lambda kv: (-kv[1]["input_tokens"], -kv[1]["blocks"], kv[0]))
    for key, s in order:
        rate = s["usable_rate"]
        out.append(f"  {key:<36} {_fmt_int(s['paid']):>7} {'-' if rate is None else f'{rate:.0%}':>7}"
                   f" {_fmt_int(s['input_tokens']):>11} {_fmt_usd(s['usd_input']):>8}"
                   f" {s['wait_ms'] / 3_600_000:>7.2f} {s['outcomes_verified']:>6}"
                   f" {_fmt_range(s['minutes_saved']):>11} {_fmt_range(s['minutes_per_usd']):>13}"
                   f"  {s['recommendation']}")
    out.append("")
    out.append("EVIDENCE")
    for key, s in order:
        if s["outcomes_verified"]:
            refs = ", ".join(f"{e['ref']} (\"{e['quote']}\")" for e in s["evidence"])
            out.append(f"  {key}: {s['outcomes_verified']} finding(s) confirmed by fix commit: {refs}")
            if s["minutes_saved"] is None:
                out.append(f"  {key}: minutes saved UNKNOWN — no CI history; {FETCH_HINT}")
        elif s["blocks"]:
            out.append(f"  {key}: {s['blocks']} block(s), not counted as value — {s['note'] or 'correctness unverified'}")
        else:
            out.append(f"  {key}: insufficient evidence — no verdict links to a later change, fix or CI routing"
                       + (f" ({s['note']})" if s["note"] else ""))
    out.append(f"  agent tokens saved, every site: {TOKENS_SAVED_SOURCE}")
    out.append("")
    out.append("MEASURED BASELINE (for value ranges and the holdout)")
    if base["ci_round_minutes_median"] is None:
        out.append(f"  CI history absent — {FETCH_HINT}")
    else:
        out.append(f"  median CI round {base['ci_round_minutes_median']} min · rounds per PR median "
                   f"{base['ci_rounds_per_pr_median']} mean {base['ci_rounds_per_pr_mean']:.2f}"
                   f" sd {base['ci_rounds_per_pr_sd']} · failed rounds per PR mean "
                   f"{base['ci_failed_rounds_per_pr_mean']} · PRs with CI {base['prs_with_ci']}")
        out.append(f"  merged PRs {base['merged_prs']} · median time to merge {base['time_to_merge_hours_median']} h"
                   f" · main canary failures {base['main_canary_failures']} · reverts {base['reverts']}")
        out.append(f"  holdout n per arm to detect a {EFFECT_SHARE:.0%} change in CI rounds/PR"
                   f" (α=.05 two-sided, power .8): {base['holdout_n_per_arm']}")
    out.append("")
    out.append("METHOD: value low = one median CI round per proven finding; high = one median PR's"
               " CI rounds. Billed = not refused by vendor/cap; failures without token counts are"
               " counted as paid but unpriced. Wait h = summed Jev latency recorded by jev-judge.")
    proven = sum(s["outcomes_verified"] for s in report["sites"].values())
    saved = [s["minutes_saved"] for s in report["sites"].values() if s["minutes_saved"]]
    low, high = sum(p[0] for p in saved), sum(p[1] for p in saved)
    wait = sum(s["wait_ms"] for s in report["sites"].values()) / 3_600_000
    out.append(f"BOTTOM LINE: cost {_fmt_usd(totals['usd_input'])} input-only + output UNKNOWN,"
               f" {_fmt_int(totals['paid'])} paid calls, {wait:.1f} h Jev wait; proven value"
               f" {proven} finding(s) worth {'UNKNOWN' if proven and not saved else f'{low:,.0f}–{high:,.0f}'}"
               f" CI minutes; {sum(1 for s in report['sites'].values() if not s['outcomes_verified'])}"
               f" of {len(report['sites'])} sites have insufficient evidence.")
    return "\n".join(out)


def data_root():
    try:
        common = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
                                cwd=SOURCE_ROOT, capture_output=True, text=True, check=True,
                                timeout=5).stdout.strip()
        return Path(common).parent if common else SOURCE_ROOT
    except (OSError, subprocess.SubprocessError):
        return SOURCE_ROOT


def read_jsonl(path, unreadable):
    rows, bad = [], 0
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except ValueError:
                    bad += line.strip() != ""
                    continue
                if isinstance(row, dict):
                    rows.append(row)
    except OSError:
        return rows
    if bad:
        unreadable[str(path)] = bad
    return rows


def read_commits(root, start, end):
    ref = "origin/main"
    if subprocess.run(["git", "rev-parse", "--verify", "-q", ref], cwd=root,
                      capture_output=True).returncode:
        ref = "HEAD"
    text = subprocess.run(["git", "log", ref, f"--since={start.isoformat()}", f"--until={end.isoformat()}",
                           "--format=%h%x1f%aI%x1f%s%x1f%b%x1e"], cwd=root, capture_output=True,
                          text=True, check=True).stdout
    commits = []
    for record in text.split("\x1e"):
        parts = record.strip("\n").split("\x1f")
        if len(parts) == 4:
            commits.append({"sha": parts[0], "date": parts[1], "subject": parts[2], "body": parts[3]})
    return commits


def _gh(args):
    return subprocess.run(args, capture_output=True, text=True, check=True, cwd=SOURCE_ROOT).stdout


def fetch_ci(root, start, end, gh=_gh):
    """Save GitHub Actions runs and closed PRs for [start, end) through gh REST.

    The runs endpoint returns at most 1000 results per filtered query, and this
    repo runs ~300 a day, so runs are asked for one UTC day at a time.
    """
    jq_run = ".workflow_runs[] | {name, event, conclusion, head_branch, run_started_at, updated_at, run_attempt}"
    lines, day = [], start.date()
    while day < (end + timedelta(seconds=-1)).date() + timedelta(days=1):
        lines += gh(["gh", "api", "--paginate",
                     f"repos/{{owner}}/{{repo}}/actions/runs?per_page=100&created={day.isoformat()}",
                     "--jq", jq_run]).splitlines()
        day += timedelta(days=1)
    since = start.date().isoformat()
    pulls = gh(["gh", "api", "--paginate",
                "repos/{owner}/{repo}/pulls?state=closed&per_page=100&sort=updated&direction=desc",
                "--jq", f'.[] | select(.updated_at >= "{since}") | {{number, created_at, merged_at, head: .head.ref}}'])
    snapshot = {"fetched_at": datetime.now(timezone.utc).isoformat(), "since": since,
                "runs": [json.loads(line) for line in lines if line.strip()],
                "pulls": [json.loads(line) for line in pulls.splitlines() if line.strip()]}
    path = Path(root) / "out" / CI_SNAPSHOT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snapshot), encoding="utf-8")
    return path, snapshot


def _day(text):
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--since", help="UTC date, inclusive (overrides --days)")
    parser.add_argument("--until", help="UTC date, exclusive (default now)")
    parser.add_argument("--root", help="directory holding out/ (default: the main checkout)")
    parser.add_argument("--price-config", default=str(PRICE_CONFIG))
    parser.add_argument("--ci-snapshot", help=f"default out/{CI_SNAPSHOT}")
    parser.add_argument("--fetch-ci", action="store_true", help="save CI history via gh api, then report")
    parser.add_argument("--no-git", action="store_true", help="skip local git history")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    end = _day(args.until) if args.until else datetime.now(timezone.utc)
    start = _day(args.since) if args.since else end - timedelta(days=args.days)
    root = Path(args.root) if args.root else data_root()
    out = root / "out"
    unreadable = {}
    ci = None
    if args.fetch_ci:
        path, ci = fetch_ci(root, start, end)
        print(f"saved {len(ci['runs'])} runs and {len(ci['pulls'])} PRs to {path}", file=sys.stderr)
    else:
        snapshot = Path(args.ci_snapshot) if args.ci_snapshot else out / CI_SNAPSHOT
        if snapshot.is_file():
            ci = json.loads(snapshot.read_text(encoding="utf-8"))
    try:
        price = json.loads(Path(args.price_config).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        price = None
    sources = {
        "calls": read_jsonl(out / "jev-calls.jsonl", unreadable),
        "judge": read_jsonl(out / "jev-judge.jsonl", unreadable),
        "gate": [row for gate in sorted(out.glob("jev-*-gate.jsonl")) for row in read_jsonl(gate, unreadable)],
        "commits": [] if args.no_git else read_commits(root, start, end),
        "ci": ci, "price": price, "unreadable": unreadable,
    }
    report = build_report(sources, start, end)
    print(json.dumps(report, indent=2, default=list) if args.json else render(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
