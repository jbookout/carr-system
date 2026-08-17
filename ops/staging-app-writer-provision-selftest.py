#!/usr/bin/env python3
"""Hermetic contract tests for the staging-only app_writer provisioner."""

from __future__ import annotations

import contextlib
import dataclasses
import hashlib
import importlib.util
import io
import json
import os
import pathlib
import subprocess
import sys
from typing import Any


REPO = pathlib.Path(__file__).resolve().parents[1]
PROVISIONER = REPO / "tools" / "provision-staging-app-writer.py"


def load_module(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    provision = load_module("staging_app_writer_provision", PROVISIONER)
    snapshot = load_module("schema_snapshot_grants_for_test", REPO / "tools/schema_snapshot_grants.py")
    checked = 0

    def check(label: str, condition: bool) -> None:
        nonlocal checked
        checked += 1
        if not condition:
            raise AssertionError(label)
        print(f"  ok  {label}")

    schema_text = (REPO / "db/schema.sql").read_text(encoding="utf-8")
    extracted = snapshot.grants_to_role(schema_text, "carr_writer")
    section = snapshot.carr_grants_section_lines(schema_text)
    raw_writer = [line for line in section if line.endswith(" to carr_writer;")]
    check("provisioning reuses every canonical carr_writer GRANT byte-for-byte",
          extracted == raw_writer and len(extracted) >= 170)
    check("the extracted ACL set names no second grantee",
          all(line.endswith(" to carr_writer;") for line in extracted))

    current_grants = snapshot.load_current_grants_to_role(
        REPO / "db/schema.sql", REPO / "migrations", "carr_writer"
    )
    current_facts = set(snapshot.acl_facts(current_grants))
    check("the current grant plan composes every post-snapshot migration",
          ("table", "ops.work_request", "insert", False) not in current_facts
          and ("table", "ops.work_request", "update", False) in current_facts
          and ("function",
               "ops.capture_sourced_work_request(text, text, text, jsonb, uuid, uuid, uuid)",
               "execute", False) in current_facts
          and len(current_facts) > len(snapshot.acl_facts(extracted)))

    synthetic_applied = "begin; commit;\n"
    synthetic_pending = """begin;
do $$ begin
  grant execute on function ops.capture(text,uuid) to carr_writer;
end $$;
revoke insert on ops.work_request from carr_writer;
commit;
"""
    synthetic_later = """begin;
revoke all on function ops.capture(text,uuid) from carr_writer;
grant execute on function ops.capture_v2(text,uuid) to carr_writer;
commit;
"""
    synthetic_schema = f"""COPY public.schema_migrations (filename, sha256, applied_at) FROM stdin;
0001_base.sql\t{hashlib.sha256(synthetic_applied.encode()).hexdigest()}\t2026-08-16 00:00:00+00
\\.
{snapshot.SECTION_MARKER}
grant insert, select on table ops.work_request to carr_writer;
{snapshot.SECTION_END}
"""
    synthetic_plan = snapshot.compose_grants_to_role(
        synthetic_schema,
        (("0001_base.sql", synthetic_applied),
         ("0002_pending.sql", synthetic_pending),
         ("0003_later.sql", synthetic_later)),
        "carr_writer",
    )
    check("pending GRANT and REVOKE operations compose in migrate filename order",
          set(snapshot.acl_facts(synthetic_plan)) == {
              ("table", "ops.work_request", "select", False),
              ("function", "ops.capture_v2(text, uuid)", "execute", False),
          })
    try:
        snapshot.compose_grants_to_role(
            synthetic_schema,
            (("0001_base.sql", synthetic_applied),
             ("0002_grantable.sql",
              "grant select on table public.actor to carr_writer with grant option;")),
            "carr_writer",
        )
    except snapshot.SnapshotGrantError:
        check("a pending migration cannot make grantable authority canonical", True)
    else:
        raise AssertionError("grantable pending authority was accepted")
    try:
        snapshot.grants_to_role(schema_text.replace(snapshot.SECTION_MARKER, "missing"), "carr_writer")
    except snapshot.SnapshotGrantError:
        check("a missing canonical grants boundary is refused", True)
    else:
        raise AssertionError("a missing canonical grants boundary was accepted")
    destructive = schema_text.replace(
        snapshot.SECTION_MARKER,
        snapshot.SECTION_MARKER
        + "\ngrant select on table public.actor to carr_writer; "
          "drop table public.actor; -- to carr_writer;",
        1,
    )
    try:
        snapshot.grants_to_role(destructive, "carr_writer")
    except snapshot.SnapshotGrantError:
        check("a disguised destructive multi-statement GRANT is refused", True)
    else:
        raise AssertionError("a disguised destructive multi-statement GRANT was accepted")

    for bad_env in (
        {"CARR_BREAK_GLASS": "1"},
        {"DATABASE_URL": "postgresql://ambient.invalid/db"},
        {"CARR_DB_OWNER_URL": "postgresql://ambient.invalid/db"},
        {"CARR_DB_WRITER_URL": "postgresql://ambient.invalid/db"},
        {"PGHOST": "ambient.invalid"},
    ):
        try:
            provision.reject_unsafe_environment(bad_env)
        except provision.ProvisioningRefusal:
            pass
        else:
            raise AssertionError(f"unsafe environment was accepted: {sorted(bad_env)}")
    check("break-glass and ambient database credentials are refused", True)

    good_projects = [{"id": "staging-project", "name": "carr-staging"}]
    good_branches = [{"id": "staging-main", "project_id": "staging-project",
                      "name": "main", "default": True}]
    endpoint_host = "ep-fixture.c-10.us-east-1.aws.neon.tech"
    good_endpoints = [{"id": "ep-fixture", "branch_id": "staging-main",
                       "type": "read_write", "host": endpoint_host}]
    context = provision.validate_provider_scope(good_projects, good_branches, good_endpoints)
    check("the one carr-staging default main branch is admitted",
          context.project_id == "staging-project" and context.branch_id == "staging-main"
          and context.endpoint_id == "ep-fixture" and context.endpoint_host == endpoint_host
          and context.port == 5432 and context.database == "neondb")
    refused_scopes: tuple[Any, ...] = (
        ([{"id": provision.PRODUCTION_PROJECT_ID, "name": "carr-staging"}], good_branches,
         good_endpoints),
        ([{"id": "other", "name": "other"}], good_branches, good_endpoints),
        (good_projects, [{"id": "wrong", "project_id": "staging-project",
                          "name": "develop", "default": True}], good_endpoints),
        (good_projects, [{"id": "staging-main", "project_id": "staging-project",
                          "name": "main", "default": False}], good_endpoints),
        (good_projects, [{"id": "staging-main", "project_id": "other-project",
                          "name": "main", "default": True}], good_endpoints),
        (good_projects, good_branches, []),
        (good_projects, good_branches,
         [{"id": "ep-fixture", "branch_id": "staging-main", "type": "read_write",
           "host": "wrong.neon.tech"}]),
    )
    for projects, branches, endpoints in refused_scopes:
        try:
            provision.validate_provider_scope(projects, branches, endpoints)
        except provision.ProvisioningRefusal:
            pass
        else:
            raise AssertionError("a non-staging project/branch scope was accepted")
    check("production, another project, and a non-default/non-main branch are refused", True)

    scope = provision.ProviderScope(
        "staging-project", "staging-main", "ep-fixture", endpoint_host, 5432, "neondb"
    )
    owner = provision.ScopedDsn(
        scope, "neondb_owner", endpoint_host, 5432, "neondb",
        f"postgresql://neondb_owner:owner-secret@{endpoint_host}/neondb?sslmode=require",  # ci-secret-scan: allow — hermetic non-routable fixture
    )
    writer = provision.ScopedDsn(
        scope, "app_writer", endpoint_host, 5432, "neondb",
        f"postgresql://app_writer:writer-secret@{endpoint_host}/neondb?sslmode=require",  # ci-secret-scan: allow — hermetic non-routable fixture
    )
    provision.validate_connection_scope(owner, writer)
    bad_writers = (
        provision.ScopedDsn(scope, "app_writer", "other.example", 5432, "neondb", writer.value),
        provision.ScopedDsn(scope, "app_writer", endpoint_host, 6432, "neondb", writer.value),
        provision.ScopedDsn(scope, "app_writer", endpoint_host, 5432, "other", writer.value),
        provision.ScopedDsn(provision.ProviderScope(
            "rebuilt", "staging-main", "ep-fixture", endpoint_host, 5432, "neondb"),
            "app_writer", endpoint_host, 5432, "neondb", writer.value),
    )
    for bad_writer in bad_writers:
        try:
            provision.validate_connection_scope(owner, bad_writer)
        except provision.ProvisioningRefusal:
            pass
        else:
            raise AssertionError("a mismatched app_writer DSN was accepted")
    check("owner/app_writer must share immutable scope, endpoint, port and database", True)

    class DsnRunner:
        def __init__(self, query: str = "sslmode=require&channel_binding=require"):
            self.calls: list[list[str]] = []
            self.query = query

        def __call__(self, args, **kwargs):
            self.calls.append(list(args))
            role = args[args.index("--role-name") + 1]
            return subprocess.CompletedProcess(
                args, 0,
                f"postgresql://{role}:fixture@{endpoint_host}:5432/neondb?{self.query}",  # ci-secret-scan: allow — hermetic non-routable fixture
                "",
            )

    dsn_runner = DsnRunner()
    scoped_owner = provision.provider_dsn(
        scope, "neondb_owner", neonctl="neonctl", run=dsn_runner, environ={},
    )
    check("only owner DSN uses exact immutable project/branch ids and normalized endpoint",
          dsn_runner.calls[0][2] == scope.branch_id
          and dsn_runner.calls[0][dsn_runner.calls[0].index("--project-id") + 1] == scope.project_id
          and scoped_owner.endpoint == endpoint_host
          and scoped_owner.port == 5432)
    unsafe_queries = (
        "sslmode=require&channel_binding=require&host=elsewhere",
        "sslmode=require&channel_binding=require&hostaddr=192.0.2.1",
        "sslmode=require&channel_binding=require&port=5433",
        "sslmode=require&channel_binding=require&dbname=postgres",
        "sslmode=require&channel_binding=require&user=app_writer",
        "sslmode=require&channel_binding=require&service=staging",
        "sslmode=require&channel_binding=require&options=-csearch_path%3Dpublic",
        "sslmode=require&sslmode=require&channel_binding=require",
        "sslmode=require", "channel_binding=require",
        "sslmode=verify-full&channel_binding=require",
    )
    for unsafe_query in unsafe_queries:
        try:
            provision.provider_dsn(
                scope, "neondb_owner", neonctl="neonctl",
                run=DsnRunner(unsafe_query), environ={},
            )
        except provision.ProvisioningRefusal:
            pass
        else:
            raise AssertionError(f"unsafe provider DSN query accepted: {unsafe_query}")
    check("provider DSN refuses query overrides, duplicates, missing keys and wrong values", True)
    try:
        provision.provider_dsn(
            scope, "app_writer", neonctl="neonctl", run=dsn_runner, environ={},
        )
    except provision.ProvisioningRefusal:
        check("provider app_writer connection-string reveal is removed", True)
    else:
        raise AssertionError("provider app_writer DSN path remains reachable")
    source = PROVISIONER.read_text(encoding="utf-8")
    check("official bare create response is non-authority and provider create is unused",
          '"roles", "create"' not in source and "provider_role_created" not in source)

    class WorkerRunner:
        def __init__(self):
            self.calls: list[tuple[list[str], dict[str, Any]]] = []
        def __call__(self, args, **kwargs):
            self.calls.append((list(args), kwargs))
            if "put" in args:
                return subprocess.CompletedProcess(args, 0, "suppressed", "suppressed")
            return subprocess.CompletedProcess(
                args, 0, '[{"name":"DATABASE_URL_READER","type":"secret_text"}]', ""
            )

    worker_runner = WorkerRunner()
    reader_profile = next(profile for profile in provision.PROFILES if profile.label == "reader")
    provision.put_worker_database_secret(
        reader_profile, "future-reader", wrangler="wrangler", run=worker_runner, environ={}
    )
    provision.verify_worker_secret_binding(
        reader_profile, wrangler="wrangler", run=worker_runner, environ={}
    )
    check("Worker publish/readback argv pins config, staging name and canonical account",
          worker_runner.calls[0][0] == [
              "wrangler", "secret", "put", "DATABASE_URL_READER", "--env", "staging",
              "--config", str(provision.WRANGLER_CONFIG), "--name", "carr-mcp-staging",
          ]
          and worker_runner.calls[1][0] == [
              "wrangler", "secret", "list", "--env", "staging",
              "--config", str(provision.WRANGLER_CONFIG), "--name", "carr-mcp-staging",
              "--format", "json",
          ]
          and all(call[1]["env"]["CLOUDFLARE_ACCOUNT_ID"]
                  == provision.CLOUDFLARE_ACCOUNT_ID for call in worker_runner.calls))
    try:
        provision.worker_environment({"CLOUDFLARE_ACCOUNT_ID": "0" * 32})
    except provision.ProvisioningRefusal:
        check("mismatched ambient Cloudflare account is refused", True)
    else:
        raise AssertionError("mismatched ambient Cloudflare account was accepted")

    def timed_out(_args, **_kwargs):
        raise subprocess.TimeoutExpired("neonctl", 60, output="secret", stderr="secret")

    try:
        provision.provider_dsn(
            scope, "neondb_owner", neonctl="neonctl", run=timed_out, environ={},
        )
    except provision.ProvisioningRefusal as exc:
        check("provider timeout suppresses captured output", "secret" not in str(exc))
    else:
        raise AssertionError("provider timeout escaped the refusal boundary")

    class ScopeRunner:
        def __init__(self, project_id: str = "staging-project",
                     branch_id: str = "staging-main"):
            self.project_id = project_id
            self.branch_id = branch_id
            self.calls: list[list[str]] = []

        def __call__(self, args, **kwargs):
            self.calls.append(list(args))
            if args[1:3] == ["projects", "list"]:
                payload = {"projects": [{"id": self.project_id, "name": "carr-staging"}]}
            elif args[1:3] == ["branches", "list"]:
                payload = {"branches": [{"id": self.branch_id,
                                           "project_id": self.project_id,
                                           "name": "main", "default": True}]}
            elif args[1] == "api":
                payload = {"endpoints": [{"id": "ep-fixture",
                    "branch_id": self.branch_id, "type": "read_write",
                    "host": endpoint_host}]}
            else:
                raise AssertionError(args)
            return subprocess.CompletedProcess(args, 0, json.dumps(payload), "")

    stable_scope = ScopeRunner()
    provision.verify_provider_scope(
        scope, neonctl="neonctl", run=stable_scope, environ={},
    )
    rebuilt_scope = ScopeRunner(project_id="rebuilt-project", branch_id="rebuilt-main")
    try:
        provision.verify_provider_scope(
            scope, neonctl="neonctl", run=rebuilt_scope, environ={},
        )
    except provision.ProvisioningRefusal:
        check("project/branch rebuild drift after resolution is refused", True)
    else:
        raise AssertionError("provider scope rebuild drift was accepted")

    original_resolver = provision.resolve_provider_scope
    original_reject = provision.reject_unsafe_environment
    try:
        provision.reject_unsafe_environment = lambda _environment: None

        def dependency_exit(**_kwargs):
            raise SystemExit("https://provider.invalid/secret-output")

        provision.resolve_provider_scope = dependency_exit
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            return_code = provision.main([])
        check("provider dependency SystemExit is caught without leaking output",
              return_code == 2 and "secret-output" not in stderr.getvalue())
    finally:
        provision.resolve_provider_scope = original_resolver
        provision.reject_unsafe_environment = original_reject

    profiles = {profile.label: profile for profile in provision.PROFILES}
    plans = {
        label: snapshot.load_current_grants_to_role(
            REPO / "db/schema.sql", REPO / "migrations", profile.bundle_role
        ) for label, profile in profiles.items()
    }

    def profile_closure(profile, facts=None, creator="neondb_owner"):
        facts = tuple(facts if facts is not None else snapshot.acl_facts(plans[profile.label]))
        return provision.ProfileClosure(
            login=provision.RoleAuthority(
                True, True, (), ("idle_in_transaction_session_timeout=120s",
                                 "statement_timeout=60s"),
                ((profile.bundle_role, False, True, True),),
                (profile.bundle_role,), (), (),
            ),
            bundle=provision.RoleAuthority(False, True, (), (), (), (), facts, ()),
            creator_edges=((creator, True, False, False,
                            provision.BOOTSTRAP_SUPERUSER_OID),),
        )

    for label, profile in profiles.items():
        provision.validate_profile_closure(
            profile_closure(profile), profile, plans[label], exact=True,
            expected_creator="neondb_owner",
        )
    check("reader and writer exact closed authority profiles validate", True)
    class IdentityCursor:
        def __init__(self, row): self.row = row
        def execute(self, _statement): pass
        def fetchone(self): return self.row
    check("direct owner session/current identity is admitted",
          provision.require_direct_owner_identity(
              IdentityCursor(("neondb_owner", "neondb_owner", False, True))
          ) == "neondb_owner")
    for identity in (
        ("app_writer", "app_writer", False, True),
        ("neondb_owner", "app_writer", False, True),
        ("neondb_owner", "neondb_owner", True, True),
        ("neondb_owner", "neondb_owner", False, False),
        None,
    ):
        try:
            provision.require_direct_owner_identity(IdentityCursor(identity))
        except provision.ProvisioningRefusal:
            pass
        else:
            raise AssertionError(f"unsafe owner identity accepted: {identity!r}")
    check("SET ROLE, superuser, non-CREATEROLE and non-owner identities refuse", True)
    for creator_edges in (
        (),
        (("neondb_owner", True, False, False, 11),),
        (("neondb_owner", True, True, False, provision.BOOTSTRAP_SUPERUSER_OID),),
        (("neondb_owner", True, False, False, provision.BOOTSTRAP_SUPERUSER_OID),
         ("other", True, False, False, provision.BOOTSTRAP_SUPERUSER_OID)),
    ):
        try:
            provision.validate_profile_closure(
                dataclasses.replace(
                    profile_closure(profiles["writer"]), creator_edges=creator_edges,
                ),
                profiles["writer"], plans["writer"], exact=True,
                expected_creator="neondb_owner",
            )
        except provision.ProvisioningRefusal:
            pass
        else:
            raise AssertionError(f"missing or drifted creator edge accepted: {creator_edges!r}")
    check("missing, extra, option-drifted or non-bootstrap creator edges refuse", True)

    expected_actions = {
        (False, "absent"): "prepare_create",
        (False, "pending"): "create",
        (True, "pending"): "resume",
        (True, "final"): "reuse",
    }
    check("absent/create and pending/final resume matrix is exact",
          all(provision.decide_profile_action(
              role_exists_now=exists, credential_state=state) == action
              for (exists, state), action in expected_actions.items()))
    for bad in ((False, "final"), (True, "absent"), (True, "unknown")):
        try:
            provision.decide_profile_action(role_exists_now=bad[0], credential_state=bad[1])
        except provision.ProvisioningRefusal:
            pass
        else:
            raise AssertionError(f"unsafe state accepted: {bad}")
    check("orphan final, uncredentialed role and unknown state refuse", True)

    events: list[str] = []
    def converge(profile):
        events.append("converge:" + profile.label)
        return "secret-" + profile.label, "created"
    def publish(profile, value):
        events.append("publish:" + profile.label)
        if profile.label == "writer":
            raise provision.ProvisioningRefusal("synthetic publish failure")
    try:
        provision.run_profile_sequence(provision.PROFILES, converge, publish)
    except provision.ProvisioningRefusal:
        pass
    else:
        raise AssertionError("profile publish failure was accepted")
    check("reader converges/publishes before writer and failure stops at exact boundary",
          events == ["converge:reader", "publish:reader", "converge:writer", "publish:writer"])
    events.clear()
    provision.run_profile_sequence(
        provision.PROFILES, converge,
        lambda profile, _value: events.append("resume-publish:" + profile.label),
    )
    check("rerun resumes both profiles in the same deterministic order",
          events == ["converge:reader", "resume-publish:reader",
                     "converge:writer", "resume-publish:writer"])

    class Cursor:
        def __init__(self, fail_secret=None):
            self.statements = []
            self.fail_secret = fail_secret
        def execute(self, statement, params=None):
            self.statements.append(statement)
            if self.fail_secret:
                raise RuntimeError("database rejected " + self.fail_secret)
        def fetchone(self):
            return ("",)
    class Connection:
        def __init__(self, fail_secret=None):
            self.cur = Cursor(fail_secret)
            self.commits = 0
            self.rollbacks = 0
        def cursor(self): return self.cur
        def commit(self): self.commits += 1
        def rollback(self): self.rollbacks += 1

    profile = profiles["writer"]
    tiny_plan = plans["writer"][:3]
    original_exists = provision.role_exists
    original_bundle = provision.collect_role_authority
    original_closure = provision.collect_profile_closure
    try:
        provision.role_exists = lambda _cur, _role: False
        provision.collect_role_authority = lambda _cur, _role: profile_closure(
            profile, snapshot.acl_facts(tiny_plan)
        ).bundle
        provision.collect_profile_closure = lambda _cur, _profile: profile_closure(
            profile, snapshot.acl_facts(tiny_plan)
        )
        success = Connection()
        created = provision.apply_login_profile(
            success, profile, tiny_plan, "s" * 64, expected_creator="neondb_owner"
        )
        check("new SQL login profile creates, validates and commits once",
              created and success.commits == 1 and success.rollbacks == 0)
        check("new SQL login relies on PostgreSQL's automatic creator ADMIN edge",
              not any(
                  "sql('grant ')" in repr(statement).lower()
                  and "identifier('app_writer')" in repr(statement).lower()
                  and "identifier('neondb_owner')" in repr(statement).lower()
                  for statement in success.cur.statements
              )
              and [str(statement).lower() for statement in success.cur.statements].index(
                  "set local createrole_self_grant = ''"
              ) < next(
                  index for index, statement in enumerate(success.cur.statements)
                  if "create role" in repr(statement).lower()
              ))
        secret = "leak-check-" + "z" * 48
        failed = Connection(secret)
        try:
            provision.apply_login_profile(
                failed, profile, tiny_plan, secret, expected_creator="neondb_owner"
            )
        except provision.ProvisioningRefusal as exc:
            check("CREATE ROLE failure rolls back without exposing password",
                  failed.rollbacks == 1 and secret not in str(exc))
        else:
            raise AssertionError("secret-bearing database failure escaped")
        provision.role_exists = lambda _cur, _role: True
        reused = Connection()
        created = provision.apply_login_profile(
            reused, profile, tiny_plan, "s" * 64, expected_creator="neondb_owner"
        )
        check("exact reused SQL login profile commits without CREATE ROLE",
              not created and reused.commits == 1
              and all("create role" not in str(statement).lower()
                      for statement in reused.cur.statements))
    finally:
        provision.role_exists = original_exists
        provision.collect_role_authority = original_bundle
        provision.collect_profile_closure = original_closure

    check("Production project id is imported from the canonical db-tap pin",
          provision.PRODUCTION_PROJECT_ID
          == str(provision.db_tap.PROJECTS["production"]["id"]))

    print(f"PASS: staging app_writer provisioner self-test ({checked} checks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
