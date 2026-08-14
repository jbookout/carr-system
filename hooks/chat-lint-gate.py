#!/usr/bin/env python3
"""chat-lint-gate.py — the writing rules reach the surface they were taught
about (rules 5be2f462 + 3a9dbafd), at the only moment that surface exists.

THE GAP THIS CLOSES. tools/writing-lint.py has banned the contrast-reframe
shapes and the LLM-tell vocabulary for months, and lint-gate.py enforces that
on every prospect-visible FILE write. But rule 5be2f462's actual binding scope
is INTERNAL dialogue — the assistant talking to Joe — and no hook ever touched
chat text, because chat text only exists at Stop. So the constructions the
system refuses to write into a client email arrived in Joe's terminal daily.

TWO CHECKS, both on the turn's final assistant message:

  1. WRITING (5be2f462): exactly the HARD ids the audit row names — vocab,
     contrast-reframe, contrast-reframe-split — reusing tools/writing-lint.py's
     own RULES table so chat and client surfaces can never drift apart.
     contrast-compressed stays OFF this surface on purpose: 'X, not Y' is
     REVIEW severity because a genuine correction of fact takes that shape,
     and honest technical chat corrects facts constantly. The em-dash ban
     stays prospect-only for the same reason (house style uses them).

  2. BARE IDS (3a9dbafd): an 8-hex rule id, or a 'loop #N' / 'migration #N'
     reference, must ride in a sentence that carries plain words — the gloss.
     A bare id makes Joe decode it; an id beside its meaning costs nothing.
     Guards, each from a real false-positive shape: fenced code is exempt
     (git output is hex), all-digit tokens are exempt (20260814 is a date),
     and the gloss test is per-sentence so a glossed id passes untouched.

NEVER LOOPS: stop_hook_active short-circuits, same as every Stop gate here.
FAILS OPEN on any internal error, and if writing-lint cannot be imported the
writing half is skipped rather than the session wedged. Audit rows share
out/conduct-gate.jsonl; fixtures (session 'selftest') do not count.

Fixtures: ops/chat-lint-gate-selftest.py.
"""
import importlib.util
import json
import os
import re
import sys
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(REPO, "out", "conduct-gate.jsonl")
DEBUG = os.path.join(REPO, "out", "conduct-gate.log")

CHAT_RULE_IDS = {"vocab", "contrast-reframe", "contrast-reframe-split"}

# 8-hex with at least one letter (all-digit is a date or a number, not an id),
# and the two numbered-artifact spellings partners actually receive.
HEX_ID = re.compile(r"\b[0-9a-f]{8}\b")
NUM_REF = re.compile(r"\b(?:loop|migration)\s*#\d{2,4}\b", re.I)
# A sentence's plain words. Five is the floor because 'Close loop and loop'
# has four and is exactly the undecodable shape the rule bans.
WORD = re.compile(r"[A-Za-z]{3,}")
MIN_GLOSS_WORDS = 5


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def dlog(msg):
    try:
        os.makedirs(os.path.dirname(DEBUG), exist_ok=True)
        with open(DEBUG, "a") as fh:
            fh.write(f"{now()}  chat-lint {msg}\n")
    except Exception:
        pass


def audit(record):
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


def strip_fences(text):
    return re.sub(r"```.*?```", " ", text, flags=re.S)


def writing_rules():
    """The filtered HARD rules, from writing-lint's own table. One source of
    truth: a ban added there reaches chat with nobody editing this file."""
    try:
        spec = importlib.util.spec_from_file_location(
            "writing_lint", os.path.join(REPO, "tools", "writing-lint.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return [(rid, pat, what, fix) for rid, sev, pat, what, fix in mod.RULES
                if rid in CHAT_RULE_IDS and sev == "HARD"], mod.mask
    except Exception as exc:
        dlog(f"writing-lint import failed ({exc}); writing half skipped")
        return [], lambda t: t


def sentences(text):
    for chunk in re.split(r"(?<=[.!?])\s+|\n+", text):
        if chunk.strip():
            yield chunk.strip()


def bare_id_findings(prose):
    found = []
    for sentence in sentences(prose):
        ids = [t for t in HEX_ID.findall(sentence) if not t.isdigit()]
        ids += NUM_REF.findall(sentence)
        if not ids:
            continue
        rest = sentence
        for t in ids:
            rest = rest.replace(t, " ")
        words = WORD.findall(rest)
        if len(words) < MIN_GLOSS_WORDS:
            found.append(("bare-id", ", ".join(ids[:4]),
                          sentence[:90]))
    return found


def scan(assistant):
    prose = strip_fences(assistant)
    rules, mask = writing_rules()
    masked = mask(prose)
    findings = []
    for rid, pat, what, fix in rules:
        m = re.search(pat, masked, re.I)
        if m:
            quote = prose[max(0, m.start() - 20):m.end() + 20].replace("\n", " ")
            findings.append((rid, quote.strip()[:90], fix))
    for rid, ids, quote in bare_id_findings(prose):
        findings.append((rid, quote,
                         f"say what {ids} IS in the same sentence — the id "
                         "rides with its gloss, or stays out of the message"))
    return findings


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

        assistant = ""
        for rec in read_tail(path):
            t = text_of(rec, ("assistant",))
            if t and t.strip():
                assistant = t.strip()
        if not assistant:
            sys.exit(0)

        findings = scan(assistant)
        if not findings:
            sys.exit(0)

        audit({"ts": now(), "hook": "chat-lint-gate",
               "classes": sorted({f[0] for f in findings}),
               "session": payload.get("session_id"),
               "excerpt": findings[0][1][:200]})
        lines = [
            "CHAT LINT — this reply breaks writing rules that bind chat "
            "(5be2f462 banned constructions / 3a9dbafd bare ids). Revise the "
            "reply, keeping its content:",
            ""]
        for rid, quote, fix in findings[:6]:
            lines.append(f"  [{rid}] …{quote}…")
            lines.append(f"      fix: {fix}")
        lines.append("")
        lines.append("Vocab and contrast-reframe bans are the same ones every "
                     "client surface already enforces; a bare 8-hex id or "
                     "'loop #N' needs its plain-language gloss in the same "
                     "sentence. Code fences are exempt.")
        print("\n".join(lines), file=sys.stderr)
        sys.exit(2)

    except SystemExit:
        raise
    except Exception as exc:
        dlog(f"ALLOW(internal-error) {exc}")
        sys.exit(0)


if __name__ == "__main__":
    main()
