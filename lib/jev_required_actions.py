"""lib/jev_required_actions.py — read a turn's Jev build-advisory
`required_actions` back out of the transcript, and judge whether the turn
satisfied decision 0b11c89b (2026-09-24, Joe: "Jev is not advisory only. It's
in our hard rules or it is supposed to be.") for each listed facet.

THIS IS A LIBRARY, ON PURPOSE. No shebang, no `__main__` guard — see the header
of ops/typesafe_client.py for why that distinction matters in this repository:
either one would register this file as a new script entrypoint in the sealed
source inventory (ops/scac-mutation-inventory.mjs's isScriptEntrypoint()) and
move the frontier for no functional reason. Callers are hooks that already
exist: hooks/completion-evidence-gate.py and hooks/executor-tier-gate.py.

WHERE THE DATA LIVES. hooks/rule-pack-preuse-reselection.py answers
UserPromptSubmit with `{"hookSpecificOutput": {"hookEventName":
"UserPromptSubmit", "additionalContext": <canonical JSON text of the build
receipt, schema jev-build-turn-receipt/v1>}}`. Claude Code records that as an
"attachment" transcript record: `{"attachment": {"type": "hook_success" |
"hook_non_blocking_error", "hookName": ..., "hookEvent": "UserPromptSubmit",
"stdout": <the hook's raw stdout, itself JSON>}}`. Nothing before this module
read that back out — that is exactly the gap the 2026-09-24 bypass audit
labeled K1/C10 ("required_actions has no reader beyond the validator").

WHAT COUNTS AS EVIDENCE JEV WAS CALLED. ops/typesafe_client.py's `ask()`
appends one best-effort row to out/jev-calls.jsonl per call. This module also
accepts the coarser, turn-scoped signal of a Bash command that names
`typesafe_client` directly, because a session's own transcript window for this
turn is the more precise binding: the receipt file proves a call happened
somewhere in the session, but not that it happened in the window being judged,
unless matched by session AND time. Either signal is treated as "Jev was used
this turn" and, per ops/typesafe_client.py's own "ASK TOGETHER" design (one
batched request evaluates every question against the state at once), a single
call is not attributed to one named facet — it counts as evidence for every
required facet not separately refused.

A NAMED REFUSAL is a `JEV-REFUSED: <facet> <reason>` line in assistant text.
Facet spelling matches the facet keys in ops/jev_build_advisory.py's FACETS
(e.g. `architecture_or_design`), case-insensitive.

FAILS OPEN. A missing transcript, an unreadable attachment, or a malformed
advisory record all read as "unavailable" rather than inventing a requirement
or raising — callers log that and let the turn through, the same posture as
every other gate in this repository.

Fixtures: ops/jev-required-actions-selftest.py
"""

import json
import re

BUILD_RECEIPT_SCHEMA = "jev-build-turn-receipt/v1"
BUILD_ADVISORY_UNAVAILABLE_SCHEMA = "jev-build-advisory-unavailable/v1"
POSTWRITE_RECEIPT_SCHEMA = "jev-post-write-review/v2"

JEV_REFUSED_RE = re.compile(r"\bJEV-REFUSED:\s*([A-Za-z_]+)\s+(\S.*)", re.I)
TYPESAFE_CALL_RE = re.compile(r"typesafe_client", re.I)
NOT_APPLICABLE_RE = re.compile(r"jev\s+required\s+actions\s*:\s*not\s+applicable\s*(?:—|-|--)\s*\S", re.I)


def _attachment_stdout_json(rec):
    attachment = rec.get("attachment") if isinstance(rec, dict) else None
    if not isinstance(attachment, dict):
        return None
    stdout = attachment.get("stdout")
    if not isinstance(stdout, str) or not stdout.strip():
        return None
    try:
        return json.loads(stdout)
    except ValueError:
        return None


def _hook_context(rec, event_name):
    outer = _attachment_stdout_json(rec)
    if not isinstance(outer, dict):
        return None
    hook_output = outer.get("hookSpecificOutput")
    if not isinstance(hook_output, dict):
        return None
    if event_name is not None and hook_output.get("hookEventName") != event_name:
        return None
    ctx = hook_output.get("additionalContext")
    if not isinstance(ctx, str) or not ctx.strip():
        return None
    try:
        return json.loads(ctx)
    except ValueError:
        return None


def find_build_advisory(recs):
    """The most recent build-turn receipt anywhere in `recs`, or None.

    The LAST match wins: `recs` is the whole transcript in order, so a later
    UserPromptSubmit receipt supersedes an earlier turn's. Returns the raw
    receipt dict (schema jev-build-turn-receipt/v1); its `advisory` key is
    either the Jev advisory (schema jev-build-advisory/v1, present on
    ops.jev_build_advisory.advise()'s FACETS) or the fixed
    jev-build-advisory-unavailable/v1 abstention.
    """
    found = None
    for rec in recs or ():
        receipt = _hook_context(rec, "UserPromptSubmit")
        if isinstance(receipt, dict) and receipt.get("schema") == BUILD_RECEIPT_SCHEMA:
            found = receipt
    return found


