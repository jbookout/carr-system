#!/usr/bin/env python3
"""Bind a completion claim to the clauses of the order that produced it.

WHAT THIS GATE WAS, AND WHY IT WAS NOT ENOUGH. It used to fire only when a
session's last message claimed completion with no fresh read behind it, plus
one special case: a delivery claim naming no recipient. That is a real control
and it fires 155 times on the record. It also could not have caught the failure
that convened the 2026-08-23 completion-integrity council.

The case: Joe ordered two things — recategorize 218 rules into enforcement
classes AND make them load into every session. Several sessions later the map
was complete, reviewed, versioned and green in CI; production held admission
rows for 4 of 218; the scoped-loading half had never been written at all. No
session lied. Each said something TRUE about the map. As Grok's chair put it,
this was never a partially-done job — it was a COMPLETED SLICE STANDING FOR THE
ORDER, and no claim-triggered control was in its path, because the claim that
was made was not the claim any of them intercept.

THE WIDENING. The trigger is no longer a word in the final message. At Stop the
gate decomposes the originating order into CLAUSES, resolves each clause's
CONSUMER SURFACE, and blocks if any clause lacks a fresh RECEIPT from that
surface. Vocabulary is not the trigger, which means SILENCE FIRES TOO: closing
with "wrapped up", "should be good", or a neutral sentence about the artifact
blocks exactly as hard as closing with "done". That is deliberate and it is the
one design constraint Grok's chair put on this fix:

    "If it is too noisy, people will route around it with weaker verbs
     ('wrapped up', 'should be good') — so the gate must match EFFECT CLAIMS,
     not a banned word list."

A banned-word list has an escape for every synonym anyone can think of. This
has one escape and only one: say what remains. A clause is accounted for by a
receipt from its consumer surface, or by the close naming that clause as not
done. Nothing else gets past it, so there is nothing to route around.

WHOSE CONTEXT COUNTS. Per clause, and this is the whole of Sol's chair's
"authority inversion" finding — the producer's account of completion must not
outrank the consumer's state:

    deploy / release / ship / live   -> production worker probe, never
                                        wrangler's exit status
    install / admit / migrate /      -> readback of the live store
      activate / register
    load / wire / hook up / at boot  -> the runtime that actually invokes it
    schedule / nightly / every day   -> the loaded scheduler
    send / notify / hand off         -> a named recipient
    ready for a partner to use       -> the human journey, and ONLY this clause
    build / write / fix / refactor   -> the repository: a fresh read or test,
                                        which is the ORIGINAL predicate, kept

That last row is why this is not ceremony. Ordinary repo work keeps exactly the
friction it had yesterday. Grok's chair was explicit that over-requiring
human-journey receipts for mechanical landings is how this becomes ceremony and
gets routed around, so "ready for a partner" is matched narrowly — it needs a
person, not the word "ready".

STANDING CLAUSES (Joe's ruling, 2026-08-23). Scope decayed ACROSS turns in the
case study: "recategorize AND scope the loading" became "recategorize". So a
non-repo clause from an earlier turn keeps standing until it is receipted or
explicitly retired ("skip the loading part for now"). Repo clauses do not
stand — they are accounted by their own turn's evidence, and carrying them
would make every long session noisy for no recall.

WHAT IS NOT REQUIRED. No Work Request. Sol's chair wanted material
conversational work routed into the opslang lifecycle at its first mutation;
Grok's refused ("gate the claim, not the genre") on the grounds that a second
mandatory enrollment is exactly the shadow system whose bypass produced these
failures. Joe took the middle path: an order identity — clauses plus consumer
surfaces — is required only WHEN A COMPLETION CLAIM IS UTTERED. It is derived
here, at the claim, from the order text. Nothing is filed at session start and
no session has to enroll anything to do ordinary work.

THE DUAL, and it binds just as hard. Where consumer receipts exist, a session
may not close by calling the work UNBUILT because an attestation verb was
skipped. That is the false-incomplete half: ops/built_unclosed.py's own header
records a session reading "0/51 completed" as "nothing is built" and starting a
rebuild of work that had already landed — 24 times, 9 caught by a human. So an
"it was never built" close is blocked when this session's own record holds a
successful read of the artifact it says does not exist.

Note the asymmetry, because it is on purpose. The primary half cannot use a
word list (silence is the common failure). The dual half MUST use one, because
a false-incomplete is a STATED mischaracterization — there is an actual sentence
to match, and matching it is not the failure mode being guarded against.

WHAT IT STILL CANNOT DO, said plainly rather than left for someone to discover:
clause extraction is shallow. It splits an order on sentence and conjunction
boundaries and classifies by the clause's own verb. An order phrased as one
long clause containing two effects will read as one. It cannot see an order
given in a previous SESSION, only previous turns of this one. And a receipt is
matched by the surface a command touches, not by that command's output, so a
probe that ran and returned bad news still counts as a probe — the close has to
be honest about the result, which is what the original predicate covers.

FAILS OPEN on any error. A wedged session is worse than a claim that has to be
restated.

Fixtures: ops/completion-evidence-gate-selftest.py
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from stop_latch import (  # noqa: E402
    claim_identity, latched, record_fire, record_satisfied)

LOG = os.path.join(REPO, "out", "completion-evidence-gate.jsonl")
# The FLOOR trigger, kept and widened with the verbs Joe named (finished,
# landed, phase-complete, ready, live). It is no longer the only trigger: the
# clause predicate below fires with or without any of these words.
CLAIM = re.compile(
    r"\b(done|complete(?:d)?|verified|shipped|deployed|delivered|sent|notified|"
    r"handed\s+off|finished|landed|live|ready|phase[-\s]complete|wrapped\s+up)\b", re.I)
# A terminal claim may disclose why fresh verification is unavailable, but a
# bare word is not evidence: "no tests failed" and "skipped none" are success
# claims, not a reason verification could not happen.
DISCLOSURE = re.compile(
    r"\b(?:unverified|not\s+verified|could\s+not\s+verify|"
    r"not\s+(?:yet\s+)?(?:landed|live|ready|built|wired|loaded|deployed|complete\w*)|"
    r"(?:verification|tests?|checks?)\s+(?:was|were)?\s*"
    r"(?:unavailable|failed|skipped))\b", re.I)
DISCLOSURE_REASON = re.compile(r"(?:\bbecause\b|\bdue\s+to\b|\bwhen\b|[:—-])\s*\S", re.I)
DELIVERY = re.compile(r"\b(delivered|sent|notified|handed\s+off)\b", re.I)
NEGATED_DELIVERY = re.compile(r"\b(not|never|hasn['’]t|haven['’]t|didn['’]t)\s+(?:been\s+)?(?:delivered|sent|notified|handed\s+off)\b", re.I)
RECIPIENT = re.compile(r"\b(to|with)\s+(?:Joe|Dell|[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b")
FILE_MUTATION_TOOLS = {"Write", "Edit", "MultiEdit", "apply_patch", "functions.apply_patch"}
VERIFY_TOOLS = {"Read", "Grep", "Glob", "WebFetch", "WebSearch"}
DEPLOY = re.compile(r"\b(wrangler\s+deploy|run\.sh\s+migrate\s+--apply|git\s+push)\b", re.I)
# pytest and *-selftest.py are the two commonest verification commands in this
# repo and neither matched: `\btest\b` does not fire inside "pytest" or
# "rule-map-selftest.py". A gate that cannot see the project's own test runner
# was demanding evidence that had already been produced.
VERIFY_COMMAND = re.compile(
    r"\b(test|check|lint|health|diff|status|verify|smoke|render|build)\b|"
    r"pytest|selftest", re.I)
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
    "project-room-queue",   # shape-checked unattended room projection write;
                              # "project" is not generalized because projection reads exist
    "report-problem",       # Program 6 additive Work Request capture; "report"
                              # is not generalized because report-style reads exist
    "review-and-triage",    # Program 6 human state transition; exact because
                              # other review-* actions include non-mutating reads
    "propose-ready-plan",   # Program 6 immutable plan proposal; explicit evidence coverage
    "review-heavy-build-plan", # Program 6 independent heavy-plan review receipt;
                               # exact because other review-* actions include reads
    "accept-ready-plan",    # Program 6 human readiness transition; never execution
    "propose-outcome-feedback", # Program 6 immutable evidence proposal; no self-attestation
    "accept-outcome-feedback",  # Program 6 human-only observational acceptance; never completion
    "review-deal",
    "review-engineering-slice",  # independent typed review is a persisted verdict;
                                   # other review-* actions include non-mutating reads
    "observe-memory",  # evidence-backed candidate write; exact because observe-* reads may exist
    "correct-memory",  # immutable successor write; exact transition
    "forget-memory",   # reversible suppression write; exact transition
    "issue-execution-envelope",  # persists one immutable governed execution envelope
    "transition-evaluation-case",  # human-authority append-only eval lifecycle write
    "transition-execution-environment-provider",  # human-authority provider CAS/rollback lifecycle write
}
# The three reason classes that carry a latch identity. Named constants rather
# than repeated literals, because an identity keyed on a string that drifts is
# an identity that silently stops matching — the latch would then look present
# and do nothing, which is worse than no latch.
CLAUSE_REASON = "unaccounted clause"
FLOOR_REASONS = ("terminal completion claim has no fresh verification",
                 "delivery claim names no recipient")

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


# ---------------------------------------------------------------- clauses
# An ORDER decomposes into clauses; each clause names the surface that has to
# change. The class is taken from the clause's OWN verb, which is the only
# mechanical sense in which a Stop hook can "bind to the originating order".
#
# ORDER OF THESE ENTRIES IS LOAD-BEARING. "make them load into every session"
# contains both a repo verb (make) and a runtime verb (load); the consumer is
# the live boot, so runtime has to be tested first. Likewise "admit the rules
# into the production store" must not read as a deploy — which is why the
# production patterns match "to production" and the deploy verbs, never the
# bare word "production".
# THIRD PARTIES ONLY. "the user" and "you" are the person in the conversation:
# delivering to them is answering, not delivery, and demanding a named recipient
# for it fired four times on real logs.
PERSON = re.compile(r"\b(Joe|Dell|partner|client|customer)\b", re.I)
CONSUMER_PATTERNS = (
    ("recipient", re.compile(
        r"\b(send|sends?|sent|e-?mail(?:ed)?|notif(?:y|ied)|deliver(?:ed)?|"
        r"message|tell|hand(?:ed)?\s+off|share\s+with)\b", re.I)),
    ("human", re.compile(
        r"\bready\s+for\b|\bfor\s+\w+\s+to\s+(?:use|see|review|try)\b|"
        r"\bso\s+\w+\s+can\b|\bfirst\s+(?:real\s+)?use\b|\busable\s+by\b", re.I)),
    ("production", re.compile(
        r"\b(deploy(?:ed|s)?|releas(?:e|ed|es)|ship(?:ped)?|publish(?:ed)?|"
        r"promote[ds]?|go\s+live|make\s+(?:it\s+)?live|to\s+production|"
        r"in\s+production)\b", re.I)),
    ("scheduler", re.compile(
        r"\b(schedul(?:e|ed|es|ing)|nightly|cron|launchd|daily|weekly|"
        r"every\s+(?:night|day|morning|hour))\b", re.I)),
    ("runtime", re.compile(
        r"\b(load(?:s|ed|ing)?|wire[ds]?|wiring|hook(?:ed)?\s+up|invoke[ds]?|"
        r"fires?|mount(?:ed)?|enabl(?:e|ed|es)|at\s+boot|so\s+it\s+runs|"
        r"actually\s+runs|into\s+every\s+session)\b", re.I)),
    ("store", re.compile(
        r"\b(install(?:ed|s)?|admit(?:ted)?|admission|migrat(?:e|ed|es|ion)|"
        r"seed(?:ed)?|import(?:ed)?|enroll(?:ed)?|provision(?:ed)?|"
        r"activat(?:e|ed|es)|register(?:ed)?|backfill(?:ed)?)\b", re.I)),
    ("repo", re.compile(
        r"\b(build|built|writ(?:e|ing)|wrote|add(?:ed)?|fix(?:ed)?|"
        r"refactor(?:ed)?|implement(?:ed)?|design(?:ed)?|creat(?:e|ed|es)|"
        r"updat(?:e|ed|es)|renam(?:e|ed)|remov(?:e|ed)|delet(?:e|ed)|"
        r"document(?:ed)?|test(?:ed)?|widen(?:ed)?|extend(?:ed)?|"
        r"re?categoriz\w*|rewrit(?:e|ten)|rewrote|make|made|chang(?:e|ed)|"
        r"split|merg(?:e|ed)|mov(?:e|ed)|port(?:ed)?|generat(?:e|ed)|"
        r"clean\s*up|revert(?:ed)?)\b", re.I)),
)

# The receipt families, one per consumer class. A receipt is recognised by the
# SURFACE a call touches, not by a session asserting it verified something.
RECEIPT_PATTERNS = {
    "production": re.compile(
        r"wrangler\s+(?:deployments|tail|versions|whoami)|verb-count|list-verbs|"
        r"run\.sh\s+health|health-check|deployed-verbs|workers?\.dev", re.I),
    "store": re.compile(
        r"run\.sh\s+retrieve|\bpsql\b|migrate(?:-prod)?\s+--check|admission|"
        r"production-audit|audit-against-production|list-rules|get-rule|"
        r"\bretrieve\b|standing-context", re.I),
    # NOT hooks.json / settings.json / the enforcement map. Those are REPO
    # FILES: reading one is builder truth wearing the runtime's name, and the
    # fixture "a plain file read does not account for a live-surface clause"
    # exists because an earlier draft of this line accepted exactly that. Every
    # pattern here names something that reports what the runtime ACTUALLY
    # loaded, which is the only observation that can be false while CI is green.
    "runtime": re.compile(
        r"standing-context|applicable-rules|gate-integrity|enforcement-coverage|"
        r"launchctl\s+list|session-brief", re.I),
    "scheduler": re.compile(
        r"launchctl\s+list|crontab\s+-l|scheduled-run|schedule-registry|"
        r"list-schedules|list-workflows", re.I),
}
# A runtime clause is satisfied by a store readback too: "the rules are admitted"
# and "the rules load at boot" are observations of the same live surface from
# two sides, and demanding both would be ceremony.
RECEIPT_ACCEPTS = {"runtime": ("runtime", "store"), "store": ("store",),
                   "production": ("production",), "scheduler": ("scheduler",)}

FIRST_USE = re.compile(
    r"\b(used\s+it|has\s+used|walked\s+through|traversed|first\s+use|"
    r"confirmed\s+by\s+\w+)\b", re.I)
# The ONE escape from an unaccounted clause: say what is left.
RESIDUAL = re.compile(
    r"\b(not\s+(?:yet\s+)?(?:built|done|landed|wired|loaded|live|verified|started|"
    r"implemented|shipped|deployed|activated|admitted|scheduled|complete\w*)|"
    r"never\s+(?:built|written|wired|ran|run)|no\s+\w+\s+exists?|"
    r"does\s+not\s+exist|is\s+not\s+\w+|are\s+not\s+\w+|has\s+not|have\s+not|"
    r"hasn'?t|haven'?t|did\s+not|didn'?t|still\s+(?:needs?|to|open|outstanding)|"
    r"remain\w*|outstanding|unbuilt|unwired|unloaded|unverified|unconfirmed|"
    r"not\s+yet|yet\s+to|blocked|deferred|skipped|pending|todo|next\s+step|"
    r"left\s+to\s+do|out\s+of\s+scope|untouched|nothing\s+loads)\b", re.I)
RETIRE = re.compile(
    r"\b(skip|never\s+mind|nevermind|drop\s+the|forget\s+(?:the|about)|"
    r"don'?t\s+worry|do\s+not\s+worry|not\s+now|hold\s+off|park\s+(?:the|that)|"
    r"deprioriti\w*|leave\s+(?:it|that)|instead\s+of)\b", re.I)
# The dual. This half is a word class ON PURPOSE — see the module docstring.
UNBUILT = re.compile(
    r"\b(never\s+(?:built|implemented|created|written|wired|landed|shipped)|"
    r"(?:was|is|were|are)\s+not\s+built|not\s+implemented|nothing\s+is\s+built|"
    r"no\s+implementation|does\s+not\s+exist|doesn'?t\s+exist|"
    r"(?:is|are|remains?|still)\s+unbuilt|needs?\s+to\s+be\s+built)\b", re.I)

CLAUSE_SPLIT = re.compile(
    r"(?:[.;?!\n]+|\b(?:and\s+then|and\s+also|and|then|also|plus|as\s+well\s+as)\b)", re.I)
WORD = re.compile(r"[a-z0-9]+")
STOPWORDS = frozenset("""
the a an and or but so to of in on at for from with by as is are was were be been
being it its this that these those there here them they he she we you i my your
our their his her me us do does did doing done have has had having will would
can could should shall may might must not no nor if then than when while where
which who whom whose what how why all any both each few more most other some such
only own same too very just now also into onto over under again further once about
against between through during before after above below up down out off again
please make sure need needs want lets let go get got take put use using next
""".split())


# AN ORDER COMES FROM THE HUMAN, and a "user" record is not all human. It also
# carries system-reminders, scheduled-task envelopes, local-command output and
# the deny text of other gates. Replayed over seven days of real logs before
# this existed, that was the LARGEST source of false fires by a wide margin:
# 44 recipient clauses, nearly all of them one hook's own banner —
#   "[hooks/drift-assertion-gate.py]: DRIFT ASSERTION — you are about to tell
#    Joe that a present state is WRONG"
# read as Joe ordering a session to tell Joe something. Twenty-one more were
# <scheduled-task name="nightly-record-layer"> envelopes read as orders to
# schedule something.
#
# So machine text is stripped before an order is parsed. This is the same
# boundary the rest of the system draws everywhere else: what arrives through
# tooling is DATA, and only what the human typed is an instruction. A gate that
# blurred that line would be enforcing against its own neighbours' output.
MACHINE_TAG = re.compile(
    r"<(system-reminder|scheduled-task|environment_context|app-context|"
    r"local-command-[a-z]+|command-(?:name|message|args)|user-prompt-submit-hook|"
    r"task-notification|function_results|budget)\b[^>]*>.*?</\1\s*>"
    r"|</?[a-z]+[-_][a-z0-9_-]*\b[^>]*/?>", re.I | re.S)
# A hook announces itself by filename or by an ALL-CAPS banner. Neither is an order.
MACHINE_LINE = re.compile(
    r"^[ \t]*\[[^\]\n]*\.py\][ \t]*:.*$"        # [/usr/bin/python3 …/some-gate.py]: …
    r"|^[ \t]*[\w./-]*\.py[ \t]*:.*$"           # some-gate.py: …
    r"|^[ \t]*[A-Z][A-Z0-9 _-]{6,}[ \t]*[—:-].*$", re.M)


CODE_SPAN = re.compile(r"```.*?```|~~~.*?~~~|`[^`\n]+`", re.S)


def order_text(value):
    """What the human actually typed, with injected machine text removed."""
    value = MACHINE_TAG.sub(" ", value or "")
    value = CODE_SPAN.sub(" ", value)
    return MACHINE_LINE.sub(" ", value)


class Clause:
    """One ordered effect, its consumer surface, and the turn it was ordered on."""
    __slots__ = ("text", "terms", "consumer", "turn")

    def __init__(self, text, terms, consumer, turn):
        self.text, self.terms, self.consumer, self.turn = text, terms, consumer, turn

    def __repr__(self):
        return f"Clause({self.consumer}: {self.text!r})"


def terms_of(value):
    """Content words, lightly stemmed, for matching a clause against prose."""
    out = set()
    for word in WORD.findall(value.lower()):
        if len(word) < 3 or word in STOPWORDS:
            continue
        for suffix in ("ings", "ing", "ed", "es", "s"):
            if word.endswith(suffix) and len(word) - len(suffix) >= 3:
                word = word[: -len(suffix)]
                break
        out.add(word)
    return out


def terms_match(terms, value):
    """True when a distinctive clause term appears in `value`.

    Prefix matching in both directions, minimum four characters, so "loading"
    in a close answers a clause term "load" without "loan" answering it.
    """
    found = terms_of(value)
    for term in terms:
        if len(term) < 4:
            continue
        for other in found:
            if term == other or (len(other) >= 4 and
                                 (other.startswith(term) or term.startswith(other))):
                return True
    return False


# NOUN FORMS ARE NOT ORDERS, and this cost a false fire on a real transcript
# before it was here: "fix my media encoder install for me" read `install` as a
# store verb and demanded a live-store readback for a request about a codec.
# A single-word effect token only counts in a VERB position — starting the
# fragment, or following a word that can precede a verb. Multi-word patterns
# ("hand off", "every night", "ready for", "into every session") are phrases
# and are exempt, because they cannot be misread as a bare noun.
VERB_PRECEDERS = frozenset("""
to and then also please make makes made have has had get gets got let lets so
that it they them we i you should must can could would will shall now just
actually still first next finally only do does did not never but or nor if when
while before after once again better ensure need needs want wants try help go
""".split())


# AN ORDER IS IMPERATIVE; A DESCRIPTION IS NOT. This distinction is the whole
# of the gate's precision, and it was learned from replaying seven days of real
# logs. Joe's messages are often long documents: analysis, quoted output,
# bulleted specs. Sentences inside them look exactly like orders to a verb
# matcher and are nothing of the kind. Every one of these fired before this
# existed, and each is a real fragment from a real transcript:
#
#   "The write-door version of this check only fires when a record gets filed"
#   "I am not asking you to deploy anything or to take my word for any of it"
#   "tested behind a feature gate, but not promoted to the production default"
#   "- Invokes the underlying tool the way this project runs it"
#   "Seed prompt must never contain the nonce"
#   "tool not installed"
#
# An English imperative starts with a BARE verb and has no subject. So a
# fragment led by an article, a pronoun, or an inflected form is a statement,
# and a subject followed by a modal ("Seed prompt MUST never...") is a statement
# no matter what its first word looks like.
NON_IMPERATIVE_HEAD = frozenset("""
the a an this that these those my your our their its his her
i we he she it they there here what which who whose how why when where
because although though however but and or so if while during since
still already currently nothing everything nobody someone anyone
every each all both most many several few
perhaps maybe probably likely apparently
""".split())
# A PROHIBITION IS NOT AN ORDER. "Do not merge, deploy, push, or open a PR"
# demanded evidence for work that was explicitly forbidden.
PROHIBITION = re.compile(r"^\s*(?:do\s+not|don'?t|never|avoid|no\s+need)\b", re.I)
# Raw JSON pasted into a message, outside any fence. Nobody orders in JSON, and
# '"description": "Name of an already-configured MCP server to invoke"' read as
# an order to invoke something.
JSON_PAIR = re.compile(r"""^\s*[\{\[]?\s*["'][^"']+["']\s*:""")
MODAL = frozenset("""
must should shall will would can could may might is are was were has have had
""".split())
SUBJECT_PRONOUN = frozenset("it they we he she this that which who".split())
# Bare imperatives that happen to end in a suffix the inflection test rejects.
BARE_DESPITE_SUFFIX = frozenset("""
seed feed need embed exceed proceed speed breed succeed spread read
""".split())


def inflected(word):
    """True for a form no imperative takes: fires, invokes, tested, running."""
    if word in BARE_DESPITE_SUFFIX or len(word) <= 4:
        return False
    if word.endswith(("ed", "ing")):
        return True
    # "address", "process", "focus" are bare; "fires", "runs", "needs" are not.
    return word.endswith("s") and not word.endswith(("ss", "us", "is"))


def is_directive(fragment):
    """Only an instruction can carry an ordered clause."""
    words = WORD.findall(fragment.lower())
    if not words:
        return False
    if PROHIBITION.search(fragment) or JSON_PAIR.search(fragment):
        return False
    head = words[0]
    if head in {"you", "please", "let", "lets"}:
        return True
    # A MODAL HEAD IS ALWAYS A STATEMENT: "is Joe's call to schedule" fired five
    # times as an order to schedule something.
    if head in NON_IMPERATIVE_HEAD or head in MODAL or inflected(head):
        return False
    if len(words) > 1 and len(words[1]) > 4 and words[1].endswith("ed"):
        return False  # "Promotion COUNTED violations" — head is the subject
    if len(words) > 2 and words[1] in SUBJECT_PRONOUN and inflected(words[2]):
        return False  # "nightly: IT BRANCHES staging"
    return not any(word in MODAL for word in words[1:4])


DETERMINER = frozenset("""
the a an this that these those my your our their its his her every each any some no
""".split())


def in_verb_position(fragment, match):
    token = match.group(0).strip()
    if " " in token:
        return True
    if inflected(token.lower()):
        return False  # "installed"/"promoted" name a state, not an instruction
    words = WORD.findall(fragment[: match.start()].lower())
    if not words:
        return True
    if len(words) >= 2 and words[-2] in DETERMINER:
        return False  # "the first RELEASE approval" heads a noun phrase
    return words[-1] in VERB_PRECEDERS


def used_as_effect(pattern, fragment):
    return any(in_verb_position(fragment, m) for m in pattern.finditer(fragment))


def consumer_of(fragment):
    if PERSON.search(fragment):
        for name, pattern in CONSUMER_PATTERNS:
            if name in {"recipient", "human"} and used_as_effect(pattern, fragment):
                return name
    for name, pattern in CONSUMER_PATTERNS:
        if name in {"recipient", "human"}:
            continue  # both require a person; handled above
        if used_as_effect(pattern, fragment):
            return name
    return None


_PARSE_CACHE: dict = {}


def parsed_clauses(value):
    """Fragment -> (text, terms, consumer), memoised.

    Pure in `value`, so caching is safe. It matters because standing_clauses
    re-reads every earlier turn, and an order can be a very long message.
    Clause OBJECTS are built fresh from this by the caller: bounds are keyed by
    identity, so two turns must never share one instance.
    """
    cached = _PARSE_CACHE.get(value)
    if cached is not None:
        return cached
    out, seen = [], set()
    for fragment in CLAUSE_SPLIT.split(value or ""):
        fragment = fragment.strip()
        if len(fragment.split()) < 3:
            continue
        if RETIRE.search(fragment) or not is_directive(fragment):
            continue  # a retirement, or prose that merely describes
        consumer = consumer_of(fragment)
        if not consumer:
            continue
        terms = terms_of(fragment)
        key = (consumer, frozenset(terms))
        if key in seen:
            continue
        seen.add(key)
        out.append((fragment, terms, consumer))
    if len(_PARSE_CACHE) < 512:
        _PARSE_CACHE[value] = out
    return out


def order_clauses(value, turn=0):
    """Decompose an order into (effect, consumer surface) pairs.

    Deliberately shallow: sentence and conjunction boundaries, classified by
    each fragment's own verb. A fragment with no effect verb is not a clause,
    which is why an ordinary question yields none and the gate stays quiet.
    """
    return [Clause(fragment, terms, consumer, turn)
            for fragment, terms, consumer in parsed_clauses(value)]


def human_turns(recs):
    """Absolute indices of genuine human turns, oldest first."""
    return [idx for idx, rec in enumerate(recs)
            if not is_synthetic_user_record(rec) and text(rec, {"user", "human"}).strip()]


def receipt_index(recs):
    """Every call that OBSERVES a named consumer surface, by absolute index."""
    seen = []
    for idx, rec in enumerate(recs):
        name, value = tool(rec)
        if not name or name in VERIFY_TOOLS or name in FILE_MUTATION_TOOLS:
            # Read/Grep/Glob are builder-context by construction. They can
            # answer a repo clause (the floor already lets them) and they can
            # never answer a clause whose consumer is somewhere else.
            continue
        # Bounded on purpose: a heredoc carrying a whole file is not a probe,
        # and running four patterns over 50KB of pasted content once per record
        # is how a Stop hook spends its timeout.
        surface = " ".join([name.rsplit("__", 1)[-1].replace("_", "-"),
                            command(value)[:4000]])
        classes = {cls for cls, pattern in RECEIPT_PATTERNS.items() if pattern.search(surface)}
        if classes:
            seen.append((idx, classes))
    return seen


SOURCE_SUFFIX = frozenset("""
.py .js .mjs .cjs .ts .tsx .jsx .sh .sql .json .md .yml .yaml .toml .html .css
""".split())


def read_paths(recs):
    """Paths this session successfully READ — the dual's landed receipts.

    Reads only. A path this session wrote is builder truth; a path it read and
    found is the consumer surface answering, which is the whole distinction the
    council drew.
    """
    results = {}
    for rec in recs:
        msg = message(rec)
        content = msg.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    results[block.get("tool_use_id")] = bool(block.get("is_error"))
    found = set()
    for rec in recs:
        msg = rec.get("message") or rec
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            if str(block.get("name", "")) not in {"Read", "Grep", "Glob"}:
                continue
            value = block.get("input") or {}
            path = value.get("file_path") or value.get("path") or value.get("pattern")
            if not isinstance(path, str) or not path:
                continue
            if results.get(block.get("id"), False):
                continue  # the read errored: nothing was observed
            found.add(path)
    return found


def clause_accounted(clause, recs, bounds, final, floor_verified, receipts):
    """A clause is closed by a receipt from its surface, or by naming what is left."""
    if RESIDUAL.search(final):
        for sentence in re.split(r"(?<=[.;!?\n])\s+", final):
            if RESIDUAL.search(sentence) and terms_match(clause.terms, sentence):
                return True
    if clause.consumer == "repo":
        return floor_verified
    if clause.consumer == "recipient":
        return bool(RECIPIENT.search(final))
    if clause.consumer == "human":
        return bool(FIRST_USE.search(final))
    lo, hi = bounds
    last_mutation = lo - 1
    for idx in range(lo, hi + 1):
        name, value = tool(recs[idx])
        if mutation(name, value) or file_paths(name, value):
            last_mutation = idx
    accepts = RECEIPT_ACCEPTS.get(clause.consumer, (clause.consumer,))
    return any(idx > last_mutation and classes.intersection(accepts)
               for idx, classes in receipts)


def standing_clauses(recs, turns):
    """Clauses of the last turn, plus non-repo clauses earlier turns left open.

    Only non-repo clauses stand. Repo work is accounted by its own turn's
    evidence, and carrying it forward would make every long session noisy
    without adding recall on the failure this gate exists for.
    """
    if not turns:
        return [], {}
    said = [order_text(text(recs[idx], {"user", "human"})) for idx in turns]
    bounds, clauses = {}, []
    for position, idx in enumerate(turns):
        end = (turns[position + 1] - 1) if position + 1 < len(turns) else len(recs) - 1
        for clause in order_clauses(said[position], turn=idx):
            if position < len(turns) - 1 and clause.consumer == "repo":
                continue
            if any(RETIRE.search(later) and terms_match(clause.terms, later)
                   for later in said[position + 1:]):
                continue  # a later turn explicitly stood this one down
            bounds[id(clause)] = (idx + 1, end)
            clauses.append(clause)
    return clauses, bounds


def dual_block(recs, final):
    """False-incomplete: an unbuilt close over the session's own landed receipt."""
    if not final or not UNBUILT.search(final):
        return None
    claimed = set()
    for sentence in re.split(r"(?<=[.;!?\n])\s+", final):
        if UNBUILT.search(sentence):
            claimed |= terms_of(sentence)
    if not claimed:
        return None
    for path in read_paths(recs):
        if os.path.splitext(path)[1].lower() not in SOURCE_SUFFIX:
            continue  # a screenshot is not the work; iso-render.png fired once
        # EVERY distinctive token of the filename, and enough of them to be an
        # identity. A single shared word matched eight unrelated closes on real
        # logs — reading hooks/guard-unattended.py while saying something else
        # does not exist.
        name = {term for term in terms_of(os.path.basename(path)) if len(term) >= 4}
        if len(name) < 2 and not any(len(term) >= 8 for term in name):
            continue
        if name and name.issubset(claimed):
            return ("this session read " + path + " without error, so its own record holds a "
                    "landed receipt for work this close calls unbuilt")
    return None


def write_verb_names(window):
    """The write verbs this turn actually called, for the floor's identity.

    Paths alone are not the claim-set: a record write touches no file, so two
    turns calling different verbs against the same repo would otherwise collapse
    into one identity and the second finding would be swallowed.
    """
    names = set()
    for rec in window:
        name, value = tool(rec)
        if not name:
            continue
        if write_verb(name, value):
            names.add(name.split("__")[-1])
        for action in nested_carr_actions(value):
            if is_write_action(action):
                names.add(action)
    return names


def evaluate(recs, ledger=None):
    """Block when an ordered clause has no receipt, or a close denies one it holds.

    THE LEDGER OUT-PARAMETER carries what the latch needs and nothing else, so
    this keeps its two-value return and every existing fixture keeps working.
    When a dict is passed, evaluate fills it as it walks:

        ledger["identity"]  = (reason_class, [token, ...])  the finding, if any
        ledger["satisfied"] = [(reason_class, [token, ...]), ...]  what came
                              with receipts on this pass

    Nothing here changes what blocks. See main() for why the memory exists.

    Three layers, in the order they are reported. The CLAUSE layer is the
    widening and is deliberately first, because "the loading half was never
    built" is a more useful thing to hand a session than "no fresh
    verification". The FLOOR is the original predicate, unchanged, so a
    transcript that fired yesterday still fires today. The DUAL runs even when
    nothing was mutated, because a rebuild starts from a session that has done
    no work yet.
    """
    def note(key, reason_class, tokens):
        if ledger is None:
            return
        entry = (reason_class, [str(t) for t in tokens])
        if key == "identity":
            ledger["identity"] = entry
        else:
            ledger.setdefault("satisfied", []).append(entry)

    turns = human_turns(recs)
    start = (turns[-1] + 1) if turns else 0
    window = recs[start:]
    final = ""
    for rec in window:
        candidate = text(rec, {"assistant"}).strip()
        if candidate:
            final = candidate

    contradiction = dual_block(recs, final)
    if contradiction:
        return True, "unbuilt close contradicts a landed receipt — " + contradiction

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

    latest = max(mutation_at + [idx for idx, rec in enumerate(window)
                                if file_paths(*tool(rec))])
    verified = any(verification(*tool(rec)) for rec in window[latest + 1:])

    # THE CLAUSE LAYER. No word in `final` is required to reach this: a session
    # that mutated against an order and closed on a clause with no receipt is
    # making an effect claim whatever it chose to say, including nothing.
    clauses, bounds = standing_clauses(recs, turns)
    # Scanned ONCE. This used to be recomputed per clause, which on a long
    # transcript is the difference between a Stop hook and a stalled session.
    receipts = receipt_index(recs) if clauses else []
    for clause in clauses:
        if clause_accounted(clause, recs, bounds[id(clause)], final, verified, receipts):
            # BANK IT EVEN WHEN A NEIGHBOUR IS ABOUT TO FIRE. A clause receipted
            # in this turn must stay settled once the session fixes the clause
            # beside it, or fixing the neighbour re-fires the one that was
            # already answered — which is the duplicate again, one layer down.
            note("satisfied", CLAUSE_REASON, [clause.consumer, clause.text])
            continue
        # IDENTITY IS THE CLAUSE, NOT THE FILES. Two turns can touch identical
        # paths with a different clause open, and they must not share an id.
        note("identity", CLAUSE_REASON, [clause.consumer, clause.text])
        return True, (f'unaccounted clause [{clause.consumer}]: "{clause.text}" — '
                      f"no receipt from its {clause.consumer} surface and the close "
                      f"does not say what is left")

    # THE FLOOR, unchanged in what it blocks. Its claim-set really is the
    # artifact set, so that is its identity: the changed paths plus the names of
    # the write verbs called, which is what "these claims" means here.
    artifacts = sorted(changed_files) + sorted(write_verb_names(window))

    if not final or not CLAIM.search(final) or valid_disclosure(final):
        return False, "no unsupported terminal claim"
    if verified:
        # THE RECEIPTED CASE. Both floor reason classes are banked, because a
        # later restatement of these same claims could arrive as either one, and
        # the evidence just seen answers both.
        for reason_class in FLOOR_REASONS:
            note("satisfied", reason_class, artifacts)
        return False, "fresh verification present"
    if DELIVERY.search(final) and not NEGATED_DELIVERY.search(final) and not RECIPIENT.search(final):
        note("identity", FLOOR_REASONS[1], artifacts)
        return True, "delivery claim names no recipient"
    note("identity", FLOOR_REASONS[0], artifacts)
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
        session = payload.get("session_id") or payload.get("sessionId")
        ledger = {}
        blocked, reason = evaluate(recs, ledger)

        # THE CLAIM-SET LATCH (2026-08-23, Joe's Stop-gate rationing).
        #
        # WHAT IT FIXES, measured twice and independently. The gates-audit
        # council's labeled ledger caught this gate firing a SECOND time on a
        # summary whose claims already carried receipts one message earlier. A
        # replay over seven days, 127 transcripts and 916 Stop points found the
        # rate behind that anecdote: one session hit at FIVE consecutive stops
        # on the same claim and the same reason class.
        #
        # THE PRECEDENT, and it is why this is a memory and not a narrower
        # matcher. Joe, 2026-08-15: "WHEN A REFUSAL CAN BE ROUTED AROUND,
        # REMEMBER WHAT WAS REFUSED RATHER THAN WIDENING THE BAN." The first
        # ruling in that same record is why the duplicate could not simply be
        # tolerated — a gate that punishes the honest interim state gets
        # deleted, and a session that verified its work, reported it, and then
        # summarised it is exactly that state.
        #
        # SATISFACTION IS BANKED FIRST, and before the `blocked` check, because
        # a turn can receipt one clause while firing on its neighbour. Bank the
        # receipted clause anyway or fixing the neighbour re-fires the settled
        # one, which is this same defect one layer down.
        for reason_class, tokens in ledger.get("satisfied", []):
            record_satisfied(session, claim_identity(
                "completion-evidence-gate", reason_class, tokens))

        if not blocked:
            return 0

        # THE DUAL IS NEVER LATCHED. dual_block() returns before the tracked
        # check and fires on a session that has mutated nothing; a close that
        # calls landed work unbuilt is worth refusing every time it is uttered,
        # and its identity is the artifact rather than a claim-set anyway.
        identity = None
        if ledger.get("identity"):
            reason_class, tokens = ledger["identity"]
            identity = claim_identity("completion-evidence-gate", reason_class, tokens)
            if latched(session, identity):
                return 0
            record_fire(session, identity)

        audit({"ts": now(), "hook": "completion-evidence-gate",
               "session": session, "reason": reason,
               "claim_identity": identity})
        print(json.dumps({"decision": "block", "reason":
            "COMPLETION EVIDENCE GATE — " + reason + ".\n"
            "A close binds to the ORDER, not to the slice you finished. Every ordered "
            "clause needs one of: a fresh receipt read from the surface that was "
            "supposed to change (production probe, live-store readback, the runtime "
            "that invokes it, the loaded scheduler, a named recipient, real first use), "
            "or a sentence naming that clause as not done. Rewording the close does not "
            "help — silence blocks the same as \"done\". If your own record already shows "
            "the work landed, do not close by calling it unbuilt."}))
        return 0
    except Exception:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
