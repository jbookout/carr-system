"""typesafe_client.py — the one way CARR calls TypeSafe's Jev decision model.

WHAT JEV IS, because the shape decides how this file is written. Jev takes text
plus typed questions and returns calibrated probabilities. It does not write
text, produce code, or explain itself. Three question types exist and nothing
else: a yes/no probability (noul), a pick-one over a defined set (choice), and
a position on ordered levels (score). Code owns the workflow; the model supplies
the semantic judgment where ordinary code has none.

THIS FILE IS A LIBRARY ON PURPOSE. It carries no shebang and no main guard, and
it must never gain either. A .py file with either becomes a registered script
entrypoint in the sealed source inventory — see isScriptEntrypoint() in
ops/scac-mutation-inventory.mjs — which moves the frontier and owes a
forward-only registry successor. Callers are entrypoints that already exist.
Adding a shebang here turns a one-line import into a multi-file sealing cascade.

That detector is a REGEX OVER THE WHOLE FILE and does not know what a docstring
is, so writing the guard construct out even as an example — in prose, in a
comment, inside backticks — is enough to seal this file. The first draft of
this paragraph did exactly that and ops/typesafe-client-selftest.py caught it.
Describe the construct; never spell it.

FOUR THINGS THE VENDOR'S OWN DOCS SAY THAT THIS FILE ENFORCES RATHER THAN
TRUSTS A CALLER TO REMEMBER:

  1. ASK TOGETHER. Jev ingests the state once and evaluates every question
     against it in parallel. The published parallel-questions measurement put
     one batched request at 12.2x cheaper and 10.0x faster than the same
     questions sent separately, with no change in the answers. So `ask` takes a
     MAP of questions, never one, and there is deliberately no single-question
     helper to reach for by accident.

  2. KEEP ARITHMETIC IN CODE. jev-1.13 reads dates as text rather than as
     ordered quantities, and does not count reliably. Asking it which of two
     dates is earlier, how far apart they are, or how many items match is
     documented as unreliable. Extraction is a judgment and belongs here;
     comparison, duration, and totals are not and belong in the caller.

  3. TYPED IS NOT TRUE. The response schema guarantees the SHAPE of an answer,
     never its correctness — "cannot hallucinate" means it cannot emit an
     illegal type, and it can still pick the wrong option. Every caller needs a
     threshold and a fallback, which is why `decide` exists beside `ask` and
     returns an explicit escalate outcome rather than a bare value.

  4. FILTER FIRST. Accuracy falls as the state grows with content unrelated to
     the decision, and there is a hard ceiling besides. Retrieve and narrow in
     code, then send only the fields the question needs.

TRANSPORT. The authenticated CARR Worker holds the vendor credential and owns
cache, retries and spend admission. This client never reads a vendor key and
refuses paid requests until the Worker advertises its spend authority.

AUTHORITY. Joe ruled on 2026-09-17 that CARR records, client and deal material
included, may be sent to third-party model APIs and admitted this vendor on that
basis. Network reachability is separate and already granted: both hosts sit in
KNOWN_HOSTS in hooks/guard-unattended.py.
"""

import hashlib
import glob
import importlib.util
import json
import math
import os
import re
import sqlite3
import shutil
import uuid
import subprocess
import sys
from contextlib import closing, contextmanager
from contextvars import ContextVar
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from functools import partial

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Entry points may import with only ops/ on sys.path.
if REPO not in sys.path:
    sys.path.insert(0, REPO)
_judge_spec = importlib.util.spec_from_file_location(
    "carr_judge_interface", os.path.join(REPO, "tools", "judge", "interface.py"))
assert _judge_spec and _judge_spec.loader
JUDGE = importlib.util.module_from_spec(_judge_spec)
_judge_spec.loader.exec_module(JUDGE)


def _canonical_repo_root(fallback):
    """The ONE repo root every worktree of this repository shares, so a call
    made from any worktree's copy of this file and a hook reading from the
    canonical checkout resolve to the SAME physical out/jev-calls.jsonl.

    Round-2 fix (2026-09-24): `ask()` used to write next to whichever copy of
    this file was executing (`REPO`, worktree-relative) while
    hooks/completion-evidence-gate.py read from the canonical checkout's
    out/ — two different physical files, so a real call from a worktree was
    invisible to the gate. `git rev-parse --path-format=absolute
    --git-common-dir` resolves to the shared `.git` directory across every
    worktree of one repository (verified: an absolute path, git 2.54.0);
    its parent is the canonical repo root regardless of which worktree is
    running. Falls back to `fallback` (this file's own on-disk REPO) on any
    failure — never raises, matching this module's fail-open posture.
    """
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=fallback, capture_output=True, text=True, timeout=5, check=True,
        ).stdout.strip()
        if out:
            return os.path.dirname(out)
    except Exception:
        pass
    return fallback


CANONICAL_REPO = _canonical_repo_root(REPO)
# WHERE A CALL BECOMES OBSERVABLE (decision 0b11c89b, 2026-09-24). A missing
# Jev call used to be checkable only by grepping a session's Bash history for
# the string "typesafe_client", which a bare `echo typesafe_client` also
# satisfied. Every successful ask() now appends one best-effort row here —
# never on failure, never the request or the answers, just enough for a
# reader (lib/jev_required_actions.py) to bind a call to a session, a time
# window, and the facets it named. Failed attempts and cache hits carry ok=false
# and cannot count as evidence that the vendor answered. Writing this must never turn a working
# Jev call into a failure, so every step here is wrapped and swallowed.
# Uses CANONICAL_REPO (not the possibly-worktree-local REPO) so every
# worktree's ask() and the canonical checkout's Stop-hook reader agree on one
# physical file — see _canonical_repo_root above.
JEV_CALLS_LOG = os.path.join(CANONICAL_REPO, "out", "jev-calls.jsonl")
# Receipt destinations may vary for evals; the spend budget never does.
JEV_DAILY_CAP_LOG = JEV_CALLS_LOG
with open(os.path.join(REPO, "ops", "config", "jev-cost-guard.v1.json"), encoding="utf-8") as _config_file:
    JEV_COST_CONFIG = json.load(_config_file)
JUDGE_CACHE_TTL_SECONDS = JEV_COST_CONFIG["judge_cache_ttl_seconds"]
# The env vars a caller's own session id is found under, same set
# ops/settlement-run-token.py's NATIVE_SESSION_KEYS already uses.
SESSION_ID_ENV_KEYS = ("CODEX_THREAD_ID", "CLAUDE_CODE_SESSION_ID",
                       "CLAUDE_CODE_HOST_SESSION_ID")

# jev-latest is an alias and MOVES when a release ships, so answers can change
# with no change on our side. A caller that has tuned thresholds against a
# specific version should pass that version's exact id instead and step forward
# deliberately. The response reports which version actually answered; `ask`
# returns it untouched under "model" so a receipt can record it.
DEFAULT_MODEL = "jev-latest"

