import json
from datetime import datetime, timezone
from pathlib import Path

ACTION = ('on breach: open/update one fingerprint loop · owner orchestrator · '
          'repair the failing route or capture/consumer connectivity · '
          'verify an error replay becomes one loop and a successful request passes · '
          'auto-clear after 24h quiet on a newer release')


def health_row(repo, now=None):
    now = now or datetime.now(timezone.utc)
    try:
        receipt = json.loads((Path(repo) / 'out/runtime-error-health.json').read_text())
        age = (now - datetime.fromisoformat(receipt['checked_at'])).total_seconds()
        if not 0 <= age <= 600:
            return f'UNAVAILABLE runtime errors — consumer receipt older than 10m · {ACTION}'
        row = receipt['health']
        if not isinstance(row, str) or 'on breach:' not in row:
            raise ValueError('unbound runtime error row')
        return row
    except (OSError, ValueError, KeyError, TypeError):
        return f'UNAVAILABLE runtime errors — capture/consumer receipt missing · {ACTION}'
