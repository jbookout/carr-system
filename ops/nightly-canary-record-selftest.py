#!/usr/bin/env python3
"""Hermetic contract checks for the isolated Nightly availability canary."""
from __future__ import annotations
import hashlib, json, os, shutil, subprocess, sys, uuid
from pathlib import Path
from typing import Any

ROOT=Path(__file__).resolve().parents[1]
failed: list[str] = []; checks = 0
def check(label, ok):
    global checks; checks+=1
    print(('  ok  ' if ok else '  FAIL ')+label)
    if not ok: failed.append(label)

nightly=(ROOT/'bin/nightly.sh').read_text()
matcher=(ROOT/'pipelines/availability_matcher.py').read_text()
runner=(ROOT/'tools/control-plane.py').read_text()
sql=(ROOT/'migrations/0221_nightly_availability_canary_record_layer.sql').read_text()
manifest=json.loads((ROOT/'ops/config/control-plane-workflows.v1.json').read_text())
workflow=next(w for w in manifest['workflows'] if w['key']=='nightly-record-layer')
check('Nightly canary is an explicit standalone argument before normal setup',
      'run_canary()' in nightly and '[ "$#" -eq 1 ] && [ "$1" = "--canary" ]' in nightly
      and nightly.index('run_canary()') < nightly.index('LOG="$REPO/out/nightly.log"'))
check('canary refuses ambient Drive, database, provider and secret capability before mkdir or lock',
      all(key in nightly for key in ('PG*','CARR_ONEDRIVE_DEALS','CARR_INGEST_URL','CARR_AI_ROUTE_PRIMARY_URL','CARR_GMAIL_APP_PASSWORD','CARR_AGE_IDENTITY','CARR_DB_*','*TOKEN*|*SECRET*|*PASSWORD*|*API_KEY*|*PROVIDER_URL*'))
      and nightly.index('nightly canary refused ambient live capability') < nightly.index('mkdir -p "$REPO/out"'))
check('canary runs only availability matcher from protected stdin',
      'pipelines/availability_matcher.py" --canary' in nightly and 'snapshot="$(cat)"' in nightly
      and 'nightly canary result:' in nightly and 'carr_take_lock nightly' not in nightly[:nightly.index('if [ "${1:-}" = "--preflight" ]')])
check('child rejects database capability and constrains output to one direct non-symlinked canary run directory',
      'CARR_DB_' in matcher and '_safe_canary_root' in matcher and 'root.parent != base' in matcher and 'root.is_symlink()' in matcher and 'source is vacuous' in matcher and 'os.replace(temp_name, path)' in matcher and 'os.unlink(temp_name)' in matcher)
check('manifest enables exact v3 isolated canary and code registers its guard',
      workflow['version']==3 and workflow['execution']['canary']=={'enabled':True,'isolation_guard':'nightly-record-layer.availability-matcher.canary.v1','args':['--canary']}
      and 'nightly-record-layer.availability-matcher.canary.v1' in (ROOT/'lib/control_plane.py').read_text())
check('parent alone mints and exact-readbacks a lease-bound source and receipt',
      'create_nightly_availability_canary_source_snapshot' in runner and 'record_nightly_availability_canary_receipt' in runner
      and 'resolve_nightly_availability_canary_receipt' in runner and 'nightly canary immutable receipt did not read back' in runner)
check('database source and receipt bind exact Nightly v3 deterministic canary lease and are append-only',
      "j.definition_key<>'nightly-record-layer' or j.definition_version<>3 or j.mode<>'canary'" in sql
      and 'unique(job_id,attempt)' in sql and 'nightly_availability_canary_source_append_only' in sql
      and 'nightly_availability_canary_receipt_append_only' in sql)
check('source is database-owned canonical availability/search joins and direct DML is denied',
      'distinct on (av.space_id)' in sql and 'order by av.space_id,av.observed_at desc,av.id desc' in sql and 'from space_search s join client' in sql
      and 'revoke all on ops.nightly_availability_canary_source_snapshot,ops.nightly_availability_canary_receipt' in sql)
check('disposable local PostgreSQL CI discovers the mandatory lease, ACL, drift, and race gate',
      'ops/nightly-canary-local-pg-acceptance.py' in (ROOT/'ops/local-pg-ci.py').read_text()
      and 'source replay conflicts with canonical snapshot' in (ROOT/'ops/nightly-canary-local-pg-acceptance.py').read_text()
      and "race(['d'*64,'d'*64])" in (ROOT/'ops/nightly-canary-local-pg-acceptance.py').read_text())

