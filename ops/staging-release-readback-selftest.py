#!/usr/bin/env python3
"""Hermetic contract for Program 5 staging deployment/recovery receipts."""
from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import types
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("ops_record", ROOT / "tools" / "ops-record.py")
assert spec is not None
mod = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(mod)
ops_source = (ROOT / "tools" / "ops-record.py").read_text()
assert 'connection_kind = "authority" if args.action == "approve" else "write"' in ops_source
assert "approved_by_actor = %s" not in ops_source
assert "ops.approve_program5_release" in ops_source
for marker in ("os.lstat", "os.open", "os.fstat", "O_NOFOLLOW",
               "MAX_RELEASE_BODY_BYTES + 1", "st_nlink != 1"):
    assert marker in ops_source, marker
assert "Path(path).read_bytes()" not in ops_source


def refuses(fn, contains: str) -> None:
    try:
        fn()
    except (ValueError, SystemExit) as exc:
        assert contains in str(exc), (contains, str(exc))
    else:
        raise AssertionError(f"expected refusal containing {contains!r}")


sha = "a" * 40
version_id = str(uuid.uuid4())
tag = "carr-staging-" + "b" * 32
payload = {
    "ok": True,
    "env": {"value": "staging", "reason": None},
    "git_sha": {"value": sha, "reason": None},
    "provider": "cloudflare-workers",
    "worker_version": {
        "id": version_id,
        "tag": tag,
        "timestamp": "2026-08-20T16:00:00.000Z",
    },
    "verb_count": 211,
    "schema": {
        "highest_applied_migration": "0202_staging_release_readback_receipt.sql",
        "applied_count": 202,
        "reason": None,
        "note": "untrusted prose that must not survive",
    },
    "doctrine_generation": {"value": 170, "reason": None},
    "program6_actions": {"enabled": False},
    "secret": "must-not-survive",
}

with tempfile.TemporaryDirectory() as tmp:
    p = Path(tmp) / "release.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    projection = mod.staging_readback_projection(str(p), sha, tag)
    assert projection == {
        "git_sha": sha,
        "provider": "cloudflare-workers",
        "provider_version_id": version_id,
        "provider_tag": tag,
        "verb_count": 211,
        "schema_highest_migration": "0202_staging_release_readback_receipt.sql",
        "schema_applied_count": 202,
        "doctrine_generation": 170,
    }
    assert "secret" not in json.dumps(projection)
    assert "timestamp" not in json.dumps(projection)

    for field, value, message in (
        ("env", {"value": "production"}, "environment"),
        ("provider", "other", "provider"),
        ("worker_version", {"id": "not-uuid", "tag": tag}, "version"),
        ("worker_version", {"id": version_id, "tag": "wrong"}, "tag"),
        ("verb_count", True, "verb_count"),
        ("schema", {"highest_applied_migration": "../../secret", "applied_count": 1}, "schema"),
        ("doctrine_generation", {"value": -1}, "doctrine"),
    ):
        bad = json.loads(json.dumps(payload))
        bad[field] = value
        p.write_text(json.dumps(bad), encoding="utf-8")
        refuses(lambda p=p: mod.staging_readback_projection(str(p), sha, tag), message)

    p.write_bytes(b"{" + b"x" * (mod.MAX_RELEASE_BODY_BYTES + 1))
    refuses(lambda: mod.staging_readback_projection(str(p), sha, tag), "too large")

    versions = Path(tmp) / "versions.json"
    versions.write_text(json.dumps([
        {"id": version_id, "metadata": {"created_on": "2026-08-20T16:00:00Z"},
         "annotations": {"workers/tag": tag, "workers/message": "untrusted"}},
        {"id": str(uuid.uuid4()), "annotations": {"workers/tag": "other"}},
    ]), encoding="utf-8")
    assert mod.staging_provider_version(str(versions), tag, version_id) == version_id
    duplicate = json.loads(versions.read_text())
    duplicate.append({"id": str(uuid.uuid4()), "annotations": {"workers/tag": tag}})
    versions.write_text(json.dumps(duplicate), encoding="utf-8")
    refuses(lambda: mod.staging_provider_version(str(versions), tag, version_id), "recreated")
    versions.write_text(json.dumps([
        {"id": str(uuid.uuid4()), "annotations": {"workers/tag": tag}}
    ]), encoding="utf-8")
    refuses(lambda: mod.staging_provider_version(str(versions), tag, version_id), "differs")

    regular = Path(tmp) / "regular.json"
    regular.write_text(json.dumps(payload), encoding="utf-8")
    symlink = Path(tmp) / "symlink.json"
    symlink.symlink_to(regular)
    refuses(lambda: mod.staging_readback_projection(str(symlink), sha, tag), "regular single-link")

    hardlink = Path(tmp) / "hardlink.json"
    os.link(regular, hardlink)
    refuses(lambda: mod.staging_readback_projection(str(regular), sha, tag), "regular single-link")

    fifo = Path(tmp) / "release.fifo"
    os.mkfifo(fifo)
    refuses(lambda: mod.staging_readback_projection(str(fifo), sha, tag), "regular single-link")


target = mod.staging_worker_target()
assert target == {
    "account_id": "12ccca77eb49142a6be8eb84c0d6a3a0",
    "worker_name": "carr-mcp-staging",
    "host": "carr-mcp-staging.joe-bookout-carr-us.workers.dev",
}

