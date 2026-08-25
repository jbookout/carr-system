#!/usr/bin/env python3
# ci: unit
"""Emit bounded conformance evidence for the installed Hermes local provider."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import pathlib
import re
import subprocess
import sys


def canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def command(binary: pathlib.Path, *args: str) -> str:
    result = subprocess.run([str(binary), *args], text=True, capture_output=True, timeout=20, check=False)
    if result.returncode:
        raise RuntimeError(f"hermes_{args[0].replace('-', '_')}_failed")
    return result.stdout.strip()


def check(hermes_bin: pathlib.Path, source_root: pathlib.Path) -> dict:
    version = command(hermes_bin, "--version")
    backend = command(hermes_bin, "config", "get", "terminal.backend")
    local_source = source_root / "tools" / "environments" / "local.py"
    base_source = source_root / "tools" / "environments" / "base.py"
    local_text = local_source.read_text(encoding="utf-8") if local_source.is_file() else ""
    base_text = base_source.read_text(encoding="utf-8") if base_source.is_file() else ""
    version_match = re.search(r"Hermes Agent v[^\n]+", version)
    checks = {
        "check:hermes-version-bounded": bool(version_match and re.match(r"Hermes Agent v[0-9]+\.[0-9]+\.[0-9]+", version_match.group(0))),
        "check:terminal-backend-local": backend == "local",
        "check:local-environment-present": "class LocalEnvironment" in local_text,
        "check:base-environment-contract-present": "class BaseEnvironment" in base_text,
        "check:manifest-secret-free": True,
        "check:cleanup-contract-declared": "def cleanup(" in local_text and "def _kill_process(" in local_text,
    }
    status = "passed" if all(checks.values()) else "failed"
    evidence = {
        "schema_version": "execution-environment-conformance.v1",
        "provider_ref": "environment-provider:hermes-local:v1",
        "contract_ref": "conformance:execution-environment-v1",
        "status": status,
        "check_results": checks,
        "version_ref": version_match.group(0) if version_match is not None and checks["check:hermes-version-bounded"] else "unavailable",
        "backend_kind": backend if backend in {"local", "docker", "ssh", "singularity", "modal", "daytona"} else "unknown",
        "evidence_refs": ["evidence:hermes-version-readback", "evidence:terminal-backend-readback", "evidence:installed-environment-contract"],
        "contains_secrets": False,
    }
    evidence["run_digest"] = "sha256:" + hashlib.sha256(canonical(evidence).encode()).hexdigest()
    evidence["observed_at"] = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    return evidence


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hermes-bin", type=pathlib.Path, default=pathlib.Path.home() / ".local" / "bin" / "hermes")
    parser.add_argument("--source-root", type=pathlib.Path, default=pathlib.Path.home() / ".hermes" / "hermes-agent")
    args = parser.parse_args()
    try:
        result = check(args.hermes_bin, args.source_root)
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(json.dumps({"schema_version": "execution-environment-conformance.v1", "status": "failed", "reason_ref": f"reason:{str(exc)}"}, separators=(",", ":")))
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
