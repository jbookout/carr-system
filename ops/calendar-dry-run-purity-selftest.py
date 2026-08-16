#!/usr/bin/env python3
"""Prove the calendar dry-run does not append to capture-lanes.log."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib.loadpy import load_module_from_path

calendar = load_module_from_path('calendar_dry_run_under_test', str(ROOT / 'bin' / 'pull-gmail-calendar.py'))
setattr(calendar, 'FEEDS', {'joe': '/definitely/missing/joe.ics', 'dell': '/definitely/missing/dell.ics'})
setattr(calendar, 'say', lambda _message: (_ for _ in ()).throw(AssertionError('dry-run attempted log append')))
rc = calendar.run_calendar(argparse.Namespace(dry_run=True, days_back=1, days_ahead=14))
if rc != 0:
    raise SystemExit('dry-run should succeed when feeds are absent')
print('calendar dry-run purity selftest — pass')
