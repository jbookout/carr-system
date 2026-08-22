#!/usr/bin/env python3
"""TOMBSTONE -- parity-lead-board.py is retired. Phase 4 (Drive retirement), 2026-08-22.

WHAT IT WAS. ORDER 26(c)'s done-test. It built the Lead Board twice on one day's data -- once from the generated files, once from the record layer -- and diffed the payload the template is driven by, never the HTML.

WHY IT IS RETIRED. Nothing invoked it. It compared the record path against the FILE path, and the file path is what Phase 4 retires, so the comparison loses one of its two sides.

WHY A TOMBSTONE RATHER THAN A DELETION. A tool that vanishes leaves the next
reader wondering whether the check it performed still happens somewhere. This
file says plainly that it does not, and why. The full implementation is in git
history at d574a443 and recovers with:

    git show d574a443:tools/parity-lead-board.py

IF A PARITY QUESTION COMES UP BEFORE THE SWITCH-OFF, recover it from that
commit and run it there rather than reviving this path. It carried a hardcoded
Drive root with no flag, no reason and no refusal, which is the ambient Drive
selection Phase 4 exists to remove; a revived copy would have to take the
explicit recovery boundary in lib/drive_recovery.py like every other vault-only
tool now does.
"""
import sys

sys.exit(
    "parity-lead-board.py: RETIRED 2026-08-22 (Phase 4, Drive retirement). It compared the record "
    "path against the legacy Drive path, and that second side is being retired. "
    "Read this file's docstring for what it proved and how to recover it from git."
)
