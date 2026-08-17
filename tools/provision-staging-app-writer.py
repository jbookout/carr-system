#!/usr/bin/env python3
"""Provision the one isolated-staging ``app_writer`` runtime identity.

Dry-run is the default. ``--apply`` is the only mutating path. There are no
project, branch, role, DSN, or grants arguments: those are the authority being
bounded, not caller choices.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import pathlib
import re
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import unquote, urlsplit

try:
    import psycopg
except ImportError:
    sys.exit("staging app_writer provisioner requires the repo virtualenv (psycopg)")


REPO = pathlib.Path(__file__).resolve().parents[1]
SCHEMA = REPO / "db/schema.sql"
MIGRATIONS = REPO / "migrations"
STAGING_PROJECT_NAME = "carr-staging"
STAGING_BRANCH_NAME = "main"
APP_ROLE = "app_writer"
BUNDLE_ROLE = "carr_writer"
EXPECTED_PROPOSAL_STATUS = (("pending", 10),)

MEMBERSHIP_SQL = (
    "grant carr_writer to app_writer with admin false",
    "grant carr_writer to app_writer with inherit true",
    "grant carr_writer to app_writer with set true",
)
STATEMENT_TIMEOUT_SQL = "alter role app_writer set statement_timeout = '60s'"
IDLE_TIMEOUT_SQL = (
    "alter role app_writer set idle_in_transaction_session_timeout = '120s'"
)

FORBIDDEN_ENV = (
    "CARR_BREAK_GLASS",
    "DATABASE_URL",
    "CARR_DB_OWNER_URL",
    "CARR_DB_WRITER_URL",
    "CARR_DB_READER_URL",
    "CARR_DB_JOBS_URL",
    "CARR_DB_AUTHORITY_URL",
    "PGHOST",
    "PGPORT",
    "PGDATABASE",
    "PGUSER",
    "PGPASSWORD",
    "PGSERVICE",
    "PGSERVICEFILE",
    "PGPASSFILE",
)

# This is an executable postflight matrix, not a second source of grants. The
# applied ACL source remains db/schema.sql's generated CARR GRANTS section.
REQUIRED_IMPORTER_PRIVILEGES: tuple[tuple[str, str], ...] = (
    ("public.actor", "SELECT"),
    ("public.doctrine_document", "SELECT"),
    ("public.doctrine_document", "INSERT"),
    ("public.doctrine_slug_alias", "SELECT"),
    ("public.doctrine_migration_batch", "SELECT"),
    ("public.doctrine_migration_batch", "INSERT"),
    ("public.doctrine_migration_batch", "UPDATE"),
    ("public.doctrine_review_policy", "SELECT"),
    ("public.doctrine_change_set", "SELECT"),
    ("public.doctrine_change_set", "INSERT"),
    ("public.doctrine_section", "SELECT"),
    ("public.doctrine_section", "INSERT"),
    ("public.doctrine_section", "UPDATE"),
    ("public.doctrine_revision", "SELECT"),
    ("public.doctrine_revision", "INSERT"),
    ("public.doctrine_meta", "SELECT"),
    ("public.doctrine_meta", "UPDATE"),
    ("public.doctrine_snapshot", "SELECT"),
    ("public.doctrine_snapshot", "INSERT"),
    ("public.doctrine_snapshot", "UPDATE"),
)


def load_module(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


db_tap = load_module("staging_app_writer_db_tap", REPO / "tools/db-tap.py")
PRODUCTION_PROJECT_ID = str(db_tap.PROJECTS["production"]["id"])
if not PRODUCTION_PROJECT_ID:
    raise RuntimeError("db-tap Production project pin is empty")
snapshot_grants = load_module(
    "staging_app_writer_snapshot_grants", REPO / "tools/schema_snapshot_grants.py"
)
seed = load_module(
    "staging_app_writer_seed_contract",
    REPO / "pipelines/staging_retrieval_doctrine_seed.py",
)


class ProvisioningRefusal(RuntimeError):
    """The requested action is outside the exact isolated-staging contract."""


@dataclass(frozen=True)
class ProviderScope:
    project_id: str
    branch_id: str


@dataclass(frozen=True)
class ScopedDsn:
    """A secret DSN bound to the immutable provider scope that produced it."""

    scope: ProviderScope
    role_name: str
    endpoint: str
    port: int
    database: str
    value: str = field(repr=False, compare=False)


@dataclass(frozen=True)
class ExpectedSeedState:
    proposal_status: tuple[tuple[str, int], ...]
    target_count: int
    batch_count: int


@dataclass(frozen=True)
class RoleAuthority:
    can_login: bool
    inherits_privileges: bool
    powerful_attributes: tuple[str, ...]
    role_config: tuple[str, ...]
    memberships: tuple[tuple[str, bool, bool, bool], ...]
    reachable_roles: tuple[str, ...]
    direct_acl_facts: tuple[tuple[str, str, str, bool], ...]
    owned_objects: tuple[tuple[str, str, str], ...]


@dataclass(frozen=True)
class AuthorityClosure:
    app_writer: RoleAuthority
    carr_writer: RoleAuthority


@dataclass(frozen=True)
class PostflightEvidence:
    session_user: str
    current_user: str
    statement_timeout_seconds: int
    idle_timeout_seconds: int
    authority: AuthorityClosure
    missing_privileges: tuple[str, ...]
    seed_state: ExpectedSeedState


Run = Callable[..., subprocess.CompletedProcess]
Connect = Callable[[str], Any]


ROLE_ACL_FACTS_SQL = """
with target as (select oid from pg_roles where rolname=%s)
select 'database',d.datname::text,lower(a.privilege_type),a.is_grantable
  from pg_database d cross join lateral aclexplode(d.datacl) a
  join target on target.oid=a.grantee
