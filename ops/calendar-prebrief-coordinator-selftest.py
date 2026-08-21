#!/usr/bin/env python3
"""Hermetic subprocess proof of the calendar-prebrief credential boundary."""
from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools/calendar-prebrief-coordinator.py"
spec = importlib.util.spec_from_file_location("coordinator", SCRIPT)
assert spec and spec.loader
coordinator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(coordinator)
bad: list[str] = []


def check(name: str, value: bool) -> None:
    print(("  ok " if value else "  FAIL ") + name)
    if not value:
        bad.append(name)


def run(args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run([sys.executable, str(SCRIPT), *args], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)


def write_capture(path: Path, envelope: dict[str, object]) -> None:
    path.write_text("import json\nprint(json.dumps(" + repr(envelope) + "))\n", encoding="utf-8")
    path.chmod(0o700)


with tempfile.TemporaryDirectory() as raw:
    root = Path(raw)
    private, public, message, signature = (root / "private.pem", root / "public.pem", root / "message.json", root / "signature.bin")
    subprocess.run(["openssl", "genpkey", "-algorithm", "ED25519", "-out", str(private)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["openssl", "pkey", "-in", str(private), "-pubout", "-out", str(public)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    key = "a" * 64
    payload = {"version": 1, "window": {"starts_at": "2026-08-13T00:00:00Z", "ends_at": "2026-10-04T00:00:00Z"}, "observed_calendars": [{"sponsor": "joe", "calendar_key": key}], "events": [{"sponsor": "joe", "calendar_key": key, "event_key": "b" * 64, "occurrence_key": "c" * 64, "starts_at": "2026-08-20T01:00:00Z", "ends_at": "2026-08-20T02:00:00Z", "title": "Meeting: Dr Smith", "location": None, "attendee_emails": ["raw.attendee@example.test"]}]}
    message.write_bytes(coordinator._canonical(payload))
    subprocess.run(["openssl", "pkeyutl", "-sign", "-inkey", str(private), "-rawin", "-in", str(message), "-out", str(signature)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    envelope = {"payload": payload, "signature": base64.b64encode(signature.read_bytes()).decode("ascii"), "collector_key_fingerprint": hashlib.sha256(public.read_bytes()).hexdigest(), "collector_version": "fixture-1"}
    capture = root / "capture.py"
    write_capture(capture, envelope)
    claim = root / "claim.py"
    claim.write_text("import json\nprint(json.dumps({'job_id':'00000000-0000-0000-0000-000000000001','lease':'00000000-0000-0000-0000-000000000002','scheduled_for':'2026-08-20T01:00:00Z'}))\n", encoding="utf-8")
    claim.chmod(0o700)
    fake = root / "fake"
    (fake / "psycopg" / "types").mkdir(parents=True)
    (fake / "psycopg" / "types" / "__init__.py").write_text("", encoding="utf-8")
    (fake / "psycopg" / "types" / "json.py").write_text("class Jsonb:\n def __init__(self,value): self.value=value\n", encoding="utf-8")
    (fake / "psycopg" / "__init__.py").write_text("\n".join((
        "from urllib.parse import urlsplit,unquote", "order=[]", "class Cursor:",
        " def __init__(self,user): self.user=user; self.query=''", " def execute(self,query,args=None): self.query=query",
        " def fetchone(self):", "  if 'session_user,current_user' in self.query: return (self.user,self.user)",
        "  if 'resolve_calendar_prebrief_email_ref' in self.query: order.append('resolver'); return ('C-1',)",
        "  if 'record_calendar_prebrief_verified_envelope' in self.query:", "   if order != ['resolver']: return None", "   order.append('attestor'); return ('00000000-0000-0000-0000-000000000003',)",
        "  if 'ingest_calendar_prebrief_' in self.query:", "   if order != ['resolver','attestor']: return None", "   order.append('ingest'); return ('00000000-0000-0000-0000-000000000004',)",
        "  return None", " def __enter__(self): return self", " def __exit__(self,*_): return False", "class Conn:",
        " def __init__(self,dsn): self.user=unquote(urlsplit(dsn).username or '')", " def cursor(self): return Cursor(self.user)",
        " def commit(self): pass", " def __enter__(self): return self", " def __exit__(self,*_): return False", "def connect(dsn): return Conn(dsn)", "")), encoding="utf-8")
    profile = root / "child.env"

    def dsn(user: str) -> str:
        return f"postgresql://{user}:fixture@db.example/carr"

    def write_profile(attestor: str = "carr_calendar_prebrief_attestor_joe") -> None:
        profile.write_text("\n".join((f"CARR_DB_CALENDAR_PREBRIEF_ATTESTOR_JOE_URL={dsn(attestor)}", f"CARR_DB_CALENDAR_PREBRIEF_RESOLVER_JOE_URL={dsn('carr_calendar_prebrief_resolver_joe')}", f"CARR_DB_CALENDAR_PREBRIEF_JOE_URL={dsn('carr_calendar_prebrief_joe')}")) + "\n", encoding="utf-8")
        profile.chmod(0o600)

    write_profile()
    environment = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(fake), "CARR_DB_JOBS_URL": dsn("carr_jobs"), "CARR_CALENDAR_PREBRIEF_CLAIM_COMMAND": f"{sys.executable} {claim}", "CARR_CALENDAR_PREBRIEF_CHILD_PROFILE": str(profile), "CARR_CALENDAR_PREBRIEF_COLLECTOR_PUBLIC_KEY": str(public), "CARR_CALENDAR_PREBRIEF_CAPTURE_COMMAND": f"{sys.executable} {capture}"}
    live = run(["--sponsor", "joe", "--mode", "live"], environment)
    check("signed live subprocess reaches attestor then live ingest", live.returncode == 0 and json.loads(live.stdout).get("mode") == "live" and b"raw.attendee@example.test" not in live.stdout + live.stderr)
    canary = run(["--sponsor", "joe", "--mode", "canary"], environment)
    check("signed canary subprocess stays isolated", canary.returncode == 0 and json.loads(canary.stdout).get("mode") == "canary")
    ambient = dict(environment)
    ambient["CARR_DB_WRITER_URL"] = dsn("carr_writer")
    check("jobs parent rejects any ambient non-jobs database credential", run(["--sponsor", "joe", "--mode", "live"], ambient).returncode == 78)
    altered = dict(envelope)
    altered["signature"] = "AA=="
    write_capture(capture, altered)
    check("signature alteration refuses before database writes", run(["--sponsor", "joe", "--mode", "live"], environment).returncode == 78)
    write_capture(capture, envelope)
    write_profile("carr_jobs")
    check("child rejects a misplaced jobs credential", run(["--sponsor", "joe", "--mode", "live"], environment).returncode == 78)
    write_profile()
    foreign = json.loads(json.dumps(payload))
    foreign["events"][0]["sponsor"] = "dell"
    resolved: list[str] = []
    try:
        coordinator._snapshot_from_raw(foreign, "joe", resolved.append)
        crossed = False
    except coordinator.Refusal:
        crossed = not resolved
    check("foreign sponsor source refuses before Joe resolver sees raw attendee", crossed)
    claim.write_text("import json\nprint(json.dumps({'job_id':'only-one-field'}))\n", encoding="utf-8")
    check("parent rejects lost or incomplete lease before child", run(["--sponsor", "joe", "--mode", "live"], environment).returncode == 78)
    source = SCRIPT.read_text(encoding="utf-8")
    check("coordinator creates no raw source temporary file or argument", all(token not in source for token in ("NamedTemporaryFile", "mkstemp", "--snapshot", "--email")))

print("OK" if not bad else "FAIL " + ", ".join(bad))
raise SystemExit(bool(bad))
