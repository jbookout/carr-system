#!/usr/bin/env python3
"""conduct-stop-gate.py — the Stop HARD GATE for session conduct.

WHY THIS EXISTS, IN ONE PARAGRAPH. On 2026-08-09 Joe re-issued three
instructions that were ALREADY ACTIVE TAUGHT RULES, recited to the session at
boot, and violated anyway:
  - 14e0408b "stop asking me to decide everything"          (taught 2026-08-02)
  - e313a3ca "no more asking me to run terminal commands"   (taught 2026-08-03)
  - 179be4b8 + 647f843d "no more adding loops"              (taught twice)
His words that sitting: "you have become lazy and are trying to offload many
tasks and decisions to me... If you were a real COO, I would have replaced you
by now for being so insecure with decision making." A prose rule in a context
window is ADVISORY no matter how forcefully worded or how often recited. The
only rule of the three that ever changed behaviour is 647f843d, and it changed
behaviour because it got CODE: a verb refusal plus a check constraint. This
hook is that same move applied to the other two.

THE CHANNEL PROBLEM, STATED HONESTLY. "Asking Joe to decide" and "handing Joe a
command to paste" have NO TOOL CALL. They are free prose in an assistant turn.
PreToolUse cannot see them; there is no PreAssistantMessage deny in this
product. So generation-time prevention is IMPOSSIBLE here, and this file does
not pretend otherwise. What IS possible — live-verified on this machine
2026-08-09, see VERIFICATION below — is that a Stop hook returning
{"decision":"block","reason":...} BLOCKS the session from ending and the model
then obeys the reason text. So the honest guarantee is:

    NOT "impossible to say"  —  but "impossible to LEAVE SAID and walk away".

The session is forced back to work and must replace the offload with the
finished thing before it is allowed to stop. That is the real ceiling on a
prose channel, it is a large improvement over a nudge, and calling it anything
stronger would be the "hardness cosplay" the review council explicitly warned
against.

VERIFICATION (rule 97326357 — a claim about a surface becomes doctrine only
after a live test FROM that surface). Probe run 2026-08-09 in a scratch dir: a
Stop hook emitting {"decision":"block","reason":"output the token ZZQQ7"}
against a session instructed to say "HELLO" produced "ZZQQ7", and the hook
fired twice (block, then allow). Blocking Stop works and the injected reason is
obeyed. Re-run that probe before trusting this file on any new Claude Code
build.

WHAT IT CATCHES, and why each pattern class is here:
  (1) DECISION OFFLOAD — "should I", "would you like me to", "do you want me
      to", "which do you prefer", "let me know if", option menus addressed to
      the partner. Rule 14e0408b + aa411351.
  (2) COMMAND HANDOFF — fenced bash/sh/zsh/console blocks, "run this", "paste
      this into Terminal", or a bare line starting with a command the session
      already holds permission to run. Rule e313a3ca.
  (3) SOFT WAIT — "I'll hold here until you weigh in", "parked pending your
      preference", "confirm before I proceed". The review council flagged this
      as the #1 six-week bypass: pressure selects for offload phrased OUTSIDE
      a question mark. Catching only "?" loses within weeks.

THE ESCAPE HATCHES, deliberately narrow and all STRUCTURAL rather than
vibes-based, so this gate never blocks legitimate work:
  - The partner ASKED. If the human's own last turn requested a command, a
    recommendation, or a choice ("show me the command", "what are my options",
    "which would you pick"), the corresponding class is exempt for that turn.
    A session cannot grant itself this; it comes off the human's keystrokes.
  - A PROTECTED-CLASS question. Client-facing, public-facing, money and
    irreversible decisions are Joe's BY RULE (aa411351) and must still reach
    him. Detected by external vocabulary — client, landlord, listing agent,
    LOI, send, publish, post, spend, delete. Those turns pass.
  - The gate already fired this turn (`stop_hook_active`) — never loop.

FAILS OPEN on every error. A conduct gate that wedges a session is worse than
the conduct it prevents; the egress guard fails CLOSED because it protects
against exfiltration, this one fails OPEN because it protects against rudeness.

AUDIT SIGNAL (the thing the rule-shape gate demands, and the reason the
development-kit ledgers died silently): every fire appends one JSON line to
out/conduct-gate.jsonl. Zero fires and zero instrumentation look identical
without it. `run.sh health` reads the count; the return-brief prints it.
"""
import json
import os
import re
import sys
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(REPO, "out", "conduct-gate.jsonl")
DEBUG = os.path.join(REPO, "out", "conduct-gate.log")

