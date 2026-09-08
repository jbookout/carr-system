#!/usr/bin/env python3
"""Seeded-failure tests for the backup staleness alarm.

Every case here is one of the ways the 2026-08/09 backup outage actually hid.
The alarm is only worth having if it fires on each of them, so each is seeded
deliberately rather than asserted about in prose.
"""
from __future__ import annotations

import importlib.util
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "backup_freshness_watch", REPO / "tools" / "backup-freshness-watch.py")
if SPEC is None or SPEC.loader is None:
    raise SystemExit("backup-freshness-watch-selftest: cannot load the watcher")
w: Any = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(w)

FAILURES: list[str] = []
NOW = datetime(2026, 9, 3, 4, 0, tzinfo=timezone.utc)


def check(label: str, ok: bool) -> None:
    print(("  ok   " if ok else "  FAIL ") + label)
    if not ok:
        FAILURES.append(label)


def days_ago(n: float) -> datetime:
    return NOW - timedelta(days=n)


def main() -> int:
    # --- the assessment itself -------------------------------------------
    both_fresh = w.assess(days_ago(0.5), days_ago(0.5), 0, NOW)
    check("both paths current is the only state that reports ok",
          both_fresh["ok"] and both_fresh["severity"] == "ok")

    local_dead = w.assess(days_ago(17), days_ago(0.9), 0, NOW)
    check("the real 2026-09-03 state alarms: local 17 days, cloud current",
          not local_dead["ok"]
          and local_dead["local"]["stale"] and not local_dead["cloud"]["stale"]
          and "ONE BACKUP PATH" in local_dead["severity"])

    # THE CORRECTION THAT COST A WRONG PUBLIC CLAIM. On 2026-09-02 a session
    # checked one path, found it dead, and announced seventeen days with no
    # backup. The other path had succeeded the day before. One dead path must
    # never render as "no backups".
    check("a dead local path does NOT report the cloud path as stale",
          local_dead["cloud"]["age_days"] == 0.9 and not local_dead["cloud"]["stale"])

    cloud_dead = w.assess(days_ago(0.5), days_ago(6), 6, NOW)
    check("the reverse asymmetry holds too: cloud dead, local current",
          not cloud_dead["ok"]
          and cloud_dead["cloud"]["stale"] and not cloud_dead["local"]["stale"])

    both_dead = w.assess(days_ago(17), days_ago(9), 6, NOW)
    check("both stale escalates above one stale",
          both_dead["severity"] == "BOTH BACKUP PATHS ARE STALE")

    # THE WORST CASE, AND THE ONE A FAILURE-TRIGGERED ALARM CANNOT SEE. A path
    # that never ran emits no failure event at all. Absence must read as stale,
    # never as "nothing wrong".
    never = w.assess(None, None, 0, NOW)
    check("a path that has NEVER succeeded is stale, not healthy",
          not never["ok"] and never["local"]["stale"] and never["cloud"]["stale"]
          and never["local"]["last_success"] is None)
    check("the never-succeeded case says so in words, not as a blank",
          "NO SUCCESSFUL BACKUP ON RECORD AT ALL" in w.alarm_text(never))

    # The boundary either side of the threshold, because an off-by-one here
    # means the alarm fires a day late or a day early forever.
    check("just inside the threshold is current",
          w.assess(days_ago(1.9), days_ago(1.9), 0, NOW)["ok"])
    check("just past the threshold is stale",
          not w.assess(days_ago(2.1), days_ago(1.9), 0, NOW)["ok"])

    # --- escalation, so six identical lines do not read as one ------------
    run = w.alarm_text(w.assess(days_ago(0.5), days_ago(6), 6, NOW))
    check("a run of failures is called a run, with its count",
          "6 consecutive failed runs" in run and "not a single bad night" in run)
    single = w.alarm_text(w.assess(days_ago(0.5), days_ago(6), 1, NOW))
    check("a single failure is NOT dressed up as a run", "consecutive failed runs" not in single)

    # --- reading the local path ------------------------------------------
    with tempfile.TemporaryDirectory() as raw:
        backups = Path(raw)
        check("an empty backups directory reports no success, not a fresh one",
              w.local_last_success(backups) is None)
        check("a missing backups directory does not crash",
              w.local_last_success(backups / "absent") is None)

        # THE MTIME TRAP. A checkout, clone or worktree rewrites every mtime to
        # now. Trusting mtime would make a three-week-old dump look like
        # today's and turn this check green on a machine that has never taken
        # one. The stamp in the NAME is the only honest clock here.
        (backups / "carr-20260817.sql.age").write_text("x")     # old name, mtime = now
        found = w.local_last_success(backups)
        check("the dump's date comes from its NAME, never its mtime",
              found is not None and found.date().isoformat() == "2026-08-17")

        (backups / "carr-20260903.sql.age").write_text("x")
        check("the NEWEST dump wins, not the last one listed",
              w.local_last_success(backups).date().isoformat() == "2026-09-03")

        (backups / "carr-notadate.sql.age").write_text("x")
        (backups / "README.md").write_text("x")
        check("unparseable and unrelated files are ignored, not crashed on",
              w.local_last_success(backups).date().isoformat() == "2026-09-03")

    # --- reading the cloud path ------------------------------------------
    def run_row(when: str, conclusion: str | None, status: str = "completed") -> dict:
        return {"createdAt": when, "conclusion": conclusion, "status": status, "url": "u"}

    last, failures = w.cloud_state([
        run_row("2026-09-03T02:00:00Z", None, status="in_progress"),
        run_row("2026-09-02T02:00:00Z", "success"),
    ])
    check("a run still in flight is neither a success nor a failure",
          last is not None and last.date().isoformat() == "2026-09-02" and failures == 0)

    last, failures = w.cloud_state([
        run_row("2026-09-01T02:00:00Z", "failure"),
        run_row("2026-08-31T02:00:00Z", "failure"),
        run_row("2026-08-30T02:00:00Z", "failure"),
        run_row("2026-08-29T02:00:00Z", "success"),
        run_row("2026-08-28T02:00:00Z", "failure"),
    ])
    check("failures are counted only back to the last success, not forever",
          last is not None and last.date().isoformat() == "2026-08-29" and failures == 3)

    last, failures = w.cloud_state([run_row("2026-09-01T02:00:00Z", "failure")])
    check("a history with no success at all reports none",
          last is None and failures == 1)
    check("an empty run history reports none rather than assuming health",
          w.cloud_state([]) == (None, 0))

    if FAILURES:
        print(f"backup-freshness-watch-selftest: {len(FAILURES)} FAILED")
        return 1
    print("backup-freshness-watch-selftest: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
