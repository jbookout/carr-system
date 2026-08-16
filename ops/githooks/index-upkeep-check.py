#!/usr/bin/env python3
"""Require the nearest category INDEX.md beside staged new content (99e951b9).

Only categories that already declare an ``INDEX.md`` participate.  That gives
the rule an executable, local meaning without inventing indexes for source-code
directories.  When a new file is added below such a category, the same commit
must include that category's INDEX.md.  Existing files may be edited without
churn, and a category's own INDEX may of course be added first.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def staged_added() -> list[str]:
    proc = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=A"],
        text=True, capture_output=True, check=True,
    )
    return [p for p in proc.stdout.splitlines() if p]


def staged_all() -> set[str]:
    proc = subprocess.run(["git", "diff", "--cached", "--name-only"],
                          text=True, capture_output=True, check=True)
    return set(p for p in proc.stdout.splitlines() if p)


def nearest_index(path: str) -> str | None:
    parent = Path(path).parent
    while str(parent) not in ("", "."):
        candidate = parent / "INDEX.md"
        if candidate.is_file():
            return candidate.as_posix()
        parent = parent.parent
    return None


def violations(added: list[str], staged: set[str]) -> list[str]:
    missing = []
    for path in added:
        if path.endswith("/INDEX.md") or path == "INDEX.md":
            continue
        index = nearest_index(path)
        if index and index not in staged:
            missing.append(f"{path}: its category index {index} is not staged")
    return missing


def main() -> int:
    try:
        added, staged = staged_added(), staged_all()
    except Exception as exc:  # local convenience must fail open on broken git
        print(f"index-upkeep-check: could not read staged paths ({exc}); allowing unchecked.",
              file=sys.stderr)
        return 0
    bad = violations(added, staged)
    if not bad:
        return 0
    print("\nCOMMIT REFUSED — category index not updated (rule 99e951b9)\n", file=sys.stderr)
    for finding in bad:
        print(f"  - {finding}", file=sys.stderr)
    print("\nStage the nearest INDEX.md with the new category content.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