# PATTERNS AND EXEMPTIONS LIVE IN conduct_patterns.py — shared verbatim with
# hooks/escalation-gate.py, which catches the same behaviour one moment earlier
# (as an AskUserQuestion tool call rather than as prose). Two moments, one rule,
# one copy.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from conduct_patterns import (  # noqa: E402
    OFFLOAD, SOFT_WAIT, FENCE, BARE_FENCE_CMD, HANDOFF_PROSE,
    HUMAN_WANTS_COMMAND, HUMAN_WANTS_CHOICE, PROTECTED,
)


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def dlog(msg):
    try:
        os.makedirs(os.path.dirname(DEBUG), exist_ok=True)
        with open(DEBUG, "a") as fh:
            fh.write(f"{now()}  {msg}\n")
    except Exception:
        pass


def audit(record):
    """One JSON line per fire. This is the audit signal — without it a gate
    that never fires and a gate that is not installed look identical.

    FIXTURES DO NOT COUNT. The selftests spawn this hook for real, so without
    this skip they write real-looking rows into the ledger — 169 of them on the
    first run, which would have made the fire count meaningless the moment
    anyone tried to read it. A metric its own test suite inflates is not a
    metric (rule 590b11e1: no metric without a bound action)."""
    if record.get("session") == "selftest":
        return
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a") as fh:
            fh.write(json.dumps(record) + "\n")
    except Exception:
        pass


def read_tail(path, limit=400):
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


def text_of(rec, kinds):
    if rec.get("type") not in kinds:
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


def is_harness_injected(rec, text):
    """System-reminders, task notifications and tool results are NOT the
    partner's keystrokes. ledger-sweep.py learned this the hard way on
    2026-08-05/06 when it quoted a task-notification block as 'his words'."""
    origin = (rec.get("origin") or {})
    if isinstance(origin, dict) and origin.get("kind") not in (None, "", "user", "keyboard"):
        return True
    if rec.get("isMeta") or rec.get("isCompactSummary"):
        return True
    if not text:
        return True
    t = text.lstrip()
    for marker in ("<system-reminder>", "<task-notification>", "[SYSTEM NOTIFICATION",
                   "<local-command", "<command-name>", "Caveat:", "<user-prompt-submit-hook>"):
        if t.startswith(marker):
            return True
    return False


def strip_noise(text):
    """Remove fenced code and quoted blocks before scanning PROSE patterns, so
    a bash snippet the session legitimately shows inside a quoted rule, or a
    question inside a quoted email draft, does not trip the offload class."""
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"^\s*>.*$", " ", text, flags=re.M)
    return text


def scan(assistant, human_last):
    """Return (fired, findings). findings = list of (klass, name)."""
    findings = []
    prose = strip_noise(assistant)

    human_ok_cmd = bool(human_last and HUMAN_WANTS_COMMAND.search(human_last))
    human_ok_choice = bool(human_last and HUMAN_WANTS_CHOICE.search(human_last))
    protected = bool(PROTECTED.search(prose))

    # (2) COMMAND HANDOFF — exempt only if the human asked to see a command.
    if not human_ok_cmd:
        if FENCE.search(assistant) or BARE_FENCE_CMD.search(assistant):
            findings.append(("command_handoff", "shell_fence"))
        for name, pat in HANDOFF_PROSE:
            if pat.search(prose):
                findings.append(("command_handoff", name))

    # (1)+(3) OFFLOAD — exempt if the human asked for a choice, or if the
    # decision is genuinely a protected class that belongs to Joe by rule.
    if not human_ok_choice and not protected:
        for name, pat in OFFLOAD:
            if pat.search(prose):
                findings.append(("decision_offload", name))
        for name, pat in SOFT_WAIT:
            if pat.search(prose):
                findings.append(("soft_wait", name))

    return (len(findings) > 0), findings


