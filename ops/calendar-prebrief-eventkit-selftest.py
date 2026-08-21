#!/usr/bin/env python3
"""Hermetic contract tests for the bounded EventKit prebrief projection."""
from __future__ import annotations

import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "tools" / "calendar-prebrief-eventkit.py"
spec = importlib.util.spec_from_file_location("calendar_prebrief_eventkit", SCRIPT)
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

failed: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print(("  ok   " if condition else "  FAIL ") + name + (f" {detail}" if detail else ""))
    if not condition:
        failed.append(name)


def _refuses(fn):
    try:
        fn()
    except mod.Refusal:
        return True
    return False


class FakeURL:
    def __init__(self, value): self.value = value
    def resourceSpecifier(self): return self.value


class FakeAttendee:
    def __init__(self, email): self.email = email
    def URL(self): return FakeURL(f"mailto:{self.email}")


class FakeCalendar:
    def __init__(self, identifier): self.identifier = identifier
    def calendarIdentifier(self): return self.identifier


class FakeEvent:
    def __init__(self, calendar, identifier, start, end, title, location, attendees):
        self._calendar, self.identifier = calendar, identifier
        self.start, self.end, self._title, self._location = start, end, title, location
        self._attendees = attendees
        self.notes = "do not leak fixture notes"
        self.URL = "https://do-not-leak.invalid"
        self.recurrenceRules = "do not leak recurrence"
    def calendar(self): return self._calendar
    def eventIdentifier(self): return self.identifier
    def startDate(self): return self.start
    def endDate(self): return self.end
    def title(self): return self._title
    def location(self): return self._location
    def attendees(self): return [FakeAttendee(x) for x in self._attendees]


class FakeStore:
    def __init__(self, calendars, events, allowed=True):
        self.calendars, self.events, self.allowed = calendars, events, allowed
        self.predicate_calendars = None
        self.predicate_start = self.predicate_end = None
    def requestFullAccessToEventsWithCompletion_(self, callback): callback(self.allowed, None)
    def calendarsForEntityType_(self, _): return self.calendars
    def predicateForEventsWithStartDate_endDate_calendars_(self, start, end, calendars):
        self.predicate_start, self.predicate_end, self.predicate_calendars = start, end, calendars
        return "predicate"
    def eventsMatchingPredicate_(self, _): return self.events


def write_json(path: Path, obj: object, mode: int = 0o600) -> Path:
    path.write_text(json.dumps(obj))
    path.chmod(mode)
    return path


