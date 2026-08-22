#!/usr/bin/env python3
# ci: db-gate
"""Behaviorally rehearse the exact legacy-retirement boundary in 0228/0247.

This is intentionally a sibling-database fixture.  The ordinary migration
class has already applied 0228/0247 before gates run; loading the pinned,
immutable predecessor snapshot is the only honest way to execute their forward
path again.  It never contacts a provider and refuses non-loopback DSNs.
"""
from __future__ import annotations

import base64
import hashlib
import ipaddress
import os
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import psycopg
from psycopg import sql

REPO = Path(__file__).resolve().parents[1]
SNAPSHOT = "3ad2775ec3dadf647b09da39d68af2a7704089ad"
SNAPSHOT_PATH = "db/schema.sql"
JOE = "b6c38b27-d006-4fad-9c38-49edf3130a07"
COST_RULE = "a57d981a-8f6d-4c18-95ee-0e63a5a90b89"
RETIRE_EVENT = "34f34e23-225b-4d0f-946f-478b59fbce63"
RULE_STATEMENT = (
    "Every metered CARR execution must pass a machine-enforced pre-dispatch budget gate; "
    "prose or registry-only guidance does not count as enforcement, and Joe alone may "
    "approve exceeding a cap, buying usage credits, or enabling paid overage."
)
RULE_QUOTE = (
    "Also, how does this budgeting plan become impossible to overlook? If it’s just prose "
    "the system won’t remember it"
)
RETIRE_RATIONALE_B64 = (
    "Sm9lIGVuZGVkIGl0LCAyMDI2LTA4LTIxLCBpbiBoaXMgb3duIHdvcmRzOiAibGlzdGVuLCBlbmQgdGhlIGNvc3QgcmVzdHJpY3Rpb25zLiBpIGZlZWwgbGlrZSB0aGUgZW50aXJlIHN5c3RlbSBpcyBtYWxmdW5jdGlvbmluZyBiZWNhdXNlIHlvdSBvdmVyIGFwcGxpZWQgdGhlIHJ1bGUuIGFsbCBpIHdhbnRlZCB3YXMgdG8gc3RvcCB5b3UgZnJvbSB3YXN0aW5nIGFjdGlvbiBtaW51dGVzIG9uIHRlc3RpbmcgcHJvY2VkdXJlcyBhbmQganVzdCB1c2UgdGhlIGFjdGlvbiBtaW51dGVzIHJlc3BvbnNpYmx5LiBub3cgZXZlcnl0aGluZyBpcyBiZWluZyBibG9ja2VkIGJjIHlvdSB0aGluayB5b3UgYXJlbnQgYWxsb3dlZCB0byBkbyBhbnl0aGluZyB0aGF0IGNvc3RzIHVzYWdlIG9yIG1vbmV5LiIKCldIQVQgVEhJUyBSVUxFIERFTUFOREVEOiB0aGF0IGV2ZXJ5IG1ldGVyZWQgZXhlY3V0aW9uIHBhc3MgYSBNQUNISU5FLUVORk9SQ0VEIHByZS1kaXNwYXRjaCBidWRnZXQgZ2F0ZSwgd2l0aCBwcm9zZSBndWlkYW5jZSBleHBsaWNpdGx5IG5vdCBjb3VudGluZyBhcyBlbmZvcmNlbWVudCwgYW5kIEpvZSBhbG9uZSBhcHByb3ZpbmcgYW55IGNhcCBvdmVycnVuLiBJdCBuZXZlciBhY3RpdmF0ZWQsIGJ1dCBtaWdyYXRpb24gMDIyOCB3YXMgd3JpdHRlbiB0byBiaW5kIGl0IHRvIGEgcmVhbCBlbmZvcmNlbWVudCBjb250cm9sIG5hbWVkIHBsYXRmb3JtX21ldGVyaW5nX3ByZV9kaXNwYXRjaCDigJQgc28gYXBwbHlpbmcgdGhhdCBtaWdyYXRpb24gd291bGQgaGF2ZSB0dXJuZWQgYSBwcm9wb3NlZCByZXN0cmljdGlvbiBpbnRvIGEgbGl2ZSBtYWNoaW5lIGdhdGUgaW4gZnJvbnQgb2YgZXZlcnkgbWV0ZXJlZCBjYWxsLCBvbiB0aGUgc2FtZSBkYXkgSm9lIGFza2VkIGZvciB0aGUgcmVzdHJpY3Rpb25zIHRvIGVuZC4KCldIQVQgSk9FIEFDVFVBTExZIFdBTlRFRCwgc3RhdGVkIHBsYWlubHkgc28gdGhpcyBpcyBub3QgcmUtdGF1Z2h0IGluIHRoaXMgc2hhcGU6IGRvIG5vdCBidXJuIEFjdGlvbnMgbWludXRlcyBvbiB0ZXN0aW5nIHByb2NlZHVyZSwgYW5kIHVzZSB0aGVtIHJlc3BvbnNpYmx5LiBUaGF0IGlzIGEganVkZ2VtZW50IGluc3RydWN0aW9uIHRvIHRoZSBvcGVyYXRvciwgbm90IGEgcHJlLWRpc3BhdGNoIGludGVybG9jay4gVGhlIGludGVybG9jayBpcyB3aGF0IHByb2R1Y2VkIHRoZSBwYXJhbHlzaXMgaGUgaXMgZGVzY3JpYmluZyDigJQgYSBzeXN0ZW0gdGhhdCB0cmVhdHMgZXZlcnkgYWN0aW9uIHdpdGggYSBjb3N0IGFzIGZvcmJpZGRlbiB1bnRpbCBjbGVhcmVkIHN0b3BzIGRvaW5nIHdvcmssIHdoaWNoIGlzIG1vcmUgZXhwZW5zaXZlIHRoYW4gdGhlIHNwZW5kIGl0IHdhcyBndWFyZGluZy4KCldIQVQgSVMgREVMSUJFUkFURUxZIEtFUFQsIGJlY2F1c2UgaXQgaXMgdGhlIHNhbWUgaW5zdGluY3QgZG9uZSByaWdodCBhbmQgSm9lIHJlc3RhdGVkIGl0IGhvdXJzIGVhcmxpZXIgKCJpbSB0aXJlZCBvZiBjbGF1ZGUgd2FzdGluZyB0b2tlbnMgYnkgbm90IGRlbGVnYXRpbmcgdG8gY2hlYXBlciBtb2RlbHMiKTogdGhlIHJ1bGUgcmVzZXJ2aW5nIHRoZSBmcm9udGllciBzZWF0IGZvciBkZXNpZ24gYW5kIGp1ZGdtZW50IHdoaWxlIE9wdXMsIFNvbm5ldCwgQ29kZXggYW5kIEdyb2sgY2FycnkgZXhlY3V0aW9uLCBhbmQgdGhlIHJ1bGUgbWFraW5nIGNvc3QgdGllciBwYXJ0IG9mIGNob29zaW5nIGFuIGV4ZWN1dG9yLiBUaG9zZSByb3V0ZSB3b3JrIHRvIHRoZSBjaGVhcGVzdCBjYXBhYmxlIHNlYXQuIFRoZXkgZG8gbm90IHN0YW5kIGluIGZyb250IG9mIHdvcmsgYW5kIHJlZnVzZSBpdC4KCkFsc28ga2VwdCBhbmQgbm93IHRvIGJlIGFjdGl2YXRlZCByYXRoZXIgdGhhbiByZXRpcmVkOiBKb2UncyBvd24gcnVsaW5nIGZyb20gZWFybGllciB0b2RheSB0aGF0IEdpdEh1YiBBY3Rpb25zIHN0YXlzIG9uIHBlcm1hbmVudGx5LCB3aGljaCBpcyB0aGUgYW50aS1yZXN0cmljdGlvbiDigJQgaXQgcmVmdXNlcyBhbnkgcGxhbiB0byBzYXZlIG1vbmV5IGJ5IHN3aXRjaGluZyBvZmYgdGhlIGNoZWNrIHRoYXQgbGV0cyB3b3JrIGxhbmQu"
)