union all
select 'schema',n.nspname,lower(a.privilege_type),a.is_grantable
  from pg_namespace n cross join lateral aclexplode(n.nspacl) a
  join target on target.oid=a.grantee
union all
select case c.relkind when 'S' then 'sequence' else 'table' end,
       n.nspname||'.'||c.relname,lower(a.privilege_type),a.is_grantable
  from pg_class c join pg_namespace n on n.oid=c.relnamespace
  cross join lateral aclexplode(c.relacl) a join target on target.oid=a.grantee
union all
select 'column',n.nspname||'.'||c.relname||'('||att.attname||')',lower(a.privilege_type),a.is_grantable
  from pg_attribute att join pg_class c on c.oid=att.attrelid
  join pg_namespace n on n.oid=c.relnamespace
  cross join lateral aclexplode(att.attacl) a join target on target.oid=a.grantee
 where not att.attisdropped
union all
select 'function',n.nspname||'.'||p.proname||'('||oidvectortypes(p.proargtypes)||')',
       lower(a.privilege_type),a.is_grantable
  from pg_proc p join pg_namespace n on n.oid=p.pronamespace
  cross join lateral aclexplode(p.proacl) a join target on target.oid=a.grantee
union all
select 'type',n.nspname||'.'||t.typname,lower(a.privilege_type),a.is_grantable
  from pg_type t join pg_namespace n on n.oid=t.typnamespace
  cross join lateral aclexplode(t.typacl) a join target on target.oid=a.grantee
union all
select 'language',l.lanname,lower(a.privilege_type),a.is_grantable
  from pg_language l cross join lateral aclexplode(l.lanacl) a
  join target on target.oid=a.grantee
union all
select 'foreign_data_wrapper',f.fdwname,lower(a.privilege_type),a.is_grantable
  from pg_foreign_data_wrapper f cross join lateral aclexplode(f.fdwacl) a
  join target on target.oid=a.grantee
union all
select 'foreign_server',s.srvname,lower(a.privilege_type),a.is_grantable
  from pg_foreign_server s cross join lateral aclexplode(s.srvacl) a
  join target on target.oid=a.grantee
union all
select 'large_object',l.oid::text,lower(a.privilege_type),a.is_grantable
  from pg_largeobject_metadata l cross join lateral aclexplode(l.lomacl) a
  join target on target.oid=a.grantee
