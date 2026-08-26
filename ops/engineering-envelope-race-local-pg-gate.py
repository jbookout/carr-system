#!/usr/bin/env python3
# ci: db-gate
# doctrine: runbook
"""Disposable two-session proof for Engineering session-terminalization fencing."""

from __future__ import annotations

import importlib.util
import os
import sys
import threading
import time
from pathlib import Path

import psycopg


RUNTIME_ROLE = "carr_jobs"


def fail(message: str) -> int:
    print(f"engineering-envelope-race-local-pg-gate: FAIL — {message}", file=sys.stderr)
    return 1


def load_claim_gate():
    path = Path(__file__).with_name("engineering-claim-local-pg-gate.py")
    spec = importlib.util.spec_from_file_location("engineering_claim_local_pg_gate", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load Engineering claim fixture")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    dsn = os.environ.get("DATABASE_URL", "") or os.environ.get("CARR_LOCAL_PG_DSN", "")
    if not dsn or not any(host in dsn for host in ("127.0.0.1", "localhost")):
        return fail("a disposable loopback DATABASE_URL or CARR_LOCAL_PG_DSN is required")
    gate = load_claim_gate()
    try:
        with psycopg.connect(dsn) as setup, setup.cursor() as cur:
            gate.grant_settable_runtime_roles(cur, RUNTIME_ROLE)
            job_id, envelope_id, session_id, _, _, _, _ = gate.fixture(cur, expires_in_seconds=900)
            gate.set_local_role(cur, RUNTIME_ROLE)
            claimed = gate.one(cur, "select job_id,lease_token from ops.engineering_claim_slice(%s,1,300)",
                               ("engineering-envelope-race",))
            if claimed[0] != job_id:
                return fail("fresh race fixture was not claimed")
            setup.commit()

        binding_ready = threading.Event()
        allow_binding_commit = threading.Event()
        terminal_started = threading.Event()
        terminal_result: list[str] = []

        def bind() -> None:
            with psycopg.connect(dsn) as conn, conn.cursor() as cur:
                gate.set_local_role(cur, RUNTIME_ROLE)
                binding = gate.one(cur, "select ops.engineering_controller_binding(%s,%s,%s)",
                                   (envelope_id, job_id, claimed[1]))[0]
                if binding is None:
                    raise RuntimeError("live fixture unexpectedly had no controller binding")
                binding_ready.set()
                if not allow_binding_commit.wait(3):
                    raise RuntimeError("terminal race did not begin")
                conn.commit()

        def terminalize() -> None:
            with psycopg.connect(dsn) as conn, conn.cursor() as cur:
                terminal_started.set()
                try:
                    cur.execute("set local statement_timeout='3s'")
                    cur.execute("update ops.capability_agent_session set state='cancelled',cancelled_at=now() where id=%s",
                                (session_id,))
                    conn.commit()
                    terminal_result.append("unexpectedly succeeded")
                except psycopg.Error as exc:
                    terminal_result.append(str(exc))
                    conn.rollback()

        binding_thread = threading.Thread(target=bind, daemon=True)
        terminal_thread = threading.Thread(target=terminalize, daemon=True)
        binding_thread.start()
        if not binding_ready.wait(3):
            return fail("controller binding did not acquire its lineage lock")
        terminal_thread.start()
        if not terminal_started.wait(3):
            return fail("terminalization session did not start")
        time.sleep(0.15)
        if not terminal_thread.is_alive():
            return fail("terminalization did not serialize behind controller binding")
        allow_binding_commit.set()
        binding_thread.join(3)
        terminal_thread.join(3)
        if binding_thread.is_alive() or terminal_thread.is_alive():
            return fail("two-session terminalization race did not finish")
        if len(terminal_result) != 1 or "engineering session terminalization deferred while its dispatch lease is live" not in terminal_result[0]:
            return fail(f"terminalization race did not fail closed: {terminal_result!r}")
    except Exception as exc:  # noqa: BLE001 - report exact disposable DB failure
        return fail(str(exc))
    print("engineering envelope race acceptance passed: terminalization serializes and fails closed behind a live binding")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
