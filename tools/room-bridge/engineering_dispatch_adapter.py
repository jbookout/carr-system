#!/usr/bin/env python3
"""The room-bridge side of one server-issued Engineering Passport dispatch.

This process receives no database credential.  The controller has already
claimed the immutable job and passed only the server-issued envelope plus the
accepted slice.  It launches a fresh *dedicated* Codex desk through the same
Hermes dispatch wire ordinary local work uses, validates the typed receipt
before it returns it, and never writes the record layer itself.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))

import dispatch  # noqa: E402
import desks  # noqa: E402
import engineering_passport  # noqa: E402

ENGINEERING_DESK = "engineering-codex"
DESK_SPEC_PATH = REPO / "ops" / "config" / "engineering-codex-desk.v1.json"
# The Engineering controller does not inherit the general router's optional
# registry-location override.  This fixed per-user registry is part of the
# local adapter identity, just like the fixed desk name below.
DEDICATED_REGISTRY_PATH = Path.home() / ".config" / "carr" / "hermes-desks.json"
DISPATCH_MINIMUM_RUNWAY = timedelta(seconds=930)
EXECUTOR_TIMEOUT_SECONDS = 900
EXECUTOR_RECEIPT_RESERVE_SECONDS = 120
AUTHORIZED_WRITABLE_ROOTS = (
    "/Users/booko/carr-system/.git",
    "/Users/booko/carr-system/out",
)


class DispatchRefusal(RuntimeError):
    pass


def _git_common_dir() -> Path:
    """Resolve the shared Git metadata root without trusting a child process."""
    dotgit = REPO / ".git"
    if dotgit.is_dir():
        common = dotgit.resolve()
    elif dotgit.is_file():
        try:
            marker = dotgit.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise DispatchRefusal("Engineering repository Git metadata is unavailable") from exc
        prefix = "gitdir: "
        if not marker.startswith(prefix) or "\n" in marker:
            raise DispatchRefusal("Engineering repository Git metadata is malformed")
        git_dir = Path(marker[len(prefix):])
        if not git_dir.is_absolute():
            git_dir = REPO / git_dir
        git_dir = git_dir.resolve()
        commondir = git_dir / "commondir"
        if commondir.is_file():
            try:
                common_ref = commondir.read_text(encoding="utf-8").strip()
            except OSError as exc:
                raise DispatchRefusal("Engineering repository common Git metadata is unavailable") from exc
            if not common_ref or "\n" in common_ref:
                raise DispatchRefusal("Engineering repository common Git metadata is malformed")
            common = (git_dir / common_ref).resolve()
        else:
            common = git_dir
    else:
        raise DispatchRefusal("Engineering repository Git metadata is unavailable")
    if not common.is_dir() or not (common / "HEAD").is_file() or common.name != ".git":
        raise DispatchRefusal("Engineering repository common Git metadata is invalid")
    return common


def _dedicated_writable_roots() -> list[str]:
    """The exact two shared lock roots authorized for repository-write slices."""
    common = _git_common_dir()
    shared_out = (common.parent / "out").resolve()
    if not shared_out.is_dir():
        raise DispatchRefusal("Engineering shared output root is unavailable")
    roots = [str(common), str(shared_out)]
    if tuple(roots) != AUTHORIZED_WRITABLE_ROOTS:
        raise DispatchRefusal("Engineering desk writable roots do not match the authorized machine boundary")
    return roots


def _canonical_utc_second(value: object) -> datetime:
    """Accept only the exact server timestamp representation in an envelope."""
    if not isinstance(value, str):
        raise DispatchRefusal("engineering envelope authority expiry is malformed")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise DispatchRefusal("engineering envelope authority expiry is malformed") from exc
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise DispatchRefusal("engineering envelope authority expiry is malformed")
    return parsed


def _require_dispatch_runway(envelope: dict, claim_lease_expires_at: object) -> None:
    """Refuse unless every authority can outlive the fixed 900s timeout."""
    if not isinstance(envelope, dict):
        raise DispatchRefusal("engineering envelope is malformed")
    expiry = _canonical_utc_second(envelope.get("expires_at"))
    agent_session = envelope.get("agent_session")
    if not isinstance(agent_session, dict):
        raise DispatchRefusal("engineering envelope agent session is malformed")
    session_expiry = _canonical_utc_second(agent_session.get("lease_expires_at"))
    if session_expiry != expiry:
        raise DispatchRefusal("engineering envelope and session expiry do not match")
    claim_expiry = _canonical_utc_second(claim_lease_expires_at)
    checked_at = datetime.now(timezone.utc)
    if (expiry - checked_at < DISPATCH_MINIMUM_RUNWAY
            or claim_expiry - checked_at < DISPATCH_MINIMUM_RUNWAY):
        raise DispatchRefusal("engineering envelope authority runway is insufficient")


def _safe_child_env() -> dict[str, str]:
    """Give Codex its local runtime, never the controller's DB capability."""
    allowed = ("HOME", "PATH", "LANG", "LC_ALL", "TMPDIR", "TERM")
    return {key: os.environ[key] for key in allowed if os.environ.get(key)}


