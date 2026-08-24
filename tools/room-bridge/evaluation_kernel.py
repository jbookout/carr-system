"""Offline, deterministic shared CARR evaluation kernel v1.

This is an evidence read model and deterministic admission recommendation, not
an experiment tracker, router, store, or promotion authority. A controller may
use its result as an input to existing policy; executor and judge claims never
self-commit canonical CARR state.
"""
from __future__ import annotations

from typing import Any

import execution_contract as contract
from evaluation_rubrics import rubric_for


class EvalPortfolioError(contract.ContractError):
    """An evaluation portfolio is malformed, unbound, or too vague to act on."""


RUNGS = {"smoke", "regression", "hill_climb", "launch"}
OUTCOMES = {"passed", "failed", "blocked", "unknown"}
DIRECTIONS = {"improved", "equivalent", "regressed", "not_compared"}
PORTFOLIO_FIELDS = {"schema_version", "portfolio_id", "data_class", "generated_at", "workflow", "policy", "provenance", "migration", "binding", "case_set", "taxonomy", "cases", "results", "frontier_comparisons", "experiments", "behavior_findings", "notes"}
WORKFLOW_FIELDS = {"workflow_id", "rubric_id", "rubric_version", "rubric_digest", "case_set_digest"}
POLICY_FIELDS = {"policy_id", "policy_version", "policy_digest", "default_effect", "risk_requirements"}
RISK_REQUIREMENT_FIELDS = {"risk_class", "lifecycle", "required_rungs", "independent_review_required"}
PROVENANCE_FIELDS = {"source_class", "production_trace_review", "expires_at"}
MIGRATION_FIELDS = {"canonical_contract", "legacy_aliases"}
BINDING_FIELDS = {"work_request_id", "plan_revision_digest", "state_version", "canonical_record_digest", "accepted_resource_revisions", "projection_digest", "risk_class", "lifecycle"}
CASE_SET_FIELDS = {"case_set_id", "version", "refresh_state", "production_trace_review"}
TAXONOMY_FIELDS = {"taxonomy_version", "refresh_state", "production_trace_review", "failure_modes"}
FAILURE_MODE_FIELDS = {"failure_mode_id", "parent_id", "class_name", "status", "affected_stages", "affected_dimensions", "evidence_refs", "provenance", "refresh_state"}
CASE_FIELDS = {"case_id", "rung", "phase_id", "job_stages", "adapter_configuration", "evaluator_version", "evidence_refs", "refresh_state", "diagnosis_required"}
RESULT_FIELDS = {"result_id", "case_id", "rung", "attempt_id", "status", "confidence", "evidence_refs", "dimension_results", "stage_results", "telemetry", "failure_mode_ids"}
DIMENSION_FIELDS = {"dimension_id", "status", "direction_vs_baseline", "evidence_refs"}
STAGE_RESULT_FIELDS = {"stage_id", "status", "dimension_ids", "evidence_refs"}
TELEMETRY_FIELDS = {"latency_ms", "cost_usd", "usage", "recovery_count", "intervention_count", "reset_tax"}
COMPARISON_FIELDS = {"comparison_id", "baseline_result_id", "candidate_result_id", "required_dimensions", "dimension_tolerances", "evidence_required", "promotion_state"}
TOLERANCE_FIELDS = {"dimension_id", "required_status", "accepted_directions"}
EXPERIMENT_FIELDS = {"experiment_id", "primary_dimension", "baseline_result_id", "hypothesis", "non_regression_dimensions", "phase_id", "session_affinity", "switch_condition", "tool_context", "result"}
TOOL_CONTEXT_FIELDS = {"eligible_count", "exposed_count", "selected_count", "used_count", "provenance"}
BEHAVIOR_FINDING_FIELDS = {"finding_id", "root_cause_key", "failure_mode_id", "evidence_refs"}


def _exact(value: Any, fields: set[str], label: str) -> dict[str, Any]:
    try:
        return contract._expect_exact(value, fields, label)  # shared strict contract primitive
    except contract.ContractError as exc:
        raise EvalPortfolioError(str(exc)) from exc


