#!/usr/bin/env python3
"""ledger-sweep.py — a Stop hook that notices the partner said something
ledger-worthy and no ledger entry followed.

WHY THIS EXISTS, and it is the third attempt at the same problem.

Joe's development kit (rules 21-23) produced ZERO entries in the weeks it was
active. Diagnosis one: the rules named a ROLE, not events. Fix one: rule
bbffc139, five explicit triggers plus a visible count. That rule was written at
02:05 on 2026-08-03 and did not fire ONCE in the hour that followed, across four
qualifying exchanges, until Joe asked directly whether it had.

So diagnosis one was incomplete. Triggers tell a session WHAT to notice; they do
not make it notice. An audit signal says to state a count; nothing makes it get
stated. BOTH HALVES STILL RUN ON MEMORY, and memory is what failed twice.

The only control that worked unassisted all night was a hook: the Bash guard
blocked a destructive command whether anyone remembered it or not. So this is
the ledger rule expressed as a mechanism instead of an intention.

HOW IT WORKS. On Stop (the session finished a response), read the transcript,
find the most recent human turn, and test it against the five triggers from rule
bbffc139. If it matches and NO log-decision or teach call has occurred since
that turn, inject a reminder naming which trigger matched and quoting the line.

WHY IT NAMES THE MATCHED LINE rather than nagging generically: a reminder that
says "consider logging something" is ignorable and gets ignored. One that says
"he corrected you, here are his words, that is override-ledger trigger 1" is a
specific accusation with evidence, which is much harder to skip past.

DELIBERATELY CONSERVATIVE. It only fires on the LAST human turn, so a long
exchange produces at most one reminder per response rather than a backlog. It
stays silent when a ledger write already happened. Patterns are tuned toward
missing real events rather than crying wolf, because a hook that fires on every
turn trains the eye to skip it, which is the exact failure the audit found in
health checks that report everything at one severity.

FAILS OPEN AND SILENT on any error: unreadable transcript, unknown format,
anything. It is a nudge on top of an existing rule and must never be the reason
a response appears to fail. Logged to out/hook-guard.log for auditing.
"""

import json
from datetime import datetime, timezone
import os
import re
import sys

LOG = os.path.expanduser("~/carr-system/out/hook-guard.log")
LEDGER_VERBS = ("log-decision", "teach")

# The five triggers from rule bbffc139, in the order that rule lists them.
TRIGGERS = [
    ("1 · OVERRULED A RECOMMENDATION", re.compile(
        r"(\bactually,?\s|\bno,?\s+(i|you|that|it|lets|let's)\b|\bi disagree\b|"
        r"\bthat's not\b|\bthats not\b|\bi'd argue\b|\bid argue\b|\binstead of\b|"
        r"\bwhy (don't|dont|not)\s+(you|we)\b|\byou should\b|\bmaybe you should\b|"
        r"\bi'd say\b|\bid say\b|\brather than\b|\bpush back\b)", re.I)),
    ("2 · STATED A PREDICTION OR CONFIDENT CALL", re.compile(
        r"(\bin my opinion\b|\bi think\b|\bi believe\b|\bi bet\b|\bmy guess\b|"
        r"\bi predict\b|\bit'll\b|\bit will\b|\bthat'll\b|\bi'm confident\b|"
        r"\bim confident\b|\bi would argue\b|\bmy take\b)", re.I)),
    ("3 · COMPRESSED SOMETHING INTO A FRAMING", re.compile(
        r"(\bto me,?\s|\bthink of it\b|\bit's really\b|\bits really\b|"
        r"\bthe way i see\b|\bwhat that means\b|\bin other words\b|"
        r"\bthat's basically\b|\bthats basically\b|\bhow does that sound\b)", re.I)),
    ("4 · RULED ON HOW SOMETHING IS STRUCTURED", re.compile(
        r"(\blets? (do|go|use|make|build|split|keep|move)\b|\bwe (should|need to)\b|"
        r"\bgo with\b|\bfrom now on\b|\bgoing forward\b|\balways\b|\bnever\b|"
        r"\bmake (it|that|this)\b|\bi want\b)", re.I)),
    ("5 · CORRECTED A FACT OR A PREMISE", re.compile(
        r"(\bthat's wrong\b|\bthats wrong\b|\bincorrect\b|\bnot true\b|"
        r"\bi already\b|\bi did\b|\bi meant\b|\byou (missed|forgot|didn't|didnt)\b|"
        r"\bpretty sure\b|\bare you sure\b|\bdid you\b)", re.I)),
    # TRIGGER 6 exists because the first version of this hook MISSED the two
    # sharpest interventions of 2026-08-02, and both had the same shape.
    # "so everything is addressed in this session that is supposed to be right?"
    # and "well if you detected the default failure mode protect against it".
    # Neither says "you are wrong". Both are questions or conditionals that make
    # the error unavoidable, and both landed harder than any direct contradiction
    # that night. Patterns built around "actually" / "no," / "i disagree"
    # structurally cannot see that, which is a fact about how THIS partner
    # corrects rather than a gap in the taxonomy. Socratic challenge is still an
    # override and belongs in the ledger.
    ("6 · CHALLENGED A CLAIM, OFTEN AS A QUESTION", re.compile(
        r"(\bare you sure\b|\bis that (right|true|correct|everything)\b|"
        r"\bright\?|\bdid you (actually|really|check|verify|log|do)\b|"
        r"\bhow do you know\b|\bwhat about\b|\bshouldn'?t (you|we|it)\b|"
        r"\bcouldn'?t (you|we)\b|\bwhy (did|didn'?t|not|wouldn'?t)\b|"
        r"\bwell if\b|\bif you\b.{0,60}\b(then|protect|fix|do)\b|"
        r"\bso (what|why|everything|whats|is|does)\b|"
        r"\bhave you\b|\bhopefully for the last time\b|\bever\b.{0,20}\?)", re.I)),
    # TRIGGER 7 exists because triggers 1-6 missed FIVE of seven qualifying
    # turns on 2026-08-03, and every miss had the same shape: hedged confidence.
    # "I'll argue you on drive", "I'm almost positive we already addressed all
    # these D's", "I thought we had switched to refreshing files every 30
    # minutes?", "I'm fairly certain the other session is done." Joe corrects by
    # hedging, never by contradicting, so patterns built around assertive
    # openers ("actually", "no,", "i disagree", "i'd argue") structurally cannot
    # see him. That is the same class of blindness that produced trigger 6, one
    # register further out.
    #
    # THE HEDGE IS NOT LOW CONFIDENCE AND MUST NOT BE READ AS ONE. On the night
    # this was written he was right FIVE times out of five while the session was
    # wrong five out of five: the D-rulings already settled, the Drive-to-git
    # plan, the launchd refresh job, the other session's state, and the file
    # conversions. This register carries his HIGHEST-value interventions, not
    # his softest. It also matches the posture he holds himself to in rule
    # 95e76c1f, "confidence without cockiness" — so a hook that only sees blunt
    # contradiction is reading for a partner this system's own doctrine says he
    # is not.
    #
    # The final clauses catch the flat correction carrying no hedge at all
    # ("we just converted X tonight"), which trigger 5 missed because it looked
    # for "i already" and never for "we already".
    ("7 · HEDGED-CONFIDENT RECALL OR CORRECTION", re.compile(
        r"(\bi'?ll argue\b|\balmost positive\b|\bfairly (certain|sure|positive)\b|"
        r"\bpretty (certain|positive)\b|\bi thought (we|you|it|that|this|there)\b|"
        r"\bi'?m remembering\b|\bif i recall\b|\biirc\b|\bi could be wrong\b|"
        r"\bdidn'?t we\b|\bweren'?t we\b|\bwasn'?t (it|that)\b|\bwe already\b|"
        r"\bwe (just|already) (did|have|had|converted|addressed|switched|"
        r"solved|built|found|determined|disassembled|migrated|moved|retired|"
        r"flipped|wired|shipped|finished|ran)\b|\bwe found\b|\bwe determined\b)", re.I)),
]


