#!/usr/bin/env python3
"""Rollback-only DB proof for the lease-bound Nightly canary source/receipt."""
import json, os, threading, uuid
from typing import Any
from urllib.parse import urlparse
import psycopg

dsn=os.environ.get('CARR_LOCAL_PG_DSN',''); parsed=urlparse(dsn)
if parsed.scheme not in {'postgres','postgresql'} or parsed.hostname not in {'127.0.0.1','localhost','::1'}:
    raise RuntimeError('Nightly canary acceptance requires loopback CARR_LOCAL_PG_DSN')
def one(value: tuple[Any, ...] | None, label: str) -> tuple[Any, ...]:
    if value is None: raise RuntimeError(f'missing expected row: {label}')
    return value
def refused(cur,sql,args,state,text):
    cur.execute('savepoint expected_refusal')
    try: cur.execute(sql,args); raise RuntimeError('expected refusal was accepted')
    except psycopg.Error as exc:
        if exc.sqlstate!=state or text not in str(exc): raise
        cur.execute('rollback to savepoint expected_refusal')
def seed(cur,job,lease):
    cur.execute("select id from actor where active order by slug limit 1"); actor=one(cur.fetchone(),'active actor')[0]
    cur.execute("select slug from client_status order by sort limit 1"); status=one(cur.fetchone(),'client status')[0]
    cur.execute("insert into party(kind,name,email,created_by,updated_by) values('person','Nightly Canary','nightly.canary@example.com',%s,%s) returning id",(actor,actor)); party=one(cur.fetchone(),'party')[0]
    cur.execute("insert into client(roster_ref,party_id,status,created_by,updated_by) values(%s,%s,%s,%s,%s) returning id",('C-NIGHTLY-'+str(job)[:8],party,status,actor,actor)); client=one(cur.fetchone(),'client')[0]
    cur.execute("insert into building(address,city,state,created_by,updated_by) values('1 Canary Way','Mobile','AL',%s,%s) returning id",(actor,actor)); building=one(cur.fetchone(),'building')[0]
    cur.execute("insert into space(building_id,suite,area_amount,created_by,updated_by) values(%s,'1',1000,%s,%s) returning id",(building,actor,actor)); space=one(cur.fetchone(),'space')[0]
    cur.execute("insert into availability(space_id,source,status,rate_amount,rate_basis,observed_at) values(%s,'fixture','available',10,'usd_sf_yr',now()-interval '1 second')",(space,))
    cur.execute("insert into availability(space_id,source,status,rate_amount,rate_basis,observed_at) values(%s,'fixture','leased',10,'usd_sf_yr',now())",(space,))
    cur.execute("insert into space_search(client_id,spec,created_by) values(%s,%s::jsonb,%s)",(client,'{"cities":["mobile"]}',actor))
    cur.execute("insert into ops.job(id,definition_key,definition_version,idempotency_key,scheduled_for,mode,state,attempt,max_attempts,next_attempt_at,lease_owner,lease_token,leased_until,timeout_seconds) values(%s,'nightly-record-layer',3,%s,now(),'canary','running',1,1,now(),'local',%s,now()+interval '5 minutes',60)",(job,str(job),lease))
def make_source(cur,job,lease):
    cur.execute('set session authorization carr_jobs'); cur.execute('select id,snapshot_digest,availability_count,open_search_count,snapshot_text from ops.create_nightly_availability_canary_source_snapshot(%s,%s)',(job,lease)); return one(cur.fetchone(),'canary source')