base=ROOT/'out/canary/nightly-record-layer'; run=base/('selftest-'+uuid.uuid4().hex)
source={'availabilities':[{'id':'00000000-0000-0000-0000-000000000011','status':'available','rate_norm':None,'owed':False,'available_on':None,'observed':'2026-08-20','source':'fixture','area':1000,'suite':'1','city':'Mobile','state':'AL','sub_type':'office','address':'1 Test Way','bname':'Fixture'}],
        'searches':[{'id':'00000000-0000-0000-0000-000000000012','spec':{'cities':['mobile']},'ref':'C-TEST','name':'Fixture Search'}]}
digest=hashlib.sha256(json.dumps(source,sort_keys=True,separators=(',',':')).encode()).hexdigest()
payload={'source_snapshot_id':'00000000-0000-0000-0000-000000000001','snapshot_digest':digest,'snapshot_preimage':json.dumps(source,sort_keys=True,separators=(',',':'))}
before=(ROOT/'out/availability-matches.md').read_bytes() if (ROOT/'out/availability-matches.md').exists() else None
env={'PATH':'/Users/booko/carr-system/.venv/bin:'+os.environ.get('PATH','/usr/bin:/bin'),'HOME':os.environ.get('HOME','/tmp'),'CARR_CONTROL_PLANE_MODE':'canary','CARR_NIGHTLY_CANARY_ROOT':str(run)}
try:
    result=subprocess.run(['/Users/booko/carr-system/.venv/bin/python',str(ROOT/'pipelines/availability_matcher.py'),'--canary'],cwd=ROOT,env=env,input=json.dumps(payload),text=True,capture_output=True,timeout=15)
    check('protected availability-matcher canary emits exactly one typed aggregate',result.returncode==0 and result.stdout.startswith('availability-matcher: canary-result ') and result.stdout.count('canary-result')==1)
    check('canary writes only its dedicated report root',run.is_dir() and (run/'availability-matches.json').is_file())
    after=(ROOT/'out/availability-matches.md').read_bytes() if (ROOT/'out/availability-matches.md').exists() else None
    check('canary leaves normal matcher report and canonical outputs untouched',before==after)
finally:
    shutil.rmtree(run,ignore_errors=True)
tampered=base/('tampered-'+uuid.uuid4().hex)
tampered_payload={**payload,'snapshot_preimage':payload['snapshot_preimage'].replace('"Mobile"','"Elsewhere"')}
result=subprocess.run(['/Users/booko/carr-system/.venv/bin/python',str(ROOT/'pipelines/availability_matcher.py'),'--canary'],cwd=ROOT,env={**env,'CARR_NIGHTLY_CANARY_ROOT':str(tampered)},input=json.dumps(tampered_payload),text=True,capture_output=True,timeout=15)
check('tampered protected snapshot bytes fail before output creation',result.returncode!=0 and 'protected snapshot bytes do not reconcile' in result.stderr and not tampered.exists())
poisoned=('DATABASE_URL','PGHOST','PGHOSTADDR','PGPORT','PGDATABASE','PGUSER','PGPASSFILE','PGSERVICEFILE','PGOPTIONS','PGSSLCERT','PGSSLKEY','PGSSLROOTCERT','PGSSLCRL','PGSSLSNI','CARR_ONEDRIVE_DEALS','CARR_INGEST_URL','CARR_AI_ROUTE_PRIMARY_URL','CARR_GMAIL_APP_PASSWORD','CARR_AGE_IDENTITY')
poison_results=[]; child_poison_results=[]
for key in poisoned:
    poison=base/('poison-'+key.lower()+'-'+uuid.uuid4().hex)
    poison_env={**env,'CARR_NIGHTLY_CANARY_ROOT':str(poison),key:'poisoned-live-value'}
    result=subprocess.run(['/bin/zsh',str(ROOT/'bin/nightly.sh'),'--canary'],cwd=ROOT,env=poison_env,input=json.dumps(payload),text=True,capture_output=True,timeout=15)
    poison_results.append(result.returncode!=0 and f'refused ambient live capability: {key}' in result.stderr and not poison.exists())
    child_poison=base/('child-poison-'+key.lower()+'-'+uuid.uuid4().hex)
    child_env={**env,'CARR_NIGHTLY_CANARY_ROOT':str(child_poison),key:'poisoned-live-value'}
    child_result=subprocess.run(['/Users/booko/carr-system/.venv/bin/python',str(ROOT/'pipelines/availability_matcher.py'),'--canary'],cwd=ROOT,env=child_env,input=json.dumps(payload),text=True,capture_output=True,timeout=15)
    child_poison_results.append(child_result.returncode!=0 and 'refused ambient live capability' in child_result.stderr and not child_poison.exists())
check('poisoned database, provider, and Drive environment refuses in both layers before output creation',all(poison_results) and all(child_poison_results))
print(f'nightly canary record selftest — {checks-len(failed)}/{checks} passed')
raise SystemExit(bool(failed))
