#!/usr/bin/env python3
"""Subprocess proof for the signed EventKit collector with no live Calendar."""
from __future__ import annotations

import base64
import hashlib
import json
import importlib.util
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib.machine_prerequisites import require_openssl_ed25519_or_exit

OPENSSL = require_openssl_ed25519_or_exit()
COLLECTOR = ROOT / "tools/calendar-prebrief-collector.py"
spec = importlib.util.spec_from_file_location("calendar_prebrief_collector", COLLECTOR)
assert spec and spec.loader
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)
bad: list[str] = []


def check(name: str, value: bool) -> None:
    print(("  ok " if value else "  FAIL ") + name)
    if not value:
        bad.append(name)


def contract() -> dict[str, object]:
    return {"challenge_id": "00000000-0000-4000-8000-000000000003", "sponsor": "joe", "job_id": "00000000-0000-4000-8000-000000000001", "attempt": 1, "lease_token": "00000000-0000-4000-8000-000000000002", "scheduled_for": "2026-08-20T06:30:00Z", "window_starts_at": "2026-08-13T06:30:00Z", "window_ends_at": "2026-10-04T06:30:00Z", "mode": "live", "destination": "live", "allowlist_revision_id": "00000000-0000-4000-8000-000000000004", "allowlist_digest": "d" * 64, "calendar_keys": ["f491ebbaf3343e0567f64d1f04a34fab8d4a145936a2ba3a2df0577680288b36"]}


with tempfile.TemporaryDirectory() as raw:
    root = Path(raw)
    fake = root / "fake"
    fake.mkdir()
    # The fake bundle implements the EventKit methods the real collector calls;
    # its attendee string must reach only the collector stdout pipe.
    (fake / "EventKit.py").write_text('''
class URL:
 def resourceSpecifier(self): return "mailto:raw.attendee@example.test"
class Attendee:
 def URL(self): return URL()
class Calendar:
 def calendarIdentifier(self): return "calendar-joe"
class Event:
 def calendar(self): return Calendar()
 def eventIdentifier(self): return "event-1"
 def startDate(self): from datetime import datetime,timezone; return datetime(2026,8,20,8,tzinfo=timezone.utc)
 def endDate(self): from datetime import datetime,timezone; return datetime(2026,8,20,9,tzinfo=timezone.utc)
 def title(self): return "Meeting"
 def location(self): return None
 def attendees(self): return [Attendee()]
 def organizer(self): return None
class Store:
 def requestFullAccessToEventsWithCompletion_(self, done): done(True,None)
 def calendarsForEntityType_(self, _): return [Calendar()]
 def predicateForEventsWithStartDate_endDate_calendars_(self,*args): return args
 def eventsMatchingPredicate_(self,_): return [Event()]
class EKEventStore:
 @classmethod
 def alloc(cls): return cls()
 def init(self): return Store()
''')
    (fake / "Foundation.py").write_text("class NSDate:\n @staticmethod\n def dateWithTimeIntervalSince1970_(value): return value\n")
    allowlist = root / "joe.json"
    allowlist.write_text('{"version":1,"calendars":[{"identifier":"calendar-joe","sponsor":"joe"}]}')
    allowlist.chmod(0o600)
    key = root / "collector.pem"
    subprocess.run([OPENSSL, "genpkey", "-algorithm", "ED25519", "-out", str(key)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    key.chmod(0o600)
    real_run = subprocess.run

    def delayed_signer(command, **kwargs):
        if "pkeyutl" not in command:
            return real_run(command, **kwargs)
        payload = kwargs.pop("input")
        kwargs.pop("check")
        timeout = kwargs.pop("timeout")
        with subprocess.Popen(command, stdin=subprocess.PIPE if payload is not None else subprocess.DEVNULL, **kwargs) as process:
            # Force OpenSSL to inspect its input before communicate writes.
            time.sleep(0.1)
            stdout, stderr = process.communicate(payload, timeout=timeout)
            return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)

    with patch.object(subprocess, "run", side_effect=delayed_signer):
        try:
            delayed_signature = collector.sign(key, b"synthetic delayed signing input")
        except collector.Refusal:
            delayed_signature = ""
    check("Ed25519 signing is independent of stdin scheduling", bool(delayed_signature))
    environment = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(fake), "CARR_CALENDAR_PREBRIEF_ALLOWLIST": str(allowlist), "CARR_CALENDAR_PREBRIEF_COLLECTOR_PRIVATE_KEY": str(key), "CARR_CALENDAR_PREBRIEF_COLLECTOR_VERSION": "fixture-1"}
    run = subprocess.run([sys.executable, str(COLLECTOR)], input=json.dumps(contract()), text=True, capture_output=True, env=environment, check=False)
    envelope = json.loads(run.stdout) if run.returncode == 0 else {}
    check("real collector subprocess signs DB-bound EventKit capture", bool(run.returncode == 0 and envelope.get("challenge_id") == contract()["challenge_id"] and envelope.get("raw_payload_count") == 1 and envelope.get("signature")))
    public = real_run([OPENSSL, "pkey", "-in", str(key), "-pubout"], capture_output=True, check=True).stdout
    public_file = root / "public.pem"
    public_file.write_bytes(public)
    verified = False
    if envelope:
        # Only synthetic fixture bytes reach these anonymous test files.
        with tempfile.TemporaryFile() as body, tempfile.TemporaryFile() as signature:
            body.write(collector.canonical({name: value for name, value in envelope.items() if name != "signature"}))
            signature.write(base64.b64decode(envelope["signature"], validate=True))
            body.flush(); body.seek(0)
            signature.flush(); signature.seek(0)
            proof = real_run([OPENSSL, "pkeyutl", "-verify", "-pubin", "-inkey", str(public_file), "-rawin", "-in", f"/dev/fd/{body.fileno()}", "-sigfile", f"/dev/fd/{signature.fileno()}"], capture_output=True, pass_fds=(body.fileno(), signature.fileno()), check=False)
            verified = proof.returncode == 0 and envelope["key_fingerprint"] == hashlib.sha256(public).hexdigest()
    check("signature and key fingerprint match independent OpenSSL", verified)
    check("raw attendee appears only in the allowed collector stdout pipe", "raw.attendee@example.test" not in run.stderr and "raw.attendee@example.test" in run.stdout)
    changed = contract(); changed["calendar_keys"] = ["a" * 64]
    mismatch = subprocess.run([sys.executable, str(COLLECTOR)], input=json.dumps(changed), text=True, capture_output=True, env=environment, check=False)
    check("DB/local allowlist mismatch refuses before raw output", mismatch.returncode == 78 and "raw.attendee@example.test" not in mismatch.stdout + mismatch.stderr)
    bad_window = contract(); bad_window["window_starts_at"] = "2026-08-12T06:30:00Z"
    window = subprocess.run([sys.executable, str(COLLECTOR)], input=json.dumps(bad_window), text=True, capture_output=True, env=environment, check=False)
    check("noncanonical DB scheduled window refuses before EventKit", window.returncode == 78 and "raw.attendee@example.test" not in window.stdout + window.stderr)
    key.chmod(0o644)
    insecure = subprocess.run([sys.executable, str(COLLECTOR)], input=json.dumps(contract()), text=True, capture_output=True, env=environment, check=False)
    check("insecure private key refuses before raw output", insecure.returncode == 78 and "raw.attendee@example.test" not in insecure.stdout + insecure.stderr)

print("OK" if not bad else "FAIL " + ", ".join(bad))
raise SystemExit(bool(bad))
