#!/usr/bin/env python3
"""Write deterministic EventKit canary aggregates to the isolated receipt target."""
from __future__ import annotations
import argparse, hashlib, json, os, re, sys
from pathlib import Path
from urllib.parse import urlparse
def refuse(message: str) -> None: raise RuntimeError(f"calendar canary refused: {message}")
def digest(value: object) -> str: return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def main() -> int:
    parser=argparse.ArgumentParser(); parser.add_argument('--proposals',required=True); args=parser.parse_args()
    if os.environ.get('CARR_CONTROL_PLANE_MODE') != 'canary': refuse('only explicit control-plane canary mode may write this target')
    destination=os.environ.get('CARR_CALENDAR_CANARY_DESTINATION_ID',''); dsn=os.environ.get('CARR_CALENDAR_CANARY_DSN','')
    if not re.fullmatch(r'calendar-canary-[a-z0-9_-]+',destination): refuse('missing or invalid isolated destination identifier')
    parsed=urlparse(dsn)
    if parsed.scheme not in {'postgres','postgresql'} or not parsed.hostname or not parsed.path.strip('/'): refuse('missing explicit isolated PostgreSQL destination')
    if any(os.environ.get(k)==dsn for k in ('CARR_DB_JOBS_URL','DATABASE_URL','CARR_DB_WRITER_URL')): refuse('canary destination aliases a live database credential')
    path=Path(args.proposals)
    if not path.is_file(): refuse('deterministic EventKit proposal output is missing')
    proposal=json.loads(path.read_text()); counts=proposal.get('counts')
    if not isinstance(counts,dict) or not all(isinstance(counts.get(k),int) and counts[k]>=0 for k in ('exact','domain','unknown')): refuse('proposal output has nondeterministic aggregate counts')
    source=digest(proposal); output=digest({'schema':'calendar-canary-output-v1','exact_count':counts['exact'],'domain_count':counts['domain'],'unknown_count':counts['unknown']}); idem='calendar-canary-v1:'+digest({'destination':destination,'source':source,'output':output})
    import psycopg
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute('select (ops.record_calendar_canary_receipt(%s,%s,%s,%s,%s)).id',(destination,idem,source,output,counts['exact']))
        minted=cur.fetchone()
        cur.execute('select source_digest,output_digest,exact_count from ops.resolve_calendar_canary_receipt(%s,%s)',(destination,idem))
        row=cur.fetchone()
        if minted is None or row != (source,output,counts['exact']): refuse('immutable receipt readback does not reconcile')
        conn.commit()
    print(f'calendar-capture: source=eventkit mode=canary destination={destination} exact={counts["exact"]} receipt={minted[0]}')
    return 0
if __name__=='__main__':
    try: raise SystemExit(main())
    except Exception as exc: print(str(exc),file=sys.stderr); raise SystemExit(78)
