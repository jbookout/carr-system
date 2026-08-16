#!/usr/bin/env python3
"""Hermetic tests for the provider-native Claude scheduler reader."""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from lib.claude_scheduler_native import discover_snapshot, read_native_task
from lib.control_plane_scheduler_cutover import CutoverRefusal

FAILED: list[str] = []


def check(label: str, condition: bool) -> None:
    print(("  ok    " if condition else "  FAIL  ") + label)
    if not condition:
        FAILED.append(label)


def refuses(fn) -> bool:
    try:
        fn()
    except CutoverRefusal:
        return True
    return False


def main() -> int:
    locator = "cc-update-audit"
    portable = REPO / "ops/scheduled-tasks/cc-update-audit.SKILL.md"
    digest = hashlib.sha256(portable.read_bytes()).hexdigest()
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        vault = home / "My Drive" / "CARR AI"
        live = home / ".claude/scheduled-tasks" / locator / "SKILL.md"
        live.parent.mkdir(parents=True)
        live.write_text(portable.read_text(encoding="utf-8").replace("{{HOME}}", str(home))
                        .replace("{{REPO}}", str(REPO)).replace("{{VAULT}}", str(vault)), encoding="utf-8")
        snapshot = home / "Library/Application Support/Claude/claude-code-sessions/account/session/scheduled-tasks.json"
        snapshot.parent.mkdir(parents=True)
        task = {"id": locator, "cronExpression": "45 9 * * 1", "enabled": True,
                "filePath": str(live), "cwd": str(vault), "createdAt": 1,
                "lastRunAt": "2026-08-16T14:45:00Z", "lastScheduledFor": "2026-08-16T14:45:00Z"}
        snapshot.write_text(json.dumps({"scheduledTasks": [task]}), encoding="utf-8")
        observed = read_native_task(home=home, repo=REPO, locator=locator, expected_cron="45 9 * * 1",
                                    expected_timezone="America/Chicago", portable_definition_sha256=digest,
                                    observed_at=datetime(2026, 8, 16, 18, 0, tzinfo=timezone.utc),
                                    host_timezone="America/Chicago")
        check("native reader derives enabled state and provenance from Claude-owned snapshot",
              observed["enabled"] is True and observed["timezone"] == "America/Chicago"
              and len(observed["source_fingerprint"]) == 64
              and len(observed["provider_revision"]) == 64)
        check("native snapshot discovery requires one exact provider state file", discover_snapshot(home) == snapshot)
        bad = dict(task); bad["enabled"] = "false"
        snapshot.write_text(json.dumps({"scheduledTasks": [bad]}), encoding="utf-8")
        check("string/caller-like enabled state is refused", refuses(lambda: read_native_task(
            home=home, repo=REPO, locator=locator, expected_cron="45 9 * * 1",
            expected_timezone="America/Chicago", portable_definition_sha256=digest,
            host_timezone="America/Chicago")))
        snapshot.write_text(json.dumps({"scheduledTasks": [task]}), encoding="utf-8")
        live.write_text("drifted", encoding="utf-8")
        check("live task definition drift is refused", refuses(lambda: read_native_task(
            home=home, repo=REPO, locator=locator, expected_cron="45 9 * * 1",
            expected_timezone="America/Chicago", portable_definition_sha256=digest,
            host_timezone="America/Chicago")))
        live.write_text(portable.read_text(encoding="utf-8").replace("{{HOME}}", str(home))
                        .replace("{{REPO}}", str(REPO)).replace("{{VAULT}}", str(vault)), encoding="utf-8")
        check("host timezone drift is refused instead of assigning the expected zone",
              refuses(lambda: read_native_task(
                  home=home, repo=REPO, locator=locator, expected_cron="45 9 * * 1",
                  expected_timezone="America/Chicago", portable_definition_sha256=digest,
                  host_timezone="America/New_York")))
        second = snapshot.parent.parent / "other/scheduled-tasks.json"
        second.parent.mkdir(parents=True); second.write_text(snapshot.read_text(encoding="utf-8"), encoding="utf-8")
        check("ambiguous provider snapshot is refused", refuses(lambda: discover_snapshot(home)))
    print(f"Claude scheduler native selftest — {len(FAILED)} failure(s)")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
