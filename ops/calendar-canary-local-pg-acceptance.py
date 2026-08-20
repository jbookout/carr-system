#!/usr/bin/env python3
"""Rollback-only local proof for lease-bound Calendar canary receipts."""
import os, uuid, threading
from urllib.parse import urlparse
import psycopg
dsn=os.environ.get('CARR_LOCAL_PG_DSN',''); parsed=urlparse(dsn)
if parsed.scheme not in {'postgres','postgresql'} or parsed.hostname not in {'127.0.0.1','localhost','::1'}: raise RuntimeError('calendar canary acceptance requires loopback CARR_LOCAL_PG_DSN')
def refused(cur, sql, args, state, text):
 cur.execute('savepoint expected_refusal')
 try: cur.execute(sql,args); raise RuntimeError('expected refusal was accepted')
 except psycopg.Error as exc:
  if exc.sqlstate != state or text not in str(exc): raise
  cur.execute('rollback to savepoint expected_refusal')
with psycopg.connect(dsn) as conn, conn.cursor() as cur:
 cur.execute('begin'); lease=uuid.uuid4(); job=uuid.uuid4(); wrong=uuid.uuid4()
 cur.execute("select execution_kind from ops.job_definition where key='calendar-fetch-daily' and version=5"); definition=cur.fetchone()
 if definition != ('deterministic',): raise RuntimeError('synced Calendar v5 definition is missing')
 cur.execute("""insert into ops.job(id,definition_key,definition_version,idempotency_key,scheduled_for,mode,state,attempt,max_attempts,next_attempt_at,lease_owner,lease_token,leased_until,timeout_seconds) values(%s,'calendar-fetch-daily',5,%s,now(),'canary','running',1,1,now(),'local',%s,now()+interval '5 minutes',60)""",(job,str(job),lease))
 cur.execute("select id from actor where active order by slug limit 1"); actor=cur.fetchone()[0]
 cur.execute("select slug from client_status order by sort limit 1"); status=cur.fetchone()[0]
 cur.execute("insert into party(kind,name,email,created_by,updated_by) values('person','Calendar Acceptance',' calendar.acceptance@example.com ',%s,%s) returning id",(actor,actor)); party=cur.fetchone()[0]
 cur.execute("insert into client(roster_ref,party_id,status,created_by,updated_by) values('C-CALENDAR-ACCEPTANCE',%s,%s,%s,%s)",(party,status,actor,actor))
 cur.execute('set session authorization carr_jobs'); cur.execute('select id,snapshot_digest,contact_count from ops.create_calendar_canary_source_snapshot(%s,%s)',(job,lease)); source_id,source_digest,source_count=cur.fetchone(); args=(job,lease,source_id,'b'*64,1,2,3)
 cur.execute('select (ops.record_calendar_canary_receipt(%s,%s,%s,%s,%s,%s,%s)).id',args); first=cur.fetchone()[0]
 cur.execute('select (ops.record_calendar_canary_receipt(%s,%s,%s,%s,%s,%s,%s)).id',args)
 if cur.fetchone()[0]!=first: raise RuntimeError('exact replay did not return same receipt')
 refused(cur,'select ops.record_calendar_canary_receipt(%s,%s,%s,%s,%s,%s,%s)',(wrong,lease,source_id,'b'*64,1,2,3),'55000','current live job lease')
 refused(cur,'select ops.record_calendar_canary_receipt(%s,%s,%s,%s,%s,%s,%s)',(job,uuid.uuid4(),source_id,'b'*64,1,2,3),'55000','current live job lease')
 refused(cur,'select ops.record_calendar_canary_receipt(%s,%s,%s,%s,%s,%s,%s)',(job,lease,source_id,'c'*64,1,2,3),'23505','conflicts with immutable attempt')
 refused(cur,'insert into ops.calendar_canary_receipt(job_id,attempt,source_snapshot_id,source_snapshot_digest,source_contact_count,output_digest,exact_count,domain_count,unknown_count) values(%s,9,%s,%s,%s,%s,1,2,3)',(job,source_id,source_digest,source_count,'b'*64),'42501','permission denied')
 refused(cur,'insert into ops.calendar_canary_source_snapshot(job_id,attempt,workflow_version,snapshot,snapshot_digest,contact_count) values(%s,9,5,\'[]\',%s,0)',(job,'a'*64),'42501','permission denied')
 refused(cur,'update ops.calendar_canary_receipt set exact_count=9 where id=%s',(first,),'42501','permission denied')
 refused(cur,'delete from ops.calendar_canary_receipt where id=%s',(first,),'42501','permission denied')
 refused(cur,'update ops.calendar_canary_source_snapshot set contact_count=9 where id=%s',(source_id,),'42501','permission denied')
 refused(cur,'delete from ops.calendar_canary_source_snapshot where id=%s',(source_id,),'42501','permission denied')
 cur.execute('reset session authorization'); refused(cur,'update ops.calendar_canary_receipt set exact_count=9 where id=%s',(first,),'P0001','append-only')
 cur.execute('set session authorization carr_writer')
 refused(cur,'select ops.record_calendar_canary_receipt(%s,%s,%s,%s,%s,%s,%s)',args,'42501','permission denied')
 refused(cur,'select * from ops.resolve_calendar_canary_receipt(%s,%s)',(job,1),'42501','permission denied')
 refused(cur,'select * from ops.create_calendar_canary_source_snapshot(%s,%s)',(job,lease),'42501','permission denied')
 cur.execute('reset session authorization')
 refused(cur,'update ops.calendar_canary_source_snapshot set contact_count=9 where id=%s',(source_id,),'P0001','append-only')
 cur.execute('select snapshot_digest from ops.calendar_canary_source_snapshot where id=%s',(source_id,)); original_snapshot_digest=cur.fetchone()[0]
 cur.execute("update party set email='calendar.changed@example.com' where id=%s",(party,))
 cur.execute('set session authorization carr_jobs')
 refused(cur,'select * from ops.create_calendar_canary_source_snapshot(%s,%s)',(job,lease),'23505','source snapshot replay conflicts with canonical contacts')
 cur.execute('reset session authorization');cur.execute('select snapshot_digest from ops.calendar_canary_source_snapshot where id=%s',(source_id,))
 if cur.fetchone()[0]!=original_snapshot_digest: raise RuntimeError('source replay rewrote immutable snapshot')
 cur.execute('select count(*) from event'); before=cur.fetchone()[0]
 cur.execute('select count(*) from activity'); activity_before=cur.fetchone()[0]
 cur.execute('select count(*) from event');
 if cur.fetchone()[0]!=before: raise RuntimeError('canary changed live activity rows')
 cur.execute('select count(*) from activity')
 if cur.fetchone()[0]!=activity_before: raise RuntimeError('canary changed live activity rows')
 conn.rollback()

