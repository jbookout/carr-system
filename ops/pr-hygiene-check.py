#!/usr/bin/env python3
"""ops/pr-hygiene-check.py — notices a pull request that nobody is coming back for.

WHY THIS EXISTS, in Joe's own question on 2026-08-14: "so if i don't have the
emails and things sit until they are noticed how will the system fix them
consistently and timely". It was the right objection to the answer he had just
been given. GitHub's failure emails were the ONLY thing watching the repo, they
went to him personally because every session pushes as him, and turning them off
would have left nothing at all.

WHAT WENT UNNOTICED THAT DAY. Pull request #79 was opened at 13:09Z and sat for
eight and a half hours: no CI run against it ever, a merge conflict against main,
and no session coming back. Its change was still needed — a gate was refusing an
orchestrating session's messages to its OWN subagents — and it merged that
evening as #167 once someone rebased it by hand. It was found because a session
went looking, which is not a mechanism.

WHAT THIS DOES NOT DO. It fixes nothing and it writes nothing. It reports, with a
bound action per finding (rule 590b11e1), on the surface Joe already reads:
`run.sh health`. The nightly chain runs that, so a stranded pull request surfaces
within a day without any inbox involved.

WHO CLEARS WHAT THIS NOTICES: ops/pr_actor.py, and the bound action on every
finding here names it. This half NOTICES, that half CLEARS, split on purpose so
only one of them can write. Keep the two in step — this file shipped 44 minutes
BEFORE the actor did, on 2026-08-14, and its remedy text went on telling readers
to hand-rebase for a day after code took the job over. A nightly run on
2026-08-15 followed that stale text and proposed exactly the manual path Joe had
already ruled out twice (rule 5e89c211: never spend a model on a decision already
expressible as tested code). A bound action that names the wrong actor is worse
than none, because it reads as current.

THE ONE FINDING THAT STAYS HUMAN is stale-red. The actor refuses to touch a red
check by design — rebasing one buries a possibly-real failure under a fresh run —
so that action still points at a person, and that is not drift.

WHY THE THRESHOLDS ARE WIDE. A red pull request minutes old is a session
mid-iteration, and CI failing IS the feedback loop working — on 2026-08-14 the
repo took 397 runs with 21 failures spread across 12 branches, nearly all of them
fixed by the session that caused them within minutes. An alarm that fires on
normal building is an alarm everyone learns to ignore, which is the failure mode
the vault drift watch's own comments warn about twice. These thresholds are set
so an ordinary build loop never trips them.

RECENCY IS MEASURED ON THE LAST PUSH, never on when the pull request was opened.
A branch opened days ago and pushed to a minute ago has someone on it.

RUN IT:
    python3 ops/pr-hygiene-check.py              # human-readable, exit 1 on findings
    python3 ops/pr-hygiene-check.py --json       # machine-readable
    python3 ops/pr-hygiene-check.py --health-row # one line for tools/health-check.py
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import subprocess
import sys

# ── thresholds, in minutes ──────────────────────────────────────────────────
# Each is deliberately generous. See the module docstring on why a tight
# threshold here would be worse than no check at all.
RED_STALE_MINUTES = 180        # red checks, untouched for 3h
NO_CHECKS_MINUTES = 90         # open 1.5h, head commit unchecked — usually a dead session
CONFLICT_MINUTES = 180         # conflicted against main for 3h

_FIELDS = ("number,title,headRefName,isDraft,createdAt,updatedAt,"
           "mergeStateStatus,statusCheckRollup")


def _parse(iso: str) -> _dt.datetime:
    return _dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))


def minutes_before(now_iso: str, minutes: int) -> str:
    """Test helper: an ISO timestamp `minutes` before `now_iso`."""
    t = _parse(now_iso) - _dt.timedelta(minutes=minutes)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _age_minutes(iso: str, now: _dt.datetime) -> float:
    return (now - _parse(iso)).total_seconds() / 60.0


def _rollup_state(row: dict) -> str | None:
    """FAILURE / SUCCESS / IN_PROGRESS, or None when the HEAD COMMIT has no check.

    None and FAILURE are genuinely different findings and must never be
    reported as the same one (rule 88e9b5eb): a red pull request had a session
    that got far enough to run CI; a check-less head usually did not.

    None means THIS COMMIT is unchecked, never that the branch was never built.
    GitHub's rollup describes the head only, so a pull request whose earlier
    commit passed and whose later push ran nothing reads None here — which is
    exactly #179 on 2026-08-15, and exactly the overstatement that reached Joe
    when this returned a sentence saying CI had never run.
    """
    roll = row.get("statusCheckRollup") or []
    if not roll:
        return None
    states = [(c.get("conclusion") or c.get("status") or "").upper() for c in roll]
    if any(s in ("IN_PROGRESS", "QUEUED", "PENDING", "") for s in states):
        return "IN_PROGRESS"
    if any(s in ("FAILURE", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED") for s in states):
        return "FAILURE"
    return "SUCCESS"


def classify(rows: list[dict], now_iso: str | None = None) -> list[dict]:
    """Pure. Rows in, findings out. No network, so the selftest is deterministic."""
    now = _parse(now_iso) if now_iso else _dt.datetime.now(_dt.timezone.utc)
    findings: list[dict] = []

    for row in rows:
        # A draft is a session saying out loud that it is not done. Never flag one.
        if row.get("isDraft"):
            continue

        idle = _age_minutes(row.get("updatedAt") or row["createdAt"], now)
        state = _rollup_state(row)
        num = row["number"]
        title = row.get("title") or row.get("headRefName") or f"pull request {num}"
        branch = row.get("headRefName") or "?"
        merge = (row.get("mergeStateStatus") or "").upper()

        def add(kind: str, why: str, action: str) -> None:
            findings.append({
                "kind": kind, "number": num, "title": title, "branch": branch,
                "idle_minutes": round(idle), "why": why, "action": action,
            })

        if state == "IN_PROGRESS":
            continue

        if state is None and idle >= NO_CHECKS_MINUTES:
            # WORDING IS LOAD-BEARING HERE, corrected 2026-08-15. This said "CI
            # has never run against it", and a session repeated that to Joe about
            # #179 — where CI had in fact run and PASSED on an earlier commit,
            # and a later push left the CURRENT HEAD unchecked. The rollup this
            # reads describes the head commit only, so the classification was
            # right and the sentence was not. An overstated finding is how a
            # correct detector loses its reader.
            add("no-checks",
                f"open {idle/60:.1f}h with no CI run against its CURRENT HEAD "
                f"(an earlier commit may well have passed; this reads the head only)",
                f"`python3 ops/pr_actor.py` to see the plan, `--execute` to act "
                f"— it decides #{num} and clears it.")
        elif state == "FAILURE" and idle >= RED_STALE_MINUTES:
            add("stale-red",
                f"CI red and nothing pushed for {idle/60:.1f}h",
                f"`gh run view --log-failed` on its latest run, fix or close: "
                f"`gh pr view {num}`. The actor refuses a red check on purpose, "
                f"so this one is genuinely yours.")

        if merge == "DIRTY" and idle >= CONFLICT_MINUTES:
            add("conflicted",
                f"conflicted against main for {idle/60:.1f}h",
                f"`python3 ops/pr_actor.py` to see the plan, `--execute` to act "
                f"— it decides #{num} and clears it.")

    findings.sort(key=lambda f: -f["idle_minutes"])
    return findings


def fetch(repo: str | None = None) -> list[dict]:
    cmd = ["gh", "pr", "list", "--state", "open", "--limit", "100", "--json", _FIELDS]
    if repo:
        cmd += ["--repo", repo]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
    if p.returncode != 0:
        raise RuntimeError((p.stderr or p.stdout or "gh failed").strip()[:300])
    return json.loads(p.stdout or "[]")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true", help="machine-readable findings")
    ap.add_argument("--health-row", action="store_true",
                    help="one line in tools/health-check.py's idiom")
    ap.add_argument("--repo", default=None)
    args = ap.parse_args()

    try:
        rows = fetch(args.repo)
    except Exception as e:
        # An unreachable GitHub is NOT a clean repo. Saying so is the whole point
        # of rule 2b889e80: no negative finding from a single collection.
        msg = f"UNKNOWN — could not read pull requests ({type(e).__name__}: {e})"
        if args.health_row:
            print(f"  ⚠︎ stranded PRs        {msg} · on breach: run "
                  f"`gh pr list --state open` by hand")
        elif args.json:
            print(json.dumps({"ok": False, "error": str(e)}))
        else:
            print(msg)
        return 1

    findings = classify(rows)

    if args.json:
        print(json.dumps({"ok": True, "open": len(rows), "findings": findings}, indent=2))
        return 1 if findings else 0

    if args.health_row:
        if not findings:
            print(f"  OK stranded PRs        none of {len(rows)} open "
                  f"({RED_STALE_MINUTES//60}h red / {NO_CHECKS_MINUTES//60}h no-CI / "
                  f"{CONFLICT_MINUTES//60}h conflicted)")
            return 0
        # Count PRs, not findings. One pull request can raise two (no-checks AND
        # conflicted), so `len(findings)` printed against `len(rows)` produced
        # "5 of 3 open" on 2026-08-15 — a count that cannot be true, which cost a
        # nightly run a paragraph arguing the arithmetic was broken. It was not;
        # the label was.
        stranded = len({f["number"] for f in findings})
        worst = findings[0]
        print(f"  ⚠︎ stranded PRs        {stranded} of {len(rows)} open "
              f"({len(findings)} finding(s)), oldest "
              f"#{worst['number']} {worst['why']} · on breach: {worst['action']}")
        return 1

    if not findings:
        print(f"no stranded pull requests ({len(rows)} open, all active or green)")
        return 0

    print(f"{len(findings)} stranded pull request finding(s) out of {len(rows)} open:\n")
    for f in findings:
        print(f"  [{f['kind']}] #{f['number']} — {f['title']}")
        print(f"      branch {f['branch']}, {f['why']}")
        print(f"      do: {f['action']}\n")
    return 1


if __name__ == "__main__":
    sys.exit(main())
