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

# ── an ask to open a file carries a clickable link (rule 8c1e6057) ──────────
# Joe: "in the future if you need me to edit a file - always include the link to
# the file like you have above. that was way easier."
#
# TWO HALVES, both mechanical: an ask verb pointed AT THE READER, and a file
# path that is not already a markdown link. Neither needs the meaning of the
# sentence, which is what keeps this a predicate (rule 5e89c211).
#
# THE FALSE POSITIVE THAT WOULD KILL IT is the ordinary report. Session messages
# name paths constantly and a check that fired on every mention would be muted
# within a day, so a MENTION IS NOT AN ASK — the verb must be present.
#
# THE VERB ALONE IS NOT THE SIGNAL, and finding that out is what made this
# usable. A sweep of 121 real session-authored messages flagged ten, every one
# the same shape: the verb was never an ask at all — "fails open", "both open
# with", "--check is red", "index-level check", "a type-check class". Chasing
# those with more spellings is endless, because English reuses these words as
# nouns and particles constantly.
#
# WHAT ACTUALLY DISTINGUISHES AN ASK IS WHO IT IS AIMED AT. The rule says a
# partner is ASKED, so the sentence must be addressed to him: an imperative
# opening the sentence, or a second-person or politeness marker. That is a
# predicate, and it took the ten false positives to zero without weakening any
# of the six real shapes.
ASK_VERB = r"""(?:open|edit|review|check|sign(?:\s+off)?|approve|look\s+at
    |take\s+a\s+look|have\s+a\s+look|glance\s+at|read\s+through|fill\s+(?:in|out))"""

# (a) The sentence OPENS with the ask, optionally behind a courtesy or connective.
IMPERATIVE_ASK = re.compile(
    # [^\w-] rather than \W for the leading run: a CLI flag is not an
    # imperative. "bin/schema-snapshot.sh --check is red" was the last false
    # positive left in the 201-message sweep, because "--" is non-word.
    r"^[^\w-]*(?:please\s+|also\s+|then\s+|and\s+|now\s+|first\s+)*" + ASK_VERB + r"\b",
    re.I | re.X)

# (b) Or it is pointed at him in so many words.
ADDRESSED = re.compile(r"\b(?:you|your|please)\b", re.I)
ANY_ASK_VERB = re.compile(r"\b" + ASK_VERB + r"\b", re.I | re.X)

# First person kills the ask: "I opened x", "let me check y", "I will review z"
# are the session narrating its OWN work and asking nobody for anything.
SELF_NARRATION = re.compile(
    r"\b(?:i|we)\s+(?:just\s+|already\s+|then\s+)?(?:will\s+|am\s+going\s+to\s+)?"
    r"(?:open|edit|review|check|look|read|opened|edited|reviewed|checked|looked|read)"
    r"|\blet\s+me\s+(?:open|edit|review|check|look|read)", re.I)

# A path with a directory separator or a document-ish extension. Deliberately
# NOT bare words: 'check the lease' must not read as a filename.
FILE_PATH = re.compile(
    r"(?<![\w/\[(])"
    r"(?:[\w.\-]+/)+[\w.\-]+\.[A-Za-z0-9]{1,5}"          # a/b/c.ext
    r"|(?<![\w/\[(])[\w.\-]+\.(?:docx?|xlsx?|pptx?|pdf|csv|tsv|md|json|ya?ml|"
    r"py|js|ts|sh|sql|ics|txt)\b",                        # bare name.ext
    re.I)

# Already clickable: the path sits inside a markdown link target.
MD_LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")


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


def unlinked_file_ask_findings(prose):
    """Asks to open a file where the path is not clickable (rule 8c1e6057).

    Sentence by sentence, because the ask and the path must travel together —
    a link three paragraphs away is not the one he clicks.
    """
    found = []
    for sentence in sentences(prose):
        # THE PATHS COME OUT BEFORE THE VERB SEARCH. A filename can contain an
        # ask verb — ops/map-row-evidence-check.py carries "check" — and reading
        # that as an imperative flagged a plain report of work already done.
        paths = FILE_PATH.findall(sentence)
        without_paths = sentence
        for p in paths:
            without_paths = without_paths.replace(p, " ")

        asked = IMPERATIVE_ASK.search(without_paths) or (
            ADDRESSED.search(without_paths) and ANY_ASK_VERB.search(without_paths))
        if not asked:
            continue
        if SELF_NARRATION.search(without_paths):
            continue
        linked = " ".join(MD_LINK.findall(sentence))
        bare = [p for p in paths if p not in linked]
        if bare:
            found.append(("unlinked-file-ask", ", ".join(bare[:3]),
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
    for rid, paths, quote in unlinked_file_ask_findings(prose):
        findings.append((rid, quote,
                         f"make {paths} a clickable markdown link — he is being "
                         "asked to open it, and a path in prose is not clickable"))
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
            "(5be2f462 banned constructions / 3a9dbafd bare ids). Send ONLY THE "
            "CORRECTED LINES, not the whole reply again — Joe has already read it, "
            "and rule 1d50a3bb makes a full restatement a second charge for text "
            "he owns. Fix these:",
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
