#!/usr/bin/env python3
"""Prove calendar dry-run is pure and its shadow evidence contains no event data."""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import io
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib.loadpy import load_module_from_path

calendar = load_module_from_path('calendar_dry_run_under_test', str(ROOT / 'bin' / 'pull-gmail-calendar.py'))
setattr(calendar, 'FEEDS', {'joe': '/definitely/missing/joe.ics', 'dell': '/definitely/missing/dell.ics'})
setattr(calendar, 'say', lambda _log, _message: (_ for _ in ()).throw(AssertionError('dry-run attempted log append')))
rc = calendar.run_calendar(argparse.Namespace(dry_run=True, days_back=1, days_ahead=14,
                                               canary=False, recovery=True,
                                               recovery_root='/fixture-root', reason='test'))
if rc != 0:
    raise SystemExit('dry-run should succeed when feeds are absent')

today = dt.date.today().strftime('%Y%m%d')
with tempfile.TemporaryDirectory() as td:
    feed = Path(td) / 'calendar.ics'
    feed.write_text(
        'BEGIN:VCALENDAR\nBEGIN:VEVENT\n'
        'UID:external-id-should-never-print\n'
        f'DTSTART;VALUE=DATE:{today}\n'
        'SUMMARY:PRIVATE EVENT TITLE\n'
        'DESCRIPTION:https://meet.example/join?passcode=PRIVATE-PASSCODE attendee@example.com\n'
        'LOCATION:Private conference room\n'
        'ORGANIZER:mailto:organizer@example.com\n'
        'ATTENDEE:mailto:attendee@example.com\n'
        'END:VEVENT\nEND:VCALENDAR\n', encoding='utf-8')
    setattr(calendar, 'FEEDS', {'joe': str(feed)})
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        rc = calendar.run_calendar(argparse.Namespace(dry_run=True, days_back=1, days_ahead=1,
                                                       canary=False, recovery=True,
                                                       recovery_root=td, reason='test'))
    if rc != 0:
        raise SystemExit('dry-run should parse an in-window fixture without posting')
    stdout = output.getvalue()
    forbidden = ('PRIVATE EVENT TITLE', 'meet.example', 'PRIVATE-PASSCODE',
                 'attendee@example.com', 'organizer@example.com',
                 'external-id-should-never-print', 'Private conference room')
    leaked = [value for value in forbidden if value in stdout]
    if leaked:
        raise SystemExit('dry-run leaked event payload data: ' + ', '.join(leaked))
    lines = [line for line in stdout.splitlines() if line]
    if not lines or not lines[-1].startswith('calendar-pull: source=calendar '):
        raise SystemExit('dry-run must end with the aggregate calendar-pull marker')
    if 'posted=0' not in lines[-1] or 'failed=0' not in lines[-1]:
        raise SystemExit('dry-run aggregate must prove no post and no failure')

print('calendar dry-run purity selftest — pass')