def _id(value: Any, label: str) -> str:
    try:
        return contract._string(value, label, identifier=True)
    except contract.ContractError as exc:
        raise EvalPortfolioError(str(exc)) from exc


def _refs(value: Any, label: str) -> list[str]:
    try:
        return contract._list_of_strings(value, label)
    except contract.ContractError as exc:
        raise EvalPortfolioError(str(exc)) from exc


def _digest(value: Any, label: str) -> str:
    try:
        return contract._digest(value, label)
    except contract.ContractError as exc:
        raise EvalPortfolioError(str(exc)) from exc


def _outcome(value: Any, label: str) -> str:
    if value not in OUTCOMES:
        raise EvalPortfolioError(f"{label} must be passed, failed, blocked, or unknown")
    return value


def _validate_binding(value: Any) -> dict[str, Any]:
    row = _exact(value, BINDING_FIELDS, "eval portfolio binding")
    _id(row["work_request_id"], "eval portfolio work_request_id")
    for field in ("plan_revision_digest", "canonical_record_digest", "projection_digest"):
        _digest(row[field], f"eval portfolio {field}")
    if not isinstance(row["state_version"], int) or isinstance(row["state_version"], bool) or row["state_version"] < 1:
        raise EvalPortfolioError("eval portfolio state_version must be a positive integer")
    if row["risk_class"] not in {f"R{i}" for i in range(7)} or row["lifecycle"] not in {"rehearsal", "change", "experiment", "launch"}:
        raise EvalPortfolioError("eval portfolio risk/lifecycle binding is invalid")
    if not isinstance(row["accepted_resource_revisions"], list): raise EvalPortfolioError("eval portfolio accepted resource revisions must be a list")
    for resource in row["accepted_resource_revisions"]:
        checked = _exact(resource, {"resource_ref", "revision_ref", "digest"}, "eval accepted resource revision")
        _id(checked["resource_ref"], "eval accepted resource resource_ref"); _id(checked["revision_ref"], "eval accepted resource revision_ref"); _digest(checked["digest"], "eval accepted resource digest")
    return row


def _validate_adapter(value: Any) -> dict[str, Any]:
    try:
        return contract._validate_adapter(value)
    except contract.ContractError as exc:
        raise EvalPortfolioError(str(exc)) from exc


