#!/usr/bin/env python3
"""Validate scoped prebrief credentials and register opaque EventKit allowlists.

This is an explicit local installation utility. It never provisions roles,
enables a scheduler, or prints a DSN, calendar identifier, or attendee address.
"""
from __future__ import annotations
import argparse, hashlib, importlib.util, json, os, stat
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import parse_qsl, unquote, urlsplit

REPO=Path(__file__).resolve().parent.parent
SPECS={
 "CARR_DB_AUTHORITY_JOE_URL":"carr_authority_joe", "CARR_DB_AUTHORITY_DELL_URL":"carr_authority_dell",
 "CARR_DB_CALENDAR_PREBRIEF_ATTESTOR_JOE_URL":"carr_calendar_prebrief_attestor_joe",
 "CARR_DB_CALENDAR_PREBRIEF_ATTESTOR_DELL_URL":"carr_calendar_prebrief_attestor_dell",
 "CARR_DB_CALENDAR_PREBRIEF_RESOLVER_JOE_URL":"carr_calendar_prebrief_resolver_joe",
 "CARR_DB_CALENDAR_PREBRIEF_RESOLVER_DELL_URL":"carr_calendar_prebrief_resolver_dell",
 "CARR_DB_CALENDAR_PREBRIEF_JOE_URL":"carr_calendar_prebrief_joe",
 "CARR_DB_CALENDAR_PREBRIEF_DELL_URL":"carr_calendar_prebrief_dell", "CARR_DB_JOBS_URL":"carr_jobs"}
BROAD={"DATABASE_URL","CARR_DB_WRITER_URL","CARR_DB_OWNER_URL","CARR_DB_READER_URL","CARR_DB_EXPORTER_URL","CARR_DB_BACKUP_URL","CARR_DB_DEVICE_URL","CARR_DB_AUTHORITY_URL"}
CAPABILITY={"CARR_DB_AUTHORITY_JOE_URL":"carr_authority","CARR_DB_AUTHORITY_DELL_URL":"carr_authority",
 "CARR_DB_CALENDAR_PREBRIEF_ATTESTOR_JOE_URL":"carr_calendar_prebrief_attestors",
 "CARR_DB_CALENDAR_PREBRIEF_ATTESTOR_DELL_URL":"carr_calendar_prebrief_attestors",
 "CARR_DB_CALENDAR_PREBRIEF_RESOLVER_JOE_URL":"carr_calendar_prebrief_email_resolver",
 "CARR_DB_CALENDAR_PREBRIEF_RESOLVER_DELL_URL":"carr_calendar_prebrief_email_resolver",
 "CARR_DB_CALENDAR_PREBRIEF_JOE_URL":"carr_calendar_prebrief_jobs","CARR_DB_CALENDAR_PREBRIEF_DELL_URL":"carr_calendar_prebrief_jobs",
 "CARR_DB_JOBS_URL":"carr_jobs"}
class Refusal(RuntimeError): pass

def strict_uri(value:str, user:str)->bool:
 try:
  p=urlsplit(value.strip()); items=parse_qsl(p.query,keep_blank_values=True,strict_parsing=True); port=p.port
 except (TypeError,ValueError): return False
 q=dict(items)
 return bool(p.scheme in {"postgres","postgresql"} and unquote(p.username or "")==user and p.password not in (None,"") and p.hostname and p.path not in ("","/") and p.fragment=="" and (port is None or 1<=port<=65535) and len(items)==len(q) and q.get("sslmode")=="verify-full" and isinstance(q.get("sslrootcert"),str) and q["sslrootcert"].startswith("/") and set(q) in ({"sslmode","sslrootcert"},{"sslmode","sslrootcert","channel_binding"}) and ("channel_binding" not in q or q["channel_binding"]=="require"))

def load_scoped_env(path:Path,environ:Mapping[str,str]|None=None)->dict[str,str]:
 env=os.environ if environ is None else environ
 if any(env.get(k) for k in BROAD) or any(env.get(k) for k in env if k.startswith("PG")): raise Refusal("ambient database credential input is forbidden")
 try: st=path.lstat()
 except OSError as e: raise Refusal("scoped env file is missing") from e
 if not stat.S_ISREG(st.st_mode) or stat.S_ISLNK(st.st_mode) or stat.S_IMODE(st.st_mode)!=0o600: raise Refusal("scoped env file must be a 0600 regular non-symlink")
 out={}
 try: lines=path.read_text(encoding="utf-8").splitlines()
 except OSError as e: raise Refusal("scoped env file is unreadable") from e
 for line in lines:
  line=line.strip()
  if not line or line.startswith("#"): continue
  key,sep,value=line.partition("=")
  if not sep or key not in SPECS or key in out: raise Refusal("scoped env file has an unknown or duplicate key")
  value=value.strip()
  if len(value)>=2 and value[0]==value[-1] and value[0] in "\"'": value=value[1:-1]
  if not strict_uri(value,SPECS[key]): raise Refusal(f"{key} has an unsafe identity or URI shape")
  out[key]=value
 if set(out)!=set(SPECS): raise Refusal("scoped env file must contain exactly the required identities")
 return out

