#!/usr/bin/env python3
# ci: db-gate
# doctrine: runbook
"""V5-J102 live acceptance on a disposable loopback sibling database.

WHAT IT PROVES. The CRE lifecycle's candidate SQL (ops/cre-lifecycle.candidate.sql)
executes on top of the migrated schema, and then, against real PostgreSQL:

  * the SQL fixture (mcp-server/test/cre-lifecycle-postgres.sql) passes as a
    verified partner (Joe, Dell) and runs its admitted groups as a sponsored agent;
  * the live Node suite (mcp-server/test/cre-lifecycle-live-pg.v5.test.mjs) passes:
    the full truth table and its refusals, Q103 concurrent merge/reconcile, the
    writer-credential attribution split, and the Q081 migration shadow.

WHERE. Never in the CI database itself. A sibling is copied from it (so it carries
every numbered migration), F01's domain.sql is added only when F01 is not yet a
migration there, the J102 SQL is applied, and the sibling is always dropped.

CLUSTER STATE IS RESTORED. The two authority logins and carr_writer's LOGIN are
cluster-global; whatever this gate adds is recorded first and put back after, so a
later gate sees the cluster as it found it.
"""

from __future__ import annotations

import ipaddress
import os
import shutil
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import psycopg
from psycopg import sql

REPO = Path(__file__).resolve().parents[1]
J102_SQL = REPO / "ops/cre-lifecycle.candidate.sql"
F01_DOMAIN_SQL = REPO / "domain.sql"
F01_DOCUMENT_SOURCE_SQL = REPO / "ops/document-derivative-registration.candidate.sql"
FIXTURE = REPO / "mcp-server/test/cre-lifecycle-postgres.sql"
LIVE_SUITE = "test/cre-lifecycle-live-pg.v5.test.mjs"
AUTHORITY_LOGINS = ("carr_authority_joe", "carr_authority_dell")


def fail(message: str) -> int:
    print(f"cre-lifecycle-local-pg-gate: FAIL — {message}", file=sys.stderr)
    return 1


def loopback(dsn: str) -> dict:
    parts = psycopg.conninfo.conninfo_to_dict(dsn)
    host = str(parts.get("hostaddr") or parts.get("host") or "")
    if not host or "," in host:
        raise RuntimeError("one explicit loopback host is required")
    try:
        ok = ipaddress.ip_address(host).is_loopback
    except ValueError:
        ok = host == "localhost"
    if not ok:
        raise RuntimeError("refusing a non-loopback DATABASE_URL")
    return parts


@contextmanager
def sibling(base: str) -> Iterator[str]:
    parts = loopback(base)
    source = parts.get("dbname")
    if not source:
        raise RuntimeError("DATABASE_URL must name its database")
    name = f"j102_gate_{os.getpid()}_{time.time_ns()}"[:63]
    admin = psycopg.conninfo.make_conninfo(base, dbname="postgres")
    with psycopg.connect(admin, autocommit=True) as con, con.cursor() as cur:
        cur.execute(sql.SQL("create database {} template {}").format(
            sql.Identifier(name), sql.Identifier(source)))
    try:
        yield psycopg.conninfo.make_conninfo(base, dbname=name)
    finally:
        with psycopg.connect(admin, autocommit=True) as con, con.cursor() as cur:
            cur.execute("select pg_terminate_backend(pid) from pg_stat_activity "
                        "where datname=%s and pid<>pg_backend_pid()", (name,))
            cur.execute(sql.SQL("drop database if exists {}").format(sql.Identifier(name)))


@contextmanager
def principals(base: str) -> Iterator[None]:
    """Add the two authority logins and carr_writer LOGIN; restore exactly after."""
    admin = psycopg.conninfo.make_conninfo(base, dbname="postgres")
    created: list[str] = []
    granted: list[tuple[str, str]] = []
    with psycopg.connect(admin, autocommit=True) as con, con.cursor() as cur:
        cur.execute("select rolcanlogin from pg_roles where rolname='carr_writer'")
        row = cur.fetchone()
        if row is None:
            raise RuntimeError("carr_writer does not exist; the migrated schema is incomplete")
        writer_could_login = bool(row[0])
        for login in AUTHORITY_LOGINS:
            cur.execute("select 1 from pg_roles where rolname=%s", (login,))
            if cur.fetchone() is None:
                cur.execute(sql.SQL("create role {} login").format(sql.Identifier(login)))
                created.append(login)
            for bundle in ("carr_authority", "carr_writer"):
                cur.execute("select pg_has_role(%s, %s, 'member')", (login, bundle))
                if not cur.fetchone()[0]:
                    cur.execute(sql.SQL("grant {} to {}").format(
                        sql.Identifier(bundle), sql.Identifier(login)))
                    granted.append((bundle, login))
        if not writer_could_login:
            cur.execute("alter role carr_writer login")
    try:
        yield
    finally:
        with psycopg.connect(admin, autocommit=True) as con, con.cursor() as cur:
            for bundle, login in granted:
                if login not in created:
                    cur.execute(sql.SQL("revoke {} from {}").format(
                        sql.Identifier(bundle), sql.Identifier(login)))
            for login in created:
                cur.execute(sql.SQL("drop role if exists {}").format(sql.Identifier(login)))
            if not writer_could_login:
                cur.execute("alter role carr_writer nologin")


