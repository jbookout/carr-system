#!/usr/bin/env python3
"""Hermetic adversarial checks for local calendar-prebrief activation inputs."""
from __future__ import annotations
import importlib.util, os, tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("activation",ROOT/"tools/calendar-prebrief-activation.py"); assert spec and spec.loader
mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
bad: list[str] = []
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
 joe_env=root/"joe-live.env"; joe_keys=("CARR_DB_AUTHORITY_JOE_URL","CARR_DB_JOBS_URL","CARR_DB_CALENDAR_PREBRIEF_ATTESTOR_JOE_URL","CARR_DB_CALENDAR_PREBRIEF_RESOLVER_JOE_URL","CARR_DB_CALENDAR_PREBRIEF_JOE_URL")
 joe_env.write_text("\n".join(f"{k}={values[k]}" for k in joe_keys)+"\n"); joe_env.chmod(0o600)
 joe=mod.load_joe_live_env(joe_env,{})
 check("Joe live loader admits exactly five Joe identities",set(joe)==set(joe_keys) and "DELL" not in str(joe))
 check("Joe live preflight refuses cross-sponsor identity",refuses(lambda:mod.joe_live_preflight({**joe,"CARR_DB_AUTHORITY_DELL_URL":values["CARR_DB_AUTHORITY_DELL_URL"]},lambda _:None)))
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
 probes: list[str] = []
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
 calls: list[tuple[object, object]] = []
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
 # The only supported live switch is DB receipt -> atomic local profile ->
 # typed launchd readback.  Use a fake connector/runner to prove ordering
 # without a database, launcher, or secrets.
 app=root/"Applications"/"CARR Calendar Access.app"; (app/"Contents/MacOS").mkdir(parents=True)
 (app/"Contents/MacOS/carr-calendar-access").write_text("#!/bin/sh\n"); (app/"Contents/MacOS/carr-calendar-access").chmod(0o700)
 (app/"Contents/Info.plist").write_text("us.carr.calendar-access NSCalendarsFullAccessUsageDescription")
 child=root/"child.env"; child.write_text("CARR_DB_CALENDAR_PREBRIEF_ATTESTOR_JOE_URL="+values["CARR_DB_CALENDAR_PREBRIEF_ATTESTOR_JOE_URL"]+"\nCARR_DB_CALENDAR_PREBRIEF_RESOLVER_JOE_URL="+values["CARR_DB_CALENDAR_PREBRIEF_RESOLVER_JOE_URL"]+"\nCARR_DB_CALENDAR_PREBRIEF_JOE_URL="+values["CARR_DB_CALENDAR_PREBRIEF_JOE_URL"]+"\n"); child.chmod(0o600)
 for key in ("private.pem","public.pem","allowlist.json"):
  item=root/key; item.write_text("fixture"); item.chmod(0o600)
 runtime=root/"runtime.env"; runtime.write_text("\n".join(("CARR_CALENDAR_PREBRIEF_ENABLED=false","CARR_DB_JOBS_URL="+values["CARR_DB_JOBS_URL"],"CARR_CALENDAR_PREBRIEF_CHILD_PROFILE="+str(child),"CARR_CALENDAR_PREBRIEF_COLLECTOR_PUBLIC_KEY="+str(root/"public.pem"),"CARR_CALENDAR_PREBRIEF_ALLOWLIST="+str(root/"allowlist.json"),"CARR_CALENDAR_PREBRIEF_COLLECTOR_PRIVATE_KEY="+str(root/"private.pem"),"CARR_CALENDAR_PREBRIEF_COLLECTOR_VERSION=fixture","CARR_CALENDAR_PREBRIEF_EVENTKIT_APP="+str(app)))+"\n"); runtime.chmod(0o600)
 plist=root/"com.carr.calendar-prebrief-joe.plist"; plist.write_text("com.carr.calendar-prebrief-joe calendar-prebrief-joe-runtime.py")
 class ActivationCur:
  def __init__(self,user): self.query=""; self.user=user
  def execute(self,q,*_): self.query=q
  def fetchone(self):
   if "session_user,current_user" in self.query: return (self.user,self.user)
   if "pg_has_role" in self.query: return (True,)
   if "activate_calendar_prebrief" in self.query: return ("00000000-0000-4000-8000-000000000099",)
   return ({"sponsor":"joe","app_evidence_digest":"e"*64,"id":"00000000-0000-4000-8000-000000000099"},)
  def __enter__(self): return self
  def __exit__(self,*_): return False
 class ActivationConn:
  def __init__(self,user): self.user=user
  def cursor(self): return ActivationCur(self.user)
  def commit(self): pass
  def __enter__(self): return self
  def __exit__(self,*_): return False
 class Done: returncode=0
 launch_calls: list[list[str]] = []
 def launch(args,**_): launch_calls.append(args); return Done()
 def activation_connect(value): return ActivationConn(value.split("://",1)[1].split(":",1)[0])
 sealed=mod.seal_activate_joe_live("e"*64,joe, runtime, plist, activation_connect, launch, 501, root)
 check("sealed activation flips 0600 runtime only after typed receipt and launchd readback",sealed["sponsor"]=="joe" and "CARR_CALENDAR_PREBRIEF_ENABLED=true" in runtime.read_text() and [call[1] for call in launch_calls]==["bootout","bootstrap","kickstart","print"])
 installer=(ROOT/"bin/install-calendar-prebrief-joe.sh").read_text()
 check("installer builds and copies the app from the same checked-out repository",
       'CARR_REPO="$REPO" "$REPO/bin/build-calendar-access.sh"' in installer)
print("OK" if not bad else "FAIL "+", ".join(bad)); raise SystemExit(bool(bad))
