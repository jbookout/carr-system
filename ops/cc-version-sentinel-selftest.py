#!/usr/bin/env python3
"""Hermetic regression checks for the Claude version sentinel latch."""
from __future__ import annotations

import os
import subprocess
import tempfile
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


def run(home: Path) -> None:
    env = os.environ.copy()
    env["HOME"] = str(home)
    subprocess.run(["zsh", str(SCRIPT)], cwd=REPO, env=env, check=True,
                   capture_output=True, text=True)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="cc-sentinel-") as tmp:
        home = Path(tmp)
        task = home / ".claude/scheduled-tasks/cc-update-audit"
        task.mkdir(parents=True)
        app = home / "Library/Application Support/Claude/claude-code/2.0.0"
        app.mkdir(parents=True)
        cli = observed_cli()
        last = f"cli={cli} app=1.0.0"
        (task / "last-audited-version.txt").write_text(last + "\n", encoding="utf-8")

        run(home)
        pending = task / "pending-version.txt"
        first = pending.read_text(encoding="utf-8")
        check("first distinct version pair writes a pending marker",
              "current: cli=" + cli + " app=2.0.0" in first
              and "last_audited: " + last in first)

        run(home)
        second = pending.read_text(encoding="utf-8")
        check("identical pending pair does not rewrite the marker", second == first)
        log = (REPO / "out/cc-version-sentinel.log").read_text(encoding="utf-8")
        check("identical pending pair records a quiet latch decision",
              "already notified for this change" in log)

        app_new = home / "Library/Application Support/Claude/claude-code/3.0.0"
        app_new.mkdir()
        run(home)
        third = pending.read_text(encoding="utf-8")
        check("a new version pair replaces the marker",
              "current: cli=" + cli + " app=3.0.0" in third
              and third != second)

    print(f"Claude version sentinel selftest — {len(FAILED)} failure(s)")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
