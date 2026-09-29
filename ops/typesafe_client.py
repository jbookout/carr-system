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

CREDENTIAL. Read from ~/.config/carr/typesafe.env, mode 600, outside the repo,
recorded by name in secrets-inventory.md. The value is never logged, never
echoed into an exception, and never written to a receipt. On an HTTP error this
module reports the status and a truncated response body, and deliberately does
not report the request it sent, because the request carries the bearer token.

AUTHORITY. Joe ruled on 2026-09-17 that CARR records, client and deal material
included, may be sent to third-party model APIs and admitted this vendor on that
basis. Network reachability is separate and already granted: both hosts sit in
KNOWN_HOSTS in hooks/guard-unattended.py.
"""

import hashlib
import json
import math
import os
import re
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

ENDPOINT = "https://api.typesafe.ai/v1/systemone"
KEY_PATH = os.path.expanduser("~/.config/carr/typesafe.env")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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
JUDGE_CACHE_PATH = os.path.join(CANONICAL_REPO, "out", "jev-judge-cache.sqlite3")
with open(os.path.join(REPO, "ops", "config", "jev-cost-guard.v1.json"), encoding="utf-8") as _config_file:
    JEV_COST_CONFIG = json.load(_config_file)
JUDGE_CACHE_TTL_SECONDS = JEV_COST_CONFIG["judge_cache_ttl_seconds"]
# The env vars a caller's own session id is found under, same set
# ops/settlement-run-token.py's NATIVE_SESSION_KEYS already uses.
SESSION_ID_ENV_KEYS = ("CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_HOST_SESSION_ID",
                       "CODEX_THREAD_ID")
KEY_NAME = "TYPESAFE_API_KEY"

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
RATE_LIMIT_RETRIES = 3


class TypeSafeError(RuntimeError):
    """Any failure reaching or being understood by the service."""


def read_api_key(path=KEY_PATH):
    """The bearer token, from the 600-mode env file. Never logged."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            lines = handle.readlines()
    except OSError as err:
        raise TypeSafeError(
            f"cannot read the TypeSafe credential at {path}: {err.strerror}. "
            "Joe creates it by hand; no agent holds the value."
        ) from None
    for line in lines:
        line = line.strip()
        if line.startswith(f"{KEY_NAME}="):
            value = line.split("=", 1)[1].strip()
            if not value:
                raise TypeSafeError(f"{path}: {KEY_NAME} is present but empty")
            return value
    raise TypeSafeError(f"{path}: no {KEY_NAME} line")


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
    return question


def choice(instructions, options):
    """Pick one option from a defined set. `options` maps option -> rubric.

    Include an explicit no-match option wherever nothing may fit. The model
    cannot choose a value that was never offered, so for source-value selection
    check that the candidates actually cover the answer before blaming the
    judgment.
    """
    if not isinstance(options, dict) or len(options) < 2:
        raise TypeSafeError("a choice needs a mapping of at least two options")
    return {"type": "choice", "instructions": instructions, "criteria": dict(options)}


def score(instructions, levels):
    """Rate against ordered levels, lowest first.

    Each level must describe a concrete situation and stand on its own. The
    returned value is probability-weighted across the levels, so it is useful
    for comparing against a threshold and NOT for reconstructing an exact
    number by interpolating between levels — the docs call that out directly.
    """
    levels = list(levels)
    if len(levels) < 2:
        raise TypeSafeError("a score needs at least two ordered levels")
    return {"type": "score", "instructions": instructions, "criteria": levels}


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


def _cache_key(endpoint, account, credential, model, caller, question_kind, prompt_sha256):
    # The credential hash scopes unnamed accounts without persisting a secret.
    return _prompt_sha256({
        "endpoint": endpoint, "account": account,
        "credential_sha256": hashlib.sha256(credential.encode("utf-8")).hexdigest(),
        "model": model, "caller": caller, "question_kind": question_kind,
        "prompt_sha256": prompt_sha256,
    })


def _cache_connection(path):
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    db = sqlite3.connect(path, timeout=2)
    os.chmod(path, 0o600)
    db.execute("CREATE TABLE IF NOT EXISTS results_v2 ("
               "cache_key TEXT PRIMARY KEY, expires_at REAL NOT NULL, "
               "result_json TEXT NOT NULL)")
    return db


