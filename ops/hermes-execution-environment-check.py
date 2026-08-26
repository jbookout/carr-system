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

from git_env import scrubbed_env


EXPECTED_LOCAL_IMPLEMENTATION_DIGEST = "sha256:7d680c252bedc88ff7b80d50a5bfbdb9b926823d8bbc521f606e7b58237cbc1e"
EXPECTED_UPSTREAM_COMMIT = "1bbb6e5bce56e721ab685af4cd87df21bbff4d35"
EXPECTED_LOCAL_HEAD = "706f33d42415d706b8f93dd299f4b317428e4a6b"
EXPECTED_HERMES_VERSION = "0.20.5"
SECRET_ASSIGNMENT = re.compile(
    r"(?im)^\s*(?:api[_-]?key|access[_-]?token|secret|password|private[_-]?key)\s*=\s*['\"][^'\"]{8,}['\"]"
)
# Build the sentinel fragments at runtime so the repository's unconditional
# private-key-block scanner does not mistake the detector for key material.
SECRET_MARKERS = ("-----BEGIN " + "PRIVATE KEY-----", "-----BEGIN OPENSSH " + "PRIVATE KEY-----")


def canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha_text(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode()).hexdigest()


def _local_manifest(implementation_digest: str) -> dict:
    manifest = {
        "schema_version": "execution-environment-provider.v1",
        "provider_key": "hermes-local",
        "provider_version": 1,
        "display_name": "Hermes Local Terminal",
        "source_class": "built_in",
        "backend_kind": "local",
        "implementation_ref": "hermes:tools.environments.local.LocalEnvironment",
        "implementation_digest": implementation_digest,
        "capability_refs": ["environment:exec", "environment:filesystem", "environment:process"],
        "operation_refs": ["operation:create", "operation:exec", "operation:cancel", "operation:destroy", "operation:health"],
        "isolation_class": "host_process",
        "egress_policy_ref": "egress:host-governed",
        "secret_policy_ref": "secrets:never-in-manifest",
        "persistence_mode": "session_scoped",
        "resource_policy_ref": "resources:bounded-local-v1",
        "cleanup_policy_ref": "cleanup:process-tree-v1",
        "threat_model_ref": "threat-model:local-trusted-input-v1",
        "conformance_contract_ref": "conformance:execution-environment-v1",
        "conformance_contract_digest": _sha_text("conformance:execution-environment-v1"),
        "configuration_schema_digest": _sha_text("hermes:terminal.backend:local:v1"),
        "package_provenance": {
            "package_ref": "package:nous-hermes-agent",
            "package_digest": _sha_text("hermes-upstream:1bbb6e5bce56e721ab685af4cd87df21bbff4d35"),
            "signature_ref": "signature:upstream-git-commit",
            "sbom_ref": "sbom:hermes-installed-tree",
        },
        "collision_policy": "protected_builtin",
        "contains_secrets": False,
    }
    manifest["manifest_digest"] = "sha256:" + hashlib.sha256(canonical(manifest).encode()).hexdigest()
    return manifest


def command(binary: pathlib.Path, *args: str) -> str:
    result = subprocess.run([str(binary), *args], text=True, capture_output=True, timeout=20, check=False)
    if result.returncode:
        raise RuntimeError(f"hermes_{args[0].replace('-', '_')}_failed")
    return result.stdout.strip()


def _git(source_root: pathlib.Path, *args: str) -> tuple[int, str]:
    result = subprocess.run(
        ["git", "-C", str(source_root), *args], text=True, capture_output=True,
        timeout=20, check=False, env=scrubbed_env(),
    )
    return result.returncode, result.stdout.strip()


