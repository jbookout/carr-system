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
        "capability_inherited": False,
    }
    replacement["server_binding"]["authority"]["capability_grant_ref"] = "grant-synthetic-replacement"
    contract.validate_replacement_envelope(previous, replacement)
    replacement["server_binding"]["authority"]["capability_grant_ref"] = \
        previous["server_binding"]["authority"]["capability_grant_ref"]
    expect_refusal(lambda: contract.validate_replacement_envelope(previous, replacement), "cannot inherit")


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
    assert "raw provider answer" not in json.dumps(actual)


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
        ("native transports produce comparable receipt shapes", native_transports_have_comparable_receipt_shapes),
        ("lifecycle and verification states stay distinct", terminal_and_verification_states_remain_distinct),
        ("declared versus observed remains uncertain", declared_vs_observed_is_uncertain_and_filesystem_alone_is_not_a_deviation),
        ("progress event is ephemeral and observational", progress_event_is_redacted_observational_and_can_stay_ephemeral),
        ("observatory projection preserves profile/staffing distinction", observatory_projection_groups_by_work_request_and_separates_profile_from_staffing),
        ("compatibility wrapper uses a fake without persisting raw result", compatibility_wrapper_uses_existing_dispatch_with_a_fake_and_redacts_result),
    ]:
        check(name, fn)
