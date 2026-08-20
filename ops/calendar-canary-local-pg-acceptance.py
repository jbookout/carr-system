#!/usr/bin/env python3
"""Rollback-only proof for the isolated Calendar canary receipt target."""
import os
from urllib.parse import urlparse
import psycopg

def refuse(message): raise RuntimeError(message)
dsn=os.environ.get('CARR_LOCAL_PG_DSN','')
parsed=urlparse(dsn)
if parsed.scheme not in {'postgres','postgresql'} or parsed.hostname not in {'127.0.0.1','localhost','::1'}: refuse('calendar canary acceptance requires loopback CARR_LOCAL_PG_DSN')
with psycopg.connect(dsn) as conn:
  try:
    with conn.cursor() as cur:
      cur.execute('begin')
      cur.execute("insert into ops.calendar_canary_destination(destination_id,database_name) values ('calendar-canary-local',current_database())")
      key='calendar-canary-v1:'+'a'*64; source='b'*64; output='c'*64
      cur.execute('set session authorization carr_jobs')
      cur.execute('select (ops.record_calendar_canary_receipt(%s,%s,%s,%s,%s)).id',('calendar-canary-local',key,source,output,3)); first=cur.fetchone()[0]
      cur.execute('select (ops.record_calendar_canary_receipt(%s,%s,%s,%s,%s)).id',('calendar-canary-local',key,source,output,3)); replay=cur.fetchone()[0]
      if first != replay: refuse('exact canary replay did not return durable receipt')
      cur.execute('savepoint mutation_refusal')
      try:
        cur.execute('select ops.record_calendar_canary_receipt(%s,%s,%s,%s,%s)',('calendar-canary-local',key,'d'*64,output,3)); refuse('mutated exact replay was accepted')
      except psycopg.Error: cur.execute('rollback to savepoint mutation_refusal')
      cur.execute('reset session authorization')
      cur.execute('savepoint append_only_refusal')
      try:
        cur.execute('update ops.calendar_canary_receipt set exact_count=4 where id=%s',(first,)); refuse('immutable receipt update was accepted')
      except psycopg.Error: cur.execute('rollback to savepoint append_only_refusal')
      cur.execute("update ops.calendar_canary_destination set database_name='not-current-db' where destination_id='calendar-canary-local'")
      cur.execute('set session authorization carr_jobs')
      cur.execute('savepoint database_binding_refusal')
      try:
        cur.execute('select ops.record_calendar_canary_receipt(%s,%s,%s,%s,%s)',('calendar-canary-local','calendar-canary-v1:'+'e'*64,source,output,3)); refuse('database-alias mismatch was accepted')
      except psycopg.Error: cur.execute('rollback to savepoint database_binding_refusal')
      conn.rollback()
    print('calendar canary local acceptance passed: isolated destination, idempotency, append-only and database binding')
  except Exception:
    conn.rollback(); raise
