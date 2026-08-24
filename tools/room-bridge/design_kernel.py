"""Machine-readable CARR Design Kernel and browser-evidence visual gate contract.

The kernel supplies narrowly selected context to an adapter.  It does not
render a surface, select a provider, write canonical state, or promote work.
Visual gate reports are evidence inputs to the shared Evaluation Kernel; a
failed critical gate is never averaged away by an aesthetic or cost opinion.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any


class DesignKernelError(ValueError):
    """Raised when a design contract or visual-gate report is unsafe to use."""


PRIORITY = ["user_task", "authority_and_truth", "accessibility", "consistency", "aesthetics", "developer_convenience"]
REQUIRED_STATES = ["default", "hover", "active", "focus-visible", "disabled", "loading", "error", "empty"]
VIEWPORT_GATES = {"narrow_280": 280, "narrow_320": 320, "narrow_414": 414}
TOP_FIELDS = {"schema_version", "contract_id", "version", "status", "priority_hierarchy", "token_architecture", "context_slices", "design_intents", "component_state_contract", "visual_gate_portfolio", "aesthetic_critique", "adapter_projection", "provenance"}


def canonical_digest(value: Any) -> str:
    """Stable digest used by adapters and reports without a second source."""
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _exact(value: Any, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        actual = sorted(value) if isinstance(value, dict) else type(value).__name__
        raise DesignKernelError(f"{label} fields must be exactly {sorted(fields)}, got {actual}")
    return value


def _strings(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or not value or any(not isinstance(item, str) or not item for item in value):
        raise DesignKernelError(f"{label} must be a non-empty list of strings")
    return value


def validate_design_kernel(raw: Any) -> dict[str, Any]:
    value = _exact(raw, TOP_FIELDS, "design kernel")
    if value["schema_version"] != "carr-design-kernel.v1" or value["status"] != "active_contract_not_a_runtime_or_authority":
        raise DesignKernelError("unsupported design kernel version or authority posture")
    if not isinstance(value["contract_id"], str) or not value["contract_id"] or not isinstance(value["version"], str) or not value["version"]:
        raise DesignKernelError("design kernel identity is required")
    if value["priority_hierarchy"] != PRIORITY:
        raise DesignKernelError("design hierarchy must preserve user, truth, accessibility, consistency, aesthetics, developer order")

    token = _exact(value["token_architecture"], {"canonical_stylesheet", "primitive_layer", "semantic_layer", "component_layer", "theme_policy", "legacy_canon"}, "token architecture")
    if token["canonical_stylesheet"] != "design/tokens.css" or token["theme_policy"] != "primitives_stable_semantic_aliases_swap":
        raise DesignKernelError("token contract must preserve CARR stylesheet canon and stable primitives")
    for field in ("primitive_layer", "semantic_layer", "component_layer", "legacy_canon"):
        _strings(token[field], f"token architecture {field}")

    slices: dict[str, dict[str, Any]] = {}
    if not isinstance(value["context_slices"], list) or not value["context_slices"]:
        raise DesignKernelError("context slices must be a non-empty list")
    for raw_slice in value["context_slices"]:
        row = _exact(raw_slice, {"slice_id", "purpose", "required_for"}, "design context slice")
        if not isinstance(row["slice_id"], str) or not row["slice_id"] or row["slice_id"] in slices or not isinstance(row["purpose"], str) or not row["purpose"]:
            raise DesignKernelError("context slice identity/purpose is invalid")
        _strings(row["required_for"], "context slice required_for")
        slices[row["slice_id"]] = row
    if "slice:core" not in slices:
        raise DesignKernelError("all adapters require the core context slice")

    profiles: dict[str, set[str]] = {}
    gates = _exact(value["visual_gate_portfolio"], {"profiles", "critical_gate_ids", "evidence_policy", "aggregation_policy"}, "visual gate portfolio")
    for profile in gates["profiles"]:
        row = _exact(profile, {"profile_id", "required_gates"}, "visual gate profile")
        gate_ids = _strings(row["required_gates"], "visual gate profile required_gates")
        if not isinstance(row["profile_id"], str) or not row["profile_id"] or row["profile_id"] in profiles:
            raise DesignKernelError("visual gate profile identity is invalid")
        profiles[row["profile_id"]] = set(gate_ids)
    critical = set(_strings(gates["critical_gate_ids"], "critical gate ids"))
    if not critical or not critical.issubset(set().union(*profiles.values())):
        raise DesignKernelError("critical visual gates must belong to a declared profile")
    if gates["evidence_policy"] != "render_and_measure_in_a_real_browser_or_record_not_verified" or gates["aggregation_policy"] != "no_blended_score_critical_failure_blocks_admission":
        raise DesignKernelError("visual gates must require browser evidence and refuse blended scoring")

    intents: set[str] = set()
    for raw_intent in value["design_intents"]:
        row = _exact(raw_intent, {"intent_id", "surface_class", "required_slices", "optional_slices", "evaluation_profile"}, "design intent")
        if not isinstance(row["intent_id"], str) or not row["intent_id"] or row["intent_id"] in intents or not isinstance(row["surface_class"], str) or not row["surface_class"]:
            raise DesignKernelError("design intent identity is invalid")
        intents.add(row["intent_id"])
        required = _strings(row["required_slices"], "design intent required_slices")
        optional = row["optional_slices"]
        if not isinstance(optional, list) or any(not isinstance(item, str) for item in optional):
            raise DesignKernelError("design intent optional_slices must be a string list")
        if "slice:core" not in required or not set(required + optional).issubset(slices) or set(required) & set(optional):
            raise DesignKernelError("design intent must select core and known non-overlapping slices")
        if row["evaluation_profile"] not in profiles:
            raise DesignKernelError("design intent references an unknown visual gate profile")

    states = _exact(value["component_state_contract"], {"required_states", "conditional_states", "applicability", "failure_posture"}, "component state contract")
    if states["required_states"] != REQUIRED_STATES or states["conditional_states"] != ["success"]:
        raise DesignKernelError("component state contract is incomplete or reordered")
    if states["applicability"] != "state_must_be_authored_or_explicitly_not_applicable" or states["failure_posture"] != "partial_stale_offline_and_refusal_are_visible_not_silent":
        raise DesignKernelError("component state contract must keep explicit applicability and visible failures")

    critique = _exact(value["aesthetic_critique"], {"requires_evidence_refs", "may_override_correctness", "may_grant_promotion", "role"}, "aesthetic critique")
    if critique != {"requires_evidence_refs": True, "may_override_correctness": False, "may_grant_promotion": False, "role": "advisory_human_review_input"}:
        raise DesignKernelError("aesthetic critique must remain evidence-bound advisory input")
    adapter = _exact(value["adapter_projection"], {"allowed_surface_families", "projection_rule", "required_provenance"}, "adapter projection")
    _strings(adapter["allowed_surface_families"], "adapter allowed surface families")
    if adapter["projection_rule"] != "adapters_project_semantic_and_component_tokens_without_redefining_canonical_truth":
        raise DesignKernelError("adapters may not redefine CARR canonical truth")
    if set(_strings(adapter["required_provenance"], "adapter required provenance")) != {"contract_id", "version", "content_digest", "adapter_id"}:
        raise DesignKernelError("adapter provenance is incomplete")
    provenance = _exact(value["provenance"], {"derived_from", "external_inspiration", "data_class"}, "design kernel provenance")
    _strings(provenance["derived_from"], "design kernel derived_from")
    if provenance["data_class"] != "metadata_only":
        raise DesignKernelError("design contract must remain metadata only")
    return value


def design_context(kernel: Any, intent_id: str, include_optional: bool = False) -> dict[str, Any]:
    """Return only the contract slices needed for a specific visual job."""
    value = validate_design_kernel(kernel)
    intent = next((row for row in value["design_intents"] if row["intent_id"] == intent_id), None)
    if intent is None:
        raise DesignKernelError("unknown design intent")
    selected = list(intent["required_slices"])
    if include_optional:
        selected.extend(intent["optional_slices"])
    slice_map = {row["slice_id"]: row for row in value["context_slices"]}
    return {
        "schema_version": "carr-design-context.v1",
        "contract_id": value["contract_id"],
        "contract_version": value["version"],
        "contract_digest": canonical_digest(value),
        "intent_id": intent_id,
        "surface_class": intent["surface_class"],
        "evaluation_profile": intent["evaluation_profile"],
        "priority_hierarchy": value["priority_hierarchy"],
        "token_architecture": value["token_architecture"],
        "component_state_contract": value["component_state_contract"],
        "context_slices": [slice_map[item] for item in selected],
    }


REPORT_FIELDS = {"schema_version", "report_id", "kernel_binding", "target", "evidence", "gate_results", "aesthetic_critique", "admission"}


def validate_visual_gate_report(raw: Any, kernel: Any, *, expected_work_request_id: str | None = None, expected_projection_digest: str | None = None) -> dict[str, Any]:
    """Validate a real-browser visual evaluation receipt, failing closed on claims.

    A failed or unverified critical gate yields not_admitted.  The report never
    asserts promotion; it is consumable by the shared Evaluation Kernel only.
    """
    value = _exact(raw, REPORT_FIELDS, "visual gate report")
    source = validate_design_kernel(kernel)
    binding = _exact(value["kernel_binding"], {"contract_id", "version", "content_digest", "adapter_id"}, "visual gate report binding")
    if binding["contract_id"] != source["contract_id"] or binding["version"] != source["version"] or binding["content_digest"] != canonical_digest(source) or not isinstance(binding["adapter_id"], str) or not binding["adapter_id"]:
        raise DesignKernelError("visual gate report must bind exact design kernel and adapter")
    target = _exact(value["target"], {"intent_id", "surface_family", "work_request_id", "projection_digest"}, "visual gate report target")
    intent = next((row for row in source["design_intents"] if row["intent_id"] == target["intent_id"]), None)
    if intent is None or target["surface_family"] not in source["adapter_projection"]["allowed_surface_families"]:
        raise DesignKernelError("visual gate report target is not covered by the design contract")
    if expected_work_request_id is not None and target["work_request_id"] != expected_work_request_id:
        raise DesignKernelError("visual gate report does not bind evaluation work request")
    if expected_projection_digest is not None and target["projection_digest"] != expected_projection_digest:
        raise DesignKernelError("visual gate report does not bind evaluation projection")
    evidence = _exact(value["evidence"], {"runner", "browser", "captured_at", "refs"}, "visual gate evidence")
    if evidence["runner"] not in {"real_browser_measurement", "browser_unavailable"} or not isinstance(evidence["browser"], str) or not evidence["browser"] or not isinstance(evidence["captured_at"], str) or not evidence["captured_at"]:
        raise DesignKernelError("visual gates require named browser measurement or explicit browser-unavailable evidence")
    _strings(evidence["refs"], "visual gate evidence refs")
    required = next(row["required_gates"] for row in source["visual_gate_portfolio"]["profiles"] if row["profile_id"] == intent["evaluation_profile"])
    seen: dict[str, dict[str, Any]] = {}
    for result in value["gate_results"]:
        row = _exact(result, {"gate_id", "status", "evidence_refs", "measurement"}, "visual gate result")
        if row["gate_id"] in seen or row["gate_id"] not in required or row["status"] not in {"passed", "failed", "not_verified", "not_applicable"}:
            raise DesignKernelError("visual gate result is duplicate, unknown, or invalid")
        _strings(row["evidence_refs"], "visual gate result evidence refs")
        if not isinstance(row["measurement"], dict) or not row["measurement"]:
            raise DesignKernelError("visual gate result must carry a browser measurement")
        if row["gate_id"] in VIEWPORT_GATES and row["status"] in {"passed", "failed"} and row["measurement"].get("viewport_width_px") != VIEWPORT_GATES[row["gate_id"]]:
            raise DesignKernelError("narrow-width gate must record exact viewport width")
        if row["gate_id"] == "target_size" and row["status"] == "passed" and row["measurement"].get("minimum_target_px", 0) < 44:
            raise DesignKernelError("target-size pass must measure CARR 44px minimum")
        if row["gate_id"] == "target_size" and row["status"] == "failed" and not isinstance(row["measurement"].get("minimum_target_px"), (int, float)):
            raise DesignKernelError("target-size failure must retain its observed measurement")
        seen[row["gate_id"]] = row
    if set(seen) != set(required):
        raise DesignKernelError("visual gate report must cover every required gate")
    if evidence["runner"] == "browser_unavailable" and any(row["status"] not in {"not_verified", "not_applicable"} for row in seen.values()):
        raise DesignKernelError("browser-unavailable reports may not fabricate passed or failed measurements")
    critique = _exact(value["aesthetic_critique"], {"verdict", "evidence_refs", "authority"}, "aesthetic critique report")
    if critique["verdict"] not in {"not_run", "advisory_pass", "advisory_concerns"} or critique["authority"] != "advisory_never_promotion":
        raise DesignKernelError("aesthetic critique must remain advisory")
    _strings(critique["evidence_refs"], "aesthetic critique evidence refs")
    critical = set(source["visual_gate_portfolio"]["critical_gate_ids"])
    blockers = sorted(gate_id for gate_id, row in seen.items() if gate_id in critical and row["status"] != "passed")
    admission = _exact(value["admission"], {"aggregate_score", "state", "critical_blockers", "promotion"}, "visual gate admission")
    if admission["aggregate_score"] is not None or admission["promotion"] != "not_performed":
        raise DesignKernelError("visual gate admission has no aggregate score or promotion authority")
    expected_state = "not_admitted" if blockers else "eligible_for_controller_review"
    if admission["state"] != expected_state or admission["critical_blockers"] != blockers:
        raise DesignKernelError("visual gate admission must be derived from critical gate results")
    return value


def evaluation_blockers(report: Any, kernel: Any, *, expected_work_request_id: str, expected_projection_digest: str) -> list[str]:
    """Return exact critical visual blockers for shared Evaluation Kernel input."""
    checked = validate_visual_gate_report(report, kernel, expected_work_request_id=expected_work_request_id, expected_projection_digest=expected_projection_digest)
    return [f"visual_gate_critical_failure:{gate_id}" for gate_id in checked["admission"]["critical_blockers"]]
