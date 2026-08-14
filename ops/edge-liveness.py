#!/usr/bin/env python3
"""edge-liveness.py — read the job ledger from somewhere that is not Joe's Mac,
and say so out loud when the Mac has gone quiet.

PROGRAM 4'S GATE HAS TWO VERBS: "loss of Joe's Mac does not DESTROY or SILENTLY
STOP critical operations." Destroy is covered twice over by 2026-08-14 — the
local nightly dump archives to R2, and .github/workflows/backup-nightly.yml
takes an independent encrypted dump on GitHub's infrastructure, sharing no
failure point with the Mac. Silently stop was NOT covered, and the shape of the
gap was specific.

WHAT WAS ALREADY THERE. Four Healthchecks dead-man checks (exports, backup, MCP,
whole chain). Those genuinely detect a dead Mac: if it stops, the pings stop and
Healthchecks alarms on its own. They are the reason this file is a completion
rather than a first line of defence.

WHAT WAS MISSING. Those four checks watch the NIGHTLY CHAIN. As of 2026-08-14
this system registers 35 service/environment rows, 17 of them launchd jobs that
now write durable run results — and NOTHING OFF THE MAC READS ANY OF IT. The
whole observability layer Program 3 built terminates in a table that only a
human running `ops-record health` on the failed machine can see. If capture-poll
stops, or rules-refresh stops, or the Mac is up but a job is wedged, every
dead-man check keeps pinging green because the chain is fine, and the ledger
that knows better is unread.

So this runs on GitHub's schedule, connects with the SAME read-only credential
the cloud backup uses (carr_backup: SELECT on the CARR schemas and nothing
else — migrations/0119), reads ops.v_service_environment_health, and fails the
workflow when something has gone quiet. A failed workflow emails Joe through
GitHub, which is an alert path with no dependency on the machine being reported
on.

── THREE RULES ABOUT WHAT IT WILL NOT SAY ──────────────────────────────────────

NEVER-OBSERVED IS NOT GONE QUIET, and this is the one that would have wrecked it.
A service that has never reported cannot have stopped reporting. On the day this
was written, 8 services were newly registered and 22 of 35 rows read `missing` —
alerting on those would have fired 22 alarms about things that were working, on
day one, which is precisely how both partners learn to stop reading alarms. Only
a service that HAS been observed and has since gone past its cadence counts.

NO CADENCE MEANS NO OPINION. The KeepAlive servers and the WatchPaths jobs are
registered deliberately without a cadence: silence is their normal state. A
staleness check has nothing to say about them and says nothing.

ONE LINE FOR A DEAD MAC, NOT SEVENTEEN. If every launchd service has gone quiet
together, that is one fact — the machine is down — and reporting it as seventeen
stale services buries it. The single loud line is the useful output; the list
is the evidence under it.

Exit codes: 0 everything that should be reporting is reporting · 1 something has
gone quiet · 78 no credential in this environment (EX_CONFIG, the convention
bin/nightly.sh and bin/run-scheduled.sh already use for "not configured here").
"""
import os
import sys
from typing import Any

# Only services at or above this criticality can raise an alarm. A low-criticality
# cosmetic job going quiet is a thing to notice on the health screen, never a thing
# to wake somebody for — and an alarm that fires for a keyboard remapper is an
# alarm nobody reads when the record layer stops.
ALARM_CRITICALITY = {"critical", "high"}


