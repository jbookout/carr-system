#!/usr/bin/env python3
"""Fail-closed eligibility check for ``bin/fleet-sync.sh``.

The canonical checkout deliberately carries the reproducible Quill patch after
``build-quill.sh``.  The health classifier proves that exact dirt byte-by-byte;
this small adapter lets fleet-sync use the same proof without turning the
submodule path into an ignore rule.
"""
from __future__ import annotations

import os
import subprocess
import sys

from health_submodule import classify_loose_status


QUILL = "tools/dictation-rig/vendor/quill"
QUILL_PATCHES = "tools/dictation-rig/patches"
SUBMODULE_CONFIG = ".gitmodules"


def _git(repo: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True,
                          text=True, check=False)


def eligible_for_fast_forward(repo: str, incoming: str) -> tuple[bool, str]:
    """Return whether a checkout may fast-forward without discarding work.

    The sole permitted tracked state is the exact Quill patch dirt classified by
    ``health_submodule``.  Even that state is safe only when the incoming range
    changes neither the recorded Quill gitlink, the patch source that explains
    the dirt, nor the repository's submodule configuration.  Every uncertainty
    is a refusal.
    """
    # Fence identity inputs even for a currently clean checkout.  A changed
    # gitlink, patch source, or .gitmodules entry needs a reviewed submodule
    # transition; fleet-sync cannot prove that a blind fast-forward preserves
    # the installed Quill build.
    changed = _git(repo, "diff", "--quiet", "HEAD", incoming, "--", QUILL,
                   QUILL_PATCHES, SUBMODULE_CONFIG)
    if changed.returncode == 1:
        return (False,
                "incoming update changes Quill gitlink, tracked patches, or .gitmodules")
    if changed.returncode != 0:
        return False, "could not compare incoming Quill identity inputs"

    status = _git(repo, "status", "--porcelain", "--untracked-files=no")
    if status.returncode:
        return False, "could not read tracked checkout status"
    rows = status.stdout.splitlines()
    if not rows:
        return True, "no tracked local changes"

    buckets = classify_loose_status(repo, rows)
    if buckets["actionable_tracked"]:
        return False, "actionable local changes: " + ", ".join(
            buckets["actionable_tracked"]) + "; expected patched submodules: " + ", ".join(
                buckets["expected_patched_submodules"])
    expected = buckets["expected_patched_submodules"]
    if expected != [QUILL]:
        return False, "unverifiable tracked submodule state: " + ", ".join(expected)

    return True, "only byte-proven expected Quill patch dirt"


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: fleet_sync_safety.py REPO INCOMING", file=sys.stderr)
        return 2
    allowed, reason = eligible_for_fast_forward(os.path.abspath(sys.argv[1]), sys.argv[2])
    print(reason)
    return 0 if allowed else 78


if __name__ == "__main__":
    raise SystemExit(main())
