#!/usr/bin/env python3
"""Offline regression checks for the lease-bound Engineering controller seam."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))

import engineering_dispatch_adapter as adapter  # noqa: E402
import engineering_passport as passport  # noqa: E402
import execution_contract as contract  # noqa: E402
import bridge  # noqa: E402

GATE_SPEC = importlib.util.spec_from_file_location(
    "engineering_rule_pack_gate", ROOT / "hooks" / "rule-pack-drift-gate.py")
assert GATE_SPEC and GATE_SPEC.loader
rule_pack_gate = importlib.util.module_from_spec(GATE_SPEC)
GATE_SPEC.loader.exec_module(rule_pack_gate)


FAILURES: list[str] = []


def check(label, fn):
    try:
        fn()
    except AssertionError as exc:
        FAILURES.append(f"{label}: {exc}")
        print(f"FAIL {label}: {exc}")
    except Exception as exc:  # noqa: BLE001
        FAILURES.append(f"{label}: unexpected {exc!r}")
        print(f"FAIL {label}: unexpected {exc!r}")
    else:
        print(f"ok {label}")


FIXTURES = ROOT / "control-room" / "contracts" / "fixtures" / "execution-fabric"
ENVELOPE = json.loads((FIXTURES / "codex_desktop.execution-envelope.v1.json").read_text())
PLAN = json.loads((FIXTURES / "engineering-passport.synthetic.plan.v1.json").read_text())


def evidence(ref: str) -> dict:
    return {"ref": ref, "redaction_class": "redacted_evidence",
            "content_digest": "sha256:" + "a" * 64}


def valid_receipt() -> dict:
    return {
        "schema_version": "engineering-slice-receipt.v1",
        "envelope_digest": contract.execution_envelope_digest(ENVELOPE),
        "attempt_id": "attempt:1", "slice_ref": "slice:a", "plan_digest": PLAN["plan_digest"],
        "attribution": {"actor_ref": ENVELOPE["server_binding"]["identity"]["agent_principal_id"],
                        "session_ref": ENVELOPE["agent_session"]["id"],
                        "adapter_ref": ENVELOPE["server_binding"]["adapter"]["adapter_id"]},
        "planned_resource_refs": ["resource:worktree-a"], "actual_resource_refs": ["resource:worktree-a"],
        "planned_component_refs": ["component:execution-fabric"], "actual_component_refs": ["component:execution-fabric"],
        "checks": [{"check_ref": "check:contracts", "state": "passed", "evidence_refs": [evidence("evidence:check")]}],
        "outcome": "claimed_complete", "artifact_refs": ["artifact:controller"], "evidence_refs": [evidence("evidence:receipt")],
        "deviations": [],
        "source_evidence": {"worktree_ref": "worktree:isolated", "branch_ref": "branch:controller", "source_sha": "abc1234", "evidence_refs": [evidence("evidence:source")]},
        "reset_reconstruction": {"fresh_session": True, "inherited_transcript_used": False, "reconstruction_free": True, "remediation_action": None},
        "executor_claim": {"claim_state": "executor_claim", "claimed_by": "codex", "claimed_at": "2026-08-25T18:00:00Z"},
        "independent_verification_required": True,
    }


def request() -> dict:
    first = PLAN["slices"][0]
    return {"desk": "engineering-codex", "envelope": copy.deepcopy(ENVELOPE), "executor_slug": "codex",
            "task": {"work_request": PLAN["work_request"]["id"], "slice_ref": "slice:a", "plan_digest": PLAN["plan_digest"],
                     "job_ref": ENVELOPE["request"]["job_ref"], "attempt_id": "attempt:1",
                     "generation": 1,
                     "engineering_plan": copy.deepcopy(PLAN),
                     "engineering_slice": copy.deepcopy(first)}}


class ValidEngineeringDesk:
    def resolve(self, name):
        assert name == "engineering-codex"
        return {"name": name, "kind": "codex-session", "model": "gpt-5.6-sol", "effort": "high",
                "cwd": str(ROOT), "sandbox": "workspace-write", "room_seat": None}


def test_bridge_auth_observations_are_allowed_but_malformed_metadata_refuses():
    for auth in (True, None):
        class AuthStampedDesk(ValidEngineeringDesk):
            def resolve(self, name):
                return {**super().resolve(name), "last_auth": auth,
                        "last_auth_at": "2026-08-25T18:00:00+00:00"}

        assert adapter._dedicated_codex_desk(AuthStampedDesk())["last_auth"] is auth

    for key, value in (("last_auth", "true"), ("last_auth_at", 1)):
        class MalformedAuthDesk(ValidEngineeringDesk):
            def resolve(self, name):
                return {**super().resolve(name), key: value}

        try:
            adapter._dedicated_codex_desk(MalformedAuthDesk())
        except adapter.DispatchRefusal:
            continue
        raise AssertionError(f"malformed {key} metadata was accepted")


def test_success_is_fresh_and_database_capability_is_not_forwarded():
    seen = {}

    def fake_dispatch(desk, prompt, **kwargs):
        seen.update({"desk": desk, "prompt": prompt, **kwargs})
        return {"status": "completed", "thread_id": "fresh-thread", "result": json.dumps(valid_receipt())}

    old = os.environ.get("CARR_DB_JOBS_URL")
    os.environ["CARR_DB_JOBS_URL"] = "never-forward-this"
    try:
        result = adapter.run(request(), dispatch_fn=fake_dispatch, registry=ValidEngineeringDesk())
    finally:
        if old is None:
            os.environ.pop("CARR_DB_JOBS_URL", None)
        else:
            os.environ["CARR_DB_JOBS_URL"] = old
    assert result["ok"] is True
    assert seen["desk"] == "engineering-codex" and seen["fresh"] is True
    assert "CARR_DB_JOBS_URL" not in seen["env"]
    assert "SERVER-ISSUED SLICE PACKET" in seen["prompt"]
    assert "RULE-DELIVERY WORKFLOW: engineering-slice" in seen["prompt"]
    assert ("RULE-DELIVERY PACKS: engineering-git,delegation-council,"
            "scheduled-automation,source-study") in seen["prompt"]
    assert "REFUSE before inspecting the envelope, source, or job" in seen["prompt"]
    prompt_record = {"type": "response_item", "payload": {"type": "message",
                     "role": "user", "content": [
                         {"type": "input_text", "text": seen["prompt"]}]}}
    assert rule_pack_gate.engineering_workflow_packs(prompt_record) == [
        "engineering-git", "delegation-council", "scheduled-automation", "source-study"]
    tampered_prompt = copy.deepcopy(prompt_record)
    tampered_prompt["payload"]["content"][0]["text"] = seen["prompt"].replace(
        '"packet_digest":"sha256:', '"packet_digest":"sha256:0', 1)
    assert rule_pack_gate.engineering_workflow_packs(tampered_prompt) == []
    assert rule_pack_gate.work_text(tampered_prompt)
    task_marker = "\n\nCONTROLLER TASK BINDING (immutable):\n"
    prompt_prefix, prompt_task = seen["prompt"].split(task_marker, 1)
    unbound_task = json.loads(prompt_task)
    unbound_task["work_request"] = "wr:unrelated-human-spoof"
    unbound_prompt = copy.deepcopy(prompt_record)
    unbound_prompt["payload"]["content"][0]["text"] = (
        prompt_prefix + task_marker
        + json.dumps(unbound_task, sort_keys=True, separators=(",", ":")))
    assert rule_pack_gate.engineering_workflow_packs(unbound_prompt) == []


def test_invalid_model_receipt_refuses_before_the_controller_can_persist_it():
    def fake_dispatch(*_args, **_kwargs):
        return {"status": "completed", "result": "not json"}
    try:
        adapter.run(request(), dispatch_fn=fake_dispatch, registry=ValidEngineeringDesk())
    except adapter.DispatchRefusal:
        return
    raise AssertionError("non-JSON result reached receipt persistence")


def test_unbound_task_work_request_refuses_before_dispatch():
    value = request()
    value["task"]["work_request"] = "wr:unrelated-human-spoof"
    dispatched = False
    def fake_dispatch(*_args, **_kwargs):
        nonlocal dispatched
        dispatched = True
        return {}
    try:
        adapter.run(value, dispatch_fn=fake_dispatch, registry=ValidEngineeringDesk())
    except adapter.DispatchRefusal:
        assert dispatched is False
        return
    raise AssertionError("unbound task work request reached dispatch")


def test_receipt_cannot_relabel_the_server_issued_native_session_or_adapter():
    bad = valid_receipt()
    bad["attribution"] = {**bad["attribution"], "session_ref": "session:caller-chosen"}

    def fake_dispatch(*_args, **_kwargs):
        return {"status": "completed", "result": json.dumps(bad)}
    try:
        adapter.run(request(), dispatch_fn=fake_dispatch, registry=ValidEngineeringDesk())
    except adapter.DispatchRefusal:
        return
    raise AssertionError("caller-chosen session attribution reached receipt persistence")


def test_desk_is_fixed_and_refuses_claude_or_unmodeled_registry_entries_before_dispatch():
    wrong = request()
    wrong["desk"] = "codex-desk"
    calls = 0

    def fake_dispatch(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return {"status": "completed", "result": json.dumps(valid_receipt())}

    try:
        adapter.run(wrong, dispatch_fn=fake_dispatch, registry=ValidEngineeringDesk())
    except adapter.DispatchRefusal:
        pass
    else:
        raise AssertionError("caller-controlled desk was accepted")

    class ClaudeDesk:
        def resolve(self, _name):
            return {"kind": "claude-session", "model": "ignored", "effort": "ignored"}

    try:
        adapter.run(request(), dispatch_fn=fake_dispatch, registry=ClaudeDesk())
    except adapter.DispatchRefusal:
        pass
    else:
        raise AssertionError("Claude desk received an Engineering packet")

    class WidenedDesk(ValidEngineeringDesk):
        def resolve(self, name):
            return {**super().resolve(name), "add_dirs": [str(Path.home() / ".config" / "carr")]}

    try:
        adapter.run(request(), dispatch_fn=fake_dispatch, registry=WidenedDesk())
    except adapter.DispatchRefusal:
        pass
    else:
        raise AssertionError("registry add_dirs widened the Engineering desk")
    assert calls == 0


def test_tracked_bootstrap_registers_one_unseated_exact_desk_and_wrapper_has_no_overrides():
    import tempfile
    with tempfile.TemporaryDirectory() as root:
        registry = adapter.desks.Registry(Path(root) / "hermes-desks.json")
        entry = adapter.install_dedicated_codex_desk(registry)
        assert entry["name"] == "engineering-codex"
        assert entry["kind"] == "codex-session" and entry["model"] == "gpt-5.6-sol"
        assert entry["effort"] == "high" and entry["sandbox"] == "workspace-write"
        assert entry.get("room_seat") is None and entry["thread_id"] is None
        assert adapter._dedicated_codex_desk(registry)["cwd"] == str(ROOT)
    wrapper = (ROOT / "bin" / "run-engineering-dispatch.sh").read_text()
    runner = (ROOT / "mcp-server" / "bin" / "run-engineering-dispatch.mjs").read_text()
    adapter_source = (HERE / "engineering_dispatch_adapter.py").read_text()
    assert "CARR_ENGINEERING_NODE" not in wrapper and "CARR_ENGINEERING_DESK" not in wrapper
    assert 'NODE="/opt/homebrew/opt/node@22/bin/node"' in wrapper
    assert wrapper.index('engineering_dispatch_adapter.py" --preflight') < wrapper.index("carr_load_routine_db_env")
    assert 'const DESK = "engineering-codex"' in runner
    assert runner.index("await preflightDedicatedDesk()") < runner.index("new Pool")
    assert "DEDICATED_REGISTRY_PATH" in adapter_source
    assert "desks.Registry(DEDICATED_REGISTRY_PATH)" in adapter_source


def test_source_keeps_controller_scoped_and_bridge_only_invokes_it_after_room_state_save():
    runtime = (ROOT / "mcp-server" / "src" / "engineering-runtime.js").read_text()
    runner = (ROOT / "mcp-server" / "bin" / "run-engineering-dispatch.mjs").read_text()
    bridge = (HERE / "bridge.py").read_text()
    migration = (ROOT / "migrations" / "0312_engineering_dispatch_controller.sql").read_text()
    assert '"select * from ops.engineering_claim_slice' in runtime
    assert "ops.claim_job(" not in runner and "ops.claim_job_mode(" not in runner
    assert "safeAdapterEnv" in runner and "CARR_DB_JOBS_URL" not in runner.split("function safeAdapterEnv", 1)[1].split("function runAdapter", 1)[0]
    assert bridge.index("state_mod.save_state") < bridge.index("engineering = {")
    assert 'os.environ.get("CARR_ENGINEERING_DISPATCH_ENABLED") == "true"' in bridge
    assert "engineering_controller_binding" in migration
    assert "p_executor_actor_id is distinct from session_executor" in migration


def test_bridge_controller_readback_is_typed_and_never_relays_child_stderr():
    class Completed:
        returncode = 0
        stdout = json.dumps({"ok": True, "claimed": 1, "completed": 1,
                             "results": [{"job_id": "job:opaque"}]})
        stderr = "model or credential text must not escape"

    original = bridge.subprocess.run
    bridge.subprocess.run = lambda *_args, **_kwargs: Completed()
    try:
        assert bridge.run_engineering_dispatch(command=Path("/fixed/controller")) == {
            "claimed": 1, "completed": 1, "results": [{"job_id": "job:opaque"}]}
    finally:
        bridge.subprocess.run = original


if __name__ == "__main__":
    for value in list(globals().values()):
        if callable(value) and getattr(value, "__name__", "").startswith("test_"):
            check(value.__name__, value)
    raise SystemExit(1 if FAILURES else 0)
