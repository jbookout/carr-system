#!/usr/bin/env python3
"""phase4-qualification.py — is the session substrate deployed, and does anything
actually qualify?

WHY THIS EXISTS. Migration 0257 gives the database an authenticated session
identity and the doors mint one. Both halves can be present and correct while
the system produces NO qualified evidence at all, and that state is invisible:
a row with a null session is the ordinary legacy path, so a fleet that has
silently stopped qualifying looks exactly like a fleet nobody used.

That is not hypothetical. The first attempt to wire the OAuth door minted
nothing on every request, for three independent reasons, and every test in the
repo passed — because the tests supplied an actor shape no door can produce.
Nothing anywhere asserted that qualifying rows were non-zero. THIS is that
assertion, and it is the only one that can only be made against a real database.

READ ONLY. It opens its connection with default_transaction_read_only=on and
runs SELECTs over catalogs and counts. It cannot write, and it never prints the
connection string.

Exit codes are the point, so this can gate a deploy:

  0  the substrate is deployed AND evidence is qualifying
  0  the substrate is absent — reported as NOT DEPLOYED, which is a legitimate
     pre-deploy state rather than a failure
  1  the substrate IS deployed and NOTHING qualifies, or a runtime role can
     reach the mint. Both mean the guarantees are not load-bearing.

Usage:
  .venv/bin/python tools/phase4-qualification.py
  .venv/bin/python tools/phase4-qualification.py --project staging
  .venv/bin/python tools/phase4-qualification.py --since-hours 24
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import psycopg
except ImportError:
    sys.exit("psycopg is not importable; run this with the repo's .venv interpreter")

EVIDENCE_TABLES = ("tool_call", "event", "tool_read_call")
RUNTIME_ROLES = ("carr_writer", "carr_reader", "carr_jobs", "carr_authority",
                 "carr_exporter", "carr_device_evidence")


def substrate_state(cur):
    cur.execute("""
        select
          (select count(*) from pg_class c join pg_namespace n on n.oid=c.relnamespace
            where n.nspname='ops' and c.relname='application_session'),
          (select count(*) from pg_proc p join pg_namespace n on n.oid=p.pronamespace
            where n.nspname='ops' and p.proname='mint_application_session'),
          (select count(*) from pg_proc p join pg_namespace n on n.oid=p.pronamespace
            where n.nspname='ops' and p.proname='mint_application_session_for_slug'),
          (select count(*) from pg_roles where rolname='carr_session_minter'),
          (select count(*) from pg_roles where rolname='carr_session_issuer')
    """)
    table, mint, mint_slug, minter, issuer = cur.fetchone()
    return {"table": table, "mint": mint, "mint_by_slug": mint_slug,
            "minter_role": minter, "issuer_role": issuer}


def columns_present(cur):
    cur.execute("""select table_name from information_schema.columns
                    where table_schema='public' and column_name='application_session_id'""")
    return {r[0] for r in cur.fetchall()}


def reachers(cur):
    """Any runtime role that can reach the mint, directly or by inheritance."""
    cur.execute("""
        select r,
               pg_has_role(r,'carr_session_minter','MEMBER'),
               pg_has_role(r,'carr_session_minter','USAGE')
          from unnest(%s::text[]) r
         where exists (select 1 from pg_roles where rolname = r)
    """, (list(RUNTIME_ROLES),))
    return [(r, m, u) for r, m, u in cur.fetchall() if m or u]


def counts(cur, table, since_hours):
    """Counts scoped to a recent window as well as overall.

    The window is what makes this a live gate rather than a historical one: a
    system that qualified last month and stopped yesterday still has a non-zero
    lifetime count, and a lifetime count would call that healthy.
    """
    time_col = "occurred_at" if table == "event" else "created_at"
    cur.execute(f"""
        select count(*) filter (where application_session_id is not null),
               count(*) filter (where application_session_id is null),
               count(*) filter (where application_session_id is not null
                                  and {time_col} > now() - make_interval(hours => %s)),
               count(*) filter (where {time_col} > now() - make_interval(hours => %s))
          from {table}
    """, (since_hours, since_hours))
    return cur.fetchone()


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", default="production")
    ap.add_argument("--branch", default=None)
    ap.add_argument("--since-hours", type=int, default=24,
                    help="window for the live check (default 24)")
    args = ap.parse_args()

    # CARR_QUALIFICATION_DSN exists so this gate can be PROVEN against a
    # disposable local cluster, where the substrate can be deployed with nothing
    # qualifying — the exact state it is built to catch and the one state
    # production cannot be put into on purpose. Unset in every real run.
    url = os.environ.get("CARR_QUALIFICATION_DSN")
    target_label = args.project if not url else "LOCAL (CARR_QUALIFICATION_DSN)"
    if not url:
        # db-tap.py's filename is not a legal module name, so its DSN function is
        # loaded by path. Reused rather than reimplemented: it is the one place
        # that knows how to obtain a connection string without ever printing it.
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "dbtap", os.path.join(os.path.dirname(os.path.abspath(__file__)), "db-tap.py"))
        dbtap = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(dbtap)
        url = dbtap.dsn(branch=args.branch, project=args.project)

    failures = []
    with psycopg.connect(url, options="-c default_transaction_read_only=on") as conn:
        with conn.cursor() as cur:
            state = substrate_state(cur)
            cols = columns_present(cur)

            print(f"target project : {target_label}"
                  + (f" (branch {args.branch})" if args.branch else ""))
            print(f"window         : last {args.since_hours}h\n")
            print("substrate      :", end=" ")
            deployed = state["table"] and state["mint"] and state["minter_role"]
            if not deployed:
                print("NOT DEPLOYED")
                for k, v in state.items():
                    print(f"    {k:16} {'present' if v else 'ABSENT'}")
                print(f"    evidence tables carrying the session column: "
                      f"{sorted(cols) if cols else 'none'}")
                print("\nVERDICT: the session substrate is not deployed here. Nothing can "
                      "qualify\n         yet, and that is the expected pre-deploy state, "
                      "not a failure.")
                return 0

            print("deployed")
            for k, v in state.items():
                print(f"    {k:16} {'present' if v else 'ABSENT'}")
            missing_cols = set(EVIDENCE_TABLES) - cols
            if missing_cols:
                failures.append(f"evidence tables missing the session column: "
                                f"{sorted(missing_cols)}")

            bad = reachers(cur)
            if bad:
                failures.append(
                    "runtime roles can reach the mint: "
                    + ", ".join(f"{r} (SET ROLE={m}, inherits={u})" for r, m, u in bad))
            print(f"\n    roles able to reach the mint: "
                  f"{'NONE — correct' if not bad else [r for r, _, _ in bad]}")

            print(f"\n{'table':16} {'qualifying':>11} {'legacy':>10} "
                  f"{'qual/window':>12} {'all/window':>11}")
            live_qualifying = 0
            for t in EVIDENCE_TABLES:
                if t not in cols:
                    print(f"{t:16} {'(no column)':>11}")
                    continue
                q, l, qw, aw = counts(cur, t, args.since_hours)
                live_qualifying += qw
                print(f"{t:16} {q:>11} {l:>10} {qw:>12} {aw:>11}")
                if aw and not qw:
                    failures.append(
                        f"{t}: {aw} rows written in the last {args.since_hours}h and "
                        f"NOT ONE qualifies — the door is minting nothing")

            if not live_qualifying:
                failures.append(
                    f"no qualifying evidence anywhere in the last {args.since_hours}h; "
                    "the substrate is deployed and load-bearing on nothing")

    print()
    if failures:
        print("VERDICT: FAIL")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("VERDICT: PASS — the substrate is deployed and evidence is qualifying")
    return 0


if __name__ == "__main__":
    sys.exit(main())