union all
select 'default_acl',coalesce(n.nspname,'*')||':'||d.defaclobjtype::text||':'||owner.rolname,
       lower(a.privilege_type),a.is_grantable
  from pg_default_acl d left join pg_namespace n on n.oid=d.defaclnamespace
  join pg_roles owner on owner.oid=d.defaclrole
  cross join lateral aclexplode(d.defaclacl) a join target on target.oid=a.grantee
order by 1,2,3,4
"""


def reject_unsafe_environment(environ: Mapping[str, str]) -> None:
    present = sorted(key for key in FORBIDDEN_ENV if environ.get(key))
    if present:
        raise ProvisioningRefusal(
            "break-glass and ambient database credentials are forbidden: "
            + ", ".join(present)
        )


def _rows(payload: Any, key: str) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict) and key in payload:
        rows = payload[key]
    else:
        raise ProvisioningRefusal(f"provider {key} response is missing its rows array")
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ProvisioningRefusal(f"provider {key} response has the wrong shape")
    return rows


def _provider_run(
    args: Sequence[str], *, run: Run = subprocess.run, env: Mapping[str, str] | None = None
) -> subprocess.CompletedProcess:
    try:
        result = run(
            list(args), capture_output=True, text=True, timeout=60,
            env=dict(env) if env is not None else None,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProvisioningRefusal(
            "provider command did not complete; all provider output suppressed"
        ) from exc
    if result.returncode != 0:
        # Provider role creation output may contain a generated password. Never
        # include stdout/stderr even on failure.
        raise ProvisioningRefusal(
            f"provider command failed without exposing output (rc={result.returncode})"
        )
    return result


def _provider_json(
    args: Sequence[str], *, run: Run = subprocess.run, env: Mapping[str, str] | None = None
) -> Any:
    result = _provider_run(args, run=run, env=env)
    try:
        return json.loads(result.stdout)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ProvisioningRefusal("provider returned non-JSON output; output suppressed") from exc


def provider_environment(environ: Mapping[str, str]) -> dict[str, str]:
    result = dict(environ)
    result["PATH"] = "/usr/local/opt/node@22/bin:/opt/homebrew/bin:" + environ.get("PATH", "")
    key = db_tap._neon_api_key()
    if key:
        result["NEON_API_KEY"] = key
    return result


def validate_provider_scope(
    projects: Sequence[dict[str, Any]], branches: Sequence[dict[str, Any]]
) -> ProviderScope:
    matches = [row for row in projects if row.get("name") == STAGING_PROJECT_NAME]
    if len(matches) != 1:
        raise ProvisioningRefusal(
            f"expected one {STAGING_PROJECT_NAME} project, found {len(matches)}"
        )
    project_id = str(matches[0].get("id") or "")
    if not project_id or project_id == PRODUCTION_PROJECT_ID:
        raise ProvisioningRefusal("staging resolved to an empty or Production project id")
    branch_matches = [row for row in branches if row.get("name") == STAGING_BRANCH_NAME]
    if len(branch_matches) != 1 or branch_matches[0].get("default") is not True:
        raise ProvisioningRefusal("staging must resolve to exactly one default main branch")
    branch_id = str(branch_matches[0].get("id") or "")
    branch_project = str(branch_matches[0].get("project_id") or "")
    if not branch_id or branch_project != project_id:
        raise ProvisioningRefusal(
            "staging main branch has no immutable id or belongs to another project"
        )
    return ProviderScope(project_id, branch_id)


def resolve_provider_scope(
    *, neonctl: str, run: Run = subprocess.run, environ: Mapping[str, str]
) -> ProviderScope:
    env = provider_environment(environ)
    project_payload = _provider_json(
        [neonctl, "projects", "list", "--org-id", db_tap.NEON_ORG, "--output", "json"],
        run=run, env=env,
    )
    projects = _rows(project_payload, "projects")
    matches = [row for row in projects if row.get("name") == STAGING_PROJECT_NAME]
    if len(matches) != 1:
        return validate_provider_scope(projects, [])
    project_id = str(matches[0].get("id") or "")
    if not project_id or project_id == PRODUCTION_PROJECT_ID:
        return validate_provider_scope(projects, [])
    branch_payload = _provider_json(
        [neonctl, "branches", "list", "--project-id", project_id, "--output", "json"],
        run=run, env=env,
    )
    return validate_provider_scope(projects, _rows(branch_payload, "branches"))


def verify_provider_scope(
    scope: ProviderScope, *, neonctl: str, run: Run = subprocess.run,
    environ: Mapping[str, str],
) -> None:
    """Reject a rename/rebuild race without replacing the resolved IDs."""
    env = provider_environment(environ)
    project_payload = _provider_json(
        [neonctl, "projects", "list", "--org-id", db_tap.NEON_ORG, "--output", "json"],
        run=run, env=env,
    )
    projects = _rows(project_payload, "projects")
    exact_projects = [
        row for row in projects
        if row.get("name") == STAGING_PROJECT_NAME and str(row.get("id") or "") == scope.project_id
    ]
    if len(exact_projects) != 1 or len(
        [row for row in projects if row.get("name") == STAGING_PROJECT_NAME]
    ) != 1:
        raise ProvisioningRefusal("staging project changed after immutable scope resolution")
    branch_payload = _provider_json(
        [neonctl, "branches", "list", "--project-id", scope.project_id, "--output", "json"],
        run=run, env=env,
    )
    branches = _rows(branch_payload, "branches")
    exact_branches = [
        row for row in branches
        if row.get("name") == STAGING_BRANCH_NAME
        and row.get("default") is True
        and str(row.get("id") or "") == scope.branch_id
        and str(row.get("project_id") or "") == scope.project_id
    ]
    if len(exact_branches) != 1 or len(
        [row for row in branches if row.get("name") == STAGING_BRANCH_NAME]
    ) != 1:
        raise ProvisioningRefusal("staging main branch changed after immutable scope resolution")


def ensure_provider_role(
    scope: ProviderScope, *, neonctl: str, run: Run = subprocess.run,
    environ: Mapping[str, str] | None = None,
) -> bool:
    """Create app_writer if absent. Return True only when this call created it."""
    if not scope.project_id or scope.project_id == PRODUCTION_PROJECT_ID or not scope.branch_id:
        raise ProvisioningRefusal("provider role target is not isolated staging")
    env = provider_environment(environ or os.environ)
    payload = _provider_json(
        [neonctl, "roles", "list", "--project-id", scope.project_id,
         "--branch", scope.branch_id, "--output", "json"],
        run=run, env=env,
    )
    roles = _rows(payload, "roles")
    matches = [row for row in roles if row.get("name") == APP_ROLE]
    if len(matches) > 1:
        raise ProvisioningRefusal("provider returned duplicate app_writer roles")
    if matches:
        return False
    # capture_output remains mandatory. The provider may return the generated
    # password in JSON; no caller receives or logs that payload.
    created = _provider_json(
        [neonctl, "roles", "create", "--project-id", scope.project_id,
         "--branch", scope.branch_id, "--name", APP_ROLE, "--output", "json"],
        run=run, env=env,
    )
    role = created.get("role") if isinstance(created, dict) else None
    if not isinstance(role, dict) or role.get("name") != APP_ROLE:
        raise ProvisioningRefusal("provider role-create response has the wrong shape")
    return True


def _dsn_parts(dsn: str) -> tuple[str, str, int, str]:
    try:
        parsed = urlsplit(dsn)
    except ValueError as exc:
        raise ProvisioningRefusal("provider returned an invalid DSN; value suppressed") from exc
    username = unquote(parsed.username or "")
    host = (parsed.hostname or "").lower().rstrip(".")
    try:
        port = parsed.port or 5432
    except ValueError as exc:
        raise ProvisioningRefusal("provider returned an invalid DSN port; value suppressed") from exc
    database = unquote(parsed.path.lstrip("/"))
    if parsed.scheme not in {"postgres", "postgresql"} or not username or not host or not database:
        raise ProvisioningRefusal("provider returned an incomplete DSN; value suppressed")
    return username, host, port, database


def provider_dsn(
    scope: ProviderScope, role_name: str, *, neonctl: str,
    run: Run = subprocess.run, environ: Mapping[str, str],
) -> ScopedDsn:
    """Derive one secret DSN from exact immutable project and branch IDs."""
    if (
        not scope.project_id
        or scope.project_id == PRODUCTION_PROJECT_ID
        or not scope.branch_id
        or role_name not in {"neondb_owner", APP_ROLE}
    ):
        raise ProvisioningRefusal("provider DSN target is outside isolated staging")
    result = _provider_run(
        [neonctl, "connection-string", scope.branch_id,
         "--project-id", scope.project_id, "--role-name", role_name],
        run=run, env=provider_environment(environ),
    )
    value = result.stdout.strip()
    if not value or "\n" in value or "\r" in value:
        raise ProvisioningRefusal("provider DSN response has the wrong shape; output suppressed")
    username, endpoint, port, database = _dsn_parts(value)
    if username != role_name:
        raise ProvisioningRefusal("provider DSN returned a different role; value suppressed")
    return ScopedDsn(scope, role_name, endpoint, port, database, value)


def validate_connection_scope(owner: ScopedDsn, writer: ScopedDsn) -> None:
    if owner.role_name != "neondb_owner" or writer.role_name != APP_ROLE:
        raise ProvisioningRefusal("staging connection roles are not owner/app_writer")
    if owner.scope != writer.scope:
        raise ProvisioningRefusal("owner/app_writer DSNs were not derived from one immutable scope")
    if owner.scope.project_id == PRODUCTION_PROJECT_ID or not owner.scope.branch_id:
        raise ProvisioningRefusal("connection scope is not isolated staging")
    if (owner.endpoint, owner.port, owner.database) != (
        writer.endpoint, writer.port, writer.database
    ):
        raise ProvisioningRefusal(
            "staging owner/app_writer endpoint, port, or database changed between DSN calls"
        )


def collect_seed_state(cur: Any) -> ExpectedSeedState:
    cur.execute("select status,count(*) from retrieval_proposal group by status order by status")
    proposal_status = tuple((str(status), int(count)) for status, count in cur.fetchall())
    target_count = 0
    for target in seed.TARGETS:
        cur.execute(
            """select (select count(*) from doctrine_document where slug=%s)
                    + (select count(*) from doctrine_slug_alias where alias_slug=%s)
                    + (select count(*) from doctrine_section s
                         join doctrine_document d on d.id=s.document_id
                        where d.slug=%s and s.section_key=%s)""",
            (target.slug, target.slug, target.slug, target.section_key),
        )
        target_count += int(cur.fetchone()[0])
    cur.execute("select count(*) from doctrine_migration_batch")
    batch_count = int(cur.fetchone()[0])
    return ExpectedSeedState(proposal_status, target_count, batch_count)


def validate_seed_state(state: ExpectedSeedState) -> None:
    if state.proposal_status != EXPECTED_PROPOSAL_STATUS:
        raise ProvisioningRefusal(
            f"staging retrieval proposals are not exactly 10 pending: {state.proposal_status}"
        )
    if state.target_count != 0 or state.batch_count != 0:
        raise ProvisioningRefusal(
            "staging doctrine seed targets or migration batches are no longer absent"
        )


def read_seed_state(dsn: str, *, connect: Connect = psycopg.connect) -> ExpectedSeedState:
    conn = connect(dsn)
    try:
        cur = conn.cursor()
        cur.execute("begin transaction read only")
        state = collect_seed_state(cur)
        conn.rollback()
        return state
    finally:
        conn.close()


def collect_role_acl_facts(
    cur: Any, role: str
) -> tuple[tuple[str, str, str, bool], ...]:
    cur.execute(ROLE_ACL_FACTS_SQL, (role,))
    return tuple(sorted((str(kind), str(identity), str(privilege), bool(grantable))
                        for kind, identity, privilege, grantable in cur.fetchall()))


def collect_memberships(cur: Any, role: str) -> tuple[tuple[str, bool, bool, bool], ...]:
    cur.execute(
        """select granted.rolname,m.admin_option,m.inherit_option,m.set_option
             from pg_auth_members m
             join pg_roles granted on granted.oid=m.roleid
             join pg_roles member on member.oid=m.member
            where member.rolname=%s
            order by granted.rolname""",
        (role,),
    )
    return tuple((str(name), bool(admin), bool(inherit), bool(can_set))
                 for name, admin, inherit, can_set in cur.fetchall())


def collect_reachable_roles(cur: Any, role: str) -> tuple[str, ...]:
    """Return every role reachable through any recursive membership edge."""
    cur.execute(
        """with recursive closure(roleid) as (
             select m.roleid
               from pg_auth_members m join pg_roles member on member.oid=m.member
              where member.rolname=%s
             union
             select m.roleid from pg_auth_members m join closure c on m.member=c.roleid
           )
           select r.rolname from closure c join pg_roles r on r.oid=c.roleid
           order by r.rolname""",
        (role,),
    )
    return tuple(str(row[0]) for row in cur.fetchall())


def collect_owned_objects(cur: Any, role: str) -> tuple[tuple[str, str, str], ...]:
    """Use PostgreSQL's shared dependency ledger for cluster-wide ownership."""
    cur.execute(
        """select d.dbid::text,d.classid::regclass::text,d.objid::text
             from pg_shdepend d join pg_roles r on r.oid=d.refobjid
            where d.refclassid='pg_authid'::regclass and d.deptype='o'
              and r.rolname=%s
            order by 1,2,3""",
        (role,),
    )
    return tuple((str(database), str(catalog), str(object_id))
                 for database, catalog, object_id in cur.fetchall())


