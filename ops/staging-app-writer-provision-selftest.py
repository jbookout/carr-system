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
import uuid
from typing import Any


REPO = pathlib.Path(__file__).resolve().parents[1]
PROVISIONER = REPO / "tools" / "provision-staging-app-writer.py"
CANDIDATE_OPERATION_ID = uuid.UUID("f870b3e2-f99a-4bf2-ba16-629d9725ba6d")
RECEIPT_ID = uuid.UUID("c4cddf05-03bd-4f9e-8691-b54dac7be8f4")
EXPECTED_SHA = "07d13398824dad987c40331ae7c2092db07b75d8"


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
    # Pending security migrations can intentionally revoke snapshot authority,
    # so cardinality is not a valid composition invariant. Pin representative
    # current grant and revoke outcomes here; the synthetic fixture below proves
    # that arbitrary GRANT/REVOKE operations compose in filename order.
    check("the current grant plan composes every post-snapshot migration",
          ("table", "ops.work_request", "insert", False) not in current_facts
          and ("table", "ops.work_request", "update", False) in current_facts
          and ("table", "public.lease", "insert", False) not in current_facts
          and ("table", "public.lease", "update", False) not in current_facts
          and ("function",
               "ops.capture_sourced_work_request(text, text, text, jsonb, uuid, uuid, uuid)",
               "execute", False) in current_facts)

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

    target = provision.replacement_target(
        str(CANDIDATE_OPERATION_ID), str(RECEIPT_ID), EXPECTED_SHA,
        run=lambda args, **_kwargs: subprocess.CompletedProcess(args, 0, "", ""),
    )
    check("replacement target binds full canonical UUIDv4 values and exact merged SHA",
          target.candidate_operation_id == CANDIDATE_OPERATION_ID
          and target.receipt_id == RECEIPT_ID and target.expected_sha == EXPECTED_SHA)
    for candidate, receipt, sha in (
        (str(CANDIDATE_OPERATION_ID)[:8], str(RECEIPT_ID), EXPECTED_SHA),
        (str(CANDIDATE_OPERATION_ID), str(RECEIPT_ID)[:8], EXPECTED_SHA),
        (str(uuid.uuid1()), str(RECEIPT_ID), EXPECTED_SHA),
        (str(CANDIDATE_OPERATION_ID), str(uuid.uuid1()), EXPECTED_SHA),
        (str(CANDIDATE_OPERATION_ID), str(RECEIPT_ID), EXPECTED_SHA[:12]),
        (str(CANDIDATE_OPERATION_ID), str(RECEIPT_ID), "A" * 40),
    ):
        try:
            provision.replacement_target(
                candidate, receipt, sha,
                run=lambda args, **_kwargs: subprocess.CompletedProcess(args, 0, "", ""),
            )
        except provision.ProvisioningRefusal:
            pass
        else:
            raise AssertionError("a partial/non-v4 replacement target was accepted")
    try:
        provision.replacement_target(
            str(CANDIDATE_OPERATION_ID), str(RECEIPT_ID), EXPECTED_SHA,
            run=lambda args, **_kwargs: subprocess.CompletedProcess(args, 1, "secret", "secret"),
        )
    except provision.ProvisioningRefusal as exc:
        check("unmerged source SHA refusal suppresses child output", "secret" not in str(exc))
    else:
        raise AssertionError("a source SHA outside origin/main was accepted")

    replacement = provision.replacement
    production = replacement.ProviderScope(
        replacement.PRODUCTION_PROJECT_ID, "production", "br-production", "ep-production",
        "ep-production.c-10.us-east-1.aws.neon.tech")
    old = replacement.ProviderScope(
        "old-staging-project", replacement.STAGING_NAME, "br-old", "ep-old",
        "ep-old.c-10.us-east-1.aws.neon.tech")
    candidate = replacement.ProviderScope(
        "candidate-project", replacement.candidate_name(CANDIDATE_OPERATION_ID),
        "br-candidate", "ep-candidate", "ep-candidate.c-10.us-east-1.aws.neon.tech")
    fixture_migration = replacement.CONTRACT_MIGRATION
    fixture_migration_sha = "1" * 64
    fixture_ledger = {fixture_migration: fixture_migration_sha}
    fixture_ledger_material = f"{fixture_migration}\0{fixture_migration_sha}\n"
    source_manifest = {
        "git_sha": EXPECTED_SHA, "source_tree_oid": "2" * 40,
        "source_tree_sha256": "sha256:" + "3" * 64,
        "source_tree_entry_count": 123, "artifact_sha256": "sha256:" + "4" * 64,
        "config_sha256": "sha256:" + "5" * 64,
        "dependency_sha256": "sha256:" + "6" * 64,
        "migration_ledger": fixture_ledger, "migration_count": 1,
        "migration_highest": fixture_migration,
        "migration_ledger_sha256": "sha256:" + hashlib.sha256(
            fixture_ledger_material.encode()).hexdigest(),
    }
    exact_receipt = {
        "contract_id": str(uuid.UUID("11111111-2222-4333-8444-555555555555")),
        "receipt_id": str(RECEIPT_ID),
        "evidence_ref": "ops.staging-replacement-project:sha256:" + "7" * 64,
        "receipt_sha256": "sha256:" + "a" * 64,
        "git_sha": EXPECTED_SHA, "source_tree_oid": source_manifest["source_tree_oid"],
        "source_tree_sha256": source_manifest["source_tree_sha256"],
        "source_tree_entry_count": source_manifest["source_tree_entry_count"],
        "artifact_sha256": source_manifest["artifact_sha256"],
        "config_sha256": source_manifest["config_sha256"],
        "dependency_sha256": source_manifest["dependency_sha256"],
        "prior_staging_project_id": old.project_id,
        "replacement_project_id": candidate.project_id,
        "replacement_branch_id": candidate.branch_id,
        "replacement_endpoint_id": candidate.endpoint_id,
        "live_migration_ledger": fixture_ledger,
        "live_migration_count": 1, "live_migration_highest": fixture_migration,
        "live_migration_ledger_sha256": source_manifest["migration_ledger_sha256"],
        "synthetic_data_count": 5,
        "production_overlap_count": 0,
        "observed_at": "2026-08-26T00:00:00Z",
    }
    provision.validate_replacement_receipt(
        target, production, old, candidate, exact_receipt, source_manifest)
    for field, bad in (
        ("receipt_id", str(uuid.uuid4())), ("git_sha", "b" * 40),
        ("prior_staging_project_id", production.project_id),
        ("replacement_project_id", old.project_id),
        ("replacement_branch_id", old.branch_id),
        ("replacement_endpoint_id", old.endpoint_id),
        ("production_overlap_count", 1), ("receipt_sha256", "sha256:short"),
    ):
        changed = dict(exact_receipt); changed[field] = bad
        try:
            provision.validate_replacement_receipt(
                target, production, old, candidate, changed, source_manifest)
        except provision.ProvisioningRefusal:
            pass
        else:
            raise AssertionError(f"replacement receipt mismatch was accepted: {field}")
    wrong_production = dataclasses.replace(production, project_id="not-production")
    try:
        provision.validate_replacement_receipt(
            target, wrong_production, old, candidate, exact_receipt, source_manifest)
    except provision.ProvisioningRefusal:
        check("receipt binding refuses wrong Production identity", True)
    else:
        raise AssertionError("wrong Production identity was accepted")
    partial_receipt = dict(exact_receipt); partial_receipt.pop("live_migration_ledger")
    try:
        provision.validate_replacement_receipt(
            target, production, old, candidate, partial_receipt, source_manifest)
    except provision.ProvisioningRefusal:
        check("partial immutable receipt projection is refused", True)
    else:
        raise AssertionError("partial immutable receipt projection was accepted")
    drifted_source = dict(source_manifest)
    drifted_source["artifact_sha256"] = "sha256:" + "9" * 64
    try:
        provision.validate_replacement_receipt(
            target, production, old, candidate, exact_receipt, drifted_source)
    except provision.ProvisioningRefusal:
        check("receipt source-tree/schema projection must match merged source contract", True)
    else:
        raise AssertionError("receipt disagreed with merged source contract")

    candidate_root = provision.replacement_credential_root(CANDIDATE_OPERATION_ID)
    candidate_profiles = {
        label: provision.credential.profile(label, config_root=candidate_root)
        for label in ("reader", "writer")
    }
    canonical_profiles = {
        label: provision.credential.profile(label) for label in ("reader", "writer")
    }
    check("candidate app credentials live under the candidate operation private root",
          all(profile.paths.final.parent == candidate_root
              for profile in candidate_profiles.values()))
    check("candidate app credential paths never overwrite canonical old staging files",
          all(candidate_profiles[label].paths.final != canonical_profiles[label].paths.final
              for label in candidate_profiles))
    saved_scope_resolver = provision.replacement.resolve_existing_scopes
    try:
        provision.replacement.resolve_existing_scopes = lambda *_args, **_kwargs: (
            production, old, None)
        try:
            provision.resolve_replacement_binding(
                target, run=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                    AssertionError("source/credential fallback should not run")),
                environ={}, connect=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                    AssertionError("candidate DB fallback should not run")))
        except provision.ProvisioningRefusal:
            check("missing exact candidate refuses before any old-target fallback", True)
        else:
            raise AssertionError("missing candidate fell back to old staging")
    finally:
        provision.replacement.resolve_existing_scopes = saved_scope_resolver

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
            if "bulk" in args:
                return subprocess.CompletedProcess(args, 0, "bulk complete", "")
            return subprocess.CompletedProcess(
                args, 0, json.dumps([
                    {"name": "CARR_MCP_TOKEN", "type": "secret_text"},
                    {"name": "DATABASE_URL_WRITER", "type": "secret_text"},
                    {"name": "DATABASE_URL_READER", "type": "secret_text"},
                ]), ""
            )

    worker_runner = WorkerRunner()
    future_values = {
        "DATABASE_URL_READER": "future-reader-secret",
        "DATABASE_URL_WRITER": "future-writer-secret",
    }
    provision.bulk_worker_database_secrets(
        future_values, wrangler="wrangler", run=worker_runner,
        environ={"PATH": "/safe/bin", "HOME": "/safe/home",
                 "CLOUDFLARE_API_TOKEN": "cloudflare-token",
                 "UNSAFE_CHILD_SECRET": "must-not-travel"},
    )
    provision.verify_worker_database_secret_bindings(
        wrangler="wrangler", run=worker_runner,
        environ={"PATH": "/safe/bin", "HOME": "/safe/home",
                 "CLOUDFLARE_API_TOKEN": "cloudflare-token",
                 "UNSAFE_CHILD_SECRET": "must-not-travel"},
    )
    check("Worker publishes both database secrets in one stdin JSON bulk request",
          worker_runner.calls[0][0] == [
              "wrangler", "secret", "bulk", "--env", "staging",
              "--config", str(provision.WRANGLER_CONFIG), "--name", "carr-mcp-staging",
          ]
          and json.loads(worker_runner.calls[0][1]["input"]) == future_values
          and sum("bulk" in call[0] for call in worker_runner.calls) == 1
          and all("put" not in call[0] for call in worker_runner.calls)
          and worker_runner.calls[1][0] == [
              "wrangler", "secret", "list", "--env", "staging",
              "--config", str(provision.WRANGLER_CONFIG), "--name", "carr-mcp-staging",
              "--format", "json",
          ])
    check("Worker child environment is an exact allowlist with pinned account",
          all(set(call[1]["env"]) == {
              "PATH", "HOME", "CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID"
          } for call in worker_runner.calls)
          and all(call[1]["env"]["CLOUDFLARE_ACCOUNT_ID"]
                  == provision.CLOUDFLARE_ACCOUNT_ID for call in worker_runner.calls)
          and all("UNSAFE_CHILD_SECRET" not in call[1]["env"] for call in worker_runner.calls))
    serialized = worker_runner.calls[0][1]["input"]
    check("secret stdin JSON never reaches argv, stdout or stderr",
          all(secret not in json.dumps(worker_runner.calls[0][0])
              and secret not in worker_runner.calls[0][1].get("stdout", "")
              and secret not in worker_runner.calls[0][1].get("stderr", "")
              for secret in future_values.values())
          and all(secret in serialized for secret in future_values.values()))
    source = PROVISIONER.read_text(encoding="utf-8")
    check("sequential Worker secret put is absent from the provisioner source",
          '"secret", "put"' not in source and "put_worker_database_secret" not in source)

    class SecretFailureRunner:
        def __call__(self, args, **_kwargs):
            raise subprocess.TimeoutExpired(
                args, 60, output="future-reader-secret", stderr="future-writer-secret")
    try:
        provision.bulk_worker_database_secrets(
            future_values, wrangler="wrangler", run=SecretFailureRunner(), environ={})
    except provision.ProvisioningRefusal as exc:
        check("bulk timeout suppresses every secret and child output",
              all(secret not in str(exc) for secret in future_values.values()))
    else:
        raise AssertionError("bulk timeout escaped refusal boundary")

    class WrongBindingRunner:
        def __call__(self, args, **_kwargs):
            return subprocess.CompletedProcess(
                args, 0, json.dumps([
                    {"name": "DATABASE_URL_READER", "type": "secret_text"},
                    {"name": "DATABASE_URL_OLD", "type": "secret_text"},
                ]), "")
    try:
        provision.verify_worker_database_secret_bindings(
            wrangler="wrangler", run=WrongBindingRunner(), environ={})
    except provision.ProvisioningRefusal:
        check("Worker readback requires the exact two DATABASE_URL names", True)
    else:
        raise AssertionError("wrong Worker database secret name list was accepted")

    rollback_events: list[tuple[str, Any]] = []
    old_values = {
        "DATABASE_URL_READER": "old-reader-secret",
        "DATABASE_URL_WRITER": "old-writer-secret",
    }
    preservation_calls = 0
    def preservation() -> None:
        nonlocal preservation_calls
        preservation_calls += 1
        rollback_events.append(("preserve", preservation_calls))
        if preservation_calls == 2:
            raise provision.ProvisioningRefusal("provider identity changed")
    def bulk(values) -> None:
        rollback_events.append(("bulk", dict(values)))
    def verify() -> None:
        rollback_events.append(("verify", None))
    try:
        provision.publish_worker_cutover(
            future_values, old_values, preserve=preservation,
            bulk=bulk, verify=verify)
    except provision.ProvisioningRefusal:
        pass
    else:
        raise AssertionError("post-publish provider drift was accepted")
    check("post-publish refusal atomically restores both old Worker secrets",
          rollback_events == [
              ("preserve", 1), ("bulk", future_values), ("verify", None),
              ("preserve", 2), ("bulk", old_values), ("verify", None),
              ("preserve", 3),
          ])
    first_bulk_events: list[tuple[str, Any]] = []
    first_bulk_calls = 0
    def fail_first_bulk(values) -> None:
        nonlocal first_bulk_calls
        first_bulk_calls += 1
        first_bulk_events.append(("bulk", dict(values)))
        if first_bulk_calls == 1:
            raise provision.ProvisioningRefusal("uncertain candidate bulk")
    try:
        provision.publish_worker_cutover(
            future_values, old_values,
            preserve=lambda: first_bulk_events.append(("preserve", None)),
            bulk=fail_first_bulk,
            verify=lambda: first_bulk_events.append(("verify", None)))
    except provision.ProvisioningRefusal:
        pass
    else:
        raise AssertionError("initial bulk failure escaped rollback")
    check("initial candidate bulk uncertainty also restores the complete old pair",
          first_bulk_events == [
              ("preserve", None), ("bulk", future_values),
              ("bulk", old_values), ("verify", None), ("preserve", None),
          ])
    uncertain_events: list[tuple[str, Any]] = []
    uncertain_preserve_calls = 0
    injected_secret = "provider-post-restore-secret-detail"
    def uncertain_preservation() -> None:
        nonlocal uncertain_preserve_calls
        uncertain_preserve_calls += 1
        uncertain_events.append(("preserve", uncertain_preserve_calls))
        if uncertain_preserve_calls == 3:
            raise provision.ProvisioningRefusal(
                f"provider post-restore failed: {injected_secret}")
    def uncertain_bulk(values) -> None:
        uncertain_events.append(("bulk", dict(values)))
    def uncertain_verify() -> None:
        uncertain_events.append(("verify", None))
    try:
        provision.publish_worker_cutover(
            future_values, old_values, preserve=uncertain_preservation,
            bulk=uncertain_bulk, verify=uncertain_verify,
            postflight=lambda: (_ for _ in ()).throw(
                provision.ProvisioningRefusal("candidate postflight refused")),
        )
    except provision.ProvisioningRefusal as exc:
        check("post-restore provider refusal occurs only after old bulk and name verification",
              uncertain_events == [
                  ("preserve", 1), ("bulk", future_values), ("verify", None),
                  ("preserve", 2), ("bulk", old_values), ("verify", None),
                  ("preserve", 3),
              ])
        check("post-restore provider refusal reports fixed redacted uncertain outcome",
              str(exc) == (
                  "Worker credential cutover refused and rollback outcome is uncertain; "
                  "output suppressed")
              and injected_secret not in str(exc)
              and "provider post-restore failed" not in str(exc))
    else:
        raise AssertionError("post-restore provider uncertainty was accepted")
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            provision.parse_args([
                "--candidate-operation-id", str(CANDIDATE_OPERATION_ID),
                "--receipt-id", str(RECEIPT_ID), "--sha", EXPECTED_SHA,
                "--rollback-to-prior-staging",
            ])
    except SystemExit as exc:
        check("explicit prior-staging rollback requires the apply gate", exc.code == 2)
    else:
        raise AssertionError("rollback mode was accepted without --apply")

    candidate_scope = provision.ProviderScope(
        candidate.project_id, candidate.branch_id, candidate.endpoint_id,
        candidate.endpoint_host, 5432, "neondb")
    candidate_owner = provision.ScopedDsn(
        candidate_scope, "neondb_owner", candidate.endpoint_host, 5432, "neondb",
        f"postgresql://neondb_owner:candidate-owner-secret@{candidate.endpoint_host}/neondb?sslmode=require",  # ci-secret-scan: allow — hermetic non-routable fixture
    )
    binding_fixture = provision.ReplacementBinding(
        target, production, old, candidate, source_manifest, exact_receipt, candidate_owner)
    valid_state = provision.SeedState(
        (("approved", 60), ("pending", 2)), 3, 4)
    worker_lock = provision.worker_cutover_lock_path()
    active_locks: set[pathlib.Path] = set()
    lock_events: list[tuple[str, pathlib.Path]] = []

    @contextlib.contextmanager
    def exclusive_lock(path: pathlib.Path):
        if path in active_locks:
            raise provision.credential.CredentialRefusal("fixture lock is already held")
        active_locks.add(path)
        lock_events.append(("enter", path))
        try:
            yield
        finally:
            lock_events.append(("exit", path))
            active_locks.remove(path)

    try:
        with exclusive_lock(worker_lock):
            with exclusive_lock(worker_lock):
                raise AssertionError("overlapping Worker cutover lock was admitted")
    except provision.credential.CredentialRefusal:
        check("one global Worker lock refuses an overlapping candidate cutover", True)
    check("global Worker lock is outside every candidate credential root",
          worker_lock.parent == pathlib.Path.home() / ".config/carr"
          and worker_lock != candidate_root / ".staging-role-operation.lock")

    class FakeOwnerCursor:
        def __init__(self):
            self.statements: list[str] = []

        def execute(self, statement, _params=None) -> None:
            self.statements.append(str(statement))

        def fetchone(self):
            return (True,)

    class FakeOwner:
        def __init__(self):
            self.autocommit = False
            self.cur = FakeOwnerCursor()
            self.closed = False

        def cursor(self):
            return self.cur

        def commit(self) -> None:
            pass

        def rollback(self) -> None:
            pass

        def execute(self, statement, _params=None) -> None:
            self.cur.execute(statement, _params)

        def close(self) -> None:
            self.closed = True

    orchestration_events: list[tuple[str, Any]] = []
    profile_roots: list[pathlib.Path | None] = []
    preserve_calls = 0
    unlocked_preserve_budget = 0
    reader_candidate = (
        f"postgresql://app_reader:candidate-reader-secret@{candidate.endpoint_host}/neondb?sslmode=require"  # ci-secret-scan: allow — hermetic non-routable fixture
    )
    writer_candidate = (
        f"postgresql://app_writer:candidate-writer-secret@{candidate.endpoint_host}/neondb?sslmode=require"  # ci-secret-scan: allow — hermetic non-routable fixture
    )
    expected_candidate_values = {
        "DATABASE_URL_READER": reader_candidate,
        "DATABASE_URL_WRITER": writer_candidate,
    }
    saved_main_dependencies = {
        "reject_unsafe_environment": provision.reject_unsafe_environment,
        "replacement_target": provision.replacement_target,
        "resolve_replacement_binding": provision.resolve_replacement_binding,
        "load_rollback_worker_values": provision.load_rollback_worker_values,
        "bulk_worker_database_secrets": provision.bulk_worker_database_secrets,
        "verify_worker_database_secret_bindings": provision.verify_worker_database_secret_bindings,
        "read_seed_state": provision.read_seed_state,
        "connect": provision.psycopg.connect,
        "prove_provider_preservation": provision.replacement.prove_provider_preservation,
        "load_grants": provision.snapshot_grants.load_current_grants_to_role,
        "exclusive_lock": provision.credential.exclusive_lock,
        "credential_profile": provision.credential.profile,
        "load_existing": provision.credential.load_existing,
        "validate_profile_login": provision.validate_profile_login,
        "require_direct_owner_identity": provision.require_direct_owner_identity,
    }
    try:
        provision.reject_unsafe_environment = lambda _environment: None
        provision.replacement_target = lambda *_args, **_kwargs: target
        provision.resolve_replacement_binding = lambda *_args, **_kwargs: binding_fixture
        provision.load_rollback_worker_values = lambda _old: dict(old_values)
        provision.snapshot_grants.load_current_grants_to_role = lambda *_args, **_kwargs: []
        provision.credential.exclusive_lock = exclusive_lock

        # The default plan proves the full candidate binding but must stop before
        # credential, role, provider, or Worker mutations.
        provision.read_seed_state = lambda *_args, **_kwargs: valid_state
        provision.replacement.prove_provider_preservation = lambda *_args, **_kwargs: \
            (_ for _ in ()).throw(AssertionError("dry-run mutated/read provider scopes"))
        provision.load_rollback_worker_values = lambda *_args, **_kwargs: \
            (_ for _ in ()).throw(AssertionError("dry-run loaded secret credentials"))
        provision.credential.profile = lambda *_args, **_kwargs: \
            (_ for _ in ()).throw(AssertionError("dry-run opened candidate credential state"))
        provision.bulk_worker_database_secrets = lambda *_args, **_kwargs: \
            (_ for _ in ()).throw(AssertionError("dry-run mutated Worker secrets"))
        provision.psycopg.connect = lambda *_args, **_kwargs: \
            (_ for _ in ()).throw(AssertionError("dry-run opened candidate owner DB"))

        # An unreadable initial snapshot must refuse before any provider,
        # credential, role, or Worker mutation path becomes callable.
        provision.read_seed_state = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            provision.ProvisioningRefusal("synthetic initial read failure"))
        initial_stdout = io.StringIO()
        initial_stderr = io.StringIO()
        with contextlib.redirect_stdout(initial_stdout), contextlib.redirect_stderr(initial_stderr):
            initial_rc = provision.main([
                "--candidate-operation-id", str(CANDIDATE_OPERATION_ID),
                "--receipt-id", str(RECEIPT_ID), "--sha", EXPECTED_SHA, "--apply",
            ])
        check("initial business-state read failure refuses before every mutation path",
              initial_rc == 2 and initial_stdout.getvalue() == ""
              and "synthetic initial read failure" in initial_stderr.getvalue())

        provision.read_seed_state = lambda *_args, **_kwargs: valid_state
        dry_stdout = io.StringIO()
        with contextlib.redirect_stdout(dry_stdout):
            dry_rc = provision.main([
                "--candidate-operation-id", str(CANDIDATE_OPERATION_ID),
                "--receipt-id", str(RECEIPT_ID), "--sha", EXPECTED_SHA,
            ])
        dry_output = json.loads(dry_stdout.getvalue())
        check("main dry-run proves the exact candidate without any mutation path",
              dry_rc == 0 and dry_output["state"] == "dry_run"
              and dry_output["mutated"] is False
              and dry_output["candidate_project_id"] == candidate.project_id
              and dry_output["prior_staging_project_id"] == old.project_id
              and dry_output["production_project_id"] == production.project_id
              and dry_output["proposal_status"] == {"approved": 60, "pending": 2}
              and dry_output["target_count"] == 3 and dry_output["batch_count"] == 4)

        real_profile = saved_main_dependencies["credential_profile"]
        def candidate_profile(label: str, *, config_root=None):
            profile_roots.append(config_root)
            return real_profile(label, config_root=config_root)
        def load_candidate_credential(_paths, *, role_name, **_kwargs):
            value = reader_candidate if role_name == provision.READER_ROLE else writer_candidate
            return provision.credential.StoredCredential(
                "final", pathlib.Path("/fixture/final"), value, "fixture-password",
                candidate.endpoint_host, 5432, "neondb")
        provision.credential.profile = candidate_profile
        provision.credential.load_existing = load_candidate_credential
        provision.load_rollback_worker_values = lambda _old: dict(old_values)
        provision.validate_profile_login = lambda *_args, **_kwargs: None
        provision.require_direct_owner_identity = lambda _cur: "neondb_owner"
        provision.psycopg.connect = lambda *_args, **_kwargs: FakeOwner()
        def record_preservation(*_args, **_kwargs) -> None:
            nonlocal preserve_calls
            preserve_calls += 1
            if preserve_calls > unlocked_preserve_budget and worker_lock not in active_locks:
                raise AssertionError("provider preservation ran outside the global Worker lock")
            orchestration_events.append(("preserve", None))
        provision.replacement.prove_provider_preservation = record_preservation
        def record_bulk(values) -> None:
            if worker_lock not in active_locks:
                raise AssertionError("Worker bulk ran outside the global cutover lock")
            orchestration_events.append(("bulk", dict(values)))
        provision.bulk_worker_database_secrets = record_bulk
        def record_verify() -> None:
            if worker_lock not in active_locks:
                raise AssertionError("Worker readback ran outside the global cutover lock")
            orchestration_events.append(("verify", None))
        provision.verify_worker_database_secret_bindings = record_verify

        # A successful main apply uses candidate-root credentials, one atomic
        # Worker pair, provider pre/post proof, and the complete final readback.
        seed_reads = 0
        preserve_calls = 0
        unlocked_preserve_budget = 1
        def successful_seed_read(*_args, **_kwargs):
            nonlocal seed_reads
            seed_reads += 1
            if seed_reads == 2 and worker_lock not in active_locks:
                raise AssertionError("final seed readback ran outside the global Worker lock")
            orchestration_events.append(("seed", seed_reads))
            return valid_state
        provision.read_seed_state = successful_seed_read
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            rc = provision.main([
                "--candidate-operation-id", str(CANDIDATE_OPERATION_ID),
                "--receipt-id", str(RECEIPT_ID), "--sha", EXPECTED_SHA, "--apply",
            ])
        apply_output = json.loads(stdout.getvalue())
        check("main apply routes both credentials only through the candidate root",
              profile_roots == [candidate_root, candidate_root]
              and all(root != pathlib.Path.home() / ".config/carr" for root in profile_roots))
        check("main apply performs one atomic candidate pair with provider pre/post proof",
              rc == 0 and orchestration_events == [
                  ("seed", 1), ("preserve", None), ("preserve", None),
                  ("bulk", expected_candidate_values), ("verify", None),
                  ("preserve", None), ("seed", 2),
              ])
        check("main apply completes exact final readback without fallback or disclosure",
              apply_output["state"] == "provisioned" and seed_reads == 2
              and apply_output["candidate_operation_id"] == str(CANDIDATE_OPERATION_ID)
              and apply_output["proposal_status"] == {"approved": 60, "pending": 2}
              and apply_output["target_count"] == 3 and apply_output["batch_count"] == 4
              and all(secret not in stdout.getvalue()
                      for secret in expected_candidate_values.values()))

        # Every field in the readable business-state snapshot is immutable
        # across credential publication. Any drift restores the prior pair.
        for drift_label, drift_state in (
            ("proposal", provision.SeedState(
                (("approved", 61), ("pending", 1)), 3, 4)),
            ("target", provision.SeedState(
                (("approved", 60), ("pending", 2)), 4, 4)),
            ("batch", provision.SeedState(
                (("approved", 60), ("pending", 2)), 3, 5)),
        ):
            orchestration_events.clear()
            profile_roots.clear()
            preserve_calls = 0
            unlocked_preserve_budget = 1
            drift_reads = 0
            def drifting_seed_read(*_args, **_kwargs):
                nonlocal drift_reads
                drift_reads += 1
                if drift_reads == 2 and worker_lock not in active_locks:
                    raise AssertionError(
                        "drifted business-state read ran outside the global Worker lock")
                orchestration_events.append(("seed", drift_reads))
                return valid_state if drift_reads == 1 else drift_state
            provision.read_seed_state = drifting_seed_read
            drift_stdout = io.StringIO()
            drift_stderr = io.StringIO()
            with contextlib.redirect_stdout(drift_stdout), \
                    contextlib.redirect_stderr(drift_stderr):
                drift_rc = provision.main([
                    "--candidate-operation-id", str(CANDIDATE_OPERATION_ID),
                    "--receipt-id", str(RECEIPT_ID), "--sha", EXPECTED_SHA, "--apply",
                ])
            check(f"main {drift_label} drift restores the exact prior Worker pair",
                  drift_rc == 2 and orchestration_events == [
                      ("seed", 1), ("preserve", None), ("preserve", None),
                      ("bulk", expected_candidate_values), ("verify", None),
                      ("preserve", None), ("seed", 2),
                      ("bulk", old_values), ("verify", None), ("preserve", None),
                  ] and all(secret not in drift_stdout.getvalue() + drift_stderr.getvalue()
                            for secret in (*expected_candidate_values.values(),
                                           *old_values.values())))

        # If the complete final seed readback refuses, main must atomically
        # restore and verify the old pair before re-proving provider preservation.
        orchestration_events.clear()
        profile_roots.clear()
        failure_reads = 0
        preserve_calls = 0
        unlocked_preserve_budget = 1
        def failing_final_seed_read(*_args, **_kwargs):
            nonlocal failure_reads
            failure_reads += 1
            if failure_reads == 2 and worker_lock not in active_locks:
                raise AssertionError("failed final readback ran outside the global Worker lock")
            orchestration_events.append(("seed", failure_reads))
            if failure_reads == 2:
                raise provision.ProvisioningRefusal("synthetic final readback failure")
            return valid_state
        provision.read_seed_state = failing_final_seed_read
        failure_stdout = io.StringIO()
        failure_stderr = io.StringIO()
        with contextlib.redirect_stdout(failure_stdout), contextlib.redirect_stderr(failure_stderr):
            failure_rc = provision.main([
                "--candidate-operation-id", str(CANDIDATE_OPERATION_ID),
                "--receipt-id", str(RECEIPT_ID), "--sha", EXPECTED_SHA, "--apply",
            ])
        check("main final-readback refusal restores and provider-verifies the old pair",
              failure_rc == 2 and orchestration_events == [
                  ("seed", 1), ("preserve", None), ("preserve", None),
                  ("bulk", expected_candidate_values), ("verify", None),
                  ("preserve", None), ("seed", 2),
                  ("bulk", old_values), ("verify", None), ("preserve", None),
              ])
        check("final-readback rollback suppresses both candidate and prior secrets",
              all(secret not in failure_stdout.getvalue() + failure_stderr.getvalue()
                  for secret in (*expected_candidate_values.values(), *old_values.values())))

        # Explicit rollback shares the same global Worker lock and does no
        # candidate credential or database role work.
        orchestration_events.clear()
        preserve_calls = 0
        unlocked_preserve_budget = 0
        provision.credential.profile = lambda *_args, **_kwargs: \
            (_ for _ in ()).throw(AssertionError("rollback opened candidate credentials"))
        provision.read_seed_state = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("rollback must not read/converge candidate roles"))
        provision.psycopg.connect = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("rollback must not open candidate owner DB"))
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            rc = provision.main([
                "--candidate-operation-id", str(CANDIDATE_OPERATION_ID),
                "--receipt-id", str(RECEIPT_ID), "--sha", EXPECTED_SHA,
                "--rollback-to-prior-staging", "--apply",
            ])
        rollback_output = json.loads(stdout.getvalue())
        check("explicit rollback uses one atomic old-pair bulk with pre/post preservation",
              rc == 0 and orchestration_events == [
                  ("preserve", None), ("bulk", old_values), ("verify", None),
                  ("preserve", None),
              ])
        check("explicit rollback performs no candidate role mutation or secret disclosure",
              rollback_output["candidate_roles_mutated"] is False
              and all(secret not in stdout.getvalue() for secret in old_values.values()))
    finally:
        provision.reject_unsafe_environment = saved_main_dependencies["reject_unsafe_environment"]
        provision.replacement_target = saved_main_dependencies["replacement_target"]
        provision.resolve_replacement_binding = saved_main_dependencies["resolve_replacement_binding"]
        provision.load_rollback_worker_values = saved_main_dependencies["load_rollback_worker_values"]
        provision.bulk_worker_database_secrets = saved_main_dependencies["bulk_worker_database_secrets"]
        provision.verify_worker_database_secret_bindings = saved_main_dependencies[
            "verify_worker_database_secret_bindings"]
        provision.read_seed_state = saved_main_dependencies["read_seed_state"]
        provision.psycopg.connect = saved_main_dependencies["connect"]
        provision.replacement.prove_provider_preservation = saved_main_dependencies[
            "prove_provider_preservation"]
        provision.snapshot_grants.load_current_grants_to_role = saved_main_dependencies["load_grants"]
        provision.credential.exclusive_lock = saved_main_dependencies["exclusive_lock"]
        provision.credential.profile = saved_main_dependencies["credential_profile"]
        provision.credential.load_existing = saved_main_dependencies["load_existing"]
        provision.validate_profile_login = saved_main_dependencies["validate_profile_login"]
        provision.require_direct_owner_identity = saved_main_dependencies[
            "require_direct_owner_identity"]
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

    original_target = provision.replacement_target
    original_reject = provision.reject_unsafe_environment
    try:
        provision.reject_unsafe_environment = lambda _environment: None

        def dependency_exit(*_args, **_kwargs):
            raise SystemExit("https://provider.invalid/secret-output")

        provision.replacement_target = dependency_exit
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            return_code = provision.main([
                "--candidate-operation-id", str(CANDIDATE_OPERATION_ID),
                "--receipt-id", str(RECEIPT_ID), "--sha", EXPECTED_SHA,
            ])
        check("provider dependency SystemExit is caught without leaking output",
              return_code == 2 and "secret-output" not in stderr.getvalue())
    finally:
        provision.replacement_target = original_target
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
