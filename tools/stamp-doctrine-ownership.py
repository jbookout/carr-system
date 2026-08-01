#!/usr/bin/env python3
"""Apply ORDER 38's doctrine-ownership stamp to ORDER 30 phase 2's file set (R-40d).

THE SET (ruled by the Fable seat 2026-08-01, R-40d):
  * three Joe-personal doctrine files: model-tiering, x-article-playbook,
    social-media-workflow
  * every SKILL.md — 5 in the Drive vault's .claude/skills, 12 in ~/.claude/skills

EXPLICITLY EXCLUDED: 00_Context/ai-operating-notes.md. It is becoming compiled
output of the rule store, and a hand-edit stamp on a file that is about to stop
being hand-edited would be stale the day it lands.
Also excluded: skills/write-content/graphics/fonts/README.md — a font asset
readme, not doctrine.

PLACEMENT matches the 37 files ORDER 38 already stamped: immediately after the
H1 title, as a blockquote, with a blank line either side. On a SKILL.md the H1
sits below the YAML frontmatter, which is left untouched — the frontmatter is
parsed by the harness and a blockquote above it would break skill loading.

IDEMPOTENT: a file already carrying the stamp is skipped, so this can be re-run
after new skills are added without touching what is already correct.

Usage:
  .venv/bin/python tools/stamp-doctrine-ownership.py [--apply]
Default is a dry run that reports what it would change.
"""

import argparse
import os
import sys
from pathlib import Path

STAMP = (
    "> **Doctrine ownership: single writer.** One seat edits this file, the Fable "
    "design seat. Every other session, either brain, proposes changes through the "
    "`teach` verb or a team-board row, and the seat lands them. Set 2026-08-01 per "
    "ORDER 38 (two-writer endgame D3)."
)
MARKER = "Doctrine ownership: single writer"

DRIVE = Path("/Users/booko/Library/CloudStorage/GoogleDrive-joe.bookout.carr.us@gmail.com/My Drive")
VAULT = DRIVE / "CARR AI"
HOME_SKILLS = Path.home() / ".claude" / "skills"
DRIVE_SKILLS = DRIVE / ".claude" / "skills"

DOCTRINE = [
    VAULT / "00_Context" / "model-tiering.md",
    VAULT / "Marketing" / "Social Media" / "x-article-playbook.md",
    VAULT / "Marketing" / "Social Media" / "social-media-workflow.md",
]


def targets():
    out = list(DOCTRINE)
    for root in (DRIVE_SKILLS, HOME_SKILLS):
        out += sorted(root.glob("*/SKILL.md"))
    return out


def stamp(text):
    """-> (new_text, reason). reason None means changed."""
    if MARKER in text:
        return None, "already stamped"
    lines = text.split("\n")

    i = 0
    # step over YAML frontmatter if present
    if lines and lines[0].strip() == "---":
        for j in range(1, len(lines)):
            if lines[j].strip() == "---":
                i = j + 1
                break
        else:
            return None, "frontmatter opened but never closed — refusing"

    # find the H1
    h1 = None
    for j in range(i, min(i + 12, len(lines))):
        if lines[j].startswith("# "):
            h1 = j
            break
    if h1 is None:
        return None, "no H1 within 12 lines of the body start — refusing"

    new = lines[:h1 + 1] + ["", STAMP] + lines[h1 + 1:]
    # collapse a doubled blank line if the source already had one after the H1
    if len(new) > h1 + 3 and new[h1 + 3].strip() == "":
        pass
    else:
        new.insert(h1 + 3, "")
    return "\n".join(new), None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    changed = skipped = refused = 0
    for p in targets():
        if not p.exists():
            print(f"MISSING  {p}")
            refused += 1
            continue
        text = p.read_text(encoding="utf-8")
        new, reason = stamp(text)
        if new is None:
            if reason == "already stamped":
                skipped += 1
            else:
                print(f"REFUSED  {p} — {reason}")
                refused += 1
            continue
        if a.apply:
            p.write_text(new, encoding="utf-8")
        changed += 1
        print(f"{'STAMPED' if a.apply else 'WOULD  '}  {p}")

    print(f"\n{'applied' if a.apply else 'dry run'}: "
          f"{changed} stamped, {skipped} already stamped, {refused} refused")
    sys.exit(1 if refused else 0)


if __name__ == "__main__":
    main()
