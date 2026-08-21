#!/usr/bin/env python3
"""No-service adversarial proof for parent/child calendar credential separation."""
from __future__ import annotations
import importlib.util
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; s=importlib.util.spec_from_file_location("c",ROOT/"tools/calendar-prebrief-coordinator.py"); assert s and s.loader; c=importlib.util.module_from_spec(s); s.loader.exec_module(c)
bad=[]
def ok(n,v): print(("  ok " if v else "  FAIL ")+n); bad.extend([] if v else [n])
def refuse(fn):
 try: fn()
 except c.Refusal:return True
 return False
k="a"*64; event={"sponsor":"joe","calendar_key":k,"event_key":"b"*64,"occurrence_key":"c"*64,"starts_at":"2026-08-20T01:00:00Z","ends_at":"2026-08-20T02:00:00Z","title":"Meeting","location":None,"participant_refs":["C-1"]}; snap={"version":1,"window":{"starts_at":"2026-08-13T00:00:00Z","ends_at":"2026-10-04T00:00:00Z"},"observed_calendars":[{"sponsor":"joe","calendar_key":k}],"events":[event]}
env={"CARR_DB_CALENDAR_PREBRIEF_DEVICE_JOE_URL":"device","CARR_DB_CALENDAR_PREBRIEF_JOE_URL":"ingest"}; calls=[]
out=c.child(sponsor="joe",job_id="j",lease="l",capture=lambda r:snap,resolve=lambda email:"C-1",attest=lambda *a:calls.append(a) or "att",ingest=lambda *a:calls.append(a) or "receipt",destination="live",env=env)
ok("attest precedes live ingest and sponsor is stripped",out["ingested"] and len(calls)==2 and all("sponsor" not in e for e in calls[0][3]))
ok("canary cannot call live ingest",not c.child(sponsor="joe",job_id="j",lease="l",capture=lambda r:snap,resolve=lambda e:"C-1",attest=lambda *a:"canary",ingest=lambda *a: (_ for _ in ()).throw(RuntimeError("live")),destination="isolated-canary",env=env)["ingested"])
ok("jobs credential child refusal",refuse(lambda:c.child(sponsor="joe",job_id="j",lease="l",capture=lambda r:snap,resolve=lambda e:"C-1",attest=lambda *a:None,ingest=lambda *a:None,destination="live",env={**env,"CARR_DB_JOBS_URL":"no"})))
ok("parent rejects lease-loss/incomplete claim",refuse(lambda:c.parent(sponsor="joe",claim={"job_id":"j"},spawn=lambda **_:{})))
print("OK" if not bad else "FAIL "+", ".join(bad));raise SystemExit(bool(bad))