def validate_eval_portfolio(raw: Any, projection: Any | None = None) -> dict[str, Any]:
    """Fail closed on anonymous scores, vague failures, or dangling evidence.

    The record deliberately has no score/aggregate field. Launch is represented
    for portfolio parity but cannot claim online traffic in this offline v1.
    """
    value = _exact(raw, PORTFOLIO_FIELDS, "eval portfolio")
    if value["schema_version"] != "carr-evaluation-kernel.v1":
        raise EvalPortfolioError("unsupported shared evaluation kernel schema_version")
    _id(value["portfolio_id"], "eval portfolio portfolio_id")
    if value["data_class"] != "synthetic_only":
        raise EvalPortfolioError("eval portfolio v1 is synthetic_only")
    contract._timestamp(value["generated_at"], "eval portfolio generated_at")
    workflow = _exact(value["workflow"], WORKFLOW_FIELDS, "evaluation workflow rubric")
    for field in ("workflow_id", "rubric_id", "rubric_version"):_id(workflow[field], f"evaluation workflow {field}")
    for field in ("rubric_digest", "case_set_digest"): _digest(workflow[field], f"evaluation workflow {field}")
    rubric = rubric_for(workflow["workflow_id"])
    if rubric is None or rubric["rubric_id"] != workflow["rubric_id"]:
        raise EvalPortfolioError("evaluation workflow/rubric is not registered")
    policy = _exact(value["policy"], POLICY_FIELDS, "evaluation policy")
    for field in ("policy_id", "policy_version"): _id(policy[field], f"evaluation policy {field}")
    _digest(policy["policy_digest"], "evaluation policy digest")
    if policy["default_effect"] != "deny" or not isinstance(policy["risk_requirements"], list): raise EvalPortfolioError("evaluation policy must default deny")
    policy_rows = set()
    for raw_requirement in policy["risk_requirements"]:
        requirement = _exact(raw_requirement, RISK_REQUIREMENT_FIELDS, "evaluation risk requirement")
        key = (requirement["risk_class"], requirement["lifecycle"])
        if key in policy_rows or requirement["risk_class"] not in {f"R{i}" for i in range(7)} or requirement["lifecycle"] not in {"rehearsal", "change", "experiment", "launch"} or not set(requirement["required_rungs"]).issubset(RUNGS) or not requirement["required_rungs"] or not isinstance(requirement["independent_review_required"], bool): raise EvalPortfolioError("evaluation risk requirement is invalid")
        policy_rows.add(key)
    provenance = _exact(value["provenance"], PROVENANCE_FIELDS, "evaluation provenance")
    if provenance["source_class"] not in {"synthetic_only", "production_redacted"} or provenance["production_trace_review"] not in {"unavailable_no_production_traces", "redacted_production_review"}: raise EvalPortfolioError("evaluation provenance is invalid")
    contract._timestamp(provenance["expires_at"], "evaluation provenance expires_at")
    migration = _exact(value["migration"], MIGRATION_FIELDS, "evaluation migration")
    _id(migration["canonical_contract"], "evaluation migration canonical contract"); _refs(migration["legacy_aliases"], "evaluation migration aliases")
    binding = _validate_binding(value["binding"])
    if projection is not None:
        checked = contract.validate_observatory_projection(projection)
        expected = {"work_request_id": checked["work_request_id"], "plan_revision_digest": checked["source_state"]["plan_revision_digest"], "state_version": checked["source_state"]["state_version"], "canonical_record_digest": checked["source_state"]["canonical_record_digest"], "projection_digest": checked["projection_digest"]}
        if any(binding[field] != expected[field] for field in expected):
            raise EvalPortfolioError("eval portfolio must bind the exact current observatory projection")
    case_set = _exact(value["case_set"], CASE_SET_FIELDS, "eval case set")
    _id(case_set["case_set_id"], "eval case set id"); _id(case_set["version"], "eval case set version")
    if case_set["refresh_state"] not in {"current_synthetic", "refresh_due", "unknown"} or case_set["production_trace_review"] != "unavailable_no_production_traces":
        raise EvalPortfolioError("eval case set must preserve synthetic drift posture")
    taxonomy = _exact(value["taxonomy"], TAXONOMY_FIELDS, "eval taxonomy")
    _id(taxonomy["taxonomy_version"], "eval taxonomy version")
    if taxonomy["refresh_state"] not in {"current_synthetic", "refresh_due", "unknown"} or taxonomy["production_trace_review"] != "unavailable_no_production_traces":
        raise EvalPortfolioError("eval taxonomy must preserve unavailable production trace review")
    modes = set()
    if not isinstance(taxonomy["failure_modes"], list) or not taxonomy["failure_modes"]:
        raise EvalPortfolioError("eval taxonomy needs named failure modes")
    for raw_mode in taxonomy["failure_modes"]:
        mode = _exact(raw_mode, FAILURE_MODE_FIELDS, "failure mode")
        _id(mode["failure_mode_id"], "failure mode id")
        if mode["failure_mode_id"] in modes or mode["class_name"] in {"bad_answer", "failed", "generic_failure"}:
            raise EvalPortfolioError("failure mode must be unique and actionable, never generic")
        modes.add(mode["failure_mode_id"])
        if mode["parent_id"] is not None and mode["parent_id"] not in modes:
            raise EvalPortfolioError("failure mode parent must precede and exist")
        _id(mode["class_name"], "failure mode class_name"); _refs(mode["affected_stages"], "failure mode affected_stages")
        _refs(mode["affected_dimensions"], "failure mode affected_dimensions"); _refs(mode["evidence_refs"], "failure mode evidence_refs")
        if mode["status"] not in {"active", "unclassified", "retired"} or mode["provenance"] not in {"synthetic_fixture", "behavior_audit"} or mode["refresh_state"] not in {"current_synthetic", "refresh_due", "unknown"}:
            raise EvalPortfolioError("failure mode vocabulary is invalid")
    cases: dict[str, dict[str, Any]] = {}
    if not isinstance(value["cases"], list) or not value["cases"]:
        raise EvalPortfolioError("eval portfolio needs cases")
    for raw_case in value["cases"]:
        case = _exact(raw_case, CASE_FIELDS, "eval case")
        _id(case["case_id"], "eval case id")
        if case["case_id"] in cases or case["rung"] not in RUNGS:
            raise EvalPortfolioError("eval case id/rung is invalid")
        cases[case["case_id"]] = case
        _id(case["phase_id"], "eval case phase_id"); _refs(case["job_stages"], "eval case job_stages")
        if not set(case["job_stages"]).issubset(rubric["stages"]): raise EvalPortfolioError("eval case has unknown workflow rubric stage")
        _validate_adapter(case["adapter_configuration"]); _id(case["evaluator_version"], "eval case evaluator_version"); _refs(case["evidence_refs"], "eval case evidence_refs")
        if case["refresh_state"] not in {"current_synthetic", "refresh_due", "unknown"} or not isinstance(case["diagnosis_required"], bool): raise EvalPortfolioError("eval case refresh/diagnosis is invalid")
        if case["rung"] == "launch" and case_set["production_trace_review"] != "unavailable_no_production_traces": raise EvalPortfolioError("launch must remain offline representation")
    results: dict[str, dict[str, Any]] = {}
    for raw_result in value["results"]:
        result = _exact(raw_result, RESULT_FIELDS, "eval result")
        _id(result["result_id"], "eval result id")
        if result["result_id"] in results or result["case_id"] not in cases or result["rung"] != cases[result["case_id"]]["rung"]: raise EvalPortfolioError("eval result binding is invalid")
        results[result["result_id"]] = result; _id(result["attempt_id"], "eval result attempt_id"); _outcome(result["status"], "eval result status")
        if result["confidence"] not in {"high", "medium", "low", "unknown"}: raise EvalPortfolioError("eval result confidence is invalid")
        _refs(result["evidence_refs"], "eval result evidence_refs")
        dimensions = set()
        for raw_dimension in result["dimension_results"]:
            dimension = _exact(raw_dimension, DIMENSION_FIELDS, "eval dimension result"); _id(dimension["dimension_id"], "eval dimension id")
            if dimension["dimension_id"] in dimensions: raise EvalPortfolioError("eval dimensions cannot duplicate")
            dimensions.add(dimension["dimension_id"]); _outcome(dimension["status"], "eval dimension status")
            if dimension["direction_vs_baseline"] not in DIRECTIONS: raise EvalPortfolioError("eval dimension direction is invalid")
            _refs(dimension["evidence_refs"], "eval dimension evidence_refs")
        if not dimensions: raise EvalPortfolioError("eval result needs named dimensions, not an aggregate")
        stages = set()
        for raw_stage in result["stage_results"]:
            stage = _exact(raw_stage, STAGE_RESULT_FIELDS, "eval stage result"); _id(stage["stage_id"], "eval stage id")
            if stage["stage_id"] in stages or stage["stage_id"] not in cases[result["case_id"]]["job_stages"]: raise EvalPortfolioError("eval stage result is dangling")
            stages.add(stage["stage_id"]); _outcome(stage["status"], "eval stage status"); _refs(stage["dimension_ids"], "eval stage dimensions"); _refs(stage["evidence_refs"], "eval stage evidence_refs")
            if not set(stage["dimension_ids"]).issubset(dimensions): raise EvalPortfolioError("eval stage dimension is not named in result")
        if cases[result["case_id"]]["diagnosis_required"] and (len(stages) < 2 or not stages): raise EvalPortfolioError("final-only evaluation is not diagnosable")
        telemetry = _exact(result["telemetry"], TELEMETRY_FIELDS, "eval telemetry")
        for field in ("latency_ms", "cost_usd", "recovery_count", "intervention_count"): contract._finite_nonnegative(telemetry[field], f"eval telemetry {field}")
        _exact(telemetry["usage"], {"input_tokens", "output_tokens", "cached_input_tokens"}, "eval telemetry usage")
        _exact(telemetry["reset_tax"], {"context_reconstruction_ms", "duplicated_tool_calls", "repeated_failed_approach_count", "human_correction_count", "switch_overhead_ms"}, "eval telemetry reset_tax")
        _refs(result["failure_mode_ids"], "eval result failure modes")
        if not set(result["failure_mode_ids"]).issubset(modes): raise EvalPortfolioError("eval result names unknown failure mode")
    for raw_comparison in value["frontier_comparisons"]:
        comparison = _exact(raw_comparison, COMPARISON_FIELDS, "cost curve comparison"); _id(comparison["comparison_id"], "comparison id")
        if comparison["baseline_result_id"] not in results or comparison["candidate_result_id"] not in results: raise EvalPortfolioError("cost curve comparison has dangling result")
        _refs(comparison["required_dimensions"], "comparison required dimensions")
        tolerances = {}
        if not isinstance(comparison["dimension_tolerances"], list): raise EvalPortfolioError("cost curve dimension tolerances must be a list")
        for raw_tolerance in comparison["dimension_tolerances"]:
            tolerance = _exact(raw_tolerance, TOLERANCE_FIELDS, "cost curve dimension tolerance")
            _id(tolerance["dimension_id"], "cost curve tolerance dimension")
            if tolerance["dimension_id"] in tolerances or tolerance["required_status"] not in OUTCOMES or not isinstance(tolerance["accepted_directions"], list) or not set(tolerance["accepted_directions"]).issubset(DIRECTIONS):
                raise EvalPortfolioError("cost curve dimension tolerance is invalid")
            tolerances[tolerance["dimension_id"]] = tolerance
        if set(tolerances) != set(comparison["required_dimensions"]): raise EvalPortfolioError("cost curve needs explicit tolerance for every critical dimension")
        if not isinstance(comparison["evidence_required"], bool) or comparison["promotion_state"] not in {"eligible_for_human_review", "not_eligible", "blocked"}: raise EvalPortfolioError("cost curve comparison state is invalid")
    for raw_experiment in value["experiments"]:
        experiment = _exact(raw_experiment, EXPERIMENT_FIELDS, "hill climb experiment"); _id(experiment["experiment_id"], "experiment id")
        _id(experiment["primary_dimension"], "experiment primary dimension")
        if experiment["baseline_result_id"] not in results or not isinstance(experiment["hypothesis"], str) or not experiment["hypothesis"]: raise EvalPortfolioError("hill climb experiment binding is invalid")
        _refs(experiment["non_regression_dimensions"], "experiment non-regression dimensions"); _id(experiment["phase_id"], "experiment phase")
        if experiment["session_affinity"] not in {"required", "preferred", "none"} or experiment["switch_condition"] != "verified_checkpoint": raise EvalPortfolioError("experiment must preserve phase affinity and verified checkpoint switching")
        tool_context = _exact(experiment["tool_context"], TOOL_CONTEXT_FIELDS, "experiment tool context")
        for field in ("eligible_count", "exposed_count", "selected_count", "used_count"): contract._nonnegative_int(tool_context[field], f"experiment tool context {field}")
        _id(tool_context["provenance"], "experiment tool context provenance"); _outcome(experiment["result"], "experiment result")
    roots = set()
    for raw_finding in value["behavior_findings"]:
        finding = _exact(raw_finding, BEHAVIOR_FINDING_FIELDS, "eval behavior finding"); _id(finding["finding_id"], "eval behavior finding id"); _id(finding["root_cause_key"], "eval behavior root cause")
        if finding["root_cause_key"] in roots or finding["failure_mode_id"] not in modes: raise EvalPortfolioError("behavior findings must dedupe and map failure taxonomy")
        roots.add(finding["root_cause_key"]); _refs(finding["evidence_refs"], "eval behavior finding evidence")
    if not isinstance(value["notes"], str): raise EvalPortfolioError("eval portfolio notes must be a string")
    return value


