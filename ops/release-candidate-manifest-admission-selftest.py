#!/usr/bin/env python3
"""Hermetic contract for exact release-candidate manifest admission."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

REPO = Path(__file__).resolve().parents[1]
RECORD = REPO / "tools" / "ops-record.py"
MANIFEST = REPO / "tools" / "release-manifest.py"
FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  ok    {name}")
    else:
        FAILURES.append(name)
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))


def run_manifest(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(MANIFEST), *args], cwd=REPO,
        capture_output=True, text=True, timeout=300,
    )


def build(environment: str, path: Path) -> dict[str, Any]:
    built = run_manifest(
        "build", "--sha", "HEAD", "--environment", environment,
        "--performance-budget-ref", "runbook:staging-performance-v1",
        "--performance-budget-ms", "1000",
        "--recovery-strategy", "rollback",
        "--rollback-plan-ref", "runbook:staging-rollback-v1",
    )
    if built.returncode != 0:
        raise RuntimeError((built.stderr or built.stdout).strip())
    path.write_text(built.stdout, encoding="utf-8")
    return json.loads(built.stdout)


def args_for(environment: str, manifest: Path, **changes: Any) -> SimpleNamespace:
    values: dict[str, Any] = {
        "action": "candidate", "key": f"candidate-{environment}",
        "service": "carr-mcp", "environment": environment,
        "manifest": str(manifest), "sha": None,
        "provider": None, "provider_version_id": None,
        "rollback_ready": False, "rollback_plan": None,
    }
    values.update(changes)
    return SimpleNamespace(**values)


def main() -> int:
    print("release-candidate-manifest-admission-selftest: exact target and assurance")
    spec = importlib.util.spec_from_file_location("ops_record_candidate_test", RECORD)
    assert spec and spec.loader
    module: Any = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    validator = getattr(module, "release_candidate_manifest_refusal", None)
    check("1. candidate intake exposes one shared fail-closed validator",
          callable(validator))

    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        staging_path = tmp / "staging.json"
        production_path = tmp / "production.json"
        staging = build("staging", staging_path)
        production = build("production", production_path)

        if callable(validator):
            check("2. exact staging target with complete assurance verifies",
                  validator(args_for("staging", staging_path), staging) is None)

            # Production has an additional immutable provider binding. Bind it
            # through the canonical tool before asking admission to verify it.
            bound = run_manifest(
                "bind-provider", "--manifest", str(production_path),
                "--provider", "cloudflare-workers",
                "--provider-version-id", "11111111-2222-4333-8444-555555555555",
            )
            check("3. production source manifest binds for the admission fixture",
                  bound.returncode == 0, (bound.stderr or bound.stdout)[:160])
            if bound.returncode == 0:
                production_path.write_text(bound.stdout, encoding="utf-8")
                production = json.loads(bound.stdout)
                production_args = args_for(
                    "production", production_path,
                    provider="cloudflare-workers",
                    provider_version_id="11111111-2222-4333-8444-555555555555",
                )
                check("3a. exact production target/provider/assurance verifies",
                      validator(production_args, production) is None)

            # Every CLI environment must bind to the manifest environment before
            # verification or any connection. Local/rehearsal are non-serving,
            # but they must not become an unchecked bypass.
            for environment in ("local", "rehearsal", "staging", "production"):
                wrong_path, wrong_manifest = ((staging_path, staging)
                                               if environment == "production"
                                               else (production_path, production))
                refusal = validator(args_for(environment, wrong_path), wrong_manifest)
                check(f"4. {environment} refuses a different manifest target",
                      isinstance(refusal, str) and "service/environment" in refusal)

            for environment in ("local", "rehearsal"):
                nonserving = dict(staging)
                nonserving["environment"] = environment
                nonserving_path = tmp / f"{environment}.json"
                nonserving_path.write_text(json.dumps(nonserving), encoding="utf-8")
                refusal = validator(
                    args_for(environment, nonserving_path), nonserving
                )
                check(f"4a. exact {environment} target still runs manifest verification",
                      isinstance(refusal, str)
                      and "manifest verification failed" in refusal)

            missing_assurance = dict(staging)
            for field in (
                "performance_budget_ref", "performance_budget_ms",
                "recovery_strategy", "rollback_plan_ref",
            ):
                missing_assurance[field] = None
            missing_assurance["rollback_ready"] = False
            missing_path = tmp / "missing-assurance.json"
            missing_path.write_text(json.dumps(missing_assurance), encoding="utf-8")
            refusal = validator(args_for("staging", missing_path), missing_assurance)
            check("5. staging candidate assurance is mandatory and approval-bound",
                  isinstance(refusal, str) and "assurance" in refusal.lower())

            refusal = validator(
                args_for("staging", staging_path,
                         rollback_plan="runbook:caller-supplied-different-plan"),
                staging,
            )
            check("5a. caller rollback fields cannot replace the hashed manifest plan",
                  isinstance(refusal, str) and "differs" in refusal)

        # Reproduce the real defect: a production-default manifest passed to a
        # staging candidate must refuse before the database connection opens.
        connections: list[str] = []

        def forbidden_connect(kind: str):
            connections.append(kind)
            raise AssertionError("malformed candidate reached database connection")

        module.connect = forbidden_connect
        malformed_args = args_for("staging", production_path)
        # cmd_release's candidate write fields are intentionally absent: an exact
        # target refusal must occur before any of them or a credential are read.
        malformed_rc = module.cmd_release(malformed_args)
        check("6. production-default manifest for staging refuses before DB write",
              malformed_rc == 2 and not connections,
              f"rc={malformed_rc} connections={connections}")

    source = (REPO / "bin" / "deploy-worker.sh").read_text(encoding="utf-8")
    check("7. deploys use one manifest builder for exact target/assurance recomputation",
          "build_release_manifest()" in source
          and source.count("build_release_manifest ") >= 3)
    check("8. standalone staging refuses without the full assurance preimage",
          "standalone staging release requires performance/recovery assurance" in source)

    record_source = RECORD.read_text(encoding="utf-8")
    check("9. named successor is locked and same-target before old-row terminalization",
          "with eligible_successor as" in record_source
          and "for share" in record_source
          and "successor.service_id = target.service_id" in record_source
          and "successor.environment = target.environment" in record_source)
    candidate_insert = record_source[record_source.index("insert into ops.release"):]
    candidate_insert = candidate_insert[:candidate_insert.index("row = cur.fetchone()")]
    check("10. persisted rollback evidence comes from the verified manifest",
          'manifest.get("rollback_ready")' in candidate_insert
          and 'manifest.get("rollback_plan_ref")' in candidate_insert
          and "else args.rollback_ready" not in candidate_insert
          and "else args.rollback_plan" not in candidate_insert)

    if FAILURES:
        print(f"release-candidate-manifest-admission-selftest: {len(FAILURES)} FAILED")
        return 1
    print("release-candidate-manifest-admission-selftest: contract holds")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
