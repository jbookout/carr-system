#!/usr/bin/env python3
"""Hermetic signed-envelope adversarial tests using a disposable keypair."""
from __future__ import annotations
import base64, importlib.util, json, subprocess, tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; s=importlib.util.spec_from_file_location("e",ROOT/"tools/calendar-prebrief-envelope.py"); assert s and s.loader; e=importlib.util.module_from_spec(s); s.loader.exec_module(e)
bad=[]
def ok(n,v): print(("  ok " if v else "  FAIL ")+n); bad.extend([] if v else [n])
def refuses(fn):
 try:fn()
 except e.Refusal:return True
 return False
with tempfile.TemporaryDirectory() as d:
 d=Path(d); private=d/"private.pem"; public=d/"public.pem"; subprocess.run(["openssl","genpkey","-algorithm","ED25519","-out",str(private)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); subprocess.run(["openssl","pkey","-in",str(private),"-pubout","-out",str(public)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
 x={"sponsor":"joe","job_id":"j","lease":"l","scheduled_for":"2026-08-21T11:30:00Z","allowlist_revision":"r","observed_calendar_keys":["a"*64],"raw_capture_digest":"b"*64,"raw_capture_count":1,"collector_version":"v1","key_fingerprint":"fp","signature":""}; payload=e.canonical(x); sig=subprocess.run(["openssl","pkeyutl","-sign","-inkey",str(private),"-rawin","-in","/dev/stdin"],input=payload,capture_output=True,check=True).stdout; x["signature"]=base64.b64encode(sig).decode()
 ok("valid signed envelope verifies",e.verify(x,public,"fp",lambda _:False)["sponsor"]=="joe")
 badsig=dict(x); badsig["signature"]="AA=="; ok("bad signature refuses",refuses(lambda:e.verify(badsig,public,"fp",lambda _:False)))
 wrong=dict(x); wrong["key_fingerprint"]="other"; ok("wrong key fingerprint refuses",refuses(lambda:e.verify(wrong,public,"fp",lambda _:False)))
 old=dict(x); old["collector_version"]=""; ok("bad collector version refuses",refuses(lambda:e.verify(old,public,"fp",lambda _:False)))
 ok("replay refuses",refuses(lambda:e.verify(x,public,"fp",lambda _:True)))
print("OK" if not bad else "FAIL "+", ".join(bad));raise SystemExit(bool(bad))
