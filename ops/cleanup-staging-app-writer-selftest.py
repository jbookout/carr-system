#!/usr/bin/env python3
"""Hermetic refusal and crash tests for the one-purpose staging cleanup."""

from __future__ import annotations

import dataclasses
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import tempfile


REPO = pathlib.Path(__file__).resolve().parents[1]
CLEANUP = REPO / "tools" / "cleanup-staging-app-writer.py"


def load_module():
    spec = importlib.util.spec_from_file_location("cleanup_staging_app_writer", CLEANUP)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {CLEANUP}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    cleanup = load_module()
    checked = 0

    def check(label: str, condition: bool) -> None:
        nonlocal checked
        checked += 1
        if not condition:
            raise AssertionError(label)
        print(f"  ok  {label}")

    scope = cleanup.provision.ProviderScope(
        "staging-project", "staging-main", "ep-fixture",
        "ep-fixture.c-10.us-east-1.aws.neon.tech", 5432, "neondb",
    )
    row = {
        "authentication_method": "password",
        "branch_id": scope.branch_id,
        "created_at": "2026-08-17T00:00:00Z",
        "name": "app_writer",
        "protected": False,
        "updated_at": "2026-08-17T00:00:01Z",
    }
    check("official neonctl six-field bare role-list object is the only admitted row shape",
          cleanup.exact_provider_role([row], scope) == row)
    missing_rows = [{key: value for key, value in row.items() if key != missing} for missing in row]
    wrong_types = [
        {**row, "authentication_method": 1},
        {**row, "branch_id": 1},
        {**row, "created_at": 1},
        {**row, "name": 1},
        {**row, "protected": 0},
        {**row, "updated_at": 1},
    ]
    for bad in (
        {"roles": [row]},
        *([candidate] for candidate in missing_rows),
        *([candidate] for candidate in wrong_types),
        [{**row, "id": "invented"}],
        [{**row, "name": "app_reader"}],
        [{**row, "branch_id": "another-branch"}],
        [{**row, "authentication_method": "scram-sha-256"}],
        [{**row, "protected": True}],
        [{**row, "created_at": "not-a-timestamp"}],
        [{**row, "created_at": "2026-08-17T00:00:00"}],
        [{**row, "updated_at": "2026-02-30T00:00:00Z"}],
        [row, row],
        [],
    ):
        try:
            cleanup.exact_provider_role(bad, scope)
        except cleanup.CleanupRefusal:
            pass
        else:
            raise AssertionError(f"unsafe provider role shape accepted: {bad!r}")
    check("wrong/missing/extra/branch/auth/protected/type/timestamp rows refuse", True)

    good = cleanup.ProviderManagedFingerprint(
        can_login=True,
        inherits_privileges=True,
        powerful_attributes=("createdb", "createrole", "replication", "bypassrls"),
        role_config=(),
        memberships=(("neon_superuser", False, True, True, "cloud_admin"),),
        reachable_roles=cleanup.EXPECTED_PROVIDER_REACHABLE_ROLES,
        inbound_memberships=(),
        direct_acl_facts=(),
        owned_objects=(),
        shared_dependencies=(),
        reader_role_exists=False,
        active_sessions=0,
    )
    cleanup.validate_provider_managed_fingerprint(good)
    check("only the exact observed provider-managed fingerprint is deletable", True)
    negatives = (
        dataclasses.replace(good, powerful_attributes=()),
        dataclasses.replace(good, memberships=(("carr_writer", False, True, True, "neondb_owner"),)),
        dataclasses.replace(good, inbound_memberships=(("neondb_owner", True, False, False, "neondb_owner"),)),
        dataclasses.replace(good, direct_acl_facts=(("table", "public.actor", "select", False),)),
        dataclasses.replace(good, owned_objects=(("1", "pg_class", "2"),)),
        dataclasses.replace(good, shared_dependencies=(("1", "pg_database", "2", "a"),)),
        dataclasses.replace(good, reader_role_exists=True),
        dataclasses.replace(good, role_config=("statement_timeout=60s",)),
        dataclasses.replace(good, active_sessions=1),
    )
    for candidate in negatives:
        try:
            cleanup.validate_provider_managed_fingerprint(candidate)
        except cleanup.CleanupRefusal:
            pass
        else:
            raise AssertionError(f"unsafe cleanup fingerprint accepted: {candidate!r}")
    check("SQL-created, in-use, configured, owned, ACL-bearing and drifted roles refuse", True)
    cleanup.require_quiescent_reads((0, 0))
    for readings in ((0,), (1, 0), (0, 1), (0, 0, 0)):
        try:
            cleanup.require_quiescent_reads(readings)
        except cleanup.CleanupRefusal:
            pass
        else:
            raise AssertionError(f"bad quiescence readings accepted: {readings}")
    check("cleanup requires exactly two separated zero-session reads", True)

    class Runner:
        def __init__(self, *, delete_timeout: bool = False, absent_after: bool = True):
            self.calls: list[tuple[list[str], dict]] = []
            self.delete_timeout = delete_timeout
            self.absent_after = absent_after

        def __call__(self, args, **kwargs):
            self.calls.append((list(args), kwargs))
            if "secret" in args and "put" in args:
                return subprocess.CompletedProcess(args, 0, "secret output", "secret error")
            if "secret" in args and "list" in args:
                return subprocess.CompletedProcess(
                    args, 0,
                    '[{"name":"DATABASE_URL_READER","type":"secret_text"},'
                    '{"name":"DATABASE_URL_WRITER","type":"secret_text"}]', ""
                )
            if args[1:3] == ["roles", "delete"]:
                if self.delete_timeout:
                    raise subprocess.TimeoutExpired(args, 60, output="secret", stderr="secret")
                return subprocess.CompletedProcess(args, 0, json.dumps(row), "")
            raise AssertionError(args)

    runner = Runner()
    cleanup.quiesce_worker_database_secret(
        "DATABASE_URL_READER", "future-reader-value", wrangler="wrangler", run=runner,
    )
    cleanup.quiesce_worker_database_secret(
        "DATABASE_URL_WRITER", "future-writer-value", wrangler="wrangler", run=runner,
    )
    cleanup.verify_worker_database_bindings(
        ("DATABASE_URL_READER", "DATABASE_URL_WRITER"), wrangler="wrangler", run=runner,
    )
    check("Worker reader then writer are overwritten through stdin with exact pinned target",
          runner.calls[0][0] == [
              "wrangler", "secret", "put", "DATABASE_URL_READER", "--env", "staging",
              "--config", str(cleanup.WRANGLER_CONFIG),
              "--name", "carr-mcp-staging",
          ]
          and runner.calls[1][0] == [
              "wrangler", "secret", "put", "DATABASE_URL_WRITER", "--env", "staging",
              "--config", str(cleanup.WRANGLER_CONFIG),
              "--name", "carr-mcp-staging",
          ]
          and runner.calls[0][1].get("input") == "future-reader-value"
          and runner.calls[1][1].get("input") == "future-writer-value"
          and all("future-" not in " ".join(call[0]) for call in runner.calls[:2])
          and all(call[1].get("env", {}).get("CLOUDFLARE_ACCOUNT_ID")
                  == cleanup.provision.CLOUDFLARE_ACCOUNT_ID for call in runner.calls[:2]))
    check("both quiesced Worker secret names read back without revealing values",
          runner.calls[2][0] == [
              "wrangler", "secret", "list", "--env", "staging",
              "--config", str(cleanup.WRANGLER_CONFIG),
              "--name", "carr-mcp-staging", "--format", "json",
          ])

    delete_runner = Runner()
    cleanup.delete_provider_role_once(scope, neonctl="neonctl", run=delete_runner, environ={})
    check("provider deletion names exact project, branch and app_writer once",
          delete_runner.calls[0][0] == [
              "neonctl", "roles", "delete", "app_writer", "--project-id", "staging-project",
              "--branch", "staging-main", "--output", "json",
          ])

    states = {"reader": "absent", "writer": "absent"}
    digest, receipt = cleanup.cleanup_fingerprint(scope, row, good, states)
    digest_again, _ = cleanup.cleanup_fingerprint(scope, row, good, states)
    changed_provider_digest, _ = cleanup.cleanup_fingerprint(
        scope, {**row, "updated_at": "2026-08-17T00:00:02Z"}, good, states
    )
    changed, _ = cleanup.cleanup_fingerprint(
        scope, row, good, {"reader": "pending", "writer": "pending"}
    )
    check("dry-run fingerprint binds full scope/provider/database/both credential states",
          len(digest) == 64 and digest == digest_again
          and digest != changed and digest != changed_provider_digest
          and receipt["project_id"] == scope.project_id
          and receipt["endpoint_id"] == scope.endpoint_id
          and receipt["endpoint_host"] == scope.endpoint_host
          and receipt["pending_credential_states"] == states)
    for bad_states in (
        {"reader": "final", "writer": "pending"},
        {"reader": "pending"},
    ):
        try:
            cleanup.cleanup_fingerprint(scope, row, good, bad_states)
        except cleanup.CleanupRefusal:
            pass
        else:
            raise AssertionError("final or incomplete two-profile credential state was accepted")
    cleanup.require_expected_fingerprint(apply=False, expected=None, actual=digest)
    cleanup.require_expected_fingerprint(apply=True, expected=digest, actual=digest)
    for expected in (None, "0" * 64):
        try:
            cleanup.require_expected_fingerprint(apply=True, expected=expected, actual=digest)
        except cleanup.CleanupRefusal:
            pass
        else:
            raise AssertionError("missing/stale cleanup fingerprint was accepted")
    check("irreversible apply requires the exact fresh dry-run fingerprint", True)

    with tempfile.TemporaryDirectory() as raw:
        root = pathlib.Path(raw)
        os.chmod(root, 0o700)
        owner = cleanup.provision.ScopedDsn(
            scope, "neondb_owner", scope.endpoint_host, scope.port, scope.database,
            f"postgresql://neondb_owner:owner@{scope.endpoint_host}/neondb?sslmode=require",
        )
        first_pending = cleanup.prepare_containment_credentials(owner, config_root=root)
        second_pending = cleanup.prepare_containment_credentials(owner, config_root=root)
        check("cleanup creates reader then writer pending credentials and resumes both exactly",
              list(first_pending) == ["reader", "writer"]
              and all(first_pending[label].value == second_pending[label].value
                      for label in ("reader", "writer")))

        state_path = root / ".cleanup-state.json"
        durable = cleanup.cleanup_state_from_receipt(scope, digest, receipt, "prepared")
        for phase in cleanup.CLEANUP_STATE_PHASES:
            durable = dataclasses.replace(durable, phase=phase)
            cleanup.write_cleanup_state(state_path, durable)
            loaded = cleanup.read_cleanup_state(state_path, scope)
            if loaded != durable:
                raise AssertionError(f"durable cleanup state did not round-trip phase {phase}")
        for crash_boundary in ("after_open", "after_write", "after_fsync", "after_publish"):
            def crash(boundary: str) -> None:
                if boundary == crash_boundary:
                    raise SystemExit("simulated hard stop")
            try:
                cleanup.write_cleanup_state(
                    state_path, dataclasses.replace(durable, phase="contained"), boundary=crash
                )
            except SystemExit:
                pass
            else:
                raise AssertionError(f"cleanup state {crash_boundary} did not stop")
            cleanup.write_cleanup_state(
                state_path, dataclasses.replace(durable, phase="delete_intent")
            )
            if cleanup.read_cleanup_state(state_path, scope).phase != "delete_intent":
                raise AssertionError(f"cleanup state did not resume after {crash_boundary}")
        check("cleanup receipt resumes after every atomic file boundary", True)
        intent = dataclasses.replace(durable, phase="delete_intent")
        cleanup.write_cleanup_state(state_path, intent)
        intent_runner = Runner()
        called = cleanup.issue_provider_delete_from_intent(
            scope, state_path, intent, neonctl="neonctl", run=intent_runner, environ={},
            verify_present=lambda rows: cleanup.exact_provider_role(rows, scope),
        )
        check("crash after durable delete intent resumes with one fresh-state-authorized call",
              called.phase == "delete_called"
              and len(intent_runner.calls) == 1
              and intent_runner.calls[0][0][1:3] == ["roles", "delete"])

        class RetryRunner:
            def __init__(self, provider_row):
                self.provider_row = provider_row
                self.delete_calls = 0
                self.calls: list[list[str]] = []
            def __call__(self, args, **kwargs):
                self.calls.append(list(args))
                if args[1:3] == ["roles", "delete"]:
                    self.delete_calls += 1
                    if self.delete_calls == 1:
                        raise subprocess.TimeoutExpired(args, 60)
                    return subprocess.CompletedProcess(args, 0, "{}", "")
                if args[1:3] == ["roles", "list"]:
                    return subprocess.CompletedProcess(args, 0, json.dumps([self.provider_row]), "")
                raise AssertionError(args)

        cleanup.write_cleanup_state(state_path, intent)
        retry_runner = RetryRunner(row)
        called = cleanup.issue_provider_delete_from_intent(
            scope, state_path, intent, neonctl="neonctl", run=retry_runner, environ={},
            verify_present=lambda rows: (
                cleanup.exact_provider_role(rows, scope) == row
                or (_ for _ in ()).throw(cleanup.CleanupRefusal("changed row"))
            ),
            sleep=lambda _seconds: None,
        )
        check("timeout with exact provider target retries boundedly after fresh validation",
              called.phase == "delete_called" and retry_runner.delete_calls == 2)
        changed_runner = RetryRunner({**row, "created_at": "2026-08-17T01:00:00Z"})
        cleanup.write_cleanup_state(state_path, intent)
        try:
            cleanup.issue_provider_delete_from_intent(
                scope, state_path, intent, neonctl="neonctl", run=changed_runner, environ={},
                verify_present=lambda rows: (
                    cleanup.exact_provider_role(rows, scope) == row
                    or (_ for _ in ()).throw(cleanup.CleanupRefusal("changed row"))
                ), sleep=lambda _seconds: None,
            )
        except cleanup.CleanupRefusal:
            check("timeout refuses a recreated provider row before any retry",
                  changed_runner.delete_calls == 1)
        else:
            raise AssertionError("changed provider row was retried")
        cleanup.remove_cleanup_state(state_path)
        check("durable nonsecret cleanup receipt round-trips every crash phase", not state_path.exists())

    class RoleListRunner:
        def __init__(self, states):
            self.states = iter(states)
            self.calls: list[list[str]] = []
        def __call__(self, args, **kwargs):
            self.calls.append(list(args))
            present = next(self.states)
            payload = [row] if present else []
            return subprocess.CompletedProcess(args, 0, json.dumps(payload), "")

    poll_runner = RoleListRunner((True, False))
    sleeps: list[float] = []
    cleanup.poll_provider_role_absent(
        scope, neonctl="neonctl", run=poll_runner, environ={}, attempts=2,
        sleep=sleeps.append,
    )
    check("async deletion polls boundedly until provider absence",
          len(poll_runner.calls) == 2 and sleeps == [1])
    stuck_runner = RoleListRunner((True, True))
    try:
        cleanup.poll_provider_role_absent(
            scope, neonctl="neonctl", run=stuck_runner, environ={}, attempts=2,
            sleep=lambda _seconds: None,
        )
    except cleanup.CleanupRefusal:
        check("provider-only poll exhaustion refuses", len(stuck_runner.calls) == 2)
    else:
        raise AssertionError("persistent provider role was accepted")
    timeout_runner = Runner(delete_timeout=True)
    try:
        cleanup.delete_provider_role_once(
            scope, neonctl="neonctl", run=timeout_runner, environ={},
        )
    except cleanup.CleanupRefusal:
        pass
    else:
        raise AssertionError("provider delete timeout was accepted as success")
    absent_runner = RoleListRunner((False,))
    cleanup.poll_provider_role_absent(
        scope, neonctl="neonctl", run=absent_runner, environ={}, attempts=1,
    )
    check("raw timeout remains unknown until provider read-state",
          len(timeout_runner.calls) == 1
          and timeout_runner.calls[0][0][1:3] == ["roles", "delete"]
          and all(call[1:3] == ["roles", "list"] for call in absent_runner.calls))

    class LagCursor:
        def __init__(self, rows):
            self.rows = iter(rows)
            self.last = None
        def execute(self, _statement, _params=None):
            self.last = next(self.rows)
        def fetchone(self):
            return self.last

    lag_runner = RoleListRunner((False, False))
    lag_sleeps: list[float] = []
    cleanup.poll_provider_and_database_absent(
        scope, LagCursor(((123,), (None,))), neonctl="neonctl", run=lag_runner,
        environ={}, attempts=2, sleep=lag_sleeps.append,
    )
    check("provider-absent resume polls delayed database disappearance without re-delete",
          lag_sleeps == [1] and len(lag_runner.calls) == 2
          and all(call[1:3] == ["roles", "list"] for call in lag_runner.calls))

    source = CLEANUP.read_text(encoding="utf-8")
    provision_source = (REPO / "tools" / "provision-staging-app-writer.py").read_text(encoding="utf-8")
    check("cleanup carries local+database locks and provisioner has no provider create path",
          "pg_advisory_lock" in source and "exclusive_lock" in source
          and '"roles", "create"' not in provision_source)
    check("cleanup inspects all non-template databases and every shared role dependency",
          "where not datistemplate" in source and "datallowconn" not in source
          and "d.deptype::text" in source and "shared_dependencies" in source)
    print(f"PASS: staging app_writer cleanup self-test ({checked} checks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
