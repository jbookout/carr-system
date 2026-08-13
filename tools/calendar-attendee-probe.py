#!/usr/bin/env python3
"""Probe the LOCAL Apple Calendar store for attendee coverage.

Answers one question: how many of Joe's calendar events actually carry attendee
email addresses? The baseline to beat is 6 of 81 — what the 2026-08-09 Last Touch
backfill scored against the published .ics feed, which Microsoft strips of
ATTENDEE and ORGANIZER properties by design.

Reads via EventKit, per the standing rule that mandates EventKit or icalBuddy and
forbids both the Google connector and AppleScript against Calendar.app (which hung
past two minutes on ~17 events). Read-only: opens nothing, writes nothing, and
prints derived counts plus a small sample rather than dumping calendar contents.
"""

import sys
import time
from datetime import datetime, timedelta

import objc
from EventKit import EKEventStore, EKEntityTypeEvent
from Foundation import NSDate

LOOKBACK_DAYS = 120


def request_access(store):
    """Ask for Calendars access, handling both the pre- and post-macOS-14 APIs."""
    granted = {"value": None, "error": None}

    def completion(ok, err):
        granted["value"] = bool(ok)
        granted["error"] = err

    if hasattr(store, "requestFullAccessToEventsWithCompletion_"):
        store.requestFullAccessToEventsWithCompletion_(completion)
    else:
        store.requestAccessToEntityType_completion_(EKEntityTypeEvent, completion)

    # The completion fires on another thread; give the prompt time to be answered.
    for _ in range(600):  # up to 60s
        if granted["value"] is not None:
            break
        time.sleep(0.1)
    return granted


def main():
    store = EKEventStore.alloc().init()
    granted = request_access(store)

    if granted["value"] is None:
        print("RESULT: no answer from the permission prompt within 60s.")
        print("        Nothing was read. Allow Calendars access and re-run.")
        return 2
    if not granted["value"]:
        print("RESULT: Calendars access DENIED.")
        print(f"        detail: {granted['error']}")
        print("        Nothing was read. This is a permission answer, not an empty store.")
        return 3

    calendars = store.calendarsForEntityType_(EKEntityTypeEvent)
    print(f"calendars visible: {len(calendars)}")
    for cal in calendars:
        print(f"  - {cal.title()}")

    end = NSDate.date()
    start = NSDate.dateWithTimeIntervalSinceNow_(-LOOKBACK_DAYS * 86400)
    predicate = store.predicateForEventsWithStartDate_endDate_calendars_(
        start, end, None
    )
    events = store.eventsMatchingPredicate_(predicate) or []

    total = len(events)
    with_attendees = 0
    attendee_emails = set()
    samples = []

    for ev in events:
        attendees = ev.attendees() or []
        emails = []
        for a in attendees:
            url = a.URL()
            if url and str(url).lower().startswith("mailto:"):
                emails.append(str(url)[7:].lower())
        if emails:
            with_attendees += 1
            attendee_emails.update(emails)
            if len(samples) < 5:
                samples.append((str(ev.title() or "(untitled)"), len(emails)))

    print()
    print(f"window: last {LOOKBACK_DAYS} days")
    print(f"events found:            {total}")
    print(f"events WITH attendees:   {with_attendees}")
    print(f"distinct attendee emails: {len(attendee_emails)}")
    if total:
        print(f"attendee coverage:       {with_attendees}/{total} "
              f"({100.0 * with_attendees / total:.0f}%)")
    print()
    print("BASELINE TO BEAT: 6 of 81 from the attendee-less published feed.")
    print()
    if samples:
        print("sample events carrying attendees (title, attendee count):")
        for title, n in samples:
            print(f"  - {title}  [{n}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
