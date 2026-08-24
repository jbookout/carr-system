#!/usr/bin/env python3
"""Offline acceptance tests for the provider-neutral execution fabric v1."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))

import execution_contract as contract  # noqa: E402
import dispatch  # noqa: E402
import job_passport_artifact as artifact_renderer  # noqa: E402


FIXTURES = ROOT / "control-room" / "contracts" / "fixtures" / "execution-fabric"


def check(name, fn):
    try:
        fn()
    except Exception as exc:  # noqa: BLE001 - standalone test runner reports every assertion
        raise AssertionError(f"FAIL {name}: {exc}") from exc
    print(f"ok  {name}")


def envelope(surface="codex_desktop"):
    raw = json.loads((FIXTURES / f"{surface}.execution-envelope.v1.json").read_text())
    contract.validate_execution_envelope(raw)
    return raw


def receipt(surface="codex_desktop"):
    raw = json.loads((FIXTURES / f"{surface}.attempt-receipt.v1.json").read_text())
    contract.validate_attempt_receipt(raw, envelope(surface))
    return raw


def expect_refusal(fn, contains):
    try:
        fn()
    except contract.ContractError as exc:
        assert contains in str(exc), exc
        return
    raise AssertionError("expected ContractError")


def schemas_are_versioned_and_fail_closed():
    for name in ("execution-envelope.v1.schema.json", "attempt-receipt.v1.schema.json"):
        schema = json.loads((ROOT / "control-room" / "contracts" / name).read_text())
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert schema["additionalProperties"] is False
        assert schema["properties"]["schema_version"]["const"].endswith(".v1")


def unknown_fields_fail_closed():
    value = envelope()
    value["model_selected_by_client"] = "nope"
    expect_refusal(lambda: contract.validate_execution_envelope(value), "unknown fields")
    value = receipt()
    value["raw_transcript"] = "never persisted"
    expect_refusal(lambda: contract.validate_attempt_receipt(value, envelope()), "unknown fields")


def client_identity_and_capability_selection_is_refused():
    safe = {"job_ref": "job:synthetic-read-only", "input_digest": "sha256:" + "1" * 64}
    assert contract.validate_client_job_request(safe) == {**safe, "data_class": "synthetic_only"}
    for field in (
        "organization_tenant_id", "sponsoring_human_id", "runtime_principal", "environment",
        "risk_class", "capability_profile", "personal_brain_scope", "provider_id", "model_id",
    ):
        request = {**safe, field: "client-chosen"}
        expect_refusal(lambda request=request: contract.validate_client_job_request(request), field)


def digest_is_canonical_and_deterministic():
    first = envelope()
    second = json.loads(json.dumps(first, sort_keys=True))
    assert contract.execution_envelope_digest(first) == contract.execution_envelope_digest(second)
    assert contract.execution_envelope_digest(first).startswith("sha256:")


def different_envelope_receipt_is_refused():
    first = envelope("codex_desktop")
    second = envelope("claude_desktop")
    value = receipt("codex_desktop")
    assert value["envelope_digest"] == contract.execution_envelope_digest(first)
    expect_refusal(lambda: contract.validate_attempt_receipt(value, second), "does not bind")


def replacement_cannot_inherit_capability():
    previous = envelope()
    replacement = copy.deepcopy(previous)
    replacement["envelope_id"] = "env-synthetic-replacement"
    replacement["agent_session"]["id"] = "session-synthetic-replacement"
    replacement["handoff"] = {
        "mode": "replacement", "replaces_agent_session_id": previous["agent_session"]["id"],
        "capability_inherited": False, "checkpoint_ref": "checkpoint:synthetic-verified",
        "native_session_transfer": "semantic_state_only",
    }
    replacement["server_binding"]["authority"]["capability_grant_ref"] = "grant-synthetic-replacement"
    contract.validate_replacement_envelope(previous, replacement)
    replacement["server_binding"]["authority"]["capability_grant_ref"] = \
        previous["server_binding"]["authority"]["capability_grant_ref"]
    expect_refusal(lambda: contract.validate_replacement_envelope(previous, replacement), "cannot inherit")


def state_binding_requires_compare_and_swap_and_handoff_checkpoint():
    value = envelope()
    assert value["state_binding"]["state_version"] == 1
    assert value["state_binding"]["compare_and_swap_required"] is True
    value["state_binding"]["compare_and_swap_required"] = False
    expect_refusal(lambda: contract.validate_execution_envelope(value), "compare_and_swap_required")
    previous = envelope()
    replacement = copy.deepcopy(previous)
    replacement["envelope_id"] = "env-synthetic-checkpoint-replacement"
    replacement["agent_session"]["id"] = "session-synthetic-checkpoint-replacement"
    replacement["server_binding"]["authority"]["capability_grant_ref"] = "grant-synthetic-checkpoint-replacement"
    replacement["handoff"] = {
        "mode": "replacement", "replaces_agent_session_id": previous["agent_session"]["id"],
        "capability_inherited": False, "checkpoint_ref": None, "native_session_transfer": "semantic_state_only",
    }
    expect_refusal(lambda: contract.validate_replacement_envelope(previous, replacement), "checkpoint_ref")


def receipts_are_claims_not_canonical_promotion_and_measure_reset_tax():
    value = receipt()
    assert value["attestation"]["canonical_promotion_state"] == "not_promoted"
    assert set(value["telemetry"]["reset_tax"]) == {
        "context_reconstruction_ms", "duplicated_tool_calls", "repeated_failed_approach_count",
        "human_correction_count", "switch_overhead_ms",
    }
    value["negative_knowledge"] = [{
        "approach_ref": "approach:synthetic-failed", "evidence_refs": ["evidence:synthetic-check"],
        "applicability": "scope:synthetic-read", "revalidate_after": "2026-08-25T12:00:00Z",
        "expires_at": "2026-09-01T12:00:00Z",
    }]
    contract.validate_attempt_receipt(value, envelope())
    value["attestation"]["canonical_promotion_state"] = "promoted"
    expect_refusal(lambda: contract.validate_attempt_receipt(value, envelope()), "not canonical verified state")


def phase_binding_preserves_native_sessions_and_reserves_evaluation_arms():
    value = envelope()
    assert value["phase_binding"]["native_session_transfer"] == "semantic_state_only"
    assert value["evaluation_context"]["experiment_arm"] == "same_pair_audited_state"
    value["phase_binding"]["switch_conditions"] = ["phase_boundary"]
    expect_refusal(lambda: contract.validate_execution_envelope(value), "verified_checkpoint")


def self_contained_visual_artifact_is_bound_to_projection_and_staleness():
    bound = envelope()
    value = receipt()
    value["visual_artifacts"] = [{
        "artifact_ref": "artifact:observatory-map", "media_type": "text/html", "self_contained": True,
        "external_service_dependency": False, "visual_form": "topology",
        "source_binding": {
            "work_request_id": bound["work_request_id"], "plan_revision_digest": bound["plan_revision"]["digest"],
            "state_version": bound["state_binding"]["state_version"],
            "canonical_record_digest": bound["state_binding"]["canonical_record_digest"],
            "projection_schema_version": "observatory-attempt-projection.v1",
            "projection_digest": "sha256:" + "e" * 64,
        },
        "generation": {
            "generating_attempt_id": value["attempt_id"],
            "adapter_configuration_fingerprint": bound["server_binding"]["adapter"]["configuration_fingerprint"],
            "skill_id": "skill:html-diagram", "skill_version": "candidate-v1",
        },
        "generated_at": "2026-08-24T12:00:05Z", "freshness": {"state": "stale", "valid_through": "2026-08-24T12:00:05Z"},
        "redaction_class": "metadata_only", "content_digest": "sha256:" + "f" * 64,
        "evidence_refs": ["evidence:synthetic-check"],
        "accessibility": {"color_independent_meaning": True, "reduced_motion_supported": True, "responsive_verified": True, "keyboard_accessible": True},
    }]
    contract.validate_attempt_receipt(value, bound)
    assert value["visual_artifacts"][0]["freshness"]["state"] == "stale"
    value["visual_artifacts"][0]["external_service_dependency"] = True
    expect_refusal(lambda: contract.validate_attempt_receipt(value, bound), "no external service dependency")


def native_transports_have_comparable_receipt_shapes():
    rows = [receipt(surface) for surface in (
        "claude_desktop", "codex_desktop", "hermes_desktop", "grok_x_native",
    )]
    assert len({tuple(sorted(row)) for row in rows}) == 1
    assert len({tuple(sorted(row["adapter"])) for row in rows}) == 1
    assert all(row["result"]["job_ref"] == "job:synthetic-read-only" for row in rows)


def terminal_and_verification_states_remain_distinct():
    states = {
        "success": ("succeeded", "verified_success"),
        "failure": ("failed", "verified_failure"),
        "timeout": ("timed_out", "unknown"),
        "cancellation": ("cancelled", "not_attempted"),
        "partial": ("partial", "partial"),
        "unknown": ("unknown", "unknown"),
    }
    base = receipt()
    seen = set()
    for label, expected in states.items():
        value = copy.deepcopy(base)
        value["attempt_id"] = f"attempt-{label}"
        value["lifecycle"]["state"] = expected[0]
        value["result"]["outcome"] = label
        value["result"]["verification_state"] = expected[1]
        contract.validate_attempt_receipt(value, envelope())
        seen.add((value["lifecycle"]["state"], value["result"]["verification_state"]))
    assert len(seen) == len(states)


def declared_vs_observed_is_uncertain_and_filesystem_alone_is_not_a_deviation():
    value = receipt()
    value["observation"] = {
        "progress_state": "quiet", "coverage_state": "partial", "activity_fidelity": "filesystem",
        "declared_refs_observed": ["step:synthetic-read"], "unmapped_activity_refs": ["resource:worktree-b"],
        "deviation_candidates": [{
            "candidate_id": "candidate-synthetic-1", "basis": ["filesystem_change"],
            "confidence": "low", "requires_review": True,
        }], "uncertainty": "filesystem_only",
    }
    expect_refusal(lambda: contract.validate_attempt_receipt(value, envelope()), "filesystem movement alone")
    value["observation"]["deviation_candidates"][0]["basis"] = ["filesystem_change", "declared_binding_mismatch"]
    contract.validate_attempt_receipt(value, envelope())


def progress_event_is_redacted_observational_and_can_stay_ephemeral():
    event = {
        "schema_version": "progress-event.v1", "attempt_id": "attempt-progress-1", "sequence": 1,
        "occurred_at": "2026-08-24T12:00:01Z", "event_type": "observed_filesystem",
        "declared_step_ref": "step-synthetic-read", "observed_resource_ref": "resource-worktree-b",
        "observed_component_ref": None, "tool_class": "filesystem_watcher", "state": "quiet",
        "correlation_id": "corr-synthetic-1", "causation_id": "cause-dispatch-1",
        "redaction_class": "metadata_only", "evidence_refs": ["evidence:fs-summary-1"], "retention": "ephemeral",
    }
    contract.validate_progress_event(event)


def observatory_projection_groups_by_work_request_and_separates_profile_from_staffing():
    event = {
        "schema_version": "progress-event.v1", "attempt_id": "attempt-synthetic-codex", "sequence": 1,
        "occurred_at": "2026-08-24T12:00:01Z", "event_type": "observed_tool",
        "declared_step_ref": "step:synthetic-read", "observed_resource_ref": "resource:worktree-a",
        "observed_component_ref": "component:execution-fabric", "tool_class": "tool:codex-event",
        "state": "active", "correlation_id": "corr:synthetic-1", "causation_id": "cause:dispatch-1",
        "redaction_class": "metadata_only", "evidence_refs": ["evidence:synthetic-check"], "retention": "ephemeral",
    }
    actual = contract.project_observatory_attempt(
        envelope(), receipt(), [event], {"profile_id": "profile:doc", "display_label": "Doc"},
    )
    expected = json.loads((FIXTURES / "codex_desktop.observatory-projection.v1.json").read_text())
    assert actual == expected
    assert actual["work_request_id"] == "wr-synthetic-read-only"
    assert actual["attempt_lane"]["persistent_profile"]["display_label"] == "Doc"
    assert actual["attempt_lane"]["actual_staffing"]["model_id"] == "model:codex-synthetic"
    assert actual["projection_digest"] == contract.canonical_digest({key: value for key, value in actual.items() if key != "projection_digest"})
    assert actual["source_state"]["state_version"] == 1
    assert "raw provider answer" not in json.dumps(actual)


def wire_receipts_validate_projection_and_keep_typed_facts_distinct():
    projection = json.loads((FIXTURES / "codex_desktop.observatory-projection.v1.json").read_text())
    wire = contract.job_passport_wire_receipt("observatory_projection", projection)
    assert wire["job_passport"]["schema_version"] == "job-passport-wire.v1"
    assert wire["job_passport"]["payload"]["projection_digest"] == projection["projection_digest"]
    projection["source_state"]["state_version"] = 2
    expect_refusal(lambda: contract.validate_observatory_projection(projection), "does not bind")


def self_contained_job_passport_artifact_binds_content_and_is_stale_visible():
    projection = json.loads((FIXTURES / "codex_desktop.observatory-projection.v1.json").read_text())
    behavior = json.loads((FIXTURES / "codex_desktop.job-passport.behavior-verification.v1.json").read_text())
    contract.validate_product_behavior_verification(behavior)
    document, artifact = artifact_renderer.build_visual_artifact(envelope(), receipt(), projection, behavior)
    assert artifact_renderer.verify_visual_artifact(document, artifact)
    assert artifact["content_digest"] == contract.canonical_digest(document)
    assert "https://" not in document and "fetch(" not in document
    assert "prefers-reduced-motion" in document and "<details>" in document and "Behavior audit" in document
    stale = copy.deepcopy(projection)
    stale["state"]["progress"] = "stale"
    stale["observed_movement"]["progress_state"] = "stale"
    stale["projection_digest"] = contract.canonical_digest({key: value for key, value in stale.items() if key != "projection_digest"})
    _, stale_artifact = artifact_renderer.build_visual_artifact(envelope(), receipt(), stale)
    assert stale_artifact["freshness"]["state"] == "stale"
    assert artifact_renderer.verify_visual_artifact(document + "tampered", artifact) is False


def behavior_audit_fails_closed_on_dangling_claim_or_fake_live_verification():
    behavior = json.loads((FIXTURES / "codex_desktop.job-passport.behavior-verification.v1.json").read_text())
    behavior["items"][0]["claim_id"] = "claim:missing"
    expect_refusal(lambda: contract.validate_product_behavior_verification(behavior), "dangling")
    behavior = json.loads((FIXTURES / "codex_desktop.job-passport.behavior-verification.v1.json").read_text())
    behavior["items"][0]["status"] = "passed"
    expect_refusal(lambda: contract.validate_product_behavior_verification(behavior), "live browser evidence")


def compatibility_wrapper_uses_existing_dispatch_with_a_fake_and_redacts_result():
    calls = []

    def fake_dispatch(name, task, **kwargs):
        calls.append((name, task, kwargs))
        return {
            "msg_id": "dispatch-message-1", "desk": name, "kind": "codex-session",
            "dispatched_at": "2026-08-24T12:00:00Z", "status": "completed",
            "result": "a raw provider answer must not land in the receipt",
            "thread_id": "native-thread-1",
        }

    out = dispatch.dispatch_envelope(
        "codex-desk", envelope(), "perform synthetic read only work", dispatch_fn=fake_dispatch,
    )
    assert calls and calls[0][0] == "codex-desk"
    assert out["dispatch"]["result"].startswith("a raw provider")
    serialized = json.dumps(out["attempt_receipt"], sort_keys=True)
    assert "raw provider answer" not in serialized
    contract.validate_attempt_receipt(out["attempt_receipt"], envelope())


if __name__ == "__main__":
    for name, fn in [
        ("schemas are versioned and fail closed", schemas_are_versioned_and_fail_closed),
        ("unknown fields fail closed", unknown_fields_fail_closed),
        ("client identity/capability selection is refused", client_identity_and_capability_selection_is_refused),
        ("canonical digest is deterministic", digest_is_canonical_and_deterministic),
        ("receipt for another envelope is refused", different_envelope_receipt_is_refused),
        ("handoff replacement cannot inherit capability", replacement_cannot_inherit_capability),
        ("state binding requires CAS and verified handoff checkpoint", state_binding_requires_compare_and_swap_and_handoff_checkpoint),
        ("receipts are claims and carry reset-tax metrics", receipts_are_claims_not_canonical_promotion_and_measure_reset_tax),
        ("phase binding preserves native sessions", phase_binding_preserves_native_sessions_and_reserves_evaluation_arms),
        ("visual artifacts bind exact source state and remain stale-visible", self_contained_visual_artifact_is_bound_to_projection_and_staleness),
        ("native transports produce comparable receipt shapes", native_transports_have_comparable_receipt_shapes),
        ("lifecycle and verification states stay distinct", terminal_and_verification_states_remain_distinct),
        ("declared versus observed remains uncertain", declared_vs_observed_is_uncertain_and_filesystem_alone_is_not_a_deviation),
        ("progress event is ephemeral and observational", progress_event_is_redacted_observational_and_can_stay_ephemeral),
        ("observatory projection preserves profile/staffing distinction", observatory_projection_groups_by_work_request_and_separates_profile_from_staffing),
        ("room wire accepts only validated typed Job Passport facts", wire_receipts_validate_projection_and_keep_typed_facts_distinct),
        ("self-contained Job Passport artifact has verified content and stale posture", self_contained_job_passport_artifact_binds_content_and_is_stale_visible),
        ("behavior audit fails closed on dangling or non-live verification", behavior_audit_fails_closed_on_dangling_claim_or_fake_live_verification),
        ("compatibility wrapper uses a fake without persisting raw result", compatibility_wrapper_uses_existing_dispatch_with_a_fake_and_redacts_result),
    ]:
        check(name, fn)
