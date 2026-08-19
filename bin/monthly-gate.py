#!/usr/bin/env python3
"""
monthly-gate.py — answer "has this monthly routine already done its work this
calendar month?" as a PREDICATE, before any model reasoning happens.

WHY THIS EXISTS (2026-08-19). Four monthly routines fire on a WINDOW of days
rather than one fixed day, because a single-day cron on a laptop loses the whole
month if the Mac is asleep at 9am:

    playbook-review-monthly   0 9 15-21 * *   7 firings
    system-sweep-monthly     30 8 15-21 * *   7 firings
    idea-resurface-monthly    0 9  5-11 * *   7 firings
    health-audit-monthly      0 9  6-10 * *   5 firings

Exactly one firing per routine does the work; a STEP 0 gate inside each routine
sends the rest home. That design is correct. What was wrong is HOW the gate got
evaluated: by booting a full model session that called standing-context, read an
entire doctrine document, and queried the decision record — roughly 22 sessions a
month spent answering a recurrence question. Rule 5e89c211 forbids exactly that:
never spend a cognition token on state, recurrence, routing or validation that a
predicate can express.

This is the predicate. One query, no model, no doctrine read.

CONTRACT
  exit 0  PROCEED — no successful run recorded this calendar month, do the work
  exit 1  STOP    — this month is already done, end the session now
  exit 0  also on any error (see FAIL-OPEN)

FAIL-OPEN, deliberately. If the ledger cannot be reached we return PROCEED. The
alternative fails closed and silently skips a month, which is the failure this
whole windowed design exists to prevent. A routine that then cannot reach the
store hits the same outage itself and reports it properly, which is a better
outcome than a gate swallowing the month in silence.

MONTH BOUNDARY is America/Chicago, matching the STEP 0 wording in each routine's
own SOP, not UTC — a 9am CT firing on the 1st must not read as the previous month.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))

PROCEED, STOP = 0, 1

# THE RUN KEY MATTERS MORE THAN THE STATE, and getting this wrong is the whole
# trap. hooks/scheduled-run-record.py already writes one row per FIRING under
# run_key 'scheduled-session' with state 'succeeded' — that means "the session
# reached Stop without a deny", NOT "the routine did its monthly work". Six such
# rows existed for these routines in August, one per firing, including pure gate
# exits that did nothing. A predicate keying on state alone would have read the
# first no-op of a window as the month being done and suppressed the real run for
# the rest of it. So the work path writes its OWN row under this key, and only
# this key answers the question.
COMPLETION_KEY = "monthly.completed"

QUERY = """
select r.ended_at
  from ops.run r
  join ops.service s on s.id = r.service_id
 where s.key = %s
   and r.run_key = %s
   and r.state = 'succeeded'
   and date_trunc('month', r.ended_at at time zone 'America/Chicago')
     = date_trunc('month', now()      at time zone 'America/Chicago')
 order by r.ended_at desc
 limit 1
"""


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Has this monthly routine already run this calendar month?"
    )
    ap.add_argument("service", help="registered service key, e.g. playbook-review-monthly")
    ap.add_argument(
        "--run-key", default=COMPLETION_KEY,
        help=f"ledger run_key the work path stamps on completion (default {COMPLETION_KEY})",
    )
    ap.add_argument(
        "--quiet", action="store_true",
        help="print nothing; communicate through the exit code only",
    )
    args = ap.parse_args()

    def say(msg: str) -> None:
        if not args.quiet:
            print(msg)

    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "ops_record",
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools", "ops-record.py"),
        )
        ops_record = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ops_record)
        with ops_record.connect("read") as conn, conn.cursor() as cur:
            cur.execute(QUERY, (args.service, args.run_key))
            row = cur.fetchone()
    except SystemExit as exc:
        say(f"PROCEED — ledger unreachable ({exc}); failing open rather than skipping the month")
        return PROCEED
    except Exception as exc:  # noqa: BLE001 - fail-open is the whole point
        say(f"PROCEED — ledger unreachable ({type(exc).__name__}: {exc}); failing open")
        return PROCEED

    if row:
        say(f"STOP — {args.service} completed its monthly work on {row[0]:%Y-%m-%d %H:%M %Z}")
        return STOP

    say(f"PROCEED — no completed {args.service} work recorded this calendar month")
    return PROCEED


if __name__ == "__main__":
    raise SystemExit(main())
