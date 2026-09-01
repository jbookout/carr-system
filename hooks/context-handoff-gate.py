#!/usr/bin/env python3
"""Cross-surface context lifecycle gate.

Claude invokes this file at PostToolUse, PreCompact, and Stop. PostToolUse
records high-water/density evidence and may announce once; PreCompact records
but always allows; Stop is the only Claude refusal seam. Codex has no claimed
native Stop hook: its rollout and dispatcher adapters are explicit bare CLI
commands exposed by this same file.

Lifecycle mutations are expected-version CAS operations over one task file.
Immutable offer, receipt, and final packet objects are written before the CAS;
if a concurrent writer wins, those unreferenced objects are harmless orphans.
"""

from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import json
import math
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from stop_latch import announce  # noqa: E402

MANIFEST_DEFAULT = REPO / "ops/config/session-context-lifecycle.v1.json"
STATE_DIR_DEFAULT = REPO / "out/session-context-lifecycle"
ACTIONS = {"NOOP", "RECOVER_SAME_TASK", "REFUSE"}
REASONS = {
    "CONTEXT_HANDOFF_REQUIRED", "CONTEXT_SIGNAL_UNAVAILABLE",
    "CONTEXT_SIGNAL_AMBIGUOUS", "CONTEXT_SIGNAL_INVALID",
    "WINDOW_CONFIG_INVALID", "HANDOFF_RECEIPT_MISSING",
    "HANDOFF_RECEIPT_INVALID", "SUCCESSOR_SURFACE_INVALID",
    "SUCCESSOR_NOT_ACTIVE", "SUCCESSOR_NOT_PINNED",
    "TAKEOVER_NOT_VERIFIED", "OWNERSHIP_MISMATCH", "OWNERSHIP_MISSING",
    "OWNERSHIP_DUPLICATE", "LIFECYCLE_INVALID", "RECOVERY_CAPACITY",
    "RECOVERY_ERROR", "RECOVERY_IDLE", "RECOVERY_ACTIVE",
    "RECOVERY_TERMINAL", "RECOVERY_SNAPSHOT_STALE",
    "RECOVERY_SNAPSHOT_INVALID", "RECOVERY_WAITING",
}
WORKER_STATES = {
    "RUNNING", "IDLE", "WAITING_ATTENTION", "CAPACITY_EXHAUSTED",
    "ERROR", "TERMINAL",
}
SURFACES = {"claude", "codex"}
OWNER_STATES = {"ACTIVE", "DRAINING", "SUCCESSOR_DECLARED", "TERMINAL"}
HANDOFF_STATES = {
    "DRAINING", "SUCCESSOR_DECLARED", "TAKEOVER_VERIFIED",
    "PREDECESSOR_TERMINAL",
}
RECOVERY_STATES = {"PENDING", "COMPLETED", "ABORTED"}
MUTATING_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
WORKER_TOOLS = {"Agent", "Task", "spawn_agent", "create_thread", "fork_thread"}
WINDOW_KEYS = {"model_context_window", "context_window", "contextWindow"}
TOTAL_KEYS = {"total_tokens", "totalTokens"}
USAGE_PARTS = {
    "input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens",
    "output_tokens", "inputTokens", "cacheCreationInputTokens",
    "cacheReadInputTokens", "outputTokens",
}


class LifecycleError(RuntimeError):
    def __init__(self, reason: str, detail: str):
        super().__init__(detail)
        self.reason = reason if reason in REASONS else "LIFECYCLE_INVALID"
        self.detail = detail


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_time(value: Any) -> dt.datetime:
    if not isinstance(value, str) or not value:
        raise ValueError("timestamp must be a non-empty string")
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(dt.timezone.utc)


