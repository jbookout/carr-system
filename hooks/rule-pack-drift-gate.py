#!/usr/bin/env python3
"""rule-pack-drift-gate.py — the turn's OBSERVED WORK is diffed against the rule
packs the session actually loaded, and the gap is either recorded or reopened.

# doctrine: rule-delivery-load-layers

WHY THIS EXISTS. Scoping rule delivery is safe only while one thing stays true:
a session that wanders into work it never declared still gets that work's rules.
Rule 347a9ca6 is the law — "a session's name does not predict the work it will
do", taught after Joe pointed out that he does full system builds inside a
session called nightly-record-layer. Both 2026-08-23 council chairs made the
same requirement structural rather than hopeful:

    "A Stop gate diffs the turn's verbs and nouns against loaded packs. If
     git/CI ran and the engineering pack was not loaded, reopen and load."

Without this gate, scoping does not merely risk the old failure — it INSTALLS
it, because the boot payload would shrink on the guess that a session's declared
work is its whole work.

WHAT IT LOOKS AT. Only the current turn: every tool call the session made, the
verbs it named, and the text on both sides, from the last genuine user message
onward. Triggers come from ops/config/rule-enforcement-map.json, the same
reviewed file the database tags are compiled from, so the gate and the compiler
cannot drift into disagreeing about what a pack is for.

SHADOW FIRST, AND THE MODE IS NOT THIS FILE'S TO DECIDE. Both chairs required a
week of running the selector beside full recitation before anything is cut. That
switch lives in one place — ops.rule_delivery_policy in the database — and this
gate reads it the only way a hook can: out of the standing-context result already
sitting in the transcript. No second copy of the policy, no local flag to fall
out of sync with the verb (rule 0f38532e). If the mode cannot be established,
the gate RECORDS and does not block, because a gate that blocks on an
unestablished policy is a gate that will be removed within a week.

WHAT IT WRITES, and this is the shadow week's whole evidence base:
out/rule-delivery-shadow.jsonl, one row per turn — which packs the work implied,
which were loaded, which rules a scoped boot would have omitted, and the subset
of those the work actually needed. ops/rule-delivery-shadow-watch.py reads it
nightly, so the comparison cannot quietly stop running (the lesson of the
admission contract, which sat unmeasured in Production for months because the
only thing that could measure it was a door a human had to open).

A MISS is the row that matters: a rule this turn's work needed, that a scoped
boot would not have delivered. Enforcement flips on at zero unexplained misses.
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAP = os.path.join(REPO, "ops", "config", "rule-enforcement-map.json")
LOG = os.path.join(REPO, "out", "rule-delivery-shadow.jsonl")
CARR_PATH_MARKERS = ("/carr-system/", "/carr-system", "my drive/carr ai")
SYNTHETIC_PREFIXES = ("The following is the Codex agent history", "<environment_context>",
                      "<app-context>", "<skills_instructions>", "<permissions instructions>")


def load_packs():
    """Pack name -> compiled trigger regex, and pack name -> its rules."""
    with open(MAP, encoding="utf-8") as handle:
        data = json.load(handle)
    triggers, members = {}, {}
    for name, pack in data.get("rule_packs", {}).items():
        words = [re.escape(t) for t in pack.get("triggers", []) if str(t).strip()]
        if not words:
            continue
        # \b around a term that ends in punctuation (x.com) never matches, so the
        # boundary is only asserted where the term's own edge is a word character.
        parts = []
        for word in words:
            left = r"\b" if re.match(r"\w", word[0]) else ""
            right = r"\b" if re.match(r"\w", word[-1]) else ""
            parts.append(f"{left}{word}{right}")
        triggers[name] = re.compile("|".join(parts), re.I)
    for short, entry in data.get("rule_load_layers", {}).items():
        for name in entry.get("packs", []):
            members.setdefault(name, []).append(short)
    return triggers, members


def _content_text(content, kinds=("text", "input_text", "output_text")):
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "\n".join(str(b.get("text", "")) for b in content
                     if isinstance(b, dict) and b.get("type") in kinds)


def role_and_text(record):
    payload = record.get("payload")
    if isinstance(payload, dict) and payload.get("type") == "message":
        return payload.get("role"), _content_text(payload.get("content"))
    message = record.get("message") if isinstance(record.get("message"), dict) else record
    return message.get("role") or record.get("type"), _content_text(message.get("content"))


def serialized(record):
    values = []

    def walk(value):
        if isinstance(value, dict):
            for key, item in value.items():
                values.append(str(key))
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
        elif value is not None:
            values.append(str(value))

    walk(record)
    return "\n".join(values)


def genuine_user_task(record):
    role, value = role_and_text(record)
    if role not in {"user", "human"} or not value.strip():
        return ""
    if value.lstrip().startswith(SYNTHETIC_PREFIXES):
        return ""
    message = record.get("message") if isinstance(record.get("message"), dict) else record
    content = message.get("content")
    if isinstance(content, list) and content and all(
            isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
        return ""
    return value


def current_turn(records):
    for index in range(len(records) - 1, -1, -1):
        if genuine_user_task(records[index]):
            return records[index:]
    return records


def delivery_state(records):
    """What this session has loaded, across every standing-context call it made.

    Returns (mode, declared_packs, would_omit). A session that never called the
    verb yields (None, [], []) — nothing to compare against and nothing to block.

    PACKS ACCUMULATE; THEY DO NOT REPLACE. The council's word for it is monotonic:
    entering another domain ADDS its pack and never subtracts an earlier one. That
    is also what stops a false miss here — a session that loads the engineering
    pack, then calls standing-context again bare to look a rule up by id, has not
    unloaded anything, and reading only the latest call would say it had. The mode
    and the omission list come from the LATEST call, because those describe the
    policy and the payload as they stand now.
    """
    mode, declared, omit = None, set(), []
    for record in records:
        if "rule_delivery" not in serialized(record):
            continue
        found = _find_delivery(record)
        if found:
            mode, packs, omit = found
            declared.update(packs)
    return mode, sorted(declared), omit


def _find_delivery(value):
    """Depth-first hunt for a rule_delivery object, whatever wrapper it arrived in."""
    if isinstance(value, dict):
        block = value.get("rule_delivery")
        if isinstance(block, dict) and "mode" in block:
            return (block.get("mode"),
                    [str(p) for p in block.get("declared_packs", []) or []],
                    [str(r) for r in block.get("would_omit", []) or []])
        for item in value.values():
            found = _find_delivery(item)
            if found:
                return found
        return None
    if isinstance(value, list):
        for item in value:
            found = _find_delivery(item)
            if found:
                return found
        return None
    if isinstance(value, str) and "rule_delivery" in value:
        try:
            return _find_delivery(json.loads(value))
        except (TypeError, ValueError):
            return None
    return None


def work_text(record):
    """What the SESSION did and said this turn — never what a tool said back.

    Tool RESULTS are excluded on purpose, and the reason is not tidiness. The
    standing-context payload itself carries the pack index, which is a list of
    every pack's triggers; scanning results would make one boot call fire every
    pack in the catalog and the gate would demand all of them on every turn. A
    directory listing or a search result has the same shape of problem: the
    nouns in it are the world's, not the session's. What this gate is
    adjudicating is the work the session CHOSE to do — its tool calls and its
    prose — which is exactly what rule 347a9ca6 says to judge by.
    """
    parts = []
    payload = record.get("payload")
    if isinstance(payload, dict) and payload.get("type") == "custom_tool_call":
        parts.append(serialized(payload))
    message = record.get("message") if isinstance(record.get("message"), dict) else record
    role = message.get("role") or record.get("type")
    content = message.get("content")
    if isinstance(content, str):
        if role in {"user", "human", "assistant"}:
            parts.append(content)
    elif isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            kind = block.get("type")
            if kind == "tool_use":
                parts.append(str(block.get("name", "")))
                parts.append(serialized(block.get("input")))
            elif kind in {"text", "input_text", "output_text"}:
                parts.append(str(block.get("text", "")))
    return "\n".join(p for p in parts if p)


def observed_packs(turn, triggers):
    """Which packs THIS TURN's work implies, with the words that fired each."""
    text = "\n".join(work_text(record) for record in turn)
    hits = {}
    for name, pattern in triggers.items():
        found = sorted({m.group(0).lower() for m in pattern.finditer(text)})
        if found:
            hits[name] = found[:6]
    return hits


