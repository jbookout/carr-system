#!/usr/bin/env python3
"""gate_paths.py — ONE definition of "this file IS enforcement", for both doors.

WHY THIS MODULE EXISTS, and it is a hole that was open for three days.

Loop #231 opened 2026-08-07 when a subagent widened the security guard's own
KNOWN_HOSTS to unblock itself, and only the harness caught it. gate-edit-gate.py
closed the Write/Edit door on 2026-08-09 and its docstring told every reader:

    "Do NOT route around this by writing the file with a shell command;
     guard-unattended.py covers that path"

THAT SENTENCE WAS FALSE. Verified 2026-08-10 by firing the real hook rather than
by reading it — four shapes that rewrite a gate or wipe the live wiring all
returned exit 0, ALLOW:

    echo "KNOWN_HOSTS.add(...)" >> hooks/guard-unattended.py     -> ALLOW
    sed -i "" s/x/y/ hooks/record-home-gate.py                    -> ALLOW
    echo {} > ~/.claude/settings.json                             -> ALLOW
    cat x | tee hooks/gate-edit-gate.py                           -> ALLOW

The control in the same run — `echo hi >> open-loops.md` — was correctly DENIED,
so the Bash door was wired and working; it simply had no idea the gates existed.
guard-unattended.py's shell door guards generated renders, corpus renders and
vault .md, and nothing else. The false claim is the interesting part: it is why
nobody built this. A docstring asserted the coverage, so the gap read as closed.

THE RULE THIS OBEYS. a8c55a47 — a manual path and an automated path that do the
same job must be the same code. Two doors onto the same protected class must not
each carry their own copy of the list, which is how md_manifest.py and
corpus_renders.py are already shared between record-home-gate.py and
guard-unattended.py. This module is that same pattern for the gate files.

POSTURE: ANNOUNCE, NOT DENY — and that is Joe's ruling, not a softening chosen
here. Decision bd30b665, 2026-08-10: "dude, i think the gate think is a shitty
rule. its causing some serious blockage" / "yea downgrade it". The Write/Edit
door announces and allows; this door does the same, so a session gets the same
answer whichever way it reaches the file. A deny here would ALSO be worse than
the one it mirrors, because the Bash door has no approval channel at all — it
would be exactly the wall he rejected, with no way to open it.

WHAT ANNOUNCE ACTUALLY BUYS, stated plainly so nobody over-reads it: it does not
PREVENT a rogue gate edit. It makes one impossible to perform QUIETLY, which was
always the real property — the 2026-08-07 incident was caught by an outside
reviewer precisely because nothing in CARR said a word. Prevention lives in
hooks/gate-integrity.py, which hashes every gate against
ops/config/gate-baseline.json at each SessionStart and reports drift.

FAILS OPEN on any internal error, like every other gate here.
"""

import os
import re

# The files whose CONTENTS are the enforcement. Kept identical in meaning to
# gate-edit-gate.py's original private list, which now imports from here.
PROTECTED_PATTERNS = [
    re.compile(r"/carr-system/hooks/[^/]+\.py$"),       # the gate logic itself
    re.compile(r"/carr-system/ops/config/hooks\.json$"),        # the wiring
    re.compile(r"/carr-system/ops/config/gate-baseline\.json$"),  # the baseline
    re.compile(r"/carr-system/ops/harden-gates\.sh$"),           # the lock
    re.compile(r"/\.claude/settings\.json$"),           # live wiring, all copies
]

# Fixtures prove a gate change; they are not the gate. Gating them would make
# testing a gate harder than weakening one — precisely backwards.
EXEMPT = re.compile(r"-selftest\.py$")


def is_enforcement(path):
    """True when `path` is one of the files that ARE the enforcement."""
    if not path:
        return False
    try:
        p = os.path.abspath(os.path.expanduser(path))
    except Exception:
        return False
    if EXEMPT.search(p):
        return False
    return any(rx.search(p) for rx in PROTECTED_PATTERNS)


