#!/usr/bin/env python3
# ci: db-gate
# doctrine: runbook
"""Real controller race remains valid after a slow fixture admission commit."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import patch

import psycopg


def main() -> int:
    path = Path(__file__).with_name("engineering-envelope-race-local-pg-gate.py")
    spec = importlib.util.spec_from_file_location("engineering_envelope_race", path)
    assert spec and spec.loader
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)
    commit = psycopg.Connection.commit
    delayed = False

    def slow_admission_commit(conn):
        nonlocal delayed
        if not delayed:
            delayed = True
            # The controller requires 930s remaining on a 960s claim. A real
            # admission commit exceeding that 30s margin must not age the claim.
            with conn.cursor() as cur:
                cur.execute("select pg_sleep(31)")
        return commit(conn)

    with patch.object(psycopg.Connection, "commit", slow_admission_commit):
        return gate.main()


if __name__ == "__main__":
    raise SystemExit(main())
