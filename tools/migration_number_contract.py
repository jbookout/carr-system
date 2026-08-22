"""Shared migration-slot policy for the allocator, runner, and CI.

Migration identity in PostgreSQL is the full filename. Numeric slots are still
globally allocated so concurrent work cannot create ambiguous history. The
already-merged exceptions are frozen here by their exact filename sets.
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
    # Both merged to main on 2026-08-21, hours apart, from branches that never
    # saw each other: the renewal ingress with #452 and the conduct-stop
    # registration with #453. Each passed CI before the other landed, so no
    # check either one ran could have seen the collision -- the allocator reads
    # the tree, and neither tree contained the other file yet.
    #
    # FROZEN RATHER THAN RENAMED, which is the whole point of this table. By the
    # time a collision is visible both files are already on main, and
    # 0248_register_conduct_stop_control.sql is applied to production besides;
    # renaming an applied migration is the one thing the runner refuses
    # outright. They sort deterministically by full filename, which is the
    # identity PostgreSQL actually keys on.
    "0248": (
        "0248_register_conduct_stop_control.sql",
        "0248_renewal_signed_source_ingress.sql",
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
) -> None:
    """Allow only exact historical collisions; optionally require all of them."""
    materialized = tuple(names)
    for name in materialized:
        match = SLOT_RE.match(name)
        if match and match.group(1) in FROZEN_COLLISIONS:
            known_names = FROZEN_COLLISIONS[match.group(1)]
            if name not in known_names:
                raise MigrationNumberError(
                    f"frozen collision {match.group(1)} changed: "
                    f"unexpected filename {name}"
                )
    for slot, slot_names in collision_report(materialized).items():
        registered_names = FROZEN_COLLISIONS.get(slot)
        if registered_names is None:
            raise MigrationNumberError(
                f"unregistered collision {slot}: {', '.join(slot_names)}; "
                "allocate a new migration number"
            )
        if slot_names != registered_names and not (
            allow_frozen_subset and set(slot_names).issubset(registered_names)
        ):
            raise MigrationNumberError(
                f"frozen collision {slot} changed: expected {', '.join(registered_names)}; "
                f"found {', '.join(slot_names)}"
            )
    if require_frozen:
        present_names = set(materialized)
        for slot, frozen in FROZEN_COLLISIONS.items():
            present = tuple(name for name in frozen if name in present_names)
            if present != frozen:
                raise MigrationNumberError(
                    f"frozen collision {slot} changed: expected {', '.join(frozen)}; "
                    f"found {', '.join(present) if present else 'none'}"
                )
