#!/usr/bin/env python3
"""Hermetic contract tests for the staging-only retrieval doctrine seeder."""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import os
import pathlib
import sys
import tempfile
from dataclasses import replace


REPO = pathlib.Path(__file__).resolve().parent.parent
SEEDER = REPO / "pipelines" / "staging_retrieval_doctrine_seed.py"


def load_seeder():
    spec = importlib.util.spec_from_file_location("staging_retrieval_doctrine_seed", SEEDER)
    assert spec and spec.loader, "staging retrieval doctrine seeder is missing"
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def check(label: str, condition: bool) -> None:
    print(f"  {'ok' if condition else 'FAIL'}  {label}")
    if not condition:
        raise AssertionError(label)


@contextlib.contextmanager
def altered_environ(**values: str | None):
    old = {key: os.environ.get(key) for key in values}
    try:
        for key, value in values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        yield
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


seed = load_seeder()

with tempfile.TemporaryDirectory() as tmp:
    root = pathlib.Path(tmp)
    old_vault = seed.VAULT
    seed.VAULT = root
    runbook = root / "runbook.md"
    runbook.write_text(
        "# Runbook\n\n## Ignore this\n\nnot a target\n\n"
        "## Diagnosis checklist (in order, 2 minutes)\n\nexact target\n",
        encoding="utf-8",
    )
    review = root / "playbook-review.md"
    review.write_text("# Playbook Review\n\nexact preamble\n\n## Ignore this\n\nnot a target\n", encoding="utf-8")
    targets = (
        replace(seed.TARGETS[0], source_path=runbook,
                source_sha256=hashlib.sha256(runbook.read_bytes()).hexdigest()),
        replace(seed.TARGETS[1], source_path=review,
                source_sha256=hashlib.sha256(review.read_bytes()).hexdigest()),
    )
    fixture_authority = seed._fixture_manifest_authority_for_test(targets)
    parsed = seed._parse_fixture_targets_for_test(targets, fixture_authority)
    check("the manifest yields exactly two documents", len(parsed) == 2)
    check("runbook is filtered before insert", [s["section_key"] for s in parsed[0].document["sections"]]
          == ["diagnosis-checklist-in-order-2-minutes"])
    check("playbook is filtered before insert", [s["section_key"] for s in parsed[1].document["sections"]]
          == ["preamble"])
    check("the target addresses stay canonical", [item.address for item in parsed]
          == ["runbook#diagnosis-checklist-in-order-2-minutes", "playbook-review#preamble"])

    runbook.write_text("# Runbook\n\n## Diagnosis checklist (in order, 2 minutes)\n\nchanged target\n", encoding="utf-8")
    try:
        seed._parse_fixture_targets_for_test(targets, fixture_authority)
    except seed.SeedRefusal as exc:
        check("a changed canonical source is refused", "hash mismatch" in str(exc))
    else:
        check("a changed canonical source is refused", False)

    bad_key = (replace(targets[0], section_key="not-present"), targets[1])
    try:
        seed._parse_fixture_targets_for_test(bad_key, fixture_authority)
    except seed.SeedRefusal as exc:
        check("an unapproved section is refused", "exact approved canonical" in str(exc))
    else:
        check("an unapproved section is refused", False)

    same_address_alternate = (
        replace(
            seed.TARGETS[0],
            source_path=seed.TARGETS[0].source_path.with_name("runbook-alternate.md"),
            source_sha256="f" * 64,
            content_class="playbook",
        ),
        seed.TARGETS[1],
    )
    try:
        seed.validate_target_manifest(same_address_alternate)
    except seed.SeedRefusal as exc:
        check("same-address alternate path, hash, and class are refused",
              "exact approved canonical" in str(exc))
    else:
        check("same-address alternate path, hash, and class are refused", False)

    try:
        seed._parse_fixture_targets_for_test(targets, object())
    except seed.SeedRefusal as exc:
        check("fixture source injection requires the explicit test authority",
              "fixture manifest authority" in str(exc))
    else:
        check("fixture source injection requires the explicit test authority", False)
    seed.VAULT = old_vault

with altered_environ(CARR_BREAK_GLASS="1", DATABASE_URL=None):
    try:
        seed.reject_unsafe_environment()
    except seed.SeedRefusal as exc:
        check("break-glass is refused", "break-glass" in str(exc))
    else:
        check("break-glass is refused", False)

with altered_environ(CARR_BREAK_GLASS=None, DATABASE_URL="postgresql://owner@example.invalid/db"):
    try:
        seed.reject_unsafe_environment()
    except seed.SeedRefusal as exc:
        check("caller-supplied owner DSNs are refused", "DATABASE_URL" in str(exc))
    else:
        check("caller-supplied owner DSNs are refused", False)

captured: dict[str, object] = {}
old_dsn = seed.db_tap.dsn
try:
    seed.db_tap.dsn = lambda **kwargs: captured.update(kwargs) or "postgresql://app_writer@example.invalid/db"
    with altered_environ(CARR_BREAK_GLASS=None, DATABASE_URL=None):
        check("only the isolated staging app_writer DSN is derived",
              seed.staging_app_writer_dsn() == "postgresql://app_writer@example.invalid/db"
              and captured == {"project": "staging", "role_name": "app_writer"})
finally:
    seed.db_tap.dsn = old_dsn

for forbidden in ("--project", "--branch", "--files", "--reason"):
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            seed.parse_args(["--batch-no", "100", "--apply", forbidden, "production"])
    except SystemExit:
        continue
    check(f"{forbidden} cannot redirect the staging-only seed", False)
check("arbitrary project, branch, source, and break-glass arguments are rejected", True)

class IdentityCursor:
    def execute(self, statement, params=None):
        self.statement = str(statement)

    def fetchone(self):
        return ("app_writer", "app_writer")


authority_calls: list[tuple[bool, str]] = []
original_collect = seed.provision.collect_profile_closure
original_validate = seed.provision.validate_profile_closure
original_plan = seed.provision.snapshot_grants.load_current_grants_to_role
try:
    seed.provision.collect_profile_closure = lambda _cur, profile: ("closure", profile.label)
    seed.provision.validate_profile_closure = lambda _closure, _profile, _grants, **kwargs: authority_calls.append(
        (kwargs["exact"], kwargs["expected_creator"])
    )
    seed.provision.snapshot_grants.load_current_grants_to_role = lambda *_args: ("grant",)
    seed.require_staging_app_writer(IdentityCursor())
finally:
    seed.provision.collect_profile_closure = original_collect
    seed.provision.validate_profile_closure = original_validate
    seed.provision.snapshot_grants.load_current_grants_to_role = original_plan
check("seeder binds exact app_writer closure to SQL owner before mutation",
      authority_calls == [(True, "neondb_owner")])

class SetRoleCursor(IdentityCursor):
    def fetchone(self):
        return ("neondb_owner", "app_writer")

try:
    seed.require_staging_app_writer(SetRoleCursor())
except seed.SeedRefusal:
    check("owner SET ROLE app_writer cannot satisfy the staging mutation identity", True)
else:
    check("owner SET ROLE app_writer cannot satisfy the staging mutation identity", False)

print("PASS: staging retrieval doctrine seed self-test")