def _source_digest(path: pathlib.Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _contains_secret_material(*sources: str) -> bool:
    combined = "\n".join(sources)
    return bool(SECRET_ASSIGNMENT.search(combined) or any(marker in combined for marker in SECRET_MARKERS))


def check(
    hermes_bin: pathlib.Path,
    source_root: pathlib.Path,
    *,
    expected_implementation_digest: str = EXPECTED_LOCAL_IMPLEMENTATION_DIGEST,
    expected_upstream_commit: str = EXPECTED_UPSTREAM_COMMIT,
    expected_local_head: str = EXPECTED_LOCAL_HEAD,
    expected_version: str = EXPECTED_HERMES_VERSION,
) -> dict:
    version = command(hermes_bin, "--version")
    backend = command(hermes_bin, "config", "get", "terminal.backend")
    local_source = source_root / "tools" / "environments" / "local.py"
    base_source = source_root / "tools" / "environments" / "base.py"
    local_text = local_source.read_text(encoding="utf-8") if local_source.is_file() else ""
    base_text = base_source.read_text(encoding="utf-8") if base_source.is_file() else ""
    implementation_digest = _source_digest(local_source) if local_source.is_file() else "unavailable"
    contains_secrets = _contains_secret_material(local_text, base_text)
    expected_manifest = _local_manifest(expected_implementation_digest)
    version_match = re.search(r"Hermes Agent v[^\n]+", version)
    head_rc, head_commit = _git(source_root, "rev-parse", "HEAD")
    expected_rc, resolved_expected = _git(source_root, "rev-parse", f"{expected_upstream_commit}^{{commit}}")
    ancestor_rc, _ = _git(source_root, "merge-base", "--is-ancestor", expected_upstream_commit, "HEAD")
    shallow_rc, shallow_value = _git(source_root, "rev-parse", "--is-shallow-repository")
    tree_rc, tree_status = _git(source_root, "status", "--porcelain=v1", "--untracked-files=all")
    package_tree_clean = tree_rc == 0 and tree_status == ""
    version_value = version_match.group(0) if version_match is not None else ""
    package_provenance_exact = (
        head_rc == 0 and head_commit == expected_local_head
        and expected_rc == 0 and resolved_expected == expected_upstream_commit
        and (ancestor_rc == 0 or (shallow_rc == 0 and shallow_value == "true"))
        and f"upstream {expected_upstream_commit[:8]}" in version_value
        and f"local {expected_local_head[:8]}" in version_value
        and package_tree_clean
    )
    observed_package_digest = (
        expected_manifest["package_provenance"]["package_digest"]
        if package_provenance_exact
        else _sha_text("hermes-head:" + (head_commit if head_rc == 0 else "unavailable"))
    )
    checks = {
        "check:hermes-version-exact": bool(version_match and re.match(rf"^Hermes Agent v{re.escape(expected_version)}(?:\s|\()", version_match.group(0))),
        "check:package-provenance-exact": package_provenance_exact,
        "check:package-tree-clean": package_tree_clean,
        "check:terminal-backend-local": backend == "local",
        "check:local-environment-present": "class LocalEnvironment" in local_text,
        "check:base-environment-contract-present": "class BaseEnvironment" in base_text,
        "check:implementation-digest-exact": implementation_digest == expected_implementation_digest,
        "check:source-secret-scan": not contains_secrets,
        "check:cleanup-contract-declared": "def cleanup(" in local_text and "def _kill_process(" in local_text,
    }
    status = "passed" if all(checks.values()) else "failed"
    evidence = {
        "schema_version": "execution-environment-conformance.v1",
        "provider_ref": "environment-provider:hermes-local:v1",
        "manifest_digest": expected_manifest["manifest_digest"],
        "implementation_digest": implementation_digest,
        "package_digest": observed_package_digest,
        "package_revision_ref": "git:" + (head_commit if head_rc == 0 else "unavailable"),
        "configuration_schema_digest": expected_manifest["configuration_schema_digest"],
        "contract_ref": "conformance:execution-environment-v1",
        "contract_digest": expected_manifest["conformance_contract_digest"],
        "run_ref": "conformance-run:hermes-local-release-20260825",
        "status": status,
        "check_results": checks,
        "version_ref": version_match.group(0) if version_match is not None else "unavailable",
        "backend_kind": backend if backend in {"local", "docker", "ssh", "singularity", "modal", "daytona"} else "unknown",
        "evidence_refs": ["evidence:hermes-version-readback", "evidence:terminal-backend-readback", "evidence:installed-environment-contract"],
        "contains_secrets": contains_secrets,
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