def probe_scoped_identity(key:str, dsn:str, connect:Callable[[str],Any])->bool:
 """Read-only exact login/current-role/capability proof; values never escape."""
 if key not in SPECS or not strict_uri(dsn,SPECS[key]): raise Refusal("scoped credential shape refused")
 try:
  with connect(dsn) as conn, conn.cursor() as cur:
   cur.execute("begin transaction read only")
   cur.execute("select session_user,current_user"); identity=cur.fetchone()
   if tuple(identity or ()) != (SPECS[key],SPECS[key]): raise Refusal("scoped database identity mismatch")
   for role in CAPABILITY[key].split(","):
    cur.execute("select pg_has_role(current_user,%s,'member')",(role,)); row=cur.fetchone()
    if tuple(row or ()) != (True,): raise Refusal("scoped database capability membership mismatch")
 except Refusal: raise
 except Exception as e: raise Refusal("scoped database identity probe failed") from e
 return True

def preflight(env:Mapping[str,str],connect:Callable[[str],Any])->dict[str,Any]:
 """Prove every supplied scoped credential before declaring activation ready."""
 for key in sorted(SPECS): probe_scoped_identity(key,env[key],connect)
 return {"ok":True,"identities":sorted(SPECS)}

def _eventkit():
 spec=importlib.util.spec_from_file_location("calendar_prebrief_eventkit",REPO/"tools/calendar-prebrief-eventkit.py"); assert spec and spec.loader
 m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m

def register_allowlist(sponsor:str,allowlist:Path,env:Mapping[str,str],connect:Callable[[str],Any]|None=None)->dict[str,Any]:
 if sponsor not in {"joe","dell"}: raise Refusal("sponsor must be joe or dell")
 eventkit=_eventkit(); entries=eventkit.load_allowlist(allowlist)
 if not entries or any(entry["sponsor"]!=sponsor for entry in entries): raise Refusal("allowlist must contain only its named sponsor calendars")
 keys=sorted(eventkit.opaque_key("calendar",entry["identifier"]) for entry in entries)
 name=f"CARR_DB_AUTHORITY_{sponsor.upper()}_URL"; expected=f"carr_authority_{sponsor}"; dsn=env.get(name,"")
 if not strict_uri(dsn,expected): raise Refusal("authority credential does not match sponsor")
 if connect is None:
  try: import psycopg
  except ImportError as e: raise Refusal("psycopg is required") from e
  connect=psycopg.connect
 with connect(dsn) as conn, conn.cursor() as cur:
  cur.execute("select session_user,current_user"); identity=cur.fetchone()
  if tuple(identity or ())!=(expected,expected): raise Refusal("authority session identity mismatch")
  cur.execute("select row_to_json(ops.replace_calendar_prebrief_allowlist(%s))",(keys,)); row=cur.fetchone()
  if not row or not isinstance(row[0],dict) or row[0].get("sponsor")!=sponsor: raise Refusal("allowlist receipt mismatch")
  conn.commit(); receipt=row[0]
 digest=receipt.get("configuration_digest"); revision=receipt.get("active_revision_id",receipt.get("id"))
 if not isinstance(digest,str) or len(digest)!=64 or not isinstance(revision,str): raise Refusal("allowlist receipt shape mismatch")
 return {"sponsor":sponsor,"calendar_count":len(keys),"configuration_digest":digest,"revision":revision}

def main()->int:
 p=argparse.ArgumentParser(description=__doc__); p.add_argument("--env-file",type=Path,required=True); sub=p.add_subparsers(dest="cmd",required=True); sub.add_parser("preflight"); r=sub.add_parser("register-allowlist"); r.add_argument("--sponsor",required=True,choices=["joe","dell"]); r.add_argument("--allowlist",required=True,type=Path); a=p.parse_args()
 try:
  env=load_scoped_env(a.env_file,{k:v for k,v in os.environ.items() if k in BROAD or k.startswith("PG")})
  if a.cmd=="preflight":
   try: import psycopg
   except ImportError as e: raise Refusal("psycopg is required") from e
   out=preflight(env,psycopg.connect)
  else: out=register_allowlist(a.sponsor,a.allowlist,env)
  print(json.dumps(out,sort_keys=True)); return 0
 except Refusal as e: print(f"calendar prebrief activation: REFUSE {e}",file=__import__('sys').stderr); return 78
if __name__=="__main__": raise SystemExit(main())
