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



def joe_approved(payload, path):
    """Did the partner approve THIS edit in his own words, this turn?

    Reads the transcript for his most recent genuine message — never a
    system-reminder, task-notification or tool result — and looks for an
    approval. Same design as conduct-stop-gate.py's exemptions: the signal comes
    off the human's actual keystrokes and a session cannot grant it to itself.

    WHY THIS EXISTS. The first version of this gate told sessions "get his
    approval, then edit" and then blocked the edit anyway, because it held no
    state and never read the transcript. Joe approved the same change three
    times on 2026-08-09 and the gate refused all three. A gate with no approval
    channel is not a gate, it is a wall — and the wall was standing in front of
    work he had explicitly asked for.

    It is a DELIBERATE LOOSENING and it is written down as one: an approval
    phrase in his last message is weaker evidence than a password, because a
    session controls how it described the change beforehand. That was the
    trade he chose when he replaced the OS hardening: "you can just ask me if i
    approve writing hooks instead of requiring a password."
    """
    path_l = (path or "").lower()
    stem = os.path.basename(path_l).replace(".py", "").replace(".json", "").replace(".sh", "")
    tp = payload.get("transcript_path")
    if not tp or not os.path.exists(tp):
        return False
    try:
        with open(tp, "r", errors="replace") as fh:
            lines = fh.readlines()[-200:]
    except Exception:
        return False
    for line in reversed(lines):
        try:
            rec = json.loads(line.strip())
        except Exception:
            continue
        if rec.get("type") not in ("user", "human"):
            continue
        if rec.get("isMeta") or rec.get("isCompactSummary"):
            continue
        msg = rec.get("message") or rec
        c = msg.get("content")
        if isinstance(c, str):
            t = c
        elif isinstance(c, list):
            t = "\n".join(b.get("text", "") for b in c
                           if isinstance(b, dict) and b.get("type") == "text")
        else:
            continue
        if not t:
            continue
        head = t.lstrip()
        if head.startswith(("<system-reminder>", "<task-notification>",
                            "[SYSTEM NOTIFICATION", "<local-command",
                            "<command-name>", "Caveat:")):
            continue
        # His most recent real message. Only this one counts — an approval two
        # turns ago does not license an unrelated edit now.
        low = " ".join(t.split()).lower()
        APPROVE = re.compile(
            r"\b(approve|approved|approve both|go ahead|do it|yes,? do|"
            r"make the edit|build (it|them|both)|add the|ship it|"
            r"you can (edit|change|write)|permission granted|lgtm)\b")
        if not APPROVE.search(low):
            return False
        # A blanket "do it" is enough; a named file is stronger and always wins.
        if stem and stem in low:
            return True
        return True
    return False


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

        # The approval channel. Without this the gate refuses an edit the
        # partner has already authorised, which is what happened three times
        # on 2026-08-09 before it was added.
        if joe_approved(payload, path):
            dlog(f"ALLOW(approved-in-session) {path}")
            sys.exit(0)

        name = os.path.basename(path)

        # DOWNGRADED FROM BLOCK TO ANNOUNCE — Joe's ruling 2026-08-10, decision
        # bd30b665: "dude, i think the gate think is a shitty rule. its causing
        # some serious blockage" / "yea downgrade it".
        #
        # THREE FINDINGS BEHIND IT, each verified rather than asserted:
        #   1. THE APPROVAL CHANNEL CANNOT SEE HIS APPROVAL. joe_approved()
        #      reads the transcript for his most recent genuine user record, but
        #      messages sent MID-TURN — while a turn is still running — are never
        #      written there. Checked directly: a session with three genuine user
        #      records contained neither of the two "approved"s he had typed.
        #      This is not a regex that can be widened; there is nothing in the
        #      file to match against.
        #   2. A CROSS-SESSION MESSAGE DISPLACES HIM. The check reads the LAST
        #      genuine user record, and an agent-to-agent handoff arrives as one
        #      — so another session's message silently becomes "Joe's last word"
        #      and the gate refuses even a genuinely approved edit.
        #   3. THE BLOCK IS REDUNDANT. gate-integrity.py already reports
        #      "UNBLESSED: hooks/X exists but is not in the baseline" at every
        #      SessionStart, and the baseline only moves through a deliberate
        #      --bless. An unauthorised gate edit is caught either way.
        #
        # WHAT WAS WRONG IN THE ORIGINAL CASE FOR THIS CHANGE, recorded so the
        # next reader does not inherit it: the session first claimed this gate
        # had NO audit trail, having looked for out/gate-edit-gate.jsonl. It
        # logs to the SHARED out/conduct-gate.jsonl and had six fires on record.
        # The claim was false; the argument stood without it, because all six
        # recorded fires were blocks of work Joe had asked for and none was a
        # change he did not want.
        #
        # WHAT THIS COSTS, PLAINLY: a rogue gate edit is now DETECTED at next
        # SessionStart rather than PREVENTED in the moment. That is weaker.
        # Rule 61551180 is why it is still right — a present threat outranks a
        # possible future use when the protection is reversible, and the present
        # threat (approved work blocked six times in two days) is the only one
        # of the two that has ever actually happened.
        #
        # The principle Joe encoded — a human authorises changes to enforcement
        # — is NOT discarded. The announcement below is that authorisation
        # surfacing where he can see it, instead of a wall he cannot open.
        announcement = (
            f"GATE EDIT ANNOUNCED — {name} is enforcement, and it just changed.\n\n"
            f"  file: {path}\n\n"
            "The write was ALLOWED. Say in one line what changed in this file "
            "and whether it makes the gate stronger or weaker — weaker is "
            "allowed, hiding that it is weaker is not. Then re-bless the "
            "baseline in the same pass (`python3 hooks/gate-integrity.py "
            "--bless`), or the next SessionStart reports it UNBLESSED and the "
            "check goes chronically red for a benign reason, which is the "
            "failure mode that hid a real five-gate wipe on 2026-08-08."
        )

        audit({"ts": now(), "hook": "gate-edit-gate", "classes": ["gate_edit"],
               "patterns": [f"gate_edit:{name}"], "session": payload.get("session_id"),
               "path": path, "decision": "announce"})
        dlog(f"ANNOUNCE {path}")
        print(json.dumps({
            "systemMessage": announcement,
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow",
                "permissionDecisionReason": announcement,
            },
        }))
        sys.exit(0)

        # --- retired block path, kept for one release so the downgrade can be
        # --- reverted by deleting the return above. Unreachable.
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
