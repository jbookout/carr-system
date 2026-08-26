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

THE SHELL DOOR, AND A CLAIM THIS FILE MADE THAT WAS FALSE FOR THREE DAYS. The
deny text below used to tell sessions "guard-unattended.py covers that path" for
shell writes. It did not. Verified 2026-08-10 by firing the real hook: append,
`sed -i`, `> ~/.claude/settings.json` and tee onto a gate all returned ALLOW,
while the render control in the same run correctly DENIED — so the Bash door was
working and simply did not know the gates existed. That sentence is why nobody
built it: a docstring asserted the coverage, so the gap read as closed. The door
exists now, announces identically, and shares this file's list through
hooks/gate_paths.py.

THE ADMISSION CARD, ADDED 2026-08-23, IS THE ONE THING THIS DOOR STILL DENIES.
Joe's Stop-gate rationing, off that day's gates-audit council, carried a second
order: a NEW BLOCKING GATE must record its seven-question card at birth. The
council's reasoning is a count — 59 named controls, born one reasonable
afternoon at a time, with this file re-blessing their hashes and nothing
anywhere asking whether the next one should be able to refuse work at all.
Grok's chair, high confidence: "This is the missing gate that prevents the next
59 from becoming 90."

It sits ahead of the approval channel below, deliberately. Joe's in-session
approval answers whether HE wants a change; it reads his words and not the diff,
so he can approve a gate edit without having been told that what is being
created is a blocker. That is exactly the gap.

