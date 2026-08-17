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
    context = provision.validate_provider_scope(good_projects, good_branches)
    check("the one carr-staging default main branch is admitted",
          context.project_id == "staging-project" and context.branch_id == "staging-main")
    refused_scopes = (
        ([{"id": provision.PRODUCTION_PROJECT_ID, "name": "carr-staging"}], good_branches),
        ([{"id": "other", "name": "other"}], good_branches),
        (good_projects, [{"id": "wrong", "project_id": "staging-project",
                          "name": "develop", "default": True}]),
        (good_projects, [{"id": "staging-main", "project_id": "staging-project",
                          "name": "main", "default": False}]),
        (good_projects, [{"id": "staging-main", "project_id": "other-project",
                          "name": "main", "default": True}]),
    )
    for projects, branches in refused_scopes:
        try:
            provision.validate_provider_scope(projects, branches)
        except provision.ProvisioningRefusal:
            pass
        else:
            raise AssertionError("a non-staging project/branch scope was accepted")
    check("production, another project, and a non-default/non-main branch are refused", True)

    scope = provision.ProviderScope("staging-project", "staging-main")
    owner = provision.ScopedDsn(
        scope, "neondb_owner", "staging.example", 5432, "db",
        "postgresql://neondb_owner:owner-secret@staging.example/db?sslmode=require",  # ci-secret-scan: allow — hermetic non-routable fixture
    )
    writer = provision.ScopedDsn(
        scope, "app_writer", "staging.example", 5432, "db",
        "postgresql://app_writer:writer-secret@staging.example/db?sslmode=require",  # ci-secret-scan: allow — hermetic non-routable fixture
    )
    provision.validate_connection_scope(owner, writer)
    bad_writers = (
        provision.ScopedDsn(scope, "app_writer", "other.example", 5432, "db", writer.value),
        provision.ScopedDsn(scope, "app_writer", "staging.example", 6432, "db", writer.value),
        provision.ScopedDsn(scope, "app_writer", "staging.example", 5432, "other", writer.value),
        provision.ScopedDsn(provision.ProviderScope("rebuilt", "staging-main"),
                            "app_writer", "staging.example", 5432, "db", writer.value),
    )
    for bad_writer in bad_writers:
        try:
            provision.validate_connection_scope(owner, bad_writer)
        except provision.ProvisioningRefusal:
            pass
        else:
            raise AssertionError("a mismatched app_writer DSN was accepted")
    check("owner/app_writer must share immutable scope, endpoint, port and database", True)

    class Runner:
        def __init__(self, roles: list[dict], create_rc: int = 0):
            self.roles = roles
            self.create_rc = create_rc
            self.calls: list[list[str]] = []

        def __call__(self, args, **kwargs):
            self.calls.append(list(args))
            if args[1:3] == ["roles", "list"]:
                return subprocess.CompletedProcess(args, 0, json.dumps(self.roles), "")
            if args[1:3] == ["roles", "create"]:
                return subprocess.CompletedProcess(
                    args, self.create_rc,
                    '{"role":{"name":"app_writer","password":"provider-secret"}}',
                    "provider-secret-error",
                )
            raise AssertionError(args)

    existing_runner = Runner([{"name": "app_writer"}])
    created = provision.ensure_provider_role(
        provision.ProviderScope("staging-project", "staging-main"),
        neonctl="neonctl", run=existing_runner,
    )
    check("an existing exact app_writer role is reused without create",
          created is False and len(existing_runner.calls) == 1)
    missing_runner = Runner([])
    created = provision.ensure_provider_role(
        provision.ProviderScope("staging-project", "staging-main"),
        neonctl="neonctl", run=missing_runner,
    )
    check("a missing app_writer role is created with captured provider output",
          created is True and len(missing_runner.calls) == 2
          and missing_runner.calls[-1][1:3] == ["roles", "create"]
          and all(call[call.index("--project-id") + 1] == scope.project_id
                  and call[call.index("--branch") + 1] == scope.branch_id
                  for call in missing_runner.calls))
    failed_runner = Runner([], create_rc=1)
    try:
        provision.ensure_provider_role(
            provision.ProviderScope("staging-project", "staging-main"),
            neonctl="neonctl", run=failed_runner,
        )
    except provision.ProvisioningRefusal as exc:
        check("provider failures never disclose captured credentials",
              "provider-secret" not in str(exc))
    else:
        raise AssertionError("provider role creation failure was accepted")
    drift_runner = Runner([])
    drift_runner.roles = []
    original_call = drift_runner.__call__

    def missing_roles_payload(args, **kwargs):
        if args[1:3] == ["roles", "list"]:
            drift_runner.calls.append(list(args))
            return subprocess.CompletedProcess(args, 0, "{}", "")
        return original_call(args, **kwargs)

    try:
        provision.ensure_provider_role(
            provision.ProviderScope("staging-project", "staging-main"),
            neonctl="neonctl", run=missing_roles_payload,
        )
    except provision.ProvisioningRefusal:
        check("missing provider roles array refuses before role creation",
              len(drift_runner.calls) == 1)
    else:
        raise AssertionError("missing provider roles array was treated as no roles")

    class DsnRunner:
        def __init__(self):
            self.calls: list[list[str]] = []

        def __call__(self, args, **kwargs):
            self.calls.append(list(args))
            role = args[args.index("--role-name") + 1]
            return subprocess.CompletedProcess(
                args, 0,
                f"postgresql://{role}:fixture@STAGING.EXAMPLE.:5432/db?sslmode=require",  # ci-secret-scan: allow — hermetic non-routable fixture
                "",
            )

    dsn_runner = DsnRunner()
    scoped_owner = provision.provider_dsn(
        scope, "neondb_owner", neonctl="neonctl", run=dsn_runner, environ={},
    )
    scoped_writer = provision.provider_dsn(
        scope, "app_writer", neonctl="neonctl", run=dsn_runner, environ={},
    )
    provision.validate_connection_scope(scoped_owner, scoped_writer)
    check("both DSNs use exact immutable project/branch ids and normalized endpoint",
          all(call[2] == scope.branch_id
              and call[call.index("--project-id") + 1] == scope.project_id
              for call in dsn_runner.calls)
          and scoped_owner.endpoint == "staging.example"
          and scoped_owner.port == 5432)

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

    class Cursor:
        def __init__(self, fail_at: int | None = None):
            self.statements: list[str] = []
            self.fail_at = fail_at

        def execute(self, statement, params=None):
            self.statements.append(str(statement))
            if self.fail_at is not None and len(self.statements) == self.fail_at:
                raise RuntimeError("synthetic database failure")
            return self

    class Connection:
        def __init__(self, fail_at: int | None = None):
            self.cur = Cursor(fail_at)
            self.commits = 0
            self.rollbacks = 0

        def cursor(self):
            return self.cur

        def commit(self):
            self.commits += 1

        def rollback(self):
            self.rollbacks += 1

    canonical_three = snapshot.acl_facts(extracted[:3])

    def authority(bundle_facts=canonical_three):
        return provision.AuthorityClosure(
            app_writer=provision.RoleAuthority(
                can_login=True, inherits_privileges=True, powerful_attributes=(),
                role_config=("idle_in_transaction_session_timeout=120s",
                             "statement_timeout=60s"),
                memberships=(("carr_writer", False, True, True),),
                reachable_roles=("carr_writer",), direct_acl_facts=(), owned_objects=(),
            ),
            carr_writer=provision.RoleAuthority(
                can_login=False, inherits_privileges=True, powerful_attributes=(),
                role_config=(), memberships=(), reachable_roles=(),
                direct_acl_facts=tuple(bundle_facts), owned_objects=(),
            ),
        )

    original_collect_authority = provision.collect_authority_closure
    try:
        authority_calls = 0

        def collect_good(_cur):
            nonlocal authority_calls
            authority_calls += 1
            return authority()

        provision.collect_authority_closure = collect_good
        success = Connection()
        provision.apply_database_provisioning(success, extracted[:3])
        check("canonical ACLs and complete authority closure validate before one commit",
              success.commits == 1 and success.rollbacks == 0 and authority_calls == 2
              and success.cur.statements[:3] == extracted[:3]
              and all(statement in success.cur.statements for statement in provision.MEMBERSHIP_SQL)
              and provision.MEMBERSHIP_SQL == (
                  "grant carr_writer to app_writer with admin false",
                  "grant carr_writer to app_writer with inherit true",
                  "grant carr_writer to app_writer with set true",
              )
              and provision.STATEMENT_TIMEOUT_SQL in success.cur.statements
              and provision.IDLE_TIMEOUT_SQL in success.cur.statements)

        failed = Connection(fail_at=2)
        try:
            provision.apply_database_provisioning(failed, extracted[:3])
        except RuntimeError:
            pass
        else:
            raise AssertionError("database provisioning failure was accepted")
        check("a database failure rolls back and contains no role deletion path",
              failed.commits == 0 and failed.rollbacks == 1
              and all("delete" not in statement.lower() and "drop role" not in statement.lower()
                      for statement in failed.cur.statements))

        bad_after = dataclasses.replace(
            authority().app_writer,
            direct_acl_facts=(("table", "public.actor", "select", False),),
        )
        closures = iter((authority(), dataclasses.replace(authority(), app_writer=bad_after)))
        provision.collect_authority_closure = lambda _cur: next(closures)
        refused_before_commit = Connection()
        try:
            provision.apply_database_provisioning(refused_before_commit, extracted[:3])
        except provision.ProvisioningRefusal:
            pass
        else:
            raise AssertionError("post-apply unsafe authority committed")
        check("complete post-apply authority refusal rolls back before commit",
              refused_before_commit.commits == 0 and refused_before_commit.rollbacks == 1)
    finally:
        provision.collect_authority_closure = original_collect_authority

    expected_state = provision.ExpectedSeedState(
        proposal_status=(("pending", 10),), target_count=0, batch_count=0
    )
    full_authority = authority(snapshot.acl_facts(extracted))
    good_postflight = provision.PostflightEvidence(
        session_user="app_writer", current_user="app_writer",
        statement_timeout_seconds=60, idle_timeout_seconds=120,
        authority=full_authority,
        missing_privileges=(), seed_state=expected_state,
    )
    provision.validate_postflight(good_postflight, expected_state, extracted)
    check("the exact app_writer identity, timeouts, membership, ACLs and seed state pass", True)
    try:
        provision.validate_postflight(
            provision.PostflightEvidence(
                **{**good_postflight.__dict__, "current_user": "neondb_owner"}
            ), expected_state, extracted)
    except provision.ProvisioningRefusal:
        check("an owner-session masquerading as app_writer is refused", True)
    else:
        raise AssertionError("wrong postflight identity was accepted")
    expected_facts = list(snapshot.acl_facts(extracted))
    grantable_facts = tuple(
        (kind, identity, privilege, True if index == 0 else grantable)
        for index, (kind, identity, privilege, grantable) in enumerate(expected_facts)
    )
    app_negatives = (
        {"memberships": (("carr_writer", True, True, True),)},
        {"memberships": (("carr_writer", False, True, True),
                         ("carr_jobs", False, True, True)),
         "reachable_roles": ("carr_jobs", "carr_writer")},
        {"direct_acl_facts": (("table", "public.actor", "select", False),)},
        {"powerful_attributes": ("createrole",)},
        {"owned_objects": (("1", "pg_class", "2"),)},
        {"role_config": ("idle_in_transaction_session_timeout=120s",
                         "search_path=public", "statement_timeout=60s")},
    )
    bundle_negatives = (
        {"direct_acl_facts": tuple(expected_facts)
                             + (("table", "public.actor", "truncate", False),)},
        {"direct_acl_facts": grantable_facts},
        {"powerful_attributes": ("createrole",)},
        {"memberships": (("carr_reader", False, True, True),),
         "reachable_roles": ("carr_reader",)},
        {"owned_objects": (("1", "pg_class", "3"),)},
    )
    unsafe_authorities = [
        dataclasses.replace(full_authority, app_writer=dataclasses.replace(
            full_authority.app_writer, **override)) for override in app_negatives
    ] + [
        dataclasses.replace(full_authority, carr_writer=dataclasses.replace(
            full_authority.carr_writer, **override)) for override in bundle_negatives
    ]
    for unsafe_authority in unsafe_authorities:
        try:
            provision.validate_postflight(
                dataclasses.replace(good_postflight, authority=unsafe_authority),
                expected_state, extracted,
            )
        except provision.ProvisioningRefusal:
            pass
        else:
            raise AssertionError(f"unsafe authority closure was accepted: {unsafe_authority}")
    check("ownership, config, grantability, role attrs, ACL and inherited authority refuse", True)

    check("Production project id is imported from the canonical db-tap pin",
          provision.PRODUCTION_PROJECT_ID
          == str(provision.db_tap.PROJECTS["production"]["id"]))

    print(f"PASS: staging app_writer provisioner self-test ({checked} checks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
