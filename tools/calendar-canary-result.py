#!/usr/bin/env python3
"""Emit a strict deterministic Calendar canary aggregate; never opens a DB."""
from __future__ import annotations
import argparse, hashlib, json, sys
from pathlib import Path
def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
def main() -> int:
    parser=argparse.ArgumentParser(); parser.add_argument('--proposals',required=True); args=parser.parse_args()
    proposal=json.loads(Path(args.proposals).read_text()); counts=proposal.get('counts')
    if not isinstance(counts,dict) or not all(type(counts.get(k)) is int and counts[k]>=0 for k in ('exact','domain','unknown')):
        raise RuntimeError('calendar canary result refused: counts must be non-negative integers')
    # Counts are the canonical, aggregate-only source material passed to the
    # parent. The parent recomputes both digests; no child assertion is trusted.
    source=proposal.get('_canary_source')
    if not isinstance(source,dict) or set(source)!={'source_snapshot_id','snapshot_digest','contact_count'}: raise RuntimeError('calendar canary result refused: source receipt is missing')
    print('calendar-capture: canary-result '+json.dumps({**source,'exact_count':counts['exact'],'domain_count':counts['domain'],'unknown_count':counts['unknown']},sort_keys=True,separators=(',',':')))
    return 0
if __name__=='__main__':
    try: raise SystemExit(main())
    except Exception as exc: print(str(exc),file=sys.stderr); raise SystemExit(78)
