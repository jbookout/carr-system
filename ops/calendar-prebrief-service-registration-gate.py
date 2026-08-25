#!/usr/bin/env python3
# ci: db-gate
"""Prove the Joe calendar-prebrief LaunchAgent is representable in operations.

The service catalog is the source of truth; ``tools/ops-record.py sync-registry``
is its one database projection.  This gate deliberately exercises that door on
the disposable migration database rather than adding seed SQL that could drift
from ``ops/config/services.json``.

The declaration must create a production health-view row, but it must not turn
on either prebrief workflow.  Joe live activation remains the existing typed,
receipt-backed authority operation; the isolated canary remains disabled.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import psycopg


REPO = Path(__file__).resolve().parents[1]
SERVICE = "calendar-prebrief-joe"
ENVIRONMENT = "production"


def require(row: tuple | None, message: str) -> tuple:
    if row is None:
        raise RuntimeError(message)
    return row


def main() -> int:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("calendar-prebrief service registration gate: DATABASE_URL is not set")

    synced = subprocess.run(
        [sys.executable, str(REPO / "tools" / "ops-record.py"), "sync-registry"],
        cwd=REPO, env={**os.environ, "DATABASE_URL": dsn}, text=True,
        capture_output=True, timeout=120,
    )
    if synced.returncode:
        raise RuntimeError(
            "calendar-prebrief service registration gate: sync-registry failed: "
            + (synced.stderr.strip() or synced.stdout.strip())
        )

    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """select s.key, s.name, s.family, s.criticality, s.owner_actor,
                      s.repo_path, s.runtime, se.environment,
                      se.deploy_mechanism, se.expected_cadence_seconds,
                      se.cadence_grace_seconds
                 from ops.service s
                 join ops.service_environment se on se.service_id=s.id
                where s.key=%s and s.retired_at is null and se.environment=%s""",
            (SERVICE, ENVIRONMENT),
        )
        service = require(cur.fetchone(), "calendar prebrief service projection is absent")
        expected = (
            SERVICE, "Joe calendar prebrief projection", "Local Mac edge", "medium", "joe",
            "tools/calendar-prebrief-joe-runtime.py", "launchd", ENVIRONMENT,
            "ops/launchd/com.carr.calendar-prebrief-joe.plist", 86400, 172800,
        )
        if service != expected:
            raise RuntimeError(f"calendar prebrief service projection drifted: {service!r}")

        cur.execute(
            """select service_key, environment, health, freshness_state,
                      observed_at
                 from ops.v_service_environment_health
                where service_key=%s and environment=%s""",
            (SERVICE, ENVIRONMENT),
        )
        health = require(cur.fetchone(), "calendar prebrief has no health-view row")
        if health[:4] != (SERVICE, ENVIRONMENT, "unknown", "missing") or health[4] is not None:
            raise RuntimeError(f"calendar prebrief initial health is not visible-and-honest: {health!r}")

        cur.execute(
            """select key, enabled
                 from ops.job_definition
                where (key, version) in
                    (('calendar-prebrief-projection-joe-daily', 1),
                     ('calendar-prebrief-canary-joe-daily', 1))
                order by key"""
        )
        definitions = cur.fetchall()
        if definitions != [
            ("calendar-prebrief-canary-joe-daily", False),
            ("calendar-prebrief-projection-joe-daily", False),
        ]:
            raise RuntimeError(
                "service registration changed calendar-prebrief activation authority: "
                f"{definitions!r}"
            )

    print("calendar prebrief service registration gate: PASS — projected, health-visible, authority unchanged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