def collect_role_authority(cur: Any, role: str) -> RoleAuthority:
    cur.execute(
        """select rolcanlogin,rolinherit,rolsuper,rolcreatedb,rolcreaterole,
                  rolreplication,rolbypassrls,rolconfig
             from pg_roles where rolname=%s""",
        (role,),
    )
    row = cur.fetchone()
    if row is None:
        raise ProvisioningRefusal(f"required role {role} disappeared")
    can_login, inherits, *rest = row
    *attribute_flags, config = rest
    attribute_names = (
        "superuser", "createdb", "createrole", "replication", "bypassrls"
    )
    powerful = tuple(name for name, enabled in zip(attribute_names, attribute_flags)
                     if bool(enabled))
    role_config = tuple(sorted(str(item) for item in (config or ())))
    return RoleAuthority(
        bool(can_login), bool(inherits), powerful, role_config,
        collect_memberships(cur, role), collect_reachable_roles(cur, role),
        collect_role_acl_facts(cur, role), collect_owned_objects(cur, role),
    )


def collect_authority_closure(cur: Any) -> AuthorityClosure:
    return AuthorityClosure(
        collect_role_authority(cur, APP_ROLE),
        collect_role_authority(cur, BUNDLE_ROLE),
    )


def _role_config_keys(config: Sequence[str]) -> tuple[str, ...]:
    keys: list[str] = []
    for item in config:
        if "=" not in item:
            raise ProvisioningRefusal("role configuration has an invalid shape")
        key = item.split("=", 1)[0]
        if not re.fullmatch(r"[a-z_][a-z0-9_]*", key) or key in keys:
            raise ProvisioningRefusal("role configuration has an invalid or duplicate key")
        keys.append(key)
    return tuple(sorted(keys))


