#!/usr/bin/env python3
"""Separated jobs-parent and sponsor-child contract for calendar prebriefs.

The parent owns the jobs lease only.  A child gets one device DSN and one
sponsor ingest DSN; raw attendee addresses remain in the child's memory.
"""
from __future__ import annotations
import importlib.util, json, os
from pathlib import Path
from typing import Any, Callable, Mapping

REPO=Path(__file__).resolve().parent.parent
class Refusal(RuntimeError): pass
def _load(name:str):
 s=importlib.util.spec_from_file_location(name,REPO/"tools"/f"{name.replace('_','-')}.py"); assert s and s.loader
 m=importlib.util.module_from_spec(s); s.loader.exec_module(m); return m

def _clean_env(env:Mapping[str,str], sponsor:str)->dict[str,str]:
 if env.get("CARR_DB_JOBS_URL") or env.get("DATABASE_URL") or any(env.get(k) for k in ("CARR_DB_WRITER_URL","CARR_DB_OWNER_URL","CARR_DB_EXPORTER_URL")):
  raise Refusal("sponsor child must not receive a jobs or broad database credential")
 keys=(f"CARR_DB_CALENDAR_PREBRIEF_DEVICE_{sponsor.upper()}_URL",f"CARR_DB_CALENDAR_PREBRIEF_{sponsor.upper()}_URL")
 if any(not env.get(k) for k in keys): raise Refusal("sponsor child lacks its exact scoped credentials")
 return {k:env[k] for k in keys}

def child(*, sponsor:str, job_id:str, lease:str, capture:Callable[[Callable[[str],str]],dict[str,Any]],
          resolve:Callable[[str],str], attest:Callable[[str,str,list[str],list[dict[str,Any]],str],Any],
          ingest:Callable[[str,str,list[str],list[dict[str,Any]]],Any], destination:str, env:Mapping[str,str])->dict[str,Any]:
 if sponsor not in {"joe","dell"} or destination not in {"live","isolated-canary"}: raise Refusal("invalid sponsor or attestation destination")
 _clean_env(env,sponsor)
 snapshot=capture(resolve)
 bridge=_load("calendar_prebrief_ingest")
 observed, events=bridge.normalize_snapshot(snapshot,sponsor)
 # The bridge strips the sponsor before either DB call; raw email is only held
 # inside the resolver callback while EventKit is constructing this projection.
 attestation=attest(job_id,lease,observed,events,destination)
 if destination != "live":
  return {"sponsor":sponsor,"destination":destination,"attestation":attestation,"ingested":False}
 receipt=ingest(job_id,lease,observed,events)
 return {"sponsor":sponsor,"destination":"live","attestation":attestation,"ingested":True,"receipt":receipt}

def parent(*, sponsor:str, claim:Mapping[str,Any], spawn:Callable[...,dict[str,Any]])->dict[str,Any]:
 """Pass a current jobs lease without forwarding the jobs credential itself."""
 required={"job_id","lease","scheduled_for"}
 if set(claim)!=required or not all(isinstance(claim[k],str) and claim[k] for k in required): raise Refusal("jobs parent requires exact leased claim")
 return spawn(sponsor=sponsor,job_id=claim["job_id"],lease=claim["lease"],scheduled_for=claim["scheduled_for"])
