#!/usr/bin/env python3
"""Hermetic proof that the version sentinel notifies once per exact pair."""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def run(script: Path, home: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["HOME"] = str(home)
    return subprocess.run(
        ["/bin/zsh", str(script), *args],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )


def check(label: str, condition: bool) -> None:
    print(("  ok  " if condition else "  FAIL  ") + label)
    if not condition:
        raise AssertionError(label)


with tempfile.TemporaryDirectory() as raw:
    base = Path(raw)
    repo = base / "repo"
    script = repo / "bin/cc-version-sentinel.sh"
    script.parent.mkdir(parents=True)
    shutil.copy2(ROOT / "bin/cc-version-sentinel.sh", script)

    home = base / "home"
    task = home / ".claude/scheduled-tasks/cc-update-audit"
    task.mkdir(parents=True)
    sentinel = task / "last-audited-version.txt"
    sentinel.write_text("cli=0 app=0\n", encoding="utf-8")
    (home / "Library/Application Support/Claude/claude-code/2.1.237").mkdir(parents=True)
    fake_cli = home / ".local/bin/claude"
    fake_cli.parent.mkdir(parents=True)
    fake_cli.write_text("#!/bin/sh\nprintf '2.1.236 (Claude Code)\\n'\n", encoding="utf-8")
    fake_cli.chmod(0o755)

    first = run(script, home, "--dry-run")
    prefix = "would notify: Claude Code changed — "
    check("a new pair reaches the notification path", first.returncode == 0 and first.stdout.startswith(prefix))
    current = first.stdout[len(prefix):].split(" (last audited:", 1)[0]

    pending = task / "pending-version.txt"
    original = f"current: {current}\nlast_audited: cli=0 app=0\ndetected_at: 2026-08-17T15:00:00Z\n"
    pending.write_text(original, encoding="utf-8")
    repeated = run(script, home)
    check("an already-notified pair exits successfully and silently", repeated.returncode == 0 and repeated.stdout == "" and repeated.stderr == "")
    check("the original pending marker and detected_at survive byte-for-byte", pending.read_text(encoding="utf-8") == original)
    log = (repo / "out/cc-version-sentinel.log").read_text(encoding="utf-8")
    check("the quiet repeat remains observable in the local log", "already notified for this version pair" in log and "2026-08-17T15:00:00Z" in log)

    sentinel.write_text("cli=0 app=changed\n", encoding="utf-8")
    changed = run(script, home, "--dry-run")
    check("a changed current/last pair is a new notification obligation", changed.returncode == 0 and changed.stdout.startswith(prefix))
    check("dry-run does not rewrite the prior pending evidence", pending.read_text(encoding="utf-8") == original)

print("PASS: cc-version-sentinel once-per-pair latch")