def main() -> int:
    dsn = (os.environ.get("LIVENESS_DATABASE_URL")
           or os.environ.get("BACKUP_DATABASE_URL")
           or os.environ.get("DATABASE_URL", ""))
    if not dsn:
        print("edge-liveness: no database credential in this environment — "
              "nothing read, nothing claimed (EX_CONFIG).")
        return 78

    try:
        import psycopg
    except ImportError:
        print("edge-liveness: psycopg unavailable — nothing read, nothing claimed.")
        return 78

    # A DSN THAT IS PRESENT AND UNREACHABLE IS NOT THE SAME AS A SERVICE GOING
    # QUIET, and until ops/degraded-mode-exercise.py cut the database off, this
    # file could not tell them apart: the connection error propagated, the
    # process exited 1, and the workflow's own error line announced "a
    # registered service has gone quiet" — about a network blip, to Joe's inbox.
    # A watchdog whose alarm text can be false is worse than one that stays
    # quiet, because the next real alarm is read with the last false one in mind.
    #
    # EX_CONFIG here, and the workflow turns 78 into a visible warning rather
    # than a passing silence, so an unreachable ledger is still SEEN without
    # being reported as something it is not.
    try:
        conn_ctx = psycopg.connect(dsn, connect_timeout=20)
    except Exception as exc:  # noqa: BLE001 — every connect failure reads the same
        print(f"edge-liveness: the ledger is unreachable ({type(exc).__name__}) "
              f"— nothing read, nothing claimed. This is NOT a report that a "
              f"service has gone quiet, and must never be shown as one.")
        return 78

    with conn_ctx as conn, conn.cursor() as cur:
        # observed_at is the last time ANYTHING was heard from this service.
        # freshness_state is the view's own verdict against the registered
        # cadence, so the staleness rule lives in the database next to the
        # cadence it uses rather than being re-derived here and drifting.
        # runtime lives on ops.service rather than on the view, and it is what
        # separates "one machine went dark" from "one job stopped". Joined
        # rather than added to the view: the view is Program 3's contract and
        # this is a reader, so a reader's need does not get to reshape it.
        cur.execute(
            """select v.service_key, v.environment, v.criticality, s.runtime,
                      v.freshness_state, v.health, v.observed_at,
                      extract(epoch from (now() - v.observed_at)) / 3600.0 as hours
                 from ops.v_service_environment_health v
                 join ops.service s on s.id = v.service_id
                where v.environment = 'production'
                  and s.retired_at is null
             order by v.service_key""")
        rows: list[Any] = cur.fetchall()

    return assess(rows)


def assess(rows: list[Any]) -> int:
    """The whole verdict, as a pure function of the rows.

    Split out so the alarm paths can be proven against fabricated rows in
    ops/edge-liveness-selftest.py. Everything above this line is I/O; every
    decision this file makes is below it.
    """
    if not rows:
        print("edge-liveness: the health view returned no rows at all. That is "
              "not silence from one job, it is the catalog being empty or "
              "unreadable, and it is a failure.")
        return 1

    observed = [r for r in rows if r[6] is not None]
    never = [r for r in rows if r[6] is None]

    # GONE QUIET = has been heard from, and the view says that observation is no
    # longer believable. `missing` is the never-observed state and is excluded by
    # construction above.
    quiet = [r for r in observed if r[4] == "stale"]
    alarming = [r for r in quiet if (r[2] or "medium") in ALARM_CRITICALITY]

    launchd_rows = [r for r in observed if r[3] == "launchd"]
    launchd_quiet = [r for r in launchd_rows if r[4] == "stale"]

    print(f"edge-liveness — read from GitHub, not from the machine it reports on")
    print(f"  {len(rows)} production row(s): {len(observed)} observed, "
          f"{len(never)} never observed (excluded: a service that has never "
          f"reported cannot have stopped)")

    # ── the dead-Mac line ────────────────────────────────────────────────────
    if launchd_rows and len(launchd_quiet) == len(launchd_rows):
        oldest = max(r[7] for r in launchd_quiet)
        print(f"\n  THE MAC HAS GONE DARK. All {len(launchd_rows)} observed "
              f"launchd service(s) are stale together; the most recent word from "
              f"any of them was {oldest:.1f}h ago. One machine hosts all of them, "
              f"so this is one fact rather than {len(launchd_quiet)}.")
        for r in sorted(launchd_quiet, key=lambda x: -x[7]):
            print(f"      {r[0]:<28} last heard {r[7]:.1f}h ago")
        return 1

    if not alarming:
        print(f"\n  Everything that should be reporting is reporting.")
        if quiet:
            print(f"  ({len(quiet)} low/medium service(s) quiet — noted, not "
                  f"alarmed: " + ", ".join(r[0] for r in quiet) + ")")
        return 0

    print(f"\n  {len(alarming)} service(s) at criticality "
          f"{'/'.join(sorted(ALARM_CRITICALITY))} have gone quiet:")
    for r in sorted(alarming, key=lambda x: -x[7]):
        print(f"      {r[0]:<28} {r[2]:<8} last heard {r[7]:.1f}h ago "
              f"(health={r[5]})")
    print("\n  Each of these HAS reported before and has now missed its "
          "registered cadence. Read the trace on the Mac if it is up: "
          "tools/ops-record.py health")
    return 1


if __name__ == "__main__":
    sys.exit(main())