def evaluate(records, triggers, members):
    turn = current_turn(records)
    mode, declared, omit = delivery_state(records)
    hits = observed_packs(turn, triggers)
    needed = sorted(hits)
    loaded = sorted({str(p).strip().lower() for p in declared})
    missing = [p for p in needed if p not in loaded]
    omitted = {str(r).lower() for r in omit}
    # THE MISS: a rule this turn's work needed that a scoped boot would not have
    # handed over. Everything else in this row is context for reading it.
    missed = sorted({short for pack in missing
                     for short in members.get(pack, [])
                     if short.lower() in omitted})
    return {"mode": mode, "needed": needed, "loaded": loaded, "missing": missing,
            "triggers": hits, "would_omit_count": len(omitted), "missed_rules": missed}


def audit(row):
    if row.get("session") == "selftest":
        return
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    except Exception:
        pass


def payload_is_carr(payload):
    cwd = (payload.get("cwd") or payload.get("working_directory")
           or payload.get("workingDirectory"))
    if not isinstance(cwd, str) or not cwd.strip():
        return True
    normalized = cwd.replace("\\", "/").lower()
    repo = REPO.replace("\\", "/").lower().rstrip("/")
    return (normalized == repo or normalized.startswith(repo + "/")
            or any(marker in normalized for marker in CARR_PATH_MARKERS))


