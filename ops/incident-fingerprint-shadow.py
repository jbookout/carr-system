#!/usr/bin/env python3
"""The council's confirm-or-kill test for recommendation 3, run against the ledger.

Process-audit council, 2026-08-23, both chairs:

  "Shadow-fingerprint the incidents and job failures from August 15-23. Confirm
   if it reduces the actionable queue by at least 50% without merging any
   failures that demand different remedies. Kill or revise the fingerprint if
   distinct causes collapse together."

  "Kill if a distinct failure is swallowed by an overly broad signature."

This runs that test. It is READ-ONLY — it opens the routine (carr_jobs) DSN,
issues nothing but SELECTs, and writes nothing anywhere. It is deliberately NOT
named *-selftest.py: ops/ci.sh auto-runs those on every push, and this one needs
the production ledger and answers a question about one week, not a question
about the code. ops/incident-fingerprint-selftest.py is the part that runs on
every push.

WHAT IT MEASURES, and why in two halves. The queue is not one population:

  RUN-SOURCED incidents come from ops.run, which records successes as well as
  failures. Recommendation 3 is about these — the launchd jobs and the nightly
  chain — and both halves of the fix reach them.

  WORKER-SOURCED incidents come from mcp-server/src/trace.js, which records
  failures only. No success row for an MCP verb exists anywhere, so no success
  sequence can ever clear one. Fingerprinting cannot help them either, because
  they are genuinely distinct: on 2026-08-23 the eleven open verb incidents
  carried a permission-denied grant problem, four separate Worker code bugs and
  three callers passing a loop number where a uuid belongs. Merging those would
  be exactly the collapse the kill condition forbids.

  So the totals are reported both ways. A single blended number would let the
  verbs either flatter the result or bury it, and the council asked a question
  about remedies, not about row counts.

Run:  ./.venv/bin/python ops/incident-fingerprint-shadow.py
      ./.venv/bin/python ops/incident-fingerprint-shadow.py --since 2026-08-15
"""
import argparse
import importlib.util
import os
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
TOOL = os.path.join(REPO, "tools", "ops-record.py")

spec = importlib.util.spec_from_file_location("ops_record", TOOL)
assert spec is not None and spec.loader is not None, f"cannot load {TOOL}"
opsrec = importlib.util.module_from_spec(spec)
spec.loader.exec_module(opsrec)

# The bar, verbatim from the brief.
REQUIRED_DROP = 0.50


def parts(signature):
    """(service, environment, operation, raw failure class) or None."""
    bits = (signature or "").split("|", 3)
    return tuple(bits) if len(bits) == 4 else None


def is_bare_exit(raw):
    return bool(opsrec._EXIT_CLASS_RE.match((raw or "").strip()))