def validate_authority_closure(
    authority: AuthorityClosure, canonical_grants: Sequence[str], *, exact: bool
) -> None:
    """Refuse every authority path outside the generated carr_writer ACLs."""
    app = authority.app_writer
    bundle = authority.carr_writer
    if not app.can_login or not app.inherits_privileges or app.powerful_attributes:
        raise ProvisioningRefusal("app_writer is not a plain inheriting LOGIN role")
    if bundle.can_login or not bundle.inherits_privileges or bundle.powerful_attributes:
        raise ProvisioningRefusal("carr_writer is not a plain NOLOGIN privilege bundle")
    if app.owned_objects or bundle.owned_objects:
        raise ProvisioningRefusal("app_writer/carr_writer must not own database objects")
    if app.direct_acl_facts:
        raise ProvisioningRefusal("app_writer has forbidden direct object ACLs")
    if bundle.memberships or bundle.reachable_roles:
        raise ProvisioningRefusal("carr_writer inherits authority from another role")
    if bundle.role_config:
        raise ProvisioningRefusal("carr_writer has forbidden role configuration")

    allowed_config_keys = (
        "idle_in_transaction_session_timeout", "statement_timeout"
    )
    config_keys = _role_config_keys(app.role_config)
    if any(key not in allowed_config_keys for key in config_keys):
        raise ProvisioningRefusal("app_writer has role configuration outside the timeout allowlist")

    if exact:
        if app.role_config != tuple(sorted((
            "idle_in_transaction_session_timeout=120s",
            "statement_timeout=60s",
        ))):
            raise ProvisioningRefusal("app_writer role configuration is not exactly the timeout allowlist")
        if app.memberships != ((BUNDLE_ROLE, False, True, True),):
            raise ProvisioningRefusal(
                "app_writer membership must be exactly carr_writer ADMIN FALSE/INHERIT TRUE/SET TRUE"
            )
        if app.reachable_roles != (BUNDLE_ROLE,):
            raise ProvisioningRefusal("app_writer has unexpected recursively reachable roles")
    else:
        if any(name != BUNDLE_ROLE for name, *_options in app.memberships):
            raise ProvisioningRefusal("reused app_writer already belongs to another role")
        if any(name != BUNDLE_ROLE for name in app.reachable_roles):
            raise ProvisioningRefusal("reused app_writer already reaches another role")

    expected_acl = set(snapshot_grants.acl_facts(canonical_grants))
    actual_acl = set(bundle.direct_acl_facts)
    if actual_acl - expected_acl:
        raise ProvisioningRefusal(
            "carr_writer has excess or grantable authority outside the canonical snapshot"
        )
    if exact and expected_acl - actual_acl:
        raise ProvisioningRefusal("carr_writer is missing canonical snapshot authority")