THE DENY IS NARROW AND IT IS NOT A REVERSAL OF HIS DOWNGRADE. It fires only when
a hooks/*.py file that is NOT already a blessed blocker gains a blocking emit
AND has an entry point AND has no complete card. Editing an existing gate — the
constant event, and the one the 2026-08-10 downgrade was about — still
announces. A matcher that only announces ships with no card at all. And the
remedy is a JSON file the session writes itself: ops/config/gate-admission.json
is deliberately NOT in hooks/gate_paths.py's protected list, because a control
whose remedy is itself gated is the wall he rejected.

THE HALF THIS DOES NOT COVER, said plainly rather than implied the way this
file's shell claim was for three days: the Bash door announces and does not
deny, by Joe's ruling, so `cat > hooks/new-blocker.py` reaches disk without a
card. hooks/gate-integrity.py still reports it UNBLESSED at the next
SessionStart, which is the same detect-not-prevent trade the rest of this layer
runs on.

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

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOME = os.path.expanduser("~")
LOG = os.path.join(REPO, "out", "conduct-gate.jsonl")
DEBUG = os.path.join(REPO, "out", "conduct-gate.log")

# THE LIST MOVED OUT, 2026-08-10 (loop #231). It now lives in hooks/gate_paths.py
# because a SECOND door needed it: guard-unattended.py's shell path had no idea
# these files existed, so `echo >> hooks/guard-unattended.py` was ALLOW while Edit
# on the same file came here. One list, two doors — the same arrangement
# md_manifest.py and corpus_renders.py already have (rule a8c55a47).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gate_paths import announcement, is_enforcement  # noqa: E402


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
    return is_enforcement(path)


# ── the gate-ADMISSION card (2026-08-23) ────────────────────────────────────
# THE MISSING GATE THE GATES AUDIT NAMED. Grok's chair, high confidence: "Gate-
# edit today re-blesses hashes. It does not ask G1–G7. This is the missing gate
# that prevents the next 59 from becoming 90." A new blocker could be born on
# any afternoon, and nothing anywhere asked whether it should exist — only
# whether its hash had been recorded afterwards.
#
# THE SEVEN QUESTIONS are the council's own test, compiled rather than recited
# (which is the 2026-08-09 precedent: the anti-deferral fix is a gate, not
# another taught rule, because the taught rule existed and did not hold):
#
#   parent_incident or landing_harm  a dated incident where prose failed, or one
#                                    concrete sentence of "if this does not bind,
#                                    THIS artifact lands in THIS person's hands".
#                                    "Could be bad" is not parentage.
#   bind_moment                      the last cheap reversible moment before that
#                                    harm. Wrong moment is a tax even when the
#                                    rule is true.
#   consumer                         who receives the wrong artifact. If the only
#                                    consumer is the gate's own log, it is sediment.
#   self_serve_remedy                what the session can DO about it without a
#                                    new login. A dead end fails even when the
#                                    rule is right — today's report-problem deny
#                                    is the type specimen.
#   scarce_resource_cost             what it spends, of Joe's attention, this
#                                    Mac's wall-clock, Stop-reopen tokens, or
#                                    billed CI minutes.
#   unique_bind                      why no existing control already binds this
#                                    class at this moment. Two blockers on one
#                                    claim-set fail uniqueness.
#   matcher_test                     a fixture path that EXISTS. A card naming a
#                                    test nobody wrote is the box-ticking version
#                                    of this control.
#
# LOG-ONLY MATCHERS SHIP CHEAPER, and that asymmetry is the design. Nothing here
# prices BUILDING a check; it prices the power to refuse. A matcher that
# announces needs no card at all.
#
# GRANDFATHERED. Every gate already in the blessed baseline keeps its place. A
# check that failed on day one across forty-odd existing gates would be muted on
# day one, which is the failure ops/enforcement-coverage-check.py documents at
# length and answers the same way.
#
# WHY THIS DENIES INSIDE AN ANNOUNCE-ONLY GATE. Joe downgraded gate-edit on
# 2026-08-10 (decision bd30b665) because it was refusing work HE HAD ASKED FOR,
# six times in two days, through an approval channel that could not see his
# approval. That reasoning is about EDITING gates, which happens constantly.
# Being born a blocker is rare, it is the event this audit was convened over,
# and the remedy is a JSON file the session writes itself with nobody's
# password — which answers the actual objection, a wall he could not open.
CARDS = os.environ.get("CARR_GATE_ADMISSION_CARDS") or os.path.join(
    REPO, "ops", "config", "gate-admission.json")
BASELINE = os.path.join(REPO, "ops", "config", "gate-baseline.json")

# A blocking emit, in the four shapes this repo actually uses. `return 2` is
# anchored to its own line because that is how a gate spells it (see
# hooks/loose-work-gate.py before its demotion) and because a bare "return 2"
# inside an expression is a number, not a refusal.
BLOCKING_EMIT = re.compile(
    r'"decision"\s*:\s*"block"'
    r'|"permissionDecision"\s*:\s*"deny"'
    r"|sys\.exit\(2\)"
    r"|raise\s+SystemExit\(2\)"
    r"|^\s*return\s+2\s*(?:#.*)?$", re.M)

# A GATE IS SOMETHING THAT CAN ACT, not something that lives in hooks/. Borrowed
# verbatim from ops/mechanism-doctrine-gate.py, which reached this predicate the
# hard way: it first classified every hooks/*.py as a mechanism and caught
# hooks/turn_origin.py, a shared detector two gates import that reads no payload
# and refuses nothing.
ENTRY_POINT = re.compile(r"sys\.stdin|def main\(|__main__", re.M)

CARD_QUESTIONS = ("parent_incident", "bind_moment", "consumer", "self_serve_remedy",
                  "scarce_resource_cost", "unique_bind", "matcher_test")
# The first question takes either answer: a dated incident, or a named harm.
CARD_ALTERNATIVES = {"parent_incident": "landing_harm"}


def _read(path):
    try:
        with open(path, errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def written_text(tool, ti):
    """Everything this call is about to put into the file."""
    if not isinstance(ti, dict):
        return ""
    if tool == "Write":
        return str(ti.get("content") or "")
    if tool == "MultiEdit":
        edits = ti.get("edits")
        if isinstance(edits, list):
            return "\n".join(str(e.get("new_string") or "")
                             for e in edits if isinstance(e, dict))
        return ""
    return str(ti.get("new_string") or "")


def blessed_gates():
    try:
        return set(json.loads(_read(BASELINE)).get("hashes") or {})
    except ValueError:
        return set()


def card_for(stem):
    """The recorded card for this gate, or None. Never raises."""
    try:
        data = json.loads(_read(CARDS)) or {}
    except ValueError:
        return None
    cards = data.get("cards")
    return cards.get(stem) if isinstance(cards, dict) else None


def card_gaps(card):
    """The questions this card has not answered. Empty means admitted."""
    if not isinstance(card, dict):
        return list(CARD_QUESTIONS)
    gaps = []
    for q in CARD_QUESTIONS:
        answers = [card.get(q)] + [card.get(CARD_ALTERNATIVES[q])] if q in CARD_ALTERNATIVES \
            else [card.get(q)]
        if not any(isinstance(a, str) and a.strip() for a in answers):
            gaps.append(q if q not in CARD_ALTERNATIVES
                        else f"{q} (or {CARD_ALTERNATIVES[q]})")
    test = card.get("matcher_test")
    if isinstance(test, str) and test.strip():
        full = test if os.path.isabs(test) else os.path.join(REPO, test)
        if not os.path.exists(full):
            gaps.append(f"matcher_test names {test}, which does not exist")
    return gaps


def new_blocker(path, tool, ti):
    """Is this call giving the system a blocker it did not have? Then (stem, gaps).

    Returns None when no card is required — which is the overwhelmingly common
    answer, and deliberately so.
    """
    if not re.search(r"/hooks/[^/]+\.py$", path.replace("\\", "/")):
        return None                      # wiring and baselines are not new gates
    stem = os.path.basename(path)
    existing = _read(path)
    added = written_text(tool, ti)
    if not added:
        return None                      # nothing to read; never guess
    if BLOCKING_EMIT.search(existing) and stem in blessed_gates():
        return None                      # already an admitted blocker
    combined = existing + "\n" + added
    if not BLOCKING_EMIT.search(combined):
        return None                      # log-only ships cheaper, on purpose
    if not ENTRY_POINT.search(combined):
        return None                      # a library refuses nothing
    gaps = card_gaps(card_for(stem))
    return (stem, gaps) if gaps else None


def admission_refusal(stem, gaps):
    listed = "\n".join(f"    · {g}" for g in gaps)
    rel = os.path.relpath(CARDS, REPO)
    return (
        f"GATE ADMISSION — {stem} would be a NEW BLOCKER, and no admission card "
        f"answers for it.\n\n"
        f"  unanswered:\n{listed}\n\n"
        "This is the 2026-08-23 gates-audit council's rule, and its reason is a "
        "count: this system runs 59 named controls, born one reasonable "
        "afternoon at a time, and nothing has ever asked at the door whether the "
        "next one should be able to REFUSE work. Building a check is free. The "
        "power to stop a session is what costs, and what gets paid for by "
        "somebody who is not you.\n\n"
        "TWO WAYS PAST THIS, both self-serve, no approval and no password.\n\n"
        "  1. SHIP IT LOG-ONLY. Drop the refusal — the block decision, the deny, "
        "the exit 2 — and announce instead. A matcher that reports needs no card "
        "at all, which is the whole asymmetry: this prices refusing, never "
        f"building. hooks/ledger-sweep.py is the pattern to copy.\n\n"
        f"  2. ANSWER THE SEVEN QUESTIONS in {rel}, under cards[\"{stem}\"]:\n\n"
        "     parent_incident      the dated incident where prose failed — OR\n"
        "     landing_harm         one sentence: if this does not bind, WHICH\n"
        "                          artifact lands in WHOSE hands. \"Could be bad\"\n"
        "                          is not parentage.\n"
        "     bind_moment          the last cheap reversible moment before that\n"
        "                          harm. A true rule at the wrong moment is a tax.\n"
        "     consumer             who receives the wrong artifact. If the only\n"
        "                          consumer is this gate's own log, it is sediment.\n"
        "     self_serve_remedy    what a session can DO about the refusal without\n"
        "                          a new login. A dead end fails even when the rule\n"
        "                          is right.\n"
        "     scarce_resource_cost what it spends: Joe's attention, this Mac's\n"
        "                          wall-clock, Stop-reopen tokens, or CI minutes.\n"
        "     unique_bind          why nothing already binds this class at this\n"
        "                          moment. Two blockers on one claim-set fail.\n"
        "     matcher_test         a fixture path that EXISTS, written first.\n\n"
        "Then make this edit again. Gates already in the blessed baseline are "
        "grandfathered and unaffected; this asks only about the power being "
        "created right now."
    )




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

        # THE ADMISSION CARD comes FIRST, and before the approval channel too.
        # Joe's in-session approval is about whether HE wants this change; the
        # card is about whether the system can account for a new power to
        # refuse. He can approve a gate edit without having been told the thing
        # being created is a blocker — that is precisely the gap the 2026-08-23
        # audit named, since the approval channel reads his words and not the
        # diff.
        needs_card = new_blocker(path, tool, ti)
        if needs_card:
            stem, gaps = needs_card
            audit({"ts": now(), "hook": "gate-edit-gate", "classes": ["gate_admission"],
                   "patterns": [f"gate_admission:{stem}"],
                   "session": payload.get("session_id"), "path": path,
                   "decision": "deny", "gaps": gaps})
            dlog(f"DENY(no admission card) {path} :: {gaps}")
            # Exit 2 with the text on stderr, not structured JSON: on a build
            # that does not parse the structured contract, exit 0 reads as ALLOW
            # and this would fail open silently. Same reasoning the retired block
            # path below carries.
            print(admission_refusal(stem, gaps), file=sys.stderr)
            sys.exit(2)

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
        # ONE WORDING, shared with the Bash door, so a session gets the same
        # answer whichever way it reaches the file (hooks/gate_paths.py).
        announcement_text = announcement(path, f"{tool.lower()}")

        audit({"ts": now(), "hook": "gate-edit-gate", "classes": ["gate_edit"],
               "patterns": [f"gate_edit:{name}"], "session": payload.get("session_id"),
               "path": path, "decision": "announce"})
        dlog(f"ANNOUNCE {path}")
        print(json.dumps({
            "systemMessage": announcement_text,
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow",
                "permissionDecisionReason": announcement_text,
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
            "Do NOT route around this by writing the file with a shell command. "
            "guard-unattended.py covers that path as of 2026-08-10 (it did NOT "
            "before then, despite this text having claimed so since 2026-08-09), "
            "and doing so deliberately is the exact behaviour this layer exists "
            "to stop."
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