# This fixture is committed only inside the disposable cluster so two real
# pooled connections can contend on the unique (job_id,attempt) boundary.
with psycopg.connect(dsn) as setup:
 with setup.cursor() as cur:
  job2=uuid.uuid4(); lease2=uuid.uuid4()
  cur.execute("select id from actor where active order by slug limit 1"); actor=cur.fetchone()[0]
  cur.execute("select slug from client_status order by sort limit 1"); status=cur.fetchone()[0]
  cur.execute("insert into party(kind,name,email,created_by,updated_by) values('person','Calendar Canary Fixture',' calendar.fixture@example.com ',%s,%s) returning id",(actor,actor)); party=cur.fetchone()[0]
  cur.execute("insert into client(roster_ref,party_id,status,created_by,updated_by) values('C-CALENDAR-CANARY',%s,%s,%s,%s)",(party,status,actor,actor))
  cur.execute("select execution_kind from ops.job_definition where key='calendar-fetch-daily' and version=5")
  if cur.fetchone() != ('deterministic',): raise RuntimeError('synced Calendar v5 definition is missing')
  cur.execute("""insert into ops.job(id,definition_key,definition_version,idempotency_key,scheduled_for,mode,state,attempt,max_attempts,next_attempt_at,lease_owner,lease_token,leased_until,timeout_seconds) values(%s,'calendar-fetch-daily',5,%s,now(),'canary','running',1,1,now(),'local',%s,now()+interval '5 minutes',60)""",(job2,str(job2),lease2))
  setup.commit()