def apply_database_provisioning(
    conn: Any, grants: Sequence[str], *, commit: bool = True
) -> None:
    """Apply all ACL/identity changes in one owner transaction."""
    cur = conn.cursor()
    try:
        # A reused provider role may already be unsafe. Refuse all non-convergent
        # authority before adding anything, then prove the exact closure again
        # after repair and before the transaction is allowed to commit.
        validate_authority_closure(
            collect_authority_closure(cur), grants, exact=False
        )
        for statement in grants:
            cur.execute(statement)
        for statement in MEMBERSHIP_SQL:
            cur.execute(statement)
        cur.execute(STATEMENT_TIMEOUT_SQL)
        cur.execute(IDLE_TIMEOUT_SQL)
        validate_authority_closure(
            collect_authority_closure(cur), grants, exact=True
        )
        if commit:
            conn.commit()
    except Exception:
        conn.rollback()
        raise


def collect_postflight(
    dsn: str, canonical_grants: Sequence[str], *, connect: Connect = psycopg.connect
) -> PostflightEvidence:
    conn = connect(dsn)
    try:
        cur = conn.cursor()
        cur.execute("begin transaction read only")
        cur.execute(
            """select session_user,current_user,
                      extract(epoch from current_setting('statement_timeout')::interval)::integer,
                      extract(epoch from current_setting('idle_in_transaction_session_timeout')::interval)::integer"""
        )
        session_user, current_user, statement_seconds, idle_seconds = cur.fetchone()
        authority = collect_authority_closure(cur)
        validate_authority_closure(authority, canonical_grants, exact=True)
        missing: list[str] = []
        for relation, privilege in REQUIRED_IMPORTER_PRIVILEGES:
            cur.execute(
                "select has_table_privilege(current_user,%s,%s)",
                (relation, privilege),
            )
            if cur.fetchone() != (True,):
                missing.append(f"{relation}.{privilege}")
        state = collect_seed_state(cur)
        conn.rollback()
        return PostflightEvidence(
            str(session_user), str(current_user), int(statement_seconds), int(idle_seconds),
            authority, tuple(missing), state,
        )
    finally:
        conn.close()


