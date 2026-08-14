#!/usr/bin/env python3
"""Static security contract for the thin GitHub Actions CI adapter."""

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


def main():
    text = WORKFLOW.read_text(encoding="utf-8")
    failures = []

    uses = re.findall(r"^\s*- uses:\s*([^\s#]+)", text, re.MULTILINE)
    if not uses or any(not re.search(r"@[0-9a-f]{40}$", item) for item in uses):
        failures.append("every action must be pinned to a full commit SHA")
    if re.search(r"^\s*(pull_request_target|workflow_run)\s*:", text, re.MULTILINE):
        failures.append("privileged trigger pull_request_target/workflow_run is forbidden")
    if not re.search(r"^permissions:\s*\n\s+contents:\s*read\s*$", text, re.MULTILINE):
        failures.append("top-level permissions must remain contents: read")
    if not re.search(r"^\s+timeout-minutes:\s*\d+\s*$", text, re.MULTILINE):
        failures.append("every CI job needs a timeout")
    if not re.search(r"uses:\s*actions/checkout@[0-9a-f]{40}[\s\S]{0,240}?fetch-depth:\s*0", text):
        failures.append("checkout needs full history for trusted semantic diff ancestry")
    if "CARR_CI_BASE_SHA: ${{ github.event.pull_request.base.sha || github.event.before }}" not in text:
        failures.append("strict CI needs the exact PR/push base SHA")
    if len(re.findall(r"run:\s*ops/ci\.sh --strict", text)) != 1:
        failures.append("the workflow must invoke the one strict check script exactly once")

    if failures:
        for failure in failures:
            print(f"workflow security: REFUSED — {failure}")
        return 1
    print(f"workflow security: ACCOUNTED — {len(uses)} pinned actions, read-only permission, trusted base, bounded job")
    return 0


if __name__ == "__main__":
    sys.exit(main())