# The documented ceiling is 64k tokens per request for state plus every
# question, and 32k for state plus the single longest question. This is a
# CHARACTER guard against the tighter of those, at a deliberately pessimistic
# 3 characters per token, and it is an ESTIMATE rather than a token count: it
# exists so an oversized state fails here, named and local, instead of as an
# opaque 422 from the service. Narrow the state in code rather than raising it.
STATE_BUDGET_CHARS = 32_000 * 3

TIMEOUT_SECONDS = 60.0


class TypeSafeError(RuntimeError):
    """Any failure reaching or being understood by the service.

    `code` names a local or policy failure; None means a vendor or account
    outage (or an untyped failure), which callers may collapse as one."""

    def __init__(self, message, *, code=None):
        super().__init__(message)
        self.code = code


class JevCallRefused(TypeSafeError):
    """A paid call this client declined before any transport ran.

    `code` is one of REFUSAL_CODES. Worker refusals may carry the authoritative
    reset time; local notice storage is only an observation of that refusal.
    """

    def __init__(self, message, *, code, site=None, scope=None, resets_at=None):
        super().__init__(message, code=code)
        self.site = site
        self.scope = scope
        self.resets_at = resets_at


# THE CALL-SITE REGISTRY (2026-10-04 system-wide audit). Four point-fixes in
# three days each caught one burner after the money was spent, because any
# code path could reach the vendor and the only bound was one shared daily
# counter. Now a paid call needs a registered site, the attribution that site
# declares, and room in the site's own hourly and daily budget, beneath a
# global hourly/daily cap. Local readers observe policy; the Worker alone
# counts and reserves paid attempts, failing closed before vendor fetch.
JEV_CALL_SITES_PATH = os.path.join(REPO, "ops", "config", "jev-call-sites.v1.json")
BUDGET_REFUSALS = ("hourly_paid_call_cap", "site_hourly_budget", "site_daily_budget")
POLICY_REFUSALS = ("fixture_offline", "unregistered_caller", "unattributed_call", "unattended_worker_off",
                   "call_site_registry_invalid")
# Declined because of what the vendor already said: its account is out of
# credit (HTTP 402), or a Worker attempt may already have been billed.
VENDOR_REFUSALS = ("vendor_credit_exhausted",)
REFUSAL_CODES = ("daily_paid_call_cap",) + BUDGET_REFUSALS + POLICY_REFUSALS + VENDOR_REFUSALS
# Worker failures that are the vendor's or the account's, not this machine's.
VENDOR_SIDE_FAILURES = ("vendor_failed_at_worker", "worker_key_unbound")
ATTRIBUTIONS = ("session", "session_or_job")
UNATTENDED_POLICIES = ("off", "allowed")
_SITE_FIELDS = {"caller", "trigger", "runs_in", "attribution", "unattended",
                "hourly_budget", "daily_budget", "owner", "value", "sources"}


def load_call_sites(path=None):
    """The validated registry: {"hourly_paid_call_cap": int, "sites": {caller: entry}}.

    Raises TypeSafeError on any malformed entry: an unreadable registry admits
    nothing, which is the fail-closed direction.
    """
    try:
        with open(path or JEV_CALL_SITES_PATH, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError) as exc:
        raise TypeSafeError(f"Jev call-site registry unreadable ({type(exc).__name__})") from None
    if not isinstance(raw, dict) or raw.get("schema") != "carr-jev-call-sites/v1":
        raise TypeSafeError("Jev call-site registry has the wrong schema")
    cap = raw.get("hourly_paid_call_cap")
    if type(cap) is not int or cap < 0:
        raise TypeSafeError("Jev call-site registry needs an integer hourly_paid_call_cap")
    sites = {}
    for entry in raw.get("sites") or []:
        if not isinstance(entry, dict) or set(entry) != _SITE_FIELDS:
            raise TypeSafeError(f"Jev call-site entry has the wrong fields: {entry!r:.120}")
        caller = entry["caller"]
        if not isinstance(caller, str) or not re.fullmatch(r"[a-z0-9_.:-]+\*?", caller):
            raise TypeSafeError(f"Jev call-site caller is not a plain name: {caller!r}")
        if caller in sites:
            raise TypeSafeError(f"Jev call-site caller registered twice: {caller}")
        if entry["attribution"] not in ATTRIBUTIONS or entry["unattended"] not in UNATTENDED_POLICIES:
            raise TypeSafeError(f"Jev call-site {caller} has an unknown attribution or unattended policy")
        for budget in ("hourly_budget", "daily_budget"):
            if type(entry[budget]) is not int or entry[budget] < 0:
                raise TypeSafeError(f"Jev call-site {caller} needs an integer {budget}")
        if entry["hourly_budget"] > entry["daily_budget"]:
            raise TypeSafeError(f"Jev call-site {caller} hourly budget exceeds its daily budget")
        for text in ("trigger", "runs_in", "owner", "value"):
            if not isinstance(entry[text], str) or not entry[text].strip():
                raise TypeSafeError(f"Jev call-site {caller} needs a {text}")
        if not isinstance(entry["sources"], list) or not entry["sources"] or not all(
                isinstance(s, str) and s for s in entry["sources"]):
            raise TypeSafeError(f"Jev call-site {caller} needs its source files")
        sites[caller] = entry
    return {"hourly_paid_call_cap": cap, "sites": sites}


def call_site(caller, registry):
    """The registry entry for `caller`: an exact name, else a `prefix:*` entry."""
    sites = registry["sites"]
    if caller in sites:
        return sites[caller]
    for name, entry in sites.items():
        if name.endswith("*") and isinstance(caller, str) and caller.startswith(name[:-1]) \
                and len(caller) > len(name) - 1:
            return entry
    return None


def _job_label():
    """A job label, including the existing Quill recording daemon's launch path."""
    label = (os.environ.get("CARR_JEV_JOB") or "").strip()
    if label:
        return label
    service = (os.environ.get("XPC_SERVICE_NAME") or "").strip()
    return service if service.startswith("com.carr.") or service == "com.digimata.quill" else None


def _unattended():
    """The orchestrator's explicit marker for an unattended worker's environment."""
    return os.environ.get("CARR_JEV_WORKER", "").strip().lower() == "off"


def _truthy_env(name):
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def _fixture_offline():
    """Selftests and CI fixtures never pay. ops/ci.sh exports CARR_JEV_OFFLINE and
    its gates class exports CARR_HOOK_FIXTURE; a hook a selftest spawns inherits
    both. Measured 2026-10-04: a fixture prompt seen in three or more sessions
    accounted for 20-45% of each day's paid attempts, because a selftest that
    drops TYPESAFE_API_KEY from its environment still reaches the key FILE."""
    return _truthy_env("CARR_JEV_OFFLINE") or _truthy_env("CARR_HOOK_FIXTURE")


