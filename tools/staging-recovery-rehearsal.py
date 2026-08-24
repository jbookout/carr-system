#!/usr/bin/env python3
"""Program 5 staging-only recovery rehearsal controller.

Runs the existing deployment wrapper for the exact current/prior/current release
chain.  It is deliberately not a generic provider deploy command: production
and caller-selected versions are refused before a provider command is reached.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def materialize_step_runtime(worktree: Path) -> None:
    """Link only validated ignored dependencies into one disposable worktree."""
    required = (
        (ROOT / ".venv", ".venv", "bin/python"),
        (ROOT / "mcp-server" / "node_modules", "mcp-server/node_modules", ".bin/wrangler"),
    )
    for source, relative_target, required_binary in required:
        if not source.is_dir() or not (source / required_binary).is_file():
            raise RuntimeError(f"controller runtime dependency is unavailable: {relative_target}")
        target = worktree / relative_target
        if target.exists() or target.is_symlink():
            raise RuntimeError(f"disposable worktree unexpectedly contains runtime dependency: {relative_target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(source, target, target_is_directory=True)

def checked(cmd: list[str], *, cwd: Path = ROOT) -> str:
    return subprocess.check_output(cmd, cwd=cwd, text=True).strip()

def execute_step(step: str, sha: str, args, attempt: str, idem: str) -> dict:
    """Use one disposable worktree; never the canonical checkout."""
    resolved = checked(["git", "rev-parse", "--verify", sha + "^{commit}"])
    if resolved != sha:
        raise ValueError("step SHA did not resolve exactly")
    base = Path(tempfile.mkdtemp(prefix="carr-staging-recovery-"))
    worktree = base / step
    try:
        subprocess.run(["git", "worktree", "add", "--detach", str(worktree), sha], cwd=ROOT, check=True)
        if checked(["git", "rev-parse", "HEAD"], cwd=worktree) != sha:
            raise RuntimeError("worktree HEAD differs from typed step binding")
        materialize_step_runtime(worktree)
        command = [str(worktree / "bin/deploy-worker.sh"), "--env", "staging", "--release-key", args.release_key,
                   "--release-sha", sha, "--recovery-attempt-id", attempt, "--recovery-step", step,
                   "--recovery-prior-release-key", args.prior_release_key, "--staging-receipt-idempotency-key", idem]
        try:
            result = subprocess.run(command, cwd=worktree, text=True, capture_output=True, timeout=300)
        except subprocess.TimeoutExpired:
            # A timeout after the wrapper may have prepared or mutated staging
            # is ambiguous by definition.  Return a normal failed receipt so
            # run() takes the isolated restore-only route and records unknown.
            return {"step": step, "sha": sha, "idempotency_key": idem, "exit_code": 124,
                    "output_tail": "deploy_worker_timeout", "command": command}
        return {"step": step, "sha": sha, "idempotency_key": idem, "exit_code": result.returncode,
                "output_tail": (result.stdout + result.stderr)[-1000:], "command": command}
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", str(worktree)], cwd=ROOT, check=False)
        shutil.rmtree(base, ignore_errors=True)


def record_restore_unknown(args, attempt: str, idem: str) -> dict:
    """Persist an ambiguous repair outcome without inventing a readback or bundle."""
    command = [str(ROOT / "tools" / "ops-record.py"), "staging-restore-only", "result",
               "--idempotency-key", idem, "--status", "unknown",
               "--reason", "restore_wrapper_nonzero_after_possible_mutation"]
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=60)
    # Do not retain deploy terminal text in durable evidence or the controller
    # result. The database result has a bounded reason; this is only an audit
    # of whether that result writer was reachable.
    return {"status": "unknown", "record_exit_code": result.returncode,
            "recorded": result.returncode == 0}


def run(argv: list[str]) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--release-key", required=True)
    p.add_argument("--prior-release-key", required=True)
    p.add_argument("--current-sha", required=True)
    p.add_argument("--prior-sha", required=True)
    p.add_argument("--environment", default="staging")
    p.add_argument("--execute", action="store_true")
    args = p.parse_args(argv)
    if args.environment != "staging":
        raise ValueError("recovery rehearsal is structurally staging-only")
    for sha in (args.current_sha, args.prior_sha):
        if len(sha) != 40 or any(c not in "0123456789abcdef" for c in sha):
            raise ValueError("recovery rehearsal requires exact lowercase commit SHAs")
    attempt = str(uuid.uuid4())
    steps = (("current_before", args.current_sha), ("prior", args.prior_sha),
             ("current_after", args.current_sha))
    receipts = []
    staging_may_have_changed = False
    for step, sha in steps:
        idem = str(uuid.uuid4())
        receipt = execute_step(step, sha, args, attempt, idem) if args.execute else {"step": step, "sha": sha, "idempotency_key": idem, "planned": True}
        receipts.append(receipt)
        # The wrapper persists intent before its Cloudflare mutation. Any nonzero
        # result after `prior` or `current_after` is therefore treated as possibly
        # changed; only a separate exact-current readback can close that doubt.
        staging_may_have_changed |= args.execute and step != "current_before"
        if receipt.get("exit_code", 0):
            recovery = None
            if staging_may_have_changed:
                restore_idem = str(uuid.uuid4())
                recovery = execute_step("restore_only", args.current_sha, args, attempt, restore_idem)
                recovery["recovery_only"] = True
                if recovery.get("exit_code"):
                    recovery["durable_outcome"] = record_restore_unknown(args, attempt, restore_idem)
                receipts.append(recovery)
            state = "restore_failed" if recovery and recovery.get("exit_code") else ("recovered_to_current" if recovery else "partial")
            print(json.dumps({"recovery_attempt_id": attempt, "state": state,
                              "bundle_complete": False, "receipts": receipts}))
            return 1
    print(json.dumps({"recovery_attempt_id": attempt, "state": "succeeded", "bundle_complete": True, "receipts": receipts}))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(run(sys.argv[1:]))
    except ValueError as exc:
        print(f"staging-recovery-rehearsal: {exc}", file=sys.stderr)
        raise SystemExit(2)