def fail(message: str) -> int:
    print(f"atomic-rule-compat-migration-gate: FAIL — {message}", file=sys.stderr)
    return 1


def loopback(dsn: str) -> None:
    try:
        parts = psycopg.conninfo.conninfo_to_dict(dsn)
    except psycopg.Error as exc:
        raise RuntimeError("sibling fixture requires valid explicit conninfo") from exc
    if parts.get("service") or parts.get("servicefile"):
        raise RuntimeError("sibling fixture refuses libpq service indirection")
    hosts: list[str] = []
    for key in ("host", "hostaddr"):
        value = str(parts.get(key) or "")
        if not value:
            continue
        if "," in value:
            raise RuntimeError("sibling fixture refuses multi-host conninfo")
        hosts.append(value)
    if not hosts or "," in str(parts.get("port") or ""):
        raise RuntimeError("sibling fixture requires one explicit loopback target")
    for host in hosts:
        try:
            is_loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            is_loopback = host == "localhost"
        if not is_loopback:
            raise RuntimeError("sibling fixture refuses every non-loopback DATABASE_URL")


def assert_loopback_guard() -> None:
    for hostile in (
        "host=localhost hostaddr=203.0.113.1 port=5432 dbname=fixture",
        "host=127.0.0.1,203.0.113.1 port=5432 dbname=fixture",
        "service=production",
    ):
        try:
            loopback(hostile)
        except RuntimeError:
            continue
        raise RuntimeError(f"sibling fixture accepted hostile conninfo: {hostile}")


