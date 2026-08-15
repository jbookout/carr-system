#!/usr/bin/env python3
"""ops/glanceable-lead-check.py — no lead-outreach reminders on a glanceable surface.

THE RULE (17ffd587, Joe, Jul 15/16 2026): never surface "call this lead / follow
up" on open-loops, the heartbeat, or the Monday brief. Leads live on The Lead
Board and Joe works them there. Its carve-out, in the rule's own words: a
follow-up DATE Joe sets on a real DEAL is the reminder system working — surface
it at the right time.

WHY IT NEEDS A CHECK RATHER THAN A HABIT. The surfaces this rule governs are
GENERATED. Nothing stopped a lead-outreach row being written into a loop and
rendering onto Joe's hot list the next morning; the rule held because sessions
remembered it, which is the exact shape rule ab814a26 was taught about.

WHAT IS A VIOLATION, and the narrowness is the whole design: a lead reference
sitting inside an OUTREACH INSTRUCTION. "Send Dr. X (L-221) the advisory" is the
shape the rule forbids.

WHAT IS NOT, each one a real line from the live surfaces or a near neighbour:
  · bookkeeping — "Registry L-004 stamp owed to the next writer" mentions a lead
    ref while doing filing, and flagging it would make the check cry about the
    system's own record-keeping until somebody muted it.
  · the CARVE-OUT — a dated follow-up on a deal (C-###) is the reminder system
    working. This check never looks at deal refs.
  · provenance — "the advisory came out of the L-221 intake" names a source.

THE HOT LIST IS GLANCEABLE; THE BACKLOG IS NOT. open-loops.md is always-read.
open-loops-backlog.md states in its own header that it is read on cadence rather
than every session, which is where a dormant lead item is allowed to live. The
brief sections under out/brief-pack/ are glanceable and are checked.

IT REPORTS; IT DOES NOT EDIT. The remedy for a real hit is to move the row to
The Lead Board, which is a judgement about that lead's state and belongs to a
session or to Joe, not to a regex.

RUN IT:
    python3 ops/glanceable-lead-check.py
    python3 ops/glanceable-lead-check.py --path FILE
"""
from __future__ import annotations

import argparse
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Glanceable, in the rule's own listing: the hot loop list and the brief
# sections. The backlog is deliberately absent — see the module docstring.
SURFACES = [
    os.path.join(REPO, "out", "exports", "open-loops.md"),
    os.path.join(REPO, "out", "exports", "action-required.md"),
]
SURFACE_DIRS = [os.path.join(REPO, "out", "brief-pack")]

# Known findings, by the lead ref they name. One existed when this check was
# written, and it is Joe's own task about a real prospect with the document
# already built — moving it off his hot list is a call about that prospect, not
# a regex's business (the module docstring says this check never edits). Recording
# it here keeps the check honest and still fails on a NEW one, which is where the
# rule actually needs holding. Same shape as the enforcement-coverage backlog,
# and for the same reason: a check that fails on day one gets muted on day one.
KNOWN = {
    "L-221": "2026-08-15 — loop 338, the Ramsey buyer advisory. Joe's own task, "
             "document already built, dated 2026-08-13. Belongs on the Lead Board "
             "or in the backlog; which one is Joe's call.",
}

LEAD = r"L-\d+"

# An instruction aimed AT the lead. Ordered loosely by how often each shows up
# in a real loop row.
OUTREACH = (r"send|call|calling|email|e-mail|follow[ -]?up|following up|nudge|"
            r"reach out|reaching out|ping|check in|checking in|touch base|"
            r"outreach|contact|phone|text|invite|schedule a call")

# The lead ref and the instruction have to be in the SAME clause. A row that
# says "follow up on the lease" and separately cites a lead ref for provenance
# is not an instruction to work that lead.
#
# THE WINDOW ALLOWS PERIODS, and that is not laziness. The first version excluded
# them to stop a match running across a sentence boundary, which silently missed
# the live case this check was written for: "Send Dr. Randy Ramsey (L-221) the
# buyer advisory" — the period in the honorific ended the clause four characters
# in. Real loop rows are full of "Dr.", and no punctuation rule separates an
# abbreviation from a sentence end. The distance cap does the clause-locality
# work instead, and BENIGN below carries the precision.
_CLAUSE = r"[^|\n]{0,70}"
VIOLATION = re.compile(
    rf"(?:{OUTREACH}){_CLAUSE}\b{LEAD}\b"
    rf"|\b{LEAD}\b{_CLAUSE}(?:{OUTREACH})",
    re.I)

# Bookkeeping and provenance, checked on the same clause and winning over the
# instruction match, because these read as verbs to a regex and are not.
BENIGN = re.compile(
    r"registry|stamp|renumber|superseded|closed, not abandoned|"
    r"came out of|filed for reuse|row it replaced|provenance|"
    r"promoted from|merged from", re.I)


def offenders(text: str) -> list[tuple[int, str]]:
    out = []
    for i, line in enumerate(text.splitlines(), 1):
        if not VIOLATION.search(line):
            continue
        if BENIGN.search(line):
            continue
        out.append((i, line.strip()))
    return out


def surfaces() -> list[str]:
    found = [p for p in SURFACES if os.path.exists(p)]
    for d in SURFACE_DIRS:
        if os.path.isdir(d):
            found += [os.path.join(d, f) for f in sorted(os.listdir(d))
                      if f.endswith(".md")]
    return found


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--path", help="check one file instead of the live surfaces")
    args = ap.parse_args()

    targets = [args.path] if args.path else surfaces()

    # A file named as a backlog is read on cadence, not glanceable. Honoured by
    # NAME so a fixture and the real render behave identically.
    targets = [t for t in targets if "backlog" not in os.path.basename(t).lower()]

    findings: list[tuple[str, int, str]] = []
    known: list[tuple[str, int, str]] = []
    for path in targets:
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError:
            continue
        for lineno, line in offenders(text):
            ref = re.search(LEAD, line)
            if ref and ref.group(0) in KNOWN:
                known.append((path, lineno, ref.group(0)))
                continue
            findings.append((path, lineno, line))

    if not findings:
        names = ", ".join(os.path.basename(t) for t in targets) or "none found"
        held = (f"; {len(known)} known finding(s) held: "
                + ", ".join(sorted({r for _, _, r in known})) if known else "")
        print(f"glanceable-lead: OK — no NEW lead-outreach reminder on "
              f"{len(targets)} surface(s): {names}{held}")
        return 0

    print(f"glanceable-lead: {len(findings)} lead-outreach reminder(s) on a "
          f"glanceable surface — rule 17ffd587 keeps these off Joe's daily view:\n")
    for path, lineno, line in findings:
        rel = os.path.relpath(path, REPO)
        print(f"  {rel}:{lineno}")
        print(f"    {line[:150]}")
    print("\n  Leads live on THE LEAD BOARD and Joe works them there. A "
          "'call/send/follow up'\n  item on a lead does not belong on the hot loop "
          "list or the brief.\n"
          "\n  NOT a violation and never flagged: a dated follow-up on a real DEAL "
          "(C-###),\n  which the rule explicitly carves out as the reminder system "
          "working.\n"
          "\n  FIX: move the row to the Lead Board, or re-file it to "
          "open-loops-backlog.md,\n  which is read on cadence rather than every "
          "session. Deciding which is a\n  judgement about that lead, so this "
          "check reports and never edits.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
