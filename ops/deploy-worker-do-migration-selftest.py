#!/usr/bin/env python3
"""deploy-worker-do-migration-selftest.py — a pending Durable Object migration
ships unattended, and an unknown applied tag ships nothing.

WHAT THIS GUARDS. bin/deploy-worker.sh --upload-version runs `wrangler versions
upload`, which wrangler 4.137 refuses while the Worker has a pending Durable
Object migration. The wrapper's do-migration block reads the applied tag,
applies a pending one with the documented `wrangler deploy`, verifies it, writes
a receipt, and only then lets the ordinary upload run.

HOW. The house pattern for this wrapper (ops/deploy-release-wiring-selftest.py):
the whole script cannot run hermetically (exact origin/main, a database, real
credentials), so the exact block between its BEGIN/END do-migration markers is
extracted VERBATIM and executed against a fake `wrangler` and a fake `curl`
that model the real ones: the fake `versions upload` refuses exactly as
wrangler 4.137 does while a migration is pending, so a harness that reaches the
upload proves the ordering, not merely the absence of an error. The real
ops/worker-do-migration.py and ops/verify-worker-release.py do the judging.

Scenarios (the brief's four, plus the edges that make them honest):
  A  no migration declared            -> no metadata read, no deploy, upload runs
  A2 declared and already applied     -> read, no deploy, upload runs
  B  pending                          -> deploy of the exact SHA, re-read,
                                         identity read-back, receipt, upload runs
  B2 pending after an earlier tag     -> only the later tag is pending
  C  applied tag unknown              -> REFUSED before any deploy or upload
     (metadata HTTP failure, malformed JSON, wrong script, undeclared tag,
      no credential)
  D  migration deploy fails           -> REFUSED; not-applied says nothing to
                                         roll back, applied says forward fix
                                         only; a failed read-back likewise
  E  source wiring: the block runs before `versions upload`, the promotion
     annotates every deployment row, and the token never reaches argv.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "bin" / "deploy-worker.sh"
HEAD_SHA = "a" * 40
V0 = "1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d"
V1 = "0f1e2d3c-4b5a-4968-8776-655443322110"
TOKEN = "cf-selftest-token-9f8e7d6c5b4a"
TAG1 = "v1-workflow-census-anchor"
TAG2 = "v2-selftest-second-class"

FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  ok    {name}")
    else:
        FAILURES.append(name)
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))


FAKE_WRANGLER = r'''#!/usr/bin/env python3
import json, os, sys
state_path = os.environ["FAKE_STATE"]
state = json.load(open(state_path))
argv = sys.argv[1:]
with open(os.environ["FAKE_CALLS"], "a") as fh:
    fh.write(json.dumps(["wrangler", *argv]) + "\n")
def save():
    json.dump(state, open(state_path, "w"))
if argv[:2] == ["auth", "token"]:
    if state.get("auth_fail"):
        print("Not logged in.", file=sys.stderr); sys.exit(1)
    print(json.dumps({"type": "api_token", "token": os.environ["FAKE_TOKEN"]}, indent=2)); sys.exit(0)
if argv[:1] == ["deploy"]:
    mode = state.get("deploy_mode", "ok")
    sha = next(a.split(":", 1)[1] for a in argv if a.startswith("GIT_SHA:"))
    if mode == "fail_before_apply":
        print("X [ERROR] A request to the Cloudflare API failed."); sys.exit(1)
    state["applied_tag"] = state["latest_tag"]
    state["served_sha"] = sha
    state["served_version"] = os.environ["FAKE_V0"]
    save()
    print("Uploaded carr-mcp")
    print("Current Version ID: " + os.environ["FAKE_V0"])
    if mode == "fail_after_apply":
        print("X [ERROR] custom domain sync failed"); sys.exit(1)
    sys.exit(0)
if argv[:2] == ["versions", "upload"]:
    if state.get("latest_tag") and state.get("applied_tag") != state["latest_tag"]:
        print("X [ERROR] This Worker has a pending Durable Object migration, which cannot be "
              "applied by `wrangler versions upload`."); sys.exit(1)
    print("Worker Version ID: " + os.environ["FAKE_V1"]); sys.exit(0)
print("fake wrangler: unexpected " + " ".join(argv), file=sys.stderr); sys.exit(2)
'''

FAKE_CURL = r'''#!/usr/bin/env python3
import json, os, sys
state = json.load(open(os.environ["FAKE_STATE"]))
argv = sys.argv[1:]
stdin = sys.stdin.read() if "--config" in argv else ""
with open(os.environ["FAKE_CALLS"], "a") as fh:
    fh.write(json.dumps(["curl", *argv]) + "\n")
url = next(a for a in argv if a.startswith("https://"))
out = argv[argv.index("-o") + 1] if "-o" in argv else None
if "/workers/services/" in url:
    if f"Authorization: Bearer {os.environ['FAKE_TOKEN']}" not in stdin:
        sys.exit(22)
    mode = state.get("services_mode", "ok")
    if mode == "http_error":
        sys.exit(22)
    if mode == "malformed":
        body = "<html>not json</html>"
    else:
        script = {"id": state.get("script_id", "carr-mcp"), "etag": "e"}
        if state.get("applied_tag") is not None:
            script["migration_tag"] = state["applied_tag"]
        body = json.dumps({"success": True, "errors": [], "result": {
            "id": "carr-mcp", "default_environment": {"environment": "production", "script": script}}})
    open(out, "w").write(body)
    sys.exit(0)
if url.endswith("/release"):
    if state.get("served_sha") is None:
        sys.exit(22)
    print(json.dumps({"ok": True, "env": {"value": "production"},
                      "git_sha": {"value": "b" * 40 if state.get("stale_readback") else state["served_sha"]},
                      "provider": "cloudflare-workers",
                      "worker_version": {"id": state["served_version"]},
                      "program6_actions": {"enabled": True, "posture": "enabled", "reason": None},
                      "schema": {"highest_applied_migration": "0590_selftest.sql", "applied_count": 5}}))
    sys.exit(0)
sys.exit(6)
'''


def extract_block(source: str) -> str:
    start = source.index("# ---------- pending Durable Object migration (BEGIN do-migration block)")
    end = source.index("# ---------- (END do-migration block) ----------", start)
    return source[start:end]


def wrangler_toml(tags: list[str]) -> str:
    base = (REPO / "mcp-server" / "wrangler.toml").read_text(encoding="utf-8")
    # The fixture starts from the real file, minus any migrations it may carry
    # by the time this runs, and declares exactly the tags each scenario needs.
    base = re.sub(r"(?ms)^\[\[migrations\]\]\n(?:[^\[\n].*\n|\n)*", "", base)
    extra = "".join(f'\n[[migrations]]\ntag = "{t}"\nnew_sqlite_classes = ["C{i}"]\n'
                    for i, t in enumerate(tags))
    return base + extra


def run(block: str, *, tags: list[str], state: dict) -> dict:
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        (tmp / "bin").mkdir()
        (tmp / "worker").mkdir()
        (tmp / "repo").mkdir()
        (tmp / "repo" / "ops").symlink_to(REPO / "ops")
        (tmp / "worker" / "wrangler.toml").write_text(wrangler_toml(tags), encoding="utf-8")
        for name, body in (("wrangler", FAKE_WRANGLER), ("curl", FAKE_CURL)):
            path = tmp / "bin" / name
            path.write_text(body.replace("/usr/bin/env python3", sys.executable, 1), encoding="utf-8")
            path.chmod(0o755)
        state = {"latest_tag": tags[-1] if tags else None, **state}
        (tmp / "state.json").write_text(json.dumps(state), encoding="utf-8")
        calls = tmp / "calls.jsonl"
        calls.write_text("", encoding="utf-8")
        harness = tmp / "harness.sh"
        harness.write_text(
            "#!/bin/sh\nset -eu\n"
            'fail() { echo ""; echo "REFUSED: $1" >&2; echo "" >&2; exit 1; }\n'
            + block +
            '\napply_pending_do_migration\n'
            'UP="$("$WRANGLER" versions upload --var "GIT_SHA:$HEAD_SHA")" || { echo "$UP"; exit 9; }\n'
            'echo "$UP"\necho "UPLOAD-REACHED evidence=${DO_MIGRATION_EVIDENCE:-}"\n',
            encoding="utf-8")
        env = {
            "PATH": f"{tmp / 'bin'}:{os.environ.get('PATH', '/usr/bin:/bin')}",
            "HOME": str(tmp), "TMPDIR": str(tmp),
            "REPO": str(tmp / "repo"), "WORKER_DIR": str(tmp / "worker"),
            "WRANGLER": str(tmp / "bin" / "wrangler"), "PY": sys.executable,
            "TARGET_ENV": "production", "HEAD_SHA": HEAD_SHA, "PROVIDER": "cloudflare-workers",
            "CANDIDATE_MANIFEST": "{}", "CANDIDATE_MANIFEST_DIGEST": "sha256:" + "0" * 64,
            "EXPECTED_PROGRAM6_ACTIONS": "enabled",
            "EXPECTED_SCHEMA_HIGHEST_MIGRATION": "0590_selftest.sql",
            "EXPECTED_SCHEMA_APPLIED_COUNT": "5",
            "CARR_READBACK_ATTEMPTS": "2", "CARR_READBACK_SLEEP": "0",
            "FAKE_STATE": str(tmp / "state.json"), "FAKE_CALLS": str(calls),
            "FAKE_TOKEN": TOKEN, "FAKE_V0": V0, "FAKE_V1": V1,
        }
        done = subprocess.run(["sh", str(harness)], env=env, capture_output=True, text=True,
                              timeout=120)
        receipt_path = tmp / "repo" / "out" / "deploy-worker" / f"do-migration-{HEAD_SHA}.json"
        receipt = json.loads(receipt_path.read_text()) if receipt_path.exists() else None
        receipt_check = None
        if receipt is not None:
            receipt_check = subprocess.run(
                [sys.executable, str(REPO / "ops" / "worker-do-migration.py"), "receipt",
                 "--file", str(receipt_path), "--sha", HEAD_SHA],
                capture_output=True, text=True).returncode
        rows = [json.loads(line) for line in calls.read_text().splitlines() if line.strip()]
        return {"rc": done.returncode, "out": done.stdout, "err": done.stderr,
                "all": done.stdout + done.stderr, "calls": rows, "receipt": receipt,
                "receipt_valid": receipt_check == 0}


def wrangler_calls(res: dict, *prefix: str) -> list[list[str]]:
    return [c[1:] for c in res["calls"] if c[0] == "wrangler" and c[1:1 + len(prefix)] == list(prefix)]


def services_reads(res: dict) -> int:
    return sum(1 for c in res["calls"] if c[0] == "curl" and any("/workers/services/" in a for a in c))


def token_in_argv(res: dict) -> bool:
    return any(TOKEN in a for c in res["calls"] for a in c)


def main() -> int:
    print("deploy-worker-do-migration-selftest: a pending DO migration ships; an unknown tag ships nothing")
    source = SCRIPT.read_text(encoding="utf-8")
    block = extract_block(source)

    # A. current main: no [[migrations]] at all
    res = run(block, tags=[], state={})
    check("A. no declared migration: upload runs unchanged", res["rc"] == 0
          and "UPLOAD-REACHED evidence=" in res["out"] and f"Worker Version ID: {V1}" in res["out"],
          res["all"][-600:])
    check("A. no declared migration: no metadata read and no deploy",
          services_reads(res) == 0 and not wrangler_calls(res, "deploy"))
    check("A. no receipt is written", res["receipt"] is None)

    # A2. declared and already applied
    res = run(block, tags=[TAG1], state={"applied_tag": TAG1})
    check("A2. applied == declared latest: upload runs unchanged",
          res["rc"] == 0 and "UPLOAD-REACHED evidence=\n" in res["out"] + "\n", res["all"][-600:])
    check("A2. the applied tag was READ, and nothing was deployed",
          services_reads(res) == 1 and not wrangler_calls(res, "deploy"))
    check("A2. no receipt, no migration marker",
          res["receipt"] is None and "DO migration applied:" not in res["out"])

    # B. pending (the first release after #1244: nothing applied yet)
    res = run(block, tags=[TAG1], state={"applied_tag": None})
    deploys = wrangler_calls(res, "deploy")
    check("B. pending: exactly one deploy, then the ordinary upload succeeds",
          res["rc"] == 0 and len(deploys) == 1 and f"Worker Version ID: {V1}" in res["out"],
          res["all"][-900:])
    check("B. the deploy carries this SHA's stamps and names the tag",
          bool(deploys) and f"GIT_SHA:{HEAD_SHA}" in deploys[0]
          and any(a.startswith("CANDIDATE_MANIFEST_DIGEST:") for a in deploys[0])
          and f"carr do-migration {TAG1} {HEAD_SHA}" in deploys[0]
          and "--env" not in deploys[0])
    order = [c[1] if c[0] == "wrangler" else "curl" for c in res["calls"]]
    check("B. order: read, deploy, re-read, read-back, then upload",
          "deploy" in order and "versions" in order
          and order.index("deploy") < order.index("versions")
          and services_reads(res) == 2, str(order))
    check("B. the marker line names tag, prior tag and version",
          f"DO migration applied: tag={TAG1} from=none version={V0}" in res["out"])
    rc = res["receipt"] or {}
    check("B. receipt records the applied, verified migration",
          rc.get("state") == "applied_verified" and rc.get("new_tag") == TAG1
          and rc.get("old_tag") is None and rc.get("migration_version_id") == V0
          and rc.get("git_sha") == HEAD_SHA and rc.get("readback") == "identity-ok"
          and res["receipt_valid"], json.dumps(rc))
    check("B. deployment evidence carries do-migration=<tag>@<version>",
          f"UPLOAD-REACHED evidence=do-migration={TAG1}@{V0}" in res["out"])
    check("B. the bearer token never reached any argv", not token_in_argv(res))

    # B2. two tags, the first already applied
    res = run(block, tags=[TAG1, TAG2], state={"applied_tag": TAG1})
    check("B2. only the later tag is pending and applied",
          res["rc"] == 0 and f"pending: {TAG2} (applied: {TAG1})" in res["out"]
          and (res["receipt"] or {}).get("pending_tags") == [TAG2]
          and (res["receipt"] or {}).get("old_tag") == TAG1, res["all"][-600:])

    # C. unknown applied tag: every variant refuses before any mutation
    for label, tags, state in (
            ("metadata read fails", [TAG1], {"services_mode": "http_error"}),
            ("metadata is not JSON", [TAG1], {"services_mode": "malformed"}),
            ("metadata names another script", [TAG1], {"script_id": "carr-mcp-staging"}),
            ("applied tag is not declared", [TAG1], {"applied_tag": "v0-someone-else"}),
            ("no credential", [TAG1], {"auth_fail": True})):
        res = run(block, tags=tags, state=state)
        check(f"C. {label}: refused, fail-closed and loud",
              res["rc"] == 1 and "REFUSED:" in res["err"] and "UNKNOWN" in res["err"]
              and "traffic was not changed" in res["err"], res["all"][-600:])
        check(f"C. {label}: no deploy, no upload, no receipt",
              not wrangler_calls(res, "deploy") and not wrangler_calls(res, "versions")
              and res["receipt"] is None)

    # D1. migration deploy fails before anything is applied
    res = run(block, tags=[TAG1], state={"applied_tag": None, "deploy_mode": "fail_before_apply"})
    check("D1. failed deploy, tag unchanged: refused, says nothing to roll back",
          res["rc"] == 1 and "NOT applied" in res["err"]
          and "Nothing needs rolling back" in res["err"]
          and not wrangler_calls(res, "versions"), res["all"][-600:])
    check("D1. receipt says not_applied; no applied marker",
          (res["receipt"] or {}).get("state") == "not_applied"
          and "DO migration applied:" not in res["out"])

    # D2. migration deploy reports failure after the tag was applied
    res = run(block, tags=[TAG1], state={"applied_tag": None, "deploy_mode": "fail_after_apply"})
    check("D2. failed deploy, tag applied: refused, rollback blocked, forward fix only",
          res["rc"] == 1 and "WAS applied" in res["err"]
          and "FORWARD FIX ONLY" in res["err"] and "do not promote a" in res["err"]
          and not wrangler_calls(res, "versions"), res["all"][-600:])
    check("D2. receipt applied_unverified and the marker is printed for the pipeline",
          (res["receipt"] or {}).get("state") == "applied_unverified"
          and f"DO migration applied: tag={TAG1}" in res["out"] and res["receipt_valid"])

    # D3. deploy returned 0 but Production does not read back the exact identity
    res = run(block, tags=[TAG1], state={"applied_tag": None, "stale_readback": True})
    check("D3. applied but read-back mismatch: refused, forward fix, no upload",
          res["rc"] == 1 and "did not read back" in res["err"]
          and "FORWARD FIX ONLY" in res["err"] and not wrangler_calls(res, "versions"),
          res["all"][-600:])
    check("D3. receipt applied_unverified with mismatch read-back",
          (res["receipt"] or {}).get("state") == "applied_unverified"
          and (res["receipt"] or {}).get("readback") == "mismatch")

    # E. source wiring
    upload_at = source.index('VERSION_UPLOAD_OUTPUT="$("$WRANGLER" versions upload')
    call_at = source.rfind("\n  apply_pending_do_migration\n", 0, upload_at)
    branch_at = source.index('if [ "$VERSION_MODE" = "upload" ]; then\n  if [ -n "$FOUNDATION_ASSURANCE_STAGING_PROVIDER" ]')
    check("E1. the upload branch applies/refuses the migration before `versions upload`",
          branch_at < call_at < upload_at)
    promote_at = source.index('"$WRANGLER" versions deploy "${PROVIDER_VERSION_ID}@100"')
    check("E2. promotion loads the receipt before it moves traffic",
          source.rfind("load_do_migration_receipt\n", 0, promote_at) > source.index("(END do-migration block)"))
    check("E3. every deployment row of that promotion names the migration",
          'rd_evidence_ref="$rd_evidence_ref;$DO_MIGRATION_EVIDENCE"' in source)
    check("E4. the smoke-failure rollback advice states the migration limit",
          'echo "$DO_MIGRATION_ROLLBACK_NOTE"' in source)
    check("E5. staging's deploy path is untouched by the block",
          "apply_pending_do_migration" not in source[source.index("deploy_staging_worker() {"):
                                                    source.index("# ---------- pending Durable Object")])

    if FAILURES:
        print(f"\n{len(FAILURES)} check(s) FAILED")
        return 1
    print("\nall checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
