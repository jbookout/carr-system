#!/usr/bin/env python3
"""Fail-closed eligibility check for ``bin/fleet-sync.sh``.

The canonical checkout deliberately carries the reproducible Quill patch after
``build-quill.sh``.  The health classifier proves that exact dirt byte-by-byte;
this small adapter lets fleet-sync use the same proof without turning the
submodule path into an ignore rule.
"""
from __future__ import annotations

import glob
import os
import subprocess
import sys
import tempfile

from health_submodule import classify_loose_status


QUILL = "tools/dictation-rig/vendor/quill"
QUILL_PATCHES = "tools/dictation-rig/patches"
SUBMODULE_CONFIG = ".gitmodules"


def _git(repo: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True,
                          text=True, check=False)


def _git_with_index(repo: str, index: str,
                    *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["GIT_INDEX_FILE"] = index
    return subprocess.run(["git", *args], cwd=repo, env=env,
                          capture_output=True, text=True, check=False)


def submodule_tree_is_exact_patch(repo: str) -> tuple[bool, str]:
    """Prove Quill's tracked worktree is exactly HEAD plus canonical patches.

    A temporary index preserves Git's own path, content, multiplicity, binary,
    and executable-mode semantics.  Updating that index with ``git add -u``
    intentionally excludes untracked scratch, matching fleet-sync's established
    contract.  The temporary directory is removed on every return and error.
    """
    tree_row = _git(repo, "ls-tree", "HEAD", "--", QUILL)
    if tree_row.returncode != 0 or not tree_row.stdout.strip():
        return False, "could not read the recorded Quill gitlink"
    try:
        metadata, recorded_path = tree_row.stdout.strip().split("\t", 1)
        mode, object_type, recorded_head = metadata.split()
    except ValueError:
        return False, "malformed recorded Quill gitlink"
    if (mode, object_type, recorded_path) != ("160000", "commit", QUILL):
        return False, "recorded Quill entry is not the expected submodule gitlink"

    quill = os.path.join(repo, QUILL)
    live_head = _git(quill, "rev-parse", "HEAD")
    if live_head.returncode != 0:
        return False, "could not read checked-out Quill HEAD"
    if live_head.stdout.strip() != recorded_head:
        return False, "checked-out Quill HEAD differs from the superproject gitlink"

    patch_dir = os.path.join(repo, QUILL_PATCHES)
    patches = sorted(glob.glob(os.path.join(patch_dir, "*.patch")))
    if not patches:
        return False, "no canonical Quill patches found"

    with tempfile.TemporaryDirectory(prefix="carr-fleet-sync-index-") as temp_dir:
        index = os.path.join(temp_dir, "index")
        read_tree = _git_with_index(quill, index, "read-tree", "HEAD")
        if read_tree.returncode != 0:
            return False, "could not seed temporary Quill index"
        for patch_path in patches:
            applied = _git_with_index(quill, index, "apply", "--cached", "--",
                                      patch_path)
            if applied.returncode != 0:
                return False, "canonical Quill patch set does not apply to recorded HEAD"
        expected = _git_with_index(quill, index, "write-tree")
        if expected.returncode != 0 or not expected.stdout.strip():
            return False, "could not materialize expected patched Quill tree"

        # Replace the temporary index's tracked entries with the live worktree.
        # -u is deliberate: untracked scratch is outside fleet-sync's dirt gate.
        live = _git_with_index(quill, index, "add", "-u", "--", ".")
        if live.returncode != 0:
            return False, "could not stage live tracked Quill tree for comparison"
        compared = _git_with_index(
            quill, index, "diff", "--cached", "--quiet", "--no-ext-diff",
            "--no-textconv", expected.stdout.strip(), "--")
        if compared.returncode == 0:
            return True, "tracked Quill tree exactly equals recorded HEAD plus canonical patches"
        if compared.returncode == 1:
            return False, "tracked Quill tree differs from recorded HEAD plus canonical patches"
        return False, "exact Quill tree comparison failed"


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

    exact, exact_reason = submodule_tree_is_exact_patch(repo)
    if not exact:
        return False, exact_reason
    return True, exact_reason


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: fleet_sync_safety.py REPO INCOMING", file=sys.stderr)
        return 2
    allowed, reason = eligible_for_fast_forward(os.path.abspath(sys.argv[1]), sys.argv[2])
    print(reason)
    return 0 if allowed else 78


if __name__ == "__main__":
    raise SystemExit(main())
