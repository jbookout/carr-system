#!/usr/bin/env python3
"""Disposable-fixture acceptance tests for r03-settlement-sweep-runner.py."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parent.parent
OPS = ROOT / "ops"
sys.path.insert(0, str(OPS))
from git_env import fixture_env


ENV = fixture_env()
RUNNER_PATH = ROOT / "tools" / "r03-settlement-sweep-runner.py"
SPEC = importlib.util.spec_from_file_location("r03_settlement_sweep_runner", RUNNER_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("could not load R03 settlement runner")
RUNNER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = RUNNER
SPEC.loader.exec_module(RUNNER)


def checked(argv: list[str], cwd: Path) -> str:
    result = subprocess.run(argv, cwd=str(cwd), env=ENV, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode:
        raise AssertionError(f"command failed: {' '.join(argv)}\n{result.stdout}\n{result.stderr}")
    return result.stdout


def git(repository: Path, *args: str) -> str:
    return checked(["git", *args], repository)


class Fixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.remote = root / "origin.git"
        self.repository = root / "work"
        self.root.mkdir(parents=True)
        checked(["git", "init", "--bare", str(self.remote)], root)
        self.repository.mkdir()
        git(self.repository, "init", "-b", "main")
        git(self.repository, "config", "user.email", "fixture@example.test")
        git(self.repository, "config", "user.name", "R03 fixture")
        (self.repository / "tracked.txt").write_text("tracked\n", encoding="utf-8")
        git(self.repository, "add", "tracked.txt")
        git(self.repository, "commit", "-m", "fixture base")
        git(self.repository, "remote", "add", "origin", str(self.remote))
        git(self.repository, "push", "-u", "origin", "main")
        git(self.repository, "fetch", "origin", "main")

    @property
    def pin(self) -> str:
        return git(self.repository, "rev-parse", "refs/remotes/origin/main").strip()

    def manifest(self, *, clean_pathspecs: list[str], clean_expected: list[str], branches: list[dict] | None = None,
                 branch_count: int | None = None) -> dict:
        return {
            "schema_version": RUNNER.MANIFEST_SCHEMA,
            "run_id": "fixture-r03-stage5",
            "approved": True,
            "pinned_origin_main": self.pin,
            "preconditions": {
                "capability_denial_tests_passed": True,
                "fresh_verified_production_backup": True,
            },
            "never_cleanable": ["never-cleanable"],
            "clean": {"pathspecs": clean_pathspecs, "expected": clean_expected},
            "restore": [],
            "park": {"paths": [], "archive": None},
            "branches": branches or [],
            "closing": {
                "expected_head": self.pin,
                "expected_branch_count": branch_count if branch_count is not None else 1 + len(branches or []),
            },
        }

    def allowlist(self, manifest: dict, *, execute: bool) -> dict:
        paths = manifest["clean"]["pathspecs"]
        commands = [{
            "id": "stage5.clean.dry",
            "argv": ["git", "clean", "-nd", "--", *paths],
            "pathspecs": paths,
        }]
        if execute:
            commands.append({
                "id": "stage5.clean.execute",
                "argv": ["git", "clean", "-fd", "--", *paths],
                "pathspecs": paths,
            })
        for branch in manifest["branches"]:
            if branch["classification"] == "ancestry_merged" and branch["tip_backup_ref"] is not None:
                commands.append({
                    "id": f"stage5.branch.safe.{branch['name']}",
                    "argv": ["git", "branch", "-d", branch["name"]], "pathspecs": [],
                })
            if branch["classification"] == "squash_merged" and branch["tip_backup_ref"] is not None:
                commands.append({
                    "id": f"stage5.branch.squash.{branch['name']}",
                    "argv": ["git", "branch", "-D", branch["name"]], "pathspecs": [],
                })
        return {
            "schema_version": RUNNER.ALLOWLIST_SCHEMA,
            "runner_argv": [str(RUNNER_PATH.resolve()), "--manifest-fd={manifest_fd}",
                            "--allowlist-fd={allowlist_fd}", "--capability-receipt-fd={capability_receipt_fd}"],
            "commands": commands,
        }


def fd_for(value: dict) -> int:
    read_fd, write_fd = os.pipe()
    body = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    os.write(write_fd, body)
    os.close(write_fd)
    return read_fd


def invoke(fixture: Fixture, manifest: dict, *, execute: bool, before_disposal=None) -> str:
    allowlist = fixture.allowlist(manifest, execute=execute)
    manifest_body = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    receipt = {
        "schema_version": RUNNER.RECEIPT_SCHEMA,
        "capability_key": RUNNER.CAPABILITY_KEY,
        "token_digest": "fixture-token",
        "operator_binding_digest": "fixture-operator",
        "approved_manifest_digest": RUNNER._sha256(manifest_body),
        "repository_identity_digest": "fixture-repository",
        "starting_object_id": fixture.pin,
        "allowlist_digest": "fixture-allowlist",
        "consumed_at_ns": 1,
    }
    fds = [fd_for(manifest), fd_for(allowlist), fd_for(receipt)]
    stdout = io.StringIO()
    try:
        with contextlib.redirect_stdout(stdout):
            RUNNER.run_settlement(
                repository=fixture.repository, manifest_fd=fds[0], allowlist_fd=fds[1],
                capability_receipt_fd=fds[2], execute=execute, before_disposal=before_disposal,
            )
    finally:
        for descriptor in fds:
            try:
                os.close(descriptor)
            except OSError:
                pass
    return stdout.getvalue()


def test_dry_run_touches_nothing(root: Path) -> None:
    fixture = Fixture(root / "dry-run")
    candidate = fixture.repository / "scratch" / "remove-me.txt"
    candidate.parent.mkdir()
    candidate.write_text("fixture debris\n", encoding="utf-8")
    before_status = git(fixture.repository, "status", "--porcelain=v1")
    before_head = git(fixture.repository, "rev-parse", "HEAD")
    output = invoke(fixture, fixture.manifest(clean_pathspecs=["scratch"], clean_expected=["scratch"]), execute=False)
    assert candidate.exists(), "dry-run removed a fixture file"
    assert git(fixture.repository, "status", "--porcelain=v1") == before_status, "dry-run changed fixture status"
    assert git(fixture.repository, "rev-parse", "HEAD") == before_head, "dry-run changed fixture HEAD"
    assert "DRY-RUN:" in output and "stage 5 clean diff: ['scratch']" in output
    print("PASS dry_run_touches_nothing")


def test_never_cleanable_candidate_aborts(root: Path) -> None:
    fixture = Fixture(root / "never-cleanable")
    candidate = fixture.repository / "never-cleanable" / "do-not-remove.txt"
    candidate.parent.mkdir()
    candidate.write_text("protected\n", encoding="utf-8")
    try:
        invoke(fixture, fixture.manifest(clean_pathspecs=["never-cleanable"], clean_expected=["never-cleanable"]), execute=False)
    except RUNNER.SweepError as exc:
        assert "never-cleanable" in str(exc)
    else:
        raise AssertionError("never-cleanable clean candidate did not abort")
    assert candidate.exists(), "abort path changed protected fixture content"
    print("PASS never_cleanable_candidate_aborts")


def test_midrun_tree_change_aborts(root: Path) -> None:
    fixture = Fixture(root / "midrun-change")
    candidate = fixture.repository / "scratch" / "remove-me.txt"
    candidate.parent.mkdir()
    candidate.write_text("fixture debris\n", encoding="utf-8")
    manifest = fixture.manifest(clean_pathspecs=["scratch"], clean_expected=["scratch"])
    def seed_write() -> None:
        (fixture.repository / "seeded-midrun-change.txt").write_text("changed after fingerprint\n", encoding="utf-8")
    try:
        invoke(fixture, manifest, execute=True, before_disposal=seed_write)
    except RUNNER.SweepError as exc:
        assert "fingerprint changed" in str(exc)
    else:
        raise AssertionError("mid-run tree change did not abort")
    assert candidate.exists(), "fingerprint abort reached git clean"
    assert (fixture.repository / "seeded-midrun-change.txt").exists()
    print("PASS midrun_tree_change_aborts")


def test_branch_law_retains_unmerged_and_unbacked_squash(root: Path) -> None:
    fixture = Fixture(root / "branch-law")
    git(fixture.repository, "switch", "-c", "unmerged-without-pr")
    (fixture.repository / "unmerged.txt").write_text("unmerged\n", encoding="utf-8")
    git(fixture.repository, "add", "unmerged.txt")
    git(fixture.repository, "commit", "-m", "unmerged fixture")
    unmerged_tip = git(fixture.repository, "rev-parse", "HEAD").strip()
    git(fixture.repository, "switch", "main")
    git(fixture.repository, "switch", "-c", "squash-without-backup")
    (fixture.repository / "squash.txt").write_text("squash\n", encoding="utf-8")
    git(fixture.repository, "add", "squash.txt")
    git(fixture.repository, "commit", "-m", "squash fixture")
    squash_tip = git(fixture.repository, "rev-parse", "HEAD").strip()
    git(fixture.repository, "switch", "main")
    branches: list[dict] = [
        {"name": "unmerged-without-pr", "tip": unmerged_tip, "classification": "unmerged_without_pr",
         "tip_backup_ref": None, "host_confirmation": None},
        {"name": "squash-without-backup", "tip": squash_tip, "classification": "squash_merged",
         "tip_backup_ref": None,
         "host_confirmation": {"provider": "github", "state": "MERGED", "base_ref": "main", "head_oid": squash_tip,
                               "evidence_id": "fixture-pr-1"}},
    ]
    manifest = fixture.manifest(clean_pathspecs=["scratch"], clean_expected=[], branches=branches, branch_count=3)
    output = invoke(fixture, manifest, execute=True)
    assert git(fixture.repository, "show-ref", "--verify", "--quiet", "refs/heads/unmerged-without-pr") == ""
    assert git(fixture.repository, "show-ref", "--verify", "--quiet", "refs/heads/squash-without-backup") == ""
    assert "retained unmerged-without-PR branch" in output
    assert "retained branch lacking tip backup ref" in output
    print("PASS branch_law_retains_unmerged_and_unbacked_squash")


def _advance_origin_main(fixture: Fixture) -> str:
    """Land a new commit on origin/main while the checkout stays where it was.

    This is the ordinary state of a repository other sessions merge into, and the
    case a manifest must survive rather than expire on.
    """
    stay = git(fixture.repository, "rev-parse", "HEAD").strip()
    (fixture.repository / "landed-elsewhere.txt").write_text("another session's merge\n", encoding="utf-8")
    git(fixture.repository, "add", "landed-elsewhere.txt")
    git(fixture.repository, "commit", "-m", "unrelated PR landing on main")
    git(fixture.repository, "push", "origin", "main")
    git(fixture.repository, "reset", "--hard", stay)
    git(fixture.repository, "fetch", "origin", "main")
    return stay


def test_freshness_accepts_advanced_origin_main(root: Path) -> None:
    """origin/main moving forward must NOT expire an otherwise-valid manifest."""
    fixture = Fixture(root / "freshness-advance")
    pin = fixture.pin
    manifest = fixture.manifest(clean_pathspecs=["scratch"], clean_expected=[])
    stayed = _advance_origin_main(fixture)
    assert stayed == pin and fixture.pin != pin, "fixture did not advance origin/main past the pin"
    output = invoke(fixture, manifest, execute=True)
    assert "origin/main advanced" in output, output
    assert "STAGE 6 closing readback passed" in output, output
    print("PASS freshness_accepts_advanced_origin_main")


def test_freshness_refuses_rewound_origin_main(root: Path) -> None:
    """A pin that origin/main can no longer reach invalidates every ancestry claim."""
    fixture = Fixture(root / "freshness-rewind")
    manifest = fixture.manifest(clean_pathspecs=["scratch"], clean_expected=[])
    git(fixture.repository, "checkout", "--orphan", "rewritten")
    (fixture.repository / "rewritten.txt").write_text("rewritten history\n", encoding="utf-8")
    git(fixture.repository, "add", "rewritten.txt")
    git(fixture.repository, "commit", "-m", "rewritten history")
    git(fixture.repository, "push", "--force", "origin", "rewritten:main")
    git(fixture.repository, "checkout", "main")
    git(fixture.repository, "fetch", "origin", "main")
    try:
        invoke(fixture, manifest, execute=True)
    except RUNNER.SweepError as exc:
        assert "does not descend from manifest pin" in str(exc), str(exc)
    else:
        raise AssertionError("a rewound origin/main did not abort the settlement")
    print("PASS freshness_refuses_rewound_origin_main")


def test_precondition_refuses_stale_head(root: Path) -> None:
    """A checkout behind the pin can never satisfy stage 6, so it is refused up front."""
    fixture = Fixture(root / "stale-head")
    debris = fixture.repository / "scratch" / "remove-me.txt"
    debris.parent.mkdir()
    debris.write_text("fixture debris\n", encoding="utf-8")
    _advance_origin_main(fixture)
    # manifest pins the NEW origin/main while the checkout still sits on the old commit
    manifest = fixture.manifest(clean_pathspecs=["scratch"], clean_expected=["scratch"])
    assert manifest["pinned_origin_main"] != git(fixture.repository, "rev-parse", "HEAD").strip()
    try:
        invoke(fixture, manifest, execute=True)
    except RUNNER.SweepHeld as exc:
        assert "is not the settled pin" in str(exc), str(exc)
    else:
        raise AssertionError("a stale checkout was not refused before destructive work")
    assert debris.exists(), "refused run still reached git clean"
    # and the dry-run must SAY so rather than implying the run would succeed
    output = invoke(fixture, manifest, execute=False)
    assert "PRECONDITION NOT MET" in output, output
    print("PASS precondition_refuses_stale_head")


def test_closing_detects_collateral_branch_loss(root: Path) -> None:
    """A branch this settlement never declared must not disappear during it."""
    fixture = Fixture(root / "collateral-loss")
    git(fixture.repository, "branch", "bystander")
    manifest = fixture.manifest(clean_pathspecs=["scratch"], clean_expected=[], branch_count=2)
    def drop_bystander() -> None:
        git(fixture.repository, "branch", "-D", "bystander")
    try:
        invoke(fixture, manifest, execute=True, before_disposal=drop_bystander)
    except RUNNER.SweepError as exc:
        assert "vanished that this settlement never deleted" in str(exc), str(exc)
        assert "bystander" in str(exc), str(exc)
    else:
        raise AssertionError("collateral branch loss was not detected by the closing readback")
    print("PASS closing_detects_collateral_branch_loss")


def test_closing_detects_undeleted_branch(root: Path) -> None:
    """A branch the runner believes it deleted must not still exist.

    End-to-end this cannot be staged -- if the delete ran, the branch is gone --
    so the closing assertion is exercised directly rather than shipped unproven.
    """
    fixture = Fixture(root / "undeleted-branch")
    git(fixture.repository, "branch", "still-here")
    manifest = fixture.manifest(clean_pathspecs=["scratch"], clean_expected=[], branch_count=2)
    parsed = RUNNER.validate_manifest(manifest)
    try:
        RUNNER._stage6_readback(
            fixture.repository, manifest, parsed,
            starting_branches={"main", "still-here"}, deleted={"still-here"},
        )
    except RUNNER.SweepError as exc:
        assert "deleted branches still present" in str(exc), str(exc)
        assert "still-here" in str(exc), str(exc)
    else:
        raise AssertionError("closing readback accepted a branch that was never actually deleted")
    print("PASS closing_detects_undeleted_branch")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="r03-settlement-sweep-") as temporary:
        root = Path(temporary)
        test_dry_run_touches_nothing(root)
        test_never_cleanable_candidate_aborts(root)
        test_midrun_tree_change_aborts(root)
        test_branch_law_retains_unmerged_and_unbacked_squash(root)
        test_freshness_accepts_advanced_origin_main(root)
        test_freshness_refuses_rewound_origin_main(root)
        test_precondition_refuses_stale_head(root)
        test_closing_detects_collateral_branch_loss(root)
        test_closing_detects_undeleted_branch(root)
    print("r03-settlement-sweep-selftest: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
