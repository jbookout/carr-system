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

def main()->int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-empty-store",action="store_true")
    args=parser.parse_args()
    dsn=os.environ.get("DATABASE_URL")
    if not dsn: print("rule-admission-audit: DATABASE_URL required",file=sys.stderr);return 2
    with psycopg.connect(dsn) as conn,conn.cursor() as cur:
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
    print(f"rule-admission-audit: total={total} admitted={admitted} needs_revision={needs_revision} missing={missing} incomplete={incomplete}")
    return 1 if ((total==0 and not args.allow_empty_store) or needs_revision or missing
                 or incomplete or admitted!=total) else 0

if __name__=="__main__": raise SystemExit(main())