def noul(instructions, true=None, false=None):
    """A yes/no question. Returns the probability that the answer is yes.

    There is NO separate confidence on a noul: the probability is the whole
    answer, and 0.5 means the model finds yes and no about equally likely
    rather than meaning medium intensity. Use one noul per label when several
    labels could apply at once.

    Write `true` and `false` so they agree with `instructions`. A noul whose
    true side describes a no reads as contradictory and measurably degrades.
    """
    question = {"type": "noul", "instructions": instructions}
    criteria = {}
    if true is not None:
        criteria["true"] = true
    if false is not None:
        criteria["false"] = false
    if criteria:
        question["criteria"] = criteria
    problem = malformed_request("", {"built": question})
    if problem:
        raise TypeSafeError(problem)
    return question


def choice(instructions, options):
    """Pick one option from a defined set. `options` maps option -> rubric.

    Include an explicit no-match option wherever nothing may fit. The model
    cannot choose a value that was never offered, so for source-value selection
    check that the candidates actually cover the answer before blaming the
    judgment.
    """
    return _built_question("choice", instructions, options)


def score(instructions, levels):
    """Rate against ordered levels, lowest first.

    Each level must describe a concrete situation and stand on its own. The
    returned value is probability-weighted across the levels, so it is useful
    for comparing against a threshold and NOT for reconstructing an exact
    number by interpolating between levels — the docs call that out directly.
    """
    return _built_question("score", instructions, list(levels))


# THE FULL DISTRIBUTION, NOT JUST THE PICK (Joe's ruling: code decides, Jev
# only judges, and thresholds are calibrated per action). A pick and a
# confidence cannot be calibrated after the fact; the distribution it came from
# can. These readers turn one typed answer into the distribution over every
# option, its entropy in bits, and the top option, identically to the Worker's
# answerDistribution() in mcp-server/src/jev-call-receipt.js — both suites run
# ops/fixtures/jev-calibration/distribution-vectors.v1.json. Nothing here is a
# threshold: entropy is recorded so a threshold can later be MEASURED per
# question family (ops/jev_calibration.py), never assumed.
#
# The tolerance is numeric sanity for rounded vendor probabilities (three
# options at two decimals can miss one by 0.015), not a decision boundary.
PROBABILITY_SUM_TOLERANCE = 0.02
MOVING_MODEL_ALIAS = re.compile(r"(?:^|-)latest$")


def model_is_pinned(model):
    """True for an exact model version; false for a moving alias or nothing."""
    return isinstance(model, str) and bool(model.strip()) and not MOVING_MODEL_ALIAS.search(model.strip())


def state_sha256(state):
    """sha256 of the canonical JSON of the state; equals the Worker's state_sha256."""
    return _prompt_sha256(state)


def entropy_bits(probabilities):
    """Shannon entropy in bits of an already-normalized probability list."""
    return -sum(p * math.log2(p) for p in probabilities if p > 0) + 0.0


def _probability(value):
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and math.isfinite(value) and 0 <= value <= 1)


def answer_distribution(question, answer):
    """One answer as {type, distribution, distribution_complete,
    probability_sum, entropy_bits, top, top_probability}.

    `question` may be None (a log row that kept the answers but not the
    questions); options the answer names are then the whole option set.
    distribution is None when the vendor returned no probabilities; entropy is
    None unless every probability is valid and they sum to one within
    PROBABILITY_SUM_TOLERANCE. Missing offered options count as zero.
    """
    question = question if isinstance(question, dict) else {}
    answer = answer if isinstance(answer, dict) else {}
    kind = question.get("type") or answer.get("type")
    distribution = None
    top = None
    if kind == "noul":
        p = answer.get("noul")
        if _probability(p):
            distribution = {"true": p, "false": 1 - p}
    elif kind in ("choice", "score"):
        raw = answer.get("probabilities")
        criteria = question.get("criteria")
        if kind == "choice":
            offered = (list(criteria) if isinstance(criteria, dict) else
                       list(question.get("choices") or []))
            top = answer.get("choice") if isinstance(answer.get("choice"), str) else None
            if isinstance(raw, dict):
                keys = offered + [k for k in raw if k not in offered]
                distribution = {k: raw.get(k, 0) for k in keys}
        else:
            levels = criteria if isinstance(criteria, list) else []
            if isinstance(raw, dict):
                count = max([len(levels)] + [int(k) + 1 for k in raw
                                             if isinstance(k, str) and k.isascii() and k.isdigit()])
                distribution = {}
                for index in range(count):
                    level = levels[index] if index < len(levels) else None
                    value = raw.get(level) if level is not None and level in raw else raw.get(str(index), 0)
                    distribution[str(index)] = value
    complete = False
    total = None
    entropy = None
    top_probability = None
    if distribution is not None:
        values = list(distribution.values())
        if values and all(_probability(v) for v in values):
            total = sum(values)
            complete = abs(total - 1) <= PROBABILITY_SUM_TOLERANCE
            if complete and total > 0:
                entropy = entropy_bits([v / total for v in values])
            best = max(values)
            top = next(k for k, v in distribution.items() if v == best)
            top_probability = best
    return {"type": kind, "distribution": distribution, "distribution_complete": complete,
            "probability_sum": total, "entropy_bits": entropy, "top": top,
            "top_probability": top_probability}


def calibration_block(state, questions, result, model_requested):
    """What a later calibration needs about one call, attached to ask()'s result."""
    result = result if isinstance(result, dict) else {}
    answers = result.get("answers") if isinstance(result.get("answers"), dict) else {}
    questions = questions if isinstance(questions, dict) else {}
    return {
        "schema": "carr.jev-calibration.v1",
        "model_requested": model_requested,
        "model_answered": result.get("model"),
        "model_pinned": model_is_pinned(model_requested),
        "state_sha256": state_sha256(state),
        "questions": {key: answer_distribution(questions.get(key), answers.get(key))
                      for key in sorted(answers)},
    }


def _safe_calibration_block(state, questions, result, model_requested):
    """Recording must never turn a usable judgment into a failed call."""
    try:
        return calibration_block(state, questions, result, model_requested)
    except Exception:
        return None


def _session_id():
    for key in SESSION_ID_ENV_KEYS:
        value = os.environ.get(key)
        if value and value.strip():
            return value.strip()
    return None


def _dispatch_binding(transcript_path=None, session=None):
    """Snapshot the human owner before sending, never when the answer arrives.

    Hook callers can pass their transcript path; shell callers discover the
    exact native session file. An absent, unreadable or ambiguous transcript
    leaves the receipt unbound and therefore unable to pay required actions.
    Native Codex identity takes precedence over inherited Claude environment.
    """
    session = session or _session_id()
    try:
        from lib.jev_required_actions import human_turn_scope
        from lib.transcript_read import load_transcript

        if not session:
            return session, None
        path = transcript_path or os.environ.get("CARR_JEV_TRANSCRIPT_PATH")
        if not path:
            # Session IDs are path components, never glob patterns or paths.
            if not re.fullmatch(r"[A-Za-z0-9_-]+", session):
                return session, None
            if os.environ.get("CODEX_THREAD_ID") == session:
                home = os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex")
                paths = glob.glob(os.path.join(home, "sessions", "**",
                                               f"rollout-*-{session}.jsonl"), recursive=True)
            else:
                paths = glob.glob(os.path.expanduser(f"~/.claude/projects/*/{session}.jsonl"))
            if len(paths) != 1:
                return session, None
            path = paths[0]
        records = load_transcript(path, hook="jev-dispatch", session=session)
        for record in records:
            if record.get("sessionId") and record["sessionId"] != session:
                return session, None
            if record.get("type") == "session_meta":
                if (record.get("payload") or {}).get("id") != session:
                    return session, None
        return session, human_turn_scope(records, session).identity
    except Exception:
        return session, None


