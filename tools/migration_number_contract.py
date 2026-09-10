"""Shared migration-slot policy for the allocator, runner, and CI.

Migration identity in PostgreSQL is the full filename. Numeric slots are still
globally allocated so concurrent work cannot create ambiguous history. Historical
exceptions remain frozen here by their exact filename sets. A separately named,
approved interstitial pair is kept out of that historical register.
"""
from __future__ import annotations

import re
from collections.abc import Iterable


SLOT_RE = re.compile(r"^(\d{4})[a-z]?_[a-z0-9_]+\.sql$")
FROZEN_COLLISIONS: dict[str, tuple[str, ...]] = {
    "0013": (
        "0013_active_book_derived.sql",
        "0013a_historical_client_status_vocabulary.sql",
    ),
    "0074": (
        "0074_deal_city_lane.sql",
        "0074_outside_model_actors.sql",
    ),
    "0078": (
        "0078_exporter_doctrine_select.sql",
        "0078_writer_participant_role_grant.sql",
    ),
    "0079": (
        "0079_deal_room_api.sql",
        "0079_review_clock_backfill.sql",
    ),
    "0080": (
        "0080_deal_room_board_view.sql",
        "0080_reader_briefing_grants.sql",
    ),
    "0081": (
        "0081_capture_bridge.sql",
        "0081_loop_blocker.sql",
    ),
    "0095": (
        "0095_sponsor_runtime_audit.sql",
        "0095_vendor_lookup_grants.sql",
    ),
    "0169": (
        "0169_control_plane_canary_fencing.sql",
        "0169_hermes_pilot_actor.sql",
        "0169_program5_release_binding.sql",
    ),
}

# Approved by codex-compaction-continuity-design
# ef786b17-b695-4d0d-8d15-604e0b02ef24@6,
# sha256:341ccee0fb477acbca0a6a9015fe8405e81560f77d50f839cdbb29b4c7f1a936.
# This is a reviewed forward release pair, not historical migration history.
# Its lettered member may appear only with the exact base member. The allocator
# admits origin/main's predecessor state (the base member alone) only while the
# pair is awaiting its approved merge; every checked worktree must carry both.
APPROVED_INTERSTITIAL_COLLISIONS: dict[str, tuple[str, ...]] = {
    "0494": (
        "0494_codex_continuity_archive_registry.sql",
        "0494a_codex_continuity_reference_manifest.sql",
    ),
}

# These twelve filenames were applied to isolated Control Plane staging before
# that branch was renumbered. They are absent from the repository by design;
# their mapped forward migrations are idempotent convergence files and must
# remain present. No other missing ledger filename is grandfathered.
LEGACY_APPLIED_ALIASES: dict[str, str] = {
    "0134_control_plane_admission.sql": "0148_control_plane_admission.sql",
    "0135_control_plane_jobs.sql": "0149_control_plane_jobs.sql",
    "0136_control_plane_job_fixes.sql": "0150_control_plane_job_fixes.sql",
    "0137_control_plane_admission_grants.sql": "0151_control_plane_admission_grants.sql",
    "0138_rule_writer_grants.sql": "0152_rule_writer_grants.sql",
    "0139_control_plane_resilience.sql": "0153_control_plane_resilience.sql",
    "0140_control_plane_cost_release.sql": "0154_control_plane_cost_release.sql",
    "0141_rule_applicability_wildcard.sql": "0155_rule_applicability_wildcard.sql",
    "0142_control_plane_input_grants.sql": "0156_control_plane_input_grants.sql",
    "0143_control_plane_runtime_guards.sql": "0157_control_plane_runtime_guards.sql",
    "0144_job_timeout_receipts.sql": "0158_job_timeout_receipts.sql",
    "0145_control_plane_evidence_grants.sql": "0159_control_plane_evidence_grants.sql",
}


class MigrationNumberError(ValueError):
    """The migration tree violates the global slot contract."""


def collision_report(names: Iterable[str]) -> dict[str, tuple[str, ...]]:
    """Return lexical migration slots claimed by more than one filename."""
    claims: dict[str, set[str]] = {}
    for name in names:
        match = SLOT_RE.match(name)
        if match:
            claims.setdefault(match.group(1), set()).add(name)
    return {
        slot: tuple(sorted(slot_names))
        for slot, slot_names in sorted(claims.items())
        if len(slot_names) > 1
    }


def validate_migration_names(
    names: Iterable[str], *, require_frozen: bool = False,
    allow_frozen_subset: bool = False,
    allow_approved_interstitial_base: bool = False,
) -> None:
    """Allow exact historical collisions and the approved interstitial pair."""
    materialized = tuple(names)
    for name in materialized:
        match = SLOT_RE.match(name)
        if not match:
            continue
        slot = match.group(1)
        known_names = FROZEN_COLLISIONS.get(slot)
        label = "frozen"
        if known_names is None:
            known_names = APPROVED_INTERSTITIAL_COLLISIONS.get(slot)
            label = "approved interstitial"
        if known_names is not None:
            if name not in known_names:
                raise MigrationNumberError(
                    f"{label} collision {slot} changed: "
                    f"unexpected filename {name}"
                )
    for slot, slot_names in collision_report(materialized).items():
        registered_names = FROZEN_COLLISIONS.get(slot)
        label = "frozen"
        if registered_names is None:
            registered_names = APPROVED_INTERSTITIAL_COLLISIONS.get(slot)
            label = "approved interstitial"
        if registered_names is None:
            raise MigrationNumberError(
                f"unregistered collision {slot}: {', '.join(slot_names)}; "
                "allocate a new migration number"
            )
        if slot_names != registered_names and not (
            allow_frozen_subset
            and label == "frozen"
            and set(slot_names).issubset(registered_names)
        ):
            raise MigrationNumberError(
                f"{label} collision {slot} changed: expected {', '.join(registered_names)}; "
                f"found {', '.join(slot_names)}"
            )
    present_names = set(materialized)
    for slot, interstitial in APPROVED_INTERSTITIAL_COLLISIONS.items():
        present = tuple(name for name in interstitial if name in present_names)
        if present and present != interstitial and not (
            allow_approved_interstitial_base and present == interstitial[:1]
        ):
            raise MigrationNumberError(
                f"approved interstitial collision {slot} changed: "
                f"expected {', '.join(interstitial)}; "
                f"found {', '.join(present)}"
            )
    if require_frozen:
        for slot, frozen in FROZEN_COLLISIONS.items():
            present = tuple(name for name in frozen if name in present_names)
            if present != frozen:
                raise MigrationNumberError(
                    f"frozen collision {slot} changed: expected {', '.join(frozen)}; "
                    f"found {', '.join(present) if present else 'none'}"
                )
