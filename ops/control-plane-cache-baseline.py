#!/usr/bin/env python3
"""Read-only Phase 5 cache baseline collector; emits no target or acceptance."""
from __future__ import annotations
import argparse, json, os, sys
from pathlib import Path
import psycopg
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
from lib.control_plane_phase5_baseline import BaselineRefusal, build_cache_baseline, resolve_cache_baseline_rows  # noqa: E402
def verify_reader_boundary(cur: object) -> None:
    cursor=cur
    cursor.execute("begin read only")  # type: ignore[attr-defined]
    cursor.execute("select session_user,current_user")  # type: ignore[attr-defined]
    if cursor.fetchone() != ("carr_reader","carr_reader"): raise BaselineRefusal("collector requires carr_reader session and effective identities")  # type: ignore[attr-defined]
    tables=("ops.job","ops.job_attempt","ops.job_definition","ops.cognition_job","ops.cognition_cache_observation")
    cursor.execute("select " + ",".join("has_table_privilege(current_user,%s,'insert,update,delete,truncate')" for _ in tables), tables)  # type: ignore[attr-defined]
    writes=cursor.fetchone()  # type: ignore[attr-defined]
    if not isinstance(writes, tuple) or len(writes)!=len(tables) or any(value is not False for value in writes): raise BaselineRefusal("collector identity has material write privilege on a baseline source table")
def main() -> int:
    p=argparse.ArgumentParser(); p.add_argument("--start",required=True); p.add_argument("--end",required=True); p.add_argument("--mode",choices=("shadow","canary","live","replay"),required=True); a=p.parse_args()
    dsn=os.environ.get("CARR_DB_READER_URL","")
    if not dsn: raise SystemExit("refused: CARR_DB_READER_URL is required")
    contract=json.loads((ROOT/"ops/config/control-plane-cache-baseline.v1.json").read_text())
    try:
      with psycopg.connect(dsn) as conn:
       with conn.cursor() as cur:
        verify_reader_boundary(cur)
        result=build_cache_baseline(contract,resolve_cache_baseline_rows(cur,start=a.start,end=a.end,mode=a.mode),start=a.start,end=a.end,mode=a.mode)
      print(json.dumps(result,sort_keys=True)); return 0
    except (BaselineRefusal, psycopg.Error) as exc: print(f"refused: {exc}",file=sys.stderr); return 78
if __name__=="__main__": raise SystemExit(main())
