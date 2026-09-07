#!/usr/bin/env python3
"""Publish and observe the evidence contract for the cloud backup workflow.

The producer writes one ``Backup artifact`` Check bound to the repository,
workflow run, attempt and commit. A successful Check is possible only after the
artifact API readback matches the producer's ID and metadata. The observer is
read-only: it classifies terminal provider state for a separate caller and
never sends a notification itself.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


CHECK_NAME = "Backup artifact"
REQUIRED_STEPS = ["dump", "encrypt", "upload", "readback"]
SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
ARTIFACT_NAME_RE = re.compile(
    r"^[A-Za-z0-9_.-]+-run-([1-9][0-9]*)-attempt-([1-9][0-9]*)(?:\.sql\.age)?$"
)
CHECKS_PER_PAGE = 100
MAX_CHECK_PAGES = 5


class StatusError(RuntimeError):
    pass


@dataclass(frozen=True)
class Identity:
    repository: str
    run_id: int
    run_attempt: int
    head_sha: str

    @classmethod
    def environment(cls) -> "Identity":
        repository = required_env("GITHUB_REPOSITORY")
        run_id = positive_int(required_env("GITHUB_RUN_ID"), "GITHUB_RUN_ID")
        attempt = positive_int(required_env("GITHUB_RUN_ATTEMPT"), "GITHUB_RUN_ATTEMPT")
        head = required_env("GITHUB_SHA").lower()
        if not REPOSITORY_RE.fullmatch(repository):
            raise StatusError("GITHUB_REPOSITORY is not an owner/repository slug")
        if not re.fullmatch(r"[0-9a-f]{40}", head):
            raise StatusError("GITHUB_SHA must be a full 40-character commit SHA")
        return cls(repository, run_id, attempt, head)

    def value(self) -> dict[str, object]:
        return {
            "repository": self.repository,
            "run_id": self.run_id,
            "run_attempt": self.run_attempt,
            "head_sha": self.head_sha,
        }

    def external_id(self) -> str:
        return canonical(self.value())


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise StatusError(f"{name} is required")
    return value


def positive_int(raw: object, label: str) -> int:
    try:
        value = int(str(raw))
    except (TypeError, ValueError) as exc:
        raise StatusError(f"{label} must be an integer") from exc
    if value <= 0:
        raise StatusError(f"{label} must be positive")
    return value


def parse_time(raw: object, label: str) -> datetime:
    if not isinstance(raw, str) or not raw:
        raise StatusError(f"{label} is missing")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise StatusError(f"{label} is not RFC3339") from exc
    if parsed.tzinfo is None:
        raise StatusError(f"{label} has no timezone")
    return parsed.astimezone(timezone.utc)


def artifact_name_matches(name: object, identity: Identity) -> bool:
    match = ARTIFACT_NAME_RE.fullmatch(str(name))
    return bool(
        match
        and int(match.group(1)) == identity.run_id
        and int(match.group(2)) == identity.run_attempt
    )


def api(
    path: str,
    *,
    method: str = "GET",
    body: dict[str, object] | None = None,
    query: dict[str, object] | None = None,
) -> Any:
    if not shutil.which("gh"):
        raise StatusError("gh is not installed")
    command = ["gh", "api", path]
    encoded: str | None = None
    if method != "GET" or body is not None or query:
        command.extend(["--method", method])
    for key, value in (query or {}).items():
        command.extend(["-f", f"{key}={value}"])
    if body is not None:
        command.extend(["--input", "-"])
        encoded = canonical(body)
    try:
        result = subprocess.run(
            command,
            input=encoded,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise StatusError(f"GitHub API call failed: {type(exc).__name__}") from exc
    if result.returncode:
        detail = (result.stderr or result.stdout or "").strip().splitlines()
        raise StatusError(detail[-1] if detail else f"gh exited {result.returncode}")
    if not result.stdout.strip():
        return {}
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise StatusError("GitHub API returned non-JSON output") from exc


def checks(identity: Identity) -> list[dict[str, Any]]:
    path = f"/repos/{identity.repository}/commits/{identity.head_sha}/check-runs"
    gathered: list[dict[str, Any]] = []
    seen: set[int] = set()
    for page in range(1, MAX_CHECK_PAGES + 1):
        response = api(
            path,
            query={"filter": "all", "per_page": CHECKS_PER_PAGE, "page": page},
        )
        rows = response.get("check_runs") if isinstance(response, dict) else None
        total = response.get("total_count") if isinstance(response, dict) else None
        if not isinstance(rows, list):
            raise StatusError("Check API response has no check_runs array")
        for row in rows:
            if not isinstance(row, dict):
                raise StatusError("Check API response contains a non-object")
            row_id = positive_int(row.get("id"), "check ID")
            if row_id not in seen:
                seen.add(row_id)
                gathered.append(row)
        if isinstance(total, int) and len(gathered) >= total:
            return gathered
        if len(rows) < CHECKS_PER_PAGE:
            return gathered
    raise StatusError("Check pagination cap exhausted")


def check_identity(item: dict[str, Any]) -> dict[str, object] | None:
    raw = item.get("external_id")
    if not isinstance(raw, str):
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def matching_checks(identity: Identity) -> list[dict[str, Any]]:
    expected = identity.value()
    return [
        item for item in checks(identity)
        if item.get("name") == CHECK_NAME
        and str(item.get("head_sha", "")).lower() == identity.head_sha
        and check_identity(item) == expected
    ]


def check_body(
    identity: Identity,
    *,
    status: str,
    conclusion: str | None = None,
    summary: dict[str, object] | None = None,
) -> dict[str, object]:
    body: dict[str, object] = {
        "name": CHECK_NAME,
        "head_sha": identity.head_sha,
        "external_id": identity.external_id(),
        "status": status,
        "output": {
            "title": CHECK_NAME,
            "summary": canonical(summary or {}),
        },
    }
    if conclusion is not None:
        body["conclusion"] = conclusion
        body["completed_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return body


def create_check(identity: Identity, body: dict[str, object]) -> dict[str, Any]:
    result = api(f"/repos/{identity.repository}/check-runs", method="POST", body=body)
    if not isinstance(result, dict) or not result.get("id"):
        raise StatusError("Check creation returned no ID")
    return result


def update_check(identity: Identity, check_id: int, body: dict[str, object]) -> dict[str, Any]:
    result = api(
        f"/repos/{identity.repository}/check-runs/{check_id}",
        method="PATCH",
        body=body,
    )
    if not isinstance(result, dict):
        raise StatusError("Check update returned no object")
    return result


def require_one_check(identity: Identity) -> dict[str, Any]:
    found = matching_checks(identity)
    if len(found) != 1:
        raise StatusError(f"expected one exact {CHECK_NAME} Check, found {len(found)}")
    return found[0]


def summary_of(item: dict[str, Any]) -> dict[str, object] | None:
    output = item.get("output")
    raw = output.get("summary") if isinstance(output, dict) else None
    if not isinstance(raw, str):
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def require_readback(
    identity: Identity,
    *,
    status: str,
    conclusion: str | None,
    summary: dict[str, object],
) -> dict[str, Any]:
    item = require_one_check(identity)
    if item.get("status") != status:
        raise StatusError("Check readback status differs")
    if conclusion is not None and item.get("conclusion") != conclusion:
        raise StatusError("Check readback conclusion differs")
    if summary_of(item) != summary:
        raise StatusError("Check readback summary differs")
    return item


def producer_start(identity: Identity) -> int:
    if matching_checks(identity):
        raise StatusError("an exact Check already exists for this workflow attempt")
    summary: dict[str, object] = {"state": "running"}
    create_check(identity, check_body(identity, status="in_progress", summary=summary))
    require_readback(identity, status="in_progress", conclusion=None, summary=summary)
    return 0


def artifact_readback(identity: Identity, args: argparse.Namespace) -> dict[str, object]:
    run_readback(identity)
    artifact_id = positive_int(args.artifact_id, "artifact_id")
    item = api(f"/repos/{identity.repository}/actions/artifacts/{artifact_id}")
    if not isinstance(item, dict):
        raise StatusError("artifact API returned no object")
    workflow = item.get("workflow_run")
    if not isinstance(workflow, dict):
        raise StatusError("artifact has no workflow_run provenance")
    expected_name = str(args.artifact_name)
    expected_digest = str(args.artifact_digest).lower()
    if re.fullmatch(r"[0-9a-f]{64}", expected_digest):
        expected_digest = "sha256:" + expected_digest
    if not SHA256_RE.fullmatch(expected_digest):
        raise StatusError("artifact_digest is not a sha256 digest")
    actual_bytes = positive_int(item.get("size_in_bytes"), "provider artifact size")
    created = str(item.get("created_at", ""))
    expires = str(item.get("expires_at", ""))
    parse_time(created, "artifact.created_at")
    if parse_time(expires, "artifact.expires_at") <= datetime.now(timezone.utc):
        raise StatusError("artifact is expired")
    if item.get("expired") is not False:
        raise StatusError("provider marks artifact expired")
    artifact_attempt = workflow.get("run_attempt")
    required = {
        "artifact ID": item.get("id") == artifact_id,
        "artifact name": item.get("name") == expected_name,
        "artifact digest": item.get("digest") == expected_digest,
        "workflow run": positive_int(workflow.get("id"), "artifact workflow run") == identity.run_id,
        "workflow head": str(workflow.get("head_sha", "")).lower() == identity.head_sha,
        "run/attempt-bound name": artifact_name_matches(expected_name, identity),
    }
    if artifact_attempt is not None:
        required["workflow attempt"] = (
            positive_int(artifact_attempt, "artifact workflow attempt") == identity.run_attempt
        )
    if args.artifact_bytes is not None:
        required["artifact bytes"] = actual_bytes == positive_int(args.artifact_bytes, "artifact_bytes")
    if args.artifact_created_at is not None:
        required["artifact creation"] = created == args.artifact_created_at
    if args.artifact_expires_at is not None:
        required["artifact expiry"] = expires == args.artifact_expires_at
    failed = [label for label, ok in required.items() if not ok]
    if failed:
        raise StatusError("artifact readback mismatch: " + ", ".join(failed))
    return {
        "artifact_id": artifact_id,
        "artifact_name": expected_name,
        "artifact_digest": expected_digest,
        "artifact_bytes": actual_bytes,
        "artifact_created_at": created,
        "artifact_expires_at": expires,
        "required_steps": REQUIRED_STEPS,
    }


def producer_complete(identity: Identity, args: argparse.Namespace) -> int:
    current = require_one_check(identity)
    if current.get("status") != "in_progress":
        raise StatusError("completion requires the attempt's in-progress Check")
    summary = artifact_readback(identity, args)
    update_check(
        identity,
        positive_int(current.get("id"), "check ID"),
        check_body(identity, status="completed", conclusion="success", summary=summary),
    )
    require_readback(identity, status="completed", conclusion="success", summary=summary)
    return 0


def failure_summary(reason: str) -> dict[str, object]:
    return {"signal": "backup-failure", "reason": reason}


def producer_fail(identity: Identity, reason: str) -> int:
    summary = failure_summary(reason)
    found = matching_checks(identity)
    if len(found) > 1:
        raise StatusError("multiple exact Checks make failure attribution ambiguous")
    body = check_body(identity, status="completed", conclusion="failure", summary=summary)
    if found:
        if found[0].get("conclusion") == "success":
            raise StatusError("refusing to replace an artifact-backed success Check")
        update_check(identity, positive_int(found[0].get("id"), "check ID"), body)
    else:
        create_check(identity, body)
    require_readback(identity, status="completed", conclusion="failure", summary=summary)
    return 0


def run_artifacts(identity: Identity) -> list[dict[str, Any]]:
    response = api(f"/repos/{identity.repository}/actions/runs/{identity.run_id}/artifacts")
    rows = response.get("artifacts") if isinstance(response, dict) else None
    if not isinstance(rows, list):
        raise StatusError("artifact listing has no artifacts array")
    total = response.get("total_count") if isinstance(response, dict) else None
    if isinstance(total, int) and total > len(rows):
        raise StatusError("artifact listing was truncated")
    return [row for row in rows if isinstance(row, dict)]


def summary_matches_artifact(
    identity: Identity,
    summary: dict[str, object] | None,
    artifacts: list[dict[str, Any]],
) -> bool:
    if not isinstance(summary, dict) or summary.get("required_steps") != REQUIRED_STEPS:
        return False
    try:
        artifact_id = positive_int(summary.get("artifact_id"), "artifact ID")
        expected_bytes = positive_int(summary.get("artifact_bytes"), "artifact bytes")
    except StatusError:
        return False
    selected = [item for item in artifacts if item.get("id") == artifact_id]
    if len(selected) != 1:
        return False
    item = selected[0]
    workflow = item.get("workflow_run")
    if not isinstance(workflow, dict):
        return False
    try:
        created = parse_time(item.get("created_at"), "artifact.created_at")
        expires = parse_time(item.get("expires_at"), "artifact.expires_at")
        actual_bytes = positive_int(item.get("size_in_bytes"), "artifact bytes")
        run_id = positive_int(workflow.get("id"), "artifact run ID")
        attempt = workflow.get("run_attempt")
        if attempt is not None and positive_int(attempt, "artifact run attempt") != identity.run_attempt:
            return False
    except StatusError:
        return False
    return all((
        item.get("expired") is False,
        expires > datetime.now(timezone.utc),
        created <= datetime.now(timezone.utc),
        item.get("name") == summary.get("artifact_name"),
        artifact_name_matches(item.get("name"), identity),
        item.get("digest") == summary.get("artifact_digest"),
        SHA256_RE.fullmatch(str(item.get("digest", ""))) is not None,
        actual_bytes == expected_bytes,
        item.get("created_at") == summary.get("artifact_created_at"),
        item.get("expires_at") == summary.get("artifact_expires_at"),
        run_id == identity.run_id,
        str(workflow.get("head_sha", "")).lower() == identity.head_sha,
    ))


def neutral_cancel(identity: Identity) -> int:
    if matching_checks(identity):
        raise StatusError("an exact Check already exists for this workflow attempt")
    summary: dict[str, object] = {"state": "disabled", "signal": None}
    create_check(
        identity,
        check_body(identity, status="completed", conclusion="neutral", summary=summary),
    )
    require_readback(identity, status="completed", conclusion="neutral", summary=summary)
    if run_artifacts(identity):
        raise StatusError("disabled workflow attempt already has artifacts")
    trusted_run_id = required_env("GITHUB_RUN_ID")
    if positive_int(trusted_run_id, "GITHUB_RUN_ID") != identity.run_id:
        raise StatusError("trusted run ID changed during neutral cancellation")
    cancel_path = f"/repos/{identity.repository}/actions/runs/{required_env('GITHUB_RUN_ID')}/cancel"
    api(cancel_path, method="POST")
    while True:
        time.sleep(60)


def run_readback(identity: Identity) -> dict[str, Any]:
    result = api(
        f"/repos/{identity.repository}/actions/runs/{identity.run_id}"
        f"/attempts/{identity.run_attempt}"
    )
    if not isinstance(result, dict):
        raise StatusError("workflow run API returned no object")
    run_id = result.get("id", result.get("databaseId"))
    attempt = result.get("run_attempt")
    head = result.get("head_sha", result.get("headSha", ""))
    if (
        positive_int(run_id, "provider run ID") != identity.run_id
        or positive_int(attempt, "provider run attempt") != identity.run_attempt
        or str(head).lower() != identity.head_sha
    ):
        raise StatusError("workflow run provenance mismatch")
    return result


def emit(state: str, signal: str | None, detail: str) -> int:
    print(canonical({"state": state, "signal": signal, "detail": detail}))
    return {"disabled": 0, "success": 0, "failure": 1, "unknown": 2}[state]


def observer(identity: Identity) -> int:
    """Read terminal provider evidence; a separate caller decides delivery."""
    try:
        run = run_readback(identity)
        found = matching_checks(identity)
        artifacts = run_artifacts(identity)
    except StatusError as exc:
        return emit("unknown", None, str(exc))
    if run.get("status") != "completed" or len(found) != 1:
        return emit("unknown", None, "terminal run has no unique exact Check")
    item = found[0]
    conclusion = item.get("conclusion")
    summary = summary_of(item)
    if conclusion == "failure":
        if isinstance(summary, dict) and summary.get("signal") == "backup-failure":
            return emit("failure", "backup-failure", str(summary.get("reason", "backup failed")))
        return emit("unknown", None, "failure Check has no exact backup-failure signal")
    if conclusion == "success":
        if summary_matches_artifact(identity, summary, artifacts):
            return emit("success", None, "artifact-backed producer Check succeeded")
        return emit("unknown", None, "success Check artifact metadata mismatch")
    if run.get("conclusion") == "cancelled" and conclusion == "neutral" and not artifacts:
        if summary in ({}, {"state": "disabled", "signal": None}):
            return emit("disabled", None, "cancelled neutral run with zero artifacts")
        return emit("unknown", None, "neutral Check summary mismatch")
    return emit("unknown", None, "terminal workflow and Check conclusions do not agree")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    sub = root.add_subparsers(dest="command", required=True)
    sub.add_parser("producer-start")
    complete = sub.add_parser("producer-complete")
    complete.add_argument("--artifact-id", required=True)
    complete.add_argument("--artifact-name", required=True)
    complete.add_argument("--artifact-digest", required=True)
    complete.add_argument("--artifact-bytes")
    complete.add_argument("--artifact-created-at")
    complete.add_argument("--artifact-expires-at")
    failed = sub.add_parser("producer-fail")
    failed.add_argument("--reason", required=True)
    sub.add_parser("neutral-cancel")
    sub.add_parser("observe")
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        identity = Identity.environment()
        if args.command == "producer-start":
            return producer_start(identity)
        if args.command == "producer-complete":
            return producer_complete(identity, args)
        if args.command == "producer-fail":
            return producer_fail(identity, args.reason)
        if args.command == "neutral-cancel":
            return neutral_cancel(identity)
        if args.command == "observe":
            return observer(identity)
        raise StatusError("unknown command")
    except StatusError as exc:
        print(f"backup-workflow-status: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
