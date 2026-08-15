#!/usr/bin/env python3
"""unread-artifact-gate.py — did this session actually OPEN the file it is
describing?

WHY THIS EXISTS. On 2026-08-14 one session filed four defects against itself and
every one had the same shape underneath: a confident, well-formed claim about an
artifact it had never opened. The evidence was always a PROXY for the artifact —

  · A GREP HIT. "tools/health-check.py writes the radar digest" came from a
    filename match. That file only WATCHES the path, in a staleness table, and
    the ten lines around the hit said so. The wrong name shipped into the one
    message whose entire purpose is telling a job's author which job to fix.
  · A RENDERING. "the write landed on a tombstone" cited three read surfaces as
    three confirmations. They are ONE collection, and the thing under suspicion
    was how records render. A single SQL read settled it the other way.
  · A HEADER, UNREAD. Five files were described as holding content that needed
    routing through verbs. Each declares "GENERATED" in its own first line.
  · AN IMPORTED FUNCTION. A ruling was called drift while the twenty lines above
    the function producing the verdict explained why the verdict had changed.

EVERY OTHER GATE HERE MISSED ALL FOUR, and not by accident. They check claims
against RULES and DECISIONS — record-home against the write law, drift-claim and
drift-assertion against the decision log, chat-lint against the writing rules.
These four statements broke no rule and contradicted no ruling. They were false,
and false in a register that reads as authoritative. A policy gate cannot catch
that, because there is no policy to catch it against.

THE ONE MECHANICAL SIGNAL THEY SHARE: the reply asserts what a file DOES, and the
session never read that file. The transcript records every tool call, so this is
a checkable question rather than a judgement about confidence.

GREP IS NOT A READ, and that is this gate's sharp edge. A grep hit proves a
string occurs somewhere. It says nothing about whether the line is a write, a
watch, a comment, a docstring or a test fixture — which is exactly the
distinction that was got wrong. So a path known only through grep counts as
UNREAD, deliberately, and the refusal says so.

WHAT COUNTS AS KNOWING A FILE: reading it (the Read tool, or cat / head / tail /
sed -n / less through Bash), or having WRITTEN it this session. Authorship is
knowledge; flagging a file the session just authored would make every build turn
noisy for nothing.

WHAT IS NOT THIS GATE'S BUSINESS: naming a path. "Committed hooks/foo.py" asserts
nothing about foo.py, and a gate that fired on it would fire on every status
report and be muted inside a day. Only a path standing as the SUBJECT OF A
BEHAVIOURAL VERB is checked — writes, reads, watches, generates, returns, owns,
contains, handles, and their kin.

IT FIRES ONCE PER CLAIM. A Stop hook that blocks the same reply forever is a
session that cannot end. The first block names the unread files; if the same
words come back, the session has been told and the call is its own.

FAILS OPEN ON EVERYTHING ELSE — no transcript, an unreadable record, a parse
error. None may strand a turn.
"""

import hashlib
import json
import os
import re
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
HOOKS = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(REPO, "out", "hook-guard.log")
STATE = os.environ.get("CARR_UNREAD_ARTIFACT_STATE") or os.path.join(
    REPO, "out", "unread-artifact")

# A path worth checking: has a directory separator and a code-ish extension.
# A bare filename is too ambiguous to attribute, and a bare word far too common.
PATH = re.compile(r"`?([A-Za-z0-9_.\-/]+/[A-Za-z0-9_.\-]+\.(?:py|sh|js|mjs|ts|sql|json|ya?ml))`?")

# The path must be the SUBJECT of one of these to count as a behavioural claim.
# Narrow on purpose: naming a file is not describing it.
CLAIMS = re.compile(
    r"^\W{0,3}(?:only\s+|never\s+|also\s+|still\s+|just\s+)*"
    r"(writes?|wrote|reads?|watch(?:es)?|generat(?:es|ed)|produc(?:es|ed)|"
    r"emit(?:s|ted)|own(?:s|ed)|contain(?:s|ed)|hold(?:s)|return(?:s|ed)|"
    r"call(?:s|ed)|handle(?:s|d)|creat(?:es|ed)|declar(?:es|ed)|say(?:s)|"
    r"is\s+the\b|is\s+a\b|does\b|never\b|only\b)", re.I)

