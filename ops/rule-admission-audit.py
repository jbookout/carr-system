#!/usr/bin/env python3
"""Exit audit: every active rule has a complete, honest admission contract."""
from __future__ import annotations
import argparse,os,sys
from typing import Any
import psycopg


def fetchone_required(row: tuple[Any, ...] | None, context: str) -> tuple[Any, ...]:
    if row is None:
        raise RuntimeError(f"admission audit expected one row for {context}")
    return row

def counts(cur: Any) -> dict[str, int]:
    """Read the admission contract's four numbers, read-only.

    Split out of main() on 2026-08-23 so the nightly watch and this audit count
    the same way rather than two ways that agree until one is edited (rule
    a8c55a47). Both statements are SELECTs over `rule` (id and status only) and
    ops.rule_admission, which is exactly what migration 0285 lets the routine
    jobs role read, so a caller may hand in a cursor inside a read-only
    transaction under that role instead of the owner credential main() uses.
    """
    cur.execute("""select count(*) filter(where a.state='admitted'),
                          count(*) filter(where a.state='needs_revision'),
                          count(*) filter(where a.rule_id is null),count(*)
                   from rule r left join ops.rule_admission a on a.rule_id=r.id
                   where r.status='active'""")
    admitted,needs_revision,missing,total=fetchone_required(cur.fetchone(), "active rule admission counts")
    cur.execute("""select count(*) from ops.rule_admission a join rule r on r.id=a.rule_id
                    where r.status='active' and a.state='admitted'
                      and (jsonb_typeof(a.applicability)<>'object'
                        or jsonb_typeof(a.projection)<>'object'
                        or jsonb_typeof(a.reachability)<>'object')""")
    incomplete=fetchone_required(cur.fetchone(), "active rule contract completeness")[0]
    return {"total":total,"admitted":admitted,"needs_revision":needs_revision,
            "missing":missing,"incomplete":incomplete}


def failing(c: dict[str,int], *, allow_empty_store: bool=False) -> bool:
    """The one place that decides whether these numbers are a failure."""
    return bool((c["total"]==0 and not allow_empty_store) or c["needs_revision"]
                or c["missing"] or c["incomplete"] or c["admitted"]!=c["total"])


def render(c: dict[str,int]) -> str:
    return (f"total={c['total']} admitted={c['admitted']} "
            f"needs_revision={c['needs_revision']} missing={c['missing']} "
            f"incomplete={c['incomplete']}")


def main()->int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-empty-store",action="store_true")
    args=parser.parse_args()
    dsn=os.environ.get("DATABASE_URL")
    if not dsn: print("rule-admission-audit: DATABASE_URL required",file=sys.stderr);return 2
    with psycopg.connect(dsn) as conn,conn.cursor() as cur:
        c=counts(cur)
    print("rule-admission-audit: "+render(c))
    return 1 if failing(c, allow_empty_store=args.allow_empty_store) else 0

if __name__=="__main__": raise SystemExit(main())
