#!/usr/bin/env python3
"""Hermetic proof that all manifest facts build from typed evidence or refuse."""
from __future__ import annotations
import json, sys
from datetime import datetime, timezone
from pathlib import Path

REPO=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(REPO))
from lib.control_plane_facts import EnvelopeFactCollector, FactConflict, FactProtocolError, FactUnavailable, RECEIPT_FACTS, build_fact, build_stage_facts, evaluate_stage, manifest_facts, registry_errors

manifest=json.loads((REPO/'ops/config/control-plane-workflows.v1.json').read_text())
now=datetime.now(timezone.utc).isoformat(); passed=0; failed=[]

def envelope(fact,value=True):
    return {'schema_version':1,'fact':fact,'source_kind':'canonical_receipt' if fact in RECEIPT_FACTS else 'canonical_db','source_ref':'evidence:'+fact,'observed_at':now,'value':value}
def check(label,ok):
    global passed
    if ok: passed+=1
    else: failed.append(label)

check('registry exactly covers every manifest fact',not registry_errors(manifest))
for workflow in manifest['workflows']:
    all_evidence=[envelope(f) for stage in ('routing','filtering','validation','completion') for f in workflow[stage]['spec']['all_of']]
    for stage in ('routing','filtering','validation','completion'):
        try:
            context=build_stage_facts(workflow,stage,EnvelopeFactCollector(all_evidence))
            check(workflow['key']+'.'+stage+' builds every fact',set(context)==set(workflow[stage]['spec']['all_of']))
            check(workflow['key']+'.'+stage+' evaluates evidence',evaluate_stage(workflow,stage,EnvelopeFactCollector(all_evidence)))
            broken=[envelope(f) for f in workflow[stage]['spec']['all_of']]
            broken[0]['value']=False
            check(workflow['key']+'.'+stage+' refuses one false fact',not evaluate_stage(workflow,stage,EnvelopeFactCollector(broken)))
        except Exception as exc: failed.append(workflow['key']+'.'+stage+': '+repr(exc))

workflow=manifest['workflows'][0]; fact=workflow['routing']['spec']['all_of'][0]
try: build_stage_facts(workflow,'routing',EnvelopeFactCollector([])); check('missing fact refuses',False)
except FactUnavailable: check('missing fact refuses',True)
try: build_stage_facts(workflow,'routing',EnvelopeFactCollector([envelope(fact),envelope(fact,False)])); check('conflict refuses',False)
except FactConflict: check('conflict refuses',True)
receipt=next(iter(RECEIPT_FACTS))
bad=envelope(receipt); bad['source_kind']='canonical_db'
try: build_fact(receipt,EnvelopeFactCollector([bad]),workflow_key='fixture',stage='completion'); check('receipt type refuses',False)
except FactProtocolError: check('receipt type refuses',True)
print(f'control-plane facts selftest — {passed}/{passed+len(failed)} passed; {len(manifest_facts(manifest))} manifest facts')
if failed: print('FAILED: '+'; '.join(failed[:8])); raise SystemExit(1)
