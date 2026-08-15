#!/usr/bin/env python3
"""drift-assertion-gate.py — the drift check, moved to the door where the claim
actually reaches a human.

WHY THIS EXISTS, and the frequency is the whole argument. "A CURRENT artifact
read accurately, the DECISION behind it left unread" is the most frequent
failure class this system has recorded, running since 2026-08-04, with the
majority caught by Joe rather than by a session. When one was filed on
2026-08-14 the record layer answered with the point this file acts on: "A repeat
class is a design problem, not a lapse: say so rather than filing the next one
quietly."

THE COUNT IS NOT WRITTEN HERE, deliberately, and that is a correction rather
than an omission. This file carried "NINE times" in its docstring and again in
the message it prints. By 2026-08-15 the ledger read eleven, so the gate whose
entire job is catching a stale artifact quoted as present state was itself
quoting a stale artifact as present state, in the sentence meant to persuade.
Rule b01edd26 bans exactly this: no hardcoded count a later edit can falsify.
A Stop hook cannot ask the record layer for the live figure — it must stay fast
and work offline — so the honest move is to state the shape and point at the
live source. `standing-context` returns the ledger with current counts.

drift-claim-gate.py already guards this class and it WORKS — it fired twice on
2026-08-14 and both flagged claims turned out to be wrong. Its limitation is
positional, not logical: it sits on PreToolUse for record-defect and add-loop, so
it speaks when a session FILES A RECORD. That is not when the damage happens. On
2026-08-14 the session told Joe the wrong thing twice in chat before it filed
anything, and the filing is what finally tripped the gate. On other occasions in
this class nothing was ever filed at all, so nothing ever fired.

The expensive failure is the ASSERTION — telling a partner that something is
broken when it was chosen, which is how six of the nine were discovered, by him.
So this is the same policy on the Stop door, where the reply is about to reach
him and can still be revised.

ONE JUDGEMENT, TWO DOORS. It imports drift-claim-gate's DRIFT detector and its
decision search rather than restating them, the same discipline bash-write-gate
and write-effect-check follow. A second copy of that regex would drift from the
first the week either one changed, and the selftest asserts the two are the same
pattern rather than trusting that they stay so.

WHY IT BLOCKS HERE WHEN THE WRITE DOOR ONLY INFORMS. A record can be corrected
after the fact; a wrong claim in front of Joe costs his attention and, six times
out of nine, his correction. Stop is also the one door where blocking is cheap:
it reopens the turn, which is exactly what happened all day on 2026-08-14 to good
effect.

IT FIRES ONCE PER CLAIM, and that is the most important line in this file. A Stop
hook that blocks the same reply forever is a session that cannot end. The first
block hands over the matching rulings; if the same claim comes back, the session
has read them and the call is now its own.

IT IS SILENT WHEN NO RULING MATCHES — drift-claim-gate's own rule, inherited
deliberately: "a drift claim with no matching ruling is probably a real finding."
Most reports of breakage are true. A gate that fires on all of them is one
somebody turns off, and then it is not a gate.

FAILS OPEN ON EVERYTHING ELSE. No transcript, no decision log, an unreadable
file, an internal error: none may strand a turn.
"""

import hashlib
import importlib.util
import json
import os
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
LOG = os.path.join(REPO, "out", "hook-guard.log")
STATE = os.environ.get("CARR_DRIFT_ASSERTION_STATE") or os.path.join(
    REPO, "out", "drift-assertion")

_policy = None
_chat = None


def log(line):
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a") as fh:
            fh.write(f"drift-assertion-gate {line}\n")
    except Exception:
        pass


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(os.path.dirname(__file__), filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def policy():
    """drift-claim-gate, the WRITE door's module — imported, never copied.

    Its DECISIONS constant is redirected when CARR_DECISIONS_PATH is set, which
    is what lets the selftest run against a throwaway log instead of the vault.
    """
    global _policy
    if _policy is None:
        _policy = _load("carr_drift_claim_policy", "drift-claim-gate.py")
        override = os.environ.get("CARR_DECISIONS_PATH")
        if override:
            _policy.DECISIONS = override
    return _policy


def chat():
    """chat-lint-gate, for its transcript reader. Same reason: one reader."""
    global _chat
    if _chat is None:
        _chat = _load("carr_chat_lint_reader", "chat-lint-gate.py")
    return _chat


def final_assistant_text(path):
    reader = chat()
    text = ""
    for record in reader.read_tail(path):
        candidate = reader.text_of(record, ("assistant",))
        if candidate and candidate.strip():
            text = candidate.strip()
    return text


def already_raised(claim_text):
    """True if this exact claim was flagged once already — then stand down.

    Keyed on the claim's own text so a session that revises genuinely gets a
    fresh judgement, while one that re-sends the same words is not held twice.
    """
    digest = hashlib.sha256(claim_text.encode("utf-8", "replace")).hexdigest()[:20]
    marker = os.path.join(STATE, f"{digest}.seen")
    if os.path.exists(marker):
        return True
    try:
        os.makedirs(STATE, exist_ok=True)
        with open(marker, "w") as fh:
            fh.write("1")
    except Exception:
        pass                                # cannot record it: speak once, allow next
    return False


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.exit(0)
    try:
        if (payload.get("hook_event_name") or "Stop") != "Stop":
            sys.exit(0)
        if payload.get("stop_hook_active"):
            sys.exit(0)
        path = payload.get("transcript_path")
        if not path or not os.path.exists(path):
            sys.exit(0)

        text = final_assistant_text(path)
        if len(text) < 60:
            sys.exit(0)

        module = policy()
        prose = chat().strip_fences(text)
        if not module.DRIFT.search(prose):
            sys.exit(0)
        if not os.path.exists(getattr(module, "DECISIONS", "")):
            sys.exit(0)

        hits = module.search_decisions(module.salient_tokens(prose))
        if not hits:
            sys.exit(0)                     # no ruling: probably a real finding
        if already_raised(prose):
            sys.exit(0)                     # said once; the call is the session's

        body = "\n".join(f"  · [{tag}] {line[:300]}" for tag, line in hits)
        log(f"BLOCK hits={len(hits)}")
        print(
            "DRIFT ASSERTION — you are about to tell Joe that a present state is "
            "WRONG, and the decision log has something on this subject.\n\n"
            "This is the most frequent failure class on record here, running since "
            "2026-08-04, most of them caught by him rather than by a session, and it "
            "always has the same shape: a current artifact read accurately, the "
            "decision behind it left unread. `standing-context` returns the live "
            "count if you want it. "
            "The write-door version of this check only fires when a record gets "
            "filed — by then the claim has usually already reached him in chat, "
            "which is what this door is for.\n\nMatching rulings, newest first:\n\n"
            + body +
            "\n\nRead them before this reply goes out. If one explains the state "
            "you are calling broken, the state was CHOSEN and the finding is either "
            "nothing or a stale prompt to correct instead. If none apply, say so "
            "and send it — a drift claim with no governing ruling is usually real. "
            "This will not stop you twice on the same words.",
            file=sys.stderr)
        sys.exit(2)
    except Exception as exc:
        log(f"ALLOW(internal-error) {exc}")
        sys.exit(0)


if __name__ == "__main__":
    main()
