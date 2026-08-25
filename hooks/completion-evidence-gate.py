#!/usr/bin/env python3
"""Require evidence before a changed CARR task is claimed complete.

This is intentionally a terminal, narrow control.  It does not inspect ordinary
answers or demand a ritual after a one-file mechanical edit.  It only fires when
the current task contains a mutation/deploy or a multi-file build AND its final
message claims completion without a later read, test, render, or explicit
unverified/failed disclosure.
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(REPO, "out", "completion-evidence-gate.jsonl")
CLAIM = re.compile(r"\b(done|complete(?:d)?|verified|shipped|deployed|delivered|sent|notified|handed\s+off)\b", re.I)
# A terminal claim may disclose why fresh verification is unavailable, but a
# bare word is not evidence: "no tests failed" and "skipped none" are success
# claims, not a reason verification could not happen.
DISCLOSURE = re.compile(
    r"\b(?:unverified|not\s+verified|could\s+not\s+verify|"
    r"(?:verification|tests?|checks?)\s+(?:was|were)?\s*"
    r"(?:unavailable|failed|skipped))\b", re.I)
DISCLOSURE_REASON = re.compile(r"(?:\bbecause\b|\bdue\s+to\b|\bwhen\b|[:—-])\s*\S", re.I)
DELIVERY = re.compile(r"\b(delivered|sent|notified|handed\s+off)\b", re.I)
NEGATED_DELIVERY = re.compile(r"\b(not|never|hasn['’]t|haven['’]t|didn['’]t)\s+(?:been\s+)?(?:delivered|sent|notified|handed\s+off)\b", re.I)
RECIPIENT = re.compile(r"\b(to|with)\s+(?:Joe|Dell|[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b")
FILE_MUTATION_TOOLS = {"Write", "Edit", "MultiEdit", "apply_patch", "functions.apply_patch"}
VERIFY_TOOLS = {"Read", "Grep", "Glob", "WebFetch", "WebSearch"}
DEPLOY = re.compile(r"\b(wrangler\s+deploy|run\.sh\s+migrate\s+--apply|git\s+push)\b", re.I)
VERIFY_COMMAND = re.compile(r"\b(test|check|lint|health|diff|status|verify|smoke|render|build)\b", re.I)
PATCH_PATH = re.compile(r"^(?:\+\+\+ b/|\*\*\* (?:Update|Add) File: )(.+)$", re.M)
# The local registry contains more write verbs than a hand-maintained name list
# can safely follow.  These are the action families used by the live registry;
# deliberately narrow exceptions avoid turning known reads such as
# `review-queue` into writes merely because they share a word with review-deal.
WRITE_ACTION_PREFIXES = {
    "accept", "activate", "add", "admit", "amend", "approve", "assign", "attach", "attest", "begin", "change", "claim", "close",
    "complete", "confirm", "create", "deactivate", "decide", "decline", "detach", "end", "link",
    "disable", "log", "measure", "merge", "new", "patch", "prepare", "promote", "propose",
    "reassign", "record", "register", "release", "resolve", "retire",
    "revert", "score", "set", "stamp", "start", "teach", "triage",
    "update", "write",
}
WRITE_ACTION_EXACT = {
    "adjudicate-incident",   # partner judgment on an operational incident — severity, owner,
                              # duplicate-of. Same reasoning as its investigation sibling below:
                              # "adjudicate" stays an exact entry rather than becoming a prefix,
                              # because the two verbs it would cover are both judgment writes
                              # somebody deliberately listed.
    "adjudicate-investigation-branch",  # owner-only branch judgment write, like review-deal:
                                         # a one-off judgment verb whose first word ("adjudicate")
                                         # is not a generic write prefix
    "call-verb",             # unknown inner call is conservatively a write
    "dry-run-doctrine-gates",
    "edit-loop-header",      # updates loop_block.prose_md, like presence-lease/review-deal:
                              # a one-off verb whose first word ("edit") is not a generic
                              # write prefix and has no sibling "edit-*" verbs to justify one
    "open-campaign",
    "open-incident",             # opens or appends to an operational incident. It is additive
                                  # and it is still a write: a session that files an incident and
                                  # then reports "handled" without reading the board back has
                                  # claimed an outcome it never verified, which is the whole of
                                  # what this gate is for.
    "open-investigation",        # same "open" first-word as open-campaign; the "open" prefix
                                  # is deliberately not generalized, so this sibling gets the
                                  # same exact-entry treatment
    "open-investigation-branch",  # sibling of open-investigation, same reasoning
    "presence-lease",
    "report-problem",       # Program 6 additive Work Request capture; "report"
                              # is not generalized because report-style reads exist
    "review-and-triage",    # Program 6 human state transition; exact because
                              # other review-* actions include non-mutating reads
    "propose-ready-plan",   # Program 6 immutable plan proposal; explicit evidence coverage
    "accept-ready-plan",    # Program 6 human readiness transition; never execution
    "propose-outcome-feedback", # Program 6 immutable evidence proposal; no self-attestation
    "accept-outcome-feedback",  # Program 6 human-only observational acceptance; never completion
    "review-deal",
    "observe-memory",  # evidence-backed candidate write; exact because observe-* reads may exist
    "correct-memory",  # immutable successor write; exact transition
    "forget-memory",   # reversible suppression write; exact transition
    "issue-execution-envelope",  # persists one immutable governed execution envelope
    "transition-evaluation-case",  # human-authority append-only eval lifecycle write
    "transition-execution-environment-provider",  # human-authority provider CAS/rollback lifecycle write
}
NESTED_CARR_CALL = re.compile(r"(?:tools\.)?(mcp__carr(?:_records)?__([A-Za-z0-9_]+))")
CALL_VERB = re.compile(r"\b(?:verb|name)\s*[:=]\s*['\"]([A-Za-z0-9_-]+)['\"]", re.I)
SYNTHETIC_CODEX_USER_PREFIXES = (
    "The following is the Codex agent history",
    "<environment_context>",
    "<app-context>",
)
CARR_PATH_MARKERS = (
    "/carr-system/", "/carr-system", "my drive/carr ai", "my\\ drive/carr\\ ai",
)


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def audit(row):
    if row.get("session") == "selftest":
        return
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a") as fh:
            fh.write(json.dumps(row) + "\n")
    except Exception:
        pass


def message(rec):
    payload = rec.get("payload")
    if isinstance(payload, dict) and payload.get("type") == "message":
        return payload
    value = rec.get("message")
    return value if isinstance(value, dict) else rec


def text(rec, roles):
    msg = message(rec)
    if (msg.get("role") or rec.get("type")) not in roles:
        return ""
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(block.get("text", "")) for block in content
                         if isinstance(block, dict) and block.get("type") in
                         {"text", "input_text", "output_text"})
    return ""


def is_synthetic_user_record(rec):
    """Exclude a Codex history/environment wrapper from task-window selection.

    The first eligible text block is decisive: a genuine user instruction may
    legitimately be followed by injected environment context, so a later
    synthetic block must not erase that instruction.
    """
    msg = message(rec)
    if (msg.get("role") or rec.get("type")) not in {"user", "human"}:
        return False
    content = msg.get("content")
    if isinstance(content, str):
        return content.lstrip().startswith(SYNTHETIC_CODEX_USER_PREFIXES)
    if not isinstance(content, list):
        return False
    for block in content:
        if not isinstance(block, dict) or block.get("type") not in {"text", "input_text", "output_text"}:
            continue
        value = block.get("text")
        if isinstance(value, str):
            return value.lstrip().startswith(SYNTHETIC_CODEX_USER_PREFIXES)
    return False


def has_carr_path_marker(value):
    """Raw-string scope check; never expand a shell expression from a transcript."""
    if not isinstance(value, str):
        return False
    candidate = value.replace("\\\\", "/").lower()
    return any(marker in candidate for marker in CARR_PATH_MARKERS)


def transcript_is_carr(recs):
    """Safe fallback when a global Stop payload omits cwd.

    Only explicit CARR paths/namespaces opt the transcript in. Relative local
    edits without a cwd stay out rather than accidentally policing another app.
    """
    for rec in recs:
        name, value = tool(rec)
        if name.startswith(("mcp__carr__", "mcp__carr_records__")):
            return True
        if name == "functions.exec" and nested_carr_actions(value):
            return True
        if has_carr_path_marker(command(value)):
            return True
        if has_carr_path_marker(text(rec, {"user", "human", "assistant"})):
            return True
    return False


def payload_is_carr(payload, recs):
    """Use cwd when supplied; fall back only when the runtime truly omits it."""
    cwd = payload.get("cwd") or payload.get("working_directory") or payload.get("workingDirectory")
    if isinstance(cwd, str) and cwd.strip():
        return has_carr_path_marker(cwd)
    return transcript_is_carr(recs)


def tool(rec):
    payload = rec.get("payload")
    if isinstance(payload, dict) and payload.get("type") == "custom_tool_call":
        name = str(payload.get("name", ""))
        # Codex records nested MCP calls inside a custom `exec` input.  Keep
        # direct MCP names intact too, for a future/runtime spelling that
        # writes them directly.
        return (name if name.startswith("mcp__") else "functions." + name), payload.get("input")
    msg = rec.get("message") or rec
    content = msg.get("content")
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                return str(block.get("name", "")), block.get("input")
    return "", None


def command(value):
    if isinstance(value, dict):
        return str(value.get("command") or value.get("cmd") or "")
    return value if isinstance(value, str) else ""


def file_paths(name, value):
    if name not in FILE_MUTATION_TOOLS:
        return set()
    if isinstance(value, dict) and value.get("file_path"):
        return {str(value["file_path"])}
    patch = command(value)
    return {path.strip() for path in PATCH_PATH.findall(patch) if path.strip()}


def write_verb(name, value):
    value = value if isinstance(value, dict) else {}
    candidates = [name.rsplit("__", 1)[-1], value.get("verb"), value.get("name")]
    nested = value.get("args")
    if isinstance(nested, dict):
        candidates.extend([nested.get("verb"), nested.get("name")])
    return any(isinstance(candidate, str) and is_write_action(normalized_action(candidate))
               for candidate in candidates)


def normalized_action(value):
    return value.replace("_", "-").lower()


def is_write_action(action):
    """Classify a CARR registry action without treating similar reads as writes."""
    return action in WRITE_ACTION_EXACT or action.partition("-")[0] in WRITE_ACTION_PREFIXES


def nested_carr_actions(value):
    """Return visible CARR MCP action names embedded in Codex exec input."""
    source = command(value)
    if not source:
        return []
    actions = [normalized_action(match.group(2)) for match in NESTED_CARR_CALL.finditer(source)]
    if any(action == "call-verb" for action in actions):
        actions.extend(normalized_action(match.group(1)) for match in CALL_VERB.finditer(source))
    return actions


def mutation(name, value):
    cmd = command(value)
    if DEPLOY.search(cmd):
        return True
    if name.startswith(("mcp__carr__", "mcp__carr_records__")) and write_verb(name, value):
        return True
    # In the real Codex JSONL, an in-process MCP invocation is represented as
    # a `functions.exec` custom call whose raw JS visibly contains
    # tools.mcp__carr__update_deal(...) or call_verb({verb: ...}).
    return name == "functions.exec" and any(is_write_action(action)
                                             for action in nested_carr_actions(value))


def verification(name, value):
    if name in VERIFY_TOOLS:
        return True
    if name in {"Bash", "functions.exec"} and VERIFY_COMMAND.search(command(value)):
        return True
    # A visible CARR read after an embedded CARR write is fresh evidence even
    # when Codex's outer custom call remains named only `functions.exec`.
    if name.startswith(("mcp__carr__", "mcp__carr_records__")):
        return not write_verb(name, value)
    return name == "functions.exec" and any(not is_write_action(action)
                                              for action in nested_carr_actions(value))


def last_human_index(recs):
    for idx in range(len(recs) - 1, -1, -1):
        if not is_synthetic_user_record(recs[idx]) and text(recs[idx], {"user", "human"}).strip():
            return idx
    return -1


def valid_disclosure(final):
    """A skipped/failed verification disclosure must say why, not just contain a word."""
    match = DISCLOSURE.search(final)
    return bool(match and DISCLOSURE_REASON.search(final[match.end():]))


def evaluate(recs):
    start = last_human_index(recs) + 1
    window = recs[start:]
    mutation_at = []
    changed_files = set()
    for idx, rec in enumerate(window):
        name, value = tool(rec)
        if mutation(name, value):
            mutation_at.append(idx)
        changed_files.update(file_paths(name, value))
    # A single local-file edit is normal mechanical work. Record writes and
    # deploys are already identified above; local code changes earn the terminal
    # check only when the patch actually spans more than one path.
    tracked = bool(mutation_at) or len(changed_files) > 1
    if not tracked:
        return False, "no tracked mutation"
    final = ""
    for rec in window:
        candidate = text(rec, {"assistant"}).strip()
        if candidate:
            final = candidate
    if not final or not CLAIM.search(final) or valid_disclosure(final):
        return False, "no unsupported terminal claim"
    latest = max(mutation_at + [idx for idx, rec in enumerate(window)
                                if file_paths(*tool(rec))])
    if any(verification(*tool(rec)) for rec in window[latest + 1:]):
        return False, "fresh verification present"
    if DELIVERY.search(final) and not NEGATED_DELIVERY.search(final) and not RECIPIENT.search(final):
        return True, "delivery claim names no recipient"
    return True, "terminal completion claim has no fresh verification"


def main():
    try:
        payload = json.load(sys.stdin)
        if payload.get("stop_hook_active"):
            return 0
        path = payload.get("transcript_path") or payload.get("transcriptPath")
        if not path or not os.path.exists(path):
            return 0
        with open(path, errors="replace") as fh:
            recs = [json.loads(line) for line in fh if line.strip()]
        if not payload_is_carr(payload, recs):
            return 0
        blocked, reason = evaluate(recs)
        if not blocked:
            return 0
        audit({"ts": now(), "hook": "completion-evidence-gate",
               "session": payload.get("session_id") or payload.get("sessionId"),
               "reason": reason})
        print(json.dumps({"decision": "block", "reason":
            "COMPLETION EVIDENCE GATE — " + reason + ". Run a fresh read/test/render "
            "and report its result, or say plainly what was unverified/skipped and why. "
            "Do not claim delivery without naming the recipient."}))
        return 0
    except Exception:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