def cost_curve_gate(portfolio: Any) -> list[dict[str, Any]]:
    """Evaluate named critical dimensions without calculating an aggregate score."""
    value = validate_eval_portfolio(portfolio)
    results = {row["result_id"]: row for row in value["results"]}
    output = []
    for comparison in value["frontier_comparisons"]:
        baseline, candidate = results[comparison["baseline_result_id"]], results[comparison["candidate_result_id"]]
        index = {row["dimension_id"]: row for row in candidate["dimension_results"]}
        blockers = []
        tolerance_by_dimension = {row["dimension_id"]: row for row in comparison["dimension_tolerances"]}
        for dimension_id in comparison["required_dimensions"]:
            row = index.get(dimension_id)
            tolerance = tolerance_by_dimension[dimension_id]
            if row is None or row["status"] != tolerance["required_status"] or row["direction_vs_baseline"] not in tolerance["accepted_directions"]: blockers.append(dimension_id)
            elif comparison["evidence_required"] and not row["evidence_refs"]: blockers.append(dimension_id)
        state = "eligible_for_human_review" if not blockers else ("blocked" if candidate["status"] in {"blocked", "unknown"} else "not_eligible")
        if comparison["promotion_state"] != state: raise EvalPortfolioError("declared cost curve promotion state disagrees with deterministic critical-dimension gate")
        output.append({"comparison_id": comparison["comparison_id"], "promotion_state": state, "blocked_dimensions": blockers,
                       "baseline": baseline["telemetry"], "candidate": candidate["telemetry"]})
    return output


