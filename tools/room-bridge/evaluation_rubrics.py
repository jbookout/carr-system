"""Code-owned extension registry for workflow-specific evaluation rubrics.

The kernel standardizes evidence and policy semantics; this registry deliberately
does not impose one rubric on every governed workflow. Adding a workflow needs
an explicit immutable rubric/case-set binding, not an ad-hoc scorer.
"""

WORKFLOW_RUBRICS = {
    "workflow:job-passport": {
        "rubric_id": "rubric:job-passport-visual",
        "stages": {"typed_receipt_ingestion", "freshness_cas_selection", "projection", "visual_interaction_accessibility", "evidence_promotion_display"},
        "critical_dimensions": {"receipt_integrity", "freshness_integrity", "visual_accessibility", "visual_comprehension", "telemetry_truth", "layout_authority_separation"},
    },
    "workflow:claude-desktop-readonly": {
        "rubric_id": "rubric:claude-desktop-readonly",
        "stages": {"typed_receipt_ingestion", "projection"},
        "critical_dimensions": {"native_hook_attribution", "receipt_integrity"},
    },
    "workflow:codex-desktop-readonly": {
        "rubric_id": "rubric:codex-desktop-readonly",
        "stages": {"typed_receipt_ingestion", "freshness_cas_selection", "projection"},
        "critical_dimensions": {"adapter_configuration_binding", "freshness_integrity"},
    },
    "workflow:hermes-orchestration": {
        "rubric_id": "rubric:hermes-orchestration",
        "stages": {"typed_receipt_ingestion", "projection"},
        "critical_dimensions": {"profile_staffing_separation", "handoff_checkpoint"},
    },
    "workflow:grok-x-native-retrieval": {
        "rubric_id": "rubric:grok-x-native-retrieval",
        "stages": {"typed_receipt_ingestion", "projection"},
        "critical_dimensions": {"x_native_provenance", "retrieval_evidence_binding"},
    },
}


def rubric_for(workflow_id: str) -> dict | None:
    """Return immutable rubric metadata or None; callers default deny unknowns."""
    return WORKFLOW_RUBRICS.get(workflow_id)