@contextmanager
def sibling(base: str, label: str) -> Iterator[str]:
    """Create only a named loopback sibling and always terminate/drop it."""
    loopback(base)
    name = f"atomic_retirement_{label}_{os.getpid()}_{time.time_ns()}"[:63]
    admin = psycopg.conninfo.make_conninfo(base, dbname="postgres")
    with psycopg.connect(admin, autocommit=True) as con, con.cursor() as cur:
        cur.execute(sql.SQL("create database {} template template0").format(sql.Identifier(name)))
    dsn = psycopg.conninfo.make_conninfo(base, dbname=name)
    try:
        yield dsn
    finally:
        with psycopg.connect(admin, autocommit=True) as con, con.cursor() as cur:
            cur.execute("select pg_terminate_backend(pid) from pg_stat_activity where datname=%s and pid<>pg_backend_pid()", (name,))
            cur.execute(sql.SQL("drop database if exists {}").format(sql.Identifier(name)))


def psql_bin() -> str:
    for path in ("psql", "/opt/homebrew/opt/libpq/bin/psql", "/usr/local/opt/libpq/bin/psql"):
        if os.path.exists(path) and os.access(path, os.X_OK):
            return path
        if path == "psql":
            from shutil import which
            if which(path): return path
    raise RuntimeError("psql is required to stream the pinned predecessor snapshot")


def load_predecessor(dsn: str) -> None:
    """Stream the pinned git blob directly into the sibling; no temp snapshot."""
    check = subprocess.run(["git", "cat-file", "-e", f"{SNAPSHOT}:{SNAPSHOT_PATH}"], cwd=REPO)
    if check.returncode:
        raise RuntimeError("pinned pre-0228 snapshot blob is unavailable")
    show = subprocess.Popen(["git", "show", f"{SNAPSHOT}:{SNAPSHOT_PATH}"], cwd=REPO, stdout=subprocess.PIPE)
    restore = subprocess.run([psql_bin(), "-X", "-v", "ON_ERROR_STOP=1", "-q", "-d", dsn], stdin=show.stdout)
    if show.stdout: show.stdout.close()
    if show.wait() or restore.returncode:
        raise RuntimeError("pinned predecessor snapshot did not load")


def migrate(dsn: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(REPO / "tools/migrate.py"), "--apply", "--yes",
                           "--through", "0247_system_rule_scope_binding.sql"],
                          cwd=REPO, env={**os.environ, "DATABASE_URL": dsn}, text=True,
                          capture_output=True, timeout=300)


