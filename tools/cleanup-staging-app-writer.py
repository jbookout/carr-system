#!/usr/bin/env python3
"""Delete only the accidental provider-managed isolated-staging app_writer.

This is intentionally separate from provisioning.  Provider deletion cannot be
rolled back, so ``--apply`` first persists the future SQL-role credential,
quiesces the staging Worker onto that not-yet-valid credential, proves two
zero-session reads, and repeats the complete provider/database fingerprint.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import importlib.util
import json
import os
import pathlib
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Mapping, Protocol, Sequence

try:
    import psycopg
except ImportError:
    sys.exit("staging app_writer cleanup requires the repo virtualenv (psycopg)")


REPO = pathlib.Path(__file__).resolve().parents[1]


def load_module(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


provision = load_module("cleanup_staging_provision_contract", REPO / "tools/provision-staging-app-writer.py")
credential = load_module("cleanup_staging_credential", REPO / "tools/staging_database_credential.py")
db_tap = provision.db_tap


class ProviderScopeLike(Protocol):
    project_id: str
    branch_id: str
    endpoint_id: str
    endpoint_host: str
    port: int
    database: str

APP_ROLE = "app_writer"
WRITER_SECRET = "DATABASE_URL_WRITER"
READER_SECRET = "DATABASE_URL_READER"
LOCK_KEY = 7301961134306001
WRANGLER = REPO / "mcp-server/node_modules/.bin/wrangler"
WRANGLER_CONFIG = REPO / "mcp-server/wrangler.toml"
STAGING_WORKER_NAME = "carr-mcp-staging"
EXPECTED_PROVIDER_REACHABLE_ROLES = (
    "neon_superuser",
    "pg_create_subscription",
    "pg_maintain",
    "pg_monitor",
    "pg_read_all_data",
    "pg_read_all_settings",
    "pg_read_all_stats",
    "pg_signal_autovacuum_worker",
    "pg_signal_backend",
    "pg_stat_scan_tables",
    "pg_write_all_data",
)

Run = Callable[..., subprocess.CompletedProcess]


class CleanupRefusal(RuntimeError):
    """The live role is not exactly the one accidental role approved for deletion."""


@dataclass(frozen=True)
class ProviderManagedFingerprint:
    can_login: bool
    inherits_privileges: bool
    powerful_attributes: tuple[str, ...]
    role_config: tuple[str, ...]
    memberships: tuple[tuple[str, bool, bool, bool, str], ...]
    reachable_roles: tuple[str, ...]
    inbound_memberships: tuple[tuple[str, bool, bool, bool, str], ...]
    direct_acl_facts: tuple[tuple[str, str, str, bool], ...]
    owned_objects: tuple[tuple[str, str, str], ...]
    shared_dependencies: tuple[tuple[str, str, str, str], ...]
    reader_role_exists: bool
    active_sessions: int


@dataclass(frozen=True)
class CleanupState:
    scope: dict[str, Any]
    cleanup_fingerprint: str
    provider_role_sha256: str
    database_fingerprint_sha256: str
    phase: str


CLEANUP_STATE_PHASES = (
    "prepared", "reader_quiesced", "contained", "delete_intent", "delete_called",
    "provider_absent",
)


def _provider_timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,9})?(?:Z|[+-]\d\d:\d\d)", value
    ):
        raise CleanupRefusal(f"provider app_writer {field} is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CleanupRefusal(f"provider app_writer {field} is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise CleanupRefusal(f"provider app_writer {field} must be timezone-aware")
    return parsed


def exact_provider_role(payload: Any, scope: ProviderScopeLike) -> dict[str, Any]:
    """Pin neonctl 2.38.5's exact six-field bare role-list contract."""
    if not isinstance(payload, list) or not all(isinstance(row, dict) for row in payload):
        raise CleanupRefusal("provider role list must be its official bare array")
    matches = [row for row in payload if row.get("name") == APP_ROLE]
    if len(matches) != 1:
        raise CleanupRefusal("provider must contain exactly one app_writer")
    row = matches[0]
    expected_fields = {
        "authentication_method", "branch_id", "created_at",
        "name", "protected", "updated_at",
    }
    if set(row) != expected_fields:
        raise CleanupRefusal("provider app_writer row differs from the official list contract")
    if not isinstance(row["name"], str) or row["name"] != APP_ROLE:
        raise CleanupRefusal("provider role name is invalid")
    if not isinstance(row["branch_id"], str) or row["branch_id"] != scope.branch_id:
        raise CleanupRefusal("provider app_writer belongs to another branch")
    if (
        not isinstance(row["authentication_method"], str)
        or row["authentication_method"] != "password"
    ):
        raise CleanupRefusal("provider app_writer is not password-authenticated")
    if not isinstance(row["protected"], bool) or row["protected"] is not False:
        raise CleanupRefusal("provider app_writer is protected or has an invalid protection flag")
    _provider_timestamp(row["created_at"], "created_at")
    _provider_timestamp(row["updated_at"], "updated_at")
    return row


