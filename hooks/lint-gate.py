#!/usr/bin/env python3
"""lint-gate.py — the PostToolUse writing-lint gate (idea-bank #32, job b).

WHY. `run.sh lint` is a gate in doctrine and a habit in practice: it only fires
when someone remembers it. `DNA/writing-rules.md` binds EVERY surface a prospect
could ever see, and the expensive failures are the ones that ship. This makes the
gate mechanical for the one moment that matters, the write itself.

WHAT IT DOES. After a Write or Edit lands on a plausibly client-facing vault file,
it runs the linter and puts the result back in front of the session. It NEVER
blocks: the linter's own doctrine is that HARD is blocked and REVIEW must be
cleared consciously, and "consciously" means a human or the session deciding, not
a regex. A clean lint run is also explicitly NOT the writing-audit; that judgment
pass still belongs to the audit skill, and the output says so.

SCOPE, deliberately narrow. Only the CARR vault, only surfaces a prospect could
see. Repo code, scratchpad files, generated renders and internal ledgers are
skipped, because a linter that fires on everything is a linter people learn to
ignore. Generated files are skipped for a second reason: they are never
hand-edited, so linting them would only ever report the exporter's output.

FAILS OPEN AND SILENT. Any error, missing linter, or timeout exits 0 with no
output. This is an advisory gate on top of an existing doctrine, and it must
never be the reason a write appears to fail.
"""

import json
import os
import subprocess
import sys

VAULT = ("/Users/booko/Library/CloudStorage/"
         "GoogleDrive-joe.bookout.carr.us@gmail.com/My Drive/CARR AI")
RUN_SH = "/Users/booko/carr-system/run.sh"
TIMEOUT = 25

# Generated renders — never hand-edited, so never linted here.
GENERATED = (
    "open-loops.md", "open-loops-backlog.md", "action-required.md", "team-loops.md",
    "compiled-rules-shared.md", "compiled-rules-joe.md", "compiled-rules-dell.md",
    "introduction-rules.md", "clients-active.md", "hunt-ledger.md",
    "deals-reciprocity.generated.md", "record-layer-dictionary.md",
)

# Path fragment -> linter surface. First match wins, most specific first.
SURFACES = (
    ("Marketing/Social Media", "social"),
    ("DNA/Marketing", "social"),
    ("/Marketing/", "social"),
    ("Outreach/", "email"),
    ("templates.md", "email"),
    ("intake/", "proposal"),
    ("Output/", "proposal"),
    ("benefit-summary", "proposal"),
    ("proposals", "proposal"),
    ("Deal Management", "proposal"),
    ("GBP", "web"),
    ("SEO", "web"),
    ("landing", "web"),
)


def surface_for(path):
    low = path.lower()
    if "scratchpad" in low or "/out/" in low or ".generations" in low:
        return None
    if os.path.basename(path) in GENERATED:
        return None
    if not path.startswith(VAULT):
        return None
    if not path.endswith((".md", ".txt", ".html")):
        return None
    for frag, surf in SURFACES:
        if frag.lower() in low:
            return surf
    return None


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    try:
        tool = payload.get("tool_name") or payload.get("toolName") or ""
        if tool not in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
            sys.exit(0)
        ti = payload.get("tool_input") or payload.get("toolInput") or {}
        path = ti.get("file_path") or ti.get("filePath") or ""
        if not path:
            sys.exit(0)

        surface = surface_for(path)
        if not surface:
            sys.exit(0)
        if not (os.path.exists(RUN_SH) and os.path.exists(path)):
            sys.exit(0)

        res = subprocess.run(
            [RUN_SH, "lint", path, "--surface", surface],
            capture_output=True, text=True, timeout=TIMEOUT,
            cwd="/Users/booko/carr-system",
        )
        out = (res.stdout or "") + (res.stderr or "")
        if not out.strip():
            sys.exit(0)

        tail = "\n".join(out.strip().splitlines()[-25:])
        rel = path[len(VAULT):].lstrip("/")
        if "FAIL" in out or "hard-ban" in out:
            msg = (f"WRITING-LINT: HARD BAN HIT on {rel} (surface: {surface}). "
                   f"writing-rules.md says do not ship until these are zero. Fix before this "
                   f"reaches Joe or a prospect.\n\n{tail}")
        elif "REVIEW" in out:
            msg = (f"WRITING-LINT: REVIEW items on {rel} (surface: {surface}). No hard bans. "
                   f"Clear each one consciously or fix it; do not ignore silently. A clean lint "
                   f"run is not the writing-audit.\n\n{tail}")
        else:
            sys.exit(0)

        # PostToolUse reaches the session ONLY through structured JSON. Plain text
        # on stdout is not injected into context, so the earlier draft of this hook
        # would have run the linter and thrown the result away. additionalContext
        # arrives as a system reminder the session reads.
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": msg,
            }
        }))
        sys.exit(0)
    except Exception:
        sys.exit(0)


if __name__ == "__main__":
    main()
