#!/usr/bin/env python3
"""
submodule-patch-dirt-selftest.py — prove the health row can tell a vendored
submodule's EXPECTED dirt from a real uncommitted edit inside it.

WHY (Joe, 2026-08-19: "fix the health row so it stops reporting quill").
`tools/dictation-rig/vendor/quill` is a vendored checkout of the third-party
upstream github.com/digimata/quill, held at a detached HEAD.
`bin/build-quill.sh` applies our own tracked patches onto it at build time, so
after any build the submodule is permanently modified. health-check's
"uncommitted work" row counted that as loose work and had been reporting it for
283 hours — a tripwire nobody can act on, which this system has twice written
down as the thing people learn to switch off.

THE FIX IS NOT AN ALLOWLIST, and that distinction is the whole point of this
file. Hardcoding the quill path would silence a real hand-edit inside the
submodule just as effectively as it silences the patch. So the row compares the
submodule's live diff against the tracked patches that are supposed to explain
it:

  dirt == patches  -> expected, drop off the clock, report as a plain figure
  dirt != patches  -> somebody changed something the patches do not account
                      for, keep it on the clock and say so

The second case is what this selftest exists to defend. A check that only ever
proves the happy path is how a silencer gets mistaken for a fix.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tools"))

from health_submodule import changed_lines, submodule_dirt_is_tracked_patch  # noqa: E402

FAILURES: list[str] = []


def check(name: str, got, want) -> None:
    if got != want:
        FAILURES.append(f"{name}: got {got!r}, want {want!r}")
    else:
        print(f"  ok  {name}")


PATCH = """diff --git a/Sources/x.swift b/Sources/x.swift
index 111..222 100644
--- a/Sources/x.swift
+++ b/Sources/x.swift
@@ -1,3 +1,4 @@
 let a = 1
-let b = 2
+let b = 3
+let c = 4
 let d = 5
"""

DIFF_MATCHING = PATCH
DIFF_EXTRA = PATCH + """diff --git a/Sources/y.swift b/Sources/y.swift
index 333..444 100644
--- a/Sources/y.swift
+++ b/Sources/y.swift
@@ -1,2 +1,2 @@
-let e = 6
+let e = 7
"""
DIFF_DIFFERENT = """diff --git a/Sources/x.swift b/Sources/x.swift
index 111..222 100644
--- a/Sources/x.swift
+++ b/Sources/x.swift
@@ -1,3 +1,4 @@
 let a = 1
-let b = 2
+let b = 99
 let d = 5
"""
DIFF_TRAILING_SPACE = PATCH.replace("+let b = 3\n", "+let b = 3 \n")
DIFF_LEADING_SPACE = PATCH.replace("+let b = 3\n", "+ let b = 3\n")


def main() -> int:
    print("submodule patch-dirt selftest")

    # changed_lines ignores context, headers and index hashes — only +/- content
    check("changed_lines ignores diff headers",
          changed_lines(PATCH),
          {("+", "let b = 3"), ("+", "let c = 4"), ("-", "let b = 2")})

    check("changed_lines preserves leading and trailing whitespace",
          changed_lines("+ leading\n+trailing \n+\n-\n"),
          {("+", " leading"), ("+", "trailing "), ("+", ""), ("-", "")})

    check("empty diff yields nothing", changed_lines(""), set())

    # THE HAPPY PATH: dirt exactly equals the tracked patch
    with tempfile.TemporaryDirectory() as d:
        pdir = os.path.join(d, "patches")
        os.makedirs(pdir)
        with open(os.path.join(pdir, "0001-x.patch"), "w") as fh:
            fh.write(PATCH)
        check("dirt equal to the patch is EXPECTED",
              submodule_dirt_is_tracked_patch(DIFF_MATCHING, pdir), True)

        # THE CASE THIS FILE EXISTS FOR: a real edit on top of the patch
        check("dirt with an EXTRA edit is NOT expected",
              submodule_dirt_is_tracked_patch(DIFF_EXTRA, pdir), False)

        # A changed value inside the patched hunk must not pass either
        check("dirt that CONTRADICTS the patch is NOT expected",
              submodule_dirt_is_tracked_patch(DIFF_DIFFERENT, pdir), False)

        check("dirt with a TRAILING-SPACE edit is NOT expected",
              submodule_dirt_is_tracked_patch(DIFF_TRAILING_SPACE, pdir), False)

        check("dirt with a LEADING-SPACE edit is NOT expected",
              submodule_dirt_is_tracked_patch(DIFF_LEADING_SPACE, pdir), False)

        # No dirt at all is not "expected patch dirt" — it is simply clean, and
        # the caller never asks. Guard it anyway so the answer is never True by
        # accident on an empty read.
        check("empty dirt is not claimed as patch dirt",
              submodule_dirt_is_tracked_patch("", pdir), False)

    # NO PATCHES DIRECTORY: dirt can never be explained, so it stays loose work
    with tempfile.TemporaryDirectory() as d:
        check("dirt with no patches directory is NOT expected",
              submodule_dirt_is_tracked_patch(DIFF_MATCHING, os.path.join(d, "patches")), False)

    # THE REAL REPO, if the submodule is present and dirty. Not a fixture: this
    # is the case Joe actually asked about, and a selftest that only ever runs
    # on synthetic strings would not have caught a wrong path constant.
    sub = os.path.join(REPO, "tools", "dictation-rig", "vendor", "quill")
    pdir = os.path.join(REPO, "tools", "dictation-rig", "patches")
    if os.path.isdir(os.path.join(sub, ".git")) or os.path.exists(os.path.join(sub, ".git")):
        live = subprocess.run(["git", "diff"], cwd=sub, capture_output=True,
                              text=True, timeout=20).stdout
        if live.strip():
            check("LIVE quill dirt is explained by its tracked patches",
                  submodule_dirt_is_tracked_patch(live, pdir), True)
        else:
            print("  --  quill is clean right now; live case not exercised")
    else:
        print("  --  quill submodule not checked out; live case not exercised")

    if FAILURES:
        print("\nFAILED:")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("submodule patch-dirt selftest: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
