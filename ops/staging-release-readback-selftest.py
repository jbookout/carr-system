#!/usr/bin/env python3
"""Hermetic contract for Program 5 staging deployment/recovery receipts."""
from __future__ import annotations

import importlib.util
import json
import os
import shlex
import subprocess
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
assert 'args.action in ("approve", "staging-approve")' in ops_source
assert "approved_by_actor = %s" not in ops_source
assert "ops.approve_program5_release" in ops_source
assert "ops.approve_staging_release" in ops_source
assert '"staging-approve"' in ops_source
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


def wrapper_receipt_path(step: str, expected: str, *, refuse: bool = False) -> tuple[int, list[list[str]]]:
    """Execute the wrapper's staging receipt functions with local fakes only."""
    deploy = (ROOT / "bin" / "deploy-worker.sh").read_text(encoding="utf-8")
    start = deploy.index("    verify_staging_receipt_file()")
    end = deploy.index("\n    # Resume before provider mutation.", start)
    functions = "\n".join(line[4:] for line in deploy[start:end].splitlines())
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        log = tmp / "calls.jsonl"
        fake_py = tmp / "python"
        fake_py.write_text("""#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
args=sys.argv[1:]
Path(os.environ['CALL_LOG']).open('a').write(json.dumps(args)+'\\n')
if 'staging-readback-verify' in args:
    if os.environ.get('REFUSE') == '1': raise SystemExit(2)
    print('11111111-2222-4333-8444-555555555555')
raise SystemExit(0)
""", encoding="utf-8")
        fake_py.chmod(0o755)
        wrangler = tmp / "wrangler"
        wrangler.write_text("#!/bin/sh\necho '[]'\n", encoding="utf-8")
        wrangler.chmod(0o755)
        (tmp / "tools").mkdir()
        (tmp / "tools" / "ops-record.py").write_text("# fake\n", encoding="utf-8")
        receipt = tmp / "release.json"
        receipt.write_text("{}", encoding="utf-8")
        recovery = ""
        if step != "standalone":
            recovery = "RECOVERY_ATTEMPT_ID=22222222-2222-4222-8222-222222222222\nRECOVERY_PRIOR_RELEASE_KEY=prior\n"
        script = tmp / "run.sh"
        script.write_text("#!/bin/sh\nset -eu\n" + functions + "\n"
            + f"PY={fake_py}\nREPO={tmp}\nWRANGLER={wrangler}\nDEPLOY_TAG=carr-staging-test\n"
            + f"EXPECTED_PROGRAM6_ACTIONS={expected}\nTARGET_ENV=staging\n"
            + f"HEAD_SHA={'a' * 40}\nRELEASE_KEY=current\nSTAGING_RECEIPT_KEY=11111111-2222-4333-8444-555555555555\n"
            + f"RECOVERY_STEP={step}\nCARR_CORRELATION_ID=33333333-3333-4333-8333-333333333333\n"
            + recovery + f"record_staging_receipt_file {receipt}\n", encoding="utf-8")
        script.chmod(0o755)
        env = {**os.environ, "CALL_LOG": str(log), "REFUSE": "1" if refuse else "0", "TMPDIR": str(tmp)}
        done = subprocess.run(["sh", str(script)], capture_output=True, text=True, env=env)
        calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
        return done.returncode, calls


