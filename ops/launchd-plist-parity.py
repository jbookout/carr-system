#!/usr/bin/env python3
"""Every loaded CARR job must be backed by a plist this repository can reproduce.

WHY THIS EXISTS. On 2026-08-13 a scheduled job, com.carr.quill-key-monitor, was
found running with NO PLIST ANYWHERE. It had been registered by a one-off
`launchctl submit`, ran a binary from /private/tmp, and had restarted about 2000
times — a 120-second diagnostic trapped in an unconditional respawn loop, logging
to a file nobody read.

The Phase 0 release-truth inventory called it the worst of eight gaps, and the
reason is the part worth keeping: submit-registered jobs exist ENTIRELY OUTSIDE the
plist and repository surface an audit inspects. A clean plist listing and a clean
repo grep BOTH reported all-clear while it ran unaccounted for. It was found only
because someone ran `launchctl print` by hand during a manual inventory.

That is the hole this closes. Release truth means the repository can reproduce what
is running; a job with no plist cannot be reproduced, will not survive a reboot, and
no audit of files will ever mention it.

WHAT IT CHECKS, in both directions, because each direction catches a different fault:

  LOADED BUT UNBACKED  a label is live in launchd with no plist in the repo and none
                       in any of the three launch directories. This is the quill-key
                       -monitor case: invisible to config-as-code, gone on reboot.

  BACKED BUT NOT LOADED  a plist exists in the repo and its label is not running.
                       Usually benign — an installer that has not been run on this
                       machine — but it is exactly how a job silently stops without
                       anyone noticing, so it is reported rather than swallowed.

Exit 1 on a loaded-but-unbacked job. Exit 0, with the other direction reported as
information, otherwise. Read-only: it never loads, unloads or edits anything.

Run: python3 ops/launchd-plist-parity.py [--quiet]
"""

import os
import plistlib
import re
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PREFIX = "com.carr."

# The three places launchd reads from on this machine, plus the repo itself. A job
# is "backed" if a plist declaring its label exists in ANY of them — the repo copy
# is what makes it reproducible, the installed copy is what makes it run.
PLIST_DIRS = [
    os.path.join(REPO, "ops", "launchd"),
    os.path.join(REPO, "tools", "dictation-rig", "launchd"),
    os.path.expanduser("~/Library/LaunchAgents"),
    "/Library/LaunchAgents",
    "/Library/LaunchDaemons",
]


def loaded_labels():
    """Labels launchd currently knows, restricted to ours."""
    out = subprocess.run(["launchctl", "list"], capture_output=True, text=True).stdout
    labels = set()
    for line in out.splitlines()[1:]:
        parts = line.split("\t")
        if len(parts) >= 3 and parts[2].startswith(PREFIX):
            labels.add(parts[2].strip())
    return labels


def backed_labels():
    """Labels declared by a plist anywhere launchd or the repo would look.

    Reads the plist's OWN Label key rather than trusting the filename, because a
    file named one thing and declaring another is precisely the drift this is for.
    """
    found = {}
    for d in PLIST_DIRS:
        if not os.path.isdir(d):
            continue
        for name in os.listdir(d):
            if not name.endswith(".plist"):
                continue
            path = os.path.join(d, name)
            label = None
            try:
                with open(path, "rb") as fh:
                    label = (plistlib.load(fh) or {}).get("Label")
            except Exception:
                # An unparseable plist is itself worth surfacing rather than skipping
                # silently, so fall back to the filename and let the mismatch show.
                m = re.match(r"(com\.carr\.[^/]+)\.plist$", name)
                label = m.group(1) if m else None
            if label and label.startswith(PREFIX):
                found.setdefault(label, []).append(path)
    return found


def main():
    quiet = "--quiet" in sys.argv
    loaded = loaded_labels()
    backed = backed_labels()

    unbacked = sorted(loaded - set(backed))
    not_loaded = sorted(set(backed) - loaded)

    if unbacked:
        print("FAIL  loaded with NO plist backing them — release truth cannot see these:",
              file=sys.stderr)
        for label in unbacked:
            prog = subprocess.run(
                ["launchctl", "print", f"gui/{os.getuid()}/{label}"],
                capture_output=True, text=True).stdout
            m = re.search(r"program = (.+)", prog)
            runs = re.search(r"runs = (\d+)", prog)
            detail = []
            if m:
                detail.append(f"program {m.group(1).strip()}")
            if runs:
                detail.append(f"{runs.group(1)} runs")
            print(f"  {label}" + (f"  ({', '.join(detail)})" if detail else ""),
                  file=sys.stderr)
        print("\nA submit-registered job is invisible to a plist listing and to a repo\n"
              "grep, does not survive a reboot, and cannot be reproduced from the\n"
              "repository. Give it a plist under ops/launchd/ or tear it down.",
              file=sys.stderr)
        return 1

    if not quiet:
        print(f"ok  launchd parity: {len(loaded)} loaded CARR job(s), every one backed by a plist")
        if not_loaded:
            print(f"    note: {len(not_loaded)} plist(s) declare a label that is not loaded here "
                  f"— usually an installer not yet run, but worth knowing:")
            for label in not_loaded:
                print(f"      {label}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