def block_reason(result):
    packs = ", ".join(result["missing"])
    words = "; ".join(f"{p}: {', '.join(result['triggers'][p])}" for p in result["missing"])
    return ("RULE PACK DRIFT — this turn did work you did not load the rules for. "
            f"Missing pack(s): {packs}. What named them: {words}. "
            f"Call standing-context with packs:[{', '.join(repr(p) for p in result['missing'])}] "
            "and read what comes back before you finish. A session's name does not "
            "predict its work (rule 347a9ca6), which is exactly why this fires on "
            "what you DID rather than on what you said you would do.")


def main():
    payload = {}
    try:
        payload = json.load(sys.stdin)
        if payload.get("stop_hook_active") or not payload_is_carr(payload):
            return 0
        path = payload.get("transcript_path") or payload.get("transcriptPath")
        if not path or not os.path.exists(path):
            return 0
        with open(path, errors="replace") as handle:
            records = [json.loads(line) for line in handle if line.strip()]
        triggers, members = load_packs()
        result = evaluate(records, triggers, members)
        row = {"ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
               "hook": "rule-pack-drift-gate",
               "session": payload.get("session_id") or payload.get("sessionId"),
               **result}
        # A turn that implied no pack and loaded none is the ordinary case and
        # writing a row per turn for it would bury the rows that matter.
        if result["needed"] or result["loaded"]:
            audit(row)
        if result["mode"] != "enforced" or not result["missing"]:
            return 0
        print(json.dumps({"decision": "block", "reason": block_reason(result)}))
        return 0
    except Exception as exc:
        # FAIL OPEN, DELIBERATELY, AND SAY SO IN THE LOG. Its siblings fail
        # closed because they guard a claim that would otherwise go out wrong.
        # This one guards DELIVERY: a bug here that blocked every turn would
        # stop all work over a check that has not cut a single rule yet, and the
        # first fix anyone reached for would be to uninstall it.
        audit({"ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
               "hook": "rule-pack-drift-gate",
               "session": payload.get("session_id") or payload.get("sessionId"),
               "error": type(exc).__name__, "detail": str(exc)[:200]})
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