# System-work calls use the Worker's append-only call log. The authenticated
# local-verb transport holds the MCP bearer; this client never reads it.
# Receipts are diagnostic evidence, not per-turn obligations: PR 1407 retired
# prompt-facet enforcement in favor of judgment-boundary checks.
# Every paid call goes to the Worker; failures never fall back to a vendor key.
SERVER_VERB = "ask-jev"
# Kept as a caller-context marker; hooks use the same Worker authority.
IN_HOOK_ENV = "CARR_JEV_IN_HOOK"

def _local_verb_script():
    """mcp-server/local-verb.mjs, preferring this checkout's own copy and
    falling back to the canonical checkout's (every worktree shares one
    Worker, so either reaches the same verb)."""
    for root in (REPO, CANONICAL_REPO):
        candidate = os.path.join(root, "mcp-server", "local-verb.mjs")
        if os.path.isfile(candidate):
            return candidate
    return None


def _node_binary():
    for candidate in (shutil.which("node"), "/opt/homebrew/bin/node", "/usr/local/bin/node"):
        if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def _server_error_category(stderr):
    """A fixed category for a failed verb call. Never the raw text: local-verb
    stderr is the Worker's own error JSON, which carries no credential, but a
    category is all a receipt or a gate needs."""
    text = stderr or ""
    for code in REFUSAL_CODES + ("jev_spend_authority_unavailable", "jev_receipt_store_unavailable"):
        if f'"{code}"' in text:
            return code
    for marker, category in (
            ('"jev_cache_miss"', "cache_miss"),
            ('"unknown_tool"', "verb_not_deployed"),
            ('"jev_proxy_unconfigured"', "worker_key_unbound"),
            ('"jev_upstream_failed"', "vendor_failed_at_worker"),
            ("could not reach the deployed Worker", "worker_unreachable"),
            ("no MCP token", "local_token_missing"),
            ("refusing MCP token file", "local_token_insecure")):
        if marker in text:
            return category
    return "server_call_failed"


def _worker_upstream(stderr):
    """(vendor HTTP status, reason) from local-verb's jev_upstream_failed
    refusal, else (None, None). Only an integer status and a short snake_case
    reason are kept; the vendor body the Worker quotes is never read."""
    text = stderr or ""
    start = text.find("TOOL ERROR ")
    if start < 0:
        return None, None
    try:
        payload, _ = json.JSONDecoder().raw_decode(text, start + len("TOOL ERROR "))
    except ValueError:
        return None, None
    if not isinstance(payload, dict) or payload.get("error") != "jev_upstream_failed":
        return None, None
    status, reason = payload.get("status"), payload.get("reason")
    return (status if type(status) is int and 100 <= status <= 599 else None,
            reason if isinstance(reason, str) and re.fullmatch(r"[a-z_]{1,40}", reason) else None)


SPEND_AUTHORITY = "carr-jev-spend/v1"
# Registered with zero budgets: the Worker admits it, and it can never pay.
PROBE_CALLER = "jev_spend_authority_probe"


def capability_probe():
    session_id = "spend-authority-probe"
    return {"idempotency_key": str(uuid.uuid4()), "session_id": session_id,
            "purpose": "call", "transport_mode": "cache_only",
            "state": {"input": "spend authority probe", "jev_attribution": {
                "caller": PROBE_CALLER, "session_id": session_id,
                "job_id": None, "unattended": False}},
            "questions": {"probe": noul("Is this a probe?")}}


