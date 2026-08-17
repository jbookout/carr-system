#!/usr/bin/env python3
# ci: db-gate
"""Disposable-DB proof of transactional app_writer ACL convergence."""

from __future__ import annotations

import importlib.util
import os
import pathlib
import sys

import psycopg


REPO = pathlib.Path(__file__).resolve().parents[1]
PROVISIONER = REPO / "tools/provision-staging-app-writer.py"


def load_provisioner():
    spec = importlib.util.spec_from_file_location("staging_app_writer_db_gate", PROVISIONER)
    if spec is None or spec.loader is None:
        raise RuntimeError("staging app_writer provisioner is missing")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def assert_precommit_refusal(conn, provision, grants, setup: tuple[str, ...], label: str,
                             baseline_bundle) -> None:
    """Seed unsafe authority and require apply() to roll the whole transaction back."""
    with conn.cursor() as cur:
        cur.execute("create role app_writer login")
        for statement in setup:
            cur.execute(statement)
    try:
        provision.apply_database_provisioning(conn, grants)
    except provision.ProvisioningRefusal:
        pass
    else:
        raise RuntimeError(f"{label} was not refused before commit")
    with conn.cursor() as cur:
        cur.execute("select to_regrole('app_writer')")
        if cur.fetchone() != (None,):
            raise RuntimeError(f"{label} refusal did not roll back app_writer")
        if provision.collect_role_authority(cur, provision.BUNDLE_ROLE) != baseline_bundle:
            raise RuntimeError(f"{label} refusal did not restore carr_writer authority")


