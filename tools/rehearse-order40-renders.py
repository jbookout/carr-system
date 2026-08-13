#!/usr/bin/env python3
"""Rehearse ORDER 40's three renders on a Neon branch. NEVER touches production.

Run through db-tap so the DSN is never a shell substitution:

  .venv/bin/python tools/db-tap.py --branch rehearse-0031-order40 \
      run tools/rehearse-order40-renders.py

WHAT IT DOES, in order:
  1. GUARD. Refuses to run against any database that does not already carry
     ORDER 40's import. Production carries none of it, so production is
     structurally unreachable from this script — the check is the safety, not a
     flag someone can forget to pass.
  2. Activates the imported ai-operating-notes rules — ON THE BRANCH ONLY.
     Activation in production is Joe's explicit yes, one rule at a time, via
     activate-rule. It happens here because v_compiled_rules is active-only
     (0015), so a compiled preview built from proposed rules would render empty
     and prove nothing. The branch is disposable; this is what it is for.
  3. Runs the three renders into out/exports/ (draft). CARR_EXPORT_LIVE is
     never set, so the vault is untouchable from here.

The exporter reads CARR_DB_EXPORTER_URL; db-tap supplies DATABASE_URL. This
script bridges the two rather than making db-tap know about exporters.
"""

import os
import sys
from pathlib import Path

import psycopg

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

MARKERS = (
    ("decision-history", "select count(*) from record_source "
                         "where source_system='decision-history'"),
    ("ai-operating-notes", "select count(*) from rule "
                           "where scope->>'source' = 'ai-operating-notes.md'"),
    ("idea-bank", "select count(*) from loop_item where kind='idea'"),
)


def guard(url):
    with psycopg.connect(url) as c, c.cursor() as k:
        counts = {}
        for name, q in MARKERS:
            k.execute(q)
            counts[name] = k.fetchone()[0]
    missing = [n for n, v in counts.items() if v == 0]
    if missing:
        sys.exit(
            "REFUSING: this database does not carry ORDER 40's import "
            f"({', '.join(missing)} = 0 rows). This script is for the rehearsal "
            "branch only; production must never be rendered from here.")
    print(f"guard OK — branch carries the import: {counts}")
    return counts


def activate_branch_only(url):
    """Branch-only. Production activation is Joe's, one yes at a time."""
    with psycopg.connect(url) as c, c.cursor() as k:
        k.execute("select id from actor where slug='joe'")
        joe = k.fetchone()[0]
        k.execute(
            "update rule set status='active', activated_by=%s, activated_at=now() "
            "where status='proposed' and scope->>'source' = 'ai-operating-notes.md' "
            "returning id", (joe,))
        n = len(k.fetchall())
        c.commit()
    print(f"activated {n} imported rule(s) — BRANCH ONLY, production untouched")
    return n


def main():
    url = os.environ.get("DATABASE_URL") or os.environ.get("CARR_IMPORT_DB_URL")
    if not url:
        sys.exit("no DATABASE_URL — run this through tools/db-tap.py")
    if os.environ.get("CARR_EXPORT_LIVE") == "1":
        sys.exit("REFUSING: CARR_EXPORT_LIVE=1. This rehearsal never writes the vault.")

    guard(url)
    activate_branch_only(url)

    # The exporter reads this name; point it at the branch.
    os.environ["CARR_DB_EXPORTER_URL"] = url
    os.environ.pop("CARR_EXPORT_LIVE", None)

    from exporters.run_exports import select
    from exporters.common import run_export

    ok = True
    for key in ("decision-history.md", "loop-idea-bank.md",
                "compiled-rules-joe", "compiled-rules-shared"):
        targets = select(key)
        for k, (rel, fn) in targets.items():
            # bootstrap=True on purpose. The façade check compares row counts
            # against the last OK run, whose history the branch inherited from
            # production — so it correctly flags 9 rules -> 53 as drift. On a
            # rehearsal branch that jump IS the thing being rehearsed. In
            # production the same guard fires again and Joe accepts it once,
            # deliberately, which is exactly the protection it is there to give.
            ok = run_export(k, rel, fn, bootstrap=True) and ok
    print("renders written to out/exports/ (draft)")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