def _read_request() -> dict:
    try:
        raw = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError) as exc:
        raise DispatchRefusal("engineering controller input is not JSON") from exc
    if not isinstance(raw, dict) or set(raw) != {"desk", "envelope", "task", "executor_slug"}:
        raise DispatchRefusal("engineering controller input has an unsupported shape")
    if not isinstance(raw["desk"], str) or not raw["desk"].strip():
        raise DispatchRefusal("engineering controller desk is missing")
    if raw["executor_slug"] != "codex":
        raise DispatchRefusal("engineering controller only supports the server-bound Codex executor")
    if not isinstance(raw["task"], dict):
        raise DispatchRefusal("engineering controller task is missing")
    return raw


def _prompt(packet: dict, task: dict) -> str:
    """One exact execution request.  The executor cannot select authority."""
    return (
        "You are the fresh, dedicated Codex executor for one bounded CARR Engineering Passport slice.\n\n"
        "RULE-DELIVERY WORKFLOW: engineering-slice\n"
        "RULE-DELIVERY PACKS: engineering-git,delegation-council,scheduled-automation,source-study\n"
        "FIRST: call `standing-context` with exactly this input and read the returned rules: "
        "{\"packs\":[\"engineering-git\",\"delegation-council\",\"scheduled-automation\",\"source-study\"]}. "
        "Do not pass `workflow`: standing-context also interprets that field as a pack name, and "
        "`engineering-slice` is a workflow label rather than a canonical rule pack. "
        "REFUSE before inspecting the envelope, source, or job if that call fails, reports any "
        "packs_not_found, or does not read back all four canonical names. Never substitute an alias or "
        "a full-set fallback.\n\n"
        "The controller—not you—owns the database lease, identity, authority, and lifecycle. "
        "Do not connect directly to any database, do not claim/retry/complete a job, do not reuse a session, "
        "and do not widen the accepted slice. Work only inside the controller's isolated Git worktree.\n\n"
        f"HARD EXECUTION BUDGET: the local adapter stops this native turn after {EXECUTOR_TIMEOUT_SECONDS} seconds. "
        f"Reserve the final {EXECUTOR_RECEIPT_RESERVE_SECONDS} seconds for the required commit/push/PR steps when "
        "the slice is complete and for the single typed JSON receipt in every outcome. Never begin a broad or "
        "unbounded check late in the turn. Run the smallest declared-scope fixture/snapshot checks first; broader "
        "repository gates belong after those bounded checks and only when enough time remains. If completion cannot "
        "be proven inside the budget, stop work and return a typed blocked receipt before the adapter deadline.\n\n"
        "RECOVERY FIRST: this fresh native session may follow an expired predecessor envelope. Before broad source "
        "reconstruction, inspect the current branch and Git status. If the isolated worktree already contains only "
        "declared-scope files for this exact slice, treat them as an untrusted checkpoint: review them, continue them, "
        "and cite fresh checks. Do not discard or recreate valid declared-scope progress merely because the native "
        "session is fresh; never inherit the predecessor transcript or its unverified conclusions.\n\n"
        "Complete the bounded slice below. Run the declared checks and preserve any unrelated dirty work. "
        "If the work cannot be completed within the envelope, return a typed failed or blocked receipt; do not "
        "invent success. Your final response must be a single JSON object and nothing else: an exact "
        "engineering-slice-receipt.v1 bound to this envelope and attempt. It must include every planned check, "
        "metadata-only/redacted evidence digests where required, source evidence, fresh-session reconstruction, "
        "and executor_claim.claimed_by exactly `codex`. Independent verification remains required.\n\n"
        f"SERVER-ISSUED SLICE PACKET (immutable):\n{json.dumps(packet, sort_keys=True, separators=(',', ':'))}\n\n"
        f"CONTROLLER TASK BINDING (immutable):\n{json.dumps(task, sort_keys=True, separators=(',', ':'))}"
    )


