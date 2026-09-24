#!/usr/bin/env python3
"""credential-health-selftest.py — proves the daily credential-health lane
(ops/credential-health.py) does what its docstring claims, wired into the
`gates` CI class by ops/ci.sh's own `ops/*-selftest.py` glob (no separate
registration needed).

Loaded by file path via importlib, NOT `python3 ops/credential-health.py`,
because the module under test is imported here with SUBPROCESS_RUN, HTTP_GET
and HTTP_POST monkeypatched to fakes BEFORE any check runs — real subprocess
or network access must never happen in this suite.

Covers, per the build spec:
  - every bucket (ok / expiring_soon / failed / unknown) is reachable and
    correctly assigned, including the two expiry-window paths (a configured
    rotation date, and the Google testing-mode 7-day window);
  - no output from a probe ever reaches the jsonl — a fake probe that PRINTS
    and RETURNS a fake secret is fed in, and the written jsonl is asserted to
    contain neither the secret string nor any key beyond name/status/
    checked_at/expires_at;
  - deduplication — a repeated bucket does not refile, a changed bucket does;
  - timeouts — a probe that raises TimeoutExpired (or its HTTP equivalent)
    reports `unknown`, never a hang and never a crash of the whole run.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MODULE_PATH = REPO / "ops" / "credential-health.py"

spec = importlib.util.spec_from_file_location("credential_health_under_test", MODULE_PATH)
assert spec and spec.loader
ch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ch)

PASSED = 0
FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASSED
    if ok:
        PASSED += 1
        print(f"  ok  {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))


def _cred(name, probe_type, **extra):
    c = {
        "name": name,
        "display_name": name,
        "kind": "test",
        "machines": ["studio"],
        "location": "test fixture, not a real path",
        "probe": {"type": probe_type, "timeout_s": 1},
        "replacement_plan": "n/a — selftest fixture",
        "doc_pointer": "",
        "expiring_soon_days": 3,
        "expiry": {},
    }
    c.update(extra)
    return c


# ═══════════════════════════════════════════════════════════════════════════
# every bucket is reachable
# ═══════════════════════════════════════════════════════════════════════════

def test_shell_exit_ok():
    ch.SUBPROCESS_RUN = lambda *a, **k: subprocess.CompletedProcess(a, 0, b"", b"")
    cred = _cred("shell-ok", "shell_exit_status", probe={"type": "shell_exit_status",
                  "command": ["true"], "timeout_s": 1})
    r = ch.evaluate_credential(cred)
    check("shell_exit_status returncode 0 -> ok", r.bucket == "ok", r.bucket)


def test_shell_exit_failed():
    ch.SUBPROCESS_RUN = lambda *a, **k: subprocess.CompletedProcess(a, 1, b"", b"")
    cred = _cred("shell-fail", "shell_exit_status", probe={"type": "shell_exit_status",
                  "command": ["false"], "timeout_s": 1})
    r = ch.evaluate_credential(cred)
    check("shell_exit_status returncode 1 -> failed", r.bucket == "failed", r.bucket)


def test_shell_exit_unknown_missing_tool():
    def _raise(*a, **k):
        raise FileNotFoundError("no such file")
    ch.SUBPROCESS_RUN = _raise
    cred = _cred("shell-missing", "shell_exit_status", probe={"type": "shell_exit_status",
                  "command": ["definitely-not-a-real-binary"], "timeout_s": 1})
    r = ch.evaluate_credential(cred)
    check("missing binary -> unknown, not failed", r.bucket == "unknown", r.bucket)


def test_worker_bearer_ok_and_unauthorized():
    import os
    os.environ["CH_SELFTEST_TOKEN"] = "not-a-real-token"
    try:
        ch.HTTP_POST = lambda url, headers, body, timeout_s: 200
        cred = _cred("worker-ok", "worker_bearer_health", probe={
            "type": "worker_bearer_health", "token_env": "CH_SELFTEST_TOKEN",
            "url": "https://example.invalid/mcp", "timeout_s": 1})
        r = ch.evaluate_credential(cred)
        check("worker bearer HTTP 200 -> ok", r.bucket == "ok", r.bucket)

        ch.HTTP_POST = lambda url, headers, body, timeout_s: 401
        r = ch.evaluate_credential(cred)
        check("worker bearer HTTP 401 -> failed", r.bucket == "failed", r.bucket)
    finally:
        del os.environ["CH_SELFTEST_TOKEN"]


def test_worker_bearer_unknown_when_token_absent():
    cred = _cred("worker-no-token", "worker_bearer_health", probe={
        "type": "worker_bearer_health", "token_env": "CH_SELFTEST_TOKEN_ABSENT",
        "url": "https://example.invalid/mcp", "timeout_s": 1})
    r = ch.evaluate_credential(cred)
    check("worker bearer with no env token set -> unknown", r.bucket == "unknown", r.bucket)


def _cloudflare_cred(**probe_extra):
    probe = {"type": "cloudflare_token_file", "token_key": "CLOUDFLARE_API_TOKEN",
             "url": "https://example.invalid/tokens/verify", "timeout_s": 1}
    probe.update(probe_extra)
    return _cred("cloudflare-deploy-token", "cloudflare_token_file", probe=probe,
                 expiring_soon_days=3)


def _write_tokens_file(td, value="fake-cf-token-DO-NOT-LEAK", mode=0o600,
                        key="CLOUDFLARE_API_TOKEN"):
    path = Path(td) / "tokens.env"
    path.write_text(f"{key}={value}\n")
    os.chmod(path, mode)
    return str(path)


def test_cloudflare_token_file_active_is_ok():
    with tempfile.TemporaryDirectory() as td:
        path = _write_tokens_file(td)
        ch.HTTP_GET = lambda url, headers, timeout_s: (
            200, {"result": {"status": "active"}})
        cred = _cloudflare_cred(path=path)
        r = ch.evaluate_credential(cred)
        check("cloudflare token file: result.status active, HTTP 200 -> ok",
              r.bucket == "ok", r.bucket)
        check("cloudflare token file: detail is the fixed word 'active'",
              r.detail == "active", r.detail)


def test_cloudflare_token_file_missing_file():
    ch.HTTP_GET = lambda *a, **k: (200, {"result": {"status": "active"}})
    cred = _cloudflare_cred(path="/nonexistent/tokens.env")
    r = ch.evaluate_credential(cred)
    check("cloudflare token file: file missing -> failed (a finding, not silence)",
          r.bucket == "failed", r.bucket)
    check("cloudflare token file: missing-file detail names the file, not a guess",
          r.detail == "token_file_missing", r.detail)


def test_cloudflare_token_file_key_missing():
    with tempfile.TemporaryDirectory() as td:
        path = _write_tokens_file(td, key="SOME_OTHER_KEY")
        ch.HTTP_GET = lambda *a, **k: (200, {"result": {"status": "active"}})
        cred = _cloudflare_cred(path=path)
        r = ch.evaluate_credential(cred)
        check("cloudflare token file: key absent from the file -> failed",
              r.bucket == "failed", r.bucket)
        check("cloudflare token file: detail is token_key_missing",
              r.detail == "token_key_missing", r.detail)


def test_cloudflare_token_file_loose_permissions_flagged():
    calls = []

    def spying_get(url, headers, timeout_s):
        calls.append((url, headers))
        return 200, {"result": {"status": "active"}}
    with tempfile.TemporaryDirectory() as td:
        path = _write_tokens_file(td, mode=0o644)
        ch.HTTP_GET = spying_get
        cred = _cloudflare_cred(path=path)
        r = ch.evaluate_credential(cred)
        check("cloudflare token file: mode 0o644 (looser than 600) -> failed",
              r.bucket == "failed", r.bucket)
        check("cloudflare token file: detail is insecure_file_permissions",
              r.detail == "insecure_file_permissions", r.detail)
        check("cloudflare token file: a loose-mode file is never even sent over HTTP",
              calls == [], calls)


def test_cloudflare_token_file_mode_600_is_fine():
    with tempfile.TemporaryDirectory() as td:
        path = _write_tokens_file(td, mode=0o600)
        ch.HTTP_GET = lambda *a, **k: (200, {"result": {"status": "active"}})
        cred = _cloudflare_cred(path=path)
        r = ch.evaluate_credential(cred)
        check("cloudflare token file: exact mode 0o600 is accepted, not flagged",
              r.bucket == "ok", r.bucket)


def test_cloudflare_token_file_not_active_is_failed():
    with tempfile.TemporaryDirectory() as td:
        path = _write_tokens_file(td)
        ch.HTTP_GET = lambda *a, **k: (200, {"result": {"status": "disabled"}})
        cred = _cloudflare_cred(path=path)
        r = ch.evaluate_credential(cred)
        check("cloudflare token file: result.status != active -> failed, a finding",
              r.bucket == "failed", r.bucket)
        check("cloudflare token file: detail is token_status_not_active, never the "
              "raw status string (fixed vocabulary only)",
              r.detail == "token_status_not_active", r.detail)


def test_cloudflare_token_file_unauthorized_is_failed():
    with tempfile.TemporaryDirectory() as td:
        path = _write_tokens_file(td)
        ch.HTTP_GET = lambda *a, **k: (401, None)
        cred = _cloudflare_cred(path=path)
        r = ch.evaluate_credential(cred)
        check("cloudflare token file: HTTP 401 -> failed (token missing/revoked)",
              r.bucket == "failed", r.bucket)
        check("cloudflare token file: detail is token_unauthorized",
              r.detail == "token_unauthorized", r.detail)


def test_cloudflare_token_file_timeout_is_unknown():
    with tempfile.TemporaryDirectory() as td:
        path = _write_tokens_file(td)
        ch.HTTP_GET = lambda *a, **k: (None, None)
        cred = _cloudflare_cred(path=path)
        r = ch.evaluate_credential(cred)
        check("cloudflare token file: HTTP timeout -> unknown, not a hang or crash",
              r.bucket == "unknown", r.bucket)


def test_cloudflare_token_file_expires_on_upgrades_to_expiring_soon():
    from datetime import datetime, timedelta, timezone
    with tempfile.TemporaryDirectory() as td:
        path = _write_tokens_file(td)
        soon = (datetime.now(timezone.utc) + timedelta(days=1)).date().isoformat()
        ch.HTTP_GET = lambda *a, **k: (
            200, {"result": {"status": "active", "expires_on": soon}})
        cred = _cloudflare_cred(path=path)
        r = ch.evaluate_credential(cred)
        check("cloudflare token file: result.expires_on 1 day out (window 3) -> "
              "expiring_soon, even though Cloudflare tokens have no expiry today",
              r.bucket == "expiring_soon", r.bucket)
        check("cloudflare token file: expires_at is carried through for the report",
              r.expires_at == soon, r.expires_at)


def test_cloudflare_token_file_value_never_leaks():
    """The token value is used exactly once, as the Authorization header, and
    must never appear in a ProbeResult, the jsonl, or anywhere HTTP_GET's
    caller can see. This stub inspects the header it was handed (proving the
    real value flows to the ONE place it must) but the selftest then proves
    that value cannot be recovered from anything the module hands back."""
    SECRET = "cf-selftest-fake-token-DO-NOT-LEAK-7a2b9"
    seen_headers = []

    def spying_get(url, headers, timeout_s):
        seen_headers.append(dict(headers))
        return 200, {"result": {"status": "active"}}
    with tempfile.TemporaryDirectory() as td:
        path = _write_tokens_file(td, value=SECRET)
        ch.HTTP_GET = spying_get
        cred = _cloudflare_cred(path=path)
        rows = ch.run_all([cred])
        check("cloudflare token file: the token DID reach the Authorization header "
              "(proves the probe used it, not that it withheld it everywhere)",
              seen_headers and SECRET in seen_headers[0].get("Authorization", ""),
              seen_headers)
        with tempfile.TemporaryDirectory() as td2:
            out_path = Path(td2) / "credential-health.jsonl"
            ch.write_jsonl(rows, path=out_path)
            written = out_path.read_text()
            check("cloudflare token file: the token value never reaches the jsonl",
                  SECRET not in written, "SECRET LEAKED INTO JSONL")
        check("cloudflare token file: the token value never reaches the row's own fields",
              SECRET not in json.dumps(rows), "SECRET LEAKED INTO run_all() ROWS")


test_cloudflare_token_file_active_is_ok()
test_cloudflare_token_file_missing_file()
test_cloudflare_token_file_key_missing()
test_cloudflare_token_file_loose_permissions_flagged()
test_cloudflare_token_file_mode_600_is_fine()
test_cloudflare_token_file_not_active_is_failed()
test_cloudflare_token_file_unauthorized_is_failed()
test_cloudflare_token_file_timeout_is_unknown()
test_cloudflare_token_file_expires_on_upgrades_to_expiring_soon()
test_cloudflare_token_file_value_never_leaks()


CLAUDE_TOKEN_FIXTURE = "sk-ant-oat" + "x" * 98
assert len(CLAUDE_TOKEN_FIXTURE) == 108, len(CLAUDE_TOKEN_FIXTURE)


def _claude_cred(**probe_extra):
    probe = {"type": "claude_cli_token_age", "token_key": "CLAUDE_CODE_OAUTH_TOKEN",
             "expected_prefix": "sk-ant-oat", "expected_length": 108, "timeout_s": 1}
    probe.update(probe_extra)
    return _cred("claude-cli-oauth-token-studio", "claude_cli_token_age", probe=probe,
                 expiring_soon_days=35)


def _write_claude_tokens_file(td, value=CLAUDE_TOKEN_FIXTURE, mode=0o600,
                               key="CLAUDE_CODE_OAUTH_TOKEN"):
    path = Path(td) / "tokens.env"
    path.write_text(f"{key}={value}\n")
    os.chmod(path, mode)
    return str(path)


def test_claude_token_active_readback_is_ok():
    with tempfile.TemporaryDirectory() as td:
        path = _write_claude_tokens_file(td)
        ch.MINT_STATE = {}
        ch.WHICH = lambda name: "/usr/bin/claude"
        ch.SUBPROCESS_RUN = lambda *a, **k: subprocess.CompletedProcess(
            a, 0, stdout="PONG\n", stderr="")
        cred = _claude_cred(path=path)
        r = ch.evaluate_credential(cred)
        check("claude cli token: shaped + PONG readback -> ok", r.bucket == "ok", r.bucket)
        check("claude cli token: expires_at is set ~365 days out from first sight",
              r.expires_at is not None, r.expires_at)


def test_claude_token_missing_file_is_unknown():
    ch.MINT_STATE = {}
    ch.WHICH = lambda name: "/usr/bin/claude"
    ch.SUBPROCESS_RUN = lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout="PONG")
    cred = _claude_cred(path="/nonexistent/tokens.env")
    r = ch.evaluate_credential(cred)
    check("claude cli token: file missing -> unknown (can't verify at all)",
          r.bucket == "unknown", r.bucket)
    check("claude cli token: detail is token_file_missing",
          r.detail == "token_file_missing", r.detail)


def test_claude_token_key_missing_is_failed():
    with tempfile.TemporaryDirectory() as td:
        path = _write_claude_tokens_file(td, key="SOME_OTHER_KEY")
        ch.MINT_STATE = {}
        cred = _claude_cred(path=path)
        r = ch.evaluate_credential(cred)
        check("claude cli token: key absent from file -> failed",
              r.bucket == "failed", r.bucket)
        check("claude cli token: detail is token_key_missing",
              r.detail == "token_key_missing", r.detail)


def test_claude_token_malformed_shape_never_calls_cli():
    calls = []

    def spying_run(*a, **k):
        calls.append((a, k))
        return subprocess.CompletedProcess(a, 0, stdout="PONG")
    with tempfile.TemporaryDirectory() as td:
        path = _write_claude_tokens_file(td, value="sk-ant-oat-too-short")
        ch.MINT_STATE = {}
        ch.SUBPROCESS_RUN = spying_run
        cred = _claude_cred(path=path)
        r = ch.evaluate_credential(cred)
        check("claude cli token: wrong length -> failed, token_malformed",
              r.bucket == "failed" and r.detail == "token_malformed",
              (r.bucket, r.detail))
        check("claude cli token: a malformed token never reaches the CLI at all",
              calls == [], calls)

    with tempfile.TemporaryDirectory() as td2:
        path = _write_claude_tokens_file(td2, value="x" * 108)  # right length, wrong prefix
        cred2 = _claude_cred(path=path)
        r2 = ch.evaluate_credential(cred2)
        check("claude cli token: right length but wrong prefix -> failed, token_malformed",
              r2.bucket == "failed" and r2.detail == "token_malformed",
              (r2.bucket, r2.detail))


def test_claude_token_cli_not_found_is_unknown():
    with tempfile.TemporaryDirectory() as td:
        path = _write_claude_tokens_file(td)
        ch.MINT_STATE = {}
        ch.WHICH = lambda name: None
        cred = _claude_cred(path=path)
        r = ch.evaluate_credential(cred)
        check("claude cli token: `claude` binary not found -> unknown, not a hard failure",
              r.bucket == "unknown", r.bucket)
        check("claude cli token: detail is claude_cli_not_found",
              r.detail == "claude_cli_not_found", r.detail)


def test_claude_token_readback_without_pong_is_failed():
    with tempfile.TemporaryDirectory() as td:
        path = _write_claude_tokens_file(td)
        ch.MINT_STATE = {}
        ch.WHICH = lambda name: "/usr/bin/claude"
        ch.SUBPROCESS_RUN = lambda *a, **k: subprocess.CompletedProcess(
            a, 1, stdout="", stderr="invalid_grant: token expired")
        cred = _claude_cred(path=path)
        r = ch.evaluate_credential(cred)
        check("claude cli token: no PONG in reply -> failed, a real finding",
              r.bucket == "failed", r.bucket)
        check("claude cli token: detail is readback_failed, never the raw CLI stderr "
              "(fixed vocabulary only — the stderr text above must never surface)",
              r.detail == "readback_failed", r.detail)


def test_claude_token_cli_timeout_is_unknown():
    def _raise(*a, **k):
        raise subprocess.TimeoutExpired(cmd="claude", timeout=1)
    with tempfile.TemporaryDirectory() as td:
        path = _write_claude_tokens_file(td)
        ch.MINT_STATE = {}
        ch.WHICH = lambda name: "/usr/bin/claude"
        ch.SUBPROCESS_RUN = _raise
        cred = _claude_cred(path=path)
        r = ch.evaluate_credential(cred)
        check("claude cli token: CLI call times out -> unknown, not a hang or crash",
              r.bucket == "unknown", r.bucket)
        check("claude cli token: detail is timeout", r.detail == "timeout", r.detail)


def test_claude_token_readback_runs_with_only_the_one_env_var_from_a_temp_cwd():
    calls = []

    def spying_run(argv, cwd=None, env=None, **k):
        calls.append({"argv": argv, "cwd": cwd, "env": dict(env or {})})
        return subprocess.CompletedProcess(argv, 0, stdout="PONG", stderr="")
    with tempfile.TemporaryDirectory() as td:
        path = _write_claude_tokens_file(td)
        ch.MINT_STATE = {}
        ch.WHICH = lambda name: "/usr/bin/claude"
        ch.SUBPROCESS_RUN = spying_run
        cred = _claude_cred(path=path)
        ch.evaluate_credential(cred)
        check("claude cli token: the readback ran exactly once", len(calls) == 1, calls)
        call = calls[0]
        check("claude cli token: env carries ONLY CLAUDE_CODE_OAUTH_TOKEN — no ambient "
              "PATH or inherited keychain login riding along",
              set(call["env"].keys()) == {"CLAUDE_CODE_OAUTH_TOKEN"}, call["env"].keys())
        check("claude cli token: the env value is the real token (proves it was used)",
              call["env"]["CLAUDE_CODE_OAUTH_TOKEN"] == CLAUDE_TOKEN_FIXTURE, "mismatch")
        check("claude cli token: run from a throwaway cwd, not the repo root",
              call["cwd"] is not None and call["cwd"] != str(ch.REPO_ROOT), call["cwd"])
        check("claude cli token: the prompt asks for exactly PONG",
              "PONG" in " ".join(call["argv"]), call["argv"])


def test_claude_token_mint_date_recorded_on_first_sight():
    with tempfile.TemporaryDirectory() as td:
        path = _write_claude_tokens_file(td)
        ch.MINT_STATE = {}
        ch.WHICH = lambda name: "/usr/bin/claude"
        ch.SUBPROCESS_RUN = lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout="PONG")
        cred = _claude_cred(path=path)
        ch.evaluate_credential(cred)
        entry = ch.MINT_STATE.get(cred["name"])
        check("claude cli token: first sight records a mint-state entry",
              entry is not None, ch.MINT_STATE)
        check("claude cli token: the recorded fingerprint is a sha256 hex digest, "
              "never the token value itself",
              entry and len(entry.get("token_sha256", "")) == 64
              and CLAUDE_TOKEN_FIXTURE not in json.dumps(entry),
              entry)


def test_claude_token_mint_date_persists_and_ages_toward_expiring_soon():
    from datetime import datetime, timedelta, timezone
    with tempfile.TemporaryDirectory() as td:
        path = _write_claude_tokens_file(td)
        ch.WHICH = lambda name: "/usr/bin/claude"
        ch.SUBPROCESS_RUN = lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout="PONG")
        cred = _claude_cred(path=path)
        fingerprint = ch._sha256_hex(CLAUDE_TOKEN_FIXTURE)
        old_mint = (datetime.now(timezone.utc) - timedelta(days=340)).date().isoformat()
        ch.MINT_STATE = {cred["name"]: {"token_sha256": fingerprint, "minted_at": old_mint}}
        r = ch.evaluate_credential(cred)
        check("claude cli token: same fingerprint keeps the OLD recorded mint date, "
              "not today",
              ch.MINT_STATE[cred["name"]]["minted_at"] == old_mint,
              ch.MINT_STATE[cred["name"]])
        check("claude cli token: 340 days into a ~365-day life (330-day warning) -> "
              "expiring_soon",
              r.bucket == "expiring_soon", r.bucket)


def test_claude_token_rotation_resets_the_mint_clock():
    from datetime import datetime, timedelta, timezone
    with tempfile.TemporaryDirectory() as td:
        path = _write_claude_tokens_file(td)
        ch.WHICH = lambda name: "/usr/bin/claude"
        ch.SUBPROCESS_RUN = lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout="PONG")
        cred = _claude_cred(path=path)
        stale_fingerprint = "0" * 64  # deliberately NOT this token's real fingerprint
        old_mint = (datetime.now(timezone.utc) - timedelta(days=400)).date().isoformat()
        ch.MINT_STATE = {cred["name"]: {"token_sha256": stale_fingerprint, "minted_at": old_mint}}
        r = ch.evaluate_credential(cred)
        check("claude cli token: a DIFFERENT fingerprint (rotation) resets the mint date, "
              "so a 400-day-old record does not carry over onto the new token",
              ch.MINT_STATE[cred["name"]]["minted_at"] != old_mint,
              ch.MINT_STATE[cred["name"]])
        check("claude cli token: right after a rotation reset, the credential reads ok",
              r.bucket == "ok", r.bucket)


def test_claude_token_value_never_leaks():
    with tempfile.TemporaryDirectory() as td:
        path = _write_claude_tokens_file(td)
        ch.MINT_STATE = {}
        ch.WHICH = lambda name: "/usr/bin/claude"
        ch.SUBPROCESS_RUN = lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout="PONG")
        cred = _claude_cred(path=path)
        rows = ch.run_all([cred])
        check("claude cli token: the token value never reaches run_all()'s rows",
              CLAUDE_TOKEN_FIXTURE not in json.dumps(rows), "SECRET LEAKED INTO ROWS")
        check("claude cli token: the token value never reaches the mint-state store",
              CLAUDE_TOKEN_FIXTURE not in json.dumps(ch.MINT_STATE),
              "SECRET LEAKED INTO MINT STATE")
        with tempfile.TemporaryDirectory() as td2:
            out_path = Path(td2) / "credential-health.jsonl"
            ch.write_jsonl(rows, path=out_path)
            written = out_path.read_text()
            check("claude cli token: the token value never reaches the jsonl",
                  CLAUDE_TOKEN_FIXTURE not in written, "SECRET LEAKED INTO JSONL")


test_claude_token_active_readback_is_ok()
test_claude_token_missing_file_is_unknown()
test_claude_token_key_missing_is_failed()
test_claude_token_malformed_shape_never_calls_cli()
test_claude_token_cli_not_found_is_unknown()
test_claude_token_readback_without_pong_is_failed()
test_claude_token_cli_timeout_is_unknown()
test_claude_token_readback_runs_with_only_the_one_env_var_from_a_temp_cwd()
test_claude_token_mint_date_recorded_on_first_sight()
test_claude_token_mint_date_persists_and_ages_toward_expiring_soon()
test_claude_token_rotation_resets_the_mint_clock()
test_claude_token_value_never_leaks()


def test_expiring_soon_from_configured_rotation_date():
    from datetime import datetime, timedelta, timezone
    ch.SUBPROCESS_RUN = lambda *a, **k: subprocess.CompletedProcess(a, 0, b"", b"")
    soon = (datetime.now(timezone.utc) + timedelta(days=1)).date().isoformat()
    cred = _cred("rotation-soon", "shell_exit_status", probe={
        "type": "shell_exit_status", "command": ["true"], "timeout_s": 1},
        expiring_soon_days=3, expiry={"configured_rotation_date": soon})
    r = ch.evaluate_credential(cred)
    check("configured rotation date 1 day out (window 3) -> expiring_soon",
          r.bucket == "expiring_soon", r.bucket)


def test_failed_from_past_expiry():
    ch.SUBPROCESS_RUN = lambda *a, **k: subprocess.CompletedProcess(a, 0, b"", b"")
    cred = _cred("rotation-past", "shell_exit_status", probe={
        "type": "shell_exit_status", "command": ["true"], "timeout_s": 1},
        expiring_soon_days=3, expiry={"configured_rotation_date": "2020-01-01"})
    r = ch.evaluate_credential(cred)
    check("configured rotation date in the past -> failed", r.bucket == "failed", r.bucket)


def test_google_testing_mode_window():
    from datetime import datetime, timedelta, timezone
    # google testing mode true, verified today -> 7-day window, well inside it
    today = datetime.now(timezone.utc).date().isoformat()
    cred = _cred("google-ok", "config_only", expiring_soon_days=2,
                 expiry={"google_testing_mode": True, "consent_screen_last_verified": today})
    r = ch.evaluate_credential(cred)
    check("google testing mode verified today -> ok (well inside 7-day window)",
          r.bucket == "ok", r.bucket)

    stale = (datetime.now(timezone.utc) - timedelta(days=6)).date().isoformat()
    cred2 = _cred("google-soon", "config_only", expiring_soon_days=2,
                  expiry={"google_testing_mode": True, "consent_screen_last_verified": stale})
    r2 = ch.evaluate_credential(cred2)
    check("google testing mode verified 6 days ago (7-day cap, 2-day window) -> expiring_soon",
          r2.bucket == "expiring_soon", r2.bucket)

    ancient = (datetime.now(timezone.utc) - timedelta(days=10)).date().isoformat()
    cred3 = _cred("google-failed", "config_only", expiring_soon_days=2,
                  expiry={"google_testing_mode": True, "consent_screen_last_verified": ancient})
    r3 = ch.evaluate_credential(cred3)
    check("google testing mode verified 10 days ago -> failed (past the 7-day cap)",
          r3.bucket == "failed", r3.bucket)


def test_google_config_incomplete_is_unknown():
    cred = _cred("google-unset", "config_only",
                 expiry={"google_testing_mode": True, "consent_screen_last_verified": None})
    r = ch.evaluate_credential(cred)
    check("google testing_mode true with no verified date -> unknown, not guessed",
          r.bucket == "unknown", r.bucket)


def test_config_only_with_nothing_set_is_unknown_not_ok():
    # The real inventory ships google-oauth-worker-client with every expiry
    # field null until Joe fills them in. A config_only credential with no
    # testing-mode flag set yet must never default to "ok" — that would be
    # exactly the silent-pass rule 6 forbids.
    cred = _cred("google-unset-entirely", "config_only",
                 expiry={"google_testing_mode": None, "consent_screen_last_verified": None})
    r = ch.evaluate_credential(cred)
    check("config_only with google_testing_mode unset -> unknown, never a default ok",
          r.bucket == "unknown", r.bucket)


def test_config_only_testing_mode_false_is_ok():
    cred = _cred("google-published", "config_only",
                 expiry={"google_testing_mode": False})
    r = ch.evaluate_credential(cred)
    check("config_only with google_testing_mode explicitly False -> ok "
          "(Joe confirmed the consent screen is published)",
          r.bucket == "ok", r.bucket)


def test_unknown_probe_type():
    cred = _cred("nonsense-type", "this-type-does-not-exist")
    r = ch.evaluate_credential(cred)
    check("unrecognised probe type -> unknown", r.bucket == "unknown", r.bucket)


test_shell_exit_ok()
test_shell_exit_failed()
test_shell_exit_unknown_missing_tool()
test_worker_bearer_ok_and_unauthorized()
test_worker_bearer_unknown_when_token_absent()
test_expiring_soon_from_configured_rotation_date()
test_failed_from_past_expiry()
test_google_testing_mode_window()
test_google_config_incomplete_is_unknown()
test_config_only_with_nothing_set_is_unknown_not_ok()
test_config_only_testing_mode_false_is_ok()
test_unknown_probe_type()


# ═══════════════════════════════════════════════════════════════════════════
# no probe output ever reaches the jsonl
# ═══════════════════════════════════════════════════════════════════════════

def test_secret_never_reaches_jsonl():
    FAKE_SECRET = "sk-selftest-fake-secret-DO-NOT-LEAK-9f3a1c"

    def leaky_run(*a, **k):
        # A probe that misbehaves and prints a secret to stdout/stderr. The
        # handler must still only ever look at .returncode.
        return subprocess.CompletedProcess(a, 0, stdout=FAKE_SECRET.encode(),
                                            stderr=FAKE_SECRET.encode())
    ch.SUBPROCESS_RUN = leaky_run
    cred = _cred("leaky", "shell_exit_status", probe={
        "type": "shell_exit_status", "command": ["true"], "timeout_s": 1})

    rows = ch.run_all([cred])
    check("leaky probe still buckets ok on returncode 0",
          rows[0]["status"] == "ok", rows[0]["status"])

    with tempfile.TemporaryDirectory() as td:
        out_path = Path(td) / "credential-health.jsonl"
        ch.write_jsonl(rows, path=out_path)
        written = out_path.read_text()
        check("the fake secret never reaches the jsonl file",
              FAKE_SECRET not in written, "SECRET LEAKED INTO JSONL")
        line = json.loads(written.strip().splitlines()[0])
        check("the jsonl row carries only name/status/checked_at(/expires_at)",
              set(line.keys()) <= {"name", "status", "checked_at", "expires_at"},
              sorted(line.keys()))
        check("the jsonl row's status is the bucket, not any probe text",
              line["status"] == "ok", line)


def test_write_jsonl_refuses_disallowed_keys():
    bad_row = {"name": "x", "status": "ok", "checked_at": "now", "secret_value": "nope"}
    raised = False
    with tempfile.TemporaryDirectory() as td:
        out_path = Path(td) / "out.jsonl"
        try:
            ch.write_jsonl([bad_row], path=out_path)
        except AssertionError:
            raised = True
        check("write_jsonl refuses a row carrying an extra key rather than writing it",
              raised, "no AssertionError raised")
        check("nothing was written when the row was refused",
              not out_path.exists() or out_path.read_text() == "",
              "a partial/bad line reached disk")


test_secret_never_reaches_jsonl()
test_write_jsonl_refuses_disallowed_keys()


# ═══════════════════════════════════════════════════════════════════════════
# deduplication
# ═══════════════════════════════════════════════════════════════════════════

def test_dedup_same_bucket_does_not_refile():
    calls = []

    def fake_run(*a, **k):
        calls.append(a)
        return subprocess.CompletedProcess(a, 0, b"", b"")
    ch.SUBPROCESS_RUN = fake_run
    cred = _cred("dedup-cred", "shell_exit_status")
    state = {}
    r1 = ch.file_loop_if_needed(cred, "failed", state)
    r2 = ch.file_loop_if_needed(cred, "failed", state)
    check("first failed filing calls add-loop", r1 == "filed" and len(calls) == 1,
          (r1, len(calls)))
    check("second identical-bucket filing is deduped, not refiled",
          r2 == "deduped" and len(calls) == 1, (r2, len(calls)))


def test_dedup_bucket_change_refiles():
    calls = []

    def fake_run(*a, **k):
        calls.append(a)
        return subprocess.CompletedProcess(a, 0, b"", b"")
    ch.SUBPROCESS_RUN = fake_run
    cred = _cred("dedup-cred-2", "shell_exit_status")
    state = {}
    ch.file_loop_if_needed(cred, "expiring_soon", state)
    r2 = ch.file_loop_if_needed(cred, "failed", state)
    check("a bucket CHANGE (expiring_soon -> failed) refiles",
          r2 == "filed" and len(calls) == 2, (r2, len(calls)))


def test_dry_run_never_calls_subprocess():
    calls = []

    def fake_run(*a, **k):
        calls.append(a)
        return subprocess.CompletedProcess(a, 0, b"", b"")
    ch.SUBPROCESS_RUN = fake_run
    cred = _cred("dry-run-cred", "shell_exit_status")
    state = {}
    r = ch.file_loop_if_needed(cred, "failed", state, dry_run=True)
    check("--no-file-loops (dry_run=True) never invokes add-loop",
          r == "dry_run" and len(calls) == 0, (r, len(calls)))


test_dedup_same_bucket_does_not_refile()
test_dedup_bucket_change_refiles()
test_dry_run_never_calls_subprocess()


# ═══════════════════════════════════════════════════════════════════════════
# timeouts
# ═══════════════════════════════════════════════════════════════════════════

def test_shell_timeout_is_unknown_not_a_hang():
    def _raise(*a, **k):
        raise subprocess.TimeoutExpired(cmd=a[0] if a else "cmd", timeout=1)
    ch.SUBPROCESS_RUN = _raise
    cred = _cred("timeout-cred", "shell_exit_status", probe={
        "type": "shell_exit_status", "command": ["sleep", "999"], "timeout_s": 1})
    r = ch.evaluate_credential(cred)
    check("subprocess.TimeoutExpired -> unknown", r.bucket == "unknown", r.bucket)


def test_http_timeout_is_unknown():
    ch.HTTP_GET = lambda *a, **k: (None, None)
    cred = _cred("neon-timeout", "neon_api", probe={"type": "neon_api", "timeout_s": 1})
    import os
    os.environ["NEON_API_KEY"] = "fake-for-selftest"
    try:
        r = ch.evaluate_credential(cred)
        check("HTTP GET timeout (status None) -> unknown", r.bucket == "unknown", r.bucket)
    finally:
        del os.environ["NEON_API_KEY"]


def test_probe_exception_is_unknown_not_a_crash():
    def _raise(*a, **k):
        raise RuntimeError("boom")
    ch.SUBPROCESS_RUN = _raise
    cred = _cred("exception-cred", "shell_exit_status", probe={
        "type": "shell_exit_status", "command": ["true"], "timeout_s": 1})
    r = ch.evaluate_credential(cred)
    check("an unexpected exception in a probe -> unknown, run does not crash",
          r.bucket == "unknown", r.bucket)


test_shell_timeout_is_unknown_not_a_hang()
test_http_timeout_is_unknown()
test_probe_exception_is_unknown_not_a_crash()


# ═══════════════════════════════════════════════════════════════════════════
# end-to-end: inventory unreadable is reported, not a clean sweep
# ═══════════════════════════════════════════════════════════════════════════

def test_main_reports_unreadable_inventory_nonzero():
    rc = ch.main(["--inventory", "/nonexistent/path/does-not-exist.json"])
    check("main() on an unreadable inventory returns nonzero, not 0",
          rc == 1, rc)


test_main_reports_unreadable_inventory_nonzero()


print(f"\ncredential-health-selftest: {PASSED}/{PASSED + len(FAILED)} passed")
if FAILED:
    print("FAILED: " + ", ".join(FAILED))
    sys.exit(1)
print("SELFTEST MET: every bucket reachable, no probe output reaches the jsonl, "
      "dedup holds across ticks, timeouts and exceptions report unknown rather "
      "than hanging or crashing.")