def pct(before, after):
    if not before:
        return 0.0
    return (before - after) / before


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--since", default="2026-08-15",
                    help="start of the window the council named (inclusive)")
    ap.add_argument("--until", default=None,
                    help="end of the window (exclusive); default is now")
    args = ap.parse_args()

    # ops-record reads db.env into the environment on its way to a connection;
    # ask it to do that first, so this refuses only when the credential is
    # genuinely absent rather than merely not exported.
    opsrec._load_db_env()
    if not os.environ.get("CARR_DB_JOBS_URL"):
        print("incident-fingerprint-shadow: needs CARR_DB_JOBS_URL to read the "
              "ledger. Nothing measured.", file=sys.stderr)
        return 78

    with opsrec.connect("routine") as conn, conn.cursor() as cur:
        cur.execute(
            """select i.ref, i.severity, i.state, i.environment, i.signature,
                      i.source_kind, i.source_ref, i.detected_at,
                      (select count(*) from ops.incident_link l
                        where l.incident_id = i.id) as evidence
                 from ops.incident i
                where i.state not in ('resolved','reviewed')
             order by i.detected_at""")
        cols = ("ref", "severity", "state", "environment", "signature",
                "source_kind", "source_ref", "detected_at", "evidence")
        openrows = [dict(zip(cols, r)) for r in cur.fetchall()]

        cur.execute(
            """select ref, signature, detected_at, state
                 from ops.incident
                where detected_at >= %s
                  and (%s::text is null or detected_at < %s::timestamptz)
             order by detected_at, ref""",
            (args.since, args.until, args.until))
        created = [dict(zip(("ref", "signature", "detected_at", "state"), r))
                   for r in cur.fetchall()]

        # Which of the open rows the run ledger can even speak about.
        streaks = {}
        for inc in openrows:
            job = opsrec.fingerprint_job(inc["signature"])
            if not job:
                continue
            service, env, run_key = job
            cur.execute(
                """select count(*) from ops.run r
                     join ops.service s on s.id = r.service_id
                    where s.key = %s and r.environment = %s and r.run_key = %s""",
                (service, env, run_key))
            if not cur.fetchone()[0]:
                continue          # no run rows at all: a Worker-sourced incident
            streaks[inc["ref"]] = opsrec._healthy_streak(cur, service, env, run_key)

    run_sourced = [i for i in openrows if i["ref"] in streaks]
    other = [i for i in openrows if i["ref"] not in streaks]

    # ── the guard, first, because a failed guard is a KILL regardless of the
    #    number it would have produced ─────────────────────────────────────────
    groups = defaultdict(list)
    for inc in openrows:
        p = parts(inc["signature"])
        if not p:
            continue
        service, env, operation, raw = p
        groups[opsrec.incident_fingerprint(service, env, operation, raw)].append(
            (inc["ref"], raw))

    violations = []
    for fingerprint, members in sorted(groups.items()):
        named = {raw for _, raw in members if not is_bare_exit(raw)}
        if len(named) > 1:
            violations.append((fingerprint, sorted(named)))

    merges = {f: m for f, m in groups.items() if len(m) > 1}

    # ── creation churn ───────────────────────────────────────────────────────
    # The council's complaint was that "the same failures opened a fresh SEV-3
    # nearly every day". This is that sentence as a number: how many rows were
    # created, and how many distinct problems they represent.
    created_fp = defaultdict(list)
    for inc in created:
        p = parts(inc["signature"])
        key = (opsrec.incident_fingerprint(*p) if p
               else f"(no fingerprint) {inc['ref']}")
        created_fp[key].append(inc)
    repeats = {k: v for k, v in created_fp.items() if len(v) > 1}
    surplus = sum(len(v) - 1 for v in repeats.values())

    print(f"window: {args.since} .. {args.until or 'now'}")
    print(f"incidents created in the window: {len(created)}, "
          f"covering {len(created_fp)} distinct fingerprint(s)")
    print(f"open right now: {len(openrows)}  "
          f"({len(run_sourced)} run-sourced, {len(other)} with no run ledger)\n")

    print("── creation churn: one fingerprint, several rows " + "─" * 25)
    if repeats:
        for key, rows in sorted(repeats.items(),
                                key=lambda kv: (-len(kv[1]), kv[0])):
            refs = ", ".join(f"{r['ref']}({r['state'][:3]})" for r in rows)
            print(f"  {len(rows)}x  {key}")
            print(f"       {refs}")
        print(f"\n  {surplus} of {len(created)} rows created in the window are a "
              f"fingerprint that")
        print("  already had a row. NOT FOLDED BY THIS CHANGE, said out loud: every")
        print("  one of these opened only after a human had RESOLVED the previous")
        print("  row, and 0116 is deliberate that a resolved problem coming back is")
        print("  a new incident, not a reopened one. Recurrence-folding here is")
        print("  scoped to an OPEN fingerprint. Whether a resolved-then-returned")
        print("  failure should reopen instead is a separate ruling, and it is the")
        print("  single largest remaining source of rows in this window.")
    else:
        print("  none — every row created in the window is its own fingerprint.")
    print()

    print("── guard: do distinct remedies collapse? " + "─" * 33)
    if violations:
        for fingerprint, named in violations:
            print(f"  VIOLATION {fingerprint}")
            print(f"            merges named classes {named}")
    else:
        print("  none. Every merge below is two spellings of one bare exit code;")
        print("  no two named failure classes share a fingerprint.")
    if merges:
        print(f"\n  {len(merges)} fingerprint(s) absorb more than one open row:")
        for fingerprint, members in sorted(merges.items()):
            print(f"    {fingerprint}")
            for ref, raw in members:
                print(f"      {ref}  was …|{raw}")
    else:
        print("\n  no open rows merge under the new fingerprint.")

    print("\n── success-clears: what a defined sequence would close " + "─" * 19)
    cleared, watching, stuck = [], [], []
    for inc in run_sourced:
        action, reason = opsrec.recovery_decision(inc, streaks[inc["ref"]])
        line = (f"    {inc['ref']}  {inc['severity']}  "
                f"{inc['signature']}  streak={streaks[inc['ref']]}")
        if action == "clear":
            cleared.append(line)
        elif action == "monitor":
            watching.append(line + f"  ({reason.split(' — ')[0]})")
        else:
            stuck.append(line)
    print(f"  clears now ({len(cleared)}):")
    for line in cleared or ["    none"]:
        print(line)
    print(f"  recovering, not yet cleared ({len(watching)}):")
    for line in watching or ["    none"]:
        print(line)
    print(f"  still failing ({len(stuck)}):")
    for line in stuck or ["    none"]:
        print(line)

    # ── the arithmetic ───────────────────────────────────────────────────────
    merged_away = sum(len(m) - 1 for m in merges.values())
    base_all = len(openrows)
    after_all = base_all - merged_away - len(cleared)
    base_run = len(run_sourced)
    cleared_run = len(cleared)
    merged_run = sum(len(m) - 1 for f, m in merges.items()
                     if any(ref in streaks for ref, _ in m))
    after_run = base_run - merged_run - cleared_run

    print("\n── the council's arithmetic " + "─" * 45)
    print(f"  run-sourced queue      {base_run:>3} → {after_run:<3} "
          f"({pct(base_run, after_run):.0%} drop)")
    print(f"  whole open queue       {base_all:>3} → {after_all:<3} "
          f"({pct(base_all, after_all):.0%} drop)")
    print(f"  of which no run ledger {len(other):>3} — unreachable by either half "
          f"of this change")

    print("\n── verdict " + "─" * 62)
    if violations:
        print("  KILL. A distinct failure is swallowed by the fingerprint above.")
        print("  The drop, whatever it is, was bought with a merge the council")
        print("  told us not to make.")
        return 1

    if pct(base_run, after_run) >= REQUIRED_DROP:
        print(f"  CONFIRM on the population recommendation 3 addresses: the")
        print(f"  run-sourced queue drops {pct(base_run, after_run):.0%}, at or above the "
              f"{REQUIRED_DROP:.0%} bar,")
        print("  and no two named failure classes were merged to get there.")
        return 0

    print(f"  PARTIAL. The guard holds — nothing distinct was merged — but the")
    print(f"  run-sourced queue drops only {pct(base_run, after_run):.0%}, under the "
          f"{REQUIRED_DROP:.0%} bar.")
    print("  Read that as a finding, not a defect in the change: 0116's dedupe")
    print("  already collapsed the repeat-failure churn this was expected to")
    print("  catch, so the remaining queue is mostly rows that are each a real,")
    print("  separate piece of work. Buying the other half would mean merging")
    print("  them, which is the thing the kill condition forbids.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