def admission_decision(kernel: Any, *, as_of: str | None = None) -> dict[str, Any]:
    """Return a deterministic recommendation with evidence-backed reason codes.

    It never promotes canonical state.  `eligible_for_controller_review` means
    only that the shared evidence contract meets the selected workflow policy.
    """
    value = validate_eval_portfolio(kernel)
    requirement = next((row for row in value["policy"]["risk_requirements"]
                        if row["risk_class"] == value["binding"]["risk_class"] and row["lifecycle"] == value["binding"]["lifecycle"]), None)
    evidence = []
    reasons = []
    if as_of is not None:
        contract._timestamp(as_of, "evaluation admission as_of")
        if as_of > value["provenance"]["expires_at"]:
            reasons.append("evidence_stale_or_revalidation_required")
    if requirement is None:
        reasons.append("risk_lifecycle_unmapped_default_deny")
    results_by_case = {row["case_id"]: row for row in value["results"]}
    for rung in requirement["required_rungs"] if requirement else []:
        cases = [case for case in value["cases"] if case["rung"] == rung]
        if not cases:
            reasons.append(f"required_rung_missing:{rung}")
            continue
        for case in cases:
            result = results_by_case.get(case["case_id"])
            if result is None:
                reasons.append(f"required_case_not_run:{case['case_id']}")
            elif result["status"] != "passed":
                reasons.append(f"required_case_not_passed:{case['case_id']}:{result['status']}")
            else:
                evidence.extend(result["evidence_refs"])
    result_index = {row["result_id"]: row for row in value["results"]}
    for comparison in cost_curve_gate(value):
        candidate = result_index[next(row["candidate_result_id"] for row in value["frontier_comparisons"] if row["comparison_id"] == comparison["comparison_id"])]
        if requirement and candidate["rung"] not in requirement["required_rungs"]:
            continue
        if comparison["promotion_state"] != "eligible_for_human_review":
            reasons.append(f"cost_curve_not_eligible:{comparison['comparison_id']}:{comparison['promotion_state']}")
            for dimension in comparison["blocked_dimensions"]:
                reasons.append(f"critical_dimension_not_equivalent:{dimension}")
    if requirement and requirement["independent_review_required"]:
        reasons.append("independent_review_required")
    if value["provenance"]["source_class"] == "synthetic_only":
        reasons.append("synthetic_evidence_not_controller_promotion")
    return {
        "schema_version": "carr-evaluation-admission-decision.v1",
        "workflow_id": value["workflow"]["workflow_id"],
        "portfolio_id": value["portfolio_id"],
        "portfolio_digest": contract.canonical_digest(value),
        "decision": "eligible_for_controller_review" if not reasons else "not_admitted",
        "reason_codes": sorted(set(reasons)),
        "evidence_refs": sorted(set(evidence)),
        "controller_promotion": "not_performed",
    }


# Canonical shared names. The legacy aliases below keep the prior Job Passport
# call sites read-compatible while migrations are in progress.
validate_evaluation_kernel = validate_eval_portfolio
evaluate_admission = admission_decision
