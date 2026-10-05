#!/usr/bin/env python3
# ci: db-gate
# doctrine: runbook
"""Run the synthetic lead-stage behavior fixture on CI's disposable database."""
import os
from pathlib import Path
import subprocess
from urllib.parse import urlparse
REPO=Path(__file__).resolve().parents[1]
def verify(env,run=subprocess.run):
    dsn=env.get('CARR_CI_DATABASE_URL') or env.get('DATABASE_URL')
    if not dsn or urlparse(dsn).hostname not in ('localhost','127.0.0.1'):
        raise ValueError('disposable loopback database required')
    for fixture in ('lead-automation.postgres.mjs','automation-undo-archive-invoice.postgres.mjs',
                    'lead-automation-concurrency.postgres.mjs','invoice-review-regressions.postgres.mjs'):
        result=run(['node',str(REPO/'mcp-server/test'/fixture)],cwd=REPO,
                   env={**env,"CARR_INVOICING_MAILBOX":"invoices@example.test"},timeout=120)
        if result.returncode: return result.returncode
    return 0
if __name__=='__main__':
    raise SystemExit(verify(dict(os.environ)))