def _desk_spec() -> dict:
    try:
        value = json.loads(DESK_SPEC_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DispatchRefusal("tracked Engineering desk configuration is unavailable") from exc
    expected = {"schema_version", "name", "kind", "model", "effort", "cwd", "sandbox", "room_seat"}
    if not isinstance(value, dict) or set(value) != expected:
        raise DispatchRefusal("tracked Engineering desk configuration has an unsupported shape")
    if (value["schema_version"] != "engineering-codex-desk.v1" or value["name"] != ENGINEERING_DESK
            or value["kind"] != "codex-session" or value["cwd"] != "{{REPO}}"
            or value["room_seat"] is not None):
        raise DispatchRefusal("tracked Engineering desk configuration is not dedicated and unseated")
    for field in ("model", "effort", "sandbox"):
        if not isinstance(value[field], str) or not value[field].strip():
            raise DispatchRefusal("tracked Engineering desk configuration is incomplete")
    return value


def install_dedicated_codex_desk(registry: desks.Registry) -> dict:
    """Bootstrap only the tracked unseated desk, then return its exact readback."""
    spec = _desk_spec()
    entry = registry.register(spec["name"], spec["kind"], model=spec["model"], effort=spec["effort"],
                              cwd=str(REPO), sandbox=spec["sandbox"],
                              add_dirs=_dedicated_writable_roots())
    return _dedicated_codex_desk(registry)


def _dedicated_codex_desk(registry: desks.Registry) -> dict:
    """Resolve the one reviewed local adapter before it sees an envelope."""
    try:
        entry = registry.resolve(ENGINEERING_DESK)
    except desks.DeskError as exc:
        raise DispatchRefusal("dedicated Engineering Codex desk is unavailable") from exc
    # A corrupted local registry must fail before a Passport packet reaches a
    # different model, a Claude socket, or a desk with unspecified execution
    # characteristics.  The fixed desk itself is the local native surface.
    spec = _desk_spec()
    allowed_fields = {
        "name", "kind", "model", "effort", "cwd", "sandbox", "add_dirs", "room_seat", "thread_id", "registered_at",
        # These are bridge-owned liveness/auth observations, never execution
        # choices. They are allowed to change without widening the desk.
        "last_seen", "last_live", "last_auth", "last_auth_at",
    }
    if set(entry) - allowed_fields:
        raise DispatchRefusal("dedicated Engineering desk has an unapproved execution field")
    expected = {"kind": spec["kind"], "model": spec["model"], "effort": spec["effort"],
                "cwd": str(REPO), "sandbox": spec["sandbox"],
                "add_dirs": _dedicated_writable_roots()}
    if entry.get("name") != ENGINEERING_DESK or any(entry.get(key) != value for key, value in expected.items()) or entry.get("room_seat") is not None:
        raise DispatchRefusal("dedicated Engineering desk is not a modeled Codex session")
    if (entry.get("thread_id") is not None and not isinstance(entry["thread_id"], str)) or (
            "registered_at" in entry and not isinstance(entry["registered_at"], str)) or (
            "last_seen" in entry and not isinstance(entry["last_seen"], str)) or (
            "last_live" in entry and not isinstance(entry["last_live"], bool)) or (
            "last_auth" in entry and entry["last_auth"] is not None and not isinstance(entry["last_auth"], bool)) or (
            "last_auth_at" in entry and not isinstance(entry["last_auth_at"], str)):
        raise DispatchRefusal("dedicated Engineering desk metadata is invalid")
    return entry


def run(request: dict, *, dispatch_fn=dispatch.dispatch, registry: desks.Registry | None = None) -> dict:
    if request.get("desk") != ENGINEERING_DESK:
        raise DispatchRefusal("engineering controller attempted to select a different desk")
    _dedicated_codex_desk(registry or desks.Registry(DEDICATED_REGISTRY_PATH))
    task = request["task"]
    plan = task.get("engineering_plan")
    slice_row = task.get("engineering_slice")
    envelope = request["envelope"]
    if envelope.get("server_binding", {}).get("adapter", {}).get("surface") != "codex_desktop":
        raise DispatchRefusal("engineering envelope does not select the Codex desktop adapter")
    # Reject malformed authority before contract parsing, and repeat the same
    # check directly beside dispatch below as the final launch boundary.
    _require_dispatch_runway(envelope, task.get("claim_lease_expires_at"))
    if not isinstance(slice_row, dict) or not isinstance(task.get("slice_ref"), str):
        raise DispatchRefusal("engineering controller task has no exact slice")
    if (not isinstance(plan, dict) or not isinstance(plan.get("work_request"), dict)
            or task.get("work_request") != plan["work_request"].get("id")
            or task.get("work_request") != envelope.get("work_request_id")):
        raise DispatchRefusal("engineering controller task work request is not plan/envelope bound")
    packet = engineering_passport.build_engineering_slice_packet(envelope, plan, task["slice_ref"])
    if packet["slice_ref"] != slice_row.get("slice_ref") or task.get("plan_digest") != packet["plan_digest"]:
        raise DispatchRefusal("engineering controller task does not match its accepted packet")
    _require_dispatch_runway(envelope, task.get("claim_lease_expires_at"))
    # The database lease deadline is controller authority, not model input; it
    # must be checked at launch but excluded from the packet/task digest.
    prompt_task = {key: value for key, value in task.items() if key != "claim_lease_expires_at"}
    row = dispatch_fn(request["desk"], _prompt(packet, prompt_task), env=_safe_child_env(), fresh=True)
    if not isinstance(row, dict) or row.get("status") != "completed":
        status = row.get("status") if isinstance(row, dict) else "invalid"
        raise DispatchRefusal(f"engineering desk dispatch did not complete: {status}")
    try:
        receipt = json.loads(str(row.get("result") or "").strip())
    except json.JSONDecodeError as exc:
        raise DispatchRefusal("engineering desk did not return one JSON receipt") from exc
    engineering_passport.validate_engineering_slice_receipt(receipt, plan, envelope)
    attribution = receipt.get("attribution")
    identity = envelope.get("server_binding", {}).get("identity", {})
    adapter_binding = envelope.get("server_binding", {}).get("adapter", {})
    expected_attribution = {
        "actor_ref": identity.get("agent_principal_id"),
        "session_ref": envelope.get("agent_session", {}).get("id"),
        "adapter_ref": adapter_binding.get("adapter_id"),
    }
    # Receipt attribution is evidence of the envelope the executor saw, not a
    # second opportunity for a model or local caller to name a principal or
    # native session.  The full envelope validator above establishes shape;
    # this equality establishes the controller boundary.
    if attribution != expected_attribution:
        raise DispatchRefusal("engineering receipt attribution does not match the server binding")
    claim = receipt.get("executor_claim")
    if not isinstance(claim, dict) or claim.get("claimed_by") != request["executor_slug"]:
        raise DispatchRefusal("engineering receipt executor does not match the server binding")
    return {"ok": True, "receipt": receipt,
            "dispatch": {"status": "completed", "thread_id": row.get("thread_id")}}


def main() -> int:
    try:
        if sys.argv[1:] == ["--preflight"]:
            entry = _dedicated_codex_desk(desks.Registry(DEDICATED_REGISTRY_PATH))
            print(json.dumps({"ok": True, "desk": {key: entry[key] for key in
                  ("name", "kind", "model", "effort", "cwd", "sandbox", "add_dirs")}}, separators=(",", ":")))
            return 0
        if sys.argv[1:] == ["--install-desk"]:
            entry = install_dedicated_codex_desk(desks.Registry(DEDICATED_REGISTRY_PATH))
            print(json.dumps({"ok": True, "desk": {key: entry[key] for key in
                  ("name", "kind", "model", "effort", "cwd", "sandbox", "add_dirs")}}, separators=(",", ":")))
            return 0
        if len(sys.argv) != 1:
            raise DispatchRefusal("engineering adapter received unsupported arguments")
        result = run(_read_request())
    except (DispatchRefusal, engineering_passport.EngineeringContractError, dispatch.DeskError) as exc:
        print(json.dumps({"ok": False, "error": type(exc).__name__}, separators=(",", ":")), file=sys.stderr)
        return 1
    print(json.dumps(result, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