bad_toml = """
name = "carr-mcp"
account_id = "12ccca77eb49142a6be8eb84c0d6a3a0"
routes = []
[env.staging]
name = "carr-mcp-staging"
workers_dev = true
routes = [{pattern = "api.doctorcre.com", custom_domain = true}]
[env.staging.vars]
CARR_ENV = "staging"
DEALROOM_HOST = "carr-mcp-staging.joe-bookout-carr-us.workers.dev"
"""
with tempfile.TemporaryDirectory() as tmp:
    cfg = Path(tmp) / "wrangler.toml"
    cfg.write_text(bad_toml, encoding="utf-8")
    refuses(lambda: mod.staging_worker_target(cfg), "routes")


class Cursor:
    def __init__(self):
        self.calls = []
        self._row = None

    def execute(self, sql, params=()):
        compact = " ".join(sql.split())
        self.calls.append((compact, params))
        if "ops.record_staging_release_readback" in compact:
            self._row = ({
                "receipt_ref": "ops.staging-release-readback:" + "c" * 64,
                "replayed": False,
            },)
        elif "ops.prepare_staging_deployment_attempt" in compact:
            self._row = ({"attempt_id": str(uuid.uuid4()), "state": "prepared",
                          "expected_provider_tag": tag, "replayed": False},)
        elif "ops.claim_staging_deployment_attempt" in compact:
            self._row = ({"deploy_allowed": True, "replayed": False},)

    def fetchone(self):
        return self._row


cur = Cursor()
args = types.SimpleNamespace(
    release_key="p5-candidate",
    prior_release_key=None,
    recovery_attempt_id=None,
    recovery_step="standalone",
    idempotency_key=str(uuid.uuid4()),
    correlation=str(uuid.uuid4()),
    git_sha=sha,
    provider_tag=tag,
    actor="attacker",
)
result = mod.record_staging_release_readback(cur, args, projection)
assert result["receipt_ref"].startswith("ops.staging-release-readback:")
sql, params = cur.calls[-1]
assert "ops.record_staging_release_readback" in sql
assert "attacker" not in params
assert sha not in params and version_id in params and tag in params

prepared = mod.prepare_staging_deployment_attempt(cur, args)
assert prepared["state"] == "prepared"
assert "ops.prepare_staging_deployment_attempt" in cur.calls[-1][0]
claimed = mod.claim_staging_deployment_attempt(cur, args.idempotency_key)
assert claimed["deploy_allowed"] is True
assert "ops.claim_staging_deployment_attempt" in cur.calls[-1][0]


migration = (ROOT / "migrations" / "0202_staging_release_readback_receipt.sql").read_text()
required = (
    "create table ops.staging_deployment_attempt",
    "create table ops.staging_deployment_claim",
    "ops.prepare_staging_deployment_attempt",
    "ops.claim_staging_deployment_attempt",
    "create table ops.staging_release_readback_receipt",
    "create table ops.staging_recovery_rehearsal_bundle",
    "recovery_rehearsal_bundle_id",
    "create table ops.release_approval_receipt",
    "ops.record_staging_release_readback",
    "ops.approve_program5_release",
    "ops.authority_actor_slug()",
    "pg_advisory_xact_lock",
    "session_user <> 'carr_jobs'",
    "recovery_strategy = 'rollback'",
    "raise exception 'Program 5 evidence is append-only'",
    "revoke all on ops.staging_release_readback_receipt",
    "revoke all on ops.staging_recovery_rehearsal_bundle",
    "revoke all on ops.release_approval_receipt",
    "declared_migration_set_sha256",
    "declared_migration_count",
    "schema_applied_count",
    "schema_ledger_sha256",
    "declared_schema_applied_count",
    "declared_schema_ledger_sha256",
    "p_schema_applied_count<>attempt.declared_schema_applied_count",
)
for marker in required:
    assert marker in migration, marker
for forbidden in (
    "readback_projection jsonb",
    "p_projection jsonb",
    "p_digest",
    "approved_by_actor = p_",
):
    assert forbidden not in migration, forbidden

wrapper = (ROOT / "bin" / "deploy-worker.sh").read_text()
for marker in (
    "staging-target",
    "--tag \"$DEPLOY_TAG\"",
    "--max-filesize 65536",
    "--expected-provider-tag \"$DEPLOY_TAG\"",
    "--release-key \"$RELEASE_KEY\"",
    "--recovery-attempt-id",
    "current_before|prior|current_after",
    "staging_attempt prepare",
    "staging_attempt claim",
    "deployment already claimed but its exact tag is not serving; refusing redeploy",
    "the unclaimed deterministic provider tag already exists; refusing tag recreation",
    "versions list --env \"$TARGET_ENV\" --json",
    "staging-provider-version",
):
    assert marker in wrapper, marker
assert "sed -nE 's/^DEALROOM_HOST" not in wrapper
resume_slice = wrapper[wrapper.index("staging_attempt()"):
                       wrapper.index("# Production promotion is not verified")]
assert resume_slice.index("staging_attempt prepare") < resume_slice.index("staging_attempt claim")
assert resume_slice.index("staging_attempt claim") < resume_slice.index('"$WRANGLER" deploy')
assert resume_slice.index('"https://$STAGING_TARGET_HOST/release"') < resume_slice.index("staging_attempt claim")

snapshot = (ROOT / "bin" / "schema-snapshot.sh").read_text()
assert "--from-disposable-local" in snapshot
assert "^postgres://carr_ci@127\\.0\\.0\\.1:" in snapshot
assert "steep-field-48688294" in snapshot  # normal Production path is preserved
assert "PGOPTIONS='-c timezone=UTC'" in snapshot

print("staging release readback selftest: typed, bounded, server-derived contract is closed")
