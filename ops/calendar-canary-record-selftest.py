#!/usr/bin/env python3
"""Hermetic contract checks for the EventKit isolated canary writer."""
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
failed=[]
def check(label,ok):
 print(('  ok  ' if ok else '  FAIL ')+label)
 if not ok: failed.append(label)
writer=(ROOT/'tools/calendar-canary-record.py').read_text()
capture=(ROOT/'bin/calendar-eventkit-capture.sh').read_text()
migration=(ROOT/'migrations/0200_calendar_canary_record_layer.sql').read_text()
manifest=(ROOT/'ops/config/control-plane-workflows.v1.json').read_text()
check('canary writer requires explicit canary mode and destination identity','CARR_CONTROL_PLANE_MODE' in writer and 'CARR_CALENDAR_CANARY_DESTINATION_ID' in writer and 'CARR_CALENDAR_CANARY_DSN' in writer)
check('writer refuses live credential aliases and never calls the normal record verb','aliases a live database credential' in writer and 'run.sh' not in writer and 'log-activity' not in writer)
check('writer records canonical deterministic digests only','sort_keys=True' in writer and 'calendar-canary-output-v1' in writer and 'record_calendar_canary_receipt' in writer)
check('receipt table is append-only, readback-resolvable, and binds database identity','calendar_canary_receipt_append_only' in migration and 'resolve_calendar_canary_receipt' in migration and 'current_database()' in migration and 'calendar_canary_destination' in migration)
check('only jobs identity may mint receipt','session_user <> \'carr_jobs\'' in migration and 'grant execute on function ops.record_calendar_canary_receipt' in migration)
check('capture cannot combine canary with dry run and diverts before live intake/writes','--canary) CANARY=1' in capture and '--canary requires explicit control-plane canary mode' in capture and capture.index('calendar-canary-record.py') < capture.index('calendar-intake-gate.py'))
check('calendar manifest names registered guarded canary','"calendar-fetch-daily.canary.v1"' in manifest and '"args": ["--canary", "--days", "7"]' in manifest)
check('new EventKit canary path has no Drive dependency','CARR_VAULT' not in writer and 'Drive' not in writer)
print(f'calendar canary record selftest — {8-len(failed)}/8 passed')
raise SystemExit(bool(failed))
