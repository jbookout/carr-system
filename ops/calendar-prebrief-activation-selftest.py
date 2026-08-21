#!/usr/bin/env python3
"""Hermetic adversarial checks for local calendar-prebrief activation inputs."""
from __future__ import annotations
import importlib.util, os, tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("activation",ROOT/"tools/calendar-prebrief-activation.py"); assert spec and spec.loader
mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
bad=[]
def check(name, ok):
 print(("  ok " if ok else "  FAIL ")+name); bad.extend([] if ok else [name])
def refuses(fn):
 try: fn()
 except mod.Refusal: return True
 return False
def dsn(user): return f"postgresql://{user}:fixture@db.example/carr?sslmode=verify-full&sslrootcert=/etc/ssl/carr-root.pem&channel_binding=require" # ci-secret-scan: allow
with tempfile.TemporaryDirectory() as raw:
 root=Path(raw); env=root/"prebrief.env"
 values={"CARR_DB_AUTHORITY_JOE_URL":dsn("carr_authority_joe"),"CARR_DB_AUTHORITY_DELL_URL":dsn("carr_authority_dell"),"CARR_DB_CALENDAR_PREBRIEF_ATTESTOR_JOE_URL":dsn("carr_calendar_prebrief_attestor_joe"),"CARR_DB_CALENDAR_PREBRIEF_ATTESTOR_DELL_URL":dsn("carr_calendar_prebrief_attestor_dell"),"CARR_DB_CALENDAR_PREBRIEF_RESOLVER_JOE_URL":dsn("carr_calendar_prebrief_resolver_joe"),"CARR_DB_CALENDAR_PREBRIEF_RESOLVER_DELL_URL":dsn("carr_calendar_prebrief_resolver_dell"),"CARR_DB_CALENDAR_PREBRIEF_JOE_URL":dsn("carr_calendar_prebrief_joe"),"CARR_DB_CALENDAR_PREBRIEF_DELL_URL":dsn("carr_calendar_prebrief_dell"),"CARR_DB_CALENDAR_PREBRIEF_CANARY_JOE_URL":dsn("carr_calendar_prebrief_canary_joe"),"CARR_DB_CALENDAR_PREBRIEF_CANARY_DELL_URL":dsn("carr_calendar_prebrief_canary_dell"),"CARR_DB_JOBS_URL":dsn("carr_jobs")}
 env.write_text("\n".join(f"{k}='{v}'" for k,v in values.items())+"\n"); env.chmod(0o600)
 parsed=mod.load_scoped_env(env,{})
 check("secure exact scoped env is admitted",set(parsed)==set(values))
 class ProbeCur:
  def __init__(self): self.i=0
  def execute(self,*_): self.i+=1
  def fetchone(self): return ("carr_calendar_prebrief_joe","carr_calendar_prebrief_joe") if self.i==2 else (True,)
  def __enter__(self): return self
  def __exit__(self,*_): return False
 class ProbeConn:
  def cursor(self): return ProbeCur()
  def __enter__(self): return self
  def __exit__(self,*_): return False
 check("identity probe requires exact login and capability",mod.probe_scoped_identity("CARR_DB_CALENDAR_PREBRIEF_JOE_URL",values["CARR_DB_CALENDAR_PREBRIEF_JOE_URL"],lambda _:ProbeConn()))
 class PreflightCur:
  def __init__(self,user): self.user=user; self.query=""
  def execute(self,q,*_): self.query=q
  def fetchone(self): return (self.user,self.user) if "session_user,current_user" in self.query else (True,)
  def __enter__(self): return self
  def __exit__(self,*_): return False
 class PreflightConn:
  def __init__(self,user): self.user=user
  def cursor(self): return PreflightCur(self.user)
  def __enter__(self): return self
  def __exit__(self,*_): return False
 probes=[]
 def connect_preflight(value):
  probes.append(value)
  user=value.split("://",1)[1].split(":",1)[0]
  return PreflightConn(user)
 check("preflight proves every identity before reporting ready",mod.preflight(parsed,connect_preflight)["ok"] and len(probes)==len(values))
 check("URI rejects host/query service override",not mod.strict_uri("postgresql://carr_jobs:x@db/carr?sslmode=verify-full&sslrootcert=/etc/root&host=evil", "carr_jobs")) # ci-secret-scan: allow
 check("URI requires verify-full and root trust",not mod.strict_uri("postgresql://carr_jobs:x@db/carr?sslmode=require&sslrootcert=/etc/root", "carr_jobs")) # ci-secret-scan: allow
 check("ambient broad credential refuses",refuses(lambda:mod.load_scoped_env(env,{"DATABASE_URL":"x"})))
 env.chmod(0o644); check("insecure env permissions refuse",refuses(lambda:mod.load_scoped_env(env,{}))); env.chmod(0o600)
 link=root/"link.env"; link.symlink_to(env); check("symlink env refuses",refuses(lambda:mod.load_scoped_env(link,{})))
 allow=root/"joe.json"; allow.write_text('{"version":1,"calendars":[{"identifier":"raw-id","sponsor":"joe"}]}'); allow.chmod(0o600)
 calls=[]
 class Cur:
  def execute(self,q,p=None): calls.append((q,p))
  def fetchone(self): return ("carr_authority_joe","carr_authority_joe") if len(calls)==1 else ({"sponsor":"joe","configuration_digest":"d"*64,"active_revision_id":"r"},)
  def __enter__(self): return self
  def __exit__(self,*_): return False
 class Conn:
  def cursor(self): return Cur()
  def commit(self): calls.append(("commit",None))
  def __enter__(self): return self
  def __exit__(self,*_): return False
 out=mod.register_allowlist("joe",allow,parsed,lambda _:Conn())
 check("registrar hashes raw ID and reports redacted receipt",out=={"sponsor":"joe","calendar_count":1,"configuration_digest":"d"*64,"revision":"r"} and "raw-id" not in str(calls))
 check("registrar rejects cross-sponsor config",refuses(lambda:mod.register_allowlist("dell",allow,parsed,lambda _:Conn())))
print("OK" if not bad else "FAIL "+", ".join(bad)); raise SystemExit(bool(bad))