def positive(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def manifest_path() -> Path:
    return Path(os.environ.get("CARR_SESSION_CONTEXT_MANIFEST", MANIFEST_DEFAULT))


def load_manifest() -> dict[str, Any]:
    try:
        data = json.loads(manifest_path().read_text(encoding="utf-8"))
    except Exception as exc:
        raise LifecycleError("WINDOW_CONFIG_INVALID", f"manifest unreadable: {exc}") from exc
    try:
        if data.get("schema_version") != 1:
            raise ValueError("schema_version must be 1")
        thresholds = data["thresholds_percent"]
        dense = positive(thresholds["dense_soft"])
        normal = positive(thresholds["normal_soft"])
        hard = positive(thresholds["hard"])
        if not dense or not normal or not hard or not dense < normal < hard <= 100:
            raise ValueError("thresholds must satisfy 0 < dense < normal < hard <= 100")
        if set(data["actions"]) != ACTIONS or set(data["reasons"]) != REASONS:
            raise ValueError("action/reason vocabulary drift")
        if not isinstance(data["model_windows"], dict):
            raise ValueError("model_windows must be an object")
    except Exception as exc:
        raise LifecycleError("WINDOW_CONFIG_INVALID", f"manifest invalid: {exc}") from exc
    return data


def state_root(manifest: dict[str, Any] | None = None) -> Path:
    explicit = os.environ.get("CARR_SESSION_CONTEXT_STATE_DIR")
    if explicit:
        return Path(explicit)
    return REPO / (manifest or {}).get("state_directory", "out/session-context-lifecycle")


def state_file(task_key: str, manifest: dict[str, Any] | None = None) -> Path:
    legacy = os.environ.get("CARR_CONTEXT_STATE")
    if legacy:
        return Path(legacy)
    return state_root(manifest) / f"{hashlib.sha256(task_key.encode()).hexdigest()}.json"


def lock_file(task_key: str, manifest: dict[str, Any] | None = None) -> Path:
    return state_file(task_key, manifest).with_suffix(".lock")


def load_json_file(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except Exception as exc:
        raise LifecycleError("LIFECYCLE_INVALID", f"state unreadable: {exc}") from exc
    if not isinstance(value, dict):
        raise LifecycleError("LIFECYCLE_INVALID", "state is not an object")
    return value


SIGNAL_KEYS = {
    "highwater", "invocations", "active_minutes", "cycles",
    "generation_tool_calls", "mutated_paths", "worker_starts",
    "last_observed_at", "notices",
}


def validate_signal_state(signal: Any) -> None:
    if not isinstance(signal, dict):
        raise LifecycleError("LIFECYCLE_INVALID", "signal is not an object")
    if frozenset(signal) not in {frozenset(SIGNAL_KEYS),
                                 frozenset(SIGNAL_KEYS | {"risk"})}:
        raise LifecycleError(
            "LIFECYCLE_INVALID", "signal fields are not exact")
    for field in ("highwater", "invocations", "cycles",
                  "generation_tool_calls", "worker_starts"):
        value = signal.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise LifecycleError(
                "LIFECYCLE_INVALID", f"signal {field} is invalid")
    active_minutes = signal.get("active_minutes")
    if (isinstance(active_minutes, bool)
            or not isinstance(active_minutes, (int, float))
            or not math.isfinite(float(active_minutes))
            or float(active_minutes) < 0):
        raise LifecycleError(
            "LIFECYCLE_INVALID", "signal active_minutes is invalid")
    for field in ("mutated_paths", "notices"):
        values = signal.get(field)
        if (not isinstance(values, list)
                or any(not isinstance(item, str) or not item for item in values)
                or values != sorted(set(values))):
            raise LifecycleError(
                "LIFECYCLE_INVALID", f"signal {field} is invalid")
    observed = signal.get("last_observed_at")
    if observed is not None:
        try:
            parse_time(observed)
        except Exception as exc:
            raise LifecycleError(
                "LIFECYCLE_INVALID",
                f"signal last_observed_at is invalid: {exc}") from exc
    if "risk" in signal and signal.get("risk") not in {
            "R0", "R1", "R2", "R3", "R4", "R5", "R6"}:
        raise LifecycleError("LIFECYCLE_INVALID", "signal risk is invalid")


def is_digest(value: Any) -> bool:
    return (isinstance(value, str) and len(value) == 64
            and all(char in "0123456789abcdef" for char in value))


def validate_task_state(value: dict[str, Any], task_key: str,
                        manifest: dict[str, Any] | None = None) -> None:
    """Reject structurally inconsistent persisted lifecycle state.

    State files are mutable input outside the hook process's trust boundary.
    Every reader must establish the small ownership/shape invariants it relies
    on before a Stop decision or state-machine transition can use them.
    """
    if value.get("schema_version") != 1:
        raise LifecycleError("LIFECYCLE_INVALID", "state schema version is invalid")
    if value.get("task_key") != task_key:
        raise LifecycleError("LIFECYCLE_INVALID", "state task_key mismatch")
    version = value.get("version")
    if (isinstance(version, bool) or not isinstance(version, int)
            or version < -1):
        raise LifecycleError("LIFECYCLE_INVALID", "state version is not an integer")
    generation = value.get("generation")
    if (isinstance(generation, bool) or not isinstance(generation, int)
            or generation < 0):
        raise LifecycleError("LIFECYCLE_INVALID", "state generation is invalid")
    status = value.get("task_status")
    if status not in {"ACTIVE", "TERMINAL"}:
        raise LifecycleError("LIFECYCLE_INVALID", "task status is invalid")
    owners = value.get("owners")
    if not isinstance(owners, dict):
        raise LifecycleError("LIFECYCLE_INVALID", "owners is not an object")
    for owner_id, owner in owners.items():
        if (not isinstance(owner_id, str) or not owner_id
                or not isinstance(owner, dict)
                or owner.get("id") != owner_id):
            raise LifecycleError("LIFECYCLE_INVALID", "owner record is invalid")
        owner_generation = owner.get("generation")
        activation_init = owner.get("activation_init_digest")
        activation_final = owner.get("activation_final_digest")
        if (owner.get("surface") not in SURFACES
                or owner.get("state") not in OWNER_STATES
                or isinstance(owner_generation, bool)
                or not isinstance(owner_generation, int)
                or owner_generation < 0
                or owner_generation > generation + 1
                or (owner.get("evidence_digest") is not None
                    and not is_digest(owner.get("evidence_digest")))
                or (activation_final is not None
                    and not is_digest(activation_final))
                or (activation_init is not None
                    and not is_digest(activation_init))
                or owner.get("ownership_digest") != owner_digest(owner)):
            raise LifecycleError("LIFECYCLE_INVALID",
                                 "owner record semantics are invalid")
        if owner_generation == 0:
            if (not is_digest(activation_init)
                    or activation_final is not None):
                raise LifecycleError(
                    "LIFECYCLE_INVALID",
                    "initial owner lacks immutable activation provenance")
            verify_owner_initialization(task_key, owner, manifest)
        elif activation_init is not None:
            raise LifecycleError(
                "LIFECYCLE_INVALID",
                "successor owner carries initialization provenance")
        if (owner_generation > 0
                and owner.get("state") != "SUCCESSOR_DECLARED"
                and not is_digest(activation_final)):
            raise LifecycleError(
                "LIFECYCLE_INVALID",
                "verified owner lacks immutable activation provenance")
        if activation_final is not None:
            verify_owner_activation(task_key, owner, manifest)
        terminal_evidence = owner.get("terminal_evidence_digest")
        terminal_provenance = owner.get("terminal_provenance_digest")
        if owner.get("state") == "TERMINAL":
            if (not is_digest(terminal_evidence)
                    or not is_digest(terminal_provenance)):
                raise LifecycleError(
                    "LIFECYCLE_INVALID",
                    "terminal owner lacks immutable terminal provenance")
        elif terminal_evidence is not None or terminal_provenance is not None:
            raise LifecycleError(
                "LIFECYCLE_INVALID",
                "nonterminal owner retains terminal provenance")
    active_owner = value.get("active_owner")
    if status == "ACTIVE":
        if not isinstance(active_owner, str) or active_owner not in owners:
            raise LifecycleError("LIFECYCLE_INVALID",
                                 "active_owner has no owner record")
    elif active_owner is not None:
        raise LifecycleError("LIFECYCLE_INVALID",
                             "terminal task retains an active owner")
    for field in ("handoff", "recovery_intent"):
        if value.get(field) is not None and not isinstance(value.get(field), dict):
            raise LifecycleError("LIFECYCLE_INVALID", f"{field} is not an object")
    validate_signal_state(value.get("signal"))

    handoff = value.get("handoff") or {}
    handoff_state = handoff.get("state")
    if handoff and handoff_state not in HANDOFF_STATES:
        raise LifecycleError("LIFECYCLE_INVALID", "handoff state is invalid")
    by_generation: dict[int, dict[str, Any]] = {}
    for owner in owners.values():
        owner_generation = int(owner["generation"])
        if owner_generation in by_generation:
            raise LifecycleError(
                "LIFECYCLE_INVALID", "multiple owners share one generation")
        by_generation[owner_generation] = owner
    expected_generations = set(range(generation + 1))
    if handoff_state == "SUCCESSOR_DECLARED":
        expected_generations.add(generation + 1)
    if set(by_generation) != expected_generations:
        raise LifecycleError(
            "LIFECYCLE_INVALID", "immutable owner lineage is incomplete")
    for owner_generation in range(1, generation + 1):
        owner = by_generation[owner_generation]
        lineage_predecessor = by_generation[owner_generation - 1]
        verified = read_verified_final_packet(
            task_key, owner.get("activation_final_digest"), manifest)
        offer = verified["offer"]
        if (offer.get("generation") != owner_generation
                or offer.get("predecessor") != lineage_predecessor.get("id")
                or offer.get("predecessor_surface")
                != lineage_predecessor.get("surface")
                or offer.get("successor") != owner.get("id")
                or offer.get("successor_surface") != owner.get("surface")):
            raise LifecycleError(
                "HANDOFF_RECEIPT_INVALID",
                "owner lineage is not linked to immutable handoff history")
    if handoff:
        predecessor = handoff.get("predecessor")
        successor = handoff.get("successor")
        handoff_generation = handoff.get("generation")
        if (not isinstance(predecessor, str) or not predecessor
                or not isinstance(successor, str) or not successor
                or predecessor == successor
                or predecessor not in owners
                or isinstance(handoff_generation, bool)
                or not isinstance(handoff_generation, int)
                or not is_digest(handoff.get("offer_digest"))):
            raise LifecycleError("LIFECYCLE_INVALID",
                                 "handoff identity is invalid")
        if handoff_state in {
                "SUCCESSOR_DECLARED", "TAKEOVER_VERIFIED",
                "PREDECESSOR_TERMINAL"}:
            if (successor not in owners
                    or not is_digest(handoff.get("declaration_digest"))):
                raise LifecycleError("LIFECYCLE_INVALID",
                                     "handoff declaration is invalid")
        if handoff_state in {"TAKEOVER_VERIFIED", "PREDECESSOR_TERMINAL"}:
            if (not is_digest(handoff.get("receipt_digest"))
                    or not is_digest(handoff.get("final_digest"))):
                raise LifecycleError("LIFECYCLE_INVALID",
                                     "verified handoff receipts are invalid")

        predecessor_state = owners[predecessor].get("state")
        successor_state = (owners.get(successor) or {}).get("state")
        if handoff_state == "DRAINING":
            valid = (handoff_generation == generation + 1
                     and active_owner == predecessor
                     and predecessor_state == "DRAINING")
        elif handoff_state == "SUCCESSOR_DECLARED":
            valid = (handoff_generation == generation + 1
                     and active_owner == predecessor
                     and predecessor_state == "DRAINING"
                     and successor_state == "SUCCESSOR_DECLARED"
                     and owners[successor].get("generation") == handoff_generation)
        elif handoff_state == "TAKEOVER_VERIFIED":
            valid = (handoff_generation == generation
                     and active_owner == successor
                     and predecessor_state == "DRAINING"
                     and successor_state == "ACTIVE"
                     and owners[successor].get("generation") == generation)
        else:
            valid = (handoff_generation == generation
                     and predecessor_state == "TERMINAL"
                     and ((status == "ACTIVE" and active_owner == successor
                           and successor_state == "ACTIVE")
                          or (status == "TERMINAL" and active_owner is None
                              and successor_state == "TERMINAL")))
        if not valid:
            raise LifecycleError("LIFECYCLE_INVALID",
                                 "handoff ownership relationship is invalid")

    recovery = value.get("recovery_intent") or {}
    recovery_state = recovery.get("state")
    if recovery and recovery_state not in RECOVERY_STATES:
        raise LifecycleError("LIFECYCLE_INVALID", "recovery state is invalid")
    if recovery:
        failed_owner = recovery.get("failed_owner")
        recovery_generation = recovery.get("generation")
        if (recovery.get("task_key") != task_key
                or failed_owner not in owners
                or isinstance(recovery_generation, bool)
                or not isinstance(recovery_generation, int)
                or recovery_generation < 0
                or not is_digest(recovery.get("nonce"))
                or not is_digest(recovery.get("snapshot_digest"))
                or not isinstance(recovery.get("source_event_id"), str)
                or not recovery.get("source_event_id")):
            raise LifecycleError("LIFECYCLE_INVALID",
                                 "recovery identity is invalid")
        failed_state = owners[failed_owner].get("state")
        if recovery_state == "PENDING" and failed_state != "DRAINING":
            raise LifecycleError("LIFECYCLE_INVALID",
                                 "pending recovery owner is not draining")
        if (recovery_state == "ABORTED" and status == "ACTIVE"
                and failed_state != "ACTIVE"):
            raise LifecycleError("LIFECYCLE_INVALID",
                                 "aborted recovery owner is not active")

    active_ids = [owner_id for owner_id, owner in owners.items()
                  if owner.get("state") == "ACTIVE"]
    transitional = (handoff_state in {"DRAINING", "SUCCESSOR_DECLARED"}
                    or recovery_state == "PENDING")
    if status == "ACTIVE":
        expected_state = "DRAINING" if transitional else "ACTIVE"
        if (owners[active_owner].get("state") != expected_state
                or active_ids != ([] if transitional else [active_owner])):
            raise LifecycleError("LIFECYCLE_INVALID",
                                 "active ownership cardinality is invalid")
    elif active_ids or any(owner.get("state") != "TERMINAL"
                           for owner in owners.values()):
        raise LifecycleError("LIFECYCLE_INVALID",
                             "terminal task retains nonterminal owners")
    for owner in owners.values():
        if owner.get("state") == "TERMINAL":
            verify_terminal_provenance(task_key, value, owner, manifest)
    if handoff_state in {"TAKEOVER_VERIFIED", "PREDECESSOR_TERMINAL"}:
        verify_handoff_state(task_key, value, manifest)


def atomic_write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(canonical(value) + b"\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temp_name, path)
        dfd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def mutate_state(task_key: str, expected_version: int | None,
                 change: Callable[[dict[str, Any] | None], dict[str, Any]],
                 manifest: dict[str, Any] | None = None) -> dict[str, Any]:
    path = state_file(task_key, manifest)
    lock = lock_file(task_key, manifest)
    lock.parent.mkdir(parents=True, exist_ok=True)
    with open(lock, "a+b") as guard:
        fcntl.flock(guard.fileno(), fcntl.LOCK_EX)
        current = load_json_file(path)
        if current is not None:
            validate_task_state(current, task_key, manifest)
        actual = int(current.get("version", -1)) if current else -1
        if expected_version is not None and actual != expected_version:
            raise LifecycleError("LIFECYCLE_INVALID",
                                 f"expected version {expected_version}, found {actual}")
        before = canonical(current) if current is not None else None
        updated = change(current)
        if not isinstance(updated, dict) or updated.get("task_key") != task_key:
            raise LifecycleError("LIFECYCLE_INVALID", "mutation returned invalid task state")
        validate_task_state(updated, task_key, manifest)
        # Observations of a terminal task, an already-drained predecessor, and
        # exact idempotent replays are reads.  Do not manufacture a new version
        # merely because they passed through the mutation lock.
        if before is not None and canonical(updated) == before:
            return updated
        updated["version"] = actual + 1
        updated["updated_at"] = utc_now()
        atomic_write(path, updated)
        return updated


def read_state(task_key: str, manifest: dict[str, Any] | None = None) -> dict[str, Any] | None:
    state = load_json_file(state_file(task_key, manifest))
    if state is not None:
        validate_task_state(state, task_key, manifest)
    return state


def object_path(task_key: str, kind: str, object_digest: str,
                manifest: dict[str, Any] | None = None) -> Path:
    task_hash = hashlib.sha256(task_key.encode()).hexdigest()
    return state_root(manifest) / "objects" / task_hash / kind / f"{object_digest}.json"


def write_immutable(task_key: str, kind: str, value: Any,
                    manifest: dict[str, Any] | None = None) -> str:
    object_digest = digest(value)
    path = object_path(task_key, kind, object_digest, manifest)
    path.parent.mkdir(parents=True, exist_ok=True)
    publication_lock = path.with_suffix(".lock")

    def verify_existing() -> None:
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise LifecycleError(
                "HANDOFF_RECEIPT_INVALID",
                f"invalid published {kind}: {exc}") from exc
        if digest(existing) != object_digest:
            raise LifecycleError(
                "HANDOFF_RECEIPT_INVALID", "immutable object collision")

    with open(publication_lock, "a+b") as guard:
        fcntl.flock(guard.fileno(), fcntl.LOCK_EX)
        if path.exists():
            verify_existing()
            return object_digest
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{path.name}.", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(canonical(value) + b"\n")
                fh.flush()
                os.fsync(fh.fileno())
            try:
                os.link(temp_name, path)
            except FileExistsError:
                verify_existing()
            dfd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
        finally:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass
    return object_digest


def read_object(task_key: str, kind: str, object_digest: str,
                manifest: dict[str, Any] | None = None) -> dict[str, Any]:
    path = object_path(task_key, kind, object_digest, manifest)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise LifecycleError("HANDOFF_RECEIPT_MISSING", f"missing {kind} {object_digest}") from exc
    except Exception as exc:
        raise LifecycleError("HANDOFF_RECEIPT_INVALID", f"invalid {kind}: {exc}") from exc
    if not isinstance(value, dict) or digest(value) != object_digest:
        raise LifecycleError("HANDOFF_RECEIPT_INVALID", f"tampered {kind} {object_digest}")
    return value


def verify_owner_initialization(task_key: str, owner: dict[str, Any],
                                manifest: dict[str, Any] | None = None) -> None:
    """Bind generation-zero ownership to an immutable admission packet."""
    packet = read_object(
        task_key, "initialization", str(owner.get("activation_init_digest")),
        manifest)
    evidence = packet.get("native_evidence")
    evidence_digest = packet.get("native_evidence_digest")
    source = packet.get("source")
    if (packet.get("schema_version") != 1
            or packet.get("task_key") != task_key
            or packet.get("owner") != owner.get("id")
            or packet.get("surface") != owner.get("surface")
            or packet.get("generation") != 0
            or source not in {"task_init", "claude_hook"}
            or not isinstance(packet.get("created_at"), str)
            or not packet.get("created_at")
            or evidence_digest != owner.get("evidence_digest")):
        raise LifecycleError(
            "HANDOFF_RECEIPT_INVALID",
            "initialization provenance linkage is invalid")
    if source == "claude_hook":
        if (not isinstance(evidence, dict)
                or not is_digest(evidence_digest)
                or digest(evidence) != evidence_digest
                or evidence.get("session_id") != owner.get("id")
                or not isinstance(evidence.get("controller_callback_id"), str)
                or not evidence.get("controller_callback_id")
                or (evidence.get("transcript_path") is not None
                    and (not isinstance(evidence.get("transcript_path"), str)
                         or not evidence.get("transcript_path")))
                or evidence.get("status") != "active"):
            raise LifecycleError(
                "HANDOFF_RECEIPT_INVALID",
                "hook initialization native evidence is invalid")
        return
    if (not isinstance(evidence, dict)
            or not is_digest(evidence_digest)
            or digest(evidence) != evidence_digest):
        raise LifecycleError(
            "HANDOFF_RECEIPT_INVALID",
            "initialization native evidence linkage is invalid")
    try:
        validated = validate_native_evidence(
            str(owner.get("surface")), evidence,
            expected_identity=str(owner.get("id")), accept=True,
            require_live=False)
    except LifecycleError as exc:
        raise LifecycleError(
            "HANDOFF_RECEIPT_INVALID",
            f"initialization native evidence is invalid: {exc.detail}") from exc
    if validated != evidence_digest:
        raise LifecycleError(
            "HANDOFF_RECEIPT_INVALID",
            "initialization native evidence digest changed")


def verify_terminal_provenance(task_key: str, state: dict[str, Any],
                               owner: dict[str, Any],
                               manifest: dict[str, Any] | None = None) -> None:
    provenance_digest = owner.get("terminal_provenance_digest")
    packet = read_object(
        task_key, "terminal", str(provenance_digest), manifest)
    evidence = packet.get("native_evidence")
    evidence_digest = packet.get("native_evidence_digest")
    kind = packet.get("terminal_kind")
    if (packet.get("schema_version") != 1
            or packet.get("task_key") != task_key
            or packet.get("owner") != owner.get("id")
            or packet.get("surface") != owner.get("surface")
            or packet.get("generation") != owner.get("generation")
            or not isinstance(packet.get("created_at"), str)
            or not packet.get("created_at")
            or not isinstance(evidence, dict)
            or not is_digest(evidence_digest)
            or evidence_digest != owner.get("terminal_evidence_digest")
            or digest(evidence) != evidence_digest):
        raise LifecycleError(
            "HANDOFF_RECEIPT_INVALID",
            "terminal provenance linkage is invalid")
    if kind == "predecessor_terminal":
        final_digest = packet.get("handoff_final_digest")
        try:
            verified = read_verified_final_packet(
                task_key, final_digest, manifest)
        except LifecycleError as exc:
            raise LifecycleError(
                "HANDOFF_RECEIPT_INVALID",
                f"terminal handoff packet is invalid: {exc.detail}") from exc
        offer = verified["offer"]
        owner_generation = owner.get("generation")
        linked = (offer.get("predecessor") == owner.get("id")
                  and offer.get("predecessor_surface") == owner.get("surface")
                  and isinstance(owner_generation, int)
                  and not isinstance(owner_generation, bool)
                  and offer.get("generation") == owner_generation + 1)
    elif kind == "task_terminal":
        final_digest = packet.get("handoff_final_digest")
        linked = state.get("task_status") == "TERMINAL"
        if linked and final_digest is not None:
            try:
                verified = read_verified_final_packet(
                    task_key, final_digest, manifest)
            except LifecycleError as exc:
                raise LifecycleError(
                    "HANDOFF_RECEIPT_INVALID",
                    f"terminal handoff packet is invalid: {exc.detail}") from exc
            offer = verified["offer"]
            linked = (offer.get("successor") == owner.get("id")
                      and offer.get("successor_surface") == owner.get("surface")
                      and offer.get("generation") == owner.get("generation"))
    else:
        linked = False
    if not linked:
        raise LifecycleError(
            "HANDOFF_RECEIPT_INVALID",
            "terminal provenance is not linked to lifecycle state")
    try:
        validated = validate_native_evidence(
            str(owner.get("surface")), evidence,
            expected_identity=str(owner.get("id")), terminal=True,
            require_live=False)
    except LifecycleError as exc:
        raise LifecycleError(
            "HANDOFF_RECEIPT_INVALID",
            f"terminal native evidence is invalid: {exc.detail}") from exc
    if validated != evidence_digest:
        raise LifecycleError(
            "HANDOFF_RECEIPT_INVALID",
            "terminal native evidence digest changed")


def audit(record: dict[str, Any], manifest: dict[str, Any] | None = None) -> None:
    if record.get("session") == "selftest" or os.environ.get("CARR_CONTEXT_AUDIT") == "off":
        return
    default = REPO / (manifest or {}).get("audit_log", "out/context-handoff.jsonl")
    path = Path(os.environ.get("CARR_CONTEXT_AUDIT", default))
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        row = {"ts": utc_now(), "hook": "context-handoff-gate", **record}
        with open(path, "ab") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            fh.write(canonical(row) + b"\n")
            fh.flush()
            os.fsync(fh.fileno())
    except Exception:
        pass


def context_envelopes(row: dict[str, Any]):
    """Containers whose model/window fields describe the current session.

    Claude publishes those fields on the row or its message envelope. Codex
    publishes its model on a turn_context payload. Tool inputs and assistant
    content may also contain fields named model or context_window, but those
    describe a child invocation and must never select this session's threshold.
    """
    yield row
    message = row.get("message")
    if isinstance(message, dict):
        yield message
    payload = row.get("payload")
    if row.get("type") == "turn_context" and isinstance(payload, dict):
        yield payload


def transcript_rows(path: str | None) -> list[dict[str, Any]]:
    if not path:
        return []
    if not isinstance(path, str):
        raise LifecycleError(
            "LIFECYCLE_INVALID", "transcript_path must be a string")
    rows: list[dict[str, Any]] = []
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if isinstance(row, dict):
                    rows.append(row)
    except OSError:
        return []
    return rows


def compact_pre_tokens(row: dict[str, Any]) -> int | None:
    raw_payload = row.get("payload")
    payload: dict[str, Any] = raw_payload if isinstance(raw_payload, dict) else row
    kind = payload.get("type") or row.get("type")
    if kind != "compact_boundary":
        return None
    for key in ("preTokens", "pre_tokens"):
        number = positive(payload.get(key))
        if number:
            return number
    return None


def usage_total(row: dict[str, Any]) -> int | None:
    # Codex token_count events carry two different domains: last_token_usage
    # is the live turn/context occupancy, while total_token_usage is cumulative
    # billing usage across the session.  Combining them turns a modest current
    # context into millions of apparent tokens and forces spurious handoffs.
    raw_payload = row.get("payload")
    payload: dict[str, Any] = raw_payload if isinstance(raw_payload, dict) else {}
    if row.get("type") == "event_msg" and payload.get("type") == "token_count":
        info = payload.get("info")
        current = info.get("last_token_usage") if isinstance(info, dict) else None
        if not isinstance(current, dict):
            return None
        direct: list[int] = []
        for key in TOTAL_KEYS:
            number = positive(current.get(key)) if key in current else None
            if number:
                direct.append(number)
        if direct:
            return max(direct)
        parts = [positive(current.get(key)) for key in USAGE_PARTS if key in current]
        return sum(number or 0 for number in parts) if any(parts) else None

    candidates: list[int] = []
    containers = [row]
    for key in ("message", "payload"):
        if isinstance(row.get(key), dict):
            containers.append(row[key])
    for container in list(containers):
        if isinstance(container.get("usage"), dict):
            containers.append(container["usage"])
        info = container.get("info")
        if isinstance(info, dict):
            for key in ("total_token_usage", "last_token_usage"):
                if isinstance(info.get(key), dict):
                    containers.append(info[key])
    for container in containers:
        candidates.extend(number for number in
                          (positive(container.get(key)) for key in TOTAL_KEYS if key in container)
                          if number)
        parts = [positive(container.get(key)) for key in USAGE_PARTS if key in container]
        if any(parts):
            candidates.append(sum(number or 0 for number in parts))
    compact = compact_pre_tokens(row)
    if compact:
        candidates.append(compact)
    return max(candidates) if candidates else None


def models_in(rows: list[dict[str, Any]]) -> set[str]:
    models: set[str] = set()
    for row in rows:
        for envelope in context_envelopes(row):
            for key in ("model", "model_name", "modelName"):
                value = envelope.get(key)
                if isinstance(value, str) and value.strip():
                    models.add(value.strip())
    return models


def explicit_windows(rows: list[dict[str, Any]]) -> tuple[bool, set[int]]:
    seen = False
    values: set[int] = set()
    for row in rows:
        for envelope in context_envelopes(row):
            for key in WINDOW_KEYS:
                if key not in envelope:
                    continue
                seen = True
                number = positive(envelope.get(key))
                if number:
                    values.add(number)
    return seen, values


def resolve_window(rows: list[dict[str, Any]], manifest: dict[str, Any]) -> dict[str, Any]:
    if "CARR_CONTEXT_WINDOW" in os.environ:
        number = positive(os.environ.get("CARR_CONTEXT_WINDOW"))
        if not number:
            return {"ok": False, "reason": "WINDOW_CONFIG_INVALID", "tier": "override"}
        return {"ok": True, "window": number, "tier": "override"}

    bindings = manifest.get("model_windows", {})
    models = models_in(rows)
    bound: set[int] = set()
    malformed = False
    for model in models:
        if model in bindings:
            number = positive(bindings.get(model))
            if number:
                bound.add(number)
            else:
                malformed = True
    if malformed:
        return {"ok": False, "reason": "WINDOW_CONFIG_INVALID", "tier": "model"}
    if len(bound) == 1:
        return {"ok": True, "window": next(iter(bound)), "tier": "model",
                "models": sorted(models)}
    if len(bound) > 1:
        return {"ok": False, "reason": "CONTEXT_SIGNAL_AMBIGUOUS", "tier": "model"}

    present, windows = explicit_windows(rows)
    if len(windows) == 1:
        return {"ok": True, "window": next(iter(windows)), "tier": "transcript"}
    if len(windows) > 1:
        return {"ok": False, "reason": "CONTEXT_SIGNAL_AMBIGUOUS", "tier": "transcript"}
    if present:
        return {"ok": False, "reason": "CONTEXT_SIGNAL_INVALID", "tier": "transcript"}
    compact = [number for number in (compact_pre_tokens(row) for row in rows) if number]
    if compact:
        return {"ok": True, "window": min(compact), "tier": "compact_boundary"}
    return {"ok": False, "reason": "CONTEXT_SIGNAL_UNAVAILABLE", "tier": "none",
            "models": sorted(models)}


def payload_mutated_paths(payload: dict[str, Any]) -> set[str]:
    tool = payload.get("tool_name") or payload.get("toolName")
    if tool not in MUTATING_TOOLS:
        return set()
    value = payload.get("tool_input") or payload.get("toolInput") or {}
    paths: set[str] = set()
    if isinstance(value, dict):
        for key in ("file_path", "path", "notebook_path"):
            item = value.get(key)
            if isinstance(item, str) and item:
                paths.add(os.path.abspath(os.path.expanduser(item)))
    return paths


def owner_digest(owner: dict[str, Any]) -> str:
    return digest({key: owner.get(key) for key in
                   ("id", "surface", "generation", "state", "evidence_digest",
                    "activation_init_digest", "activation_final_digest",
                    "terminal_evidence_digest", "terminal_provenance_digest")})


def fresh_signal() -> dict[str, Any]:
    return {"highwater": 0, "invocations": 0, "active_minutes": 0.0,
            "cycles": 0, "generation_tool_calls": 0,
            "mutated_paths": [], "worker_starts": 0,
            "last_observed_at": None, "notices": []}


def initial_state(task_key: str, owner_id: str, surface: str,
                  evidence_digest: str | None,
                  activation_init_digest: str) -> dict[str, Any]:
    owner = {"id": owner_id, "surface": surface, "generation": 0,
             "state": "ACTIVE", "evidence_digest": evidence_digest,
             "activation_init_digest": activation_init_digest}
    owner["ownership_digest"] = owner_digest(owner)
    return {
        "schema_version": 1, "task_key": task_key, "version": -1,
        "task_status": "ACTIVE", "generation": 0, "active_owner": owner_id,
        "owners": {owner_id: owner}, "handoff": None, "recovery_intent": None,
        "signal": fresh_signal(),
    }


def claude_owner_task_key(owner_id: str, manifest: dict[str, Any]) -> str | None:
    """Resolve a native Claude session back to its accepted lifecycle task.

    The controller callback does not carry an application-defined task key.
    Lifecycle state is therefore the single authoritative binding: a verified
    cross-surface successor already exists there with this native session id.
    """
    matches: set[str] = set()
    root = state_root(manifest)
    try:
        paths = list(root.glob("*.json"))
    except OSError as exc:
        raise LifecycleError(
            "LIFECYCLE_INVALID", f"Claude binding state cannot be listed: {exc}") from exc
    for path in paths:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise LifecycleError(
                "LIFECYCLE_INVALID", f"Claude binding state is unreadable: {exc}") from exc
        if not isinstance(value, dict):
            raise LifecycleError(
                "LIFECYCLE_INVALID", "Claude binding state is not an object")
        task_key = value.get("task_key")
        if not isinstance(task_key, str) or not task_key:
            raise LifecycleError("LIFECYCLE_INVALID",
                                 "Claude binding state lacks a task key")
        if path.resolve() != state_file(task_key, manifest).resolve():
            raise LifecycleError("LIFECYCLE_INVALID",
                                 "Claude owner binding path is inconsistent")
        validate_task_state(value, task_key, manifest)
        owners = value.get("owners")
        if not isinstance(owners, dict):
            raise LifecycleError(
                "LIFECYCLE_INVALID", "Claude binding owners are not an object")
        owner = owners.get(owner_id)
        if not isinstance(owner, dict) or owner.get("surface") != "claude":
            continue
        matches.add(task_key)
    if len(matches) > 1:
        raise LifecycleError("OWNERSHIP_DUPLICATE",
                             "Claude session is bound to multiple live tasks")
    return next(iter(matches)) if matches else None


def hook_task_key(payload: dict[str, Any],
                  manifest: dict[str, Any] | None = None) -> tuple[str, str]:
    explicit = payload.get("task_key") or os.environ.get("CARR_CONTEXT_TASK_KEY")
    session = payload.get("session_id") or payload.get("sessionId")
    if session:
        if manifest is not None:
            bound = claude_owner_task_key(str(session), manifest)
            if bound:
                if explicit and str(explicit) != bound:
                    raise LifecycleError(
                        "OWNERSHIP_MISMATCH",
                        "callback task key conflicts with authoritative Claude binding")
                return bound, str(session)
        if explicit:
            return str(explicit), str(session)
        return f"claude:{session}", str(session)
    if explicit:
        return str(explicit), str(explicit)
    transcript = payload.get("transcript_path") or payload.get("transcriptPath")
    if transcript:
        return f"claude-transcript:{digest(os.path.abspath(str(transcript)))}", "unknown"
    return "claude:unknown", "unknown"


def update_observation(current: dict[str, Any] | None, task_key: str, owner_id: str,
                       payload: dict[str, Any], rows: list[dict[str, Any]],
                       event: str, now: dt.datetime,
                       manifest: dict[str, Any]) -> dict[str, Any]:
    if current is None:
        transcript = payload.get("transcript_path") or payload.get("transcriptPath")
        callback_id = (payload.get("prompt_id") or payload.get("promptId")
                       or payload.get("tool_use_id") or payload.get("toolUseId"))
        if (transcript is not None and not isinstance(transcript, str)):
            raise LifecycleError(
                "LIFECYCLE_INVALID", "hook transcript_path must be a string")
        if not isinstance(callback_id, str) or not callback_id:
            raise LifecycleError(
                "LIFECYCLE_INVALID", "hook callback identity is missing")
        native_evidence = {
            "session_id": owner_id,
            "transcript_path": transcript,
            "controller_callback_id": callback_id,
            "status": "active",
        }
        evidence_digest = digest(native_evidence)
        packet = {
            "schema_version": 1, "task_key": task_key,
            "owner": owner_id, "surface": "claude", "generation": 0,
            "source": "claude_hook", "native_evidence": native_evidence,
            "native_evidence_digest": evidence_digest, "created_at": utc_now(),
        }
        init_digest = write_immutable(
            task_key, "initialization", packet, manifest)
        state = initial_state(
            task_key, owner_id, "claude", evidence_digest, init_digest)
    else:
        state = current
    recorded = (state.get("owners") or {}).get(owner_id)
    if recorded and recorded.get("surface") != "claude":
        raise LifecycleError(
            "OWNERSHIP_MISMATCH",
            "Claude callback cannot observe a non-Claude owner")
    if state.get("task_status") == "TERMINAL":
        return state
    # One task may still receive the draining predecessor's final Stop after a
    # successor is active.  That terminal callback must neither contaminate the
    # successor generation's counters nor advance task state.
    if state.get("active_owner") != owner_id:
        return state
    signal = state.setdefault("signal", {})
    totals = [usage_total(row) for row in rows]
    signal["highwater"] = max([positive(signal.get("highwater")) or 0]
                              + [number for number in totals if number])
    last = signal.get("last_observed_at")
    if last:
        try:
            elapsed = max(0.0, (now - parse_time(last)).total_seconds())
            cap = int(manifest["fallback_caps"]["adjacent_activity_cap_seconds"])
            signal["active_minutes"] = round(float(signal.get("active_minutes", 0.0))
                                                    + min(elapsed, cap) / 60.0, 3)
        except Exception:
            pass
    signal["last_observed_at"] = now.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    tool = payload.get("tool_name") or payload.get("toolName")
    if event == "PostToolUse":
        signal["invocations"] = int(signal.get("invocations", 0)) + 1
        signal["generation_tool_calls"] = int(signal.get("generation_tool_calls", 0)) + 1
        signal["mutated_paths"] = sorted(set(signal.get("mutated_paths") or [])
                                          | payload_mutated_paths(payload))
        if tool in WORKER_TOOLS:
            signal["worker_starts"] = int(signal.get("worker_starts", 0)) + 1
    if event == "Stop":
        signal["cycles"] = int(signal.get("cycles", 0)) + 1
    risk = payload.get("risk") or payload.get("risk_level")
    if isinstance(risk, str) and risk:
        signal["risk"] = risk.upper()
    return state


def density(signal: dict[str, Any], manifest: dict[str, Any]) -> bool:
    cfg = manifest["density"]
    return (
        signal.get("risk") in set(cfg["risk_levels"])
        or int(signal.get("generation_tool_calls", 0)) >= int(cfg["generation_tool_calls"])
        or len(set(signal.get("mutated_paths") or [])) >= int(cfg["distinct_mutated_paths"])
        or int(signal.get("worker_starts", 0)) >= int(cfg["worker_starts"])
    )


def fallback_level(signal: dict[str, Any], manifest: dict[str, Any], dense: bool) -> str | None:
    caps = manifest["fallback_caps"]
    values = (int(signal.get("invocations", 0)), float(signal.get("active_minutes", 0.0)),
              int(signal.get("cycles", 0)))
    for level in ("hard", "dense_soft" if dense else "normal_soft"):
        cap = caps[level]
        if (values[0] >= int(cap["invocations"]) or values[1] >= float(cap["active_minutes"])
                or values[2] >= int(cap["cycles"])):
            return level
    return None


def context_decision(rows: list[dict[str, Any]], state: dict[str, Any],
                     manifest: dict[str, Any]) -> dict[str, Any]:
    signal_state = state.get("signal") or {}
    window = resolve_window(rows, manifest)
    used = max([positive(signal_state.get("highwater")) or 0]
               + [number for number in (usage_total(row) for row in rows) if number])
    is_dense = density(signal_state, manifest)
    fallback = fallback_level(signal_state, manifest, is_dense)
    threshold_name = "dense_soft" if is_dense else "normal_soft"
    threshold = int(manifest["thresholds_percent"][threshold_name])
    if window.get("ok") and used:
        ratio = 100.0 * used / int(window["window"])
        hard = int(manifest["thresholds_percent"]["hard"])
        crossed = ratio >= hard or ratio >= threshold
        return {"available": True, "used": used, "window": window["window"],
                "ratio": round(ratio, 3), "ratio_label": "claude_transcript",
                "window_tier": window["tier"], "dense": is_dense,
                "threshold": hard if ratio >= hard else threshold,
                "crossed": crossed, "fallback_level": fallback,
                "reason": "CONTEXT_HANDOFF_REQUIRED" if crossed else None}
    reason = window.get("reason") or "CONTEXT_SIGNAL_UNAVAILABLE"
    control_error = reason == "WINDOW_CONFIG_INVALID"
    return {"available": False, "used": used or None, "dense": is_dense,
            "crossed": bool(fallback) or control_error,
            "fallback_level": fallback,
            "reason": (reason if control_error else
                       "CONTEXT_HANDOFF_REQUIRED" if fallback else reason),
            "signal_reason": reason, "window_tier": window.get("tier")}


def block_payload(task_key: str, version: int, signal: dict[str, Any],
                  reason_code: str = "CONTEXT_HANDOFF_REQUIRED") -> dict[str, Any]:
    reason = {"action": "REFUSE", "reason": reason_code,
              "signal": signal, "task": task_key, "version": version}
    return {"decision": "block", "reason": canonical(reason).decode("utf-8")}


def _add_notice(state: dict[str, Any] | None, notice: str) -> dict[str, Any]:
    if not state:
        raise LifecycleError("LIFECYCLE_INVALID", "state disappeared")
    signal = state.setdefault("signal", {})
    signal["notices"] = sorted(set(signal.get("notices") or []) | {notice})
    return state


def refuse_stop_on_control_error(event: str, task_key: str, reason: str,
                                 version: int = -1) -> None:
    if event != "Stop":
        return
    signal = {"available": False, "crossed": True, "reason": reason,
              "signal_reason": reason, "window_tier": "control_error"}
    print(canonical(block_payload(task_key, version, signal, reason)).decode("utf-8"))


def hook_main() -> int:
    wired_event = os.environ.get("CARR_CONTEXT_HOOK_EVENT")
    event = wired_event or "Stop"
    task_key = os.environ.get("CARR_CONTEXT_TASK_KEY", "claude:unknown")
    owner_id = "unknown"
    manifest: dict[str, Any] | None = None
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise LifecycleError(
                "LIFECYCLE_INVALID", "hook payload is not an object")
        payload_event = payload.get("hook_event_name") or payload.get("hookEventName")
        if wired_event is not None:
            if wired_event not in {"PostToolUse", "PreCompact", "Stop"}:
                raise LifecycleError(
                    "LIFECYCLE_INVALID", "wired hook event is invalid")
            event = wired_event
        else:
            event = payload_event or "Stop"
            if event not in {"PostToolUse", "PreCompact", "Stop"}:
                raise LifecycleError(
                    "LIFECYCLE_INVALID", "payload hook event is invalid")
        task_key, owner_id = hook_task_key(payload)
        rows = transcript_rows(
            payload.get("transcript_path") or payload.get("transcriptPath"))
        manifest = load_manifest()
        task_key, owner_id = hook_task_key(payload, manifest)
    except LifecycleError as exc:
        audit({"session": owner_id, "event": event,
               "action": "REFUSE" if event == "Stop" else "NOOP",
               "reason": exc.reason, "detail": exc.detail[:500]}, manifest)
        refuse_stop_on_control_error(event, task_key, exc.reason)
        return 0
    except Exception as exc:
        audit({"session": owner_id, "event": event,
               "action": "REFUSE" if event == "Stop" else "NOOP",
               "reason": "LIFECYCLE_INVALID", "detail": str(exc)[:500]}, manifest)
        refuse_stop_on_control_error(event, task_key, "LIFECYCLE_INVALID")
        return 0
    try:
        state = mutate_state(
            task_key, None,
            lambda current: update_observation(current, task_key, owner_id, payload, rows,
                                               event, dt.datetime.now(dt.timezone.utc), manifest),
            manifest,
        )
    except LifecycleError as exc:
        audit({"session": owner_id, "event": event,
               "action": "REFUSE" if event == "Stop" else "NOOP",
               "reason": exc.reason}, manifest)
        refuse_stop_on_control_error(event, task_key, exc.reason)
        return 0
    except Exception as exc:
        audit({"session": owner_id, "event": event,
               "action": "REFUSE" if event == "Stop" else "NOOP",
               "reason": "LIFECYCLE_INVALID", "detail": str(exc)[:500]}, manifest)
        refuse_stop_on_control_error(event, task_key, "LIFECYCLE_INVALID")
        return 0
    try:
        signal = context_decision(rows, state, manifest)
    except Exception as exc:
        # Persisted state is outside the hook process's trust boundary.  A
        # semantically malformed scalar must refuse Stop just like unreadable
        # JSON does; letting ValueError escape disables the only blocking seam.
        audit({"session": owner_id, "event": event,
               "action": "REFUSE" if event == "Stop" else "NOOP",
               "reason": "LIFECYCLE_INVALID", "detail": str(exc)[:500]}, manifest)
        version = state.get("version", -1)
        if isinstance(version, bool) or not isinstance(version, int):
            version = -1
        refuse_stop_on_control_error(event, task_key, "LIFECYCLE_INVALID",
                                     version)
        return 0
    audit({"session": owner_id, "event": event, "task_key": task_key,
           "version": state["version"], "caught": bool(signal.get("crossed")),
           "signal": signal}, manifest)
    if event == "PreCompact":
        return 0
    if state.get("task_status") == "TERMINAL":
        return 0
    pending = state.get("handoff") or {}
    if pending.get("state") in {"TAKEOVER_VERIFIED", "PREDECESSOR_TERMINAL"}:
        try:
            verify_handoff_state(task_key, state, manifest)
        except LifecycleError as exc:
            if event == "Stop":
                print(canonical(block_payload(
                    task_key, state["version"], signal, exc.reason)).decode("utf-8"))
            return 0
    caller = (state.get("owners") or {}).get(owner_id) or {}
    if (caller.get("surface") == "claude"
            and caller.get("state") == "TERMINAL"):
        return 0
    if (pending.get("state") in {"TAKEOVER_VERIFIED", "PREDECESSOR_TERMINAL"}
            and pending.get("predecessor") == owner_id
            and caller.get("state") in {"DRAINING", "TERMINAL"}):
        return 0
    if state.get("active_owner") != owner_id:
        if event == "Stop":
            print(canonical(block_payload(
                task_key, state["version"], signal,
                "OWNERSHIP_MISMATCH")).decode("utf-8"))
        return 0
    if event == "Stop" and signal.get("crossed"):
        reason_code = ("WINDOW_CONFIG_INVALID"
                       if signal.get("reason") == "WINDOW_CONFIG_INVALID"
                       else "CONTEXT_HANDOFF_REQUIRED")
        print(canonical(block_payload(
            task_key, state["version"], signal, reason_code)).decode("utf-8"))
        return 0
    if event == "PostToolUse" and (signal.get("crossed") or not signal.get("available")):
        notice_key = str(signal.get("reason") or signal.get("signal_reason")
                         or "CONTEXT_SIGNAL_UNAVAILABLE")
        notices = set((state.get("signal") or {}).get("notices") or [])
        if notice_key not in notices:
            try:
                mutate_state(task_key, state["version"],
                             lambda current: _add_notice(current, notice_key), manifest)
            except LifecycleError:
                return 0
            if signal.get("crossed"):
                announce("Context lifecycle threshold reached. Finish the current unit of work and complete a verified same-task handoff before the next Stop boundary.")
            else:
                announce(f"Context lifecycle signal is {signal.get('signal_reason', 'unavailable')}; fallback activity counters are active.")
    return 0


def parse_evidence(text: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
    except Exception as exc:
        raise LifecycleError("LIFECYCLE_INVALID", f"evidence JSON invalid: {exc}") from exc
    if not isinstance(value, dict):
        raise LifecycleError("LIFECYCLE_INVALID", "evidence must be an object")
    return value


def validate_native_evidence(surface: str, evidence: dict[str, Any], *,
                             expected_identity: str | None = None,
                             accept: bool = False,
                             terminal: bool = False,
                             require_live: bool = True) -> str:
    if surface == "codex":
        required = {"thread_id", "project_id", "cwd", "status", "event_id"}
        if not required.issubset(evidence) or not all(evidence.get(key) for key in required):
            raise LifecycleError("SUCCESSOR_SURFACE_INVALID", "Codex evidence fields missing")
        if expected_identity is not None and str(evidence.get("thread_id")) != expected_identity:
            raise LifecycleError("SUCCESSOR_SURFACE_INVALID",
                                 "Codex thread_id does not match lifecycle owner")
        if (require_live
                and not Path(str(evidence.get("cwd"))).expanduser().is_dir()):
            raise LifecycleError("SUCCESSOR_SURFACE_INVALID", "Codex cwd is not a live directory")
        if accept and str(evidence.get("status")).lower() not in {"active", "idle", "running"}:
            raise LifecycleError("SUCCESSOR_NOT_ACTIVE", "Codex successor is not active")
        if accept and positive(evidence.get("pinnedIndex")) is None:
            raise LifecycleError("SUCCESSOR_NOT_PINNED", "Codex successor is not pinned")
        if terminal and str(evidence.get("status")).lower() not in {"archived", "terminated", "terminal"}:
            raise LifecycleError("LIFECYCLE_INVALID", "Codex predecessor lacks terminal evidence")
    elif surface == "claude":
        required = {"session_id", "transcript_path", "controller_callback_id"}
        if not required.issubset(evidence) or not all(evidence.get(key) for key in required):
            raise LifecycleError("SUCCESSOR_SURFACE_INVALID", "Claude evidence fields missing")
        if expected_identity is not None and str(evidence.get("session_id")) != expected_identity:
            raise LifecycleError("SUCCESSOR_SURFACE_INVALID",
                                 "Claude session_id does not match lifecycle owner")
        if (require_live
                and not Path(str(evidence.get("transcript_path"))).expanduser().is_file()):
            raise LifecycleError("SUCCESSOR_SURFACE_INVALID",
                                 "Claude transcript_path is not a live file")
        if accept and str(evidence.get("status", "active")).lower() not in {"active", "idle", "running"}:
            raise LifecycleError("SUCCESSOR_NOT_ACTIVE", "Claude successor is not active")
        if terminal and str(evidence.get("status")).lower() not in {"archived", "terminated", "terminal"}:
            raise LifecycleError("LIFECYCLE_INVALID", "Claude predecessor lacks terminal evidence")
    else:
        raise LifecycleError("SUCCESSOR_SURFACE_INVALID", f"unsupported surface {surface}")
    return digest(evidence)


def validate_codex_checkout(evidence: dict[str, Any]) -> None:
    """Require a Codex owner to belong to this repository checkout."""
    try:
        Path(str(evidence.get("cwd"))).expanduser().resolve().relative_to(
            REPO.resolve())
    except (OSError, RuntimeError, ValueError) as exc:
        raise LifecycleError(
            "OWNERSHIP_MISMATCH",
            "Codex owner is outside the CARR checkout") from exc


def owner_activation_evidence(task_key: str, owner: dict[str, Any],
                              manifest: dict[str, Any]) -> dict[str, Any]:
    """Return the immutable evidence that admitted an owner generation."""
    if owner.get("generation") == 0:
        packet = read_object(
            task_key, "initialization",
            str(owner.get("activation_init_digest")), manifest)
        evidence = packet.get("native_evidence")
    else:
        verified = read_verified_final_packet(
            task_key, owner.get("activation_final_digest"), manifest)
        evidence = verified["acceptance"].get("native_evidence")
    if not isinstance(evidence, dict):
        raise LifecycleError(
            "HANDOFF_RECEIPT_INVALID",
            "owner activation evidence is missing")
    return evidence


def validate_predecessor_binding(task_key: str, state: dict[str, Any],
                                 owner_id: str, surface: str,
                                 evidence: dict[str, Any],
                                 manifest: dict[str, Any]) -> None:
    """Bind a new offer to the predecessor's immutable admission identity."""
    owner = (state.get("owners") or {}).get(owner_id)
    if not isinstance(owner, dict) or owner.get("surface") != surface:
        raise LifecycleError(
            "OWNERSHIP_MISMATCH",
            "predecessor surface does not match recorded owner")
    if surface != "codex":
        return
    validate_codex_checkout(evidence)
    admitted = owner_activation_evidence(task_key, owner, manifest)
    if (str(evidence.get("project_id", "")).strip()
            != str(admitted.get("project_id", "")).strip()):
        raise LifecycleError(
            "OWNERSHIP_MISMATCH",
            "Codex predecessor project changed after activation")
    try:
        same_cwd = (Path(str(evidence.get("cwd"))).expanduser().resolve()
                    == Path(str(admitted.get("cwd"))).expanduser().resolve())
    except (OSError, RuntimeError, ValueError):
        same_cwd = False
    if not same_cwd:
        raise LifecycleError(
            "OWNERSHIP_MISMATCH",
            "Codex predecessor cwd changed after activation")


def validate_successor_evidence(offer: dict[str, Any], successor: str,
                                evidence: dict[str, Any], *,
                                accept: bool = False,
                                require_live: bool = True) -> str:
    surface = offer["successor_surface"]
    evidence_digest = validate_native_evidence(
        surface, evidence, expected_identity=successor, accept=accept,
        require_live=require_live)
    if surface == "codex":
        validate_codex_checkout(evidence)
        trusted_project = offer.get("successor_project_id")
        if (not isinstance(trusted_project, str)
                or not trusted_project.strip()
                or str(evidence.get("project_id", "")).strip()
                != trusted_project):
            raise LifecycleError(
                "OWNERSHIP_MISMATCH",
                "Codex successor project is not predecessor-authorized")
    # A same-surface Codex handoff must stay in the project and checkout that
    # the predecessor offered. The thread id changes by design; changing the
    # project or cwd would be a different slice wearing this task_key.
    if offer.get("predecessor_surface") == surface == "codex":
        predecessor = offer.get("native_evidence") or {}
        try:
            same_cwd = (Path(str(evidence.get("cwd"))).expanduser().resolve()
                        == Path(str(predecessor.get("cwd"))).expanduser().resolve())
        except (OSError, RuntimeError):
            same_cwd = False
        if not same_cwd:
            raise LifecycleError(
                "OWNERSHIP_MISMATCH",
                "Codex successor is not in the offered project and cwd")
    return evidence_digest


def lifecycle_init(args, manifest):
    evidence = parse_evidence(args.evidence_json)
    evidence_digest = validate_native_evidence(
        args.surface, evidence, expected_identity=args.owner, accept=True)
    if args.surface == "codex":
        validate_codex_checkout(evidence)
    packet = {
        "schema_version": 1, "task_key": args.task_key,
        "owner": args.owner, "surface": args.surface, "generation": 0,
        "source": "task_init", "native_evidence": evidence,
        "native_evidence_digest": evidence_digest, "created_at": utc_now(),
    }
    init_digest = write_immutable(
        args.task_key, "initialization", packet, manifest)

    def create(current):
        if current is not None:
            raise LifecycleError("LIFECYCLE_INVALID", "task already exists")
        return initial_state(
            args.task_key, args.owner, args.surface,
            evidence_digest, init_digest)

    return mutate_state(args.task_key, args.expected_version, create, manifest)


def offer_create(args, manifest):
    if args.predecessor_surface not in SURFACES:
        raise LifecycleError("SUCCESSOR_SURFACE_INVALID",
                             f"unsupported predecessor surface {args.predecessor_surface}")
    if args.successor_surface not in SURFACES:
        raise LifecycleError("SUCCESSOR_SURFACE_INVALID",
                             f"unsupported successor surface {args.successor_surface}")
    evidence = parse_evidence(args.evidence_json)
    evidence_digest = validate_native_evidence(
        args.predecessor_surface, evidence, expected_identity=args.predecessor)
    current = read_state(args.task_key, manifest)
    if not current or current.get("task_status") != "ACTIVE":
        raise LifecycleError("LIFECYCLE_INVALID", "task is not active")
    if args.predecessor == args.successor or args.successor in current["owners"]:
        raise LifecycleError(
            "OWNERSHIP_MISMATCH",
            "successor identity was already used by this task")
    validate_predecessor_binding(
        args.task_key, current, args.predecessor,
        args.predecessor_surface, evidence, manifest)
    successor_project_id = None
    if args.successor_surface == "codex":
        project_field = ("project_id" if args.predecessor_surface == "codex"
                         else "successor_project_id")
        project_value = evidence.get(project_field)
        if not isinstance(project_value, str) or not project_value.strip():
            raise LifecycleError(
                "OWNERSHIP_MISMATCH",
                "Codex successor lacks predecessor-authorized project binding")
        successor_project_id = project_value.strip()
    offer = {"schema_version": 1, "task_key": args.task_key,
             "generation": args.generation, "predecessor": args.predecessor,
             "predecessor_surface": args.predecessor_surface,
             "successor": args.successor, "successor_surface": args.successor_surface,
             "native_evidence": evidence, "native_evidence_digest": evidence_digest,
             "created_at": utc_now()}
    if successor_project_id is not None:
        offer["successor_project_id"] = successor_project_id
    offer_digest = write_immutable(args.task_key, "offer", offer, manifest)

    def change(state):
        if not state or state.get("task_status") != "ACTIVE":
            raise LifecycleError("LIFECYCLE_INVALID", "task is not active")
        if state.get("active_owner") != args.predecessor:
            raise LifecycleError("OWNERSHIP_MISMATCH", "predecessor is not sole active owner")
        if args.predecessor == args.successor or args.successor in state["owners"]:
            raise LifecycleError(
                "OWNERSHIP_MISMATCH",
                "successor identity was already used by this task")
        validate_predecessor_binding(
            args.task_key, state, args.predecessor,
            args.predecessor_surface, evidence, manifest)
        if args.generation != int(state.get("generation", 0)) + 1:
            raise LifecycleError("LIFECYCLE_INVALID",
                                 "offer generation must be current generation plus one")
        if state.get("handoff") and state["handoff"].get("state") not in {None, "PREDECESSOR_TERMINAL"}:
            raise LifecycleError("LIFECYCLE_INVALID", "handoff already pending")
        owner = state["owners"].get(args.predecessor)
        recovery = state.get("recovery_intent") or {}
        if recovery.get("state") == "ABORTED":
            if (recovery.get("failed_owner") != args.predecessor
                    or recovery.get("generation") != state.get("generation")):
                raise LifecycleError(
                    "LIFECYCLE_INVALID",
                    "aborted recovery does not belong to the predecessor")
            state["recovery_intent"] = None
            recovery = {}
        recovering = (recovery.get("state") == "PENDING"
                      and recovery.get("failed_owner") == args.predecessor
                      and recovery.get("task_key") == args.task_key
                      and recovery.get("generation") == state.get("generation"))
        if not owner or owner.get("surface") != args.predecessor_surface:
            raise LifecycleError("OWNERSHIP_MISMATCH",
                                 "predecessor surface does not match recorded owner")
        if owner.get("state") != "ACTIVE" and not (
                recovering and owner.get("state") == "DRAINING"):
            raise LifecycleError("OWNERSHIP_MISMATCH", "predecessor owner state is not ACTIVE")
        owner["state"] = "DRAINING"
        owner["ownership_digest"] = owner_digest(owner)
        state["handoff"] = {"state": "DRAINING", "offer_digest": offer_digest,
                            "predecessor": args.predecessor, "successor": args.successor,
                            "generation": args.generation}
        return state

    state = mutate_state(args.task_key, args.expected_version, change, manifest)
    return {"offer_digest": offer_digest, "state": state}


def successor_declare(args, manifest):
    offer = read_object(args.task_key, "offer", args.offer_digest, manifest)
    evidence = parse_evidence(args.evidence_json)
    evidence_digest = validate_successor_evidence(
        offer, args.successor, evidence)
    declaration = {"schema_version": 1, "task_key": args.task_key,
                   "offer_digest": args.offer_digest, "successor": args.successor,
                   "native_evidence": evidence, "native_evidence_digest": evidence_digest,
                   "declared_at": utc_now()}
    declaration_digest = write_immutable(args.task_key, "declaration", declaration, manifest)

    def change(state):
        pending = (state or {}).get("handoff") or {}
        if pending.get("state") != "DRAINING" or pending.get("offer_digest") != args.offer_digest:
            raise LifecycleError("TAKEOVER_NOT_VERIFIED", "offer is not the pending handoff")
        if offer.get("successor") != args.successor:
            raise LifecycleError("OWNERSHIP_MISMATCH", "successor does not match offer")
        if args.successor in state["owners"]:
            raise LifecycleError(
                "OWNERSHIP_MISMATCH",
                "successor identity was already used by this task")
        successor = {"id": args.successor, "surface": offer["successor_surface"],
                     "generation": offer["generation"], "state": "SUCCESSOR_DECLARED",
                     "evidence_digest": evidence_digest}
        successor["ownership_digest"] = owner_digest(successor)
        state["owners"][args.successor] = successor
        pending.update({"state": "SUCCESSOR_DECLARED",
                        "declaration_digest": declaration_digest})
        return state

    state = mutate_state(args.task_key, args.expected_version, change, manifest)
    return {"declaration_digest": declaration_digest, "state": state}


def successor_accept(args, manifest):
    offer = read_object(args.task_key, "offer", args.offer_digest, manifest)
    evidence = parse_evidence(args.evidence_json)
    evidence_digest = validate_successor_evidence(
        offer, args.successor, evidence, accept=True)
    current = read_state(args.task_key, manifest)
    pending_now = (current or {}).get("handoff") or {}
    declaration_digest = pending_now.get("declaration_digest")
    if not declaration_digest:
        raise LifecycleError("TAKEOVER_NOT_VERIFIED", "successor declaration is absent")
    declaration = read_object(
        args.task_key, "declaration", declaration_digest, manifest)
    if (declaration.get("successor") != args.successor
            or declaration.get("native_evidence_digest") != evidence_digest
            or declaration.get("native_evidence") != evidence):
        raise LifecycleError("TAKEOVER_NOT_VERIFIED",
                             "acceptance evidence does not match declaration")
    acceptance = {"successor": args.successor, "surface": offer["successor_surface"],
                  "native_evidence": evidence, "native_evidence_digest": evidence_digest,
                  "accepted_at": utc_now()}
    receipt = {"schema_version": 1, "task_key": args.task_key,
               "offer_digest": args.offer_digest,
               "declaration_digest": declaration_digest,
               "ownership_acceptance": acceptance}
    receipt_digest = write_immutable(args.task_key, "receipt", receipt, manifest)
    final_packet = {"schema_version": 1, "offer": offer,
                    "declaration": declaration,
                    "ownership_acceptance": acceptance,
                    "offer_digest": args.offer_digest,
                    "declaration_digest": declaration_digest,
                    "receipt_digest": receipt_digest}
    final_digest = write_immutable(args.task_key, "final", final_packet, manifest)

    def change(state):
        pending = (state or {}).get("handoff") or {}
        if pending.get("state") != "SUCCESSOR_DECLARED" or pending.get("offer_digest") != args.offer_digest:
            raise LifecycleError("TAKEOVER_NOT_VERIFIED", "successor was not declared for this offer")
        if pending.get("declaration_digest") != declaration_digest:
            raise LifecycleError("TAKEOVER_NOT_VERIFIED", "successor declaration changed")
        if offer.get("successor") != args.successor:
            raise LifecycleError("OWNERSHIP_MISMATCH", "successor does not match offer")
        predecessor = state["owners"].get(offer["predecessor"])
        successor = state["owners"].get(args.successor)
        if not predecessor or predecessor.get("state") != "DRAINING":
            raise LifecycleError("OWNERSHIP_MISMATCH", "predecessor is not draining")
        if not successor or successor.get("state") != "SUCCESSOR_DECLARED":
            raise LifecycleError("SUCCESSOR_NOT_ACTIVE", "successor declaration is absent")
        successor.update({"state": "ACTIVE", "evidence_digest": evidence_digest,
                          "activation_final_digest": final_digest})
        successor["ownership_digest"] = owner_digest(successor)
        state["active_owner"] = args.successor
        state["generation"] = int(offer["generation"])
        state["signal"] = fresh_signal()
        recovery = state.get("recovery_intent") or {}
        if (recovery.get("state") == "PENDING"
                and recovery.get("failed_owner") == offer.get("predecessor")
                and recovery.get("task_key") == args.task_key
                and recovery.get("generation") == int(offer["generation"]) - 1):
            recovery.update({"state": "COMPLETED", "successor": args.successor,
                             "completed_at": utc_now()})
        pending.update({"state": "TAKEOVER_VERIFIED", "receipt_digest": receipt_digest,
                        "final_digest": final_digest})
        return state

    state = mutate_state(args.task_key, args.expected_version, change, manifest)
    return {"receipt_digest": receipt_digest, "final_digest": final_digest, "state": state}


def read_verified_final_packet(task_key: str, final_digest: Any,
                               manifest: dict[str, Any] | None = None
                               ) -> dict[str, Any]:
    """Validate a self-contained immutable handoff independently of live state."""
    if not is_digest(final_digest):
        raise LifecycleError("HANDOFF_RECEIPT_MISSING", "final_digest missing")
    final = read_object(task_key, "final", str(final_digest), manifest)
    digests = {
        key: final.get(key)
        for key in ("offer_digest", "declaration_digest", "receipt_digest")
    }
    for key, value in digests.items():
        if not is_digest(value):
            raise LifecycleError("HANDOFF_RECEIPT_MISSING", f"{key} missing")
    offer_digest = str(digests["offer_digest"])
    declaration_digest = str(digests["declaration_digest"])
    receipt_digest = str(digests["receipt_digest"])
    offer = read_object(task_key, "offer", offer_digest, manifest)
    declaration = read_object(
        task_key, "declaration", declaration_digest, manifest)
    receipt = read_object(
        task_key, "receipt", receipt_digest, manifest)
    acceptance = receipt.get("ownership_acceptance") or {}
    acceptance_evidence = acceptance.get("native_evidence") or {}
    offer_evidence = offer.get("native_evidence") or {}
    successor_project_id = offer.get("successor_project_id")
    offer_generation = offer.get("generation")
    if (final.get("schema_version") != 1
            or offer.get("schema_version") != 1
            or offer.get("task_key") != task_key
            or offer.get("predecessor_surface") not in SURFACES
            or offer.get("successor_surface") not in SURFACES
            or not isinstance(offer.get("predecessor"), str)
            or not offer.get("predecessor")
            or not isinstance(offer.get("successor"), str)
            or not offer.get("successor")
            or offer.get("predecessor") == offer.get("successor")
            or isinstance(offer_generation, bool)
            or not isinstance(offer_generation, int)
            or offer_generation < 1
            or not isinstance(offer_evidence, dict)
            or digest(offer_evidence) != offer.get("native_evidence_digest")
            or declaration.get("schema_version") != 1
            or declaration.get("task_key") != task_key
            or declaration.get("offer_digest") != digests["offer_digest"]
            or declaration.get("successor") != offer.get("successor")
            or receipt.get("schema_version") != 1
            or receipt.get("task_key") != task_key
            or receipt.get("offer_digest") != digests["offer_digest"]
            or receipt.get("declaration_digest")
            != digests["declaration_digest"]
            or acceptance.get("successor") != offer.get("successor")
            or acceptance.get("surface") != offer.get("successor_surface")
            or final.get("offer") != offer
            or final.get("declaration") != declaration
            or final.get("ownership_acceptance") != acceptance
            or declaration.get("native_evidence_digest")
            != acceptance.get("native_evidence_digest")
            or declaration.get("native_evidence")
            != acceptance.get("native_evidence")
            or not isinstance(acceptance_evidence, dict)
            or digest(acceptance_evidence)
            != acceptance.get("native_evidence_digest")
            or (offer.get("successor_surface") == "codex"
                and (not isinstance(successor_project_id, str)
                     or not successor_project_id.strip()
                     or str(acceptance_evidence.get("project_id", "")).strip()
                     != successor_project_id))):
        raise LifecycleError("HANDOFF_RECEIPT_INVALID",
                             "offer/receipt/final linkage is invalid")
    try:
        predecessor_evidence_digest = validate_native_evidence(
            str(offer.get("predecessor_surface")), offer_evidence,
            expected_identity=str(offer.get("predecessor")),
            require_live=False)
        successor_evidence_digest = validate_successor_evidence(
            offer, str(offer.get("successor")), acceptance_evidence,
            accept=True, require_live=False)
    except LifecycleError as exc:
        raise LifecycleError(
            "HANDOFF_RECEIPT_INVALID",
            f"handoff native evidence is invalid: {exc.detail}") from exc
    if (predecessor_evidence_digest != offer.get("native_evidence_digest")
            or successor_evidence_digest
            != acceptance.get("native_evidence_digest")):
        raise LifecycleError(
            "HANDOFF_RECEIPT_INVALID",
            "handoff native evidence digest changed")
    return {"offer": offer, "declaration": declaration,
            "receipt": receipt, "final": final, "acceptance": acceptance,
            **digests, "final_digest": final_digest}


def verify_owner_activation(task_key: str, owner: dict[str, Any],
                            manifest: dict[str, Any] | None = None) -> None:
    verified = read_verified_final_packet(
        task_key, owner.get("activation_final_digest"), manifest)
    offer = verified["offer"]
    acceptance = verified["acceptance"]
    if (offer.get("successor") != owner.get("id")
            or offer.get("successor_surface") != owner.get("surface")
            or offer.get("generation") != owner.get("generation")
            or acceptance.get("native_evidence_digest")
            != owner.get("evidence_digest")):
        raise LifecycleError(
            "HANDOFF_RECEIPT_INVALID",
            "owner activation is not linked to immutable acceptance")


def verify_handoff_state(task_key: str, state: dict[str, Any],
                         manifest: dict[str, Any] | None = None
                         ) -> dict[str, Any]:
    pending = state.get("handoff") or {}
    if pending.get("state") not in {"TAKEOVER_VERIFIED", "PREDECESSOR_TERMINAL"}:
        raise LifecycleError("TAKEOVER_NOT_VERIFIED", "takeover is not verified")
    for key in ("offer_digest", "declaration_digest", "receipt_digest", "final_digest"):
        if not pending.get(key):
            raise LifecycleError("HANDOFF_RECEIPT_MISSING", f"{key} missing")
    verified = read_verified_final_packet(
        task_key, pending["final_digest"], manifest)
    offer = verified["offer"]
    acceptance = verified["acceptance"]
    owners = state.get("owners") or {}
    predecessor_owner = owners.get(pending.get("predecessor")) or {}
    successor_owner = owners.get(pending.get("successor")) or {}
    if (verified["offer_digest"] != pending.get("offer_digest")
            or verified["declaration_digest"]
            != pending.get("declaration_digest")
            or verified["receipt_digest"] != pending.get("receipt_digest")
            or offer.get("generation") != pending.get("generation")
            or offer.get("predecessor") != pending.get("predecessor")
            or offer.get("successor") != pending.get("successor")
            or offer.get("predecessor_surface")
            != predecessor_owner.get("surface")
            or offer.get("successor_surface") != successor_owner.get("surface")
            or successor_owner.get("id") != offer.get("successor")
            or successor_owner.get("generation") != offer.get("generation")
            or successor_owner.get("evidence_digest")
            != acceptance.get("native_evidence_digest")):
        raise LifecycleError("HANDOFF_RECEIPT_INVALID",
                             "offer/receipt/final linkage is invalid")
    return {key: verified[key] for key in
            ("offer_digest", "declaration_digest", "receipt_digest", "final_digest")}


def predecessor_terminal(args, manifest):
    evidence = parse_evidence(args.evidence_json)

    def change(state):
        pending = (state or {}).get("handoff") or {}
        if pending.get("state") != "TAKEOVER_VERIFIED" or pending.get("predecessor") != args.predecessor:
            raise LifecycleError("TAKEOVER_NOT_VERIFIED", "takeover is not verified")
        owner = state["owners"].get(args.predecessor)
        if not owner or owner.get("state") != "DRAINING":
            raise LifecycleError("OWNERSHIP_MISMATCH", "predecessor is not draining")
        verify_handoff_state(args.task_key, state, manifest)
        evidence_digest = validate_native_evidence(
            owner["surface"], evidence,
            expected_identity=args.predecessor, terminal=True)
        terminal_packet = {
            "schema_version": 1, "task_key": args.task_key,
            "terminal_kind": "predecessor_terminal",
            "owner": args.predecessor, "surface": owner["surface"],
            "generation": owner["generation"],
            "native_evidence": evidence,
            "native_evidence_digest": evidence_digest,
            "handoff_final_digest": pending.get("final_digest"),
            "created_at": utc_now(),
        }
        provenance_digest = write_immutable(
            args.task_key, "terminal", terminal_packet, manifest)
        owner.update({"state": "TERMINAL",
                      "terminal_evidence_digest": evidence_digest,
                      "terminal_provenance_digest": provenance_digest})
        owner["ownership_digest"] = owner_digest(owner)
        pending["state"] = "PREDECESSOR_TERMINAL"
        return state

    return mutate_state(args.task_key, args.expected_version, change, manifest)


def task_terminal(args, manifest):
    evidence = parse_evidence(args.evidence_json)

    def change(state):
        if not state or state.get("task_status") == "TERMINAL":
            raise LifecycleError("RECOVERY_TERMINAL", "task is already terminal")
        if state.get("active_owner") != args.owner:
            raise LifecycleError("OWNERSHIP_MISMATCH", "only sole active owner may terminate task")
        active = [owner for owner in state.get("owners", {}).values()
                  if owner.get("state") == "ACTIVE"]
        if len(active) != 1:
            raise LifecycleError("OWNERSHIP_DUPLICATE", "task lacks exactly one active owner")
        handoff = state.get("handoff") or {}
        if handoff.get("state") in {"DRAINING", "SUCCESSOR_DECLARED", "TAKEOVER_VERIFIED"}:
            raise LifecycleError("TAKEOVER_NOT_VERIFIED", "handoff is incomplete")
        evidence_digest = validate_native_evidence(
            active[0]["surface"], evidence,
            expected_identity=args.owner, terminal=True)
        terminal_packet = {
            "schema_version": 1, "task_key": args.task_key,
            "terminal_kind": "task_terminal",
            "owner": args.owner, "surface": active[0]["surface"],
            "generation": active[0]["generation"],
            "native_evidence": evidence,
            "native_evidence_digest": evidence_digest,
            "handoff_final_digest": (handoff.get("final_digest")
                                     if handoff.get("state")
                                     == "PREDECESSOR_TERMINAL" else None),
            "created_at": utc_now(),
        }
        provenance_digest = write_immutable(
            args.task_key, "terminal", terminal_packet, manifest)
        active[0].update({"state": "TERMINAL",
                          "terminal_evidence_digest": evidence_digest,
                          "terminal_provenance_digest": provenance_digest})
        active[0]["ownership_digest"] = owner_digest(active[0])
        state["task_status"] = "TERMINAL"
        state["active_owner"] = None
        return state

    return mutate_state(args.task_key, args.expected_version, change, manifest)


def resolve_codex_rollout(path: str) -> dict[str, Any]:
    rows = transcript_rows(path)
    authoritative: set[str] = set()
    legacy: set[str] = set()
    turns: set[str] = set()
    for row in rows:
        raw_payload = row.get("payload")
        payload: dict[str, Any] = raw_payload if isinstance(raw_payload, dict) else {}
        if row.get("type") == "session_meta":
            if payload.get("id"):
                authoritative.add(str(payload["id"]))
            if payload.get("session_id"):
                legacy.add(str(payload["session_id"]))
        if (row.get("type") == "event_msg" and payload.get("type") == "task_started"
                and payload.get("turn_id")):
            turns.add(str(payload["turn_id"]))
    if len(authoritative) == 1:
        value = next(iter(authoritative))
        return {"ok": True, "task_key": f"codex:{value}", "resolver": "payload.id",
                "ignored_lineage": sorted(item for item in legacy if item != value)}
    if len(authoritative) > 1:
        return {"ok": False, "reason": "CONTEXT_SIGNAL_AMBIGUOUS", "resolver": "payload.id"}
    if len(legacy) == 1:
        return {"ok": True, "task_key": f"codex:{next(iter(legacy))}", "resolver": "session_id"}
    if len(legacy) > 1:
        return {"ok": False, "reason": "CONTEXT_SIGNAL_AMBIGUOUS", "resolver": "session_id"}
    if len(turns) == 1:
        return {"ok": True, "task_key": f"codex-turn:{next(iter(turns))}",
                "resolver": "task_started.turn_id"}
    return {"ok": False,
            "reason": "CONTEXT_SIGNAL_AMBIGUOUS" if len(turns) > 1
            else "CONTEXT_SIGNAL_UNAVAILABLE",
            "resolver": "task_started.turn_id"}


def codex_rollout_metrics(path: str, manifest: dict[str, Any]) -> dict[str, Any]:
    rows = transcript_rows(path)
    timestamps: list[dt.datetime] = []
    invocations = cycles = worker_starts = 0
    mutated: set[str] = set()
    for row in rows:
        try:
            timestamps.append(parse_time(row.get("timestamp")))
        except Exception:
            pass
        raw_payload = row.get("payload")
        payload: dict[str, Any] = raw_payload if isinstance(raw_payload, dict) else {}
        kind = payload.get("type")
        if (row.get("type") == "response_item"
                and kind in {"custom_tool_call", "function_call", "mcp_tool_call"}):
            invocations += 1
            name = payload.get("name") or ""
            if name in WORKER_TOOLS:
                worker_starts += 1
            args = payload.get("arguments") or payload.get("input")
            if name in MUTATING_TOOLS and isinstance(args, dict):
                for key in ("path", "file_path"):
                    if isinstance(args.get(key), str):
                        mutated.add(os.path.abspath(os.path.expanduser(args[key])))
        if row.get("type") == "event_msg" and kind == "task_started":
            cycles += 1
    timestamps.sort()
    cap = int(manifest["fallback_caps"]["adjacent_activity_cap_seconds"])
    active_seconds = sum(min(max(0.0, (b - a).total_seconds()), cap)
                         for a, b in zip(timestamps, timestamps[1:]))
    highwater = max([number for number in
                     (usage_total(row) for row in rows) if number] or [0])
    signal = {"highwater": highwater, "invocations": invocations,
              "active_minutes": round(active_seconds / 60.0, 3), "cycles": cycles,
              "generation_tool_calls": invocations, "mutated_paths": sorted(mutated),
              "worker_starts": worker_starts}
    is_dense = density(signal, manifest)
    fallback = fallback_level(signal, manifest, is_dense)
    window = resolve_window(rows, manifest)
    result = {"ratio_label": "internal_rollout", "signal": signal, "dense": is_dense,
              "fallback_level": fallback, "window": window}
    if window.get("ok") and highwater:
        result["ratio"] = round(100.0 * highwater / int(window["window"]), 3)
    return result


SNAPSHOT_KEYS = {"task_key", "observed_at", "source_event_id", "observer_id",
                 "source_surface", "evidence_ref", "evidence_digest", "workers"}
WORKER_KEYS = {"id", "surface", "generation", "normalized_state", "last_progress_at",
               "task_terminal", "attention_kind", "error_code", "recoverable",
               "capacity_code", "ownership_digest"}


def validate_snapshot(snapshot: Any, task_key: str,
                      manifest: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(snapshot, dict) or set(snapshot) != SNAPSHOT_KEYS:
        raise LifecycleError("RECOVERY_SNAPSHOT_INVALID",
                             "dispatcher snapshot keys are not exact")
    if snapshot.get("task_key") != task_key:
        raise LifecycleError("OWNERSHIP_MISMATCH", "snapshot task_key mismatch")
    for field in ("source_event_id", "observer_id", "source_surface",
                  "evidence_ref"):
        if not isinstance(snapshot.get(field), str) or not snapshot[field].strip():
            raise LifecycleError("RECOVERY_SNAPSHOT_INVALID",
                                 f"{field} must be a non-empty string")
    if snapshot.get("source_surface") not in SURFACES:
        raise LifecycleError("RECOVERY_SNAPSHOT_INVALID",
                             "source_surface is invalid")
    evidence = {key: snapshot.get(key) for key in SNAPSHOT_KEYS
                if key != "evidence_digest"}
    if (not is_digest(snapshot.get("evidence_digest"))
            or digest(evidence) != snapshot.get("evidence_digest")):
        raise LifecycleError("RECOVERY_SNAPSHOT_INVALID",
                             "snapshot evidence digest mismatch")
    now = dt.datetime.now(dt.timezone.utc)
    try:
        observed = parse_time(snapshot["observed_at"])
    except Exception as exc:
        raise LifecycleError("RECOVERY_SNAPSHOT_INVALID",
                             f"observed_at invalid: {exc}") from exc
    age = (now - observed).total_seconds()
    if age < 0:
        raise LifecycleError("RECOVERY_SNAPSHOT_INVALID", "future snapshot")
    if age > int(manifest["dispatcher"]["freshness_seconds"]):
        raise LifecycleError("RECOVERY_SNAPSHOT_STALE", "snapshot is stale")
    workers = snapshot.get("workers")
    if not isinstance(workers, list):
        raise LifecycleError("RECOVERY_SNAPSHOT_INVALID", "workers must be an array")
    for worker in workers:
        if not isinstance(worker, dict) or set(worker) != WORKER_KEYS:
            raise LifecycleError("RECOVERY_SNAPSHOT_INVALID", "worker keys are not exact")
        worker_generation = worker.get("generation")
        if (not isinstance(worker.get("id"), str) or not worker["id"].strip()
                or worker.get("surface") not in SURFACES
                or isinstance(worker_generation, bool)
                or not isinstance(worker_generation, int)
                or worker_generation < 0
                or not isinstance(worker.get("task_terminal"), bool)
                or not isinstance(worker.get("recoverable"), bool)
                or not is_digest(worker.get("ownership_digest"))):
            raise LifecycleError("RECOVERY_SNAPSHOT_INVALID",
                                 "worker identity fields are invalid")
        if worker.get("normalized_state") not in WORKER_STATES:
            raise LifecycleError("RECOVERY_SNAPSHOT_INVALID", "worker state invalid")
        attention = worker.get("attention_kind")
        if (not isinstance(attention, str) or not attention
                or attention not in ({"NONE"} | set(
                    manifest["dispatcher"]["waiting_attention_kinds"]))):
            raise LifecycleError("RECOVERY_SNAPSHOT_INVALID",
                                 "worker attention kind is invalid")
        for field in ("error_code", "capacity_code"):
            if (worker.get(field) is not None
                    and (not isinstance(worker.get(field), str)
                         or not worker[field])):
                raise LifecycleError("RECOVERY_SNAPSHOT_INVALID",
                                     f"worker {field} is invalid")
        if ((worker.get("normalized_state") == "TERMINAL")
                != worker.get("task_terminal")):
            raise LifecycleError("RECOVERY_SNAPSHOT_INVALID",
                                 "worker terminal evidence is inconsistent")
        try:
            last_progress = parse_time(worker.get("last_progress_at"))
        except Exception as exc:
            raise LifecycleError("RECOVERY_SNAPSHOT_INVALID",
                                 f"last_progress_at invalid: {exc}") from exc
        if (last_progress - now).total_seconds() > int(
                manifest["dispatcher"]["future_skew_seconds"]):
            raise LifecycleError("RECOVERY_SNAPSHOT_INVALID",
                                 "worker last_progress_at is in the future")
    return workers


def dispatcher_decision(task_key: str, snapshot: dict[str, Any],
                        state: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    workers = validate_snapshot(snapshot, task_key, manifest)
    if state.get("task_status") == "TERMINAL":
        return {"action": "NOOP", "reason": "RECOVERY_TERMINAL"}
    active_id = state.get("active_owner")
    if not active_id:
        raise LifecycleError("OWNERSHIP_MISSING", "task has no active owner")
    expected = (state.get("owners") or {}).get(active_id, {}).get("ownership_digest")
    matches = [worker for worker in workers
               if worker.get("ownership_digest") == expected]
    if not matches:
        raise LifecycleError("OWNERSHIP_MISSING", "snapshot has no current owner")
    if len(matches) > 1:
        raise LifecycleError("OWNERSHIP_DUPLICATE",
                             "snapshot has duplicate current owners")
    worker = matches[0]
    if worker.get("id") != active_id:
        raise LifecycleError("OWNERSHIP_MISMATCH", "snapshot owner id mismatch")
    recorded = (state.get("owners") or {}).get(active_id) or {}
    if (worker.get("surface") != recorded.get("surface")
            or worker.get("generation") != recorded.get("generation")):
        raise LifecycleError("OWNERSHIP_MISMATCH",
                             "snapshot owner surface or generation mismatch")
    state_name = worker["normalized_state"]
    waiting = set(manifest["dispatcher"]["waiting_attention_kinds"])
    capacity = set(manifest["dispatcher"]["recoverable_capacity_codes"])
    if state_name == "TERMINAL" or worker.get("task_terminal"):
        return {"action": "NOOP", "reason": "RECOVERY_TERMINAL",
                "failed_owner": active_id}
    if (state_name == "CAPACITY_EXHAUSTED"
            and worker.get("capacity_code") in capacity):
        return {"action": "RECOVER_SAME_TASK", "reason": "RECOVERY_CAPACITY",
                "failed_owner": active_id}
    if state_name == "ERROR" and worker.get("recoverable") is True:
        return {"action": "RECOVER_SAME_TASK", "reason": "RECOVERY_ERROR",
                "failed_owner": active_id}
    if state_name == "IDLE" and worker.get("attention_kind") in {None, "NONE"}:
        return {"action": "RECOVER_SAME_TASK", "reason": "RECOVERY_IDLE",
                "failed_owner": active_id}
    if (state_name == "WAITING_ATTENTION"
            and worker.get("attention_kind") in waiting):
        return {"action": "NOOP", "reason": "RECOVERY_WAITING",
                "failed_owner": active_id}
    if state_name == "RUNNING":
        heartbeat_age = (dt.datetime.now(dt.timezone.utc)
                         - parse_time(worker["last_progress_at"])).total_seconds()
        if heartbeat_age > int(manifest["dispatcher"]["heartbeat_max_seconds"]):
            return {"action": "RECOVER_SAME_TASK", "reason": "RECOVERY_IDLE",
                    "failed_owner": active_id}
        return {"action": "NOOP", "reason": "RECOVERY_ACTIVE",
                "failed_owner": active_id}
    raise LifecycleError("RECOVERY_SNAPSHOT_INVALID",
                         "worker condition has no valid predicate")


def dispatcher_evaluate(args, manifest):
    state = read_state(args.task_key, manifest)
    if not state:
        raise LifecycleError("OWNERSHIP_MISSING", "task state missing")
    snapshot = parse_evidence(args.snapshot_json)
    validate_snapshot(snapshot, args.task_key, manifest)
    pending = state.get("recovery_intent")
    if (pending and pending.get("state") in {"ABORTED", "COMPLETED"}
            and pending.get("source_event_id") == snapshot.get("source_event_id")):
        raise LifecycleError(
            "LIFECYCLE_INVALID", "recovery source event was already consumed")
    if pending and pending.get("state") == "PENDING":
        same = (
            pending.get("task_key") == args.task_key
            and pending.get("generation") == state.get("generation")
            and pending.get("source_event_id") == snapshot.get("source_event_id")
            and pending.get("snapshot_digest") == snapshot.get("evidence_digest")
        )
        if not same:
            raise LifecycleError("LIFECYCLE_INVALID",
                                 "recovery replay evidence changed")
        replay_state = state
        if args.apply:
            if args.expected_version is None:
                raise LifecycleError("LIFECYCLE_INVALID",
                                     "--apply requires --expected-version")

            def verify_replay(current):
                current_pending = (current or {}).get("recovery_intent") or {}
                if (current_pending.get("state") != "PENDING"
                        or current_pending.get("nonce") != pending.get("nonce")
                        or current_pending.get("snapshot_digest")
                        != snapshot.get("evidence_digest")):
                    raise LifecycleError("LIFECYCLE_INVALID",
                                         "recovery replay state changed")
                return current

            replay_state = mutate_state(
                args.task_key, args.expected_version, verify_replay, manifest)
        return {
            "action": "RECOVER_SAME_TASK",
            "reason": pending["cause"],
            "failed_owner": pending["failed_owner"],
            "nonce": pending["nonce"],
            "applied": bool(args.apply),
            "replay": True,
            **({"state": replay_state} if args.apply else {}),
        }
    decision = dispatcher_decision(args.task_key, snapshot, state, manifest)
    if decision["action"] != "RECOVER_SAME_TASK":
        return {**decision, "applied": False}
    handoff = state.get("handoff") or {}
    if handoff.get("state") in {
            "DRAINING", "SUCCESSOR_DECLARED", "TAKEOVER_VERIFIED"}:
        raise LifecycleError(
            "LIFECYCLE_INVALID",
            "recovery cannot start while a prior handoff is nonterminal")
    nonce_input = {"task_key": args.task_key, "generation": state.get("generation"),
                   "failed_owner": decision["failed_owner"],
                   "cause": decision["reason"],
                   "source_event_id": snapshot["source_event_id"],
                   "snapshot_digest": snapshot["evidence_digest"]}
    nonce = digest(nonce_input)
    result = {**decision, "nonce": nonce, "applied": False, "replay": False}
    if not args.apply:
        return result
    if args.expected_version is None:
        raise LifecycleError("LIFECYCLE_INVALID",
                             "--apply requires --expected-version")

    replay = False

    def change(current):
        nonlocal replay
        if not current or current.get("task_status") == "TERMINAL":
            raise LifecycleError("RECOVERY_TERMINAL",
                                 "terminal task cannot recover")
        pending = current.get("recovery_intent") or {}
        if pending.get("state") == "PENDING":
            if pending.get("nonce") == nonce:
                replay = True
                return current
            raise LifecycleError("LIFECYCLE_INVALID",
                                 "another recovery intent is pending")
        owner = current["owners"].get(decision["failed_owner"])
        if not owner or owner.get("state") != "ACTIVE":
            raise LifecycleError("OWNERSHIP_MISMATCH",
                                 "old owner is not ACTIVE")
        prior_handoff = current.get("handoff") or {}
        if prior_handoff.get("state") == "PREDECESSOR_TERMINAL":
            if prior_handoff.get("successor") != decision["failed_owner"]:
                raise LifecycleError(
                    "LIFECYCLE_INVALID",
                    "completed handoff successor does not match recovery owner")
            current["handoff"] = None
        owner["state"] = "DRAINING"
        owner["ownership_digest"] = owner_digest(owner)
        current["recovery_intent"] = {**nonce_input, "nonce": nonce,
                                      "state": "PENDING",
                                      "created_at": utc_now()}
        return current

    updated = mutate_state(args.task_key, args.expected_version, change, manifest)
    result.update({"applied": True, "replay": replay, "state": updated})
    return result


def recovery_abort(args, manifest):
    def change(state):
        if not state or state.get("task_status") == "TERMINAL":
            raise LifecycleError("RECOVERY_TERMINAL", "terminal task cannot recover")
        pending = state.get("recovery_intent") or {}
        if (pending.get("state") != "PENDING"
                or pending.get("nonce") != args.nonce
                or pending.get("failed_owner") != args.owner):
            raise LifecycleError("LIFECYCLE_INVALID", "pending recovery does not match abort")
        handoff = state.get("handoff") or {}
        if handoff.get("state") not in {None, "PREDECESSOR_TERMINAL"}:
            raise LifecycleError("LIFECYCLE_INVALID", "recovery handoff already started")
        owner = (state.get("owners") or {}).get(args.owner)
        if not owner or owner.get("state") != "DRAINING":
            raise LifecycleError("OWNERSHIP_MISMATCH", "recovering owner is not DRAINING")
        owner["state"] = "ACTIVE"
        owner["ownership_digest"] = owner_digest(owner)
        state["active_owner"] = args.owner
        pending.update({"state": "ABORTED", "aborted_at": utc_now()})
        return state

    return mutate_state(args.task_key, args.expected_version, change, manifest)


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command")
    status = sub.add_parser("status")
    status.add_argument("--task-key", required=True)
    key = sub.add_parser("codex-task-key")
    key.add_argument("--rollout", required=True)
    generic_key = sub.add_parser("task-key")
    generic_key.add_argument("--surface", choices=("codex", "claude"),
                             required=True)
    generic_key.add_argument("--rollout")
    generic_key.add_argument("--session-id")
    generic_key.add_argument("--transcript")
    observe = sub.add_parser("codex-observe")
    observe.add_argument("--rollout", required=True)

    init = sub.add_parser("task-init")
    init.add_argument("--task-key", required=True)
    init.add_argument("--owner", required=True)
    init.add_argument("--surface", choices=("codex", "claude"), required=True)
    init.add_argument("--evidence-json", required=True)
    init.add_argument("--expected-version", type=int, required=True)

    offer = sub.add_parser("handoff-offer-create")
    for name in ("task-key", "predecessor", "predecessor-surface",
                 "successor", "successor-surface", "evidence-json"):
        offer.add_argument(f"--{name}", required=True)
    offer.add_argument("--generation", type=int, required=True)
    offer.add_argument("--expected-version", type=int, required=True)

    declare = sub.add_parser("successor-declare")
    for name in ("task-key", "offer-digest", "successor", "evidence-json"):
        declare.add_argument(f"--{name}", required=True)
    declare.add_argument("--expected-version", type=int, required=True)

    accept = sub.add_parser("successor-accept")
    for name in ("task-key", "offer-digest", "successor", "evidence-json"):
        accept.add_argument(f"--{name}", required=True)
    accept.add_argument("--expected-version", type=int, required=True)

    pred = sub.add_parser("predecessor-terminal")
    pred.add_argument("--task-key", required=True)
    pred.add_argument("--predecessor", required=True)
    pred.add_argument("--evidence-json", required=True)
    pred.add_argument("--expected-version", type=int, required=True)
    verify = sub.add_parser("verify-handoff")
    verify.add_argument("--task-key", required=True)

    terminal = sub.add_parser("task-terminal")
    terminal.add_argument("--task-key", required=True)
    terminal.add_argument("--owner", required=True)
    terminal.add_argument("--evidence-json", required=True)
    terminal.add_argument("--expected-version", type=int, required=True)

    dispatch = sub.add_parser("dispatcher-evaluate")
    dispatch.add_argument("--task-key", required=True)
    dispatch.add_argument("--snapshot-json", required=True)
    dispatch.add_argument("--apply", action="store_true")
    dispatch.add_argument("--expected-version", type=int)
    abort = sub.add_parser("recovery-abort")
    abort.add_argument("--task-key", required=True)
    abort.add_argument("--owner", required=True)
    abort.add_argument("--nonce", required=True)
    abort.add_argument("--expected-version", type=int, required=True)
    return p


def cli_main(argv: list[str]) -> int:
    args = parser().parse_args(argv)
    try:
        manifest = load_manifest()
        if args.command == "status":
            result = read_state(args.task_key, manifest)
        elif args.command == "codex-task-key":
            result = resolve_codex_rollout(args.rollout)
        elif args.command == "task-key":
            if args.surface == "codex":
                if not args.rollout:
                    raise LifecycleError("CONTEXT_SIGNAL_UNAVAILABLE",
                                         "Codex task-key needs --rollout")
                result = resolve_codex_rollout(args.rollout)
            elif args.session_id:
                result = {"ok": True,
                          "task_key": f"claude:{args.session_id}",
                          "resolver": "session_id"}
            elif args.transcript:
                result = {
                    "ok": True,
                    "task_key": "claude-transcript:"
                    + digest(os.path.abspath(args.transcript)),
                    "resolver": "transcript_path",
                }
            else:
                raise LifecycleError("CONTEXT_SIGNAL_UNAVAILABLE",
                                     "Claude task-key needs session or transcript")
        elif args.command == "codex-observe":
            result = {"task": resolve_codex_rollout(args.rollout),
                      "context": codex_rollout_metrics(args.rollout, manifest)}
        elif args.command == "task-init":
            result = lifecycle_init(args, manifest)
        elif args.command == "handoff-offer-create":
            result = offer_create(args, manifest)
        elif args.command == "successor-declare":
            result = successor_declare(args, manifest)
        elif args.command == "successor-accept":
            result = successor_accept(args, manifest)
        elif args.command == "predecessor-terminal":
            result = predecessor_terminal(args, manifest)
        elif args.command == "verify-handoff":
            state = read_state(args.task_key, manifest)
            if not state:
                raise LifecycleError("OWNERSHIP_MISSING", "task state missing")
            result = verify_handoff_state(args.task_key, state, manifest)
        elif args.command == "task-terminal":
            result = task_terminal(args, manifest)
        elif args.command == "dispatcher-evaluate":
            result = dispatcher_evaluate(args, manifest)
        elif args.command == "recovery-abort":
            result = recovery_abort(args, manifest)
        else:
            parser().print_help(sys.stderr)
            return 2
        print(canonical(result).decode("utf-8"))
        return 0
    except LifecycleError as exc:
        print(canonical({"action": "REFUSE", "reason": exc.reason,
                         "detail": exc.detail}).decode("utf-8"))
        return 2


def main() -> int:
    return cli_main(sys.argv[1:]) if len(sys.argv) > 1 else hook_main()


if __name__ == "__main__":
    raise SystemExit(main())
