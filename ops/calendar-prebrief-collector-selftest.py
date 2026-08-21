#!/usr/bin/env python3
"""Subprocess proof for the signed EventKit collector with no live Calendar."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COLLECTOR = ROOT / "tools/calendar-prebrief-collector.py"
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
    subprocess.run(["openssl", "genpkey", "-algorithm", "ED25519", "-out", str(key)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    key.chmod(0o600)
    environment = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(fake), "CARR_CALENDAR_PREBRIEF_ALLOWLIST": str(allowlist), "CARR_CALENDAR_PREBRIEF_COLLECTOR_PRIVATE_KEY": str(key), "CARR_CALENDAR_PREBRIEF_COLLECTOR_VERSION": "fixture-1"}
    run = subprocess.run([sys.executable, str(COLLECTOR)], input=json.dumps(contract()), text=True, capture_output=True, env=environment, check=False)
    envelope = json.loads(run.stdout) if run.returncode == 0 else {}
    check("real collector subprocess signs DB-bound EventKit capture", bool(run.returncode == 0 and envelope.get("challenge_id") == contract()["challenge_id"] and envelope.get("raw_payload_count") == 1 and envelope.get("signature")))
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
