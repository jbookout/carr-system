"""
health_submodule.py — decide whether a vendored submodule's dirt is the tracked
patches doing their job, or a real uncommitted edit.

WHY THIS IS A MODULE AND NOT THREE LINES INSIDE health-check.py: it needs a
selftest (ops/submodule-patch-dirt-selftest.py), health-check.py is a script
with a hyphen in its name, and the case that matters — a hand-edit on top of the
patch still alarming — cannot be proven by reading.

THE RULE. `bin/build-quill.sh` applies tracked patches from a sibling
`patches/` directory onto a vendored third-party checkout at a detached HEAD,
so after any build that submodule is permanently modified. That is expected and
permanent. A hand-edit inside the same submodule is NOT expected and must still
be reported. The two are told apart by comparing the live diff's changed lines
against the union of the patches' changed lines:

    equal      -> expected; the build did it
    anything else -> loose work; keep it on the clock

Deliberately NOT an allowlist of paths. Silencing by path would hide the
hand-edit exactly as well as it hides the patch, which turns a noisy-but-honest
row into a quiet dishonest one.
"""

from __future__ import annotations

import glob
import os


def changed_lines(diff_text: str) -> set[tuple[str, str]]:
    """The set of (sign, content) pairs a diff actually changes.

    File headers, hunk headers and index lines are dropped: two diffs of the
    same change differ in blob hashes and line offsets, and comparing those
    would make every rebuild look like a new edit.
    """
    out: set[tuple[str, str]] = set()
    for line in diff_text.splitlines():
        if line.startswith(("+++", "---", "diff ", "index ", "@@", "new file",
                            "deleted file", "similarity ", "rename ", "old mode",
                            "new mode", "Binary files")):
            continue
        if line.startswith(("+", "-")) and len(line) > 1:
            out.add((line[0], line[1:].strip()))
    return out


def submodule_dirt_is_tracked_patch(diff_text: str, patches_dir: str) -> bool:
    """True only when the submodule's dirt is exactly what the patches apply.

    False when there is no dirt, no patches directory, no patches in it, or any
    changed line the patches do not account for — including a line that
    CONTRADICTS a patched one, which is what a hand-edit inside a patched hunk
    looks like.
    """
    dirt = changed_lines(diff_text)
    if not dirt:
        return False
    if not os.path.isdir(patches_dir):
        return False

    expected: set[tuple[str, str]] = set()
    found_any = False
    for path in sorted(glob.glob(os.path.join(patches_dir, "*.patch"))):
        found_any = True
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                expected |= changed_lines(fh.read())
        except OSError:
            return False        # cannot read a patch -> cannot vouch for the dirt
    if not found_any:
        return False

    return dirt == expected


def patches_dir_for(submodule_path: str) -> str:
    """Where a vendored submodule's patches live: a `patches/` directory beside
    the `vendor/` directory holding it. tools/dictation-rig/vendor/quill ->
    tools/dictation-rig/patches. Returns a path that may not exist; the caller
    treats a missing directory as "cannot explain this dirt".
    """
    parent = os.path.dirname(os.path.abspath(submodule_path))          # .../vendor
    return os.path.join(os.path.dirname(parent), "patches")            # .../patches