def validate_provider_managed_fingerprint(value: ProviderManagedFingerprint) -> None:
    expected = ProviderManagedFingerprint(
        can_login=True,
        inherits_privileges=True,
        powerful_attributes=("createdb", "createrole", "replication", "bypassrls"),
        role_config=(),
        memberships=(("neon_superuser", False, True, True, "cloud_admin"),),
        reachable_roles=EXPECTED_PROVIDER_REACHABLE_ROLES,
        inbound_memberships=(),
        direct_acl_facts=(),
        owned_objects=(),
        shared_dependencies=(),
        reader_role_exists=False,
        active_sessions=0,
    )
    if value != expected:
        raise CleanupRefusal(
            "app_writer is absent, in use, SQL-created, or outside the exact provider-managed fingerprint"
        )


def require_quiescent_reads(readings: Sequence[int]) -> None:
    if tuple(readings) != (0, 0):
        raise CleanupRefusal("exactly two separated zero-session reads are required")


def _run_suppressed(
    args: Sequence[str], *, run: Run, env: Mapping[str, str] | None = None,
    input_value: str | None = None,
) -> subprocess.CompletedProcess:
    try:
        result = run(
            list(args), input=input_value, capture_output=True, text=True, timeout=60,
            env=dict(env) if env is not None else None,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CleanupRefusal("external command outcome is uncertain; output suppressed") from exc
    if result.returncode != 0:
        raise CleanupRefusal(f"external command refused (rc={result.returncode}); output suppressed")
    return result


def quiesce_worker_database_secret(
    secret_name: str, future_uri: str, *, wrangler: str = str(WRANGLER), run: Run = subprocess.run,
    environ: Mapping[str, str] | None = None,
) -> None:
    if secret_name not in {READER_SECRET, WRITER_SECRET}:
        raise CleanupRefusal("cleanup can quiesce only the two reviewed staging database bindings")
    _run_suppressed(
        [wrangler, "secret", "put", secret_name,
         "--env", "staging", "--config", str(WRANGLER_CONFIG),
         "--name", STAGING_WORKER_NAME],
        run=run,
        env=provision.worker_environment(environ if environ is not None else os.environ),
        input_value=future_uri,
    )


def verify_worker_database_bindings(
    secret_names: Sequence[str],
    *, wrangler: str = str(WRANGLER), run: Run = subprocess.run,
    environ: Mapping[str, str] | None = None,
) -> None:
    result = _run_suppressed(
        [wrangler, "secret", "list", "--env", "staging",
         "--config", str(WRANGLER_CONFIG), "--name", STAGING_WORKER_NAME,
         "--format", "json"],
        run=run,
        env=provision.worker_environment(environ if environ is not None else os.environ),
    )
    try:
        payload = json.loads(result.stdout)
    except (TypeError, json.JSONDecodeError) as exc:
        raise CleanupRefusal("Worker secret readback was invalid; output suppressed") from exc
    if not isinstance(payload, list):
        raise CleanupRefusal("Worker database binding list did not have the reviewed shape")
    for secret_name in secret_names:
        if sum(isinstance(row, dict) and row.get("name") == secret_name for row in payload) != 1:
            raise CleanupRefusal(f"Worker {secret_name} binding did not read back exactly once")


def delete_provider_role_once(
    scope: ProviderScopeLike, *, neonctl: str, run: Run = subprocess.run,
    environ: Mapping[str, str],
) -> None:
    if scope.project_id == provision.PRODUCTION_PROJECT_ID or not scope.branch_id:
        raise CleanupRefusal("provider deletion target is not isolated staging")
    _run_suppressed(
        [neonctl, "roles", "delete", APP_ROLE, "--project-id", scope.project_id,
         "--branch", scope.branch_id, "--output", "json"],
        run=run, env=provision.provider_environment(environ),
    )


def provider_roles(
    scope: ProviderScopeLike, *, neonctl: str, run: Run, environ: Mapping[str, str],
) -> list[dict[str, Any]]:
    result = _run_suppressed(
        [neonctl, "roles", "list", "--project-id", scope.project_id,
         "--branch", scope.branch_id, "--output", "json"],
        run=run, env=provision.provider_environment(environ),
    )
    try:
        payload = json.loads(result.stdout)
    except (TypeError, json.JSONDecodeError) as exc:
        raise CleanupRefusal("provider role list returned invalid JSON; output suppressed") from exc
    if not isinstance(payload, list) or not all(isinstance(row, dict) for row in payload):
        raise CleanupRefusal("provider role list must be its official bare array")
    return payload


def _memberships_with_grantor(cur: Any, role: str, *, inbound: bool) -> tuple[tuple[str, bool, bool, bool, str], ...]:
    if inbound:
        name_expr = "member.rolname"
        predicate = "granted.rolname=%s"
    else:
        name_expr = "granted.rolname"
        predicate = "member.rolname=%s"
    cur.execute(
        f"""select {name_expr},m.admin_option,m.inherit_option,m.set_option,grantor.rolname
              from pg_auth_members m
              join pg_roles granted on granted.oid=m.roleid
              join pg_roles member on member.oid=m.member
              join pg_roles grantor on grantor.oid=m.grantor
             where {predicate} order by 1""",
        (role,),
    )
    return tuple((str(name), bool(admin), bool(inherit), bool(can_set), str(grantor))
                 for name, admin, inherit, can_set, grantor in cur.fetchall())


def collect_provider_managed_fingerprint(cur: Any) -> ProviderManagedFingerprint:
    cur.execute(
        "select datname from pg_database where not datistemplate order by datname"
    )
    databases = tuple(str(row[0]) for row in cur.fetchall())
    cur.execute("select current_database()")
    if databases != (str(cur.fetchone()[0]),):
        raise CleanupRefusal("cleanup requires exactly the one pinned connectable staging database")
    authority = provision.collect_role_authority(cur, APP_ROLE)
    cur.execute(
        """select d.dbid::text,d.classid::regclass::text,d.objid::text,d.deptype::text
             from pg_shdepend d join pg_roles r on r.oid=d.refobjid
            where d.refclassid='pg_authid'::regclass and r.rolname=%s
            order by 1,2,3,4""",
        (APP_ROLE,),
    )
    shared_dependencies = tuple(
        (str(database), str(catalog), str(object_id), str(dependency_type))
        for database, catalog, object_id, dependency_type in cur.fetchall()
    )
    cur.execute(
        """with target as (select oid from pg_roles where rolname=%s)
           select 'tablespace',t.spcname,lower(a.privilege_type),a.is_grantable
             from pg_tablespace t cross join lateral aclexplode(t.spcacl) a
             join target on target.oid=a.grantee
           union all
           select 'parameter',p.parname,lower(a.privilege_type),a.is_grantable
             from pg_parameter_acl p cross join lateral aclexplode(p.paracl) a
             join target on target.oid=a.grantee
           order by 1,2,3,4""",
        (APP_ROLE,),
    )
    cluster_acl = tuple((str(kind), str(identity), str(privilege), bool(grantable))
                        for kind, identity, privilege, grantable in cur.fetchall())
    cur.execute("select count(*) from pg_stat_activity where usename=%s", (APP_ROLE,))
    sessions = int(cur.fetchone()[0])
    cur.execute("select exists(select 1 from pg_roles where rolname='app_reader')")
    reader_role_exists = cur.fetchone() == (True,)
    return ProviderManagedFingerprint(
        authority.can_login,
        authority.inherits_privileges,
        authority.powerful_attributes,
        authority.role_config,
        _memberships_with_grantor(cur, APP_ROLE, inbound=False),
        authority.reachable_roles,
        _memberships_with_grantor(cur, APP_ROLE, inbound=True),
        tuple(sorted(authority.direct_acl_facts + cluster_acl)),
        authority.owned_objects,
        shared_dependencies,
        reader_role_exists,
        sessions,
    )


def validate_worker_source_contract() -> None:
    staging_script = (REPO / "bin/staging-secrets.sh").read_text(encoding="utf-8")
    if any(token in staging_script for token in (
        "connection-string", "STAGING_DSN", 'put "DATABASE_URL_WRITER"',
        'put "DATABASE_URL_READER"',
    )):
        raise CleanupRefusal("staging-secrets still owns or owner-falls-back the writer secret")
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((REPO / "mcp-server/src").glob("*.js"))
    )
    if "DATABASE_URL_WRITER" not in source:
        raise CleanupRefusal("Worker source no longer exposes the reviewed writer binding")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="quiesce and delete exact accidental role")
    parser.add_argument(
        "--expected-fingerprint",
        help="exact SHA-256 printed by a fresh dry-run; required with --apply",
    )
    return parser.parse_args(argv)


