#!/usr/bin/env python3
# ci: db-gate
"""PostgreSQL 17 proof for SQL-created staging reader/writer login profiles."""

from __future__ import annotations

import importlib.util
import ipaddress
import os
import pathlib
import sys
from urllib.parse import quote, urlsplit, urlunsplit

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict


REPO = pathlib.Path(__file__).resolve().parents[1]
PROVISIONER = REPO / "tools/provision-staging-app-writer.py"


def load_provisioner():
    spec = importlib.util.spec_from_file_location("staging_database_login_gate", PROVISIONER)
    if spec is None or spec.loader is None:
        raise RuntimeError(PROVISIONER)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def role_dsn(owner_dsn: str, role: str, password: str) -> str:
    parsed = urlsplit(owner_dsn)
    host = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port else ""
    return urlunsplit((parsed.scheme, f"{quote(role)}:{quote(password)}@{host}{port}",
                       parsed.path, parsed.query, ""))


def require_loopback(dsn: str) -> None:
    try:
        conninfo = conninfo_to_dict(dsn)
    except psycopg.Error as exc:
        raise RuntimeError("disposable-only gate requires valid explicit conninfo") from exc
    if conninfo.get("service") or conninfo.get("servicefile"):
        raise RuntimeError("disposable-only gate refuses libpq service indirection")
    hosts: list[str] = []
    for key in ("host", "hostaddr"):
        value = str(conninfo.get(key) or "")
        if not value:
            continue
        if "," in value:
            raise RuntimeError("disposable-only gate refuses multi-host conninfo")
        hosts.append(value)
    if not hosts or "," in str(conninfo.get("port") or ""):
        raise RuntimeError("disposable-only gate requires one explicit loopback target")
    for host in hosts:
        try:
            loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            loopback = host == "localhost"
        if not loopback:
            raise RuntimeError("disposable-only gate refuses every non-loopback DATABASE_URL")
    if len(set(hosts)) > 1 and not all(
        host == "localhost" or ipaddress.ip_address(host).is_loopback for host in hosts
    ):
        raise RuntimeError("disposable-only gate refuses every non-loopback DATABASE_URL")


def expect_denied(cur, statement: str, label: str) -> None:
    cur.execute("savepoint denied_operation")
    try:
        cur.execute(statement)
    except psycopg.errors.InsufficientPrivilege:
        cur.execute("rollback to savepoint denied_operation")
        return
    raise RuntimeError(f"app_reader unexpectedly allowed {label}")


