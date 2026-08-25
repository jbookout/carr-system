#!/usr/bin/env python3
"""Hermetic regression checks for the Claude version sentinel latch."""
from __future__ import annotations

import os
import subprocess
import tempfile
import time
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "bin/cc-version-sentinel.sh"
FAILED: list[str] = []


def check(label: str, condition: bool) -> None:
    print(("  ok    " if condition else "  FAIL  ") + label)
    if not condition:
        FAILED.append(label)


def observed_cli() -> str:
    for candidate in (Path("/opt/homebrew/bin/claude"),
                      Path("/usr/local/bin/claude")):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            result = subprocess.run([str(candidate), "--version"],
                                    capture_output=True, text=True, check=False)
            value = result.stdout.split(maxsplit=1)[0] if result.stdout.split() else ""
            if value:
                return value
    return "none"


def run(home: Path, notifier: Path, notifier_calls: Path, log: Path) -> None:
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["CARR_CC_VERSION_NOTIFY_COMMAND"] = str(notifier)
    env["CARR_CC_VERSION_NOTIFY_CALLS"] = str(notifier_calls)
    env["CARR_CC_VERSION_SENTINEL_LOG"] = str(log)
    subprocess.run(["zsh", str(SCRIPT)], cwd=REPO, env=env, check=True,
                   capture_output=True, text=True)


def marker_field(marker: str, field: str) -> str:
    prefix = field + ": "
    return next((line[len(prefix):] for line in marker.splitlines()
                 if line.startswith(prefix)), "")


def notification_count(path: Path) -> int:
    if not path.exists():
        return 0
    return len(path.read_text(encoding="utf-8").splitlines())


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="cc-sentinel-") as tmp:
        home = Path(tmp)
        notifier_calls = home / "notifier-calls.log"
        notifier = home / "fake-osascript"
        notifier.write_text(
            "#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$CARR_CC_VERSION_NOTIFY_CALLS\"\n",
            encoding="utf-8",
        )
        notifier.chmod(0o755)
        sentinel_log = home / "cc-version-sentinel.log"
        task = home / ".claude/scheduled-tasks/cc-update-audit"
        task.mkdir(parents=True)
        app = home / "Library/Application Support/Claude/claude-code/2.0.0"
        app.mkdir(parents=True)
        cli = observed_cli()
        last = f"cli={cli} app=1.0.0"
        (task / "last-audited-version.txt").write_text(last + "\n", encoding="utf-8")

        run(home, notifier, notifier_calls, sentinel_log)
        pending = task / "pending-version.txt"
        first = pending.read_text(encoding="utf-8")
        first_detected_at = marker_field(first, "detected_at")
        check("first distinct version pair writes a pending marker",
              "current: cli=" + cli + " app=2.0.0" in first
              and "last_audited: " + last in first
              and bool(first_detected_at))
        check("first distinct pair sends exactly one notification",
              notification_count(notifier_calls) == 1)

        run(home, notifier, notifier_calls, sentinel_log)
        second = pending.read_text(encoding="utf-8")
        check("identical pending pair does not rewrite the marker", second == first)
        check("identical pending pair preserves detected_at",
              marker_field(second, "detected_at") == first_detected_at)
        check("identical pending pair sends no second notification",
              notification_count(notifier_calls) == 1)
        log = sentinel_log.read_text(encoding="utf-8")
        check("identical pending pair records a quiet latch decision",
              "already notified for this change" in log)

        app_new = home / "Library/Application Support/Claude/claude-code/3.0.0"
        app_new.mkdir()
        time.sleep(1.1)
        run(home, notifier, notifier_calls, sentinel_log)
        third = pending.read_text(encoding="utf-8")
        check("a new version pair replaces the marker",
              "current: cli=" + cli + " app=3.0.0" in third
              and third != second)
        check("a new version pair changes detected_at",
              marker_field(third, "detected_at") != first_detected_at)
        check("a new version pair sends exactly one more notification",
              notification_count(notifier_calls) == 2)

        log_lines = sentinel_log.read_text(encoding="utf-8").splitlines()
        check("isolated log records exactly three run outcomes",
              sum("cc-version-sentinel CHANGE" in line for line in log_lines) == 3
              and sum("marker written, Joe notified" in line for line in log_lines) == 2
              and sum("already notified for this change" in line for line in log_lines) == 1)

    print(f"Claude version sentinel selftest — {len(FAILED)} failure(s)")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
