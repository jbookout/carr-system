#!/usr/bin/env python3
"""Fail-closed fast-forward for ONE fleet-sync sibling checkout.

bin/fleet-sync.sh keeps ~/carr-system's own checkout current through
tools/fleet_sync_safety.py's eligibility proof plus its own fetch/ff-only
logic. This module carries the SAME safety contract for the sibling repos
listed in ops/config/fleet-sync-siblings.json (doctorcre-app,
software-factory today) — repos that live as siblings of the carr-system
checkout, at ``${REPO:h}/<name>``.

Every case here is a DELIBERATE refusal, matching bin/fleet-sync.sh's own
design for the canonical checkout:

  - an ABSENT sibling is a clean skip, never a failure. Dell's Mac (and some
    Macs) simply won't have every sibling checked out.
  - a sibling NOT on its default branch is left alone.
  - TRACKED local changes (``git status --porcelain --untracked-files=no``)
    are never discarded; the sibling is skipped and the dirty paths named.
  - a DIVERGED sibling (HEAD not an ancestor of origin/<branch>) is a human
    question, exactly as for the canonical checkout.
  - otherwise: ``git fetch origin <branch>`` then ``git merge --ff-only
    origin/<branch>``. Never merge, rebase, reset, or force anything.

A sibling's own outcome is intentionally cheap to consume: this module
returns one of three statuses ("ok", "skip", "fail") and a one-line message,
so bin/fleet-sync.sh can report each sibling on its own line without letting
any of them affect its own exit code or block the canonical sync/re-render.
"""
from __future__ import annotations

import os
import subprocess
import sys
from typing import Tuple


def _git(repo: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True,
                          text=True, check=False)


def sync_sibling(path: str, name: str, branch: str = "main") -> Tuple[str, str]:
    """Fast-forward ``path`` (a sibling checkout) to origin/<branch>.

    Returns (status, message) with status in {"ok", "skip", "fail"}. Never
    raises for an ordinary absent/dirty/diverged/off-branch sibling — those
    are all "skip", by design.
    """
    if not os.path.isdir(path):
        return "skip", f"{name}: absent, skipping (not on this Mac)"
    if not os.path.isdir(os.path.join(path, ".git")):
        rev_parse = _git(path, "rev-parse", "--git-dir")
        if rev_parse.returncode != 0:
            return "skip", f"{name}: not a git checkout, skipping"

    rev = _git(path, "rev-parse", "--abbrev-ref", "HEAD")
    if rev.returncode != 0:
        return "skip", f"{name}: could not read current branch, skipping"
    branch_now = rev.stdout.strip()
    if branch_now != branch:
        return "skip", f"{name}: on '{branch_now}', not {branch}; leaving it alone"

    status = _git(path, "status", "--porcelain", "--untracked-files=no")
    if status.returncode != 0:
        return "fail", f"{name}: could not read tracked checkout status"
    dirty_rows = status.stdout.splitlines()
    if dirty_rows:
        dirty_paths = ", ".join(row[3:] for row in dirty_rows)
        return "skip", (f"{name}: local changes present, refusing to "
                        f"fast-forward over: {dirty_paths}")

    if not _git(path, "fetch", "--quiet", "origin", branch).returncode == 0:
        return "skip", (f"{name}: fetch of origin/{branch} failed "
                        "(offline, or no credential)")

    local_sha = _git(path, "rev-parse", "HEAD").stdout.strip()
    remote_sha = _git(path, "rev-parse", f"origin/{branch}").stdout.strip()
    if not local_sha or not remote_sha:
        return "fail", f"{name}: could not resolve local/remote HEAD"

    if local_sha == remote_sha:
        return "ok", f"{name}: already current at {local_sha[:8]}"

    ancestor = _git(path, "merge-base", "--is-ancestor", "HEAD",
                    f"origin/{branch}")
    if ancestor.returncode != 0:
        return "skip", (f"{name}: diverged from origin/{branch}; "
                        "a human decides this one")

    merged = _git(path, "merge", "--ff-only", f"origin/{branch}")
    if merged.returncode != 0:
        return "fail", f"{name}: fast-forward failed unexpectedly"
    return "ok", f"{name}: fast-forwarded {local_sha[:8]} -> {remote_sha[:8]}"


_EXIT = {"ok": 0, "skip": 78, "fail": 1}


def main(argv: list[str]) -> int:
    if len(argv) not in (3, 4):
        print("usage: fleet_sync_sibling_safety.py PATH NAME [BRANCH]",
              file=sys.stderr)
        return 2
    path, name = argv[1], argv[2]
    branch = argv[3] if len(argv) == 4 else "main"
    status, message = sync_sibling(path, name, branch)
    print(f"fleet-sync:   {message}")
    return _EXIT[status]


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
