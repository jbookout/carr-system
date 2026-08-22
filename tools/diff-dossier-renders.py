#!/usr/bin/env python3
"""TOMBSTONE -- diff-dossier-renders.py is retired. Phase 4 (Drive retirement), 2026-08-22.

WHAT IT WAS. ORDER 36 step 8's done-test. It rendered every dossier and diffed it against its frozen original.

WHY IT IS RETIRED. Nothing invoked it. Its comparison needed a frozen Drive-side original to diff against, and that side is going away with the vault.

WHY A TOMBSTONE RATHER THAN A DELETION. A tool that vanishes leaves the next
reader wondering whether the check it performed still happens somewhere. This
file says plainly that it does not, and why. The full implementation is in git
history at d574a443 and recovers with:

    git show d574a443:tools/diff-dossier-renders.py

IF A PARITY QUESTION COMES UP BEFORE THE SWITCH-OFF, recover it from that
commit and run it there rather than reviving this path. It carried a hardcoded
Drive root with no flag, no reason and no refusal, which is the ambient Drive
selection Phase 4 exists to remove; a revived copy would have to take the
explicit recovery boundary in lib/drive_recovery.py like every other vault-only
tool now does.
"""
import sys

sys.exit(
    "diff-dossier-renders.py: RETIRED 2026-08-22 (Phase 4, Drive retirement). It compared the record "
    "path against the legacy Drive path, and that second side is being retired. "
    "Read this file's docstring for what it proved and how to recover it from git."
)