def _cached_result(path, cache_key):
    try:
        db = _cache_connection(path)
        try:
            row = db.execute(
                "SELECT result_json, expires_at FROM results_v2 WHERE cache_key=?",
                (cache_key,)).fetchone()
        finally:
            db.close()
        if row and row[1] > time.time():
            answer = json.loads(row[0])
            # A cache hit made no vendor call. Do not repeat the old billable usage.
            return {**answer, "usage": None, "cache_hit": True}
    except (OSError, sqlite3.Error, ValueError, TypeError):
        pass
    return None


def _store_cached_result(path, cache_key, result, ttl):
    try:
        db = _cache_connection(path)
        try:
            db.execute("DELETE FROM results_v2 WHERE expires_at <= ?", (time.time(),))
            db.execute("INSERT OR REPLACE INTO results_v2 VALUES (?,?,?)",
                       (cache_key, time.time() + ttl,
                        json.dumps(result, separators=(",", ":"))))
            db.commit()
        finally:
            db.close()
    except (OSError, sqlite3.Error, TypeError, ValueError):
        pass  # Cache failure changes neither the answer nor a real call receipt.


def _append_call_receipt(questions, facets, result, log_path, *, caller=None,
                         question_kind=None, prompt_sha256=None, ok=True,
                         cache_hit=False, error=None, calibration=None):
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
            "session": _session_id(),
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
            "schema_valid": answered.get("schema_valid") is True,
            "usable": usable,
            "ok": bool(ok and usable and not cache_hit),
            "cache_hit": cache_hit,
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


