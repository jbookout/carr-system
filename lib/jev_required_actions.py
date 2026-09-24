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

WHERE THE DATA ACTUALLY LIVES — rewritten 2026-09-24 after an Opus review ran
the first cut over 7 real local transcripts (803 real advisory receipts, 572
with required_actions) and found it never fired. The first cut assumed hook
output lands as `attachment.stdout` holding a `hookSpecificOutput`-wrapped
JSON string; a real transcript instead stores injected hook context as
`{"attachment": {"type": "hook_additional_context", "content": [<json text>,
...]}}` — no `stdout` key, no `hookSpecificOutput` wrapper, `content` already
the plain additionalContext text(s). 130 of those 803 receipts (in one sampled
session alone) are also nested one level deeper: the top-level object is a
`rule-jev-message-delivery/v2` semantic receipt (hooks/rule-pack-preuse-
reselection.py's `_semantic_receipt()`) and the build receipt sits under its
`build_receipt` key. This module now reads both shapes. Verified directly
against a real ~/.claude/projects/*.jsonl session file before writing this.

CURRENT TURN ONLY. The advisory is read only from `latest_user_turn_index(recs)`
onward (with a one-record lookback for the observed off-by-one: in the sampled
transcript the `hook_additional_context` attachment for a turn's advisory was
recorded immediately AFTER that turn's own `type: "user"` record, never
before an EARLIER one) — never from a stale, earlier turn's advisory.

WHAT COUNTS AS EVIDENCE JEV WAS CALLED — rewritten. The first cut treated any
Bash command merely naming `typesafe_client` as a call, which a bare `echo
typesafe_client` or `grep ask ops/typesafe_client.py` satisfies without ever
reaching the vendor. ops/typesafe_client.py's `ask()` now appends a receipt
(session, ts, question ids, model, ok) to out/jev-calls.jsonl on every
SUCCESSFUL response, and this module matches a required facet against that
file: same session, a timestamp inside this turn's time window, and a
question id (or an explicit `facets` list on the call) naming the facet. One
batched `ask()` still evaluates several facets at once (ops/typesafe_client.py's
own "ASK TOGETHER" rule) — attribution is per named facet, not automatically
"any call clears everything".

A NAMED REFUSAL is a `JEV-REFUSED: <facet> <reason>` line in assistant text,
outside a fenced code block, inline-code span, or double-quoted span (so a
line quoting the syntax as an example does not itself count), with a
non-trivial reason on the same line. Facet spelling matches the facet keys in
ops/jev_build_advisory.py's FACETS (e.g. `architecture_or_design`),
case-insensitive.

FAILS OPEN. A missing transcript, an unreadable attachment, or a malformed
advisory record all read as "unavailable" rather than inventing a requirement
or raising — callers log that and let the turn through, the same posture as
every other gate in this repository.

Fixtures: ops/jev-required-actions-selftest.py
"""

import json
import re
from datetime import datetime, timezone

BUILD_RECEIPT_SCHEMA = "jev-build-turn-receipt/v1"
BUILD_ADVISORY_UNAVAILABLE_SCHEMA = "jev-build-advisory-unavailable/v1"
MESSAGE_DELIVERY_SCHEMA = "rule-jev-message-delivery/v2"
POSTWRITE_RECEIPT_SCHEMA = "jev-post-write-review/v2"

# A call this turn is a receipt in out/jev-calls.jsonl within this many
# seconds of the turn's own boundary timestamp (a real ask() carries its own
# short timeout — see ops/typesafe_client.py's TIMEOUT_SECONDS — so a call
# genuinely made "this turn" lands well inside a generous multi-minute band;
# this is deliberately wide rather than tight, because the turn boundary
# timestamp is the PROMPT's, and the call may happen minutes into a long turn).
CALL_WINDOW_SECONDS = 3600

# Known facet keys (ops/jev_build_advisory.py's FACETS) — used both to parse
# a batched JEV-REFUSED list ("semantic_creation, evidence_matching — reason")
# and to detect a facet named in a prompt (prompt_names_facet).
FACET_NAMES = (
    "architecture_or_design",
    "semantic_creation",
    "diagnosis",
    "verification_selection",
    "evidence_matching",
    "next_action_priority",
)
FACET_TOKEN_RE = re.compile(
    r"\b(" + "|".join(re.escape(f) for f in FACET_NAMES) + r")\b", re.I)
# A `JEV-REFUSED:` line, matched PER LINE so a blockquote (`>`-prefixed) line
# never counts (round-2 fix: the reviewer's literal example set includes a
# quoted line that must be excluded). Captures everything after the colon on
# that one line; facet names are then pulled out of it with FACET_TOKEN_RE
# rather than assuming exactly one token, since a real refusal legitimately
# names several facets on one line, comma- or "and"-joined, or embedded mid-
# sentence (e.g. "I made no separate Jev calls for semantic_creation,
# diagnosis, verification_selection or evidence_matching this turn because").
JEV_REFUSED_LINE_RE = re.compile(r"^[ \t]*JEV-REFUSED:[ \t]*(\S.*)$", re.I | re.M)
NOT_APPLICABLE_RE = re.compile(
    r"jev\s+required\s+actions\s*:\s*not\s+applicable\s*(?:—|-|--)\s*(\S.*)", re.I)
FENCE_RE = re.compile(r"```.*?```", re.S)
INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
DQUOTE_RE = re.compile(r'"[^"\n]*"')
SYSTEM_REMINDER_RE = re.compile(r"<system-reminder>.*?</system-reminder>", re.S | re.I)
SYNTHETIC_USER_PREFIXES = (
    "The following is the Codex agent history",
    "<environment_context>",
    "<app-context>",
)
# The reviewer's own literal examples use the placeholder reason word
# "reason" (6 chars) after every facet token is stripped out — e.g. form 1
# ("semantic_creation, evidence_matching — reason") and form 3
# ("next_action_priority. reason") both leave exactly "reason" as residual.
# Lowered from 8 (round-1 value) so those real forms still pass while still
# rejecting an empty or single-character residual.
MIN_REFUSAL_REASON_CHARS = 4
MIN_NOT_APPLICABLE_REASON_CHARS = 12
FACET_PROXIMITY_CHARS = 80


def _strip_noise(text):
    """Remove fenced code blocks, inline-code spans, and double-quoted spans,
    so an example of the JEV-REFUSED syntax quoted for illustration is not
    mistaken for a real refusal."""
    if not isinstance(text, str):
        return ""
    text = FENCE_RE.sub(" ", text)
    text = INLINE_CODE_RE.sub(" ", text)
    text = DQUOTE_RE.sub(" ", text)
    return text


def _hook_context_objects(rec):
    """Yield every JSON object embedded in this record's injected hook
    context, in the shape Claude Code actually writes it:
    `{"attachment": {"type": "hook_additional_context", "content": [...]}}`,
    each `content` item a JSON-encoded string."""
    attachment = rec.get("attachment") if isinstance(rec, dict) else None
    if not isinstance(attachment, dict) or attachment.get("type") != "hook_additional_context":
        return
    content = attachment.get("content")
    if not isinstance(content, list):
        return
    for item in content:
        if not isinstance(item, str) or not item.strip():
            continue
        try:
            obj = json.loads(item)
        except ValueError:
            continue
        if isinstance(obj, dict):
            yield obj


def _record_message(rec):
    if not isinstance(rec, dict):
        return None, None
    msg = rec.get("message")
    if isinstance(msg, dict):
        return msg, msg.get("role")
    payload = rec.get("payload")
    if isinstance(payload, dict) and payload.get("type") == "message":
        return payload, payload.get("role")
    return None, None


def _first_text_block(msg):
    content = msg.get("content") if isinstance(msg, dict) else None
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") in (
                    "text", "input_text", "output_text"):
                value = block.get("text")
                if isinstance(value, str):
                    return value
    return None


def is_real_user_turn(rec):
    """A genuine human-prompt boundary: role user/human, and not a synthetic
    wrapper (the Codex history/environment preamble). Used as the FALLBACK
    turn-boundary signal for a client whose transcript carries no `promptId`
    (see latest_user_turn_index) — Claude's own transcripts carry one, and
    for those `is_synthetic_continuation` is the more precise signal, because
    a Stop-hook reopen is role "user" with real (non-empty) text and would
    otherwise pass this check."""
    msg, role = _record_message(rec)
    if role not in ("user", "human") or not isinstance(msg, dict):
        return False
    first = _first_text_block(msg)
    if not isinstance(first, str):
        return False
    return not first.lstrip().startswith(SYNTHETIC_USER_PREFIXES)


def is_synthetic_continuation(rec):
    """A record that CONTINUES the current turn rather than starting a new
    one, even though it is role "user" with real, non-empty text: an
    automated Stop-hook reopen ("Stop hook feedback: ..."), a scheduled task
    notification, a cross-session message wrapper, or a message that is
    nothing but system-reminder wrapper text once every
    <system-reminder>...</system-reminder> block is stripped out.

    Found 2026-09-24 (Opus re-replay over real transcripts, PR #1224): a real
    Stop-hook-feedback reopen record is `{"type": "user", "promptId": <SAME
    id as the turn it reopened>, "message": {"role": "user", "content":
    "Stop hook feedback:\\n..."}}` — same shape as a genuine prompt, so a
    plain role check treated it as a fresh turn boundary and the advisory
    fell out of the window on the very next Stop.
    """
    msg, role = _record_message(rec)
    if role not in ("user", "human") or not isinstance(msg, dict):
        return False
    first = _first_text_block(msg)
    if not isinstance(first, str):
        return False
    stripped = first.lstrip()
    if stripped.startswith(("Stop hook feedback:", "<task-notification>",
                            "[SYSTEM NOTIFICATION", "[MESSAGE FROM NON-USER SOURCE")):
        return True
    without_reminders = SYSTEM_REMINDER_RE.sub("", first).strip()
    return bool(first.strip()) and not without_reminders


def _user_prompt_runs(recs):
    """Consecutive-by-index runs of type:"user" records sharing one
    `promptId`, in transcript order: [{"pid": ..., "start": i, "first_rec":
    rec}, ...]. Claude's transcript stamps every type:"user" record
    (a genuine prompt, its tool results, and any Stop-hook-feedback reopen
    of it) with the SAME promptId — verified directly against a real
    session — so a run is exactly "the records belonging to one submitted
    prompt", independent of the text-based heuristics below.
    """
    runs = []
    current_pid = object()  # sentinel that cannot equal a real promptId
    for i, rec in enumerate(recs or ()):
        if not isinstance(rec, dict) or rec.get("type") != "user" or "promptId" not in rec:
            continue
        pid = rec.get("promptId")
        if pid != current_pid:
            runs.append({"pid": pid, "start": i, "first_rec": rec})
            current_pid = pid
    return runs


def latest_user_turn_index(recs):
    """Index of the record where the CURRENT turn begins.

    Primary signal: `promptId` runs (see _user_prompt_runs). Starting from
    the LAST run, fold backward through any run whose own first record is a
    synthetic continuation (a Stop-hook reopen that was, for whatever
    reason, stamped with its own fresh promptId; a task notification; a
    system-reminder-only message) — the boundary keeps moving to that
    earlier run's start. It stops at the first run (walking backward) whose
    first record is a genuine, non-synthetic prompt; that run's start index
    is the turn boundary.

    FALLBACK for a transcript with no `promptId` at all (a non-Claude
    client): the old text-prefix heuristic, `is_real_user_turn`.

    Returns -1 when no boundary can be found at all.
    """
    recs = list(recs or ())
    runs = _user_prompt_runs(recs)
    if not runs:
        idx = -1
        for i, rec in enumerate(recs):
            if is_real_user_turn(rec):
                idx = i
        return idx
    boundary = runs[-1]["start"]
    for run in reversed(runs):
        boundary = run["start"]
        if not is_synthetic_continuation(run["first_rec"]):
            break
    return boundary


def current_turn_slice(recs):
    """This turn's records: from the latest real user-turn boundary onward.

    Starts one record early (an off-by-one lookback) because the sampled real
    transcript showed the build-advisory attachment for a turn recorded
    immediately AFTER that turn's own user record, and never before an
    earlier one — the lookback only protects against the opposite ordering
    without reaching back into a whole prior turn.
    """
    idx = latest_user_turn_index(recs)
    if idx < 0:
        return []
    start = max(0, idx - 1)
    return list(recs or ())[start:]


def _record_timestamp(rec):
    ts = rec.get("timestamp") if isinstance(rec, dict) else None
    if not isinstance(ts, str) or not ts.strip():
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def turn_boundary_timestamp(recs):
    """The timestamp of the current turn's own user record, or None."""
    idx = latest_user_turn_index(recs)
    if idx < 0:
        return None
    return _record_timestamp((recs or ())[idx])


def find_build_advisory(recs):
    """The current turn's build-turn receipt, or None.

    Scoped to current_turn_slice(recs) — decision 0b11c89b's "current turn
    only": an earlier turn's advisory must never satisfy this turn's check.
    Accepts either a direct receipt (schema jev-build-turn-receipt/v1) or one
    nested under a rule-jev-message-delivery/v2 semantic receipt's
    build_receipt key (hooks/rule-pack-preuse-reselection.py's
    `_semantic_receipt()` — this is the more common shape in real sessions).
    The LAST match in the slice wins.
    """
    found = None
    for rec in current_turn_slice(recs):
        for obj in _hook_context_objects(rec):
            schema = obj.get("schema")
            if schema == BUILD_RECEIPT_SCHEMA:
                found = obj
            elif schema == MESSAGE_DELIVERY_SCHEMA:
                nested = obj.get("build_receipt")
                if isinstance(nested, dict) and nested.get("schema") == BUILD_RECEIPT_SCHEMA:
                    found = nested
    return found


def find_postwrite_reviews(recs):
    """Every post-write Jev review receipt (schema jev-post-write-review/v2)
    found in `recs` (pass current_turn_slice(recs) to scope to this turn),
    in transcript order. Includes "unavailable" ones."""
    out = []
    for rec in recs or ():
        for obj in _hook_context_objects(rec):
            if obj.get("schema") == POSTWRITE_RECEIPT_SCHEMA:
                out.append(obj)
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
    """Lower-cased facet names named by a real `JEV-REFUSED: ...` line:
    outside a fenced/inline-code/quoted span, on a non-blockquoted line, with
    a non-trivial reason remaining after every facet-name token on that line
    is accounted for.

    Handles a facet LIST on one line — comma-separated
    ("semantic_creation, evidence_matching — reason"), "and"-joined
    ("semantic_creation and evidence_matching. reason"), a single facet
    ("next_action_priority. reason"), or facet names embedded anywhere in a
    sentence ("I made no separate Jev calls for semantic_creation,
    diagnosis, verification_selection or evidence_matching this turn because
    ..."). A `>`-prefixed blockquote line (someone quoting the syntax, not
    issuing it) never counts, even though _strip_noise's fence/inline-
    code/dquote stripping does not itself catch a blockquote.
    """
    out = set()
    for value in texts or ():
        cleaned = _strip_noise(value)
        for line in cleaned.splitlines():
            if line.lstrip().startswith(">"):
                continue
            m = JEV_REFUSED_LINE_RE.match(line)
            if not m:
                continue
            rest = m.group(1)
            facets_here = {f.lower() for f in FACET_TOKEN_RE.findall(rest)}
            if not facets_here:
                continue
            # Residual reason: the line's tail with every matched facet
            # token and connective punctuation/words stripped out.
            residual = FACET_TOKEN_RE.sub(" ", rest)
            residual = re.sub(r"[,:;.\-—]+", " ", residual)
            residual = re.sub(r"\b(and|or)\b", " ", residual, flags=re.I)
            residual = residual.strip()
            if len(residual) < MIN_REFUSAL_REASON_CHARS:
                continue
            out.update(facets_here)
    return out


def load_jev_call_receipts(path):
    """Every row in out/jev-calls.jsonl, best-effort. Never raises."""
    rows = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    rows.append(row)
    except OSError:
        return []
    return rows


def _loose_facet_pattern(facet):
    """A regex matching `facet`'s words with _/space/- treated as
    interchangeable, case handled by the caller lower-casing first.

    NOTE: built by joining escaped word-parts, never via re.sub(pattern,
    repl, ...) with a backslash-bearing repl — that treats the replacement as
    a backreference template and raises `bad escape \\s` at runtime, which a
    bare `except Exception` elsewhere can silently turn into "always false".
    """
    parts = [re.escape(p) for p in re.split(r"[_\s-]+", facet.lower()) if p]
    return r"[_\s-]+".join(parts) if parts else None


def _call_covers_facet(row, facet):
    facets = row.get("facets")
    if isinstance(facets, list) and any(
            isinstance(f, str) and f.strip().lower() == facet.lower() for f in facets):
        return True
    ids = row.get("question_ids")
    if isinstance(ids, list):
        loose = _loose_facet_pattern(facet)
        if not loose:
            return False
        pattern = re.compile(loose)
        return any(isinstance(i, str) and pattern.search(i.lower()) for i in ids)
    return False


def facets_called_this_turn(call_rows, session_id, boundary_ts, required):
    """The subset of `required` for which out/jev-calls.jsonl shows a
    successful call bound to this session and inside this turn's time window,
    whose question ids (or explicit facets list) name that facet."""
    if not required or boundary_ts is None or not session_id:
        return set()
    covered = set()
    for row in call_rows:
        if row.get("session") != session_id and row.get("session_id") != session_id:
            continue
        if row.get("ok") is False:
            continue
        ts = row.get("ts")
        row_dt = None
        if isinstance(ts, str):
            try:
                row_dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except ValueError:
                row_dt = None
        if row_dt is None:
            continue
        delta = (row_dt - boundary_ts).total_seconds()
        if delta < -5 or delta > CALL_WINDOW_SECONDS:
            continue
        for facet in required:
            if facet not in covered and _call_covers_facet(row, facet):
                covered.add(facet)
    return covered


def prompt_names_not_applicable(prompt):
    match = NOT_APPLICABLE_RE.search(_strip_noise(prompt or ""))
    if not match:
        return False
    return len(match.group(1).strip()) >= MIN_NOT_APPLICABLE_REASON_CHARS


def prompt_names_facet(prompt, facet):
    """A prompt names a facet with intent, not just the bare token: the facet
    key (loosely, with spaces/hyphens/underscores interchangeable) must
    appear within FACET_PROXIMITY_CHARS of the word "jev" — every real
    BUILD_ACTIONS instruction in ops/jev_build_advisory.py says "Jev" beside
    the facet's own instruction, so a genuine mention of intent reads the
    same way; a bare, isolated facet name dropped with no surrounding
    instruction does not."""
    if not isinstance(prompt, str) or not facet:
        return False
    prompt_l = prompt.lower()
    loose = _loose_facet_pattern(facet)
    if not loose:
        return False
    for match in re.finditer(loose, prompt_l):
        window = prompt_l[max(0, match.start() - FACET_PROXIMITY_CHARS):
                          match.end() + FACET_PROXIMITY_CHARS]
        if "jev" in window:
            return True
    return False


def missing_facets(required, refused, called):
    """The subset of `required` that is neither refused nor covered by a
    per-facet call this turn."""
    if not required:
        return []
    return [f for f in required if f.lower() not in refused and f not in called]


def _normalize_path(path):
    return str(path).replace("\\", "/") if isinstance(path, str) else ""


def _paths_match(written, reviewed):
    w, r = _normalize_path(written), _normalize_path(reviewed)
    return bool(w) and bool(r) and (w.endswith(r) or r.endswith(w))


def semantic_creation_receipt_missing(turn_recs, written_paths):
    """True when a file this turn wrote has no post-write Jev review recorded
    for it in this turn's own slice, or the review that ran on it came back
    "unavailable" (the review itself failed).

    Scoped to files actually written THIS turn: `written_paths` must be the
    set of paths mutated in the current turn's window, and reviews are read
    only from `turn_recs` (pass current_turn_slice(recs)). A "skipped" or
    "clear" status is a legitimate, non-blocking outcome (no ambiguous
    candidate in the diff) and is NOT a missing receipt.
    """
    written = {p for p in (written_paths or ()) if isinstance(p, str) and p}
    if not written:
        return False
    reviews = find_postwrite_reviews(turn_recs)
    if not reviews:
        return True
    reviewed_paths = set()
    any_unavailable = False
    for review in reviews:
        if review.get("status") == "unavailable":
            any_unavailable = True
        for entry in review.get("paths") or []:
            if isinstance(entry, dict) and isinstance(entry.get("path"), str):
                reviewed_paths.add(entry["path"])
    uncovered = {w for w in written if not any(_paths_match(w, r) for r in reviewed_paths)}
    return any_unavailable or bool(uncovered)


def evaluate_required_actions(recs, window_texts, jev_calls_path, session_id, written_paths):
    """One-call summary for a Stop-time caller.

    `recs` is the WHOLE transcript (this module scopes to the current turn
    itself); `window_texts` is the current turn's assistant text blocks (for
    JEV-REFUSED matching); `jev_calls_path` is out/jev-calls.jsonl;
    `written_paths` is the set of file paths mutated this turn. Returns:

        {"status": "required" | "none" | "unavailable",
         "required": [...], "missing": [...], "refused": [...],
         "turn_key": <str or None>}

    "unavailable" means fail-open: the advisory itself could not be read for
    this turn, so nothing here is enforceable and the caller must log that
    rather than reopen. "none" means a real, readable advisory that required
    nothing this turn. "required" means at least one facet was required, and
    `missing` (possibly empty) lists the facets neither refused nor covered
    by a per-facet Jev call this turn, PLUS (folded in under the
    "semantic_creation" name) a receipt gap on a turn that wrote code.
    `turn_key` is a value unique to THIS turn's advisory (its receipt id, or
    its prompt hash) for callers that need a per-turn latch identity rather
    than a per-session one.
    """
    turn_slice = current_turn_slice(recs)
    receipt = find_build_advisory(recs)
    if receipt is None:
        return {"status": "unavailable", "required": [], "missing": [],
                "refused": [], "turn_key": None}
    required = required_facets(receipt)
    turn_key = receipt.get("receipt_id") or receipt.get("prompt_sha256")
    if required is None:
        return {"status": "unavailable", "required": [], "missing": [],
                "refused": [], "turn_key": turn_key}
    if not required:
        return {"status": "none", "required": [], "missing": [],
                "refused": [], "turn_key": turn_key}
    refused = refused_facets_in_texts(window_texts)
    boundary_ts = turn_boundary_timestamp(recs)
    call_rows = load_jev_call_receipts(jev_calls_path)
    called = facets_called_this_turn(call_rows, session_id, boundary_ts, required)
    missing = set(missing_facets(required, refused, called))
    if ("semantic_creation" in required and "semantic_creation" not in refused
            and "semantic_creation" not in called
            and semantic_creation_receipt_missing(turn_slice, written_paths)):
        missing.add("semantic_creation")
    return {"status": "required", "required": required,
            "missing": sorted(missing), "refused": sorted(refused),
            "turn_key": turn_key}