def psql(dsn: str, path: Path, *, env: dict | None = None) -> subprocess.CompletedProcess[str]:
    binary = shutil.which("psql")
    if binary is None:
        raise RuntimeError("psql is required")
    return subprocess.run([binary, "-X", "-q", "-v", "ON_ERROR_STOP=1", "-d", dsn, "-f", str(path)],
                          env={**os.environ, **(env or {})}, text=True, capture_output=True, timeout=900)


def as_login(dsn: str, login: str) -> str:
    return psycopg.conninfo.make_conninfo(dsn, user=login, password=None)


def url_for(dsn: str, login: str | None = None) -> str:
    """node-pg reads URLs, not key=value conninfo. No password is ever carried:
    the disposable loopback cluster authenticates these logins by trust/peer."""
    from urllib.parse import quote
    parts = psycopg.conninfo.conninfo_to_dict(dsn)
    host = parts.get("hostaddr") or parts.get("host") or "127.0.0.1"
    port = parts.get("port") or "5432"
    user = login or parts.get("user") or ""
    password = "" if login else (parts.get("password") or "")
    auth = quote(user) + (":" + quote(password) if password else "")
    return f"postgresql://{auth}@{host}:{port}/{quote(parts['dbname'])}"


def main() -> int:
    base = os.environ.get("DATABASE_URL", "") or os.environ.get("CARR_LOCAL_PG_DSN", "")
    if not base:
        return fail("a disposable loopback DATABASE_URL or CARR_LOCAL_PG_DSN is required")
    try:
        with principals(base), sibling(base) as dsn:
            with psycopg.connect(dsn) as con, con.cursor() as cur:
                cur.execute("select to_regprocedure('ops.f01_principal()') is not null")
                f01_migrated = cur.fetchone()[0]
            if not f01_migrated:
                # Until F01 lands as a numbered migration, its two unnumbered
                # hunks stand in for it: domain.sql, then the document-source
                # hunk that carries the six-argument document writer F01's store
                # door calls. Once F01 is migrated this branch never runs.
                for path in (F01_DOMAIN_SQL, F01_DOCUMENT_SOURCE_SQL):
                    done = psql(dsn, path, env={"PGOPTIONS": "--client-min-messages=warning"})
                    if done.returncode:
                        return fail(f"{path.name} did not apply: {done.stderr[-800:]}")
            done = psql(dsn, J102_SQL, env={"PGOPTIONS": "--client-min-messages=warning"})
            if done.returncode:
                return fail(f"the J102 candidate SQL did not apply: {done.stderr[-800:]}")

            # The SQL fixture: both partners must pass every runnable group; the
            # agent runs the groups its class admits and names the rest.
            for who, login, options in (
                ("joe", "carr_authority_joe", ""),
                ("dell", "carr_authority_dell", ""),
                ("agent", "carr_writer", "-c carr.acting_actor_slug=claude-ci"),
            ):
                run = psql(as_login(dsn, login), FIXTURE,
                           env={"PGOPTIONS": f"--client-min-messages=notice {options}".strip()})
                out = run.stdout + run.stderr
                if run.returncode:
                    return fail(f"SQL fixture as {who} failed: {out[-1200:]}")
                expected = "PARTIAL RUN" if who == "agent" else "ALL RUNNABLE GROUPS PASSED"
                if expected not in out:
                    return fail(f"SQL fixture as {who} did not report {expected!r}: {out[-800:]}")

            env = {
                **os.environ,
                "CARR_J102_LIVE_PG_DSN_JOE": url_for(dsn, "carr_authority_joe"),
                "CARR_J102_LIVE_PG_DSN_DELL": url_for(dsn, "carr_authority_dell"),
                "CARR_J102_LIVE_PG_DSN_WRITER": url_for(dsn, "carr_writer"),
                "CARR_J102_LIVE_PG_DSN_OWNER": url_for(dsn),
            }
            # The shadow test requires an empty legacy table; the copied CI
            # database may carry seeded deals, and this is a throwaway sibling.
            with psycopg.connect(dsn) as con, con.cursor() as cur:
                cur.execute("set session_replication_role = replica")
                cur.execute("delete from public.deal")
            live = subprocess.run(["node", "--test", LIVE_SUITE], cwd=REPO / "mcp-server",
                                  env=env, text=True, capture_output=True, timeout=1800)
            if live.returncode:
                return fail(f"live suite failed: {(live.stdout + live.stderr)[-2000:]}")
            if "# skipped 0" not in live.stdout and "ℹ skipped 0" not in live.stdout:
                return fail(f"live suite skipped tests: {live.stdout[-800:]}")
    except Exception as exc:  # noqa: BLE001 - report the exact disposable-DB failure
        return fail(str(exc))
    print("cre-lifecycle live acceptance passed: fixture (joe, dell, agent) and live suite "
          "(truth table, Q103 merge/reconcile, credential split, Q081 shadow)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
