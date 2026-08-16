"""Read-only local scheduler cutover evidence contracts.

This module deliberately prepares and verifies evidence only.  It has no
launchctl invocation, database dependency, or provider adapter; a human-only
schedule retirement verb remains the only place an actual disable may occur.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from collections.abc import Callable
from typing import Any

CONTRACT = "control-plane-scheduler-cutover"
SCHEMA_VERSION = 1


class CutoverRefusal(ValueError):
    """A cutover precondition was absent or cannot be proven locally."""


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _utc(value: Any) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise CutoverRefusal("observation timestamp must be an RFC3339 UTC Z string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CutoverRefusal("observation timestamp is malformed") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise CutoverRefusal("observation timestamp must be UTC")
    return parsed


def _now(value: datetime | None) -> datetime:
    return (value or datetime.now(timezone.utc)).astimezone(timezone.utc)


def validate_registry(registry: Any, *, manifest: Any = None) -> list[str]:
    if not isinstance(registry, dict):
        return ["registry must be an object"]
    if registry.get("schema_version") != SCHEMA_VERSION:
        return ["registry schema_version must be 1"]
    if registry.get("contract") != CONTRACT:
        return ["registry contract is wrong"]
    age = registry.get("observation_max_age_seconds")
    if not isinstance(age, int) or age < 1 or age > 3600:
        return ["registry observation_max_age_seconds must be 1..3600"]
    surfaces = registry.get("surfaces")
    if not isinstance(surfaces, list) or not surfaces:
        return ["registry surfaces must be a non-empty array"]
    seen: set[str] = set()
    errors: list[str] = []
    for index, surface in enumerate(surfaces):
        prefix = f"surfaces[{index}]"
        if not isinstance(surface, dict):
            errors.append(f"{prefix} must be an object")
            continue
        for key in ("surface_id", "workflow_key", "scheduler_kind", "locator"):
            if not isinstance(surface.get(key), str) or not surface[key]:
                errors.append(f"{prefix}.{key} must be a non-empty string")
        if not isinstance(surface.get("workflow_version"), int) or surface["workflow_version"] < 1:
            errors.append(f"{prefix}.workflow_version must be positive")
        surface_id = surface.get("surface_id")
        if isinstance(surface_id, str):
            if surface_id in seen:
                errors.append(f"duplicate surface_id {surface_id}")
            seen.add(surface_id)
        if surface.get("scheduler_kind") == "launchd":
            for key in ("repo_plist_relpath", "installed_plist_name", "canonical_plist_fingerprint"):
                if not isinstance(surface.get(key), str) or not surface[key]:
                    errors.append(f"{prefix}.{key} must be a non-empty string")
            args = surface.get("canonical_program_arguments")
            if not isinstance(args, list) or not args or not all(isinstance(arg, str) for arg in args):
                errors.append(f"{prefix}.canonical_program_arguments must be a non-empty string array")
    if manifest is not None:
        workflows = manifest.get("workflows") if isinstance(manifest, dict) else None
        if not isinstance(workflows, list):
            errors.append("manifest workflows are unavailable for inventory check")
        else:
            active = [w for w in workflows if isinstance(w, dict)
                      and isinstance(w.get("legacy_schedule"), dict)
                      and w["legacy_schedule"].get("status") != "disabled"]
            required = {w.get("key") for w in active}
            represented = {s.get("workflow_key") for s in surfaces if isinstance(s, dict)}
            missing = sorted(str(key) for key in required - represented if isinstance(key, str))
            if missing:
                errors.append("missing non-disabled legacy workflow inventory: " + ", ".join(missing))
            extra = sorted(str(key) for key in represented - required if isinstance(key, str))
            if extra:
                errors.append("stale or extra scheduler surface workflow inventory: " + ", ".join(extra))
            for workflow in active:
                workflow_key = workflow.get("key")
                provider = workflow["legacy_schedule"].get("provider")
                if provider == "duplicate:claude-code+launchd":
                    expected_kinds = {"claude-code", "launchd"}
                elif provider in {"claude-code", "launchd"}:
                    expected_kinds = {provider}
                else:
                    errors.append(f"workflow {workflow_key} has unsupported legacy provider {provider}")
                    continue
                actual_kinds = {s.get("scheduler_kind") for s in surfaces if isinstance(s, dict)
                                and s.get("workflow_key") == workflow_key}
                if actual_kinds != expected_kinds:
                    errors.append(f"workflow {workflow_key} inventory kinds do not match legacy provider")
                workflow_version = workflow.get("version")
                matching = [s for s in surfaces if isinstance(s, dict) and s.get("workflow_key") == workflow_key]
                if any(s.get("workflow_version") != workflow_version for s in matching):
                    errors.append(f"workflow {workflow_key} surface version does not match manifest")
                if len(matching) != len(expected_kinds):
                    errors.append(f"workflow {workflow_key} has duplicate or stale scheduler surfaces")
    return errors


def scheduler_surface_rows(registry: Any, *, manifest: Any) -> list[tuple[str, int, str, str, str]]:
    """Return the complete, manifest-bound DB projection in a stable order.

    The SQL registry is intentionally populated by the authority ``sync``
    transaction, after its corresponding job definitions exist.  Keeping this
    conversion here makes that ordering testable and prevents a partial or
    caller-selected scheduler inventory from reaching the database.
    """
    errors = validate_registry(registry, manifest=manifest)
    if errors:
        raise CutoverRefusal("invalid scheduler cutover registry: " + "; ".join(errors))
    rows = [
        (surface["workflow_key"], surface["workflow_version"], surface["surface_id"],
         surface["locator"], surface["scheduler_kind"])
        for surface in registry["surfaces"]
    ]
    return sorted(rows, key=lambda row: (row[0], row[1], row[2]))


def _surface(registry: dict[str, Any], surface_id: str) -> dict[str, Any]:
    errors = validate_registry(registry)
    if errors:
        raise CutoverRefusal("invalid scheduler cutover registry: " + "; ".join(errors))
    matching = [surface for surface in registry["surfaces"] if surface["surface_id"] == surface_id]
    if len(matching) != 1:
        raise CutoverRefusal("scheduler surface is unknown or ambiguous")
    return matching[0]


def observe_launchd(surface: dict[str, Any], *, repo_plist: Any, installed_plist: Any,
                    repo_path: str, installed_path: str, loaded_labels: set[str] | None,
                    observed_at: str) -> dict[str, Any]:
    """Make an observation from scheduler-truth's repo/installed/live read seams.

    A missing live launchctl read is never interpreted as disabled.  A disabled
    conclusion needs both matching, readable repo and installed plists and a
    successfully-read launchctl label set that excludes the exact locator.
    """
    if surface.get("scheduler_kind") != "launchd":
        raise CutoverRefusal("only launchd surfaces have a local observation adapter")
    locator = surface.get("locator")
    if not isinstance(locator, str) or not locator:
        raise CutoverRefusal("launchd surface has no locator")
    _utc(observed_at)
    expected_args = surface.get("canonical_program_arguments")
    expected_fingerprint = surface.get("canonical_plist_fingerprint")
    repo_ok = (isinstance(repo_plist, dict) and "__error__" not in repo_plist
               and repo_plist.get("Label") == locator and repo_plist.get("ProgramArguments") == expected_args
               and _fingerprint(repo_plist) == expected_fingerprint
               and repo_path == surface.get("repo_plist_relpath"))
    installed_ok = (isinstance(installed_plist, dict) and "__error__" not in installed_plist
                    and installed_plist.get("Label") == locator and installed_plist.get("ProgramArguments") == expected_args
                    and _fingerprint(installed_plist) == expected_fingerprint
                    and installed_path == surface.get("installed_plist_name"))
    live_labels = loaded_labels if isinstance(loaded_labels, set) else set()
    live_read = isinstance(loaded_labels, set)
    state = "unknown"
    if repo_ok and installed_ok and live_read:
        state = "enabled" if locator in live_labels else "disabled"
    material = {
        "repo_path": repo_path, "installed_path": installed_path,
        "repo_fingerprint": _fingerprint(repo_plist) if isinstance(repo_plist, dict) else None,
        "installed_fingerprint": _fingerprint(installed_plist) if isinstance(installed_plist, dict) else None,
        "program_arguments": repo_plist.get("ProgramArguments") if isinstance(repo_plist, dict) else None,
        "loaded": sorted(live_labels) if live_read else None,
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "contract": CONTRACT,
        "kind": "scheduler_observation",
        "surface_id": surface["surface_id"],
        "workflow_key": surface["workflow_key"],
        "workflow_version": surface["workflow_version"],
        "scheduler_kind": "launchd",
        "locator": locator,
        "scheduler_state": state,
        "observed_at": observed_at,
        "sources": {
            "repo_plist_matches": repo_ok,
            "installed_plist_matches": installed_ok,
            "launchctl_read": live_read,
        },
        "source_fingerprint": _fingerprint(material),
    }


def _require_observation(registry: dict[str, Any], surface: dict[str, Any], observation: Any,
                         *, expected_state: str, now: datetime | None) -> dict[str, Any]:
    if not isinstance(observation, dict):
        raise CutoverRefusal("a scheduler observation is required; a DB claim is not evidence")
    if any(key in observation for key in ("legacy_disabled_at", "db_claim", "database_state")):
        raise CutoverRefusal("DB-only disabled claims are not scheduler readback evidence")
    expected = {
        "schema_version": SCHEMA_VERSION, "contract": CONTRACT, "kind": "scheduler_observation",
        "surface_id": surface["surface_id"], "workflow_key": surface["workflow_key"],
        "workflow_version": surface["workflow_version"], "scheduler_kind": surface["scheduler_kind"],
        "locator": surface["locator"], "scheduler_state": expected_state,
    }
    for key, value in expected.items():
        if observation.get(key) != value:
            raise CutoverRefusal(f"scheduler observation {key} does not match the registered surface")
    sources = observation.get("sources")
    if not isinstance(sources, dict) or not all(sources.get(key) is True for key in
                                                ("repo_plist_matches", "installed_plist_matches", "launchctl_read")):
        raise CutoverRefusal("scheduler observation lacks all local readback sources")
    if not isinstance(observation.get("source_fingerprint"), str) or len(observation["source_fingerprint"]) != 64:
        raise CutoverRefusal("scheduler observation lacks a source fingerprint")
    at = _utc(observation.get("observed_at"))
    if _now(now) - at > timedelta(seconds=registry["observation_max_age_seconds"]):
        raise CutoverRefusal("scheduler observation is stale")
    if at > _now(now) + timedelta(seconds=60):
        raise CutoverRefusal("scheduler observation is implausibly in the future")
    return observation


def _require_replacement(surface: dict[str, Any], replacement: Any,
                         receipt_verifier: Callable[[str], Any] | None) -> list[str]:
    if not isinstance(replacement, dict):
        raise CutoverRefusal("replacement health evidence is required")
    if replacement.get("workflow_key") != surface["workflow_key"] or replacement.get("workflow_version") != surface["workflow_version"]:
        raise CutoverRefusal("replacement workflow key/version does not match legacy surface")
    if replacement.get("healthy") is not True:
        raise CutoverRefusal("replacement is unhealthy")
    refs = replacement.get("accepted_receipt_refs")
    if receipt_verifier is None or not isinstance(refs, list) or not refs or not all(isinstance(ref, str) and ref for ref in refs):
        raise CutoverRefusal("typed immutable receipt verifier and receipt refs are required")
    modes: set[str] = set()
    accepted_refs: list[str] = []
    for ref in refs:
        receipt = receipt_verifier(ref)
        if not isinstance(receipt, dict) or receipt.get("kind") != "workflow_acceptance_receipt":
            raise CutoverRefusal("receipt verifier did not return a typed acceptance receipt")
        expected = {"receipt_ref": ref, "workflow_key": surface["workflow_key"],
                    "workflow_version": surface["workflow_version"], "status": "accepted", "immutable": True}
        if any(receipt.get(key) != value for key, value in expected.items()):
            raise CutoverRefusal("accepted receipt does not bind this workflow/version")
        mode = receipt.get("mode")
        if mode not in {"shadow", "canary"}:
            raise CutoverRefusal("accepted receipt has an invalid mode")
        modes.add(mode)
        accepted_refs.append(ref)
    if modes != {"shadow", "canary"}:
        raise CutoverRefusal("replacement lacks accepted shadow and canary receipts")
    return sorted(accepted_refs)


def prepare_disable(registry: dict[str, Any], *, surface_id: str, observation: Any,
                    replacement: Any, receipt_verifier: Callable[[str], Any] | None = None,
                    now: datetime | None = None) -> dict[str, Any]:
    """Create non-mutating, human-reviewable disable preparation evidence."""
    surface = _surface(registry, surface_id)
    if surface["scheduler_kind"] != "launchd":
        raise CutoverRefusal("provider scheduler cutover is outside the local launchd adapter")
    if surface.get("duplicate_group"):
        raise CutoverRefusal("duplicate scheduler group is unresolved; local launchd evidence cannot retire one of two Notes schedules")
    observed = _require_observation(registry, surface, observation, expected_state="enabled", now=now)
    accepted_refs = _require_replacement(surface, replacement, receipt_verifier)
    binding = {
        "surface_id": surface_id,
        "workflow_key": surface["workflow_key"],
        "workflow_version": surface["workflow_version"],
        "locator": surface["locator"],
        "pre_disable_observation_fingerprint": observed["source_fingerprint"],
        "replacement_receipts": accepted_refs,
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "contract": CONTRACT,
        "kind": "scheduler_disable_prepare",
        "action": "human_approval_required",
        "binding": binding,
        "prepare_fingerprint": _fingerprint(binding),
    }


def verify_disabled(registry: dict[str, Any], *, prepared: Any, pre_disable_observation: Any,
                    post_disable_observation: Any, human_approval_ref: str,
                    approval_verifier: Callable[[str], Any] | None = None,
                    now: datetime | None = None) -> dict[str, Any]:
    """Return a typed post-disable readback receipt; never perform a disable."""
    if not isinstance(prepared, dict) or prepared.get("kind") != "scheduler_disable_prepare":
        raise CutoverRefusal("matching prepared disable evidence is required")
    binding = prepared.get("binding")
    if not isinstance(binding, dict) or prepared.get("prepare_fingerprint") != _fingerprint(binding):
        raise CutoverRefusal("prepared disable evidence is malformed or tampered")
    surface_id = binding.get("surface_id")
    if not isinstance(surface_id, str) or not surface_id:
        raise CutoverRefusal("prepared disable evidence has no surface id")
    surface = _surface(registry, surface_id)
    if surface.get("duplicate_group"):
        raise CutoverRefusal("duplicate scheduler group is unresolved; a forged prepared receipt cannot retire one of two Notes schedules")
    pre = _require_observation(registry, surface, pre_disable_observation, expected_state="enabled", now=now)
    post = _require_observation(registry, surface, post_disable_observation, expected_state="disabled", now=now)
    if pre["source_fingerprint"] != binding.get("pre_disable_observation_fingerprint"):
        raise CutoverRefusal("pre-disable observation does not match prepared evidence")
    if _utc(post["observed_at"]) < _utc(pre["observed_at"]):
        raise CutoverRefusal("post-disable observation predates pre-disable observation")
    if post["source_fingerprint"] == pre["source_fingerprint"]:
        raise CutoverRefusal("post-disable readback is not a distinct scheduler observation")
    if approval_verifier is None or not isinstance(human_approval_ref, str) or not human_approval_ref:
        raise CutoverRefusal("Joe-only authority receipt verifier is required")
    approval = approval_verifier(human_approval_ref)
    expected_subject = {"workflow_key": surface["workflow_key"], "workflow_version": surface["workflow_version"],
                        "surface_id": surface["surface_id"], "locator": surface["locator"]}
    if (not isinstance(approval, dict) or approval.get("kind") != "human_authority_receipt"
            or approval.get("receipt_ref") != human_approval_ref or approval.get("immutable") is not True
            or approval.get("authority_subject") != "joe" or approval.get("action") != "disable-legacy-schedule"
            or approval.get("subject") != expected_subject):
        raise CutoverRefusal("authority receipt does not bind Joe/action/exact scheduler subject")
    receipt = {
        "surface_id": surface["surface_id"], "workflow_key": surface["workflow_key"],
        "workflow_version": surface["workflow_version"], "scheduler_kind": "launchd",
        "locator": surface["locator"], "pre_observation_fingerprint": pre["source_fingerprint"],
        "post_observation_fingerprint": post["source_fingerprint"],
        "approval_ref": human_approval_ref,
    }
    return {
        "schema_version": SCHEMA_VERSION, "contract": CONTRACT,
        "kind": "scheduler_disable_readback", "scheduler_state": "disabled",
        "receipt": receipt, "receipt_fingerprint": _fingerprint(receipt),
    }
