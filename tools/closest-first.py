#!/usr/bin/env python3
"""closest-first — open loops ordered by how little is left, not by how urgent.

WHY THIS EXISTS. Every other ordering in the system answers "should this be done
soon": the bell cap, dated rows, decision markers, the Monday brief. None answers
"how much is left". So a loop one sentence from Joe would close reads exactly like
one needing a migration and a deploy, the cheap closes never float, and the
backlog only grows. Loop #297, from the loop-vs-graph study 2026-08-09
(capture d79ff9ae), where the author names the same gap in his own pipeline.

BOUND ACTION (rule 590b11e1 — no metric without one): when a partner has a spare
sitting, take from the top. Those are the rows a single act finishes.

HONESTY RAIL. Coverage prints FIRST and unscored rows are never mixed into the
ranking. Most of the backlog predates the blocker gate and carries no class; a
list that quietly ranked those would look authoritative and be noise, which is
the specific failure loop #297 warned about. Reads v_loop_proximity and
v_loop_proximity_coverage (migration 0084). Read-only.

Run it as:  ./run.sh next  [--all] [--domain system]
"""
from __future__ import annotations

import argparse
import os
import sys

import psycopg


def main() -> int:
    ap = argparse.ArgumentParser(description="open loops ordered by effort-to-close")
    ap.add_argument("--all", action="store_true",
                    help="also list the unscored rows (those predating the blocker gate)")
    ap.add_argument("--domain", help="filter to one domain: deals, prospecting, "
                                     "networking, marketing, business, system")
    args = ap.parse_args()

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("ERROR: DATABASE_URL is not set. Run this through run.sh next, "
              "which derives it via tools/db-tap.py.", file=sys.stderr)
        return 2

    where = "where not unscored"
    params: list[str] = []
    if args.domain:
        where += " and domain = %s"
        params.append(args.domain)

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("select open_loops, scored, unscored, scored_pct "
                    "from v_loop_proximity_coverage")
        total, scored, unscored, pct = cur.fetchone()

        # Coverage first, always. The ranking below is a view of `scored` rows
        # only, and printing that share up front is what stops the head of the
        # list being read as the whole backlog.
        print(f"\nCOVERAGE  {scored} of {total} open loops carry a blocker class "
              f"({pct}%). {unscored} predate the gate and are NOT ranked below.")
        if pct is not None and pct < 50:
            print("          Coverage is low by expectation, not by fault: the gate is new "
                  "and\n          only rises as old loops are touched. Do not read the head "
                  "of this list\n          as the cheapest work in the whole backlog — only "
                  "as the cheapest\n          among rows anyone has classified.")

        cur.execute(
            f"select number, domain, owner, proximity_rank, proximity_label, "
            f"days_open, marker, drift_critical, blocker_detail, gist "
            f"from v_loop_proximity {where} order by proximity_rank, days_open desc",
            params)
        rows = cur.fetchall()

        if not rows:
            print("\nNothing classified yet" +
                  (f" in domain '{args.domain}'." if args.domain else ".") +
                  " Nothing to rank.\n")
        else:
            print(f"\nCLOSEST FIRST{f'  ·  domain: {args.domain}' if args.domain else ''}")
            last = None
            for (num, dom, owner, rank, label, age, marker, drift,
                 detail, gist) in rows:
                if rank != last:
                    print(f"\n  {rank}. {label.upper()}")
                    last = rank
                flags = []
                if drift:
                    flags.append("DRIFT")
                if marker and marker != "none":
                    flags.append(marker)
                flag = f"  [{' '.join(flags)}]" if flags else ""
                print(f"     #{num:<5} {dom:<12} {age:>3}d  {gist.strip()[:70]}{flag}")
                if detail:
                    print(f"            waiting on: {detail.strip()[:150]}")

        if args.all and unscored:
            cur.execute(
                "select number, domain, days_open, gist from v_loop_proximity "
                "where unscored" +
                (" and domain = %s" if args.domain else "") +
                " order by days_open desc",
                params)
            print(f"\nUNSCORED — {unscored} row(s) predating the blocker gate. "
                  f"Not ranked, not deferred:\n  each is a candidate to DO or to CLOSE, "
                  f"because nobody ever established it needed waiting on anything.")
            for num, dom, age, gist in cur.fetchall():
                print(f"     #{num:<5} {dom:<12} {age:>3}d  {gist.strip()[:70]}")
        elif unscored:
            print(f"\n  ({unscored} unscored rows hidden. --all to see them.)")

        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