def validate_postflight(
    evidence: PostflightEvidence, before: ExpectedSeedState,
    canonical_grants: Sequence[str],
) -> None:
    if evidence.session_user != APP_ROLE or evidence.current_user != APP_ROLE:
        raise ProvisioningRefusal("postflight did not authenticate as app_writer")
    if evidence.statement_timeout_seconds != 60 or evidence.idle_timeout_seconds != 120:
        raise ProvisioningRefusal("app_writer role timeouts are not exactly 60s/120s")
    validate_authority_closure(evidence.authority, canonical_grants, exact=True)
    if evidence.missing_privileges:
        raise ProvisioningRefusal(
            "app_writer is missing importer privileges: " + ", ".join(evidence.missing_privileges)
        )
    validate_seed_state(evidence.seed_state)
    if evidence.seed_state != before:
        raise ProvisioningRefusal("provisioning changed proposals, doctrine targets, or batches")


def redact_error(exc: BaseException) -> str:
    text = str(exc)
    text = re.sub(r"postgres(?:ql)?://\S+", "[DSN REDACTED]", text)
    text = re.sub(r"https?://\S+", "[URL REDACTED]", text)
    return text[:300]


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="create/reuse the staging role and apply canonical ACLs")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        reject_unsafe_environment(os.environ)
        grants = snapshot_grants.load_current_grants_to_role(
            SCHEMA, MIGRATIONS, BUNDLE_ROLE
        )
        scope = resolve_provider_scope(
            neonctl=db_tap.NEONCTL, environ=os.environ,
        )
        owner_dsn = provider_dsn(
            scope, "neondb_owner", neonctl=db_tap.NEONCTL, environ=os.environ,
        )
        before = read_seed_state(owner_dsn.value)
        validate_seed_state(before)
        if not args.apply:
            print(json.dumps({
                "environment": "staging", "project": STAGING_PROJECT_NAME,
                "branch": STAGING_BRANCH_NAME, "state": "dry_run",
                "canonical_carr_writer_grants": len(grants),
                "proposal_status": dict(before.proposal_status),
                "target_count": before.target_count, "batch_count": before.batch_count,
            }, sort_keys=True))
            return 0

        verify_provider_scope(
            scope, neonctl=db_tap.NEONCTL, environ=os.environ,
        )
        created = ensure_provider_role(
            scope, neonctl=db_tap.NEONCTL, environ=os.environ,
        )
        writer_dsn = provider_dsn(
            scope, APP_ROLE, neonctl=db_tap.NEONCTL, environ=os.environ,
        )
        verify_provider_scope(
            scope, neonctl=db_tap.NEONCTL, environ=os.environ,
        )
        validate_connection_scope(owner_dsn, writer_dsn)
        owner = psycopg.connect(owner_dsn.value)
        try:
            apply_database_provisioning(owner, grants)
        finally:
            owner.close()
        evidence = collect_postflight(writer_dsn.value, grants)
        validate_postflight(evidence, before, grants)
        print(json.dumps({
            "environment": "staging", "project": STAGING_PROJECT_NAME,
            "branch": STAGING_BRANCH_NAME, "state": "provisioned",
            "provider_role_created": created,
            "canonical_carr_writer_grants": len(grants),
            "identity": APP_ROLE, "statement_timeout_seconds": 60,
            "idle_timeout_seconds": 120,
            "proposal_status": dict(evidence.seed_state.proposal_status),
            "target_count": 0, "batch_count": 0,
        }, sort_keys=True))
        return 0
    except SystemExit:
        print(
            "staging-app-writer-provision: REFUSED — provider dependency exited; output suppressed",
            file=sys.stderr,
        )
        return 2
    except (
        OSError, ValueError, ProvisioningRefusal, psycopg.Error,
        subprocess.TimeoutExpired,
    ) as exc:
        print("staging-app-writer-provision: REFUSED — " + redact_error(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