print("calendar prebrief EventKit projection")
with tempfile.TemporaryDirectory() as raw:
    root = Path(raw)
    config = write_json(root / "allow.json", {"version": 1, "calendars": [
        {"identifier": "cal-joe", "sponsor": "joe"},
        {"identifier": "cal-dell", "sponsor": "dell"},
    ]})
    check("mixed-sponsor config refuses a Joe capture", _refuses(lambda: mod.load_allowlist(config, "joe")))
    joe_config = write_json(root / "joe.json", {"version": 1, "calendars": [
        {"identifier": "cal-joe", "sponsor": "joe"},
    ]})
    cfg = mod.load_allowlist(joe_config, "joe")
    resolver_data = {"client@example.com": "C-100",
                     "multiple@example.com": ["C-1", "L-2"]}
    resolve = mod.load_resolver_map_data(resolver_data)
    joe, dell, private = FakeCalendar("cal-joe"), FakeCalendar("cal-dell"), FakeCalendar("private")
    start = datetime(2026, 8, 20, 15, 0, tzinfo=timezone.utc)
    events = [FakeEvent(joe, "event-1", start, start + timedelta(hours=1),
                        "  Client   Meeting ", "  Room 5 ",
                        ["client@example.com", "joe@carr.us"])]
    store = FakeStore([joe, dell, private], events)
    snapshot = mod.capture_snapshot(store, cfg, resolve, now=datetime(2026, 8, 20, tzinfo=timezone.utc))
    check("permission denial refuses", _refuses(lambda: mod.capture_snapshot(
        FakeStore([joe, dell], events, allowed=False), cfg, resolve)))
    check("predicate gets exactly one sponsor's allowlisted calendars", store.predicate_calendars == [joe], repr(store.predicate_calendars))
    check("predicate never receives all calendars", store.predicate_calendars is not None)
    check("window is fixed -7/+45 days", store.predicate_start == datetime(2026, 8, 13, tzinfo=timezone.utc)
          and store.predicate_end == datetime(2026, 10, 4, tzinfo=timezone.utc),
          f"{store.predicate_start!r} {store.predicate_end!r}")
    entry = snapshot["events"][0]
    check("observed calendars include only the selected sponsor coverage",
          snapshot["observed_calendars"] == [{"sponsor": "joe", "calendar_key": mod.opaque_key("calendar", "cal-joe")}])
    check("internal carr.us attendee excluded", entry["participant_refs"] == ["C-100"], repr(entry))
    check("opaque keys stable", entry["calendar_key"] == mod.opaque_key("calendar", "cal-joe")
          and entry["event_key"] == mod.opaque_key("event", "cal-joe", "event-1")
          and entry["occurrence_key"] == mod.opaque_key("occurrence", "cal-joe", "event-1", "2026-08-20T15:00:00Z"))
    rendered = json.dumps(snapshot, sort_keys=True)
    check("prohibited fixture strings absent", not any(x in rendered for x in (
        "client@example.com", "cal-joe", "event-1", "do not leak fixture notes",
        "do-not-leak.invalid", "do not leak recurrence")), rendered)
    check("unresolved attendee refuses", _refuses(lambda: mod.capture_snapshot(
        FakeStore([joe, dell], [FakeEvent(joe, "x", start, start + timedelta(hours=1),
                                           "x", "", ["unknown@example.com"])]), cfg, resolve)))
    check("ambiguous attendee refuses", _refuses(lambda: mod.capture_snapshot(
        FakeStore([joe, dell], [FakeEvent(joe, "x", start, start + timedelta(hours=1),
                                           "x", "", ["multiple@example.com"])]), cfg, resolve)))
    check("configured calendar absence refuses", _refuses(lambda: mod.capture_snapshot(FakeStore([], events), cfg, resolve)))
    check("unexpected calendar event refuses", _refuses(lambda: mod.capture_snapshot(
        FakeStore([joe, dell, private], [FakeEvent(private, "private-event", start,
                                                   start + timedelta(hours=1), "Private", "", [])]), cfg, resolve)))
    empty = write_json(root / "empty.json", {"version": 1, "calendars": []})
    check("no allowlisted calendars refuses", _refuses(lambda: mod.load_allowlist(empty)))
    malformed = root / "malformed.json"
    malformed.write_text("{")
    malformed.chmod(0o600)
    check("malformed config refuses", _refuses(lambda: mod.load_allowlist(malformed)))
    bad_mode = write_json(root / "bad-mode.json", {"version": 1, "calendars": []}, 0o644)
    check("unsafe allowlist mode refuses", _refuses(lambda: mod.load_allowlist(bad_mode)))
    dup = write_json(root / "dup.json", {"version": 1, "calendars": [
        {"identifier": "cal-joe", "sponsor": "joe"},
        {"identifier": "cal-joe", "sponsor": "dell"}]})
    check("duplicate raw calendar identifiers refuse", _refuses(lambda: mod.load_allowlist(dup)))
    link = root / "link.json"
    link.symlink_to(config)
    check("symlinked config refuses", _refuses(lambda: mod.load_allowlist(link)))
    resolver_file = write_json(root / "resolver.json", resolver_data)
    with resolver_file.open("r") as raw_stdin:
        regular_input = subprocess.run(
            [sys.executable, str(SCRIPT), "--allowlist", str(joe_config), "--sponsor", "joe",
             "--resolver-map-stdin"], stdin=raw_stdin, text=True, capture_output=True)
    check("durable resolver-map file refuses", regular_input.returncode == 78
          and "process pipe" in regular_input.stderr)

print("OK all checks passed" if not failed else "FAIL " + ", ".join(failed))
raise SystemExit(bool(failed))