def cleanup_fingerprint(
    scope: ProviderScopeLike, provider_row: Mapping[str, Any],
    database: ProviderManagedFingerprint, credential_states: Mapping[str, str],
) -> tuple[str, dict[str, Any]]:
    states = dict(credential_states)
    if set(states) != {"reader", "writer"} or not set(states.values()).issubset(
        {"absent", "pending"}
    ):
        raise CleanupRefusal("cleanup requires both credentials to be absent or pending")
    receipt = {
        "project_id": scope.project_id,
        "branch_id": scope.branch_id,
        "endpoint_id": scope.endpoint_id,
        "endpoint_host": scope.endpoint_host,
        "port": scope.port,
        "database": scope.database,
        "provider_role": dict(provider_row),
        "provider_role_sha256": hashlib.sha256(
            json.dumps(dict(provider_row), sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "database_fingerprint_sha256": hashlib.sha256(
            json.dumps(dataclasses.asdict(database), sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "pending_credential_states": states,
    }
    digest = hashlib.sha256(
        json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return digest, receipt


def cleanup_state_from_receipt(
    scope: ProviderScopeLike, cleanup_digest: str, receipt: Mapping[str, Any], phase: str,
) -> CleanupState:
    if phase not in CLEANUP_STATE_PHASES:
        raise CleanupRefusal("cleanup state phase is invalid")
    return CleanupState(
        scope={
            "project_id": scope.project_id, "branch_id": scope.branch_id,
            "endpoint_id": scope.endpoint_id, "endpoint_host": scope.endpoint_host,
            "port": scope.port, "database": scope.database,
        },
        cleanup_fingerprint=cleanup_digest,
        provider_role_sha256=str(receipt["provider_role_sha256"]),
        database_fingerprint_sha256=str(receipt["database_fingerprint_sha256"]),
        phase=phase,
    )


def validate_cleanup_state(state: CleanupState, scope: ProviderScopeLike) -> None:
    expected_scope = {
        "project_id": scope.project_id, "branch_id": scope.branch_id,
        "endpoint_id": scope.endpoint_id, "endpoint_host": scope.endpoint_host,
        "port": scope.port, "database": scope.database,
    }
    if (
        state.scope != expected_scope or state.phase not in CLEANUP_STATE_PHASES
        or not re.fullmatch(r"[0-9a-f]{64}", state.cleanup_fingerprint)
        or not re.fullmatch(r"[0-9a-f]{64}", state.provider_role_sha256)
        or not re.fullmatch(r"[0-9a-f]{64}", state.database_fingerprint_sha256)
    ):
        raise CleanupRefusal("durable cleanup state is invalid or outside the pinned scope")


def read_cleanup_state(path: pathlib.Path, scope: ProviderScopeLike) -> CleanupState | None:
    if credential._safe_lstat(path) is None:
        return None
    try:
        payload = json.loads(credential._secure_read(path))
        if not isinstance(payload, dict) or set(payload) != {
            "scope", "cleanup_fingerprint", "provider_role_sha256",
            "database_fingerprint_sha256", "phase",
        }:
            raise ValueError("wrong fields")
        state = CleanupState(**payload)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise CleanupRefusal("durable cleanup state has the wrong shape") from exc
    validate_cleanup_state(state, scope)
    return state


def write_cleanup_state(
    path: pathlib.Path, state: CleanupState,
    *, boundary: Callable[[str], None] = lambda _boundary: None,
) -> None:
    credential._validate_private_directory(path.parent)
    temporary = pathlib.Path(str(path) + ".new")
    if credential._safe_lstat(temporary) is not None:
        temporary.unlink()
        credential._fsync_directory(path.parent)
    raw = (json.dumps(dataclasses.asdict(state), sort_keys=True, separators=(",", ":")) + "\n").encode()
    descriptor = os.open(
        temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600,
    )
    try:
        os.fchmod(descriptor, 0o600)
        boundary("after_open")
        offset = 0
        while offset < len(raw):
            written = os.write(descriptor, raw[offset:])
            if written <= 0:
                raise CleanupRefusal("durable cleanup state write was incomplete")
            offset += written
        boundary("after_write")
        os.fsync(descriptor)
        boundary("after_fsync")
    except Exception:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise
    finally:
        os.close(descriptor)
    os.replace(temporary, path)
    boundary("after_publish")
    credential._fsync_directory(path.parent)


def remove_cleanup_state(path: pathlib.Path) -> None:
    if credential._safe_lstat(path) is None:
        return
    path.unlink()
    credential._fsync_directory(path.parent)


def prepare_containment_credentials(
    owner_dsn: Any, *, config_root: pathlib.Path | None = None,
) -> dict[str, Any]:
    prepared: dict[str, Any] = {}
    for label in ("reader", "writer"):
        profile = credential.profile(label, config_root=config_root)
        stored = credential.prepare_pending(
            profile.paths, key=profile.key, role_name=profile.role_name,
            owner_uri=owner_dsn.value, expected_endpoint=owner_dsn.endpoint,
            expected_port=owner_dsn.port, expected_database=owner_dsn.database,
        )
        if stored.state != "pending":
            raise CleanupRefusal("cleanup requires pending reader and writer credentials")
        prepared[label] = stored
    return prepared


def nonsecret_credential_state(paths: Any) -> str:
    return credential.file_state(paths)


def require_expected_fingerprint(*, apply: bool, expected: str | None, actual: str) -> None:
    if apply and (not expected or expected != actual):
        raise CleanupRefusal("--apply requires the exact fingerprint from a fresh dry-run")


def poll_provider_role_absent(
    scope: ProviderScopeLike, *, neonctl: str, run: Run, environ: Mapping[str, str],
    attempts: int = 8, sleep: Callable[[float], None] = time.sleep,
) -> None:
    for index in range(attempts):
        rows = provider_roles(scope, neonctl=neonctl, run=run, environ=environ)
        if not any(row.get("name") == APP_ROLE for row in rows):
            return
        if index + 1 < attempts:
            sleep(1)
    raise CleanupRefusal("provider still reports app_writer after bounded deletion polling")


def poll_provider_and_database_absent(
    scope: ProviderScopeLike, cur: Any, *, neonctl: str, run: Run,
    environ: Mapping[str, str], attempts: int = 12,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    for index in range(attempts):
        rows = provider_roles(scope, neonctl=neonctl, run=run, environ=environ)
        provider_present = any(row.get("name") == APP_ROLE for row in rows)
        cur.execute("select to_regrole(%s)", (APP_ROLE,))
        database_present = cur.fetchone() != (None,)
        if not provider_present and not database_present:
            return
        if index + 1 < attempts:
            sleep(1)
    raise CleanupRefusal("provider/database app_writer deletion did not converge boundedly")


def issue_provider_delete_from_intent(
    scope: ProviderScopeLike, state_path: pathlib.Path, state: CleanupState,
    *, neonctl: str, run: Run, environ: Mapping[str, str],
    verify_present: Callable[[Sequence[dict[str, Any]]], None],
    attempts: int = 2, sleep: Callable[[float], None] = time.sleep,
) -> CleanupState:
    if state.phase != "delete_intent":
        raise CleanupRefusal("provider delete requires a durable delete_intent phase")
    for index in range(attempts):
        try:
            delete_provider_role_once(
                scope, neonctl=neonctl, run=run, environ=environ,
            )
            break
        except CleanupRefusal:
            # A command failure may have happened before Neon received it. Read
            # state, and retry only when the exact original target is freshly
            # revalidated; a changed/recreated target must refuse.
            rows = provider_roles(
                scope, neonctl=neonctl, run=run, environ=environ,
            )
            if not any(row.get("name") == APP_ROLE for row in rows):
                break
            verify_present(rows)
            if index + 1 == attempts:
                raise CleanupRefusal("provider delete did not complete after bounded exact retries")
            sleep(1)
    called = dataclasses.replace(state, phase="delete_called")
    write_cleanup_state(state_path, called)
    return called


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        provision.reject_unsafe_environment(os.environ)
        validate_worker_source_contract()
        scope = provision.resolve_provider_scope(
            neonctl=db_tap.NEONCTL, environ=os.environ,
        )
        owner_dsn = provision.provider_dsn(
            scope, "neondb_owner", neonctl=db_tap.NEONCTL, environ=os.environ,
        )
        provision.verify_provider_scope(
            scope, neonctl=db_tap.NEONCTL, environ=os.environ,
        )
        reader = credential.profile("reader")
        writer = credential.profile("writer")
        lock_path = writer.paths.final.parent / ".staging-role-operation.lock"
        state_path = writer.paths.final.parent / ".staging-app-writer-cleanup.json"
        with credential.exclusive_lock(lock_path):
            conn = psycopg.connect(owner_dsn.value, autocommit=True)
            try:
                cur = conn.cursor()
                cur.execute("select pg_advisory_lock(%s)", (LOCK_KEY,))
                credential_states = {
                    "reader": nonsecret_credential_state(reader.paths),
                    "writer": nonsecret_credential_state(writer.paths),
                }
                if not set(credential_states.values()).issubset({"absent", "pending"}):
                    raise CleanupRefusal("cleanup refuses final or ambiguous staging credentials")
                state = read_cleanup_state(state_path, scope)
                roles = provider_roles(
                    scope, neonctl=db_tap.NEONCTL, run=subprocess.run, environ=os.environ,
                )
                provider_present = any(row.get("name") == APP_ROLE for row in roles)

                if state is None:
                    if not provider_present:
                        raise CleanupRefusal("provider app_writer is absent without durable cleanup state")
                    provider_row = exact_provider_role(roles, scope)
                    first = collect_provider_managed_fingerprint(cur)
                    validate_provider_managed_fingerprint(first)
                    digest, receipt = cleanup_fingerprint(
                        scope, provider_row, first, credential_states
                    )
                    if not args.apply:
                        print(json.dumps({
                            "environment": "staging", "state": "dry_run", "role": APP_ROLE,
                            "cleanup_fingerprint": digest, "evidence": receipt,
                        }, sort_keys=True))
                        return 0
                    require_expected_fingerprint(
                        apply=True, expected=args.expected_fingerprint, actual=digest
                    )
                    pending = prepare_containment_credentials(owner_dsn)
                    state = cleanup_state_from_receipt(scope, digest, receipt, "prepared")
                    write_cleanup_state(state_path, state)
                else:
                    digest = state.cleanup_fingerprint
                    receipt = {
                        "provider_role_sha256": state.provider_role_sha256,
                        "database_fingerprint_sha256": state.database_fingerprint_sha256,
                    }
                    if not args.apply:
                        print(json.dumps({
                            "environment": "staging", "state": "resume", "role": APP_ROLE,
                            "cleanup_fingerprint": digest, "phase": state.phase,
                        }, sort_keys=True))
                        return 0
                    require_expected_fingerprint(
                        apply=True, expected=args.expected_fingerprint, actual=digest
                    )
                    if credential_states != {"reader": "pending", "writer": "pending"}:
                        raise CleanupRefusal("cleanup resume requires both exact pending credentials")
                    pending = prepare_containment_credentials(owner_dsn)

                if not provider_present:
                    if state.phase not in {"delete_intent", "delete_called", "provider_absent"}:
                        raise CleanupRefusal("provider absence predates an authorized delete boundary")
                    if state.phase != "provider_absent":
                        state = dataclasses.replace(state, phase="provider_absent")
                        write_cleanup_state(state_path, state)
                    poll_provider_and_database_absent(
                        scope, cur, neonctl=db_tap.NEONCTL, run=subprocess.run,
                        environ=os.environ,
                    )
                    remove_cleanup_state(state_path)
                    print(json.dumps({"environment": "staging", "state": "deleted", "role": APP_ROLE}))
                    return 0

                provider_row = exact_provider_role(roles, scope)
                first = collect_provider_managed_fingerprint(cur)
                validate_provider_managed_fingerprint(first)
                if state is None:
                    raise CleanupRefusal("durable cleanup state disappeared before target validation")
                authorized_state = state
                current_digest, current_receipt = cleanup_fingerprint(
                    scope, provider_row, first, {"reader": "pending", "writer": "pending"}
                )
                if (
                    current_receipt["provider_role_sha256"] != state.provider_role_sha256
                    or current_receipt["database_fingerprint_sha256"]
                    != state.database_fingerprint_sha256
                ):
                    raise CleanupRefusal("cleanup target differs from its durable authorized fingerprint")

                def verify_original_target(rows_now: Sequence[dict[str, Any]]) -> None:
                    row_now = exact_provider_role(rows_now, scope)
                    database_now = collect_provider_managed_fingerprint(cur)
                    validate_provider_managed_fingerprint(database_now)
                    row_digest = hashlib.sha256(
                        json.dumps(row_now, sort_keys=True, separators=(",", ":")).encode()
                    ).hexdigest()
                    database_digest = hashlib.sha256(
                        json.dumps(
                            dataclasses.asdict(database_now), sort_keys=True, separators=(",", ":")
                        ).encode()
                    ).hexdigest()
                    if (
                        row_digest != authorized_state.provider_role_sha256
                        or database_digest != authorized_state.database_fingerprint_sha256
                    ):
                        raise CleanupRefusal("provider role changed before bounded delete retry")

                if state.phase == "provider_absent":
                    raise CleanupRefusal("provider app_writer reappeared after confirmed absence")
                if state.phase == "delete_called":
                    poll_provider_and_database_absent(
                        scope, cur, neonctl=db_tap.NEONCTL, run=subprocess.run,
                        environ=os.environ,
                    )
                else:
                    if state.phase == "delete_intent":
                        state = issue_provider_delete_from_intent(
                            scope, state_path, state, neonctl=db_tap.NEONCTL,
                            run=subprocess.run, environ=os.environ,
                            verify_present=verify_original_target,
                        )
                        poll_provider_and_database_absent(
                            scope, cur, neonctl=db_tap.NEONCTL, run=subprocess.run,
                            environ=os.environ,
                        )
                    else:
                        provision.verify_provider_scope(
                            scope, neonctl=db_tap.NEONCTL, environ=os.environ,
                        )
                        quiesce_worker_database_secret(READER_SECRET, pending["reader"].value)
                        state = dataclasses.replace(state, phase="reader_quiesced")
                        write_cleanup_state(state_path, state)
                        provision.verify_provider_scope(
                            scope, neonctl=db_tap.NEONCTL, environ=os.environ,
                        )
                        quiesce_worker_database_secret(WRITER_SECRET, pending["writer"].value)
                        verify_worker_database_bindings((READER_SECRET, WRITER_SECRET))
                        state = dataclasses.replace(state, phase="contained")
                        write_cleanup_state(state_path, state)
                        provision.verify_provider_scope(
                            scope, neonctl=db_tap.NEONCTL, environ=os.environ,
                        )
                        readings: list[int] = []
                        for index in range(2):
                            cur.execute(
                                "select count(*) from pg_stat_activity where usename=%s", (APP_ROLE,)
                            )
                            session_row = cur.fetchone()
                            if session_row is None:
                                raise CleanupRefusal("active-session count returned no row")
                            readings.append(int(session_row[0]))
                            if index == 0:
                                time.sleep(2)
                        require_quiescent_reads(readings)
                        provision.verify_provider_scope(
                            scope, neonctl=db_tap.NEONCTL, environ=os.environ,
                        )
                        provider_row_now = exact_provider_role(
                            provider_roles(
                                scope, neonctl=db_tap.NEONCTL,
                                run=subprocess.run, environ=os.environ,
                            ),
                            scope,
                        )
                        second = collect_provider_managed_fingerprint(cur)
                        validate_provider_managed_fingerprint(second)
                        if provider_row_now != provider_row or second != first:
                            raise CleanupRefusal("cleanup target changed after quiescence")
                        state = dataclasses.replace(state, phase="delete_intent")
                        write_cleanup_state(state_path, state)
                        state = issue_provider_delete_from_intent(
                            scope, state_path, state, neonctl=db_tap.NEONCTL,
                            run=subprocess.run, environ=os.environ,
                            verify_present=verify_original_target,
                        )
                        poll_provider_and_database_absent(
                            scope, cur, neonctl=db_tap.NEONCTL, run=subprocess.run,
                            environ=os.environ,
                        )
                state = dataclasses.replace(state, phase="provider_absent")
                write_cleanup_state(state_path, state)
                remove_cleanup_state(state_path)
                print(json.dumps({"environment": "staging", "state": "deleted", "role": APP_ROLE}))
                return 0
            finally:
                try:
                    conn.execute("select pg_advisory_unlock(%s)", (LOCK_KEY,))
                finally:
                    conn.close()
    except (CleanupRefusal, credential.CredentialRefusal, provision.ProvisioningRefusal,
            psycopg.Error, OSError, ValueError, SystemExit) as exc:
        print("cleanup-staging-app-writer: REFUSED — " + provision.redact_error(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
