#!/usr/bin/env python3
"""deploy-ledger-vs-live.py — does the deployment ledger still describe the
Worker that is actually answering?

WHY THIS EXISTS. On 2026-08-19 and 2026-08-20 the ledger and production
disagreed THREE times, for three different reasons, and every one was found by a
human curling /release by hand rather than by any check:

  1. A promotion read its own identity back before Cloudflare had promoted the
     new provider version, judged it a mismatch, and recorded `failed` on code
     that was live and serving. (failure_class production_readback_mismatch)
  2. The performance budget clocked the whole 33-test golden suite and compared
     it to a per-request budget of 1000ms, so it failed EVERY promotion after
     everything real had passed. (failure_class performance_budget_exceeded)
  3. A promotion recorded `complete` with verb_count NULL. The baseline query
     requires a non-null count, so the guard silently kept reading an older row:
     ledger 141 while production served 143.

Three causes, one symptom, and nothing watching for the symptom. That is what
this file is: it does not care WHY the two disagree, only that they do.

WHY THE SYMPTOM IS WORTH ITS OWN CHECK rather than fixing each cause. The ledger
is not a diary — bin/deploy-worker.sh's verb-loss guard reads it to decide
whether a deploy is about to remove verbs, and that guard exists because
production silently went from 75 verbs to 66 on 2026-08-09 with nothing
objecting. A baseline that has drifted below reality re-opens exactly that hole:
with the ledger at 141 and production at 143, a deploy shipping 142 passes the
guard while dropping a live verb. Each cause above will be fixed and a fourth
will arrive; the drift is the thing to watch.

WHAT IT COMPARES. The live /release endpoint against the row
ops/last-deployed-verb-count.py calls the baseline — imported from that module,
never re-queried here, because two definitions of "baseline" would be free to
drift and disagreement is the very thing being measured (rule a8c55a47).

  verb_count           ledger BEHIND live  -> the guard would wave through a loss
                       ledger AHEAD of live -> verbs went missing, or a deploy
                                               recorded something it never shipped
  git_sha              a different commit is serving than the ledger's newest
  provider_version_id  the recorded provider version is not the one answering

Health-row contract per rule 590b11e1 (no metric without a bound action): the
output line names its response inline.

Prints ONE line. Exit 0 agree / 1 disagree / 2 SKIP.

  ops/deploy-ledger-vs-live.py
  ops/deploy-ledger-vs-live.py --service carr-mcp --environment production
  ops/deploy-ledger-vs-live.py --release-url https://example/release   (tests)
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_RELEASE_URL = "https://api.doctorcre.com/release"
REMEDY = ("on breach: read /release and the newest ops.deployment row together, "
          "then append a corrected row through tools/ops-record.py deployment — "
          "never edit the guard's baseline by hand")


def load_baseline_module():
    """ops/last-deployed-verb-count.py, for its baseline_row + credential path."""
    spec = importlib.util.spec_from_file_location(
        "last_deployed_verb_count", Path(__file__).with_name("last-deployed-verb-count.py"))
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load ops/last-deployed-verb-count.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Cloudflare answers urllib's default User-Agent with 403 error 1010 ("banned
# based on your browser's signature"), which is why bin/deploy-worker.sh reaches
# this same endpoint with curl and never met the problem. Send a name that says
# who is calling rather than impersonating a browser: if this check ever needs
# to be identified or rate-limited at the edge, it should be identifiable.
USER_AGENT = "carr-deploy-ledger-vs-live/1 (+ops/deploy-ledger-vs-live.py)"


def read_live(url, timeout=30):
    """The serving Worker's own account of itself. Returns dict or raises."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8"))


def unwrap(value):
    """/release reports several fields as {"value": x, "reason": ...}."""
    if isinstance(value, dict):
        return value.get("value")
    return value


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--service", default="carr-mcp")
    ap.add_argument("--environment", default="production")
    ap.add_argument("--release-url", default=DEFAULT_RELEASE_URL)
    a = ap.parse_args()

    # LIVE FIRST, and a SKIP if it cannot be read. An unreachable endpoint is not
    # evidence of drift, and a check that reports drift when it merely could not
    # look is worse than no check — that is the lesson of the readback mismatch
    # this file exists to catch.
    try:
        live = read_live(a.release_url)
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        print(f"SKIP deploy-ledger-vs-live: {a.release_url} unreadable "
              f"({type(exc).__name__})")
        return 2

    live_env = unwrap(live.get("env"))
    if live_env != a.environment:
        # env matters: git_sha and schema are identical across environments by
        # design, so without it you cannot tell which deployment answered.
        print(f"WARN deploy-ledger-vs-live — {a.release_url} answered for env "
              f"{live_env!r}, not {a.environment!r}; the comparison would be "
              f"meaningless · on breach: check the URL, not the ledger")
        return 1
    live_verbs = live.get("verb_count")
    live_sha = unwrap(live.get("git_sha"))
    live_version = unwrap(live.get("worker_version")) or (
        live.get("worker_version") or {}).get("id")

    try:
        mod = load_baseline_module()
        ops = mod.load_ops_record()
    except Exception as exc:  # noqa: BLE001
        print(f"SKIP deploy-ledger-vs-live: {type(exc).__name__}: {exc}")
        return 2

    try:
        conn = ops.connect("read")
    except SystemExit as exc:
        print(f"SKIP deploy-ledger-vs-live: {exc}")
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"SKIP deploy-ledger-vs-live: ledger unreachable ({type(exc).__name__})")
        return 2

    try:
        with conn, conn.cursor() as cur:
            row = mod.baseline_row(cur, a.service, a.environment)
    except Exception as exc:  # noqa: BLE001
        print(f"SKIP deploy-ledger-vs-live: ledger read failed ({type(exc).__name__})")
        return 2

    if not row:
        print(f"WARN deploy-ledger-vs-live — production serves {live_verbs} verbs and "
              f"the ledger has NO baseline row at all for {a.service}/{a.environment}, "
              f"so the verb-loss guard has nothing to refuse against · {REMEDY}")
        return 1

    led_verbs, led_sha, led_version, led_state, led_at = row

    disagreements = []
    if led_verbs != live_verbs:
        direction = ("BEHIND — a deploy shipping fewer verbs than production "
                     "already serves would pass the guard and drop live verbs"
                     if led_verbs < live_verbs else
                     "AHEAD — production is serving FEWER verbs than the ledger "
                     "recorded, which is the verb loss the guard exists to catch")
        disagreements.append(f"verb_count ledger {led_verbs} vs live {live_verbs}, {direction}")
    if led_sha and live_sha and led_sha != live_sha:
        disagreements.append(
            f"git_sha ledger {led_sha[:12]} vs live {live_sha[:12]} — a different "
            f"commit is answering than the newest shipped row describes")
    if led_version and live_version and led_version != live_version:
        disagreements.append(
            f"provider_version ledger {led_version} vs live {live_version}")

    stamp = str(led_at)[:16]
    if disagreements:
        print(f"WARN deploy-ledger-vs-live — the ledger does not describe what is "
              f"serving: {'; '.join(disagreements)} (baseline row state={led_state}, "
              f"observed {stamp}Z) · {REMEDY}")
        return 1

    print(f"OK deploy-ledger-vs-live — the ledger describes what is serving: "
          f"{live_verbs} verbs, {(live_sha or '?')[:12]}, provider version "
          f"{live_version} (baseline row state={led_state}, observed {stamp}Z) "
          f"· {REMEDY}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
