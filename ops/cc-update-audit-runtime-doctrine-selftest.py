#!/usr/bin/env python3
"""Prevent the Claude update audit from assigning native tasks to the CLI."""
from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
skill = " ".join((ROOT / "ops/scheduled-tasks/cc-update-audit.SKILL.md").read_text(encoding="utf-8").split())

required = {
    "native scheduler source is Claude-owned desktop provider state": "Claude Code's native scheduled tasks are provider state owned by the desktop app",
    "native scheduled tasks are explicitly separated from PATH CLI execution": "they do not invoke the PATH CLI",
    "runtime truth is refreshed rather than fossilized as a task count": "never a dated task count",
    "both current explicit PATH consumer families are named": "doc-convo and room-bridge desks",
    "app-version blast radius includes native scheduled tasks": "app=` affects Joe's interactive sessions and Claude-owned native scheduled tasks",
}

failed: list[str] = []
for label, phrase in required.items():
    ok = phrase in skill
    print(("  ok  " if ok else "  FAIL  ") + label)
    if not ok:
        failed.append(label)

stale = "16 of them, several headless"
ok = stale not in skill
print(("  ok  " if ok else "  FAIL  ") + "stale headless-task count is removed")
if not ok:
    failed.append("stale headless-task count is removed")

if failed:
    raise SystemExit("FAIL: " + ", ".join(failed))
print("PASS: cc-update-audit runtime doctrine")