with psycopg.connect(dsn) as source_conn, source_conn.cursor() as cur:
 cur.execute('set session authorization carr_jobs'); cur.execute('select id from ops.create_calendar_canary_source_snapshot(%s,%s)',(job2,lease2)); source2=cur.fetchone()[0]; source_conn.commit()
barrier=threading.Barrier(2); results=[]
def contender(job_id,lease_token,source_id,output,gate,target):
 try:
  with psycopg.connect(dsn) as c, c.cursor() as cur:
   cur.execute('set session authorization carr_jobs'); gate.wait(timeout=5)
   cur.execute('select (ops.record_calendar_canary_receipt(%s,%s,%s,%s,%s,%s,%s)).id',(job_id,lease_token,source_id,output,1,2,3)); target.append(('ok',cur.fetchone()[0],output)); c.commit()
 except psycopg.Error as exc: target.append((exc.sqlstate,str(exc),output))
threads=[threading.Thread(target=contender,args=(job2,lease2,source2,'b'*64,barrier,results)),threading.Thread(target=contender,args=(job2,lease2,source2,'b'*64,barrier,results))]
[t.start() for t in threads]; [t.join(timeout=10) for t in threads]
if len(results)!=2 or any(row[0]!='ok' for row in results) or results[0][1]!=results[1][1]: raise RuntimeError('concurrent exact replay did not return one durable receipt')
with psycopg.connect(dsn) as verify, verify.cursor() as cur:
 cur.execute('select count(*) from ops.calendar_canary_receipt where job_id=%s and attempt=1',(job2,))
 if cur.fetchone()[0]!=1: raise RuntimeError('concurrent exact replay created more than one row')
 cur.execute('select output_digest from ops.calendar_canary_receipt where job_id=%s',(job2,));
 if cur.fetchone()[0] != 'b'*64: raise RuntimeError('exact race durable fields mismatch')
with psycopg.connect(dsn) as setup:
 with setup.cursor() as cur:
  job3=uuid.uuid4();lease3=uuid.uuid4();cur.execute("""insert into ops.job(id,definition_key,definition_version,idempotency_key,scheduled_for,mode,state,attempt,max_attempts,next_attempt_at,lease_owner,lease_token,leased_until,timeout_seconds) values(%s,'calendar-fetch-daily',5,%s,now()+interval '1 second','canary','running',1,1,now(),'local',%s,now()+interval '5 minutes',60)""",(job3,str(job3),lease3));setup.commit()
with psycopg.connect(dsn) as source_conn, source_conn.cursor() as cur:
 cur.execute('set session authorization carr_jobs');cur.execute('select id from ops.create_calendar_canary_source_snapshot(%s,%s)',(job3,lease3));source3=cur.fetchone()[0];source_conn.commit()
results=[]; barrier=threading.Barrier(2)
threads=[threading.Thread(target=contender,args=(job3,lease3,source3,'d'*64,barrier,results)),threading.Thread(target=contender,args=(job3,lease3,source3,'e'*64,barrier,results))]
[t.start() for t in threads]; [t.join(timeout=10) for t in threads]
if len(results)!=2 or sum(row[0]=='ok' for row in results)!=1 or not any(row[0]=='23505' and 'conflicts with immutable attempt' in row[1] for row in results): raise RuntimeError('concurrent mutated replay did not fail closed with 23505')
winner=next(row for row in results if row[0]=='ok')
with psycopg.connect(dsn) as verify,verify.cursor() as cur:
 cur.execute('select count(*),min(output_digest) from ops.calendar_canary_receipt where job_id=%s and attempt=1',(job3,));row=cur.fetchone()
 if row!=(1,winner[2]): raise RuntimeError('mutated race durable row does not equal winner')
print('calendar canary local acceptance passed')
