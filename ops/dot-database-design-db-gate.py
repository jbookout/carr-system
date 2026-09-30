#!/usr/bin/env python3
# ci: db-gate
"""Exercise database design regressions against the disposable CI database."""
import os
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
from test_dot_database_design import DatabaseDesign

if __name__ == '__main__':
    os.environ['DOT_DATABASE_URL'] = os.environ.get('DATABASE_URL', '')
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(DatabaseDesign)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if result.skipped:
        raise SystemExit('Database design regression checks require a disposable loopback database')
    raise SystemExit(0 if result.wasSuccessful() else 1)
