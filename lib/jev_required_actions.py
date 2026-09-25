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

ONE TURN, ONE BINDING ADVISORY (round 4). A turn is a genuine human prompt
plus everything folded into it: Stop-hook feedback, background task
notifications, cross-session messages, compaction summaries. Each may carry
its own build advisory, but only the human prompt's own advisory binds
(prompt_advisory_receipt); the continuations' advisories are not consulted,
so they can neither add a facet nor remove one.

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
file: same session, a timestamp from this turn's start to now, and a
question id (or an explicit `facets` list on the call) naming the facet. One
batched `ask()` still evaluates several facets at once (ops/typesafe_client.py's
own "ASK TOGETHER" rule) — attribution is per named facet.

WHAT A FACET CREDIT PROVES, STATED PLAINLY. A call is credited for every facet
its caller DECLARES in `facets` (or names in a question id). Nothing checks
that the questions are really about those facets, so ONE real call that
declares every facet clears every facet at once. That is the same strength as
the `JEV-REFUSED: <facet> <reason>` escape hatch: both prove the model made a
deliberate, recorded choice, not that Jev judged the substance. What the
server receipt adds is that the choice cost a real Jev round trip the model
cannot fake by writing a file.

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

SERVER-SIDE VERIFICATION (2026-09-24, after a reviewer bypassed every gate here
by appending ONE forged record — a fake user prompt, a fake build advisory, or
a fake out/jev-calls.jsonl receipt plus a fake python tool_use/tool_result
pair). Both files this module used to trust are writable by the gated model,
which runs as the same OS user as the hooks. So, when a caller passes
`server` (lib/jev_server_receipts.fetch_receipts_for's result):

  * a facet counts as CALLED only from a row the Worker's read door returns:
    ops/typesafe_client.py's ask() now goes through the Worker's ask-jev verb,
    which makes the vendor call itself and then appends a server-timestamped
    receipt, and the read door returns only receipts written by the calling
    principal that have a matching ask-jev tool_call row. A local receipt
    with no server row is logged as unverified and credits nothing;
  * the REQUIRED facets are recomputed from the server's own build_advisory
    rows — Jev's answers as the Worker recorded them — unioned over this
    turn's window, so an extra advisory the model makes for itself can only
    add a facet, never remove one; the transcript's advisory copy is used only
    when the server has no row for this turn, and is then marked unverified;
  * when the server cannot be read, answers too slowly for the hook's budget,
    or hits its row cap, nothing is credited and the result says so
    (`server.status` "unreachable" and its reason): every required facet not
    named in a refusal is missing, loudly, never silently passed.

DEFENSE IN DEPTH: THE UUID CHAIN. Claude Code writes every record with a
parentUuid naming the record IT wrote before (logicalParentUuid across a
compaction). A record appended by anyone else is therefore not an ancestor of
the newest genuine record. chain_view() walks back from that head — anchored
on the Stop payload's last_assistant_message, or on the PreToolUse payload's
tool_use_id — and the turn boundary, the prompt's advisory copy, and the
assistant text a refusal is read from must all be on that chain. Measured on
the 7 real local transcripts before writing this: 283 of 283 genuine human
prompts and 177 of 177 prompt advisories are ancestors of the head. Genuine
LEAVES exist (parallel tool results, PreToolUse hook context), so leaves are
not dropped wholesale; only the records the verdict hinges on are held to the
chain. Records that look like prompts but sit off the chain are returned as
orphans for the caller to log.

THE HONEST LIMIT: DETECTABLE, NOT PREVENTED. A model that REWRITES the
transcript (not appends) can splice a record onto the chain, and can make real
ask-jev calls. Worse, the model's OS user can read the database owner
credential (tools/db-tap.py reads it), so it can insert, backdate or delete
receipt rows, or disable the table's append-only trigger, directly in the
database. None of that is prevented. It is made DETECTABLE: a receipt inserted
directly has no matching tool_call row, so the read door never returns it and
the integrity audit in `./run.sh health` (read-jev-call-receipt-integrity)
flags it, along with a disabled trigger. NOT caught: a forger who ALSO
inserts a matching tool_call row by hand; a genuine receipt edited or
backdated in place (its tool_call twin still matches, and nothing compares the
two timestamps); a genuine receipt deleted; and a disable-then-re-enable of the
trigger between two audits. Moving that credential out of the model's
reach is Joe's separate, parked decision. hooks/gate-integrity.py states the
same posture for the gates themselves: with OS hardening off by Joe's
2026-08-10 ruling, same-uid tampering is detectable, not prevented.

Fixtures: ops/jev-required-actions-selftest.py, ops/jev-server-receipts-selftest.py
"""

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone

BUILD_RECEIPT_SCHEMA = "jev-build-turn-receipt/v1"
BUILD_ADVISORY_UNAVAILABLE_SCHEMA = "jev-build-advisory-unavailable/v1"
MESSAGE_DELIVERY_SCHEMA = "rule-jev-message-delivery/v2"
POSTWRITE_RECEIPT_SCHEMA = "jev-post-write-review/v2"

# A call counts for this turn when its receipt in out/jev-calls.jsonl is bound
# to this session id and its timestamp falls between the turn's own boundary
# (less CALL_CLOCK_SKEW_SECONDS) and NOW (plus the same skew). There is no
# upper duration cap any more: round 3 (2026-09-24) found 26 of 327 folded
# real turns ran past 60 minutes, and a real call at minute 90 was rejected by
# the old fixed 3600-second window. The session id is the binding; the turn
# boundary is the lower edge; "now" (the Stop time) is the upper edge.
CALL_CLOCK_SKEW_SECONDS = 5

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
# Role-"user" records that CONTINUE the current turn instead of starting one.
# Round 3 (2026-09-24) added the cross-session message wrappers: a real one
# reads "Another Claude session sent a message:\n<cross-session-message ...>"
# (origin kind "peer"), and was being treated as a fresh, unenforced turn.
CONTINUATION_PREFIXES = (
    "Stop hook feedback:",
    "<task-notification>",
    "[SYSTEM NOTIFICATION",
    "[MESSAGE FROM NON-USER SOURCE",
    "Another Claude session sent a message",
    "<cross-session-message",
    "[Cross-session delivery",
    "This session is being continued from a previous conversation",
)
CONTINUATION_ORIGIN_KINDS = ("task-notification", "peer")
SYNTHETIC_USER_PREFIXES = (
    "The following is the Codex agent history",
    "<environment_context>",
    "<app-context>",
)
# The residual reason (the line with every facet token and connective
# stripped) must be at least this long. Round 2 had lowered it to 4, which let
# "none" through; round 3 (2026-09-24) raises it to 12. All 27 real
# JEV-REFUSED lines in session f4d5b78a carry reasons well past this floor.
MIN_REFUSAL_REASON_CHARS = 12
# Round 3 (2026-09-24): a reason must also not be one of these placeholder
# phrases, compared after lower-casing and stripping punctuation. (The
# 12-character floor already rejects most of them; the list is the explicit
# statement of intent and catches padded variants such as "not needed here".)
REFUSAL_REASON_STOPLIST = frozenset((
    "none", "n/a", "na", "skip", "skipped", "not needed", "not needed here",
    "not applicable", "no reason", "reason", "tbd", "n a",
))
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
    if stripped.startswith(CONTINUATION_PREFIXES):
        return True
    # Claude's own origin stamp, when present, is authoritative: a genuine
    # prompt is kind "human"; a background task's notification is
    # "task-notification" and a cross-session message is "peer" (both seen
    # directly in a real session, round 3).
    origin = rec.get("origin") if isinstance(rec, dict) else None
    if isinstance(origin, dict) and origin.get("kind") in CONTINUATION_ORIGIN_KINDS:
        return True
    if isinstance(rec, dict) and rec.get("isCompactSummary"):
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


def _advisory_receipts(turn_recs):
    """Every build-turn receipt in `turn_recs`, in transcript order (direct,
    or nested under a rule-jev-message-delivery/v2 build_receipt key)."""
    out = []
    for rec in turn_recs or ():
        for obj in _hook_context_objects(rec):
            schema = obj.get("schema")
            if schema == BUILD_RECEIPT_SCHEMA:
                out.append(obj)
            elif schema == MESSAGE_DELIVERY_SCHEMA:
                nested = obj.get("build_receipt")
                if isinstance(nested, dict) and nested.get("schema") == BUILD_RECEIPT_SCHEMA:
                    out.append(nested)
    return out


def prompt_advisory_receipt(recs):
    """The genuine human prompt's own build advisory for the current turn, or
    None: the FIRST build receipt in the turn, read only from the records
    before the first folded continuation (a task notification, Stop-hook
    feedback, cross-session message, or compaction summary).

    Round 4 (2026-09-24, final Opus review of PR #1224): round 3 took the
    UNION of every advisory in the turn, which made each folded notification's
    advisory binding. Jev rates the `<task-notification>` text itself and
    assigns facets the human never asked for; in session f4d5b78a 65 of 78
    missing facets at Stops that already carried a refusal line came only
    from notification advisories, and each new one re-keyed the latch and
    reopened again. Continuation advisories are now not consulted at all, so
    they can neither add a facet nor remove one.
    """
    recs = list(recs or ())
    idx = latest_user_turn_index(recs)
    if idx < 0:
        return None
    for i in range(max(0, idx - 1), len(recs)):
        rec = recs[i]
        if i > idx and isinstance(rec, dict) and rec.get("type") == "user" \
                and is_synthetic_continuation(rec):
            return None
        receipts = _advisory_receipts([rec])
        if receipts:
            return receipts[0]
    return None


def turn_required_facets(recs):
    """The current turn's required facets: the genuine human prompt's own
    advisory only (prompt_advisory_receipt). Advisories carried by folded
    continuations are ignored.

    Returns (facets, turn_key): facets is None when the prompt's advisory is
    absent or unreadable (fail open), [] when it requires nothing, else its
    facets in order. turn_key is that advisory's receipt id (or prompt hash),
    stable however many notifications follow.
    """
    receipt = prompt_advisory_receipt(recs)
    if receipt is None:
        return None, None
    turn_key = receipt.get("receipt_id") or receipt.get("prompt_sha256")
    facets = required_facets(receipt)
    if facets is None:
        return None, turn_key
    ordered = []
    for facet in facets:
        if facet not in ordered:
            ordered.append(facet)
    return ordered, turn_key


def find_build_advisory(recs):
    """The current turn's build-turn receipt, or None.

    Scoped to current_turn_slice(recs) — decision 0b11c89b's "current turn
    only": an earlier turn's advisory must never satisfy this turn's check.
    Accepts either a direct receipt (schema jev-build-turn-receipt/v1) or one
    nested under a rule-jev-message-delivery/v2 semantic receipt's
    build_receipt key (hooks/rule-pack-preuse-reselection.py's
    `_semantic_receipt()` — this is the more common shape in real sessions).
    The LAST match in the slice wins.

    NOT the enforcement reader any more (round 3): a single receipt cannot
    represent a turn that folds in notifications carrying their own
    advisories. Enforcement uses turn_required_facets (the prompt's own advisory).
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


def refusal_reason_is_real(residual):
    """A refusal reason is at least MIN_REFUSAL_REASON_CHARS characters and is
    not a stoplisted placeholder ("none", "n/a", "skip", "not needed", ...)."""
    residual = " ".join((residual or "").split())
    if len(residual) < MIN_REFUSAL_REASON_CHARS:
        return False
    normalized = " ".join(re.sub(r"[^\w/ ]+", " ", residual.lower()).split())
    return normalized not in REFUSAL_REASON_STOPLIST


# A refusal reason that claims Jev could not be reached (bypass hunt, PR #1224).
# Such a refusal is FALSE when the same turn shows Jev answering, so it does not
# satisfy the facet then; see evaluate_required_actions.
OUTAGE_REASON_RE = re.compile(r"\b(unreachable|unavailable|down|402)\b", re.I)


def refused_facets_in_texts(texts):
    """The set of facets named by a real refusal (see refusals_in_texts)."""
    return set(refusals_in_texts(texts))


def refusals_in_texts(texts):
    """{facet: [reason, ...]} for every facet (lower-cased) named by a real
    `JEV-REFUSED: ...` line:
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
    out = {}
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
            if not refusal_reason_is_real(residual):
                continue
            reason = " ".join(residual.split())
            for facet in facets_here:
                out.setdefault(facet, []).append(reason)
    return out


JEV_CALLS_BASENAME = "jev-calls.jsonl"
# A command that plausibly WRITES rather than reads: a redirect, tee, sed -i,
# cp/mv/install onto it, or an interpreter write call. Deliberately loose —
# this only classifies a detection event, it never decides a verdict.
_WRITE_HINT_RE = re.compile(
    r"((?<![0-9&>])>{1,2}(?![&>])|\btee\b|\bsed\s+-i|\b(cp|mv|install|rsync|truncate|touch|dd)\b|"
    r"\.write\(|write_text|writeFileSync|appendFileSync|open\([^)]*['\"][awx])")


def jev_calls_log_mentions(turn_recs):
    """FORGERY DETECTION (round 3, "detectable, not prevented" — decision
    d47931da). ops/typesafe_client.py's ask() is the ONLY legitimate writer of
    out/jev-calls.jsonl, and it writes from inside Python, so a legitimate
    call's own tool command never names the file. Every tool call in the turn
    whose command or file path names it is returned, so the Stop gate can
    record a detection event:

        [{"tool": <name>, "write_like": bool, "excerpt": <first 200 chars>}]

    A read (tail, grep, wc) is still returned, with write_like False: the
    event is a record for a human to look at, not a verdict.
    """
    found = []
    for rec in turn_recs or ():
        msg = rec.get("message") if isinstance(rec, dict) else None
        content = msg.get("content") if isinstance(msg, dict) else None
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            value = block.get("input")
            if not isinstance(value, dict):
                continue
            surface = " ".join(str(value.get(k) or "") for k in (
                "command", "file_path", "path", "notebook_path"))
            if JEV_CALLS_BASENAME not in surface:
                continue
            name = block.get("name") or ""
            write_like = (name in ("Write", "Edit", "MultiEdit", "NotebookEdit")
                          or bool(_WRITE_HINT_RE.search(surface)))
            found.append({"tool": name, "write_like": write_like,
                          "excerpt": surface.strip()[:200]})
    return found


# RECEIPT PROVENANCE BACKSTOP (round 4). jev_calls_log_mentions only sees a
# command that NAMES the ledger; `f=out/jev-calls; echo x >> $f.jsonl` or a
# script file that appends to it would not. So every receipt credited to a
# turn is also checked against the session transcript: a real receipt is
# written by ask() inside a Python process, which means a Bash command running
# python, or an Agent whose subagent runs one, was in flight at the receipt's
# timestamp (within RECEIPT_EXPLAIN_SECONDS after it finished). A receipt with
# no such tool call is recorded as `jev_receipt_unexplained`. Detectable, not
# prevented (decision d47931da): it never changes the verdict.
RECEIPT_EXPLAIN_SECONDS = 120
_PYTHON_CMD_RE = re.compile(r"python")
_TOOL_USE_ID_RE = re.compile(r"<tool-use-id>([^<\s]+)</tool-use-id>")


def _parse_ts(value):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _explaining_intervals(recs):
    """(start, end) for every Bash-running-python and every Agent tool call
    in `recs`. end is when it finished (its tool_result; for a background
    Agent, the task notification naming its tool-use id), or None while it is
    still running."""
    starts, kinds, background = {}, {}, {}
    results, notified = {}, {}
    for rec in recs or ():
        if not isinstance(rec, dict):
            continue
        when = _record_timestamp(rec)
        msg = rec.get("message")
        content = msg.get("content") if isinstance(msg, dict) else None
        if isinstance(content, str):
            for tid in _TOOL_USE_ID_RE.findall(content):
                notified.setdefault(tid, when)
            continue
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            kind = block.get("type")
            if kind == "tool_use":
                name = block.get("name") or ""
                value = block.get("input") if isinstance(block.get("input"), dict) else {}
                tid = block.get("id")
                if name in ("Agent", "Task"):
                    kinds[tid] = "agent"
                    background[tid] = value.get("run_in_background") is not False
                elif name in ("Bash", "functions.exec") and _PYTHON_CMD_RE.search(
                        str(value.get("command") or value.get("cmd") or "")):
                    kinds[tid] = "python"
                else:
                    continue
                starts[tid] = when
            elif kind == "tool_result":
                results.setdefault(block.get("tool_use_id"), when)
            elif kind == "text":
                for tid in _TOOL_USE_ID_RE.findall(block.get("text") or ""):
                    notified.setdefault(tid, when)
    out = []
    for tid, start in starts.items():
        if start is None:
            continue
        if kinds[tid] == "agent" and background.get(tid):
            end = notified.get(tid)
        else:
            end = results.get(tid)
        out.append((start, end))
    return out


def unexplained_receipts(recs, credited_rows):
    """The credited receipt rows that no in-flight Python tool call in the
    session transcript `recs` explains (see RECEIPT_EXPLAIN_SECONDS)."""
    intervals = _explaining_intervals(recs)
    out = []
    for row in credited_rows or ():
        row_dt = _parse_ts(row.get("ts"))
        if row_dt is None:
            out.append(row)
            continue
        explained = False
        for start, end in intervals:
            if (start - row_dt).total_seconds() > CALL_CLOCK_SKEW_SECONDS:
                continue
            if end is None or (row_dt - end).total_seconds() <= RECEIPT_EXPLAIN_SECONDS:
                explained = True
                break
        if not explained:
            out.append(row)
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


def facets_called_this_turn(call_rows, session_id, boundary_ts, required, now=None):
    """The subset of `required` for which out/jev-calls.jsonl shows a
    successful call bound to this session, timestamped from this turn's start
    to `now` (default: the current time), whose question ids (or explicit
    facets list) name that facet. No upper duration cap — see
    CALL_CLOCK_SKEW_SECONDS."""
    covered, _rows = credited_calls_this_turn(call_rows, session_id, boundary_ts, required, now)
    return covered


def answered_calls_this_turn(call_rows, session_id, boundary_ts, now=None):
    """Every successful (ok true) receipt bound to this session and this turn's
    window, whatever facets it names: evidence that Jev answered this turn."""
    if boundary_ts is None or not session_id:
        return []
    if now is None:
        now = datetime.now(timezone.utc)
    upper = (now - boundary_ts).total_seconds() + CALL_CLOCK_SKEW_SECONDS
    rows = []
    for row in call_rows:
        if row.get("session") != session_id and row.get("session_id") != session_id:
            continue
        if row.get("ok") is not True:
            continue
        row_dt = _parse_ts(row.get("ts"))
        if row_dt is None:
            continue
        delta = (row_dt - boundary_ts).total_seconds()
        if -CALL_CLOCK_SKEW_SECONDS <= delta <= upper:
            rows.append(row)
    return rows


def credited_calls_this_turn(call_rows, session_id, boundary_ts, required, now=None):
    """(covered facets, the receipt rows that credited at least one of them)."""
    if not required or boundary_ts is None or not session_id:
        return set(), []
    if now is None:
        now = datetime.now(timezone.utc)
    upper = (now - boundary_ts).total_seconds() + CALL_CLOCK_SKEW_SECONDS
    covered, credited = set(), []
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
        if delta < -CALL_CLOCK_SKEW_SECONDS or delta > upper:
            continue
        hit = [f for f in required if _call_covers_facet(row, f)]
        if hit:
            covered.update(hit)
            credited.append(row)
    return covered, credited


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


# ---------------------------------------------------------------------------
# THE UUID CHAIN (defense in depth — see the module docstring).
# ---------------------------------------------------------------------------

CHAIN_MESSAGE_TYPES = ("user", "assistant", "attachment", "system")


def _parent_uuid(rec):
    parent = rec.get("parentUuid")
    if not parent:
        # A compaction boundary carries parentUuid null and names the record
        # it logically continues in logicalParentUuid.
        parent = rec.get("logicalParentUuid")
    return parent if isinstance(parent, str) and parent else None


def _assistant_text(rec):
    msg = rec.get("message") if isinstance(rec, dict) else None
    if not isinstance(msg, dict) or msg.get("role") != "assistant":
        return None
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [b.get("text") for b in content
                 if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str)]
        return "\n".join(parts) if parts else None
    return None


def _tool_use_ids(rec):
    msg = rec.get("message") if isinstance(rec, dict) else None
    content = msg.get("content") if isinstance(msg, dict) else None
    if not isinstance(content, list):
        return ()
    return tuple(b.get("id") for b in content
                 if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("id"))


def chain_head(recs, anchor_text=None, anchor_tool_use_id=None):
    """(head record, anchor kind). The head is the LAST record carrying the
    Stop payload's final assistant text, or the assistant record carrying the
    PreToolUse payload's tool_use id; failing both, the last record with a
    uuid. Returns (None, "none") when no record carries a uuid."""
    with_uuid = [r for r in recs or () if isinstance(r, dict) and r.get("uuid")]
    if not with_uuid:
        return None, "none"
    if isinstance(anchor_tool_use_id, str) and anchor_tool_use_id:
        for rec in reversed(with_uuid):
            if anchor_tool_use_id in _tool_use_ids(rec):
                return rec, "tool_use_id"
    if isinstance(anchor_text, str) and anchor_text.strip():
        want = anchor_text.strip()
        for rec in reversed(with_uuid):
            text = _assistant_text(rec)
            if isinstance(text, str) and text.strip() == want:
                return rec, "last_assistant_message"
    return with_uuid[-1], "newest_record"


def ancestor_uuids(recs, head):
    """Every uuid on the parent chain from `head` back to the root."""
    by_uuid = {r["uuid"]: r for r in recs or () if isinstance(r, dict) and r.get("uuid")}
    seen = set()
    cur = head
    while isinstance(cur, dict) and cur.get("uuid") and cur["uuid"] not in seen:
        seen.add(cur["uuid"])
        parent = _parent_uuid(cur)
        cur = by_uuid.get(parent) if parent else None
    return seen


def _is_genuine_prompt(rec):
    return (isinstance(rec, dict) and rec.get("type") == "user"
            and is_real_user_turn(rec) and not is_synthetic_continuation(rec))


def chain_view(recs, anchor_text=None, anchor_tool_use_id=None):
    """The transcript as the gate may trust it for its turn boundary, its
    prompt advisory, and its refusal text:

        {"recs": [...], "chain": bool, "anchor": <kind>, "head": <uuid>,
         "orphans": [{"uuid", "type", "timestamp", "kind"}, ...]}

    `recs` keeps every record on the ancestor chain of the anchored head plus
    every metadata record that carries no uuid and is not a message (queue
    operations, titles, snapshots). A user/assistant/attachment/system record
    that is OFF the chain is dropped; the ones that would have mattered (a
    genuine-looking prompt, a build advisory, a JEV-REFUSED line) are listed
    in `orphans`. When no record carries a uuid at all (a non-Claude client,
    or a hand-built fixture), the chain cannot be walked: `chain` is False
    and `recs` is the input unchanged."""
    recs = [r for r in recs or () if isinstance(r, dict)]
    head, anchor = chain_head(recs, anchor_text, anchor_tool_use_id)
    if head is None:
        return {"recs": recs, "chain": False, "anchor": anchor, "head": None, "orphans": []}
    anc = ancestor_uuids(recs, head)
    kept, orphans = [], []
    for rec in recs:
        uid = rec.get("uuid")
        if uid in anc:
            kept.append(rec)
            continue
        if not uid and rec.get("type") not in CHAIN_MESSAGE_TYPES:
            kept.append(rec)
            continue
        kind = None
        if _is_genuine_prompt(rec):
            kind = "user_prompt"
        elif _advisory_receipts([rec]):
            kind = "build_advisory"
        elif refusals_in_texts([_assistant_text(rec) or ""]):
            kind = "refusal_text"
        if kind:
            orphans.append({"uuid": uid, "type": rec.get("type"),
                            "timestamp": rec.get("timestamp"), "kind": kind})
    return {"recs": kept, "chain": True, "anchor": anchor, "head": head.get("uuid"),
            "orphans": orphans}


def full_turn_slice(recs, trusted_recs):
    """This turn's records from the UNFILTERED transcript, starting at the
    trusted (on-chain) turn boundary: for data that legitimately lives on
    leaves (written paths, post-write reviews, tool calls naming the ledger),
    none of which can remove a required facet."""
    idx = latest_user_turn_index(trusted_recs)
    if idx < 0:
        return []
    boundary = trusted_recs[idx]
    recs = list(recs or ())
    uid = boundary.get("uuid") if isinstance(boundary, dict) else None
    for i, rec in enumerate(recs):
        if rec is boundary or (uid and isinstance(rec, dict) and rec.get("uuid") == uid):
            return recs[max(0, i - 1):]
    return current_turn_slice(recs)


# ---------------------------------------------------------------------------
# SERVER-SIDE RECEIPTS (see the module docstring).
# ---------------------------------------------------------------------------

# Imported lazily-safe: lib/rule_delivery_preuse.py is the one home of the
# threshold ops/jev_build_advisory.py applies when it turns Jev's facet
# probabilities into required_actions. The gate recomputes the same thing from
# the server's copy of those probabilities.
try:
    from lib.rule_delivery_preuse import BUILD_ACTION_THRESHOLD
except Exception:  # pragma: no cover - the constant is also pinned by the selftest
    BUILD_ACTION_THRESHOLD = 0.50

# Server clock versus the transcript's clock (both NTP-disciplined).
SERVER_SKEW_SECONDS = 10
# How long before its own prompt record a prompt's advisory row may land (the
# UserPromptSubmit hook can finish before the record is stamped). Used only to
# tell a repeated prompt text's rows apart.
ADVISORY_LEAD_SECONDS = 120
PURPOSE_CALL = "call"
PURPOSE_BUILD_ADVISORY = "build_advisory"


def prompt_digest(value):
    """sha256 of the canonical JSON of `value` — the same digest the
    UserPromptSubmit hook stamps as prompt_sha256 and the Worker stores for a
    build_advisory row (lib/rule_delivery_preuse.py's digest())."""
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")).hexdigest()


def _prompt_digests(rec):
    """Every digest this prompt record's text could have been submitted as:
    each text block, and the text blocks joined."""
    msg, _role = _record_message(rec)
    content = msg.get("content") if isinstance(msg, dict) else None
    texts = []
    if isinstance(content, str):
        texts.append(content)
    elif isinstance(content, list):
        blocks = [b.get("text") for b in content
                  if isinstance(b, dict) and b.get("type") in ("text", "input_text")
                  and isinstance(b.get("text"), str)]
        texts.extend(blocks)
        if blocks:
            texts.append("\n".join(blocks))
    return {prompt_digest(t) for t in texts}


def _chain_prompt_texts(trusted_recs):
    """Digests of every text on the trusted chain that the UserPromptSubmit
    hook could have been handed: each user record's text, and each
    queued_command attachment's prompt."""
    out = set()
    for rec in trusted_recs or ():
        if not isinstance(rec, dict):
            continue
        if rec.get("type") == "user":
            out |= _prompt_digests(rec)
            # A cross-session message is recorded with a one-line preface
            # ("Another Claude session sent a message:") and sometimes a
            # trailing safety note, neither of which the hook saw.
            first = _first_text_block(rec.get("message") or {})
            if isinstance(first, str) and "<cross-session-message" in first:
                body = first[first.index("<cross-session-message"):]
                out.add(prompt_digest(body))
                close = "</cross-session-message>"
                if close in body:
                    out.add(prompt_digest(body[:body.index(close) + len(close)]))
        if rec.get("type") == "queue-operation" and isinstance(rec.get("content"), str):
            out.add(prompt_digest(rec["content"]))
        attachment = rec.get("attachment")
        if isinstance(attachment, dict) and attachment.get("type") == "queued_command":
            prompt = attachment.get("prompt")
            if isinstance(prompt, str):
                out.add(prompt_digest(prompt))
            elif isinstance(prompt, list):
                blocks = [b.get("text") for b in prompt
                          if isinstance(b, dict) and isinstance(b.get("text"), str)]
                out |= {prompt_digest(t) for t in blocks}
                if blocks:
                    out.add(prompt_digest("\n".join(blocks)))
    return out


def _genuine_prompt_indices(recs):
    runs = _user_prompt_runs(recs)
    if runs:
        return [run["start"] for run in runs if not is_synthetic_continuation(run["first_rec"])]
    return [i for i, rec in enumerate(recs) if is_real_user_turn(rec)]


def facets_from_advisory_answers(answers):
    """Required facets from a server build_advisory row's answers, applying
    the same threshold ops/jev_build_advisory.py applies. None when the
    answers are not a complete, readable set."""
    if not isinstance(answers, dict):
        return None
    out = []
    for facet in FACET_NAMES:
        answer = answers.get(facet)
        if not isinstance(answer, dict):
            return None
        try:
            value = float(answer.get("noul"))
        except (TypeError, ValueError):
            return None
        if not 0.0 <= value <= 1.0:
            return None
        if value >= BUILD_ACTION_THRESHOLD:
            out.append(facet)
    return out


def _binding_bounds(trusted_recs, boundary_ts):
    """(current digests, earlier digests, lower edge) for binding_advisory_rows.
    The lower edge is the previous genuine prompt's timestamp; a prompt whose
    text repeats an earlier one ("continue") shares its digest, so for a
    repeated text the row must also sit near this turn's own boundary."""
    indices = _genuine_prompt_indices(trusted_recs)
    idx = latest_user_turn_index(trusted_recs)
    current = _prompt_digests(trusted_recs[idx]) if idx >= 0 else set()
    earlier, prev_ts = set(), None
    for i in indices:
        if i < idx:
            earlier |= _prompt_digests(trusted_recs[i])
            prev_ts = _record_timestamp(trusted_recs[i]) or prev_ts
    # Round 4's rule, kept: a folded continuation (task notification, Stop
    # feedback, cross-session message, a message queued mid-turn, a
    # "continue from where you left off" resume) gets its own advisory from
    # the same hook, and that advisory does NOT bind the turn. Its server row
    # is recognised by the continuation's own text on the chain — a user
    # record, or a queued_command attachment (the shape a mid-turn message
    # takes; measured on the real transcripts, every otherwise-unmatched
    # advisory row was one of these). What stays unmatched is a row whose
    # text appears nowhere on the chain: one the model asked for itself.
    earlier |= _chain_prompt_texts(trusted_recs) - current
    lower = prev_ts
    if boundary_ts is not None and current & earlier:
        near = boundary_ts - timedelta(seconds=ADVISORY_LEAD_SECONDS)
        lower = near if lower is None or near > lower else lower
    return current, earlier, lower


def server_read_since(trusted_recs):
    """F6: the earliest server time any row this turn's verdict can use was
    recorded at, as ISO-8601. None, only when no earlier genuine prompt bounds
    it, leaves the read verb's own default: 24 hours before the server clock.
    The gates read the server from here, by session, oldest rows first,
    instead of taking the newest N rows."""
    boundary_ts = turn_boundary_timestamp(trusted_recs)
    if boundary_ts is None:
        return None
    _current, _earlier, lower = _binding_bounds(trusted_recs, boundary_ts)
    if lower is None:
        return None
    edge = boundary_ts - timedelta(seconds=max(SERVER_SKEW_SECONDS, ADVISORY_LEAD_SECONDS))
    edge = lower if lower < edge else edge
    return edge.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def binding_advisory_rows(server_rows, trusted_recs, boundary_ts, now=None):
    """The server build_advisory rows that bind this turn (see the module
    docstring): every row whose prompt digest matches this turn's prompt and
    was recorded after the PREVIOUS genuine prompt, plus every row recorded
    from this turn's boundary onward that does not match an EARLIER prompt.
    Returns (rows, unmatched_rows)."""
    if boundary_ts is None:
        return [], []
    if now is None:
        now = datetime.now(timezone.utc)
    current, earlier, lower = _binding_bounds(trusted_recs, boundary_ts)
    rows, unmatched = [], []
    upper = now + timedelta(seconds=SERVER_SKEW_SECONDS)
    for row in server_rows or ():
        if row.get("purpose") != PURPOSE_BUILD_ADVISORY:
            continue
        at = _parse_ts(row.get("recorded_at"))
        if at is None or at > upper:
            continue
        sha = row.get("prompt_sha256")
        if sha in current and (lower is None or at > lower):
            rows.append(row)
        elif (at >= boundary_ts - timedelta(seconds=SERVER_SKEW_SECONDS)
              and sha not in earlier and sha not in current):
            rows.append(row)
            unmatched.append(row)
    return rows, unmatched


def server_credited_calls(server_rows, boundary_ts, required, now=None):
    """(covered facets, crediting rows) from server `call` rows recorded in
    this turn's window."""
    if not required or boundary_ts is None:
        return set(), []
    if now is None:
        now = datetime.now(timezone.utc)
    lower = boundary_ts - timedelta(seconds=SERVER_SKEW_SECONDS)
    upper = now + timedelta(seconds=SERVER_SKEW_SECONDS)
    covered, credited = set(), []
    for row in server_rows or ():
        if row.get("purpose") != PURPOSE_CALL:
            continue
        at = _parse_ts(row.get("recorded_at"))
        if at is None or at < lower or at > upper:
            continue
        hit = [f for f in required if _call_covers_facet(row, f)]
        if hit:
            covered.update(hit)
            credited.append(row)
    return covered, credited


def unverified_local_receipts(local_rows, server_rows):
    """Local out/jev-calls.jsonl rows credited-looking for this turn that the
    server has no row for: a forged row, or a call that fell back to the
    vendor directly. Either way it credits nothing."""
    server_ids = {r.get("receipt_id") for r in server_rows or () if r.get("receipt_id")}
    return [row for row in local_rows or ()
            if not row.get("server_receipt_id") or row.get("server_receipt_id") not in server_ids]


def server_required_facets(server_rows, trusted_recs, boundary_ts, transcript_required,
                           turn_key, now=None):
    """(required, turn_key, basis, unmatched_rows) for this turn.

    `server_rows` None means the server could not be read. basis is
    "server" (the union of the binding server build_advisory rows),
    "server_and_transcript" (the transcript copy named a facet the server rows
    did not — kept, since only adding is safe), "transcript_unverified" (no
    readable server row; the on-chain transcript copy is all there is), or
    None (nothing readable anywhere: fail open, as before)."""
    unmatched = []
    if server_rows is not None:
        rows, unmatched = binding_advisory_rows(server_rows, trusted_recs, boundary_ts, now=now)
        union, readable = [], False
        for row in rows:
            facets = facets_from_advisory_answers(row.get("answers"))
            if facets is None:
                continue
            readable = True
            for facet in facets:
                if facet not in union:
                    union.append(facet)
        if readable:
            required = [f for f in FACET_NAMES if f in union]
            turn_key = turn_key or rows[0].get("receipt_id")
            if transcript_required and set(transcript_required) - set(required):
                both = set(required) | set(transcript_required)
                return ([f for f in FACET_NAMES if f in both], turn_key,
                        "server_and_transcript", unmatched)
            return required, turn_key, "server", unmatched
    if transcript_required is not None:
        return transcript_required, turn_key, "transcript_unverified", unmatched
    return None, turn_key, None, unmatched


def binding_required_facets(recs, server, anchor_text=None, anchor_tool_use_id=None,
                            now=None):
    """For a PreToolUse caller (hooks/executor-tier-gate.py): this turn's
    required facets as the server records them, from the chain-trusted view.
    Returns (facets or None, turn_key, info) with info {"basis", "server",
    "reason", "chain", "orphans"}."""
    view = chain_view(recs, anchor_text=anchor_text, anchor_tool_use_id=anchor_tool_use_id)
    trusted = view["recs"]
    transcript_required, turn_key = turn_required_facets(trusted)
    boundary_ts = turn_boundary_timestamp(trusted)
    server_ok = isinstance(server, dict) and server.get("status") == "ok"
    rows = [r for r in (server.get("receipts") or []) if isinstance(r, dict)] if server_ok else None
    required, turn_key, basis, _unmatched = server_required_facets(
        rows, trusted, boundary_ts, transcript_required, turn_key, now=now)
    return required, turn_key, {
        "basis": basis, "server": "ok" if server_ok else "unreachable",
        "reason": None if server_ok else (server.get("reason") if isinstance(server, dict) else None),
        "chain": view["chain"], "orphans": view["orphans"]}


def evaluate_required_actions(recs, window_texts, jev_calls_path, session_id, written_paths,
                              now=None, server=None, full_turn_recs=None):
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

    With `server` (lib/jev_server_receipts.fetch_receipts_for's result)
    the verdict is taken against the server's own rows — see the module
    docstring's SERVER-SIDE VERIFICATION. `recs` should then be the
    chain-trusted view (chain_view()["recs"]) and `full_turn_recs` the
    unfiltered turn slice (full_turn_slice()) for the leaf-borne data. The
    result gains a "server" block: {"status", "reason", "advisory_basis",
    "unmatched_advisories", "unverified_local_receipts"}. Without `server`
    the pre-server behaviour is unchanged (kept for callers and fixtures that
    predate it).
    """
    turn_slice = full_turn_recs if full_turn_recs is not None else current_turn_slice(recs)
    transcript_required, turn_key = turn_required_facets(recs)
    boundary_ts = turn_boundary_timestamp(recs)
    call_rows = load_jev_call_receipts(jev_calls_path)
    server_block = None
    server_rows = []
    required = transcript_required
    if server is not None:
        server_ok = isinstance(server, dict) and server.get("status") == "ok"
        if server_ok:
            server_rows = [r for r in (server.get("receipts") or []) if isinstance(r, dict)]
        server_block = {"status": "ok" if server_ok else "unreachable",
                        "reason": None if server_ok else (
                            server.get("reason") if isinstance(server, dict) else "no_result"),
                        "advisory_basis": None, "unmatched_advisories": [],
                        "unverified_local_receipts": []}
        required, turn_key, basis, unmatched = server_required_facets(
            server_rows if server_ok else None, recs, boundary_ts,
            transcript_required, turn_key, now=now)
        server_block["advisory_basis"] = basis
        server_block["unmatched_advisories"] = [
            {k: r.get(k) for k in ("receipt_id", "recorded_at", "prompt_sha256")}
            for r in unmatched]
    if required is None or not required:
        out = {"status": "unavailable" if required is None else "none",
               "required": [], "missing": [], "refused": [], "turn_key": turn_key}
        if server_block is not None:
            out["server"] = server_block
        return out
    refusals = refusals_in_texts(window_texts)
    if server_block is None:
        called, credited = credited_calls_this_turn(
            call_rows, session_id, boundary_ts, required, now=now)
        answered = answered_calls_this_turn(call_rows, session_id, boundary_ts, now=now)
    else:
        # CREDIT COMES ONLY FROM THE SERVER. A local receipt that looks like it
        # covers a facet but has no server row is recorded, never counted.
        called, credited = server_credited_calls(server_rows, boundary_ts, required, now=now)
        _local_covered, local_credited = credited_calls_this_turn(
            call_rows, session_id, boundary_ts, required, now=now)
        server_block["unverified_local_receipts"] = [
            {k: row.get(k) for k in ("ts", "session", "facets", "question_ids",
                                     "server_receipt_id", "server_error")}
            for row in unverified_local_receipts(local_credited, server_rows)]
        answered = [r for r in server_rows if r.get("purpose") == PURPOSE_CALL]
        if server_block["status"] != "ok":
            # Nothing is verifiable; a local receipt still shows Jev answering,
            # which is all the false-outage rule below needs.
            answered = answered_calls_this_turn(call_rows, session_id, boundary_ts, now=now)
    # FALSE-OUTAGE REFUSALS (bypass hunt, PR #1224). A refusal whose every
    # reason claims Jev was unreachable/unavailable/down/402 does not satisfy
    # its facet when this turn shows Jev answering: a successful receipt in
    # this session's turn window, or the prompt's own advisory (readable, so
    # not "unavailable" — which it always is on this path, since required
    # facets come only from a readable advisory). Refusals giving any other
    # reason still count. The facet then stays missing and the gate names the
    # contradiction.
    jev_answered = {"advisory_answered": True, "receipts_ok": len(answered)}
    contradicted = sorted(
        facet for facet, reasons in refusals.items()
        if reasons and all(OUTAGE_REASON_RE.search(r) for r in reasons))
    refused = {f for f in refusals if f not in contradicted}
    missing = set(missing_facets(required, refused, called))
    if ("semantic_creation" in required and "semantic_creation" not in refused
            and "semantic_creation" not in called
            and semantic_creation_receipt_missing(turn_slice, written_paths)):
        missing.add("semantic_creation")
    out = {"status": "required", "required": required,
           "missing": sorted(missing), "refused": sorted(refused),
           "turn_key": turn_key,
           "contradicted_refusals": [f for f in contradicted if f in missing],
           "jev_answered": jev_answered,
           "credited_receipts": [
               {k: row.get(k) for k in ("ts", "recorded_at", "receipt_id", "session",
                                        "facets", "question_ids")}
               for row in credited]}
    if server_block is not None:
        out["server"] = server_block
    return out