def wrapper_readback_retry(*, curl_failures: int, verify_ok: bool,
                           attempts: str = "12", sleep_seconds: str = "5") \
        -> tuple[int, list[str], list[str], str]:
    """Execute the wrapper's exact bounded retry block with fake provider calls."""
    deploy = (ROOT / "bin" / "deploy-worker.sh").read_text(encoding="utf-8")
    start = deploy.index("  STAGING_READBACK_DEADLINE_SECONDS=60")
    end = deploy.index('\n  rm -f "$STAGING_RECEIPT"', start)
    retry_block = deploy[start:end]
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        fake_bin = tmp / "bin"
        fake_bin.mkdir()
        curl_log = tmp / "curl.log"
        sleep_log = tmp / "sleep.log"
        clock = tmp / "clock"
        curl_count = tmp / "curl.count"
        clock.write_text("0", encoding="utf-8")
        curl_count.write_text("0", encoding="utf-8")
        fake_py = fake_bin / "python"
        fake_py.write_text("""#!/bin/sh
if [ "$1" != "-c" ]; then exit 64; fi
case "$2" in
  *monotonic_ns*) cat "$CLOCK_FILE" ;;
  *sys.argv*)
    ms="$3"
    whole=$((ms / 1000))
    frac=$((ms % 1000))
    printf '%s.%03d\\n' "$whole" "$frac"
    ;;
  *) exit 64 ;;
esac
""", encoding="utf-8")
        fake_py.chmod(0o755)
        fake_curl = fake_bin / "curl"
        fake_curl.write_text("""#!/bin/sh
count=$(cat "$CURL_COUNT_FILE")
count=$((count + 1))
printf '%s\\n' "$*" >> "$CURL_LOG_FILE"
printf '%s\\n' "$count" > "$CURL_COUNT_FILE"
max_time=0
while [ "$#" -gt 0 ]; do
  if [ "$1" = "--max-time" ]; then max_time="$2"; shift 2; continue; fi
  shift
done
max_ms=$(printf '%s' "$max_time" | awk '{printf "%d", $1 * 1000}')
advance=11000
[ "$advance" -le "$max_ms" ] || advance="$max_ms"
now=$(cat "$CLOCK_FILE")
printf '%s\\n' "$((now + advance))" > "$CLOCK_FILE"
if [ "$count" -le "$CURL_FAILURES" ]; then exit 22; fi
printf '{}'
""", encoding="utf-8")
        fake_curl.chmod(0o755)
        fake_sleep = fake_bin / "sleep"
        fake_sleep.write_text("""#!/bin/sh
printf '%s\\n' "$1" >> "$SLEEP_LOG_FILE"
advance=$(printf '%s' "$1" | awk '{printf "%d", $1 * 1000}')
now=$(cat "$CLOCK_FILE")
printf '%s\\n' "$((now + advance))" > "$CLOCK_FILE"
""", encoding="utf-8")
        fake_sleep.chmod(0o755)
        receipt = tmp / "release.json"
        receipt.write_text("{}", encoding="utf-8")
        script = tmp / "run.sh"
        script.write_text(
            "#!/bin/sh\nset -eu\n"
            "fail() { echo \"REFUSED: $1\" >&2; exit 64; }\n"
            f"PY={shlex.quote(str(fake_py))}\n"
            "STAGING_HOST=staging.example\n"
            f"STAGING_RECEIPT={shlex.quote(str(receipt))}\n"
            "STAGING_OK=0\n"
            "record_staging_receipt_file() { [ \"${VERIFY_OK:-0}\" = 1 ]; }\n"
            + retry_block + "\n"
            "if [ \"$STAGING_OK\" = 1 ]; then exit 0; fi\n"
            "echo 'staging /release identity was not durably verified; recording VERIFYING only.' >&2\n"
            "exit 1\n",
            encoding="utf-8",
        )
        script.chmod(0o755)
        env = {
            **os.environ,
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "CLOCK_FILE": str(clock),
            "CURL_COUNT_FILE": str(curl_count),
            "CURL_LOG_FILE": str(curl_log),
            "CURL_FAILURES": str(curl_failures),
            "SLEEP_LOG_FILE": str(sleep_log),
            "VERIFY_OK": "1" if verify_ok else "0",
            "CARR_STAGING_READBACK_ATTEMPTS": attempts,
            "CARR_STAGING_READBACK_SLEEP_SECONDS": sleep_seconds,
        }
        done = subprocess.run(["sh", str(script)], capture_output=True, text=True, env=env)
        curls = curl_log.read_text(encoding="utf-8").splitlines() if curl_log.exists() else []
        sleeps = sleep_log.read_text(encoding="utf-8").splitlines() if sleep_log.exists() else []
        return done.returncode, curls, sleeps, done.stderr


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
    "program6_actions": {"enabled": True, "posture": "enabled", "reason": None},
    "secret": "must-not-survive",
}

