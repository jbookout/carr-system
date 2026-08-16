"""Pure, finite post-provider contracts for cognition workflow proposals.

These validators deliberately reconcile model output to the typed input that
admitted the job.  They do not open a connection, call a provider, or mutate
state.  Empty output is rejected unless the provider uses the explicit,
evidence-bound ``no_data`` shape.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class ProposalContractError(ValueError):
    pass


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProposalContractError(f"{field} must be non-empty text")
    return value.strip()


def _rows(proposal: Mapping[str, Any], field: str) -> list[Mapping[str, Any]]:
    rows = proposal.get(field)
    if not isinstance(rows, list) or not rows or not all(isinstance(row, Mapping) for row in rows):
        raise ProposalContractError(f"{field} must be a non-empty list of objects")
    return list(rows)


def _input_refs(value: Any) -> set[str]:
    refs: set[str] = set()
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key.endswith("_ref") and isinstance(item, str) and item:
                refs.add(item)
            refs |= _input_refs(item)
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, str) and item:
                refs.add(item)
            refs |= _input_refs(item)
    return refs


def _no_data(proposal: Mapping[str, Any], input_refs: set[str], result_field: str) -> bool:
    marker = proposal.get("no_data")
    if marker is None:
        return False
    if not isinstance(marker, Mapping):
        raise ProposalContractError("no_data must be an object")
    _text(marker.get("reason"), "no_data.reason")
    refs = marker.get("evidence_refs")
    if not isinstance(refs, list) or not refs or not all(isinstance(ref, str) and ref in input_refs for ref in refs):
        raise ProposalContractError("no_data.evidence_refs must name known input refs")
    if proposal.get(result_field) not in ([], None):
        raise ProposalContractError("no_data proposal may not also carry result rows")
    return True


def _source_refs(row: Mapping[str, Any], known: set[str], field: str = "source_refs") -> None:
    refs = row.get(field)
    if not isinstance(refs, list) or not refs or not all(isinstance(ref, str) and ref in known for ref in refs):
        raise ProposalContractError(f"{field} must be a non-empty subset of typed input refs")


def _proposal_only(row: Mapping[str, Any], field: str = "action") -> None:
    if row.get(field) not in {"propose", "draft"}:
        raise ProposalContractError("output action must remain proposal/draft only")


def _subject_refs(input_payload: Mapping[str, Any]) -> set[str]:
    subjects = input_payload.get("subjects")
    if not isinstance(subjects, list) or not subjects:
        raise ProposalContractError("typed input subjects are required")
    refs = {_text(row.get("subject_id"), "input subject_id") for row in subjects if isinstance(row, Mapping)}
    if len(refs) != len(subjects):
        raise ProposalContractError("typed input subjects are malformed or duplicate")
    return refs


def _validate_research(workflow: str, input_payload: Mapping[str, Any], proposal: Mapping[str, Any]) -> None:
    subjects = _subject_refs(input_payload)
    if _no_data(proposal, subjects, "findings"):
        return
    findings = _rows(proposal, "findings")
    observed: set[str] = set()
    for finding in findings:
        subject_ref = _text(finding.get("subject_ref"), "finding.subject_ref")
        if subject_ref not in subjects or subject_ref in observed:
            raise ProposalContractError("finding subject_ref must be unique and come from typed input")
        observed.add(subject_ref)
        _proposal_only(finding)
        if workflow == "contact-enrichment-weekly":
            _text(finding.get("source_ref"), "finding.source_ref")
            _text(finding.get("observed_at"), "finding.observed_at")
            if finding.get("status") not in {"verified", "unverified", "cannot_verify"}:
                raise ProposalContractError("enrichment finding status is invalid")
        else:
            if finding.get("source_class") != "direct_identity":
                raise ProposalContractError("deal-history finding must cite direct identity source")
            _source_refs(finding, subjects, "subject_refs")


def _validate_radar(input_payload: Mapping[str, Any], proposal: Mapping[str, Any]) -> None:
    lanes = input_payload.get("lanes")
    known = {_text(row.get("lane"), "input lane") for row in lanes} if isinstance(lanes, list) else set()
    if not known:
        raise ProposalContractError("typed radar lanes are required")
    if _no_data(proposal, known, "candidates"):
        return
    candidates = _rows(proposal, "candidates")
    seen: set[str] = set()
    for candidate in candidates:
        lane = _text(candidate.get("lane"), "candidate.lane")
        if lane not in known or lane in seen:
            raise ProposalContractError("radar candidate lane must be unique typed input lane")
        seen.add(lane); _proposal_only(candidate)
    health = _rows(proposal, "lane_health")
    if {row.get("lane") for row in health} != known or any(row.get("state") not in {"healthy", "warning", "blocked"} for row in health):
        raise ProposalContractError("lane_health must cover every typed input lane exactly")


def _validate_npi(input_payload: Mapping[str, Any], proposal: Mapping[str, Any]) -> None:
    inputs = input_payload.get("npi_candidates")
    if not isinstance(inputs, list) or not inputs:
        raise ProposalContractError("typed deterministic NPI candidates are required")
    known: set[tuple[str, str]] = set()
    for row in inputs:
        if not isinstance(row, Mapping):
            raise ProposalContractError("typed deterministic NPI candidate is malformed")
        pair = (_text(row.get("npi"), "input npi"),
                _text(row.get("source_ref"), "input source_ref"))
        if pair in known:
            raise ProposalContractError("typed deterministic NPI candidates are duplicated")
        known.add(pair)
    candidates = _rows(proposal, "candidates"); seen: set[tuple[str, str]] = set()
    npi_seen: set[str] = set()
    for candidate in candidates:
        source_ref = _text(candidate.get("source_row_ref"), "candidate.source_row_ref")
        npi = _text(candidate.get("npi"), "candidate.npi")
        pair = (npi, source_ref)
        if pair not in known:
            raise ProposalContractError("NPI proposal does not reconcile to deterministic input")
        if pair in seen or npi in npi_seen:
            raise ProposalContractError("NPI candidates must be deterministically deduplicated")
        seen.add(pair); npi_seen.add(npi); _proposal_only(candidate)


def _validate_idea(input_payload: Mapping[str, Any], proposal: Mapping[str, Any]) -> None:
    ideas = input_payload.get("ideas")
    known = set(ideas) if isinstance(ideas, list) and all(isinstance(x, str) and x for x in ideas) else set()
    if not known: raise ProposalContractError("typed idea refs are required")
    if _no_data(proposal, known, "shortlist"): return
    rows = _rows(proposal, "shortlist"); seen: set[str] = set()
    for row in rows:
        ref = _text(row.get("canonical_row_ref"), "shortlist.canonical_row_ref")
        if ref not in known or ref in seen: raise ProposalContractError("shortlist refs must be unique typed idea refs")
        seen.add(ref); _proposal_only(row)


def _validate_audit(workflow: str, input_payload: Mapping[str, Any], proposal: Mapping[str, Any]) -> None:
    known = _input_refs(input_payload)
    if workflow == "ai-capability-builder":
        for field in ("worktree", "tests", "risks", "next_human_action"):
            _text(proposal.get(field), f"proposal.{field}")
        return
    field = "proposed_actions" if workflow in {"loop-drain-weekdays", "playbook-review-monthly", "system-sweep-monthly"} else "findings"
    if _no_data(proposal, known, field):
        return
    rows = _rows(proposal, field)
    for row in rows:
        refs_field = "evidence_refs" if field == "proposed_actions" else "source_refs"
        _source_refs(row, known, refs_field)
        _proposal_only(row)
        if workflow == "loop-drain-weekdays" and row.get("data_class_grant") != "granted":
            raise ProposalContractError("loop proposal must remain inside its data grant")
        if workflow == "playbook-review-monthly" and row.get("approval_state") != "required":
            raise ProposalContractError("playbook proposal requires human approval")
        if workflow == "system-sweep-monthly" and row.get("destructive") is True and (row.get("recoverable") is not True or row.get("approval_state") != "required"):
            raise ProposalContractError("destructive system proposal must be recoverable and human-gated")


def _validate_social(workflow: str, input_payload: Mapping[str, Any], proposal: Mapping[str, Any]) -> None:
    if workflow == "social-metrics-pull-weekly":
        exports = input_payload.get("platform_exports")
        known = {_text(row.get("placement_id"), "input placement_id") for row in exports} if isinstance(exports, list) else set()
        if not known: raise ProposalContractError("typed platform exports are required")
        if _no_data(proposal, known, "measurements"): return
        rows = _rows(proposal, "measurements")
        seen: set[str] = set()
        for row in rows:
            placement = _text(row.get("placement_id"), "measurement.placement_id")
            if placement not in known or placement in seen: raise ProposalContractError("measurement placement_id must be unique typed input placement")
            seen.add(placement)
            if not isinstance(row.get("value"), (int, float)) or isinstance(row.get("value"), bool): raise ProposalContractError("measurement value must be numeric")
            _text(row.get("source_observed_at"), "measurement.source_observed_at"); _proposal_only(row)
        return
    if workflow == "social-batch-weekly":
        known = set(input_payload.get("source_refs", []))
        if not known or not all(isinstance(x, str) and x for x in known): raise ProposalContractError("typed social source_refs are required")
        if _no_data(proposal, known, "drafts"): return
        drafts = _rows(proposal, "drafts")
        for draft in drafts:
            _text(draft.get("platform"), "draft.platform"); _text(draft.get("body"), "draft.body")
            _source_refs(draft, known); _proposal_only(draft)
            if draft.get("lint_state") != "passed" or draft.get("format_valid") is not True: raise ProposalContractError("social draft failed lint or format")
        return
    posts = input_payload.get("source_posts")
    known = {_text(row.get("url"), "input post url") for row in posts} if isinstance(posts, list) else set()
    if not known: raise ProposalContractError("typed engagement source_posts are required")
    if _no_data(proposal, known, "drafts"): return
    drafts = _rows(proposal, "drafts")
    lower, upper = ((3, 5) if workflow == "linkedin-engagement-daily" else (5, 10))
    if not lower <= len(drafts) <= upper: raise ProposalContractError("draft count is outside workflow bound")
    voice = input_payload.get("voice_version")
    for draft in drafts:
        if _text(draft.get("source_url"), "draft.source_url") not in known: raise ProposalContractError("draft source_url is not an input post")
        _text(draft.get("relationship"), "draft.relationship"); _proposal_only(draft)
        if draft.get("voice_version") != voice: raise ProposalContractError("draft voice version does not match typed input")


def validate_proposal_contract(workflow_key: str, input_payload: Mapping[str, Any], proposal: Mapping[str, Any]) -> None:
    """Validate one named workflow proposal or raise ``ProposalContractError``."""
    if not isinstance(input_payload, Mapping) or not isinstance(proposal, Mapping):
        raise ProposalContractError("input and proposal must be objects")
    if workflow_key in {"contact-enrichment-weekly", "deal-history-research-weekly"}:
        _validate_research(workflow_key, input_payload, proposal)
    elif workflow_key == "radar-weekly": _validate_radar(input_payload, proposal)
    elif workflow_key == "npi-sweep-weekly": _validate_npi(input_payload, proposal)
    elif workflow_key == "idea-resurface-monthly": _validate_idea(input_payload, proposal)
    elif workflow_key in {"ai-capability-builder", "cc-update-audit", "health-audit-monthly", "loop-drain-weekdays", "playbook-review-monthly", "system-sweep-monthly"}:
        _validate_audit(workflow_key, input_payload, proposal)
    elif workflow_key in {"linkedin-engagement-daily", "x-reply-run-daily", "social-batch-weekly", "social-metrics-pull-weekly"}:
        _validate_social(workflow_key, input_payload, proposal)
    else:
        raise ProposalContractError(f"workflow has no registered proposal contract: {workflow_key}")