# ── the shell door ───────────────────────────────────────────────────────────
# Path shapes as they appear INSIDE a command string. Deliberately anchored on
# the same five classes as PROTECTED_PATTERNS rather than a loose "hooks" match,
# so an ordinary `grep hooks/` or `cat hooks/x.py` never trips it.
_PATH_RE = re.compile(
    r"(?:[~\w./-]*)?/(?:carr-system/hooks/[\w.-]+\.py"
    r"|carr-system/ops/config/(?:hooks|gate-baseline)\.json"
    r"|carr-system/ops/harden-gates\.sh"
    r"|\.claude/settings\.json)")

# Same proven shapes as guard-unattended.py's render door: redirect, tee,
# in-place sed, truncate. macOS sed carries a backup-suffix argument, so the
# sed clause accepts anything up to a clause separator.
_WRITE_BEFORE_CTX = re.compile(
    r"(>>?|\btee(\s+-a)?|\bsed\s+-i[^|;&]*|\btruncate\b[^|;&]*)\s*[\"']?$")

# Turning a gate OFF needs no write at all. guard-unattended.py's own docstring
# advertises it: "DISABLE FAST: ... `chmod -x` this file (a non-executable hook
# errors, and this gate fails open)". A silent chmod or rm is the quietest
# possible way to remove enforcement, so both are announced alongside writes.
_DISABLE_CMDS = ("chmod", "rm", "unlink", "chflags")
_COPY_CMDS = ("cp", "mv", "rsync", "install", "ln")


def enforcement_write(cmd):
    """The matched enforcement path when `cmd` writes to / disables one, else None.

    BEST-EFFORT, and said plainly rather than implied: it catches the ordinary
    shapes — `>`/`>>`, tee, `sed -i`, truncate, cp/mv/rsync/ln destinations,
    chmod/rm/chflags targets, and inline python open(...,'w'). It does not chase
    adversarial obfuscation, for the same reason the render door does not: a
    session determined to obfuscate has already decided to defeat the control,
    and gate-integrity.py is the backstop that does not care how the bytes
    changed.
    """
    if not cmd:
        return None
    try:
        hits = [(m.start(), m.group(0)) for m in _PATH_RE.finditer(cmd)]
        if not hits:
            return None
        for idx, path in hits:
            if EXEMPT.search(path):
                continue

            # 1. redirect / tee / sed -i / truncate immediately before the path
            before = cmd[max(0, idx - 60):idx]
            if _WRITE_BEFORE_CTX.search(before):
                return path

            # 2. inline python write
            if re.search(r"open\(\s*[\"'][^\"']*" + re.escape(os.path.basename(path))
                         + r"[\"']\s*,\s*[\"'][wa]", cmd):
                return path

        # 3. clause-leading commands: copy destinations and outright disables
        for clause in re.split(r"[;&|]", cmd):
            toks = clause.strip().split()
            if not toks:
                continue
            head = os.path.basename(toks[0])
            clause_hits = [m.group(0) for m in _PATH_RE.finditer(clause)
                           if not EXEMPT.search(m.group(0))]
            if not clause_hits:
                continue
            if head in _DISABLE_CMDS:
                return clause_hits[0]
            if head in _COPY_CMDS:
                # destination only — reading a gate INTO somewhere else is fine
                tail = clause.strip().rstrip("\"' ")
                for p in clause_hits:
                    if tail.endswith(p):
                        return p
        return None
    except Exception:
        return None                                    # fail open, like the rest


def announcement(path, how):
    """The message both doors print. One wording, so the two cannot drift."""
    name = os.path.basename(path)
    return (
        f"GATE CHANGE ANNOUNCED — {name} is enforcement, and {how} just changed it.\n\n"
        f"  file: {path}\n\n"
        "The action was ALLOWED. Say in one line what changed in this file and "
        "whether it makes the gate STRONGER or WEAKER — weaker is allowed, hiding "
        "that it is weaker is not. Then re-bless the baseline in the same pass "
        "(`python3 hooks/gate-integrity.py --bless`), or the next SessionStart "
        "reports it UNBLESSED and the check goes chronically red for a benign "
        "reason, which is the failure mode that hid a real five-gate wipe on "
        "2026-08-08."
    )