with tempfile.TemporaryDirectory() as tmp:
    p = Path(tmp) / "release.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    projection = mod.staging_readback_projection(str(p), sha, tag, "enabled")
    assert projection == {
        "git_sha": sha,
        "provider": "cloudflare-workers",
        "provider_version_id": version_id,
        "provider_tag": tag,
        "verb_count": 211,
        "schema_highest_migration": "0202_staging_release_readback_receipt.sql",
        "schema_applied_count": 202,
        "doctrine_generation": 170,
        "program6_actions_enabled": True,
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
        refuses(lambda p=p: mod.staging_readback_projection(str(p), sha, tag, "enabled"), message)

    disabled = json.loads(json.dumps(payload))
    disabled["program6_actions"] = {"enabled": False, "posture": "disabled", "reason": None}
    p.write_text(json.dumps(disabled), encoding="utf-8")
    assert mod.staging_readback_projection(str(p), sha, tag, "disabled")["git_sha"] == sha

    posture_refusals: tuple[tuple[object, str], ...] = (
        ({"enabled": False, "posture": "disabled", "reason": None}, "Program 6 posture"),
        ({"enabled": False, "posture": "misconfigured", "reason": "bad"}, "Program 6 posture"),
        (None, "Program 6 posture"),
    )
    for posture_value, message in posture_refusals:
        bad = json.loads(json.dumps(payload))
        if posture_value is None:
            bad.pop("program6_actions")
        else:
            bad["program6_actions"] = posture_value
        p.write_text(json.dumps(bad), encoding="utf-8")
        refuses(lambda p=p: mod.staging_readback_projection(str(p), sha, tag, "enabled"), message)

    p.write_bytes(b"{" + b"x" * (mod.MAX_RELEASE_BODY_BYTES + 1))
    refuses(lambda: mod.staging_readback_projection(str(p), sha, tag, "enabled"), "too large")

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
    refuses(lambda: mod.staging_readback_projection(str(symlink), sha, tag, "enabled"), "regular single-link")

    hardlink = Path(tmp) / "hardlink.json"
    os.link(regular, hardlink)
    refuses(lambda: mod.staging_readback_projection(str(regular), sha, tag, "enabled"), "regular single-link")

    fifo = Path(tmp) / "release.fifo"
    os.mkfifo(fifo)
    refuses(lambda: mod.staging_readback_projection(str(fifo), sha, tag, "enabled"), "regular single-link")


for step in ("standalone", "current_before"):
    rc, calls = wrapper_receipt_path(step, "enabled")
    verify_calls = [call for call in calls if "staging-readback-verify" in call]
    deployment_calls = [call for call in calls if "deployment" in call]
    assert rc == 0 and len(verify_calls) == 1 and len(deployment_calls) == 1, (step, rc, calls)
    assert verify_calls[0][verify_calls[0].index("--expected-program6-actions") + 1] == "enabled"
    assert deployment_calls[0][deployment_calls[0].index("--expected-program6-actions") + 1] == "enabled"
    if step == "standalone":
        assert "--recovery-attempt-id" not in deployment_calls[0]
    else:
        assert deployment_calls[0][deployment_calls[0].index("--recovery-step") + 1] == step
        assert "--recovery-attempt-id" in deployment_calls[0]

rc, calls = wrapper_receipt_path("standalone", "enabled", refuse=True)
assert rc != 0 and len([call for call in calls if "deployment" in call]) == 0


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
        elif "ops.prepare_staging_restore_only_attempt" in compact:
            self._row = ({"restore_attempt_id": str(uuid.uuid4()), "state": "prepared",
                          "expected_provider_tag": tag, "replayed": False},)
        elif "ops.claim_staging_restore_only_attempt" in compact:
            self._row = ({"mutation_allowed": True, "replayed": False},)
        elif "ops.record_staging_restore_only_result" in compact:
            self._row = ({"status": "succeeded",
                          "result_ref": "ops.staging-restore-only:" + "d" * 64,
                          "replayed": False},)

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

restore_args = types.SimpleNamespace(
    release_key="p5-candidate", prior_release_key="p5-prior",
    recovery_attempt_id=str(uuid.uuid4()), idempotency_key=str(uuid.uuid4()),
    correlation=None, git_sha=sha, status="succeeded", reason=None,
)
restore_args.correlation = restore_args.recovery_attempt_id
prepared = mod.prepare_staging_restore_only_attempt(cur, restore_args)
assert prepared["state"] == "prepared"
assert "ops.prepare_staging_restore_only_attempt" in cur.calls[-1][0]
claimed = mod.claim_staging_restore_only_attempt(cur, restore_args.idempotency_key)
assert claimed["mutation_allowed"] is True
assert "ops.claim_staging_restore_only_attempt" in cur.calls[-1][0]
result = mod.record_staging_restore_only_result(cur, restore_args, projection)
assert result["status"] == "succeeded"
assert "ops.record_staging_restore_only_result" in cur.calls[-1][0]


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

restore_migration = (ROOT / "migrations" / "0295_staging_restore_only_recovery.sql").read_text()
for marker in (
    "create table ops.staging_restore_only_attempt",
    "create table ops.staging_restore_only_claim",
    "create table ops.staging_restore_only_result",
    "status in ('succeeded','failed','unknown')",
    "ops.prepare_staging_restore_only_attempt",
    "ops.claim_staging_restore_only_attempt",
    "ops.record_staging_restore_only_result",
    "target_provider_version_id",
    "rollback_plan_ref",
    "declared_migration_set_sha256",
    "staging_restore_only_result_append_only",
):
    assert marker in restore_migration, marker
assert "staging_recovery_rehearsal_bundle" not in restore_migration
repair_migration = (ROOT / "migrations" / "0296_restore_only_recovery_repair.sql").read_text()
assert "create or replace function ops.prepare_staging_restore_only_attempt" in repair_migration
# PostgreSQL standard-conforming strings require the single regex escape used
# by the already-valid Program 5 writer; the double escape in applied 0295 is
# intentionally preserved only as historical evidence.
assert "+\\.sql$'" in repair_migration
assert "+\\\\.sql$'" not in repair_migration

uuid_repair = (ROOT / "migrations" / "0297_restore_only_provider_uuid_repair.sql").read_text()
assert "create or replace function ops.prepare_staging_restore_only_attempt" in uuid_repair
assert "provider_version_id !~ '^[0-9a-f]{8}" in uuid_repair
assert "target_provider_version:=current_release.provider_version_id::uuid" in uuid_repair
assert uuid_repair.count("target_provider_version") >= 4
assert "drop constraint staging_restore_only_attempt_declared_schema_highest_migr_check" in uuid_repair
assert "declared_schema_highest_migration ~ '^[0-9]{4}_[a-z0-9_.-]+\\.sql$'" in uuid_repair
assert "declared_schema_highest_migration ~ '^[0-9]{4}_[a-z0-9_.-]+\\\\.sql$'" not in uuid_repair
assert "migration 0295" not in uuid_repair.lower()

completion_grant = (ROOT / "migrations" / "0215_program5_completion_hash_grant.sql").read_text()
assert "grant execute on function ops.program5_migration_set_sha256(text[]) to carr_jobs" in completion_grant
for role in ("public", "carr_reader", "carr_writer", "carr_authority"):
    assert role in completion_grant

db_gate = (ROOT / "ops" / "staging-release-readback-gate.py").read_text()
assert "carr_jobs completes an approved release through the exact assurance trigger" in db_gate
assert "program5_migration_set_sha256(text[])" in db_gate

wrapper = (ROOT / "bin" / "deploy-worker.sh").read_text()
for marker in (
    "staging-target",
    "--tag \"$DEPLOY_TAG\"",
    "--max-filesize 65536",
    "--expected-provider-tag \"$DEPLOY_TAG\"",
    "--release-key \"$RELEASE_KEY\"",
    "--recovery-attempt-id",
    "current_before|prior|current_after|restore_only",
    "staging-restore-only",
    "staging_attempt prepare",
    "staging_attempt claim",
    "deployment already claimed but its exact tag is not serving; refusing redeploy",
    "the unclaimed deterministic provider tag already exists; refusing tag recreation",
    "versions list --env \"$TARGET_ENV\" --json",
    "staging-provider-version",
):
    assert marker in wrapper, marker
assert "STAGING_READBACK_DEADLINE_SECONDS=60" in wrapper
assert "STAGING_READBACK_CURL_MAX_SECONDS=15" in wrapper
assert 'STAGING_READBACK_ATTEMPTS="${CARR_STAGING_READBACK_ATTEMPTS:-12}"' in wrapper
assert 'STAGING_READBACK_SLEEP_SECONDS="${CARR_STAGING_READBACK_SLEEP_SECONDS:-5}"' in wrapper
assert "STAGING_READBACK_CURL_MAX=\"$($PY -c" not in wrapper
assert "--max-time \"$STAGING_READBACK_CURL_MAX\"" in wrapper
assert "exceed the 12-attempt safety bound" in wrapper
assert "exceeds the 5-second safety bound" in wrapper
assert 'STAGING_READBACK_ATTEMPT=0' in wrapper
assert 'staging /release identity was not durably verified; recording VERIFYING only.' in wrapper
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

# The wrapper's retry path is executed with fake provider calls. This proves
# the typed readback can settle after transient propagation, that the deadline
# clamps both curl and sleep, and that an invalid ambient control refuses before
# any provider/readback call. Persistent disagreement remains VERIFYING.
success_rc, success_curls, success_sleeps, success_stderr = wrapper_readback_retry(
    curl_failures=2, verify_ok=True)
assert success_rc == 0, success_stderr
assert len(success_curls) == 3, success_curls
assert all(line.split()[line.split().index("--max-time") + 1] == "15.000"
           for line in success_curls)
assert success_sleeps == ["5.000", "5.000"], success_sleeps

stuck_rc, stuck_curls, stuck_sleeps, stuck_stderr = wrapper_readback_retry(
    curl_failures=99, verify_ok=False)
assert stuck_rc == 1 and "VERIFYING" in stuck_stderr, stuck_stderr
assert len(stuck_curls) == 4, stuck_curls
assert stuck_curls[-1].split()[stuck_curls[-1].split().index("--max-time") + 1] == "12.000"
assert stuck_sleeps == ["5.000", "5.000", "5.000", "1.000"], stuck_sleeps

invalid_rc, invalid_curls, invalid_sleeps, invalid_stderr = wrapper_readback_retry(
    curl_failures=0, verify_ok=True, attempts="13")
assert invalid_rc == 64 and "12-attempt safety bound" in invalid_stderr, invalid_stderr
assert invalid_curls == [] and invalid_sleeps == [], (invalid_curls, invalid_sleeps)

zero_attempt_rc, zero_attempt_curls, zero_attempt_sleeps, zero_attempt_stderr = wrapper_readback_retry(
    curl_failures=0, verify_ok=True, attempts="00")
assert zero_attempt_rc == 64 and "positive integer" in zero_attempt_stderr, zero_attempt_stderr
assert zero_attempt_curls == [] and zero_attempt_sleeps == [], (zero_attempt_curls, zero_attempt_sleeps)

zero_sleep_rc, zero_sleep_curls, zero_sleep_sleeps, zero_sleep_stderr = wrapper_readback_retry(
    curl_failures=0, verify_ok=True, sleep_seconds="00")
assert zero_sleep_rc == 64 and "positive integer" in zero_sleep_stderr, zero_sleep_stderr
assert zero_sleep_curls == [] and zero_sleep_sleeps == [], (zero_sleep_curls, zero_sleep_sleeps)

print("staging release readback selftest: typed, bounded, server-derived contract is closed")
