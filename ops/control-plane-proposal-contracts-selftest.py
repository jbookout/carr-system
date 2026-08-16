#!/usr/bin/env python3
"""Fail-closed fixtures for the isolated cognition proposal contracts."""
from __future__ import annotations

import copy
import sys
from pathlib import Path
from typing import Any, cast

REPO = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(REPO))
from lib.control_plane_proposal_contracts import ProposalContractError, validate_proposal_contract

FAILED: list[str] = []


def check(label: str, value: bool) -> None:
    print(("  ok    " if value else "  FAIL  ") + label)
    if not value: FAILED.append(label)


def accepts(label: str, workflow: str, input_payload: dict, proposal: dict) -> None:
    try: validate_proposal_contract(workflow, input_payload, proposal)
    except ProposalContractError as exc: check(label, False); print(f"        {exc}")
    else: check(label, True)


def refuses(label: str, workflow: str, input_payload: dict, proposal: dict) -> None:
    try: validate_proposal_contract(workflow, input_payload, proposal)
    except ProposalContractError: check(label, True)
    else: check(label, False)


def main() -> int:
    subjects = {"subjects": [{"subject_id": "party:1"}, {"subject_id": "party:2"}]}
    enrichment = {"findings": [{"subject_ref":"party:1", "source_ref":"source:1", "observed_at":"2026-08-16T00:00:00Z", "status":"verified", "action":"propose"}]}
    accepts("enrichment reconciles finding to input subject", "contact-enrichment-weekly", subjects, enrichment)
    bad: dict[str, Any] = copy.deepcopy(cast(Any, enrichment)); bad["findings"][0]["subject_ref"] = "party:404"
    refuses("enrichment refuses unknown input subject", "contact-enrichment-weekly", subjects, bad)
    refuses("enrichment refuses vacuous empty findings", "contact-enrichment-weekly", subjects, {"findings": []})
    accepts("explicit no_data preserves legitimate enrichment zero", "contact-enrichment-weekly", subjects, {"findings": [], "no_data":{"reason":"no verified results", "evidence_refs":["party:1"]}})
    deal = {"findings":[{"subject_ref":"party:1", "subject_refs":["party:1"], "source_class":"direct_identity", "action":"propose"}]}
    accepts("deal-history binds direct identity finding", "deal-history-research-weekly", subjects, deal)
    bad = copy.deepcopy(cast(Any, deal)); bad["findings"][0]["source_class"] = "directory"
    refuses("deal-history refuses non-direct identity source", "deal-history-research-weekly", subjects, bad)
    radar_input = {"lanes":[{"lane":"local"},{"lane":"cold"}]}
    radar = {"candidates":[{"lane":"local","action":"propose"}], "lane_health":[{"lane":"local","state":"healthy"},{"lane":"cold","state":"warning"}]}
    accepts("radar reconciles candidate and complete lane health", "radar-weekly", radar_input, radar)
    bad = copy.deepcopy(cast(Any, radar)); bad["candidates"][0]["lane"] = "invented"
    refuses("radar refuses invented lane", "radar-weekly", radar_input, bad)
    audit_input = {"facts":{"release_source_ref":"release:1"}}
    accepts("release audit requires input-bound evidence", "cc-update-audit", audit_input, {"findings":[{"source_refs":["release:1"],"action":"propose"}]})
    refuses("release audit refuses empty findings", "cc-update-audit", audit_input, {"findings":[]})
    accepts("capability proposal requires all finite plan fields", "ai-capability-builder", {}, {"worktree":"w","tests":"t","risks":"r","next_human_action":"n"})
    refuses("capability proposal refuses missing finite plan field", "ai-capability-builder", {}, {"worktree":"w","tests":"t","risks":"r"})
    social_input = {"source_refs":["content:1"], "voice_version": 7}
    social = {"drafts":[{"platform":"linkedin","body":"x","source_refs":["content:1"],"action":"draft","lint_state":"passed","format_valid":True}]}
    accepts("social batch binds sources and draft firewall", "social-batch-weekly", social_input, social)
    bad = copy.deepcopy(cast(Any, social)); bad["drafts"][0]["action"] = "publish"
    refuses("social batch refuses publication action", "social-batch-weekly", social_input, bad)
    post_input = {"source_posts":[{"url":"https://x.example/1"},{"url":"https://x.example/2"},{"url":"https://x.example/3"}], "voice_version": 2}
    linked = {"drafts":[{"source_url":f"https://x.example/{n}","relationship":"known","voice_version":2,"action":"draft"} for n in (1,2,3)]}
    accepts("LinkedIn enforces bounded typed draft rows", "linkedin-engagement-daily", post_input, linked)
    refuses("LinkedIn refuses fewer than three drafts", "linkedin-engagement-daily", post_input, {"drafts": linked["drafts"][:2]})
    x_input = {"source_posts":[{"url":f"https://x.example/{n}"} for n in range(1,6)], "voice_version": 2}
    x = {"drafts":[{"source_url":f"https://x.example/{n}","relationship":"known","voice_version":2,"action":"draft"} for n in range(1,6)]}
    accepts("X enforces its five-draft lower bound", "x-reply-run-daily", x_input, x)
    metrics_input = {"platform_exports":[{"placement_id":"p1"}]}
    metrics = {"measurements":[{"placement_id":"p1","value":1,"source_observed_at":"2026-08-16T00:00:00Z","action":"propose"}]}
    accepts("metrics reconciles measurement placement", "social-metrics-pull-weekly", metrics_input, metrics)
    refuses("metrics refuses empty measurement list", "social-metrics-pull-weekly", metrics_input, {"measurements":[]})
    bad = copy.deepcopy(cast(Any, metrics)); bad["measurements"][0]["placement_id"] = "unknown"
    refuses("metrics refuses unknown placement", "social-metrics-pull-weekly", metrics_input, bad)
    npi_input = {"npi_candidates":[{"source_ref":"source:nppes:1","npi":"123"}]}
    npi = {"candidates":[{"source_row_ref":"source:nppes:1","npi":"123","action":"propose"}]}
    accepts("NPI candidate reconciles to deterministic source and dedup key", "npi-sweep-weekly", npi_input, npi)
    refuses("NPI refuses a provider-invented source even if it asserts territory", "npi-sweep-weekly",
            npi_input, {"candidates":[{"source_row_ref":"source:nppes:fake","npi":"123",
                                       "territory_match":True,"action":"propose"}]})
    refuses("NPI refuses duplicate NPI candidate", "npi-sweep-weekly", npi_input, {"candidates": npi["candidates"] * 2})
    accepts("idea shortlist reconciles canonical idea input", "idea-resurface-monthly", {"ideas":["idea:1"]}, {"shortlist":[{"canonical_row_ref":"idea:1","action":"propose"}]})
    refuses("unregistered workflow refuses by default", "unknown", {}, {})
    print(f"proposal contracts selftest — {len(FAILED)} failure(s)")
    return 1 if FAILED else 0


if __name__ == "__main__": raise SystemExit(main())
