"""Typed Jev intake for judgment-shaped engineering work.

The partner request is the earliest common seam shared by Codex and Claude.
This module asks narrow, independent questions there so a build session receives
an attributable Jev reading before it starts choosing an architecture, writing
semantically meaningful code or prose, diagnosing a failure, selecting checks,
matching evidence, or prioritizing the next action.

The output is advisory.  It does not authorize a write, select a tool, replace
deterministic checks, or prove completion.  Code owns those consequences.  A
missing Jev response is represented by the hook as a visible unavailable state;
it is never silently replaced with an agent's unrecorded judgment.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
from typing import Any


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)
from lib.rule_delivery_preuse import (  # noqa:E402
    BUILD_ACTIONS, BUILD_ACTION_THRESHOLD,
)
SCHEMA = "jev-build-advisory/v1"
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
GUIDANCE_TEXT = {
    "extend_existing_seam": (
        "Given `partner_request`, is extending an existing proven seam or deep module "
        "more likely to fit than creating a parallel mechanism?"
    ),
    "prefer_reversible_slice": (
        "Given `partner_request`, should the implementation favor a small reversible "
        "slice because uncertainty, blast radius, or future learning is material?"
    ),
    "define_typed_contract_first": (
        "Given `partner_request`, would defining the typed input/output or receipt "
        "contract before implementation materially improve correctness and clarity?"
    ),
    "gather_more_evidence_before_diagnosis": (
        "Does `partner_request` currently lack enough evidence to settle a diagnosis "
        "without first gathering another concrete observation?"
    ),
    "prefer_behavioral_verification": (
        "Given `partner_request`, is behavior or integration evidence more probative "
        "than unit-level or shape-only checks by themselves?"
    ),
    "require_fresh_exact_evidence": (
        "Given `partner_request`, should the conclusion rely on fresh exact bindings "
        "rather than reusing earlier evidence without revalidation?"
    ),
    "prioritize_blocker_removal": (
        "Given `partner_request`, should the next action prioritize removing a concrete "
        "blocker or uncertainty before expanding implementation scope?"
    ),
}


class AdvisoryUnavailable(RuntimeError):
    """Jev did not return one complete, typed advisory."""


def unavailable() -> dict:
    """A fixed, redacted abstention for a missing build-time judgment."""
    return {
        "schema": UNAVAILABLE_SCHEMA,
        "status": "unavailable",
        "effect": "visible_advisory_abstention",
        "instruction": (
            "Jev build-time intake was unavailable. Do not silently represent "
            "an agent-only semantic judgment as Jev-assisted. Deterministic "
            "work may continue; qualified judgment remains explicit and uncredited."
        ),
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
    guidance = {
        key: client.noul(
            text,
            true=f"The build should follow the {key} direction.",
            false=f"The build should not assume the {key} direction.",
        )
        for key, text in GUIDANCE_TEXT.items()
    }
    return {**facets, **guidance}


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


def advise(partner_request: str, *, client: Any | None = None,
           timeout: float = 20.0) -> dict:
    """Return one typed, attributable reading of a partner's build request."""
    if not isinstance(partner_request, str) or not partner_request.strip():
        raise AdvisoryUnavailable("partner request is empty")
    if len(partner_request) > MAX_MESSAGE_CHARS:
        raise AdvisoryUnavailable("partner request exceeds the bounded state cap")
    tsc = client or _client()
    try:
        response = tsc.ask(
            {"partner_request": partner_request},
            questions(tsc),
            timeout=timeout,
        )
    except Exception as exc:
        raise AdvisoryUnavailable(f"{type(exc).__name__}: Jev unavailable") from None
    answers = response.get("answers") if isinstance(response, dict) else None
    model = response.get("model") if isinstance(response, dict) else None
    if not isinstance(answers, dict) or not isinstance(model, str) or not model.strip():
        raise AdvisoryUnavailable("Jev omitted answers or model provenance")
    facets = {key: _probability(answers.get(key)) for key in FACETS}
    guidance = {key: _probability(answers.get(key)) for key in GUIDANCE_TEXT}
    return {
        "schema": SCHEMA,
        "partner_request_sha256": hashlib.sha256(
            json.dumps(partner_request, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=False).encode("utf-8")).hexdigest(),
        "model": model,
        "facets": facets,
        "guidance": guidance,
        "required_actions": [
            {"facet": facet, "instruction": BUILD_ACTIONS[facet]}
            for facet in FACETS if facets[facet] >= BUILD_ACTION_THRESHOLD
        ],
        "usage": response.get("usage") if isinstance(response.get("usage"), dict) else {},
        "authority": "advisory_only",
        "deterministic_exclusions": [
            "arithmetic", "dates_and_counts", "identity", "permissions_and_authority",
            "invariants", "execution", "writes", "completion_proof",
        ],
    }