def main() -> int:
    admin_dsn = os.environ.get("DATABASE_URL")
    if not admin_dsn:
        raise RuntimeError("DATABASE_URL is required")
    require_loopback(admin_dsn)
    provision = load_provisioner()
    profiles = {profile.label: profile for profile in provision.PROFILES}
    plans = {
        label: provision.snapshot_grants.load_current_grants_to_role(
            provision.SCHEMA, provision.MIGRATIONS, profile.bundle_role
        ) for label, profile in profiles.items()
    }
    passwords = {"reader": "reader-fixture-" + "r" * 48,
                 "writer": "writer-fixture-" + "w" * 48,
                 "owner": "owner-fixture-" + "o" * 48}  # ci-secret-scan: allow — disposable loopback fixture
    with psycopg.connect(admin_dsn) as admin:
        failure: BaseException | None = None
        fixture_mutated = False
        setup_committed = False
        try:
            with admin.cursor() as cur:
                cur.execute(
                    "select current_user,r.oid::bigint,r.rolsuper from pg_roles r "
                    "where r.rolname=current_user"
                )
                admin_identity = cur.fetchone()
                if (
                    admin_identity is None
                    or int(admin_identity[1]) != provision.BOOTSTRAP_SUPERUSER_OID
                    or admin_identity[2] is not True
                ):
                    raise RuntimeError(
                        "disposable gate requires the bootstrap PostgreSQL superuser"
                    )
                bootstrap_role = str(admin_identity[0])
                cur.execute(
                    "select rolcanlogin,rolsuper,rolcreatedb,rolcreaterole,"
                    "rolreplication,rolbypassrls "
                    "from pg_roles where rolname='neondb_owner'"
                )
                if cur.fetchone() != (False, False, False, False, False, False):
                    raise RuntimeError("neondb_owner fixture is absent or already powerful")
                cur.execute(
                    "select granted.rolname,m.admin_option,m.inherit_option,m.set_option,"
                    "grantor.oid::bigint from pg_auth_members m "
                    "join pg_roles granted on granted.oid=m.roleid "
                    "join pg_roles member on member.oid=m.member "
                    "join pg_roles grantor on grantor.oid=m.grantor "
                    "where member.rolname='neondb_owner' "
                    "and granted.rolname in ('carr_reader','carr_writer') order by 1"
                )
                baseline_bundle_edges = tuple(cur.fetchall())
                expected_bundle_edges = (
                    ("carr_reader", False, True, True, provision.BOOTSTRAP_SUPERUSER_OID),
                    ("carr_writer", False, True, True, provision.BOOTSTRAP_SUPERUSER_OID),
                )
                if baseline_bundle_edges != expected_bundle_edges:
                    raise RuntimeError("neondb_owner fixture bundle edges are not exact")
                cur.execute(
                    "select pg_has_role('neondb_owner',%s,'MEMBER')",
                    (bootstrap_role,),
                )
                if cur.fetchone() != (False,):
                    raise RuntimeError("neondb_owner fixture reaches the bootstrap role")
                cur.execute(
                    "select rolname from pg_roles where "
                    "rolname in ('app_reader','app_writer') order by rolname"
                )
                if cur.fetchall():
                    raise RuntimeError("disposable login roles already exist before gate")
                fixture_mutated = True
                cur.execute(sql.SQL(
                    "alter role neondb_owner login createrole nosuperuser nocreatedb "
                    "noreplication nobypassrls password {}"
                ).format(sql.Literal(passwords["owner"])))
                for profile in profiles.values():
                    cur.execute(sql.SQL(
                        "grant {} to neondb_owner with admin true"
                    ).format(sql.Identifier(profile.bundle_role)))
            admin.commit()
            setup_committed = True

            direct_owner_dsn = role_dsn(
                admin_dsn, "neondb_owner", passwords["owner"],
            )
            with psycopg.connect(direct_owner_dsn) as owner:
                with owner.cursor() as cur:
                    creator = provision.require_direct_owner_identity(cur)
                    cur.execute(
                        "select rolname from pg_roles where "
                        "rolname in ('app_reader','app_writer') order by rolname"
                    )
                    if cur.fetchall():
                        raise RuntimeError("disposable login roles already exist before gate")
                    for label, profile in profiles.items():
                        bundle = provision.collect_role_authority(cur, profile.bundle_role)
                        if set(bundle.direct_acl_facts) != set(
                            provision.snapshot_grants.acl_facts(plans[label])
                        ):
                            raise RuntimeError(
                                f"{profile.bundle_role} must already be exact; gate will not repair it"
                            )
                owner.rollback()

                for label in ("reader", "writer"):
                    provision.apply_login_profile(
                        owner, profiles[label], plans[label], passwords[label],
                        expected_creator=creator,
                    )
                    provision.validate_profile_login(
                        role_dsn(admin_dsn, profiles[label].login_role, passwords[label]),
                        profiles[label], plans[label], expected_creator=creator,
                    )

                reader_dsn = role_dsn(admin_dsn, "app_reader", passwords["reader"])
                with psycopg.connect(reader_dsn) as reader, reader.cursor() as cur:
                    cur.execute("select session_user,current_user")
                    if cur.fetchone() != ("app_reader", "app_reader"):
                        raise RuntimeError("reader authentication identity is wrong")
                    cur.execute(
                        "select has_column_privilege(current_user,'public.actor','id','select'),"
                        "has_column_privilege(current_user,'public.actor','slug','select'),"
                        "has_table_privilege(current_user,'ops.authority_receipt','select'),"
                        "has_table_privilege(current_user,'public.actor','insert'),"
                        "has_table_privilege(current_user,'public.actor','update'),"
                        "has_table_privilege(current_user,'public.actor','delete')"
                    )
                    privilege_row = cur.fetchone()
                    if privilege_row != (True, True, True, False, False, False):
                        raise RuntimeError(
                            f"reader SELECT/DML privilege boundary is wrong: {privilege_row!r}"
                        )
                    cur.execute(
                        "select coalesce(bool_or(has_sequence_privilege(current_user,c.oid,'usage')),false) "
                        "from pg_class c where c.relkind='S'"
                    )
                    if cur.fetchone() != (False,):
                        raise RuntimeError("reader has sequence usage")
                    expect_denied(cur, "create table public.reader_escalation(id integer)", "DDL")
                    expect_denied(cur, "create role reader_escalation", "role creation")
                    expect_denied(
                        cur,
                        "insert into retrieval_proposal default values",
                        "protected mutation",
                    )
                    reader.rollback()
        except BaseException as exc:  # preserve maker failure if cleanup also fails
            failure = exc
        try:
            if not setup_committed:
                admin.rollback()
            else:
                admin.rollback()
                with admin.cursor() as cur:
                    cur.execute("drop role if exists app_writer")
                    cur.execute("drop role if exists app_reader")
                    for profile in profiles.values():
                        cur.execute(sql.SQL(
                            "revoke admin option for {} from neondb_owner"
                        ).format(sql.Identifier(profile.bundle_role)))
                    cur.execute("alter role neondb_owner nologin nocreaterole password null")
                admin.commit()
                with admin.cursor() as cur:
                    cur.execute(
                        "select count(*) from pg_roles where "
                        "rolname in ('app_reader','app_writer')"
                    )
                    if cur.fetchone() != (0,):
                        raise RuntimeError("disposable login roles were not removed")
                    cur.execute(
                        "select granted.rolname,m.admin_option,m.inherit_option,m.set_option,"
                        "grantor.oid::bigint from pg_auth_members m "
                        "join pg_roles granted on granted.oid=m.roleid "
                        "join pg_roles member on member.oid=m.member "
                        "join pg_roles grantor on grantor.oid=m.grantor "
                        "where member.rolname='neondb_owner' "
                        "and granted.rolname in ('carr_reader','carr_writer') order by 1"
                    )
                    if tuple(cur.fetchall()) != baseline_bundle_edges:
                        raise RuntimeError("neondb_owner fixture bundle edges were not restored")
                    cur.execute(
                        "select rolcanlogin,rolsuper,rolcreatedb,rolcreaterole,"
                        "rolreplication,rolbypassrls "
                        "from pg_roles where rolname='neondb_owner'"
                    )
                    if cur.fetchone() != (False, False, False, False, False, False):
                        raise RuntimeError("neondb_owner fixture attributes were not restored")
            if fixture_mutated and not setup_committed:
                # All fixture writes were transactional and must have rolled back.
                with admin.cursor() as cur:
                    cur.execute(
                        "select count(*) from pg_roles where "
                        "rolname in ('app_reader','app_writer')"
                    )
                    if cur.fetchone() != (0,):
                        raise RuntimeError("rolled-back fixture unexpectedly created login roles")
                    cur.execute(
                        "select granted.rolname,m.admin_option,m.inherit_option,m.set_option,"
                        "grantor.oid::bigint from pg_auth_members m "
                        "join pg_roles granted on granted.oid=m.roleid "
                        "join pg_roles member on member.oid=m.member "
                        "join pg_roles grantor on grantor.oid=m.grantor "
                        "where member.rolname='neondb_owner' "
                        "and granted.rolname in ('carr_reader','carr_writer') order by 1"
                    )
                    if tuple(cur.fetchall()) != baseline_bundle_edges:
                        raise RuntimeError("rolled-back owner fixture bundle edges drifted")
                    cur.execute(
                        "select rolcanlogin,rolsuper,rolcreatedb,rolcreaterole,"
                        "rolreplication,rolbypassrls "
                        "from pg_roles where rolname='neondb_owner'"
                    )
                    if cur.fetchone() != (False, False, False, False, False, False):
                        raise RuntimeError("rolled-back owner fixture attributes drifted")
        except BaseException as cleanup_exc:
            if failure is not None:
                failure.add_note(f"cleanup also failed: {cleanup_exc}")
            else:
                raise
        if failure is not None:
            raise failure
    print("PASS (DISPOSABLE LOOPBACK ONLY): SQL-created app_reader/app_writer authenticate with exact closed profiles; "
          "reader DML/DDL/sequence/role escalation is denied")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"staging-database-login-provision-db-gate: FAIL — {exc}", file=sys.stderr)
        raise SystemExit(1)
