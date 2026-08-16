#!/usr/bin/env python3
"""Verify a live Worker's /release JSON against one approved identity."""

from __future__ import annotations

import argparse
import json
import re
import sys
import uuid


def canonical_uuid(value: str) -> str | None:
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        return None
    canonical = str(parsed)
    return canonical if canonical == value.lower() else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--environment", required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--provider-version-id", required=True)
    args = parser.parse_args()

    expected_version = canonical_uuid(args.provider_version_id)
    if (expected_version is None
            or re.fullmatch(r"[0-9a-fA-F]{40}", args.sha) is None
            or not args.environment or not args.provider):
        print("verify-worker-release: malformed expected identity", file=sys.stderr)
        return 2

    try:
        raw_payload: object = json.load(sys.stdin)
    except (json.JSONDecodeError, UnicodeError):
        print("verify-worker-release: malformed /release JSON", file=sys.stderr)
        return 2
    if not isinstance(raw_payload, dict):
        print("verify-worker-release: malformed /release JSON object", file=sys.stderr)
        return 2
    payload: dict[str, object] = raw_payload

    env_value = payload.get("env")
    git_sha_value = payload.get("git_sha")
    worker_version_value = payload.get("worker_version")
    env: dict[str, object] = env_value if isinstance(env_value, dict) else {}
    git_sha: dict[str, object] = (
        git_sha_value if isinstance(git_sha_value, dict) else {})
    worker_version: dict[str, object] = (
        worker_version_value if isinstance(worker_version_value, dict) else {})
    observed_id = worker_version.get("id")
    observed_version = canonical_uuid(observed_id) if isinstance(observed_id, str) else None
    mismatches = []
    if payload.get("ok") is not True:
        mismatches.append("ok")
    if env.get("value") != args.environment:
        mismatches.append("env.value")
    if git_sha.get("value") != args.sha.lower():
        mismatches.append("git_sha.value")
    if payload.get("provider") != args.provider:
        mismatches.append("provider")
    if observed_version != expected_version:
        mismatches.append("worker_version.id")
    if mismatches:
        print("verify-worker-release: identity mismatch: " + ", ".join(mismatches),
              file=sys.stderr)
        return 1
    print("verify-worker-release: exact Production identity observed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