def find_postwrite_reviews(recs):
    """Every post-write Jev review receipt (schema jev-post-write-review/v2)
    found in `recs`, in transcript order. Includes "unavailable" ones."""
    out = []
    for rec in recs or ():
        receipt = _hook_context(rec, None)
        if isinstance(receipt, dict) and receipt.get("schema") == POSTWRITE_RECEIPT_SCHEMA:
            out.append(receipt)
    return out


def required_facets(receipt):
    """`advisory.required_actions` facet names from a build-turn receipt.

    Returns None when there is nothing to require: no receipt, an unavailable
    advisory, or a malformed one. Returns [] when the advisory is a real,
    readable one that lists no required actions. Callers must treat None as
    fail-open (nothing enforceable was read) and [] as "nothing is required
    this turn" — the two are deliberately not the same value.
    """
    if not isinstance(receipt, dict):
        return None
    advisory = receipt.get("advisory")
    if not isinstance(advisory, dict):
        return None
    if advisory.get("schema") == BUILD_ADVISORY_UNAVAILABLE_SCHEMA:
        return None
    actions = advisory.get("required_actions")
    if not isinstance(actions, list):
        return None
    facets = []
    for action in actions:
        if isinstance(action, dict) and isinstance(action.get("facet"), str) and action["facet"].strip():
            facets.append(action["facet"].strip())
    return facets


def refused_facets_in_texts(texts):
    """Lower-cased facet names named by a `JEV-REFUSED: <facet> <reason>` line."""
    out = set()
    for value in texts or ():
        if not isinstance(value, str):
            continue
        for match in JEV_REFUSED_RE.finditer(value):
            out.add(match.group(1).strip().lower())
    return out


def typesafe_called_in_commands(commands):
    """True when any Bash command in this turn's window names typesafe_client."""
    return any(isinstance(c, str) and TYPESAFE_CALL_RE.search(c) for c in commands or ())


def prompt_names_not_applicable(prompt):
    return bool(NOT_APPLICABLE_RE.search(prompt or ""))


def prompt_names_facet(prompt, facet):
    """A prompt "names" a facet when the exact key appears, or its words do
    (spaces, hyphens, or underscores interchangeable), case-insensitively."""
    if not isinstance(prompt, str) or not facet:
        return False
    prompt_l = prompt.lower()
    if facet.lower() in prompt_l:
        return True
    loose = re.sub(r"[_\s-]+", r"[_\\s-]+", re.escape(facet.lower()))
    return re.search(loose, prompt_l) is not None


def missing_facets(required, refused, jev_called):
    """The subset of `required` that is neither refused nor covered by a call.

    A single Jev `ask()` evaluates a map of questions against one state in one
    request (ops/typesafe_client.py's own "ASK TOGETHER" rule), so this module
    does not attribute one call to one named facet: any call this turn is
    evidence for every required facet that was not separately refused.
    """
    if not required:
        return []
    return [f for f in required if f.lower() not in refused and not jev_called]


def semantic_creation_receipt_missing(recs, wrote_a_file):
    """True when this turn wrote code and its post-write Jev review is either
    absent or came back "unavailable" (the review itself failed to run).

    A "skipped" or "clear" status is a legitimate, non-blocking outcome (no
    ambiguous candidate in the diff) and is NOT a missing receipt — only an
    absent review or an "unavailable" one counts, per decision 0b11c89b item 4.
    """
    if not wrote_a_file:
        return False
    reviews = find_postwrite_reviews(recs)
    if not reviews:
        return True
    return any(r.get("status") == "unavailable" for r in reviews)


def evaluate_required_actions(recs, window, window_texts, window_commands, wrote_a_file):
    """One-call summary for a Stop-time caller.

    `window` is this turn's transcript slice (used only to detect a total
    read failure upstream is not this module's job — callers pass it for
    symmetry with `recs`, this function only reads `recs`, `window_texts` and
    `window_commands`). Returns a dict:

        {"status": "required" | "none" | "unavailable",
         "required": [...], "missing": [...], "refused": [...]}

    "unavailable" means fail-open: the advisory itself could not be read, so
    nothing here is enforceable and the caller must log that rather than
    reopen. "none" means a real, readable advisory that required nothing.
    "required" means at least one facet was required, and `missing` (possibly
    empty) lists the facets neither refused nor covered by a Jev call, PLUS
    (folded in under the "semantic_creation" name) a receipt gap on a turn
    that wrote code.
    """
    receipt = find_build_advisory(recs)
    if receipt is None:
        return {"status": "unavailable", "required": [], "missing": [], "refused": []}
    required = required_facets(receipt)
    if required is None:
        return {"status": "unavailable", "required": [], "missing": [], "refused": []}
    if not required:
        return {"status": "none", "required": [], "missing": [], "refused": []}
    refused = refused_facets_in_texts(window_texts)
    called = typesafe_called_in_commands(window_commands)
    missing = set(missing_facets(required, refused, called))
    if ("semantic_creation" in required and "semantic_creation" not in refused
            and semantic_creation_receipt_missing(recs, wrote_a_file)):
        missing.add("semantic_creation")
    return {"status": "required", "required": required,
            "missing": sorted(missing), "refused": sorted(refused)}
