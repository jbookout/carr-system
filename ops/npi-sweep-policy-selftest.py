#!/usr/bin/env python3
"""Hermetic tests for deterministic NPPES NPI sweep extraction."""
from __future__ import annotations
import json, sys
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
from lib.npi_sweep import NpiInputError, filter_candidates, load_policy

policy=load_policy(json.loads((ROOT/'ops/config/npi-sweep-policy.v1.json').read_text()))
now=datetime(2026,8,16,tzinfo=timezone.utc)
def row(npi='1234567890', **overrides):
    value={'npi':npi,'source_ref':'nppes:weekly:1','enumeration_type':'NPI-2',
           'last_updated':'2026-08-10T00:00:00Z','addresses':[{'postal_code':'32501'}],
           'taxonomies':['207Q00000X']}; value.update(overrides); return value
def check(label, value):
    if not value: raise AssertionError(label)
    print('ok:',label)

out=filter_candidates([row()],policy=policy,approved_taxonomy_codes=['207Q00000X'],as_of=now)
check('in-territory fresh NPI-2 healthcare candidate is proposal-only',out==[{'npi':'1234567890','source_ref':'nppes:weekly:1','last_updated':'2026-08-10T00:00:00+00:00','postal_prefix':'325','taxonomy_codes':['207Q00000X'],'action':'propose'}])
for label, candidate in [
 ('NPI-1 refuses',row(enumeration_type='NPI-1')),
 ('out-of-territory Florida refuses',row(addresses=[{'postal_code':'33101'}])),
 ('stale row refuses',row(last_updated='2026-07-01T00:00:00Z')),
 ('unknown taxonomy refuses',row(taxonomies=['9999999999'])),
]: check(label,not filter_candidates([candidate],policy=policy,approved_taxonomy_codes=['207Q00000X'],as_of=now))
dupes=filter_candidates([row(source_ref='nppes:a',last_updated='2026-08-09T00:00:00Z'),row(source_ref='nppes:b')],policy=policy,approved_taxonomy_codes=['207Q00000X'],as_of=now)
check('duplicate NPI selects newest source stably',len(dupes)==1 and dupes[0]['source_ref']=='nppes:b')
for malformed in [row(npi='bad'),row(addresses='not-list'),row(taxonomies=[]),{'npi':'1234567890'}]:
    try: filter_candidates([malformed],policy=policy,approved_taxonomy_codes=['207Q00000X'],as_of=now)
    except NpiInputError: refused=True
    else: refused=False
    check('malformed source refuses',refused)
try: filter_candidates([row()],policy=policy,approved_taxonomy_codes=[],as_of=now)
except NpiInputError: refused=True
else: refused=False
check('missing reviewed taxonomy policy refuses',refused)