# Genuine reads through the shell. `grep` is DELIBERATELY ABSENT — see the
# docstring; treating a grep hit as knowledge is the failure this gate exists for.
SHELL_READ = re.compile(r"\b(cat|head|tail|less|more|bat)\b|\bsed\s+-n\b|\bopen\s*\(")

READ_TOOLS = {"Read", "NotebookRead"}
WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}


def log(line):
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a") as fh:
            fh.write(f"unread-artifact-gate {line}\n")
    except Exception:
        pass


def helpers():
    sys.path.insert(0, REPO)
    from lib.loadpy import load_module_from_path
    sweep = load_module_from_path("_ua_sweep", os.path.join(HOOKS, "ledger-sweep.py"))
    chat = load_module_from_path("_ua_chat", os.path.join(HOOKS, "chat-lint-gate.py"))
    return sweep.read_tail, chat.strip_fences


def asserted_paths(prose):
    """Paths the reply says something about, as opposed to merely names."""
    found = []
    for match in PATH.finditer(prose):
        rel = match.group(1)
        tail = prose[match.end():match.end() + 40]
        if CLAIMS.match(tail) and rel not in found:
            found.append(rel)
    return found


def known_paths(records):
    """Every path this session READ or WROTE. Basenames, for loose matching:
    a reply usually writes a repo-relative path while a tool call carries an
    absolute one, and a basename comparison is the honest common denominator."""
    known = set()
    for rec in records:
        message = rec.get("message") or {}
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            name = block.get("name") or ""
            ti = block.get("input") or {}
            if not isinstance(ti, dict):
                continue
            if name in READ_TOOLS or name in WRITE_TOOLS:
                path = ti.get("file_path") or ti.get("filePath") or ""
                if path:
                    known.add(os.path.basename(path))
            elif name == "Bash":
                command = ti.get("command") or ""
                if SHELL_READ.search(command):
                    for hit in PATH.finditer(command):
                        known.add(os.path.basename(hit.group(1)))
    return known


def already_raised(text):
    digest = hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:20]
    marker = os.path.join(STATE, f"{digest}.seen")
    if os.path.exists(marker):
        return True
    try:
        os.makedirs(STATE, exist_ok=True)
        with open(marker, "w") as fh:
            fh.write("1")
    except Exception:
        pass
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

        read_tail, strip_fences = helpers()
        records = list(read_tail(path, limit=4000))
        text = ""
        for rec in records:
            message = rec.get("message") or {}
            content = message.get("content")
            if rec.get("type") != "assistant" or not isinstance(content, list):
                continue
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    if (block.get("text") or "").strip():
                        text = block["text"].strip()
        if len(text) < 40:
            sys.exit(0)

        prose = strip_fences(text)
        claims = asserted_paths(prose)
        if not claims:
            sys.exit(0)

        known = known_paths(records)
        unread = [c for c in claims if os.path.basename(c) not in known]
        if not unread:
            sys.exit(0)
        if already_raised(prose):
            sys.exit(0)

        listed = "\n".join(f"  · {p}" for p in unread[:8])
        log(f"BLOCK unread={unread[:5]}")
        print(
            "UNREAD ARTIFACT — you are describing what a file DOES, and this "
            f"session never opened it:\n{listed}\n\n"
            "On 2026-08-14 this exact shape produced four self-filed defects in "
            "one day: a grep hit read as a write site, three renderings counted "
            "as three confirmations, five files described without opening one of "
            "them, and a ruling called drift with the explanation twenty lines "
            "above the function that produced it. Every other gate here missed "
            "all four, because they check claims against RULES and these broke "
            "none — they were simply false, in a register that reads as "
            "authoritative.\n\n"
            "GREP DOES NOT COUNT AS READING, and that is the point. A hit proves "
            "a string occurs; it cannot tell a write from a watch, a comment, a "
            "docstring or a test fixture — which is the exact distinction that "
            "was got wrong.\n\n"
            "Open the file, then say what it does. If you have already read it "
            "another way and the claim is sound, send it again — this will not "
            "stop you twice on the same words.",
            file=sys.stderr)
        sys.exit(2)
    except Exception as exc:
        log(f"ALLOW(internal-error) {exc}")
        sys.exit(0)


if __name__ == "__main__":
    main()
