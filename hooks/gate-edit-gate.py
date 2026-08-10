#!/usr/bin/env python3
"""gate-edit-gate.py — editing a GATE requires Joe's in-session approval.

WHY THIS REPLACED THE OS HARDENING. Joe, 2026-08-09, overruling both council
chairs and this session:

    "man honestly i disagree with hardening at all. i think the council went
     overboard on that. youre not going to rewrite hooks anyway without a good
     reason. if anything, you can just ask me if i approve writing hooks instead
     of requiring a password. thats more of a pain in the ass than just me
     approving it in the session"

He is right, and the cost/benefit was lopsided the way he says. The OS hardening
(root:wheel + chflags uchg on every gate script and all four settings.json
files) genuinely worked — a full attack suite of append, truncate, rename-over,
unlink and chflags-nouchg was refused on every target. But it charged a real
price on every ordinary day:
  - "always allow" permission grants stopped sticking, forever
  - theme changes, plugin installs and /config all failed
  - every gate change became unlock -> install -> relock, authenticated, and it
    failed twice in practice before working (uchg blocks even root; the .bak
    file inherited uchg through copy2)
  - and the one that actually mattered: a buggy gate wrongly blocking real work
    could not be fixed at all until Joe was physically available

Against a threat that has never occurred. THIS gate is the same control at a
fraction of the cost: a session cannot QUIETLY edit a gate, because the edit
stops here and has to be put to Joe in the session, where approving is one
click instead of a password and an unlock/relock cycle.

WHAT IT COVERS — the files whose contents ARE the enforcement:
  hooks/*.py                      the gate logic itself
  ops/config/hooks.json           the wiring (a gate nothing invokes is off)
  ~/.claude/settings.json         the live wiring, all four locations
  ops/harden-gates.sh             the lock itself
  ops/config/gate-baseline.json   the integrity baseline
Editing any of them is, by rule aa411351's constitutional carve-out, a change to
the boundary rather than ordinary internal work — the one class both council
chairs refused to let the system decide for itself. Grok: "If the system can
'internally decide' to loosen its own collar, you do not have a boundary."

WHAT IT DOES NOT COVER, on purpose: the SELFTESTS (ops/*-selftest.py). Fixtures
are not enforcement, they are how a gate change is proven, and gating them would
make it harder to test a gate than to weaken one — precisely backwards.

WHY THIS IS NOT MERELY ADVISORY. It denies the tool call. A session that wants
the edit has to surface it to Joe and get a real answer, which is the whole
substance of "just ask me". The remaining honest gap, stated plainly rather than
hidden: an in-session approval is only as good as the session's description of
what it is about to change, whereas a password could not be talked around. Joe
weighed that and chose the cheaper control. That is his call to make and it is
recorded in the decision log, not quietly reversed later by a session that finds
this gate inconvenient.

THE DETECTION LAYER STAYS EITHER WAY: hooks/gate-integrity.py still runs at
every SessionStart, hashes every gate against ops/config/gate-baseline.json, and
checks that live settings actually INVOKES each one. So an edit that somehow
lands without passing through here is still loud at the next boot.

FAILS OPEN on any error — a wedged session is worse than an edit that has to be
re-approved.

Fixtures: ops/gate-edit-gate-selftest.py
"""
import json
import os
import re
import sys
from datetime import datetime, timezone

REPO = os.path.expanduser("~/carr-system")
HOME = os.path.expanduser("~")
LOG = os.path.join(REPO, "out", "conduct-gate.jsonl")
DEBUG = os.path.join(REPO, "out", "conduct-gate.log")

PROTECTED_PATTERNS = [
    re.compile(r"/carr-system/hooks/[^/]+\.py$"),
    re.compile(r"/carr-system/ops/config/hooks\.json$"),
    re.compile(r"/carr-system/ops/config/gate-baseline\.json$"),
    re.compile(r"/carr-system/ops/harden-gates\.sh$"),
    re.compile(r"/\.claude/settings\.json$"),
]

# Fixtures prove a gate change; they are not the gate. Gating them would make
# testing a gate harder than weakening one.
EXEMPT = re.compile(r"-selftest\.py$")


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def dlog(msg):
    try:
        os.makedirs(os.path.dirname(DEBUG), exist_ok=True)
        with open(DEBUG, "a") as fh:
            fh.write(f"{now()}  gate-edit-gate  {msg}\n")
    except Exception:
        pass


def audit(rec):
    if rec.get("session") == "selftest":
        return
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a") as fh:
            fh.write(json.dumps(rec) + "\n")
    except Exception:
        pass


def is_protected(path):
    if not path:
        return False
    p = os.path.abspath(os.path.expanduser(path))
    if EXEMPT.search(p):
        return False
    return any(rx.search(p) for rx in PROTECTED_PATTERNS)


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception as exc:
        dlog(f"ALLOW(parse-error) {exc}")
        sys.exit(0)

    try:
        tool = payload.get("tool_name") or payload.get("toolName") or ""
        if tool not in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
            sys.exit(0)
        ti = payload.get("tool_input") or payload.get("toolInput") or {}
        path = ti.get("file_path") or ti.get("filePath") or "" if isinstance(ti, dict) else ""

        if not is_protected(path):
            sys.exit(0)

        name = os.path.basename(path)
        reason = (
            f"GATE EDIT — needs Joe's approval before it can be written.\n\n"
            f"  file: {path}\n\n"
            "This file IS enforcement — a gate's logic, its wiring, or the lock "
            "itself. Changing it changes what binds every session, which rule "
            "aa411351 puts in the one class the system does not decide for "
            "itself.\n\n"
            "THIS IS NOT A REFUSAL, IT IS A HAND-OFF. Joe replaced the OS-level "
            "hardening with exactly this control on 2026-08-09: \"you can just "
            "ask me if i approve writing hooks instead of requiring a password. "
            "thats more of a pain in the ass than just me approving it in the "
            "session.\"\n\n"
            "DO THIS NOW, in the session, in three lines and no more:\n"
            f"  1. WHAT changes in {name} — the actual behaviour, not the diff\n"
            "  2. WHY — what is broken or missing right now\n"
            "  3. WHETHER it makes the gate STRONGER or WEAKER, said plainly. "
            "Weaker is allowed; hiding that it is weaker is not.\n\n"
            "Then wait for his answer. If he approves, say so and make the edit "
            "in the next turn — this gate does not fire twice on an approved "
            "change because he will have seen it.\n\n"
            "Do NOT route around this by writing the file with a shell command; "
            "guard-unattended.py covers that path, and doing so deliberately is "
            "the exact behaviour this whole layer exists to stop."
        )

        audit({"ts": now(), "hook": "gate-edit-gate", "classes": ["gate_edit"],
               "patterns": [f"gate_edit:{name}"], "session": payload.get("session_id"),
               "path": path})
        dlog(f"DENY {path}")
        # Exit 2, not JSON: on a build that does not parse the structured
        # contract, exit 0 reads as ALLOW and the gate fails open silently.
        print(reason, file=sys.stderr)
        sys.exit(2)

    except Exception as exc:
        dlog(f"ALLOW(internal-error) {exc}")
        sys.exit(0)


if __name__ == "__main__":
    main()
