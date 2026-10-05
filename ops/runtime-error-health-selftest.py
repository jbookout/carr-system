import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
from runtime_error_health import health_row, ACTION

root = Path(__file__).resolve().parent.parent / 'out/_to_delete/runtime-error-health' / str(uuid4())
(root / 'out').mkdir(parents=True)
now = datetime.now(timezone.utc)
assert health_row(root, now).startswith('UNAVAILABLE')
receipt = root / 'out/runtime-error-health.json'
receipt.write_text(json.dumps({'checked_at': now.isoformat(), 'health': f'WARN runtime errors — 1 active fingerprint · {ACTION}'}))
assert health_row(root, now).startswith('WARN')
assert 'owner orchestrator' in health_row(root, now)
assert 'verify' in health_row(root, now)
assert 'auto-clear after 24h quiet on a newer release' in health_row(root, now)
assert health_row(root, now + timedelta(minutes=11)).startswith('UNAVAILABLE')
receipt.write_text(json.dumps({'checked_at': now.isoformat(), 'health': 'OK naked metric'}))
assert health_row(root, now).startswith('UNAVAILABLE')
print('runtime-error-health-selftest: bound, stale and missing rows passed')