REASON_HEAD = (
    "CONDUCT GATE — this turn is not finished. The session is being held open, "
    "not punished; fix the turn and it will close.\n\n"
)

REMEDY = {
    "command_handoff": (
        "COMMAND HANDOFF (rule e313a3ca). You put a shell command in front of Joe "
        "instead of running it. This machine is defaultMode:auto with ~74 allowed "
        "Bash patterns; the deny list is only sudo, diskutil, dd. RUN IT NOW with "
        "the Bash tool and report the result in one line. If it is genuinely "
        "denied, needs a credential, a browser login or Face ID, say THAT and file "
        "the blocker — but do not hand him the command. His words: \"I can't run "
        "commands every 5 minutes.\""
    ),
    "decision_offload": (
        "DECISION OFFLOAD (rules 14e0408b + aa411351). You asked Joe to decide "
        "something internal. The gate is drawn by AUDIENCE, not difficulty: only "
        "client-facing, public-facing, money and irreversible calls are his. "
        "Everything internal — schema, records, renders, jobs, config, rules, "
        "refactors, procedure — is YOURS to decide and report. If you are not "
        "confident, the required move is to RESEARCH until you are: read the code, "
        "run the query, test the surface, convene the council. Decide it, do it, "
        "then tell him in one line what you did and why."
    ),
    "soft_wait": (
        "SOFT WAIT (rules 14e0408b + aa411351). You parked the turn on Joe without "
        "asking a literal question. Waiting on him IS asking him. Same test: if "
        "the call is internal, make it and report it. If it is genuinely "
        "client-facing, public-facing, money or irreversible, say so plainly and "
        "name the one thing you need — do not leave it as an open-ended hold."
    ),
}


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception as exc:
        dlog(f"ALLOW(parse-error) {exc}")
        sys.exit(0)

    try:
        # NEVER loop. If we already blocked once this turn, let it through.
        if payload.get("stop_hook_active"):
            sys.exit(0)

        path = payload.get("transcript_path")
        if not path or not os.path.exists(path):
            dlog("ALLOW(no-transcript)")
            sys.exit(0)

        recs = read_tail(path)
        if not recs:
            sys.exit(0)

        # Last genuine human turn (for exemptions), and every assistant text
        # emitted after it (that is "this turn").
        last_human_idx, last_human = None, None
        for i in range(len(recs) - 1, -1, -1):
            t = text_of(recs[i], ("user", "human"))
            if t is None or is_harness_injected(recs[i], t):
                continue
            last_human_idx, last_human = i, t
            break

        start = (last_human_idx + 1) if last_human_idx is not None else 0
        chunks = []
        for r in recs[start:]:
            t = text_of(r, ("assistant",))
            if t:
                chunks.append(t)
        assistant = "\n\n".join(chunks).strip()
        if not assistant:
            sys.exit(0)

        fired, findings = scan(assistant, last_human)
        if not fired:
            sys.exit(0)

        classes = []
        for k, _ in findings:
            if k not in classes:
                classes.append(k)

        audit({
            "ts": now(),
            "hook": "conduct-stop-gate",
            "classes": classes,
            "patterns": [f"{k}:{n}" for k, n in findings],
            "session": payload.get("session_id"),
            "excerpt": " ".join(assistant.split())[-400:],
        })

        body = REASON_HEAD + "\n\n".join(REMEDY[c] for c in classes if c in REMEDY)
        body += (
            "\n\nDO THIS NOW, in this same turn: carry out the work you were about "
            "to hand over, then restate your closing message with the offload "
            "removed. Do not re-explain this gate to Joe and do not apologise — "
            "just deliver the finished result.\n\n"
            "This gate exists because rules 14e0408b, e313a3ca and 179be4b8 were "
            "all ACTIVE and all recited at the start of this session, and were "
            "violated anyway. Prose does not bind; this does."
        )

        dlog(f"BLOCK {classes} :: {[n for _, n in findings]}")
        print(json.dumps({"decision": "block", "reason": body}))
        sys.exit(0)

    except Exception as exc:
        dlog(f"ALLOW(internal-error) {exc}")
        sys.exit(0)


if __name__ == "__main__":
    main()
