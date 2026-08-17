#!/usr/bin/env python3
"""Hermetic tests for file-only staging DB consumers and Worker secret ownership."""

from __future__ import annotations

import contextlib
import importlib.util
import json
import os
import pathlib
import stat
import subprocess
import sys
import tempfile


REPO = pathlib.Path(__file__).resolve().parents[1]
DB_TAP = REPO / "tools/db-tap.py"
LOGIN_GATE = REPO / "ops/staging-database-login-provision-db-gate.py"


def load_module():
    spec = importlib.util.spec_from_file_location("staging_database_consumer_db_tap", DB_TAP)
    if spec is None or spec.loader is None:
        raise RuntimeError(DB_TAP)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@contextlib.contextmanager
def environment(**updates):
    before = os.environ.copy()
    try:
        os.environ.clear()
        os.environ.update(before)
        for key, value in updates.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        yield
    finally:
        os.environ.clear()
        os.environ.update(before)


def main() -> int:
    db_tap = load_module()
    login_gate_spec = importlib.util.spec_from_file_location(
        "staging_database_login_gate_guard", LOGIN_GATE
    )
    if login_gate_spec is None or login_gate_spec.loader is None:
        raise RuntimeError(LOGIN_GATE)
    login_gate = importlib.util.module_from_spec(login_gate_spec)
    sys.modules[login_gate_spec.name] = login_gate
    login_gate_spec.loader.exec_module(login_gate)
    checked = 0

    def check(label: str, condition: bool) -> None:
        nonlocal checked
        checked += 1
        if not condition:
            raise AssertionError(label)
        print(f"  ok  {label}")

    class Runner:
        def __init__(self, project_id="staging-project"):
            self.calls: list[list[str]] = []
            self.project_id = project_id

        def __call__(self, args, **kwargs):
            self.calls.append(list(args))
            if args[1:3] == ["projects", "list"]:
                payload = [{"id": self.project_id, "name": "carr-staging"}]
            elif args[1:3] == ["branches", "list"]:
                payload = [{"id": "staging-main", "project_id": "staging-project",
                            "name": "main", "default": True}]
            elif args[1] == "api":
                payload = {"endpoints": [{"id": "ep-fixture", "branch_id": "staging-main",
                                            "type": "read_write",
                                            "host": "ep-fixture.c-10.us-east-1.aws.neon.tech"}]}
            else:
                raise AssertionError(args)
            return subprocess.CompletedProcess(args, 0, json.dumps(payload), "")

    with tempfile.TemporaryDirectory() as raw:
        root = pathlib.Path(raw)
        os.chmod(root, 0o700)
        writer = root / "staging-writer.env"
        value = (
            "postgresql://app_writer:fixture-secret@ep-fixture.c-10.us-east-1.aws.neon.tech:5432/"  # ci-secret-scan: allow — hermetic fixture
            "neondb?sslmode=require&channel_binding=require"
        )  # ci-secret-scan: allow — hermetic synthetic credential
        writer.write_text("CARR_DB_STAGING_WRITER_URL=" + value + "\n", encoding="utf-8")
        os.chmod(writer, 0o600)
        runner = Runner()
        original_run = db_tap.subprocess.run
        original_profile = db_tap.staging_credential.profile
        db_tap.subprocess.run = runner
        db_tap.staging_credential.profile = lambda label: original_profile(label, config_root=root)
        try:
            with environment(NEON_API_KEY="fixture-provider-key"):
                actual = db_tap.dsn(project="staging", role_name="app_writer")
            check("staging app_writer reads only the verified final credential", actual == value)
            check("staging app_writer never invokes provider connection-string reveal",
                  all("connection-string" not in call for call in runner.calls))
            check("credential is bound to exact project, default main and read-write endpoint",
                  any(call[1:3] == ["projects", "list"] for call in runner.calls)
                  and any(call[1:3] == ["branches", "list"] for call in runner.calls)
                  and any(call[1] == "api" and "/staging-project/branches/staging-main/endpoints" in call[2]
                          for call in runner.calls))
        finally:
            db_tap.subprocess.run = original_run
            db_tap.staging_credential.profile = original_profile

        writer.rename(pathlib.Path(str(writer) + ".pending"))
        runner = Runner()
        db_tap.subprocess.run = runner
        db_tap.staging_credential.profile = lambda label: original_profile(label, config_root=root)
        try:
            with environment(NEON_API_KEY="fixture-provider-key"):
                try:
                    db_tap.dsn(project="staging", role_name="app_writer")
                except (SystemExit, db_tap.staging_credential.CredentialRefusal):
                    check("db-tap refuses a pending/unverified runtime credential", True)
                else:
                    raise AssertionError("pending credential was accepted")
        finally:
            db_tap.subprocess.run = original_run
            db_tap.staging_credential.profile = original_profile

    staging_secrets = (REPO / "bin/staging-secrets.sh").read_text(encoding="utf-8")
    check("staging token rotation cannot put or derive either database secret",
          "secret put DATABASE_URL_WRITER" not in staging_secrets
          and "secret put DATABASE_URL_READER" not in staging_secrets
          and "connection-string" not in staging_secrets)
    production_runner = Runner(project_id=str(db_tap.PROJECTS["production"]["id"]))
    original_run = db_tap.subprocess.run
    db_tap.subprocess.run = production_runner
    try:
        with environment(NEON_API_KEY="fixture-provider-key"):
            try:
                db_tap._staging_runtime_target({})
            except SystemExit:
                check("staging runtime target refuses the canonical Production project id", True)
            else:
                raise AssertionError("canonical Production project id was accepted as staging")
    finally:
        db_tap.subprocess.run = original_run

    provider_secret = "provider-stderr-secret"
    for failing_run in (
        lambda args, **kwargs: subprocess.CompletedProcess(args, 1, "", provider_secret),
        lambda args, **kwargs: (_ for _ in ()).throw(
            subprocess.TimeoutExpired(args, 60, output=provider_secret, stderr=provider_secret)
        ),
    ):
        db_tap.subprocess.run = failing_run
        try:
            try:
                db_tap._staging_runtime_target({})
            except SystemExit as exc:
                if provider_secret in str(exc):
                    raise AssertionError("provider stderr leaked from staging target resolution")
            else:
                raise AssertionError("provider failure was accepted")
        finally:
            db_tap.subprocess.run = original_run
    check("staging target lookup suppresses provider stderr and timeout payloads", True)
    login_gate.require_loopback("postgresql://postgres@127.0.0.1:5432/postgres")
    for unsafe_dsn in (
        "postgresql://postgres@127.0.0.1:5432/postgres?host=remote.invalid",
        "postgresql://postgres@127.0.0.1:5432/postgres?hostaddr=203.0.113.8",
        "postgresql://postgres@127.0.0.1:5432/postgres?host=127.0.0.1,203.0.113.8",
        "service=unsafe",
    ):
        try:
            login_gate.require_loopback(unsafe_dsn)
        except RuntimeError:
            pass
        else:
            raise AssertionError(f"loopback guard accepted libpq override: {unsafe_dsn}")
    check("disposable DB gate rejects remote host/hostaddr, multi-host and service overrides", True)
    print(f"PASS: staging database consumer self-test ({checked} checks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
