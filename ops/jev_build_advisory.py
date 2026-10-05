"""Typed Jev facet classifier for explicit callers; prompt hooks defer it.

The UserPromptSubmit hook records human intent without creating an obligation.
Tool-result and Stop checks ask bounded questions once the evidence exists.
This optional classifier remains for offline calibration and direct callers.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import sys
from typing import Any


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)
SCHEMA = "jev-build-advisory/v2"
UNAVAILABLE_SCHEMA = "jev-build-advisory-unavailable/v1"
FACETS = (
    "architecture_or_design",
    "semantic_creation",
    "diagnosis",
    "verification_selection",
    "evidence_matching",
    "next_action_priority",
)
MAX_MESSAGE_CHARS = 90_000
# An explicit caller is bounded to one attempt and this timeout.
TIMEOUT_SECONDS = 6.0

QUESTION_TEXT = {
    "architecture_or_design":
        "Does answering `partner_request` require choosing among plausible "
        "architectures, interfaces, seams, data shapes, or implementation "
        "approaches based on semantic tradeoffs, rather than carrying out an "
        "already-specified mechanical operation?",
    "semantic_creation":
        "Does answering `partner_request` require creating or materially "
        "revising code or human-readable content whose meaning, fit, clarity, "
        "or likely behavior needs judgment beyond syntax and exact rules?",
    "diagnosis":
        "Does answering `partner_request` require deciding which explanation "
        "or defect class best fits an observed failure, regression, mismatch, "
        "or surprising behavior?",
    "verification_selection":
        "Does answering `partner_request` require choosing which tests, checks, "
        "or evidence would be proportionate and relevant, rather than merely "
        "running an exact check already named by the request or protocol?",
    "evidence_matching":
        "Does answering `partner_request` require judging whether a source, "
        "claim, change, artifact, or prior result semantically supports the "
        "conclusion being considered?",
    "next_action_priority":
        "Does answering `partner_request` require prioritizing among several "
        "reasonable next actions based on meaning, impact, blockage, or fit, "
        "rather than following one deterministic authorized next step?",
}


class AdvisoryUnavailable(RuntimeError):
    """Jev did not return one complete, typed advisory."""

    def __init__(self, reason: str = "unknown"):
        self.reason = reason if reason in FAILURE_REASONS else "unknown"
        super().__init__(self.reason)


FAILURE_REASONS = frozenset({"billing_exhausted", "auth_failed", "rate_limited",
                             "timeout", "network", "server_5xx", "unknown"})


def failure_reason(exc: Exception) -> str:
    """Reduce a provider failure to a safe code; never return its body."""
    inherited = getattr(exc, "reason", None)
    if isinstance(inherited, str) and inherited in FAILURE_REASONS:
        return inherited
    status = getattr(exc, "code", None)
    if not isinstance(status, int):
        match = re.search(r"\bTypeSafe returned HTTP (\d{3})\b", str(exc))
        status = int(match.group(1)) if match else None
    if status == 402:
        return "billing_exhausted"
    if status in (401, 403):
        return "auth_failed"
    if status == 429:
        return "rate_limited"
    if isinstance(status, int) and 500 <= status <= 599:
        return "server_5xx"
    name = type(exc).__name__.lower()
    message = str(exc).lower()
    if "timeout" in name or "timed out" in message or "deadline" in message:
        return "timeout"
    if ("urlerror" in name or "connection" in name or "could not reach" in message
            or "network" in message):
        return "network"
    return "unknown"


def unavailable(reason: str = "unknown") -> dict:
    """A fixed, redacted abstention for a missing build-time judgment."""
    if reason not in FAILURE_REASONS:
        reason = "unknown"
    if reason == "billing_exhausted":
        instruction = "Jev is offline: TypeSafe account has no API credits — Joe must add credits."
    elif reason == "auth_failed":
        instruction = "Jev is offline: TypeSafe authentication failed — Joe must repair access."
    else:
        instruction = (
            "Jev build-time intake was unavailable. Do not silently represent "
            "an agent-only semantic judgment as Jev-assisted. Deterministic "
            "work may continue; qualified judgment remains explicit and uncredited."
        )
    return {
        "schema": UNAVAILABLE_SCHEMA,
        "status": "unavailable",
        "reason": reason,
        "effect": "visible_advisory_abstention",
        "instruction": instruction,
    }


def _client():
    path = os.path.join(REPO, "ops", "typesafe_client.py")
    spec = importlib.util.spec_from_file_location("typesafe_client_build", path)
    if spec is None or spec.loader is None:
        raise AdvisoryUnavailable("cannot load TypeSafe client")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def questions(client: Any) -> dict[str, dict]:
    facets = {
        key: client.noul(
            text,
            true="This build turn contains that semantic judgment.",
            false="This build turn does not contain that judgment, or the work is deterministic.",
        )
        for key, text in QUESTION_TEXT.items()
    }
    return facets


def _probability(answer: Any) -> float:
    if not isinstance(answer, dict) or answer.get("type") != "noul":
        raise AdvisoryUnavailable("Jev returned a non-noul build facet")
    try:
        value = float(answer["noul"])
    except (KeyError, TypeError, ValueError):
        raise AdvisoryUnavailable("Jev omitted a build-facet probability") from None
    if not 0.0 <= value <= 1.0:
        raise AdvisoryUnavailable("Jev returned an out-of-range probability")
    return value


SKIPPED_SCHEMA = "jev-build-advisory-skipped/v1"
CACHE_PATH = os.path.join(REPO, "out", "jev-build-advisory-cache.json")
CACHE_SOURCES = ("ops/jev_build_advisory.py", "ops/typesafe_client.py",
                 "lib/rule_delivery_preuse.py", "ops/jev_verdict_cache.py",
                 "ops/machine_envelope.py")


def is_machine_envelope(prompt: str) -> bool:
    """A prompt that is ENTIRELY machine envelopes — complete background-task
    notification or cross-session blocks, with any system reminders around
    them and nothing else. ops/machine_envelope.py holds the definition and
    why a prefix test was not enough. Measured 2026-09-25: most prompts in a
    long orchestration session are task notifications, and Jev was asked for
    build advice on every one."""
    path = os.path.join(REPO, "ops", "machine_envelope.py")
    spec = importlib.util.spec_from_file_location("machine_envelope_build", path)
    if spec is None or spec.loader is None:
        return False  # cannot tell: advise, never skip
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.is_machine_envelope(prompt)


def skipped() -> dict:
    """The fixed record for a machine envelope: nothing asked, nothing owed."""
    return {"schema": SKIPPED_SCHEMA, "status": "skipped",
            "reason": "machine_envelope", "effect": "no_advice_required"}


def deferred() -> dict:
    """Human intent is recorded; semantic questions wait for evidence at use."""
    return {"schema": SKIPPED_SCHEMA, "status": "skipped",
            "reason": "boundary_deferred", "effect": "no_prompt_obligation"}


def _cache():
    path = os.path.join(REPO, "ops", "jev_verdict_cache.py")
    spec = importlib.util.spec_from_file_location("jev_verdict_cache_build", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load ops/jev_verdict_cache.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def advise(partner_request: str, *, client: Any | None = None,
           timeout: float = TIMEOUT_SECONDS, cache_path: str | None = None,
           now: float | None = None) -> dict:
    """Return one typed, attributable reading of a partner's build request.

    A machine envelope gets skipped() with no Jev call. A byte-identical
    request answered inside the cache window is answered from the cache; the
    cache is on by default only for the real client, and any cache failure
    simply asks."""
    if isinstance(partner_request, str) and is_machine_envelope(partner_request):
        return skipped()
    if cache_path is None and client is None:
        cache_path = CACHE_PATH
    cache = entry_key = None
    if cache_path and isinstance(partner_request, str):
        try:
            cache = _cache()
            entry_key = cache.key({"request": partner_request,
                                   "source": cache.source_digest(*CACHE_SOURCES)})
            cached = cache.get(cache_path, entry_key, now=now)
            if isinstance(cached, dict) and cached.get("schema") == SCHEMA:
                # No request was made, so none is reported: the original
                # call's usage stays with the original call.
                return {**cached, "usage": {"input_tokens": 0, "output_tokens": 0,
                                            "cache_hit": True}}
        except Exception:
            cache = None
    result = _advise(partner_request, client=client, timeout=timeout)
    if cache is not None:
        cache.put(cache_path, entry_key, result, now=now)
    return result


def _advise(partner_request: str, *, client: Any | None = None,
            timeout: float = TIMEOUT_SECONDS) -> dict:
    """One Jev request for a partner's build request."""
    if not isinstance(partner_request, str) or not partner_request.strip():
        raise AdvisoryUnavailable("partner request is empty")
    if len(partner_request) > MAX_MESSAGE_CHARS:
        raise AdvisoryUnavailable("partner request exceeds the bounded state cap")
    tsc = client or _client()
    try:
        response = tsc.ask(
            {"partner_request": partner_request},
            questions(tsc),
            timeout=min(timeout, TIMEOUT_SECONDS),
        )
    except Exception as exc:
        reason = failure_reason(exc)
        from ops import jev_judge
        log_path = os.path.join(getattr(tsc, "CANONICAL_REPO", REPO),
                                "out", "jev-judge.jsonl")
        jev_judge.record("build_advisory", "provider_call", None,
                         error=f"build_advisory:{reason}", log_path=log_path)
        raise AdvisoryUnavailable(reason) from None
    answers = response.get("answers") if isinstance(response, dict) else None
    model = response.get("model") if isinstance(response, dict) else None
    if not isinstance(answers, dict) or not isinstance(model, str) or not model.strip():
        raise AdvisoryUnavailable("Jev omitted answers or model provenance")
    facets = {key: _probability(answers.get(key)) for key in FACETS}
    return {
        "schema": SCHEMA,
        "partner_request_sha256": hashlib.sha256(
            json.dumps(partner_request, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=False).encode("utf-8")).hexdigest(),
        "model": model,
        "facets": facets,
        "usage": response.get("usage") if isinstance(response.get("usage"), dict) else {},
    }
