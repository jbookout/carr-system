#!/usr/bin/env python3
"""Deterministic acceptance checks for the shared CARR Design Kernel."""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "room-bridge"))

import design_kernel  # noqa: E402
import evaluation_kernel  # noqa: E402


FIXTURES = ROOT / "control-room" / "contracts" / "fixtures" / "execution-fabric"


def load(path: Path):
    return json.loads(path.read_text())


def refuse(fn, phrase: str):
    try:
        fn()
    except (design_kernel.DesignKernelError, evaluation_kernel.EvalPortfolioError) as exc:
        assert phrase in str(exc), exc
        return
    raise AssertionError(f"expected refusal containing {phrase}")


def run(name, fn):
    fn()
    print(f"ok  {name}")


def contract_is_narrow_and_carr_canonical():
    kernel = load(ROOT / "design" / "carr-design-kernel.v1.json")
    assert design_kernel.validate_design_kernel(kernel) == kernel
    assert kernel["priority_hierarchy"] == design_kernel.PRIORITY
    assert kernel["token_architecture"]["canonical_stylesheet"] == "design/tokens.css"
    tokens = (ROOT / "design" / "tokens.css").read_text()
    for token in ("--navy:", "--navy-deep:", "--orange:", "--paper:", "--component-card-background:", "--component-control-focus-outline:"):
        assert token in tokens, token
    assert "primitives_stable_semantic_aliases_swap" == kernel["token_architecture"]["theme_policy"]


def context_is_intent_routed_not_a_giant_prompt():
    kernel = load(ROOT / "design" / "carr-design-kernel.v1.json")
    context = design_kernel.design_context(kernel, "intent:model-room")
    assert [row["slice_id"] for row in context["context_slices"]] == ["slice:core", "slice:interactive", "slice:structured"]
    assert "slice:review" not in [row["slice_id"] for row in context["context_slices"]]
    assert context["contract_digest"] == design_kernel.canonical_digest(kernel)
    refuse(lambda: design_kernel.design_context(kernel, "intent:not-real"), "unknown design intent")


def invalid_hierarchy_or_incomplete_states_fail_closed():
    kernel = load(ROOT / "design" / "carr-design-kernel.v1.json")
    changed = copy.deepcopy(kernel)
    changed["priority_hierarchy"][1], changed["priority_hierarchy"][2] = changed["priority_hierarchy"][2], changed["priority_hierarchy"][1]
    refuse(lambda: design_kernel.validate_design_kernel(changed), "design hierarchy")
    changed = copy.deepcopy(kernel)
    changed["component_state_contract"]["required_states"].remove("error")
    refuse(lambda: design_kernel.validate_design_kernel(changed), "component state contract")


def real_browser_gate_receipt_is_complete_and_bound():
    kernel = load(ROOT / "design" / "carr-design-kernel.v1.json")
    report = load(FIXTURES / "carr-design-kernel.visual-gate-report.synthetic.v1.json")
    checked = design_kernel.validate_visual_gate_report(report, kernel, expected_work_request_id="wr-synthetic-read-only", expected_projection_digest="sha256:7ef1df546040c0aa000a437902fdc4d83decaf1960f63001790b11a422b5dfb9")
    assert checked["admission"]["aggregate_score"] is None
    assert checked["aesthetic_critique"]["authority"] == "advisory_never_promotion"
    changed = copy.deepcopy(report)
    next(row for row in changed["gate_results"] if row["gate_id"] == "narrow_280")["measurement"]["viewport_width_px"] = 281
    refuse(lambda: design_kernel.validate_visual_gate_report(changed, kernel), "exact viewport width")
    changed = copy.deepcopy(report)
    changed["evidence"]["runner"] = "css_read"
    refuse(lambda: design_kernel.validate_visual_gate_report(changed, kernel), "real-browser")


def critical_visual_failure_is_an_evaluation_kernel_blocker_not_a_score_tradeoff():
    kernel = load(ROOT / "design" / "carr-design-kernel.v1.json")
    report = load(FIXTURES / "carr-design-kernel.visual-gate-report.synthetic.v1.json")
    portfolio = load(FIXTURES / "carr-evaluation-kernel.synthetic.v1.json")
    decision = evaluation_kernel.admission_decision(portfolio, visual_gate_report=report, design_contract=kernel)
    assert "visual_gate_critical_failure" not in " ".join(decision["reason_codes"])
    changed = copy.deepcopy(report)
    row = next(row for row in changed["gate_results"] if row["gate_id"] == "narrow_280")
    row["status"] = "failed"
    changed["admission"]["state"] = "not_admitted"
    changed["admission"]["critical_blockers"] = ["narrow_280"]
    decision = evaluation_kernel.admission_decision(portfolio, visual_gate_report=changed, design_contract=kernel)
    assert decision["decision"] == "not_admitted"
    assert "visual_gate_critical_failure:narrow_280" in decision["reason_codes"]
    refuse(lambda: evaluation_kernel.admission_decision(portfolio, visual_gate_report=changed), "must travel together")


if __name__ == "__main__":
    for label, check in (
        ("design contract preserves CARR canon", contract_is_narrow_and_carr_canonical),
        ("design context is intent routed", context_is_intent_routed_not_a_giant_prompt),
        ("hierarchy and component states fail closed", invalid_hierarchy_or_incomplete_states_fail_closed),
        ("browser gate receipt is complete and exact-bound", real_browser_gate_receipt_is_complete_and_bound),
        ("critical visual failure blocks shared evaluation", critical_visual_failure_is_an_evaluation_kernel_blocker_not_a_score_tradeoff),
    ):
        run(label, check)