def seed_fixture(dsn: str, *, mutate_retirement: bool) -> None:
    """Seed the exact legacy-retirement preimage."""
    rationale = base64.b64decode(RETIRE_RATIONALE_B64).decode("utf-8")
    if hashlib.sha256(rationale.encode()).hexdigest() != (
        "82cf84d571cbe49eb61bf9570e2c8f86a114fa216e9ab1b3799181045c881137"
    ):
        raise RuntimeError("pinned retirement rationale fixture digest drifted")
    if mutate_retirement:
        rationale += " mutation"
    with psycopg.connect(dsn) as con, con.cursor() as cur:
        cur.execute(
            """insert into public.rule
               (id,statement,human_quote,taught_by,scope,personal_to,status,
                activated_by,activated_at,enforcement,supersedes,created_at,version,updated_at)
               values (%s,%s,%s,%s,
                 '{"domain":"system","applies_to":["github","neon","cloudflare","anthropic","openai","google","healthchecks","blotato","make"]}'::jsonb,
                 null,'retired',null,null,'prose',null,
                 '2026-08-17T08:29:06.905178Z'::timestamptz,2,
                 '2026-08-21T21:38:29.049309Z'::timestamptz)""",
            (COST_RULE, RULE_STATEMENT, RULE_QUOTE, JOE),
        )
        cur.execute(
            """insert into public.event
               (id,occurred_at,recorded_at,actor_id,verb,subject_type,subject_id,
                field,old_value,new_value,cause,human_quote,agent_rationale,
                idempotency_key,via,client_id,sponsoring_human_slug,personal_scope,
                authorization_class,organization_tenant_id,correlation_id)
               values (%s,'2026-08-21T21:38:29.049309Z'::timestamptz,
                 '2026-08-21T21:38:29.049309Z'::timestamptz,%s,'retire-rule','rule',%s,
                 'status','{"status":"proposed"}'::jsonb,'{"status":"retired"}'::jsonb,
                 'automation_job',null,%s,'c4ad90f7-d8dd-4bf3-8785-659bae3d3f27',
                 'oauth-google','https://claude.ai/oauth/mcp-oauth-client-metadata',
                 'joe','joe-personal','verified_partner','carr-internal',
                 '6923f7f8-4ae6-4db0-93ab-9424d5aea0f1'::uuid)""",
            (RETIRE_EVENT, JOE, COST_RULE, rationale),
        )
        con.commit()


def assert_tombstone(dsn: str) -> None:
    with psycopg.connect(dsn) as con, con.cursor() as cur:
        row = cur.execute(
            """select r.status,r.version,r.activated_by is null,
                      (select count(*) from ops.rule_control_binding where rule_id=r.id),
                      (select count(*) from ops.rule_admission where rule_id=r.id)
                 from public.rule r where r.id=%s""",
            (COST_RULE,),
        ).fetchone()
        if row != ("retired", 2, True, 0, 0):
            raise RuntimeError(f"legacy cost tombstone was rebound or activated: {row}")


def main() -> int:
    base = os.environ.get("CARR_CI_DATABASE_URL", "")
    if not base:
        return fail("CARR_CI_DATABASE_URL is required")
    try:
        assert_loopback_guard()
        loopback(base)
        # Create both siblings up front so no path can fall back to the CI DB.
        with sibling(base, "clean") as clean, sibling(base, "mutated") as mutated:
            load_predecessor(clean)
            load_predecessor(mutated)
            for dsn in (clean, mutated):
                with psycopg.connect(dsn) as con, con.cursor() as cur:
                    ledger_row = cur.execute(
                        "select count(*) from public.schema_migrations "
                        "where filename in ('0228_atomic_rule_lifecycle_forward_upgrade.sql',"
                        "                   '0247_system_rule_scope_binding.sql')"
                    ).fetchone()
                    if ledger_row is None:
                        raise RuntimeError("pinned snapshot ledger query returned no row")
                    ledger = ledger_row[0]
                    if ledger:
                        raise RuntimeError("pinned snapshot is not a pre-0228 predecessor")
            seed_fixture(clean, mutate_retirement=False)
            clean_result = migrate(clean)
            if clean_result.returncode:
                raise RuntimeError("clean predecessor migration failed: " + (clean_result.stderr or clean_result.stdout)[-400:])
            assert_tombstone(clean)
            seed_fixture(mutated, mutate_retirement=True)
            mutated_result = migrate(mutated)
            if mutated_result.returncode == 0:
                raise RuntimeError("mutated retirement rationale unexpectedly passed 0228")
            detail = (mutated_result.stderr or mutated_result.stdout).lower()
            if "legacy retired system cost rule does not match its exact retirement tombstone preimage" not in detail:
                raise RuntimeError("mutated retirement rationale refused for the wrong reason: " + detail[-400:])
            with psycopg.connect(mutated) as con, con.cursor() as cur:
                rows: dict[str, bool] = dict(cur.execute(
                    """select filename,true from public.schema_migrations
                        where filename in ('0228_atomic_rule_lifecycle_forward_upgrade.sql',
                                           '0247_system_rule_scope_binding.sql')"""
                ).fetchall())
                if rows:
                    raise RuntimeError(f"0228 refusal did not preserve the exact migration boundary: {rows}")
        print("PASS: exact retired tombstone passes 0228/0247; rationale drift refuses 0228")
        return 0
    except Exception as exc:
        return fail(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
