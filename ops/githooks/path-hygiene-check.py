#!/usr/bin/env python3
"""Reject newly staged paths that violate the file-shape rule (0e22e34a).

The check runs at the only point a bad repository path can still be refused
without rewriting history: pre-commit.  It examines additions, copies and
renames in the index, not the whole repository; established third-party trees
and historical filenames are not silently reclassified as a new violation.

The mechanical boundary is intentionally narrow and explicit:
  * no more than four directory components below the repository root;
  * no filename component using human draft/final-version suffixes such as
    ``_v2``, ``-v2``, ``_final`` or ``-final``. Dot-versioned machine
    contracts such as ``policy.v1.json`` are schema identifiers, not drafts.

``--paths`` exists only for the hermetic selftest; ordinary use has no
arguments and reads the staged index.
"""
from __future__ import annotations

import re
import subprocess
import sys

MAX_DIRECTORY_DEPTH = 4
BAD_VERSION_NAME = re.compile(r"(?:^|[_-])(?:final|v\d+)(?:$|[_.-])", re.I)


def violations(paths: list[str]) -> list[str]:
    bad = []
    for path in paths:
        path = path.strip().replace("\\", "/")
        if not path:
            continue
        parts = [part for part in path.split("/") if part and part != "."]
        if len(parts) < 1 or ".." in parts:
            bad.append(f"unsafe repository path: {path}")
            continue
        depth = len(parts) - 1
        if depth > MAX_DIRECTORY_DEPTH:
            bad.append(f"{path}: {depth} folder levels (maximum is {MAX_DIRECTORY_DEPTH})")
        if BAD_VERSION_NAME.search(parts[-1]):
            bad.append(f"{path}: draft/final version filename is forbidden")
    return bad


def staged_paths() -> list[str]:
    proc = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
        text=True, capture_output=True, check=True,
    )
    return proc.stdout.splitlines()


def main(argv: list[str]) -> int:
    try:
        paths = argv[1:] if len(argv) > 1 else staged_paths()
    except Exception as exc:  # accident-stopper must not wedge every commit
        print(f"path-hygiene-check: could not read staged paths ({exc}); allowing unchecked.",
              file=sys.stderr)
        return 0
    bad = violations(paths)
    if not bad:
        return 0
    print("\nCOMMIT REFUSED — path hygiene (rule 0e22e34a)\n", file=sys.stderr)
    for finding in bad:
        print(f"  - {finding}", file=sys.stderr)
    print("\nUse a descriptive final filename and keep new paths at four folder levels or less.",
          file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