def ask(state, questions, *, model=DEFAULT_MODEL, timeout=TIMEOUT_SECONDS,
        api_key=None, retries=RATE_LIMIT_RETRIES, endpoint=ENDPOINT, opener=None,
        facets=None, calls_log=JEV_CALLS_LOG, deadline=None, caller=None,
        cache_ttl_seconds=JUDGE_CACHE_TTL_SECONDS, cache_path=JUDGE_CACHE_PATH, account=None):
    """Evaluate `state` against a map of questions in ONE request.

    `state` is a string, or a mapping when the context has several parts —
    prefer named fields, and reference them from an instruction with backticked
    paths such as `ticket.messages[0].text`. `questions` maps a caller-chosen
    id to a question built by noul/choice/score; the ids come back unchanged and
    are never sent to the model, so the instruction must carry its full meaning.

    `facets` is optional: the names of any decision-0b11c89b required actions
    (ops/jev_build_advisory.py's FACETS, e.g. "architecture_or_design") this
    call is meant to satisfy. Pass it, or name the facet in a question id,
    when the call is meant to count as this turn's Jev use for that facet —
    lib/jev_required_actions.py's reader matches on either. Neither is
    required for an ask() that has nothing to do with a required action.

    Returns the decoded response: {"model": ..., "answers": {...},
    "usage": {...}}. `opener` is for the offline selftest and is not used in
    production. On a successful response this also appends one best-effort
    receipt row to `calls_log` (default out/jev-calls.jsonl) — see
    JEV_CALLS_LOG's module-level note for what it carries and why.

    `deadline` is optional: an absolute time.monotonic() value. With one,
    each attempt's timeout and each rate-limit sleep is capped at the time
    remaining, and no attempt or retry starts once it has passed — a caller
    under a hook timeout gets a TypeSafeError instead of a killed hook.
    `retries=0` turns rate-limit retries off entirely.

    `caller` names the invoking code in the usage log; if omitted it is inferred
    from the immediate caller file. Every real caller uses the shared 60s
    on-disk duplicate cache by default; a caller can explicitly pass zero.
    `account` names an account or organization when the caller has one. The
    credential hash also scopes the cache, including when no name is supplied.
    Cache hits return usage=None and cannot count as fresh vendor-call evidence.
    """
    if not isinstance(questions, dict) or not questions:
        raise TypeSafeError("ask needs a non-empty map of questions")

    payload = {"state": state, "model": model, "questions": questions}
    body = json.dumps(payload).encode("utf-8")
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
    use_cache = cache_ttl_seconds > 0 and opener is None
    credential = api_key or read_api_key()
    cache_key = (_cache_key(endpoint, account, credential, model, caller,
                            question_kind, prompt_sha256) if use_cache else None)
    if use_cache:
        hit = _cached_result(cache_path, cache_key)
        if hit is not None:
            hit["calibration"] = _safe_calibration_block(state, questions, hit, model)
            _append_call_receipt(questions, facets, hit, calls_log, caller=caller,
                                 question_kind=question_kind, prompt_sha256=prompt_sha256,
                                 ok=False, cache_hit=True, calibration=hit["calibration"])
            return hit

    request = urllib.request.Request(
        endpoint, data=body, method="POST",
        headers={
            "Authorization": f"Bearer {credential}",
            "Content-Type": "application/json",
            # Cloudflare rejects urllib's default Python-urllib signature with
            # error 1010. Identify this server-side client explicitly.
            "User-Agent": "carr-typesafe-client/1.0",
        },
    )

    send = opener or urllib.request.urlopen
    attempt = 0
    while True:
        attempt_timeout = timeout
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TypeSafeError("deadline passed before the request could be sent")
            attempt_timeout = min(timeout, remaining)
        try:
            with send(request, timeout=attempt_timeout) as response:
                http_status = getattr(response, "status", None)
                try:
                    result = json.load(response)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    if opener is None:
                        _append_call_receipt(questions, facets,
                            {"http_status": http_status, "schema_valid": False,
                             "usable": False}, calls_log, caller=caller,
                            question_kind=question_kind, prompt_sha256=prompt_sha256,
                            ok=False, error="invalid_json")
                    raise
            schema_valid = usable_judgment(result, questions)
            usable = (type(http_status) is int and 200 <= http_status < 300 and
                      schema_valid)
            calibration = (_safe_calibration_block(state, questions, result, model)
                           if schema_valid else None)
            # Round-2 fix: only a REAL production call (no opener) writes a
            # receipt. `opener` is the offline selftest/mock path (see the
            # docstring above) — a mock response was never actually seen by
            # the vendor, so a receipt for it would let a selftest run count
            # as this turn's real Jev evidence.
            if opener is None:
                receipt = ({**result, "http_status": http_status,
                            "schema_valid": schema_valid, "usable": usable}
                           if isinstance(result, dict) else
                           {"http_status": http_status, "schema_valid": False,
                            "usable": False})
                _append_call_receipt(questions, facets, receipt, calls_log,
                                     caller=caller, question_kind=question_kind,
                                     prompt_sha256=prompt_sha256, ok=usable,
                                     calibration=calibration)
            if not usable:
                raise TypeSafeError("TypeSafe returned an unusable judgment")
            if use_cache:
                _store_cached_result(cache_path, cache_key, result,
                                     cache_ttl_seconds)
            # Attached after caching, so the cache holds the vendor's answer
            # alone and a hit recomputes the block from it.
            result["calibration"] = calibration
            return result
        except urllib.error.HTTPError as err:
            if opener is None:
                _append_call_receipt(questions, facets, None, calls_log, caller=caller,
                                     question_kind=question_kind, prompt_sha256=prompt_sha256,
                                     ok=False, error=f"HTTP {err.code}")
            # 429 is documented as expected under load, and the service's own
            # limits "can change without notice". Honour retry-after when it is
            # sent; fall back to a short backoff when it is not.
            if err.code == 429 and attempt < retries:
                wait = err.headers.get("retry-after") if err.headers else None
                try:
                    delay = float(wait)
                except (TypeError, ValueError):
                    delay = 2.0 * (attempt + 1)
                if deadline is not None and delay >= deadline - time.monotonic():
                    # Sleeping as asked would leave no time for the retry.
                    raise TypeSafeError(
                        "TypeSafe returned HTTP 429 and its retry-after runs past "
                        "the caller's deadline") from None
                time.sleep(delay)
                attempt += 1
                continue
            # Report the status and what came back. NEVER report the request:
            # it carries the bearer token in a header.
            detail = ""
            try:
                detail = err.read().decode("utf-8", "replace")[:400]
            except Exception:
                pass
            raise TypeSafeError(f"TypeSafe returned HTTP {err.code}: {detail}") from None
        except urllib.error.URLError as err:
            if opener is None:
                _append_call_receipt(questions, facets, None, calls_log, caller=caller,
                                     question_kind=question_kind, prompt_sha256=prompt_sha256,
                                     ok=False, error="network")
            raise TypeSafeError(
                f"could not reach {endpoint}: {err.reason}. If this is a refusal "
                "rather than a network fault, check that the host is still in "
                "KNOWN_HOSTS in hooks/guard-unattended.py."
            ) from None
        except (json.JSONDecodeError, UnicodeDecodeError, TypeSafeError):
            raise
        except Exception as err:
            if opener is None:
                _append_call_receipt(questions, facets, None, calls_log, caller=caller,
                                     question_kind=question_kind, prompt_sha256=prompt_sha256,
                                     ok=False, error=type(err).__name__)
            raise


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