def _worker_capability(run, node, script, timeout):
    proc = run([node or "node", script or "local-verb.mjs", SERVER_VERB, json.dumps(capability_probe())],
               capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    if proc.returncode == 0:
        payload = json.loads(proc.stdout)
        return payload.get("ok") is True and payload.get("spend_authority") == SPEND_AUTHORITY
    start = (proc.stderr or "").find("TOOL ERROR ")
    if start < 0:
        return False
    payload, _ = json.JSONDecoder().raw_decode(proc.stderr, start + len("TOOL ERROR "))
    return payload.get("error") == "jev_cache_miss" and payload.get("spend_authority") == SPEND_AUTHORITY


def worker_ready():
    """No paid call or vendor credential: verify the authenticated Worker contract."""
    script, node = _local_verb_script(), _node_binary()
    if not script or not node or _fixture_offline():
        return False
    try:
        return _worker_capability(subprocess.run, node, script, 5)
    except Exception:
        return False


def server_ask(state, questions, *, model, facets, purpose, session_id, timeout,
               transport_mode, runner=None, upstream=None, caller=None, job_id=None, unattended=False):
    """Ask the Worker's ask-jev verb. Returns (result, None) on success, where
    result is {"model", "answers", "usage", "server_receipt": {...}}, or
    (None, <category>) on any failure. Never raises. When the Worker's own
    vendor call failed, a passed `upstream` dict gets its status and reason."""
    script = _local_verb_script()
    node = _node_binary()
    if runner is None and (script is None or node is None):
        return None, "node_or_local_verb_missing"
    args = {
        "idempotency_key": str(uuid.uuid4()),
        "session_id": session_id,
        "purpose": purpose,
        "state": {"input": state, "jev_attribution": {"caller": caller,
                  "session_id": session_id if session_id != "unbound" else None,
                  "job_id": job_id, "unattended": unattended}},
        "questions": questions,
        "facets": sorted({str(f) for f in facets}) if facets else [],
        "model": model,
    }
    if transport_mode is not None:
        args["transport_mode"] = transport_mode
    try:
        run = runner or subprocess.run
        started = time.monotonic()
        if not _worker_capability(run, node, script, float(timeout)):
            return None, "jev_spend_authority_unavailable"
        remaining = float(timeout) - (time.monotonic() - started)
        if remaining <= 0:
            return None, "server_timeout"
        proc = run([node or "node", script or "local-verb.mjs", SERVER_VERB,
                    json.dumps(args, ensure_ascii=False)],
                   capture_output=True, text=True,
                   timeout=remaining,
                   stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return None, "server_timeout"
    except Exception:
        return None, "server_call_failed"
    if proc.returncode != 0:
        category = _server_error_category(proc.stderr)
        if upstream is not None:
            start = (proc.stderr or '').find('TOOL ERROR ')
            try:
                error_payload, _ = json.JSONDecoder().raw_decode(proc.stderr, start + len('TOOL ERROR '))
                if isinstance(error_payload, dict) and "paid_attempts" in error_payload:
                    upstream["paid_attempts"] = error_payload["paid_attempts"]
                reset = error_payload.get('resets_at') if start >= 0 and isinstance(error_payload, dict) else None
                if isinstance(reset, str):
                    datetime.fromisoformat(reset.replace('Z', '+00:00'))
                    upstream['resets_at'] = reset
            except (ValueError, TypeError):
                pass
        if category == "vendor_failed_at_worker" and upstream is not None:
            upstream["status"], upstream["reason"] = _worker_upstream(proc.stderr)
        return None, category
    try:
        out = json.loads(proc.stdout)
    except ValueError:
        return None, "server_response_unparseable"
    if (not isinstance(out, dict) or out.get("ok") is not True
            or not isinstance(out.get("answers"), dict)
            or not isinstance(out.get("receipt_id"), str)):
        return None, "server_response_malformed"
    return {
        "model": out.get("model"),
        "answers": out["answers"],
        "usage": out.get("usage") if isinstance(out.get("usage"), dict) else None,
        "cache_hit": out.get("cache_hit") is True,
        **({"paid_attempts": out["paid_attempts"]} if "paid_attempts" in out else {}),
        "server_receipt": {k: out.get(k) for k in (
            "receipt_id", "recorded_at", "purpose", "session_id",
            "state_sha256", "prompt_sha256")},
    }, None


# Both transports consume the same maintained request policy. Adapter code
# only maps JSON shapes to each language's object/array predicates.
REQUEST_CONTRACT = json.loads((Path(__file__).resolve().parent.parent /
    "mcp-server/src/jev-request-contract.v1.json").read_text())


def malformed_request(state, questions):
    """A fixed diagnostic with a positional reference; never caller data."""
    if not isinstance(state, (str, dict)):
        return "state_invalid"
    if not isinstance(questions, dict) or not (
            REQUEST_CONTRACT["min_questions"] <= len(questions) <= REQUEST_CONTRACT["max_questions"]):
        return "question_count_invalid"
    for index, (key, question) in enumerate(questions.items()):
        ref = f"question[{index}]"
        if not isinstance(key, str) or not key or not isinstance(question, dict):
            return f"{ref}: question_invalid"
        kind = question.get("type")
        rule = REQUEST_CONTRACT["question_types"].get(kind) if isinstance(kind, str) else None
        if rule is None:
            return f"{ref}: type_invalid"
        instructions = question.get("instructions")
        if not isinstance(instructions, str) or not instructions.strip():
            return f"{ref}: instructions_invalid"
        criteria = question.get("criteria")
        if criteria is None and rule["optional"]:
            continue
        shape = dict if rule["criteria"] == "object" else list
        if not isinstance(criteria, shape) or len(criteria) < rule["min_items"]:
            return f"{ref}: criteria_invalid"
    return None


def _built_question(kind, instructions, criteria):
    question = {"type": kind, "instructions": instructions, "criteria": criteria}
    problem = malformed_request("", {"built": question})
    if problem:
        raise TypeSafeError(problem)
    question["criteria"] = criteria.copy()
    return question


def usable_judgment(result, questions):
    """Require one typed answer per requested question and measured usage."""
    if not isinstance(result, dict) or not isinstance(result.get("model"), str) or not result["model"].strip():
        return False
    usage = result.get("usage")
    if not isinstance(usage, dict) or any(
            type(usage.get(key)) is not int or usage[key] < 0
            for key in ("input_tokens", "output_tokens")):
        return False
    answers = result.get("answers")
    if not isinstance(answers, dict) or set(answers) != set(questions):
        return False
    for key, question in questions.items():
        answer = answers.get(key)
        if not isinstance(question, dict) or not isinstance(answer, dict):
            return False
        kind = question.get("type")
        if answer.get("type") != kind:
            return False
        if kind == "noul":
            value = answer.get("noul")
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
                return False
        elif kind == "choice":
            if answer.get("choice") not in question.get("criteria", {}):
                return False
        elif kind == "score":
            value = answer.get("score")
            levels = question.get("criteria")
            if (isinstance(value, bool) or not isinstance(value, (int, float)) or
                    not isinstance(levels, list) or not 0 <= value <= len(levels) - 1):
                return False
        else:
            return False
        if kind in ("choice", "score"):
            confidence = answer.get("confidence")
            if (isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or
                    not 0 <= confidence <= 1):
                return False
    return True


def _caller_name():
    """The immediate caller of ask(), with no source text or stack arguments."""
    try:
        return os.path.splitext(os.path.basename(sys._getframe(2).f_code.co_filename))[0]
    except (AttributeError, ValueError):
        return "unknown"


def _prompt_sha256(payload):
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _question_kind(questions):
    kinds = {q.get("type") for q in questions.values() if isinstance(q, dict)
             and q.get("type") in ("noul", "choice", "score")}
    return ",".join(sorted(kinds)) or "unknown"


# Keep these names aligned with lib/jev_required_actions.py's FACET_NAMES.
# The receipt reader previously inferred them from raw question IDs; infer
# them before logging so no caller-chosen ID text needs to reach the ledger.
RECEIPT_FACETS = (
    "architecture_or_design", "semantic_creation", "diagnosis",
    "verification_selection", "evidence_matching", "next_action_priority",
)


def _receipt_facets(questions, facets):
    names = {str(f) for f in facets} if facets else set()
    for facet in RECEIPT_FACETS:
        pattern = re.compile(r"[_\s-]+".join(map(re.escape, facet.split("_"))), re.I)
        if any(isinstance(qid, str) and pattern.search(qid) for qid in questions):
            names.add(facet)
    return sorted(names)


def _append_call_receipt(questions, facets, result, log_path, *, caller=None,
                         question_kind=None, prompt_sha256=None, ok=True,
                         cache_hit=False, error=None, calibration=None,
                         server_error=None, session=None, dispatch_binding=None):
    """Best-effort, APPEND-ONLY JSONL row, never storing the request or the
    answers, never able to turn a successful ask() into a failure. See
    JEV_CALLS_LOG above.

    The row carries the response's "model" and "usage" token counts. It
    deliberately carries NO response id (round 3, 2026-09-24): the vendor
    returns none (null in all 63 real receipts at the time of writing) and
    exposes no usage/audit endpoint, so an id field could never be reconciled
    and was a claim this file cannot back. Forgery of a row is DETECTED, not
    prevented (decision d47931da): hooks/bash-write-gate.py warns on any
    shell write naming this file, and hooks/completion-evidence-gate.py
    records a detection event for any turn whose tool calls name it — this
    function, reached only through ask(), is the one legitimate writer.

    `calibration` is ask()'s calibration_block(). The row keeps only its
    numbers — entropy and completeness per question, aligned with
    question_ids_sha256 — plus the state digest and the requested model. The
    distributions themselves name options, which are caller text, so they stay
    in the returned result and in jev_judge.record()'s log, never here.
    """
    try:
        answered = result if isinstance(result, dict) else {}
        usage = answered.get("usage") if isinstance(answered.get("usage"), dict) else None
        usable = (answered.get("usable") is True if "usable" in answered
                  else bool(ok and usage))
        row = {
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "session": dispatch_binding[0] if dispatch_binding else session or _session_id(),
            "human_turn_id": dispatch_binding[1] if dispatch_binding else None,
            "job": _job_label(),
            "question_ids_sha256": [hashlib.sha256(qid.encode("utf-8")).hexdigest()
                                    for qid in sorted(questions)],
            "caller": caller,
            "question_kind": question_kind,
            "prompt_sha256": prompt_sha256,
            "facets": _receipt_facets(questions, facets),
            "model": answered.get("model"),
            "usage": usage,
            "input_tokens": usage.get("input_tokens") if usage else None,
            "output_tokens": usage.get("output_tokens") if usage else None,
            "http_status": answered.get("http_status"),
            # None when no answer came back to validate (a refusal, a 402, a
            # network fault): those are transport failures, not bad answers.
            "schema_valid": (answered["schema_valid"] if type(answered.get("schema_valid")) is bool
                             else None),
            "upstream_reason": answered.get("upstream_reason"),
            "usable": usable,
            "ok": bool(ok and usable and not cache_hit),
            "cache_hit": cache_hit,
            "server_receipt_id": ((answered.get("server_receipt") or {}).get("receipt_id")
                                  if not cache_hit else None),
            "server_error": server_error,
        }
        per_question = (calibration or {}).get("questions") if isinstance(calibration, dict) else None
        ordered = [per_question.get(qid) or {} for qid in sorted(questions)] if isinstance(per_question, dict) else None
        row.update({
            "state_sha256": calibration.get("state_sha256") if per_question is not None else None,
            "model_requested": calibration.get("model_requested") if per_question is not None else None,
            "model_pinned": calibration.get("model_pinned") if per_question is not None else None,
            "entropy_bits": [q.get("entropy_bits") for q in ordered] if ordered is not None else None,
            "distribution_complete": ([q.get("distribution_complete") is True for q in ordered]
                                      if ordered is not None else None),
        })
        if error:
            row["error"] = error
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
    except Exception:
        pass


def _logged_attempt_rows(log_path, day):
    """Seed a new UTC day from pre-cap receipts, excluding free cache hits.

    Count attempts conservatively: failures may still have consumed credits.
    A missing log means no prior calls; unreadable evidence makes Jev unavailable.
    """
    try:
        with open(log_path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except ValueError:
                    raise TypeSafeError("Jev unavailable: daily cap accounting has malformed seed evidence") from None
                if not isinstance(row, dict) or not isinstance(row.get("ts"), str):
                    raise TypeSafeError("Jev unavailable: daily cap accounting has invalid seed evidence")
                try:
                    stamp = datetime.fromisoformat(row["ts"].replace("Z", "+00:00"))
                except ValueError:
                    raise TypeSafeError("Jev unavailable: daily cap accounting has invalid seed timestamp") from None
                if stamp.tzinfo is None or ("cache_hit" in row and type(row["cache_hit"]) is not bool):
                    raise TypeSafeError("Jev unavailable: daily cap accounting has invalid seed fields")
                if (stamp.astimezone(timezone.utc).strftime("%Y-%m-%d") == day
                        and not row.get("cache_hit")
                        and row.get("error") not in REFUSAL_CODES):
                    yield row
    except FileNotFoundError:
        pass
    except UnicodeError:
        raise TypeSafeError("Jev unavailable: daily cap accounting has invalid seed encoding") from None


def _logged_attempts(log_path, day):
    return sum(1 for _ in _logged_attempt_rows(log_path, day))


def _daily_cap_limit():
    cap = JEV_COST_CONFIG.get("daily_paid_call_cap")
    if type(cap) is not int or cap < 0:
        raise TypeSafeError("Jev unavailable: invalid daily paid call cap")
    return cap


PAID_CAP_ACTION = ("on breach: Worker refuses new paid attempts · owner orchestrator · "
                   "remediation reduce caller demand or restore vendor billing · "
                   "verify Worker receipts and daily cost alarm · auto-clear at UTC rollover")


def paid_cap_health(*, now=None):
    """Local observation only; the Worker ledger is the universal authority."""
    day = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y-%m-%d")
    try:
        cap = _daily_cap_limit()
        used = _logged_attempts(JEV_DAILY_CAP_LOG, day)
        return (f"UNKNOWN jev paid cap — {used} locally observed attempts; Worker enforces "
                f"{cap}/UTC day globally (hard 1000); local logs omit other clients · {PAID_CAP_ACTION}")
    except (OSError, TypeSafeError) as exc:
        return f"UNKNOWN jev paid cap — {type(exc).__name__} · {PAID_CAP_ACTION}"


def _cap_db_path():
    return os.fspath(JEV_DAILY_CAP_LOG) + ".daily-cap.sqlite3"


def _windows(now):
    """(day, hour key, next hour ISO, next UTC midnight ISO) for one instant."""
    now = now.astimezone(timezone.utc)
    hour = now.replace(minute=0, second=0, microsecond=0)
    midnight = hour.replace(hour=0)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    return (now.strftime("%Y-%m-%d"), hour.strftime("%Y-%m-%dT%H"),
            (hour + timedelta(hours=1)).strftime(fmt), (midnight + timedelta(days=1)).strftime(fmt))


_BUDGET_TABLES = (
    # Active budget pauses, read by hooks to say so once instead of per call.
    "CREATE TABLE IF NOT EXISTS budget_pause (scope TEXT, site TEXT, resets_at TEXT, "
    "PRIMARY KEY(scope,site,resets_at))",
    "CREATE TABLE IF NOT EXISTS pause_notice (session TEXT, scope TEXT, resets_at TEXT, "
    "PRIMARY KEY(session,scope,resets_at))",
)


def _budget_tables(db):
    for statement in _BUDGET_TABLES:
        db.execute(statement)


def _observe_worker_pause(code, caller, resets_at):
    """Mirror an observed server refusal for hook notices, never admission."""
    if code not in BUDGET_REFUSALS + ('daily_paid_call_cap', 'vendor_credit_exhausted') or not resets_at:
        return
    try:
        reset = datetime.fromisoformat(resets_at.replace('Z', '+00:00')).astimezone(timezone.utc)
        stamp = reset.strftime('%Y-%m-%dT%H:%M:%SZ')
        site = '*' if code in ('daily_paid_call_cap', 'hourly_paid_call_cap', 'vendor_credit_exhausted') else caller
        with closing(sqlite3.connect(_cap_db_path(), timeout=1.0)) as db, db:
            _budget_tables(db)
            db.execute('INSERT OR IGNORE INTO budget_pause VALUES (?,?,?)', (code, site, stamp))
    except (OSError, sqlite3.Error, ValueError):
        pass  # A failed notice cannot weaken the authoritative server refusal.


def _site_enabled(entry):
    return not (_unattended() and entry["unattended"] == "off")


def call_site_enabled(caller):
    """Whether this site's registry policy permits dispatch in this environment.

    Admission still owns attribution and accounting. Advisory dispatchers use
    this projection to avoid running checks that the same policy will refuse.
    """
    if not _unattended():
        return True
    try:
        entry = call_site(caller, load_call_sites())
        return entry is not None and _site_enabled(entry)
    except TypeSafeError:
        return False


def _refuse_offline(caller):
    """Fixtures refuse locally; the Worker alone resolves and admits call sites."""
    if _fixture_offline():
        raise JevCallRefused("Jev unavailable: fixture or CI run (CARR_JEV_OFFLINE/CARR_HOOK_FIXTURE)",
                             code="fixture_offline", site=caller)


_reservation_receipt: ContextVar[dict | None] = ContextVar("jev_reservation_receipt", default=None)


@contextmanager
def capture_paid_reservations(*, caller, session_id, run_id):
    """Observe Worker-committed attempts for this execution context."""
    receipt = {"caller": caller, "session_id": session_id, "run_id": run_id, "utc_days": {}, "seen": set(), "complete": True}
    token = _reservation_receipt.set(receipt)
    try:
        yield receipt
    finally:
        _reservation_receipt.reset(token)


def _observe_paid_reservations(attempts, caller, session, *, complete=True):
    receipt = _reservation_receipt.get()
    if receipt is None or receipt["caller"] != caller or receipt["session_id"] != session:
        return
    receipt["complete"] = receipt["complete"] and complete
    if not isinstance(attempts, list):
        receipt["complete"] = False
        return
    for attempt in attempts:
        try:
            key = attempt["receipt_id"]
            recorded = datetime.fromisoformat(attempt["recorded_at"].replace("Z", "+00:00"))
            if not isinstance(key, str) or not key or recorded.tzinfo is None:
                receipt["complete"] = False
                continue
        except (KeyError, TypeError, ValueError, AttributeError):
            receipt["complete"] = False
            continue
        if key not in receipt["seen"]:
            receipt["seen"].add(key)
            day = recorded.astimezone(timezone.utc).date().isoformat()
            receipt["utc_days"][day] = receipt["utc_days"].get(day, 0) + 1


def active_pause(*, sites=None, now=None):
    """The budget pause in force now, as {"scope", "resets_at"}, or None.

    Only mirrored Worker refusals count; retired local counters never pause.
    Global pauses (daily cap, hourly cap, billing) count; a site budget pause
    counts only for the named `sites`. Read-only; storage failures raise
    TypeSafeError so advisory callers stay visible.
    """
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    path = Path(_cap_db_path())
    if not path.exists():
        return None
    try:
        db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)
        try:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='budget_pause'").fetchone():
                return None
            registry = load_call_sites()
            names = ["*"] + [(call_site(name, registry) or {}).get("caller", name)
                             for name in sites or []]
            marks = ",".join("?" * len(names))
            found = db.execute(f"SELECT scope,resets_at FROM budget_pause WHERE resets_at > ? "
                               f"AND site IN ({marks}) ORDER BY resets_at DESC LIMIT 1",
                               (stamp, *names)).fetchone()
        finally:
            db.close()
    except (OSError, sqlite3.Error, TypeSafeError) as exc:
        raise TypeSafeError(f"Jev pause storage unavailable ({type(exc).__name__})") from None
    return {"scope": found[0], "resets_at": found[1]} if found else None


PAUSE_WORDS = {"daily_paid_call_cap": "daily paid-call cap",
               "hourly_paid_call_cap": "hourly paid-call cap",
               "site_daily_budget": "this check's daily budget",
               "site_hourly_budget": "this check's hourly budget",
               "vendor_credit_exhausted": "vendor billing hold"}


def pause_notice(session, *, sites=None, now=None):
    """ONE line per session per pause window, then None until the next window.

    Hooks call this instead of printing "[jev ...] unavailable" on every tool
    call and Stop while a cap holds. A missing session gets no line at all.
    """
    pause = active_pause(sites=sites, now=now)
    if not pause or not session:
        return None
    try:
        with closing(sqlite3.connect(_cap_db_path(), timeout=1.0)) as db, db:
            _budget_tables(db)
            fresh = db.execute("INSERT OR IGNORE INTO pause_notice VALUES (?,?,?)",
                               (str(session), pause["scope"], pause["resets_at"])).rowcount == 1
    except (OSError, sqlite3.Error) as exc:
        raise TypeSafeError(f"Jev notice storage unavailable ({type(exc).__name__})") from None
    if not fresh:
        return None
    resumes = pause["resets_at"].replace("T", " ").replace(":00Z", " UTC")
    return (f"[jev] paused: {PAUSE_WORDS.get(pause['scope'], pause['scope'])} reached; "
            f"resumes {resumes}. Jev checks are skipped until then; this is said once.")


def outage_notice(session, *, now=None):
    """ONE line per session per UTC hour while Jev is unavailable for any
    reason other than a budget pause (vendor down, credits exhausted)."""
    if not session:
        return None
    _, hour, _, _ = _windows(now or datetime.now(timezone.utc))
    try:
        with closing(sqlite3.connect(_cap_db_path(), timeout=1.0)) as db, db:
            _budget_tables(db)
            fresh = db.execute("INSERT OR IGNORE INTO pause_notice VALUES (?,?,?)",
                               (str(session), "outage", hour)).rowcount == 1
    except (OSError, sqlite3.Error) as exc:
        raise TypeSafeError(f"Jev notice storage unavailable ({type(exc).__name__})") from None
    if not fresh:
        return None
    return ("[jev] unavailable this hour (vendor or account outage, not a cap); "
            "Jev checks are skipped and this is said once per hour.")


def spend_by_site_health(*, now=None):
    """Observe local receipts without presenting a second admission counter."""
    day = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y-%m-%d")
    try:
        registry = load_call_sites()
        usage = {}
        for row in _logged_attempt_rows(JEV_DAILY_CAP_LOG, day):
            name = row.get("caller") or "unattributed"
            site = (call_site(name, registry) or {}).get("caller", name)
            usage[site] = usage.get(site, 0) + 1
        parts = " ".join(f"{name}={used}" for name, used in sorted(usage.items())) or "no local spend"
        return f"UNKNOWN jev sites — locally observed {parts}; Worker ledger owns all budgets · {PAID_CAP_ACTION}"
    except (OSError, TypeSafeError) as exc:
        return f"UNKNOWN jev sites — {type(exc).__name__} · {PAID_CAP_ACTION}"


def ask(state, questions, *, model=DEFAULT_MODEL, timeout=TIMEOUT_SECONDS,
        facets=None, calls_log=JEV_CALLS_LOG, deadline=None, caller=None,
        cache_ttl_seconds=JUDGE_CACHE_TTL_SECONDS,
        work_class="system_work", purpose="call", server_runner=None, session_id=None,
        transcript_path=None):
    """Compatibility entrypoint: all existing callers cross the class switch.

    The Worker owns the transport, retry, cache and receipt contract.
    Runtime consumers explicitly pass app_runtime, which cannot use Decisions.
    """
    try:
        return JUDGE.ask(state, questions, jev=partial(_ask_jev, work_class=work_class), work_class=work_class,
                         model=model, timeout=timeout, facets=facets, calls_log=calls_log,
                         deadline=deadline, caller=caller or _caller_name(),
                         cache_ttl_seconds=cache_ttl_seconds,
                         purpose=purpose, server_runner=server_runner, session_id=session_id,
                         transcript_path=transcript_path)
    except JUDGE.JudgeUnavailable as exc:
        raise TypeSafeError(str(exc)) from None


def _ask_jev(state, questions, *, model=DEFAULT_MODEL, timeout=TIMEOUT_SECONDS,
             facets=None, calls_log=JEV_CALLS_LOG, deadline=None, caller=None,
             cache_ttl_seconds=JUDGE_CACHE_TTL_SECONDS,
             purpose="call", server_runner=None, session_id=None, work_class="system_work",
             transcript_path=None):
    """Evaluate `state` against a map of questions in ONE request.

    `state` is a string, or a mapping when the context has several parts —
    prefer named fields, and reference them from an instruction with backticked
    paths such as `ticket.messages[0].text`. `questions` maps a caller-chosen
    id to a question built by noul/choice/score; the ids come back unchanged and
    are never sent to the model, so the instruction must carry its full meaning.

    `facets` labels the judgments for diagnostic and historical receipt readers.
    It never imposes per-turn obligations. `transcript_path` optionally binds
    receipts to the dispatching human; otherwise the native session is discovered.
    Retries and late answers keep that owner. Unknown owners stay unbound.

    Returns a typed judgment and its Worker receipt. server_runner is the
    offline fake Worker seam. deadline bounds the entire transport, including
    the zero-spend capability probe. cache_ttl_seconds=0 selects paid_once.
    The Worker owns cache, retries, admission and billing; failures never fall
    back to a vendor key. Cache hits cannot count as fresh vendor evidence.
    """
    if not isinstance(questions, dict) or not questions:
        raise TypeSafeError("ask needs a non-empty map of questions")
    problem = malformed_request(state, questions)
    if problem:
        raise TypeSafeError(f"malformed Jev request, nothing sent: {problem}")

    payload = {"state": state, "model": model, "questions": questions}
    caller = caller or _caller_name()
    question_kind = _question_kind(questions)
    prompt_sha256 = _prompt_sha256(payload)

    state_chars = len(json.dumps(state))
    if state_chars > STATE_BUDGET_CHARS:
        raise TypeSafeError(
            f"state is roughly {state_chars} characters, over this module's "
            f"{STATE_BUDGET_CHARS} guard. Narrow it in code: accuracy falls as "
            "a state fills with detail unrelated to the decision, so trimming "
            "is the fix rather than raising the guard."
        )

    if not isinstance(cache_ttl_seconds, (int, float)) or not math.isfinite(cache_ttl_seconds) or cache_ttl_seconds < 0:
        raise TypeSafeError("cache_ttl_seconds must be a finite nonnegative number")
    dispatch_binding = _dispatch_binding(transcript_path, session_id)
    _refuse_offline(caller)
    remaining = float(timeout)
    if deadline is not None:
        remaining = min(remaining, deadline - time.monotonic())
    if remaining <= 0:
        raise TypeSafeError("deadline passed before the request could be sent")
    upstream = {}
    served, error = server_ask(
        state, questions, model=model, facets=facets, purpose=purpose,
        session_id=dispatch_binding[0] or "unbound", timeout=remaining,
        transport_mode="paid_once" if cache_ttl_seconds == 0 else None,
        runner=server_runner, upstream=upstream, caller=caller, job_id=_job_label(), unattended=_unattended())
    observed = served or upstream
    complete = ("paid_attempts" in observed or bool(served and served.get("cache_hit"))
                or (served is None and error in REFUSAL_CODES + ("jev_spend_authority_unavailable",)))
    _observe_paid_reservations(observed.get("paid_attempts", []), caller, dispatch_binding[0], complete=complete)
    if served is None:
        _append_call_receipt(questions, facets, {"http_status": upstream.get("status")}, calls_log, dispatch_binding=dispatch_binding,
            caller=caller, question_kind=question_kind, prompt_sha256=prompt_sha256,
            ok=False, error=error, server_error=error, session=session_id)
        if error in REFUSAL_CODES:
            _observe_worker_pause(error, caller, upstream.get('resets_at'))
            raise JevCallRefused(f"Jev unavailable: {error} ({caller})", code=error, site=caller,
                                 resets_at=upstream.get('resets_at'))
        raise TypeSafeError(f"Jev Worker unavailable: {error}; no direct fallback",
                            code=None if error in VENDOR_SIDE_FAILURES else error)
    cache_hit = served.get("cache_hit") is True
    validation = {**served, "usage": {"input_tokens": 0, "output_tokens": 0}} if cache_hit else served
    valid = usable_judgment(validation, questions)
    served["calibration"] = _safe_calibration_block(state, questions, served, model) if valid else None
    _append_call_receipt(questions, facets, {**served, "schema_valid": valid, "usable": valid}, calls_log,
        dispatch_binding=dispatch_binding, caller=caller, question_kind=question_kind,
        prompt_sha256=prompt_sha256, ok=valid, cache_hit=cache_hit,
        calibration=served["calibration"], session=session_id)
    if not valid:
        raise TypeSafeError("TypeSafe returned an unusable judgment")
    return served


def decide(answer, *, yes_at=0.8, no_at=0.2, min_confidence=0.6):
    """Turn one raw answer into an act: accept it, or send it to someone else.

    Returns {"outcome": "yes"|"no"|"value", "escalate": bool, "value": ...,
    "confidence": float|None}. `escalate` true means the caller routes this case
    to a person or to a reasoning model rather than acting on it — the pattern
    every published build used, and the reason a typed answer is safe to
    automate at all.

    THE DEFAULTS ARE PLACEHOLDERS, NOT FINDINGS. Thresholds have to be measured
    on CARR's own data and against the cost of being wrong in that specific
    place, and a threshold that suits gate precision will not suit a client
    document. Treat any number here as a starting point to replace.

    Two cautions the docs make explicitly. A noul near 0.5 means yes and no are
    about equally likely, not that the answer is moderately true. And for a
    choice or score, confidence summarises how concentrated the distribution
    is — it is not a probability that the workflow is correct, and low
    confidence across several equally acceptable options need not invalidate a
    harmless preference.
    """
    kind = answer.get("type")
    if kind == "noul":
        probability = float(answer["noul"])
        if probability >= yes_at:
            return {"outcome": "yes", "escalate": False,
                    "value": probability, "confidence": None}
        if probability <= no_at:
            return {"outcome": "no", "escalate": False,
                    "value": probability, "confidence": None}
        return {"outcome": "no", "escalate": True,
                "value": probability, "confidence": None}
    if kind in ("choice", "score"):
        confidence = answer.get("confidence")
        value = answer.get("choice") if kind == "choice" else answer.get("score")
        return {
            "outcome": "value",
            "escalate": confidence is None or float(confidence) < min_confidence,
            "value": value,
            "confidence": None if confidence is None else float(confidence),
        }
    raise TypeSafeError(f"unknown answer type: {kind!r}")
