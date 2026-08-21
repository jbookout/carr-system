#!/usr/bin/env python3
"""Hermetic acquisition-to-ingest contract tests; no EventKit or DB is opened."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


capture = load("calendar_prebrief_eventkit", ROOT / "tools/calendar-prebrief-eventkit.py")
bridge = load("calendar_prebrief_ingest", ROOT / "tools/calendar-prebrief-ingest.py")
manifest = json.loads((ROOT / "ops/config/control-plane-workflows.v1.json").read_text())
failed: list[str] = []


def check(label: str, value: bool) -> None:
    print(f"  {'ok  ' if value else 'FAIL'} {label}")
    if not value:
        failed.append(label)


def refuses(fn) -> bool:
    try:
        fn()
    except bridge.Refusal:
        return True
    return False


class Calendar:
    def __init__(self, identifier): self.identifier = identifier
    def calendarIdentifier(self): return self.identifier


class URL:
    def __init__(self, value): self.value = value
    def resourceSpecifier(self): return self.value


class Attendee:
    def __init__(self, value): self.value = value
    def URL(self): return URL("mailto:" + self.value)


class Event:
    def __init__(self, cal, start): self.cal, self.start = cal, start
    def calendar(self): return self.cal
    def eventIdentifier(self): return "event-source-id"
    def startDate(self): return self.start
    def endDate(self): return self.start + timedelta(hours=1)
    def title(self): return "Client meeting"
    def location(self): return "Office"
    def attendees(self): return [Attendee("client@example.com")]


class Store:
    def __init__(self, calendars, events): self.calendars, self.events = calendars, events
    def requestFullAccessToEventsWithCompletion_(self, fn): fn(True, None)
    def calendarsForEntityType_(self, _): return self.calendars
    def predicateForEventsWithStartDate_endDate_calendars_(self, *_): return "bounded"
    def eventsMatchingPredicate_(self, _): return self.events


joe, dell = Calendar("raw-joe-id"), Calendar("raw-dell-id")
as_of = datetime(2026, 8, 20, tzinfo=timezone.utc)
snapshot = capture.capture_snapshot(Store([joe, dell], [Event(joe, as_of + timedelta(hours=1))]), [
    {"identifier": "raw-joe-id", "sponsor": "joe"},
    {"identifier": "raw-dell-id", "sponsor": "dell"},
], capture.load_resolver_map_data({"client@example.com": "C-100"}), now=as_of)

calls: list[tuple[str, tuple[object, ...]]] = []


class Cursor:
    def execute(self, sql, params=None): calls.append((sql, tuple(params or ()))); self.sql = sql
    def fetchone(self):
        if "session_user" in self.sql: return ("carr_calendar_prebrief_joe", "carr_calendar_prebrief_joe")
        return ({"id": "receipt-1"},)
    def __enter__(self): return self
    def __exit__(self, *_): return False


class Conn:
    def cursor(self): return Cursor()
    def commit(self): calls.append(("commit", ()))
    def __enter__(self): return self
    def __exit__(self, *_): return False


result = bridge.ingest(sponsor="joe", job_id="job", lease="lease", snapshot=snapshot,
                       environ={"CARR_DB_CALENDAR_PREBRIEF_JOE_URL": "postgresql://carr_calendar_prebrief_joe:fixture@db/carr"},  # ci-secret-scan: allow
                       connector=lambda _dsn: Conn())
rendered = json.dumps(snapshot, sort_keys=True)
call = next(params for sql, params in calls if "ingest_calendar_prebrief_projection" in sql)
check("fake EventKit snapshot includes empty Dell calendar coverage", len(snapshot["observed_calendars"]) == 2)
events_arg = call[3]
check("bridge strips sponsor before DB payload", result["sponsor"] == "joe"
      and isinstance(events_arg, list)
      and all(isinstance(event, dict) and "sponsor" not in event for event in events_arg))
check("bridge uses exact function call shape", len(call) == 4 and call[2] == [capture.opaque_key("calendar", "raw-joe-id")])
check("raw EventKit email and identifiers never reach snapshot", all(token not in rendered for token in ("client@example.com", "raw-joe-id", "event-source-id")))
cross = json.loads(json.dumps(snapshot)); cross["events"][0]["sponsor"] = "dell"
check("cross-sponsor calendar topology refuses", refuses(lambda: bridge.normalize_snapshot(cross, "joe")))
malformed = json.loads(json.dumps(snapshot)); malformed["events"][0]["description"] = "secret"
check("prohibited event field refuses", refuses(lambda: bridge.normalize_snapshot(malformed, "joe")))
check("DSN login mismatch refuses", refuses(lambda: bridge.ingest(sponsor="joe", job_id="j", lease="l", snapshot=snapshot,
      environ={"CARR_DB_CALENDAR_PREBRIEF_JOE_URL": "postgresql://carr_jobs:fixture@db/carr"},  # ci-secret-scan: allow
      connector=lambda _dsn: Conn())))
owners = {row["key"]: row["inventory"]["owner"] for row in manifest["workflows"] if row["key"].startswith("calendar-prebrief-projection-")}
check("manifest sync preserves sponsor owners", owners == {"calendar-prebrief-projection-joe-daily": "joe", "calendar-prebrief-projection-dell-daily": "dell"})

with tempfile.TemporaryDirectory() as raw:
    file = Path(raw) / "snapshot.json"; file.write_text(json.dumps(snapshot))
    with file.open() as stdin:
        run = subprocess.run([str(ROOT / ".venv/bin/python"), str(ROOT / "tools/calendar-prebrief-ingest.py"), "--sponsor", "joe", "--job-id", "j", "--lease", "l"], stdin=stdin, text=True, capture_output=True)
    check("regular-file snapshot stdin refuses", run.returncode == 78 and "process pipe" in run.stderr)

print("OK all checks passed" if not failed else "FAIL " + ", ".join(failed))
raise SystemExit(bool(failed))