with psycopg.connect(dsn) as conn,conn.cursor() as cur:
    cur.execute('begin'); job,lease=uuid.uuid4(),uuid.uuid4(); seed(cur,job,lease)
    cur.execute("select execution_kind from ops.job_definition where key='nightly-record-layer' and version=3")
    if cur.fetchone()!=('deterministic',): raise RuntimeError('synced Nightly v3 definition is missing')
    # Baseline canonical surfaces before the source/receipt path, not after it.
    cur.execute('select count(*) from event'); events=one(cur.fetchone(),'event count')[0]
    cur.execute('select count(*) from next_action'); actions=one(cur.fetchone(),'next action count')[0]
    cur.execute('select count(*) from activity'); activity=one(cur.fetchone(),'activity count')[0]
    cur.execute('select count(*) from export_run'); exports=one(cur.fetchone(),'export count')[0]
    source=make_source(cur,job,lease); source_id,digest,acount,scount,snapshot_text=source
    if not acount or not scount: raise RuntimeError('nonvacuous source was not minted')
    snapshot=json.loads(snapshot_text)
    if acount!=1 or snapshot['availabilities'][0]['status']!='leased': raise RuntimeError('canary snapshot did not preserve normal newest-row-per-space semantics')
    cur.execute('reset session authorization'); cur.execute("update availability set source='fixture-drift' where source='fixture'")
    cur.execute('set session authorization carr_jobs'); refused(cur,'select * from ops.create_nightly_availability_canary_source_snapshot(%s,%s)',(job,lease),'23505','source replay conflicts with canonical snapshot')
    cur.execute('reset session authorization'); cur.execute("update availability set source='fixture' where source='fixture-drift'")
    cur.execute('set session authorization carr_jobs')
    args=(job,lease,source_id,'b'*64,1)
    cur.execute('select (ops.record_nightly_availability_canary_receipt(%s,%s,%s,%s,%s)).id',args); first=one(cur.fetchone(),'first receipt')[0]
    cur.execute('select (ops.record_nightly_availability_canary_receipt(%s,%s,%s,%s,%s)).id',args)
    if one(cur.fetchone(),'replayed receipt')[0]!=first: raise RuntimeError('exact replay did not return durable receipt')
    refused(cur,'select ops.record_nightly_availability_canary_receipt(%s,%s,%s,%s,%s)',(uuid.uuid4(),lease,source_id,'b'*64,1),'55000','current live job lease')
    refused(cur,'select ops.record_nightly_availability_canary_receipt(%s,%s,%s,%s,%s)',(job,uuid.uuid4(),source_id,'b'*64,1),'55000','current live job lease')
    refused(cur,'select ops.record_nightly_availability_canary_receipt(%s,%s,%s,%s,%s)',(job,lease,source_id,'c'*64,1),'23505','conflicts with immutable attempt')
    refused(cur,'update ops.nightly_availability_canary_receipt set match_count=9 where id=%s',(first,),'42501','permission denied')
    refused(cur,'delete from ops.nightly_availability_canary_receipt where id=%s',(first,),'42501','permission denied')
    refused(cur,'insert into ops.nightly_availability_canary_receipt(job_id,attempt,source_snapshot_id,source_snapshot_digest,availability_count,open_search_count,match_count,output_digest) values(%s,9,%s,%s,%s,%s,1,%s)',(job,source_id,digest,acount,scount,'b'*64),'42501','permission denied')
    refused(cur,'insert into ops.nightly_availability_canary_source_snapshot(job_id,attempt,workflow_version,snapshot,snapshot_digest,availability_count,open_search_count) values(%s,9,3,\'{}\',%s,0,0)',(job,'a'*64),'42501','permission denied')
    refused(cur,'delete from ops.nightly_availability_canary_source_snapshot where id=%s',(source_id,),'42501','permission denied')
    cur.execute('reset session authorization'); refused(cur,'update ops.nightly_availability_canary_source_snapshot set availability_count=9 where id=%s',(source_id,),'P0001','append-only')
    refused(cur,'update ops.nightly_availability_canary_receipt set match_count=9 where id=%s',(first,),'P0001','append-only')
    refused(cur,'delete from ops.nightly_availability_canary_receipt where id=%s',(first,),'P0001','append-only')
    cur.execute('set session authorization carr_writer')
    refused(cur,'select * from ops.create_nightly_availability_canary_source_snapshot(%s,%s)',(job,lease),'42501','permission denied')
    refused(cur,'select ops.record_nightly_availability_canary_receipt(%s,%s,%s,%s,%s)',args,'42501','permission denied')
    refused(cur,'select * from ops.resolve_nightly_availability_canary_receipt(%s,%s)',(job,1),'42501','permission denied')
    cur.execute('reset session authorization')
    cur.execute('select count(*) from event'); assert one(cur.fetchone(),'event count after')[0]==events
    cur.execute('select count(*) from next_action'); assert one(cur.fetchone(),'next action count after')[0]==actions
    cur.execute('select count(*) from activity'); assert one(cur.fetchone(),'activity count after')[0]==activity
    cur.execute('select count(*) from export_run'); assert one(cur.fetchone(),'export count after')[0]==exports
    conn.rollback()

def fixture():
    job,lease=uuid.uuid4(),uuid.uuid4()
    with psycopg.connect(dsn) as c, c.cursor() as cur: seed(cur,job,lease); c.commit()
    with psycopg.connect(dsn) as c,c.cursor() as cur: source=make_source(cur,job,lease)[0]; c.commit()
    return job,lease,source
def race(outputs,expect_conflict=False):
    job,lease,source=fixture(); barrier=threading.Barrier(2); got=[]
    def call(output):
        try:
            with psycopg.connect(dsn) as c,c.cursor() as cur:
                cur.execute('set session authorization carr_jobs'); barrier.wait(timeout=5)
                cur.execute('select (ops.record_nightly_availability_canary_receipt(%s,%s,%s,%s,%s)).id',(job,lease,source,output,1)); got.append(('ok',one(cur.fetchone(),'race receipt')[0],output)); c.commit()
        except Exception as exc: got.append((getattr(exc,'sqlstate','exception'),str(exc),output))
    ts=[threading.Thread(target=call,args=(x,)) for x in outputs]; [t.start() for t in ts]; [t.join(10) for t in ts]
    if any(t.is_alive() for t in ts): raise RuntimeError('receipt race thread did not finish')
    if expect_conflict:
        if sum(x[0]=='ok' for x in got)!=1 or not any(x[0]=='23505' and 'conflicts with immutable attempt' in x[1] for x in got): raise RuntimeError('mutated receipt race did not fail closed')
    elif len(got)!=2 or any(x[0]!='ok' for x in got) or got[0][1]!=got[1][1]: raise RuntimeError('exact receipt race did not converge')
    with psycopg.connect(dsn) as c,c.cursor() as cur:
        cur.execute('select count(*),min(output_digest),max(output_digest) from ops.nightly_availability_canary_receipt where job_id=%s and attempt=1',(job,)); row=one(cur.fetchone(),'race durable receipt')
        winners=[x[2] for x in got if x[0]=='ok']
        if row[0]!=1 or row[1]!=winners[0] or row[2]!=winners[0]: raise RuntimeError('receipt race durable row does not equal winning aggregate')
race(['d'*64,'d'*64]); race(['e'*64,'f'*64],True)
print('Nightly canary local acceptance passed')
