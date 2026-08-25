#!/usr/bin/env python3
"""Prove frozen migration-number collisions cannot spread or lose a filename."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

from migration_number_contract import (  # noqa: E402
    FROZEN_COLLISIONS,
    LEGACY_APPLIED_ALIASES,
    MigrationNumberError,
    collision_report,
    validate_migration_names,
)
import migrate as migration_runner  # noqa: E402

NEXT_MIGRATION_SPEC = importlib.util.spec_from_file_location(
    "next_migration", REPO / "tools" / "next-migration.py"
)
assert NEXT_MIGRATION_SPEC and NEXT_MIGRATION_SPEC.loader
next_migration: Any = importlib.util.module_from_spec(NEXT_MIGRATION_SPEC)
NEXT_MIGRATION_SPEC.loader.exec_module(next_migration)


FROZEN_0169 = (
    "0169_control_plane_canary_fencing.sql",
    "0169_hermes_pilot_actor.sql",
    "0169_program5_release_binding.sql",
)
EXPECTED_LEGACY_ALIASES = {
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


def refuses(names: tuple[str, ...], expected: str) -> None:
    try:
        validate_migration_names(names)
    except MigrationNumberError as exc:
        assert expected in str(exc), str(exc)
    else:
        raise AssertionError(f"expected migration-number refusal containing {expected!r}")


def main() -> int:
    actual = tuple(path.name for path in (REPO / "migrations").glob("*.sql"))
    validate_migration_names(actual, require_frozen=True)

    report = collision_report(actual)
    assert report == FROZEN_COLLISIONS, report
    assert report["0169"] == FROZEN_0169
    assert LEGACY_APPLIED_ALIASES == EXPECTED_LEGACY_ALIASES

    refuses(("0171_alpha.sql", "0171_beta.sql"), "unregistered collision 0171")
    refuses(("0170_next.sql", "0170a_escape.sql"), "unregistered collision 0170")
    refuses(FROZEN_0169[:2], "frozen collision 0169 changed")
    refuses(FROZEN_0169 + ("0169_fourth.sql",), "frozen collision 0169 changed")
    refuses(FROZEN_0169 + ("0169a_escape.sql",), "frozen collision 0169 changed")
    missing_frozen = tuple(name for name in actual if name != "0074_deal_city_lane.sql")
    try:
        validate_migration_names(missing_frozen, require_frozen=True)
    except MigrationNumberError as exc:
        assert "frozen collision 0074 changed" in str(exc), str(exc)
    else:
        raise AssertionError("deleting one side of frozen collision 0074 was accepted")

    # The historical lettered repair is a frozen exception inside numeric slot
    # 0013. A new letter suffix cannot create another allocation escape hatch.
    validate_migration_names((
        "0013_active_book_derived.sql",
        "0013a_historical_client_status_vocabulary.sql",
    ))

    loaded = migration_runner.load_migrations()
    loaded_0169 = tuple(name for name, _sql, _digest in loaded if name.startswith("0169_"))
    assert loaded_0169 == FROZEN_0169, loaded_0169
    loaded_digests = {name: digest for name, _sql, digest in loaded}
    pending = migration_runner.pending_migrations(
        loaded,
        {
            "0169_control_plane_canary_fencing.sql":
                loaded_digests["0169_control_plane_canary_fencing.sql"]
        },
    )
    pending_names = {name for name, _sql, _digest in pending}
    assert "0169_control_plane_canary_fencing.sql" not in pending_names
    assert "0169_hermes_pilot_actor.sql" in pending_names
    assert "0169_program5_release_binding.sql" in pending_names

    selected, held_back = migration_runner.migrations_through(
        loaded, pending, "0170_guidance_import_lifecycle.sql"
    )
    selected_names = [name for name, _sql, _digest in selected]
    held_back_names = [name for name, _sql, _digest in held_back]
    assert "0169_program5_release_binding.sql" in selected_names
    assert selected_names[-1] == "0170_guidance_import_lifecycle.sql"
    assert held_back_names[0] == "0171_program5_provider_version.sql"
    assert all(name <= "0170_guidance_import_lifecycle.sql" for name in selected_names)
    assert all(name > "0170_guidance_import_lifecycle.sql" for name in held_back_names)
    try:
        migration_runner.migrations_through(loaded, pending, "0170_not_a_file.sql")
    except ValueError as exc:
        assert "exact checked-in migration filename" in str(exc), str(exc)
    else:
        raise AssertionError("unknown --through boundary was accepted")

    # A bounded prefix must not make an out-of-order ledger look safe.  If a
    # later file is already applied while an earlier file is absent, history
    # has drifted and the runner must stop before selecting anything.
    try:
        migration_runner.validate_applied_ledger(
            loaded,
            {
                name: digest
                for name, _sql, digest in loaded
                if name != "0170_guidance_import_lifecycle.sql"
                and name <= "0171_program5_provider_version.sql"
            },
        )
    except migration_runner.AppliedMigrationLedgerError as exc:
        assert "ledger is reordered" in str(exc), str(exc)
        assert "0170_guidance_import_lifecycle.sql" in str(exc), str(exc)
        assert "0171_program5_provider_version.sql" in str(exc), str(exc)
    else:
        raise AssertionError("out-of-order applied ledger was accepted")

    # A ledger row whose file disappeared is a rename/deletion, not harmless
    # history. Only the exact pre-renumber Control Plane aliases are accepted,
    # and only while their mapped forward migrations remain in the tree.
    missing_release = [
        item for item in loaded if item[0] != "0134_release_abandon_reason.sql"
    ]
    try:
        migration_runner.validate_applied_ledger(
            missing_release,
            {"0134_release_abandon_reason.sql":
                loaded_digests["0134_release_abandon_reason.sql"]},
        )
    except migration_runner.AppliedMigrationLedgerError as exc:
        assert "0134_release_abandon_reason.sql" in str(exc), str(exc)
    else:
        raise AssertionError("deleted applied non-legacy migration was accepted")

    alias_prefix = {
        name: digest for name, _sql, digest in loaded
        if name < "0148_control_plane_admission.sql"
    }
    alias_prefix["0134_control_plane_admission.sql"] = "legacy-ledger-digest"
    migration_runner.validate_applied_ledger(loaded, alias_prefix)
    try:
        migration_runner.validate_applied_ledger(
            loaded,
            {"0169_control_plane_canary_fencing.sql": "edited-digest"},
        )
    except migration_runner.AppliedMigrationLedgerError as exc:
        assert "sha mismatch" in str(exc), str(exc)
    else:
        raise AssertionError("edited applied migration digest was accepted")
    missing_alias_target = [
        item for item in loaded if item[0] != "0148_control_plane_admission.sql"
    ]
    try:
        migration_runner.validate_applied_ledger(
            missing_alias_target,
            {"0134_control_plane_admission.sql": "legacy-ledger-digest"},
        )
    except migration_runner.AppliedMigrationLedgerError as exc:
        assert "0148_control_plane_admission.sql" in str(exc), str(exc)
    else:
        raise AssertionError("legacy ledger alias without its forward migration was accepted")

    allocation = subprocess.run(
        [sys.executable, str(REPO / "tools" / "next-migration.py")],
        cwd=REPO,
        text=True,
        capture_output=True,
        check=True,
    ).stdout
    assert "frozen numeric collisions on origin/main" in allocation, allocation
    assert "0169: " + ", ".join(FROZEN_0169) in allocation, allocation

    # The only permitted remote-collision repair is the current 0298 incident:
    # preserve the partner-room migration, replace only the memory migration
    # with 0299, and prove the replacement bytes differ only in ordinal labels.
    exact_frozen = [name for names in FROZEN_COLLISIONS.values() for name in names]
    exact_remote = exact_frozen + [
        "0298_partner_room_origin.sql",
        "0298_memory_kernel.sql",
    ]
    exact_current = exact_frozen + [
        "0298_partner_room_origin.sql",
        "0299_memory_kernel.sql",
    ]
    assert next_migration._repairs_exact_origin_collision(
        exact_remote, exact_current, True, True
    )
    assert not next_migration._repairs_exact_origin_collision(
        exact_remote + ["0298_unrelated.sql"], exact_current, True, True
    )
    assert not next_migration._repairs_exact_origin_collision(
        exact_remote, exact_current[:-1], True, True
    )
    assert not next_migration._repairs_exact_origin_collision(
        exact_remote, exact_current[1:], True, True
    )
    assert not next_migration._repairs_exact_origin_collision(
        exact_remote, exact_current, False, True
    )
    assert not next_migration._repairs_exact_origin_collision(
        exact_remote, exact_remote, True, True
    )
    assert not next_migration._repairs_exact_origin_collision(
        exact_remote, exact_current, True, False
    )
    assert not next_migration._repairs_exact_origin_collision(
        exact_remote, exact_current + ["0300_memory_kernel.sql"], True, True
    )
    assert not next_migration._repairs_exact_origin_collision(
        [name for name in exact_remote if name != "0169_hermes_pilot_actor.sql"],
        exact_current,
        True,
        True,
    )

    # Exercise the actual allocator path with the real migration inventory:
    # red origin/main plus a clean checked tree succeeds and reserves 0298,
    # while a checked tree that still contains both 0298 files refuses.
    remote_inventory = [
        name if name != "0299_memory_kernel.sql" else "0298_memory_kernel.sql"
        for name in actual
    ]
    for colliding_current in (False, True):
        with tempfile.TemporaryDirectory(prefix="migration-number-contract-0298-repair-") as tmp:
            migration_dir = Path(tmp) / "migrations"
            migration_dir.mkdir()
            current_inventory = list(actual)
            if colliding_current:
                current_inventory.append("0298_memory_kernel.sql")
            for name in current_inventory:
                (migration_dir / name).touch()
            original_run = next_migration.run
            original_worktree_paths = next_migration.worktree_paths
            original_repo = next_migration.REPO
            original_head_check = next_migration._head_contains_origin_main
            original_content_check = next_migration._repair_contents_match
            try:
                next_migration.run = lambda args, cwd=None: (
                    "\n".join(f"migrations/{name}" for name in remote_inventory)
                    if args[:3] == ["git", "ls-tree", "--name-only"] else ""
                )
                next_migration.worktree_paths = lambda: [tmp]
                next_migration.REPO = tmp
                next_migration._head_contains_origin_main = lambda: True
                next_migration._repair_contents_match = lambda: True
                stdout, stderr = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    repair_rc = next_migration.main()
            finally:
                next_migration.run = original_run
                next_migration.worktree_paths = original_worktree_paths
                next_migration.REPO = original_repo
                next_migration._head_contains_origin_main = original_head_check
                next_migration._repair_contents_match = original_content_check
            if colliding_current:
                assert repair_rc == 1, (repair_rc, stdout.getvalue(), stderr.getvalue())
                assert "origin/main violates" in stderr.getvalue(), stderr.getvalue()
            else:
                assert repair_rc == 0, (repair_rc, stdout.getvalue(), stderr.getvalue())
                assert "next free migration number: 0301" in stdout.getvalue(), stdout.getvalue()
                assert "0298_memory_kernel.sql" in stdout.getvalue(), stdout.getvalue()

    # The same allocator path refuses if the red remote inventory is missing
    # one member of a canonical frozen collision.
    with tempfile.TemporaryDirectory(prefix="migration-number-contract-0298-frozen-gap-") as tmp:
        migration_dir = Path(tmp) / "migrations"
        migration_dir.mkdir()
        for name in actual:
            (migration_dir / name).touch()
        remote_missing_frozen = [
            name for name in remote_inventory if name != "0169_hermes_pilot_actor.sql"
        ]
        original_run = next_migration.run
        original_worktree_paths = next_migration.worktree_paths
        original_repo = next_migration.REPO
        original_head_check = next_migration._head_contains_origin_main
        original_content_check = next_migration._repair_contents_match
        try:
            next_migration.run = lambda args, cwd=None: (
                "\n".join(f"migrations/{name}" for name in remote_missing_frozen)
                if args[:3] == ["git", "ls-tree", "--name-only"] else ""
            )
            next_migration.worktree_paths = lambda: [tmp]
            next_migration.REPO = tmp
            next_migration._head_contains_origin_main = lambda: True
            next_migration._repair_contents_match = lambda: True
            stdout, stderr = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                frozen_gap_rc = next_migration.main()
        finally:
            next_migration.run = original_run
            next_migration.worktree_paths = original_worktree_paths
            next_migration.REPO = original_repo
            next_migration._head_contains_origin_main = original_head_check
            next_migration._repair_contents_match = original_content_check
        assert frozen_gap_rc == 1, (frozen_gap_rc, stdout.getvalue(), stderr.getvalue())
        assert "origin/main violates" in stderr.getvalue(), stderr.getvalue()

    # A COLLISION IN SOMEONE ELSE'S WORKTREE WARNS AND STILL RESERVES THE
    # NUMBERS. It does not refuse.
    #
    # This assertion used to require rc 1, and that veto cost most of a night on
    # 2026-08-22. Two branches merged 0248 twice; main resolved it by renumber;
    # every other checkout on the machine still held the old filename on disk,
    # as stale worktrees harmlessly do. The allocator then refused for everyone,
    # this selftest failed, and the pre-push gate refused pushes from branches
    # that touched no migration at all — three of them, whose only way through
    # was skipping CI entirely, which is strictly worse than the thing being
    # guarded. The council ruled the class the same day: a machine-global
    # condition may open a loop, never veto unrelated work.
    #
    # What actually protects the caller is the claim merge, and that is asserted
    # here: the colliding numbers must still be reported as in-flight, so the
    # allocator never hands one out.
    with tempfile.TemporaryDirectory(prefix="migration-number-contract-") as tmp:
        migration_dir = Path(tmp) / "migrations"
        migration_dir.mkdir()
        (migration_dir / "0172_first.sql").touch()
        (migration_dir / "0172_second.sql").touch()
        original_run = next_migration.run
        original_worktree_paths = next_migration.worktree_paths
        try:
            next_migration.run = lambda _args, cwd=next_migration.REPO: "\n".join(actual)
            next_migration.worktree_paths = lambda: [tmp]
            stdout, stderr = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                allocator_rc = next_migration.main()
        finally:
            next_migration.run = original_run
            next_migration.worktree_paths = original_worktree_paths
        assert allocator_rc == 0, (allocator_rc, stdout.getvalue(), stderr.getvalue())
        assert "WARNING" in stderr.getvalue(), stderr.getvalue()
        assert "unregistered collision 0172" in stderr.getvalue(), stderr.getvalue()
        assert "another session's checkout" in stderr.getvalue(), stderr.getvalue()
        # The load-bearing half: both names stay claimed, so 0172 is never
        # handed to the caller as free.
        assert "0172_first.sql" in stdout.getvalue(), stdout.getvalue()
        assert "0172_second.sql" in stdout.getvalue(), stdout.getvalue()

    # THE CALLER'S OWN TREE IS DIFFERENT, and still refuses. A collision here
    # means the number about to be handed out may itself be wrong, and it is
    # the caller's to fix rather than someone else's.
    with tempfile.TemporaryDirectory(prefix="migration-number-contract-own-") as tmp:
        migration_dir = Path(tmp) / "migrations"
        migration_dir.mkdir()
        (migration_dir / "0173_first.sql").touch()
        (migration_dir / "0173_second.sql").touch()
        original_run = next_migration.run
        original_worktree_paths = next_migration.worktree_paths
        original_repo = next_migration.REPO
        try:
            next_migration.run = lambda _args, cwd=None: "\n".join(actual)
            next_migration.worktree_paths = lambda: [tmp]
            next_migration.REPO = tmp          # the caller IS standing in this tree
            stdout, stderr = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                own_rc = next_migration.main()
        finally:
            next_migration.run = original_run
            next_migration.worktree_paths = original_worktree_paths
            next_migration.REPO = original_repo
        assert own_rc == 1, (own_rc, stdout.getvalue(), stderr.getvalue())
        assert "this tree violates" in stderr.getvalue(), stderr.getvalue()

    print("migration number contract selftest: historical collisions frozen; "
          "own-tree collisions refused; a peer worktree's collision warns and still reserves")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
