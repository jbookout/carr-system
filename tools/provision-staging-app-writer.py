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
import tomllib
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import parse_qsl, unquote, urlsplit

try:
    import psycopg
    from psycopg import sql
except ImportError:
    sys.exit("staging app_writer provisioner requires the repo virtualenv (psycopg)")


REPO = pathlib.Path(__file__).resolve().parents[1]
SCHEMA = REPO / "db/schema.sql"
MIGRATIONS = REPO / "migrations"
STAGING_PROJECT_NAME = "carr-staging"
STAGING_BRANCH_NAME = "main"
APP_ROLE = "app_writer"
BUNDLE_ROLE = "carr_writer"
READER_ROLE = "app_reader"
READER_BUNDLE_ROLE = "carr_reader"
LOCK_KEY = 7301961134306001
BOOTSTRAP_SUPERUSER_OID = 10
WRANGLER = REPO / "mcp-server/node_modules/.bin/wrangler"
WRANGLER_CONFIG = REPO / "mcp-server/wrangler.toml"
STAGING_WORKER_NAME = "carr-mcp-staging"
EXPECTED_PROPOSAL_STATUS = (("pending", 10),)

FORBIDDEN_ENV = (
    "CARR_BREAK_GLASS",
    "DATABASE_URL",
    "CARR_DB_OWNER_URL",
    "CARR_DB_WRITER_URL",
    "CARR_DB_READER_URL",
    "CARR_DB_JOBS_URL",
    "CARR_DB_AUTHORITY_URL",
    "CARR_DB_STAGING_WRITER_URL",
    "CARR_DB_STAGING_READER_URL",
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
credential = load_module(
    "staging_database_credential", REPO / "tools/staging_database_credential.py"
)


def _wrangler_account_id() -> str:
    with WRANGLER_CONFIG.open("rb") as handle:
        value = tomllib.load(handle).get("account_id")
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{32}", value):
        raise RuntimeError("wrangler.toml must pin one lowercase 32-hex Cloudflare account_id")
    return value


CLOUDFLARE_ACCOUNT_ID = _wrangler_account_id()


class ProvisioningRefusal(RuntimeError):
    """The requested action is outside the exact isolated-staging contract."""


@dataclass(frozen=True)
class ProviderScope:
    project_id: str
    branch_id: str
    endpoint_id: str
    endpoint_host: str
    port: int
    database: str


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
class LoginProfile:
    label: str
    login_role: str
    bundle_role: str
    secret_name: str


@dataclass(frozen=True)
class ProfileClosure:
    login: RoleAuthority
    bundle: RoleAuthority
    creator_edges: tuple[tuple[str, bool, bool, bool, int], ...]


PROFILES = (
    LoginProfile("reader", READER_ROLE, READER_BUNDLE_ROLE, "DATABASE_URL_READER"),
    LoginProfile("writer", APP_ROLE, BUNDLE_ROLE, "DATABASE_URL_WRITER"),
)


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


def worker_environment(environ: Mapping[str, str]) -> dict[str, str]:
    result = dict(environ)
    ambient = result.get("CLOUDFLARE_ACCOUNT_ID")
    if ambient and ambient != CLOUDFLARE_ACCOUNT_ID:
        raise ProvisioningRefusal("ambient Cloudflare account differs from the pinned CARR account")
    result["CLOUDFLARE_ACCOUNT_ID"] = CLOUDFLARE_ACCOUNT_ID
    return result


def validate_provider_scope(
    projects: Sequence[dict[str, Any]], branches: Sequence[dict[str, Any]],
    endpoints: Sequence[dict[str, Any]],
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
    endpoint_matches = [
        row for row in endpoints
        if str(row.get("branch_id") or branch_id) == branch_id
        and row.get("type") in {"read_write", "read-write", "rw"}
    ]
    if len(endpoint_matches) != 1:
        raise ProvisioningRefusal("staging main must have exactly one read-write endpoint")
    endpoint_id = str(endpoint_matches[0].get("id") or "")
    endpoint_host = str(endpoint_matches[0].get("host") or "").lower().rstrip(".")
    if (
        not endpoint_id.startswith("ep-")
        or not endpoint_host.startswith(endpoint_id + ".")
        or not endpoint_host.endswith(".neon.tech")
    ):
        raise ProvisioningRefusal("staging read-write endpoint identity or host is invalid")
    return ProviderScope(project_id, branch_id, endpoint_id, endpoint_host, 5432, "neondb")


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
        return validate_provider_scope(projects, [], [])
    project_id = str(matches[0].get("id") or "")
    if not project_id or project_id == PRODUCTION_PROJECT_ID:
        return validate_provider_scope(projects, [], [])
    branch_payload = _provider_json(
        [neonctl, "branches", "list", "--project-id", project_id, "--output", "json"],
        run=run, env=env,
    )
    branches = _rows(branch_payload, "branches")
    branch_matches = [row for row in branches if row.get("name") == STAGING_BRANCH_NAME]
    if len(branch_matches) != 1 or not str(branch_matches[0].get("id") or ""):
        return validate_provider_scope(projects, branches, [])
    branch_id = str(branch_matches[0]["id"])
    endpoint_payload = _provider_json(
        [neonctl, "api", f"/projects/{project_id}/branches/{branch_id}/endpoints",
         "--output", "json"],
        run=run, env=env,
    )
    return validate_provider_scope(
        projects, branches, _rows(endpoint_payload, "endpoints")
    )


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
    endpoint_payload = _provider_json(
        [neonctl, "api",
         f"/projects/{scope.project_id}/branches/{scope.branch_id}/endpoints",
         "--output", "json"], run=run, env=env,
    )
    current = validate_provider_scope(
        projects, branches, _rows(endpoint_payload, "endpoints")
    )
    if current != scope:
        raise ProvisioningRefusal("staging endpoint changed after immutable scope resolution")


def validate_provider_dsn_query(query_text: str) -> None:
    try:
        rows = parse_qsl(query_text, keep_blank_values=True, strict_parsing=True)
    except ValueError as exc:
        raise ProvisioningRefusal("provider DSN query is invalid; value suppressed") from exc
    query = dict(rows)
    if (
        len(rows) != len(query)
        or query != {"sslmode": "require", "channel_binding": "require"}
    ):
        raise ProvisioningRefusal("provider DSN query is outside the exact safe contract")


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
    validate_provider_dsn_query(parsed.query)
    if (
        parsed.scheme not in {"postgres", "postgresql"}
        or not username or not host or not database or parsed.fragment
    ):
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
        or not scope.endpoint_id
        or not scope.endpoint_host
        or scope.port != 5432
        or scope.database != "neondb"
        or role_name != "neondb_owner"
    ):
        raise ProvisioningRefusal("only the isolated-staging owner DSN may come from the provider")
    result = _provider_run(
        [neonctl, "connection-string", scope.branch_id,
         "--project-id", scope.project_id, "--role-name", role_name,
         "--database-name", scope.database, "--endpoint-type", "read_write"],
        run=run, env=provider_environment(environ),
    )
    value = result.stdout.strip()
    if not value or "\n" in value or "\r" in value:
        raise ProvisioningRefusal("provider DSN response has the wrong shape; output suppressed")
    username, endpoint, port, database = _dsn_parts(value)
    if (
        username != role_name or endpoint != scope.endpoint_host
        or port != scope.port or database != scope.database
    ):
        raise ProvisioningRefusal("provider DSN differs from the pinned endpoint target; value suppressed")
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
    seed_contract = load_module(
        "staging_app_writer_seed_contract_runtime",
        REPO / "pipelines/staging_retrieval_doctrine_seed.py",
    )
    cur.execute("select status,count(*) from retrieval_proposal group by status order by status")
    proposal_status = tuple((str(status), int(count)) for status, count in cur.fetchall())
    target_count = 0
    for target in seed_contract.TARGETS:
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


def collect_creator_edges(
    cur: Any, role: str
) -> tuple[tuple[str, bool, bool, bool, int], ...]:
    cur.execute(
        """select member.rolname,m.admin_option,m.inherit_option,m.set_option,grantor.oid::bigint
             from pg_auth_members m
             join pg_roles granted on granted.oid=m.roleid
             join pg_roles member on member.oid=m.member
             join pg_roles grantor on grantor.oid=m.grantor
            where granted.rolname=%s order by member.rolname""",
        (role,),
    )
    return tuple((str(name), bool(admin), bool(inherit), bool(can_set), int(grantor_oid))
                 for name, admin, inherit, can_set, grantor_oid in cur.fetchall())


def role_exists(cur: Any, role: str) -> bool:
    cur.execute("select exists(select 1 from pg_roles where rolname=%s)", (role,))
    return cur.fetchone() == (True,)


def collect_profile_closure(cur: Any, profile: LoginProfile) -> ProfileClosure:
    return ProfileClosure(
        collect_role_authority(cur, profile.login_role),
        collect_role_authority(cur, profile.bundle_role),
        collect_creator_edges(cur, profile.login_role),
    )


def validate_profile_closure(
    closure: ProfileClosure, profile: LoginProfile, canonical_grants: Sequence[str],
    *, exact: bool, expected_creator: str,
) -> None:
    login = closure.login
    bundle = closure.bundle
    if not login.can_login or not login.inherits_privileges or login.powerful_attributes:
        raise ProvisioningRefusal(f"{profile.login_role} is not a plain inheriting LOGIN role")
    if bundle.can_login or not bundle.inherits_privileges or bundle.powerful_attributes:
        raise ProvisioningRefusal(f"{profile.bundle_role} is not a plain NOLOGIN privilege bundle")
    if login.owned_objects or bundle.owned_objects:
        raise ProvisioningRefusal("staging login/bundle roles must not own objects")
    if login.direct_acl_facts:
        raise ProvisioningRefusal(f"{profile.login_role} has forbidden direct ACLs")
    if bundle.memberships or bundle.reachable_roles or bundle.role_config:
        raise ProvisioningRefusal(f"{profile.bundle_role} inherits or configures extra authority")
    if closure.creator_edges != (
        (expected_creator, True, False, False, BOOTSTRAP_SUPERUSER_OID),
    ):
        raise ProvisioningRefusal(
            f"{profile.login_role} creator ADMIN edge is not exactly bound to "
            f"{expected_creator} and the bootstrap grantor"
        )
    allowed_config = tuple(sorted((
        "idle_in_transaction_session_timeout=120s", "statement_timeout=60s",
    )))
    if exact:
        if login.role_config != allowed_config:
            raise ProvisioningRefusal(f"{profile.login_role} timeouts/config are not exact")
        if login.memberships != ((profile.bundle_role, False, True, True),):
            raise ProvisioningRefusal(f"{profile.login_role} bundle membership is not exact")
        if login.reachable_roles != (profile.bundle_role,):
            raise ProvisioningRefusal(f"{profile.login_role} reaches an unexpected role")
    else:
        if login.role_config and login.role_config != allowed_config:
            raise ProvisioningRefusal(f"reused {profile.login_role} has unexpected configuration")
        if any(name != profile.bundle_role for name, *_ in login.memberships):
            raise ProvisioningRefusal(f"reused {profile.login_role} has an extra membership")
        if any(name != profile.bundle_role for name in login.reachable_roles):
            raise ProvisioningRefusal(f"reused {profile.login_role} reaches an extra role")
    expected_acl = set(snapshot_grants.acl_facts(canonical_grants))
    actual_acl = set(bundle.direct_acl_facts)
    if actual_acl - expected_acl:
        raise ProvisioningRefusal(f"{profile.bundle_role} has excess or grantable authority")
    if exact and expected_acl - actual_acl:
        raise ProvisioningRefusal(f"{profile.bundle_role} is missing canonical authority")


def _profile_membership_sql(profile: LoginProfile) -> tuple[str, ...]:
    return (
        f"grant {profile.bundle_role} to {profile.login_role} with admin false",
        f"grant {profile.bundle_role} to {profile.login_role} with inherit true",
        f"grant {profile.bundle_role} to {profile.login_role} with set true",
    )


def apply_login_profile(
    conn: Any, profile: LoginProfile, grants: Sequence[str], password: str,
    *, expected_creator: str, commit: bool = True,
) -> bool:
    """Create/converge one login profile in one advisory-locked transaction."""
    cur = conn.cursor()
    created = False
    try:
        cur.execute("select pg_advisory_xact_lock(%s)", (LOCK_KEY,))
        exists = role_exists(cur, profile.login_role)
        if exists:
            validate_profile_closure(
                collect_profile_closure(cur, profile), profile, grants,
                exact=True, expected_creator=expected_creator,
            )
        else:
            bundle = collect_role_authority(cur, profile.bundle_role)
            if (
                bundle.can_login or not bundle.inherits_privileges
                or bundle.powerful_attributes or bundle.owned_objects
                or bundle.memberships or bundle.reachable_roles or bundle.role_config
            ):
                raise ProvisioningRefusal(f"{profile.bundle_role} is not a closed bundle role")
            expected_acl = set(snapshot_grants.acl_facts(grants))
            if set(bundle.direct_acl_facts) - expected_acl:
                raise ProvisioningRefusal(f"{profile.bundle_role} has excess authority")
            cur.execute("set local createrole_self_grant = ''")
            cur.execute("select current_setting('createrole_self_grant')")
            if cur.fetchone() != ("",):
                raise ProvisioningRefusal("createrole_self_grant did not fail closed")
            cur.execute(sql.SQL(
                "create role {} login inherit nosuperuser nocreatedb nocreaterole "
                "noreplication nobypassrls password {}"
            ).format(sql.Identifier(profile.login_role), sql.Literal(password)))
            # PostgreSQL 17 automatically grants the newly-created role back to
            # its CREATEROLE creator with ADMIN TRUE / INHERIT FALSE / SET FALSE.
            # The exact edge (including its grantor) is proved below by the
            # same fail-closed closure validation used for reused roles.
            created = True
        for statement in grants:
            cur.execute(statement)
        for statement in _profile_membership_sql(profile):
            cur.execute(statement)
        cur.execute(f"alter role {profile.login_role} set statement_timeout = '60s'")
        cur.execute(
            f"alter role {profile.login_role} set idle_in_transaction_session_timeout = '120s'"
        )
        validate_profile_closure(
            collect_profile_closure(cur, profile), profile, grants,
            exact=True, expected_creator=expected_creator,
        )
        if commit:
            conn.commit()
        return created
    except Exception as exc:
        conn.rollback()
        if password and password in str(exc):
            raise ProvisioningRefusal(
                "database role provisioning failed; credential suppressed"
            ) from exc
        raise


def validate_profile_login(
    dsn: str, profile: LoginProfile, grants: Sequence[str], *, expected_creator: str,
    connect: Connect = psycopg.connect,
) -> None:
    conn = connect(dsn)
    try:
        cur = conn.cursor()
        cur.execute("begin transaction read only")
        cur.execute("select session_user,current_user")
        if cur.fetchone() != (profile.login_role, profile.login_role):
            raise ProvisioningRefusal(f"postflight did not authenticate as {profile.login_role}")
        closure = collect_profile_closure(cur, profile)
        validate_profile_closure(
            closure, profile, grants, exact=True, expected_creator=expected_creator,
        )
        cur.execute(
            "select extract(epoch from current_setting('statement_timeout')::interval)::integer,"
            "extract(epoch from current_setting('idle_in_transaction_session_timeout')::interval)::integer"
        )
        if cur.fetchone() != (60, 120):
            raise ProvisioningRefusal(f"{profile.login_role} role timeouts are not 60s/120s")
        if profile.label == "writer":
            missing: list[str] = []
            for relation, privilege in REQUIRED_IMPORTER_PRIVILEGES:
                cur.execute("select has_table_privilege(current_user,%s,%s)", (relation, privilege))
                if cur.fetchone() != (True,):
                    missing.append(f"{relation}.{privilege}")
            if missing:
                raise ProvisioningRefusal("app_writer is missing importer privileges")
        conn.rollback()
    finally:
        conn.close()


def put_worker_database_secret(
    profile: LoginProfile, value: str, *, wrangler: str = str(WRANGLER),
    run: Run = subprocess.run, environ: Mapping[str, str] | None = None,
) -> None:
    try:
        result = run(
            [wrangler, "secret", "put", profile.secret_name,
             "--env", "staging", "--config", str(WRANGLER_CONFIG),
             "--name", STAGING_WORKER_NAME],
            input=value, capture_output=True, text=True, timeout=60,
            env=worker_environment(environ if environ is not None else os.environ),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProvisioningRefusal("Worker secret update outcome is uncertain; output suppressed") from exc
    if result.returncode != 0:
        raise ProvisioningRefusal(
            f"Worker secret update failed (rc={result.returncode}); output suppressed"
        )


def verify_worker_secret_binding(
    profile: LoginProfile, *, wrangler: str = str(WRANGLER),
    run: Run = subprocess.run, environ: Mapping[str, str] | None = None,
) -> None:
    try:
        result = run(
            [wrangler, "secret", "list", "--env", "staging",
             "--config", str(WRANGLER_CONFIG), "--name", STAGING_WORKER_NAME,
             "--format", "json"],
            capture_output=True, text=True, timeout=60,
            env=worker_environment(environ if environ is not None else os.environ),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProvisioningRefusal("Worker secret readback did not complete; output suppressed") from exc
    if result.returncode != 0:
        raise ProvisioningRefusal("Worker secret readback failed; output suppressed")
    try:
        payload = json.loads(result.stdout)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ProvisioningRefusal("Worker secret readback was not JSON; output suppressed") from exc
    if not isinstance(payload, list) or sum(
        isinstance(row, dict) and row.get("name") == profile.secret_name for row in payload
    ) != 1:
        raise ProvisioningRefusal(f"Worker does not report exactly one {profile.secret_name} binding")


def require_direct_owner_identity(cur: Any) -> str:
    cur.execute(
        "select session_user,current_user,r.rolsuper,r.rolcreaterole "
        "from pg_roles r where r.rolname=current_user"
    )
    row = cur.fetchone()
    if row != ("neondb_owner", "neondb_owner", False, True):
        raise ProvisioningRefusal(
            "provisioning requires direct non-superuser neondb_owner with CREATEROLE"
        )
    return "neondb_owner"


def decide_profile_action(*, role_exists_now: bool, credential_state: str) -> str:
    matrix = {
        (False, "absent"): "prepare_create",
        (False, "pending"): "create",
        (True, "pending"): "resume",
        (True, "final"): "reuse",
    }
    action = matrix.get((role_exists_now, credential_state))
    if action is None:
        raise ProvisioningRefusal(
            f"unsafe role/credential state: role_exists={role_exists_now}, credential={credential_state}"
        )
    return action


def run_profile_sequence(
    profiles: Sequence[LoginProfile],
    converge: Callable[[LoginProfile], tuple[str, str]],
    publish: Callable[[LoginProfile, str], None],
) -> dict[str, str]:
    """Reader first; each completed profile is recoverable if the next boundary fails."""
    outcomes: dict[str, str] = {}
    for profile in profiles:
        value, outcome = converge(profile)
        publish(profile, value)
        outcomes[profile.label] = outcome
    return outcomes


def redact_error(exc: BaseException) -> str:
    text = str(exc)
    text = re.sub(r"postgres(?:ql)?://\S+", "[DSN REDACTED]", text)
    text = re.sub(r"https?://\S+", "[URL REDACTED]", text)
    return text[:300]


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="create/reuse the staging login roles and apply canonical ACLs")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        reject_unsafe_environment(os.environ)
        plans = {
            profile.label: snapshot_grants.load_current_grants_to_role(
                SCHEMA, MIGRATIONS, profile.bundle_role
            ) for profile in PROFILES
        }
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
                "canonical_grants": {label: len(plan) for label, plan in plans.items()},
                "proposal_status": dict(before.proposal_status),
                "target_count": before.target_count, "batch_count": before.batch_count,
                "reader_least_privilege_required": True,
            }, sort_keys=True))
            return 0

        verify_provider_scope(
            scope, neonctl=db_tap.NEONCTL, environ=os.environ,
        )
        config_root = credential.profile("writer").paths.final.parent
        lock_path = config_root / ".staging-role-operation.lock"
        outcomes: dict[str, str] = {}
        with credential.exclusive_lock(lock_path):
            owner = psycopg.connect(owner_dsn.value)
            try:
                cur = owner.cursor()
                expected_creator = require_direct_owner_identity(cur)
                owner.commit()
                owner.autocommit = True
                cur.execute("select pg_advisory_lock(%s)", (LOCK_KEY,))
                owner.autocommit = False
                def converge(login_profile: LoginProfile) -> tuple[str, str]:
                    file_profile = credential.profile(login_profile.label)
                    cur.execute("select exists(select 1 from pg_roles where rolname=%s)",
                                (login_profile.login_role,))
                    exists = cur.fetchone() == (True,)
                    try:
                        stored = credential.load_existing(
                            file_profile.paths, key=file_profile.key,
                            role_name=file_profile.role_name,
                            expected_endpoint=owner_dsn.endpoint,
                            expected_port=owner_dsn.port,
                            expected_database=owner_dsn.database,
                        )
                    except credential.CredentialRefusal as exc:
                        if "is absent" not in str(exc):
                            raise
                        action = decide_profile_action(
                            role_exists_now=exists, credential_state="absent"
                        )
                        stored = credential.prepare_pending(
                            file_profile.paths, key=file_profile.key,
                            role_name=file_profile.role_name,
                            owner_uri=owner_dsn.value,
                            expected_endpoint=owner_dsn.endpoint,
                            expected_port=owner_dsn.port,
                            expected_database=owner_dsn.database,
                        )
                    else:
                        action = decide_profile_action(
                            role_exists_now=exists, credential_state=stored.state
                        )
                    if action in {"resume", "reuse"}:
                        validate_profile_login(
                            stored.value, login_profile, plans[login_profile.label],
                            expected_creator=expected_creator,
                        )
                        outcome = "resumed" if action == "resume" else "reused"
                    else:
                        apply_login_profile(
                            owner, login_profile, plans[login_profile.label], stored.password,
                            expected_creator=expected_creator,
                        )
                        validate_profile_login(
                            stored.value, login_profile, plans[login_profile.label],
                            expected_creator=expected_creator,
                        )
                        outcome = "created"
                    if stored.state == "pending":
                        credential.promote_pending(
                            file_profile.paths, key=file_profile.key,
                            expected_value=stored.value,
                        )
                    return stored.value, outcome

                def publish(login_profile: LoginProfile, value: str) -> None:
                    verify_provider_scope(
                        scope, neonctl=db_tap.NEONCTL, environ=os.environ,
                    )
                    put_worker_database_secret(login_profile, value)
                    verify_worker_secret_binding(login_profile)
                    verify_provider_scope(
                        scope, neonctl=db_tap.NEONCTL, environ=os.environ,
                    )
                outcomes = run_profile_sequence(PROFILES, converge, publish)
            finally:
                try:
                    owner.rollback()
                    owner.autocommit = True
                    owner.execute("select pg_advisory_unlock(%s)", (LOCK_KEY,))
                except (psycopg.Error, ValueError):
                    pass
                owner.close()
        after = read_seed_state(owner_dsn.value)
        validate_seed_state(after)
        if after != before:
            raise ProvisioningRefusal("provisioning changed proposals, doctrine targets, or batches")
        print(json.dumps({
            "environment": "staging", "project": STAGING_PROJECT_NAME,
            "branch": STAGING_BRANCH_NAME, "state": "provisioned",
            "role_outcomes": outcomes,
            "canonical_grants": {label: len(plan) for label, plan in plans.items()},
            "identities": [READER_ROLE, APP_ROLE], "statement_timeout_seconds": 60,
            "idle_timeout_seconds": 120,
            "proposal_status": dict(after.proposal_status),
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
        OSError, ValueError, ProvisioningRefusal, credential.CredentialRefusal, psycopg.Error,
        subprocess.TimeoutExpired,
    ) as exc:
        print("staging-app-writer-provision: REFUSED — " + redact_error(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