def main() -> int:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("staging-app-writer-provision-db-gate: DATABASE_URL is required", file=sys.stderr)
        return 1
    provision = load_provisioner()
    grants = provision.snapshot_grants.load_grants_to_role(
        provision.SCHEMA, provision.BUNDLE_ROLE
    )
    with psycopg.connect(dsn, autocommit=False) as conn, conn.cursor() as cur:
        baseline_bundle = provision.collect_role_authority(cur, provision.BUNDLE_ROLE)
        refusal_fixtures = (
            (("create table public.app_writer_owned_fixture(id integer)",
              "alter table public.app_writer_owned_fixture owner to app_writer"),
             "app_writer object ownership"),
            (("create table public.carr_writer_owned_fixture(id integer)",
              "alter table public.carr_writer_owned_fixture owner to carr_writer"),
             "carr_writer object ownership"),
            (("alter role app_writer createrole",), "powerful app_writer role attribute"),
            (("alter role carr_writer createrole",), "powerful carr_writer role attribute"),
            (("grant carr_jobs to app_writer",), "extra app_writer membership"),
            (("grant carr_reader to carr_writer",), "reachable carr_writer membership"),
            (("grant select on public.actor to app_writer",), "direct app_writer ACL"),
            (("grant select on public.actor to carr_writer with grant option",),
             "grantable carr_writer ACL"),
            (("alter role app_writer set search_path=public",),
             "extra app_writer role configuration"),
        )
        for setup, label in refusal_fixtures:
            assert_precommit_refusal(
                conn, provision, grants, setup, label, baseline_bundle
            )

        cur.execute(
            "select rolcanlogin,rolconfig from pg_roles where rolname=%s",
            (provision.APP_ROLE,),
        )
        baseline_role = cur.fetchone()
        cur.execute(
            """select m.admin_option,m.inherit_option,m.set_option
                 from pg_auth_members m
                 join pg_roles granted on granted.oid=m.roleid
                 join pg_roles member on member.oid=m.member
                where granted.rolname=%s and member.rolname=%s""",
            (provision.BUNDLE_ROLE, provision.APP_ROLE),
        )
        baseline_membership = cur.fetchone()

        cur.execute(
            """do $$ begin
              if not exists (select 1 from pg_roles where rolname='app_writer') then
                create role app_writer login;
              end if;
            end $$"""
        )
        cur.execute("grant carr_writer to app_writer with admin true")
        cur.execute("grant carr_writer to app_writer with inherit false")
        cur.execute("grant carr_writer to app_writer with set false")
        # Prove the canonical extraction repairs an actual missing ACL rather
        # than merely agreeing with an already-correct snapshot-built database.
        cur.execute("revoke select on public.actor from carr_writer")
        cur.execute("revoke insert on public.doctrine_document from carr_writer")
        cur.execute(
            "select has_table_privilege('carr_writer','public.actor','select'),"
            "has_table_privilege('carr_writer','public.doctrine_document','insert')"
        )
        if cur.fetchone() != (False, False):
            raise RuntimeError("disposable missing-ACL fixture was not established")

        provision.apply_database_provisioning(conn, grants, commit=False)

        cur.execute(
            """select m.admin_option,m.inherit_option,m.set_option
                 from pg_auth_members m
                 join pg_roles granted on granted.oid=m.roleid
                 join pg_roles member on member.oid=m.member
                where granted.rolname=%s and member.rolname=%s""",
            (provision.BUNDLE_ROLE, provision.APP_ROLE),
        )
        if cur.fetchone() != (False, True, True):
            raise RuntimeError("app_writer membership is not ADMIN FALSE/INHERIT TRUE/SET TRUE")
        cur.execute("select rolcanlogin,rolconfig from pg_roles where rolname=%s", (provision.APP_ROLE,))
        role_row = cur.fetchone()
        if role_row is None:
            raise RuntimeError("app_writer disappeared inside the provisioning transaction")
        login, config = role_row
        config = set(config or [])
        if not login or not {
            "statement_timeout=60s",
            "idle_in_transaction_session_timeout=120s",
        }.issubset(config):
            raise RuntimeError(f"app_writer login/timeouts are wrong: login={login} config={sorted(config)}")
        missing: list[str] = []
        for relation, privilege in provision.REQUIRED_IMPORTER_PRIVILEGES:
            cur.execute("select has_table_privilege(%s,%s,%s)",
                        (provision.APP_ROLE, relation, privilege))
            if cur.fetchone() != (True,):
                missing.append(f"{relation}.{privilege}")
        if missing:
            raise RuntimeError("missing importer privileges: " + ", ".join(missing))

        expected_acl = set(provision.snapshot_grants.acl_facts(grants))
        actual_acl = set(provision.collect_role_acl_facts(cur, provision.BUNDLE_ROLE))
        if actual_acl != expected_acl:
            raise RuntimeError("canonical carr_writer ACL facts did not converge exactly")
        if provision.collect_role_acl_facts(cur, provision.APP_ROLE):
            raise RuntimeError("fresh app_writer unexpectedly has direct ACLs")

        # Seed every forbidden privilege shape and prove the exact catalog
        # readers see it. These are all rolled back with the outer fixture.
        cur.execute("grant carr_jobs to app_writer")
        cur.execute("grant select on public.actor to app_writer")
        cur.execute("grant truncate on public.actor to carr_writer")
        cur.execute("alter role app_writer createrole")
        memberships = provision.collect_memberships(cur, provision.APP_ROLE)
        if not any(name == "carr_jobs" for name, *_options in memberships):
            raise RuntimeError("extra app_writer membership escaped postflight catalog read")
        if not provision.collect_role_acl_facts(cur, provision.APP_ROLE):
            raise RuntimeError("direct app_writer ACL escaped postflight catalog read")
        if ("table", "public.actor", "truncate", False) not in set(
            provision.collect_role_acl_facts(cur, provision.BUNDLE_ROLE)
        ):
            raise RuntimeError("excess carr_writer ACL escaped postflight catalog read")
        cur.execute("select rolcreaterole from pg_roles where rolname='app_writer'")
        if cur.fetchone() != (True,):
            raise RuntimeError("powerful app_writer attribute fixture was not visible")

        cur.execute("alter role app_writer nocreaterole")
        cur.execute("revoke truncate on public.actor from carr_writer")
        cur.execute("revoke select on public.actor from app_writer")
        cur.execute("revoke carr_jobs from app_writer")
        if provision.collect_memberships(cur, provision.APP_ROLE) != (
            (provision.BUNDLE_ROLE, False, True, True),
        ):
            raise RuntimeError("app_writer exact membership was not restored")
        if provision.collect_role_acl_facts(cur, provision.APP_ROLE):
            raise RuntimeError("app_writer direct ACL was not restored to empty")
        if set(provision.collect_role_acl_facts(cur, provision.BUNDLE_ROLE)) != expected_acl:
            raise RuntimeError("carr_writer ACL was not restored to canonical exactness")

        conn.rollback()

        # The disposable proof must restore even cluster-level role state.
        cur.execute("select rolcanlogin,rolconfig from pg_roles where rolname=%s", (provision.APP_ROLE,))
        if cur.fetchone() != baseline_role:
            raise RuntimeError("rollback left app_writer role/config residue")
        cur.execute(
            """select m.admin_option,m.inherit_option,m.set_option
                 from pg_auth_members m
                 join pg_roles granted on granted.oid=m.roleid
                 join pg_roles member on member.oid=m.member
                where granted.rolname=%s and member.rolname=%s""",
            (provision.BUNDLE_ROLE, provision.APP_ROLE),
        )
        if cur.fetchone() != baseline_membership:
            raise RuntimeError("rollback left app_writer membership residue")
        conn.rollback()

    print("PASS: staging app_writer ACLs, membership, and timeouts converge atomically and roll back cleanly")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"staging-app-writer-provision-db-gate: FAIL — {exc}", file=sys.stderr)
        raise SystemExit(1)