def log(msg):
    """Timestamped and self-identifying. Before 2026-08-03 no hook stamped its
    lines, so out/hook-guard.log could not answer "when did this fire" or even
    "which gate wrote this" — 51 lines with test fixtures indistinguishable from
    production denials. A log you cannot read chronologically is an artifact,
    not a check."""
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        ts = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        with open(LOG, "a") as fh:
            fh.write(f"{ts} ledger-sweep {msg.rstrip()}\n")
    except Exception:
        pass


def read_tail(path, limit=400):
    """Last `limit` JSONL records, oldest first. Tolerates partial lines."""
    out = []
    with open(path, "r", errors="replace") as fh:
        for line in fh.readlines()[-limit:]:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    return out


def human_text(rec):
    """Return the human's text for a user turn, else None. Format-tolerant."""
    if rec.get("type") not in ("user", "human"):
        return None
    msg = rec.get("message") or rec
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [b.get("text", "") for b in content
                 if isinstance(b, dict) and b.get("type") == "text"]
        return "\n".join(p for p in parts if p)
    return None


def mentions_ledger_verb(rec):
    blob = json.dumps(rec)
    return any(v in blob for v in LEDGER_VERBS)


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    try:
        # Do not re-fire while the session is already being stopped repeatedly.
        if payload.get("stop_hook_active"):
            sys.exit(0)
        path = payload.get("transcript_path")
        if not path or not os.path.exists(path):
            sys.exit(0)

        recs = read_tail(path)
        # Walk backwards to the last human turn; note anything after it.
        last_human_idx, last_human = None, None
        for i in range(len(recs) - 1, -1, -1):
            t = human_text(recs[i])
            # Skip harness-injected system reminders and tool results.
            if t and not t.lstrip().startswith(("<system-reminder", "[SYSTEM")):
                last_human_idx, last_human = i, t
                break
        if last_human is None:
            sys.exit(0)

        # Already logged since that turn? Then the rule ran. Stay quiet.
        if any(mentions_ledger_verb(r) for r in recs[last_human_idx + 1:]):
            sys.exit(0)

        hits = [name for name, pat in TRIGGERS if pat.search(last_human)]
        if not hits:
            sys.exit(0)

        quote = " ".join(last_human.split())[:220]
        msg = ("LEDGER SWEEP — the partner's last turn matched "
               + ("triggers " if len(hits) > 1 else "trigger ")
               + "; ".join(hits)
               + " from rule bbffc139, and no log-decision or teach call has "
                 "followed it.\n\nHis words: \"" + quote + "\"\n\n"
               "Decide deliberately, do not skip by default. If it belongs in a "
               "ledger, log it NOW with log-decision (override, calibration, or "
               "his framing) — logging is free and binds nobody. If it genuinely "
               "does not, that is a fine answer and needs no action. This rule "
               "produced ZERO entries across the weeks it was active, and then "
               "failed again within an hour of being rewritten with explicit "
               "triggers, which is why it is a hook now and not a reminder.")
        log(f"LEDGER-SWEEP {hits} :: {quote[:120]}")
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "Stop",
                "additionalContext": msg,
            }
        }))
        sys.exit(0)
    except Exception as exc:
        log(f"ALLOW(internal-error) ledger-sweep {exc}")
        sys.exit(0)


if __name__ == "__main__":
    main()
