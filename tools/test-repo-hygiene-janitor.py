#!/usr/bin/env python3
"""Hermetic disposable-fixture acceptance tests for repo-hygiene-janitor.py.

EVERY MUTATION IN THIS FILE HAPPENS INSIDE A TEMPORARY DIRECTORY THIS FILE
CREATED. No test touches the canonical checkout, a real remote, a real user
branch, a registered worktree, a real cache, or a scheduler. The "remote" is a
second bare repository in the same temporary directory; the pull-request
provider, clock, receipt sink, and worktree-removal door are fakes; the R09
entrant reader is the REAL module read against a fixture registry root, because
that interop is the thing worth proving.

TWO KINDS OF TEST, and the second is the one that carries the weight.

  Behaviour tests assert the janitor does the right thing on a fixture.
  Guard-mutation tests then DISABLE the specific guard that produced the answer
  and assert the outcome flips. Without them, a survivor test proves only that
  a fixture named "dirty" survived — a fixture name is not proof. With them,
  each survivor is demonstrably caused by its named guard.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "ops"))
from git_env import fixture_env                                    # noqa: E402

ENV = fixture_env()


def _load(name: str, relative: str):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


J = _load("repo_hygiene_janitor", "tools/repo-hygiene-janitor.py")
R09 = _load("worktree_runtime_isolation", "tools/room-bridge/worktree_runtime_isolation.py")

TAIL_MANIFEST = ROOT / "out" / "repo-hygiene-program" / "settlement-manifest-branches.txt"
REGISTER = ROOT / "out" / "repo-hygiene-program" / "never-cleanable-register.md"

FAILURES: list[str] = []


def check(condition: bool, label: str) -> None:
    if not condition:
        FAILURES.append(label)


def expect_raises(exc_type, fn, label: str) -> None:
    try:
        fn()
    except exc_type:
        return
    except Exception as exc:                                       # pragma: no cover
        FAILURES.append(f"{label} (raised {type(exc).__name__}: {exc})")
        return
    FAILURES.append(f"{label} (no refusal raised)")


# ── fakes ────────────────────────────────────────────────────────────────────


class FakeClock:
    def __init__(self, start: float = 1_760_000_000.0):
        self.now = start

    def __call__(self) -> float:
        self.now += 1.0
        return self.now


class FakePullRequests:
    def __init__(self, table: Mapping[str, Sequence[J.PullRequest]] | None = None):
        self.table = dict(table or {})

    def pull_requests_for_branch(self, branch: str) -> Sequence[J.PullRequest]:
        return self.table.get(branch, ())


class FakeSink:
    def __init__(self, available: bool = True):
        self._available, self.receipts = available, []

    def available(self) -> bool:
        return self._available

    def emit(self, receipt: Mapping[str, Any]) -> None:
        self.receipts.append(dict(receipt))


# ── fixtures ─────────────────────────────────────────────────────────────────


def git(repo: Path, *args: str, check_rc: bool = True) -> str:
    proc = subprocess.run(("git", "-C", str(repo), *args), capture_output=True,
                          text=True, env=ENV, check=False)
    if check_rc and proc.returncode:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr}")
    return proc.stdout.strip()


def commit(repo: Path, name: str, body: str) -> str:
    (repo / name).write_text(body, encoding="utf-8")
    git(repo, "add", "--", name)
    git(repo, "commit", "-m", f"add {name}")
    return git(repo, "rev-parse", "HEAD").lower()


def build_repo(root: Path) -> tuple[Path, Path]:
    """A disposable work repository plus its own bare 'origin'."""
    origin, work = root / "origin.git", root / "work"
    origin.mkdir(parents=True)
    work.mkdir(parents=True)
    subprocess.run(("git", "init", "--bare", "-b", "main", str(origin)),
                   capture_output=True, env=ENV, check=True)
    subprocess.run(("git", "init", "-b", "main", str(work)),
                   capture_output=True, env=ENV, check=True)
    git(work, "config", "user.email", "fixture@example.invalid")
    git(work, "config", "user.name", "R07 Fixture")
    git(work, "remote", "add", "origin", str(origin))
    commit(work, "seed.txt", "seed")
    git(work, "push", "-u", "origin", "main")
    git(work, "fetch", "origin", "main")
    return work, origin


def janitor(work: Path, *, prs=None, sink=None, clock=None, entrant_reader=None,
            remove_command=None, effect_packet="EFFECT-PACKET-FIXTURE",
            ttl=J.DEFAULT_WORKTREE_TTL_SECONDS) -> J.RepoHygieneJanitor:
    return J.RepoHygieneJanitor(
        repository=work,
        git=J.GitPort(work, env=ENV),
        pull_requests=prs or FakePullRequests(),
        receipt_sink=sink or FakeSink(),
        tail=J.load_settlement_tail(TAIL_MANIFEST),
        register=J.load_never_cleanable_register(REGISTER),
        entrant_reader=entrant_reader,
        clock=clock or FakeClock(),
        worktree_ttl_seconds=ttl,
        worktree_remove_command=remove_command or ("true",),
        effect_packet=effect_packet,
    )


def receipt_for(receipts: Sequence[Mapping[str, Any]], identity: str) -> Mapping[str, Any]:
    for receipt in receipts:
        if receipt["target_identity"] == identity:
            return receipt
    raise AssertionError(f"no receipt for {identity}")


# ── immutable inputs ─────────────────────────────────────────────────────────


def test_immutable_inputs_parse_to_their_known_shape() -> None:
    tail = J.load_settlement_tail(TAIL_MANIFEST)
    in_tail = [row for row in tail.values() if row.in_tail]
    check(len(in_tail) == 265, f"tail is exactly 265 rows (saw {len(in_tail)})")
    by_reason: dict[str, int] = {}
    for row in in_tail:
        by_reason[row.reason] = by_reason.get(row.reason, 0) + 1
    check(by_reason.get("closed_unmerged_pull_request") == 42, "42 closed-unmerged tail rows")
    check(by_reason.get("unmerged_without_pull_request") == 189, "189 no-PR tail rows")
    check(by_reason.get("reused_branch_name") == 34, "34 reused-name tail rows")
    non_tail = [row for row in tail.values() if not row.in_tail]
    check(len(non_tail) == 46, f"46 non-tail kept rows (saw {len(non_tail)})")

    register = J.load_never_cleanable_register(REGISTER)
    worktrees_root = "/Users/booko/carr-system/.claude/worktrees"
    check(worktrees_root in register.never_clean, "worktree root is NEVER CLEAN")
    check(register.verdict(worktrees_root + "/anything") == "never_cleanable_register",
          "a path under a NEVER CLEAN root is denied")
    check(register.verdict("/somewhere/unregistered") == "cache_root_not_registered",
          "an unregistered cache root is denied")


def test_tail_is_never_a_deletion_allowlist(root: Path) -> None:
    """The load-bearing refusal: a real R03 tail branch, genuinely ancestry-
    merged into the pin, still survives because no human adjudicated it."""
    work, _ = build_repo(root / "tail")
    tail = J.load_settlement_tail(TAIL_MANIFEST)
    name = next(n for n, row in tail.items()
                if row.in_tail and "/" not in n and row.reason == "unmerged_without_pull_request")

    git(work, "checkout", "-b", name)
    tip = commit(work, "tail.txt", "tail work")
    git(work, "checkout", "main")
    git(work, "merge", "--no-ff", "-m", "merge tail", name)
    git(work, "push", "origin", "main")
    git(work, "fetch", "origin", "main")

    jan = janitor(work)
    pinned = jan.pin_target()
    check(jan._is_ancestor(tip, pinned), "fixture tail branch really is ancestry-merged")

    row = jan.classify_branch(J.BranchCandidate(name=name, tip=tip), pinned)
    check(row.action == "keep", "an unadjudicated tail branch is kept")
    check(row.reason == "tail_requires_human_adjudication",
          f"kept for the tail reason (saw {row.reason})")

    # GUARD MUTATION: supply the missing human adjudication for this exact tip
    # and the same branch becomes deletable. That is what proves the survival
    # above was caused by the adjudication guard and not by the fixture.
    adjudicated = J.BranchCandidate(name=name, tip=tip, adjudication={
        "adjudicated_by": "joe", "adjudicated_at": "2026-09-09", "decision": "eligible",
        "evidence_id": "fixture-adjudication-1", "tip": tip})
    flipped = jan.classify_branch(adjudicated, pinned)
    check(flipped.action == "delete_ancestry", "adjudicated tail branch becomes deletable")

    # An adjudication recorded against a DIFFERENT tip decided another commit.
    stale = J.BranchCandidate(name=name, tip=tip, adjudication={
        "adjudicated_by": "joe", "adjudicated_at": "2026-09-09", "decision": "eligible",
        "evidence_id": "fixture-adjudication-2", "tip": "0" * 40})
    check(jan.classify_branch(stale, pinned).reason == "tail_requires_human_adjudication",
          "adjudication bound to a stale tip does not unlock the branch")


# ── allowed case 1: ancestry ─────────────────────────────────────────────────


def test_ancestry_delete_with_verified_backup(root: Path) -> None:
    work, origin = build_repo(root / "ancestry")
    git(work, "checkout", "-b", "feat-ancestry")
    tip = commit(work, "a.txt", "ancestry")
    git(work, "checkout", "main")
    git(work, "merge", "--no-ff", "-m", "merge ancestry", "feat-ancestry")
    git(work, "push", "origin", "main")
    git(work, "fetch", "origin", "main")

    sink = FakeSink()
    jan = janitor(work, sink=sink)
    plan = jan.plan(branches=[J.BranchCandidate(name="feat-ancestry", tip=tip)])
    row = plan.rows[0]
    check(row.action == "delete_ancestry", f"planned ancestry deletion (saw {row.action})")

    dry = jan.apply(plan)
    check(dry[0]["result"] == "kept", "dry run keeps")
    check(dry[0]["reason"] == "dry_run_would_delete_ancestry", "dry run names what it would do")
    check(dry[0]["argv"] == [], "dry run issued no command at all")
    check(git(work, "rev-parse", "--verify", "refs/heads/feat-ancestry") == tip,
          "dry run left the branch in place")

    receipts = jan.apply(plan, execute=True, idempotency_prefix="exec")
    receipt = receipts[0]
    check(receipt["result"] == "deleted", f"branch deleted (saw {receipt['result']}: {receipt['reason']})")
    check(git(work, "rev-parse", "--verify", "refs/heads/feat-ancestry", check_rc=False) == "",
          "the branch is really gone from the fixture")

    backup_ref = "refs/backup/r07-janitor/feat-ancestry"
    check(receipt["backup_ref"] == backup_ref, "receipt names the backup ref")
    check(receipt["backup_local_readback"] == tip, "local backup readback is the exact tip")
    check(receipt["backup_remote_readback"] == tip, "remote backup readback is the exact tip")
    check(git(origin, "rev-parse", "--verify", backup_ref) == tip,
          "the backup really exists on the fixture remote")

    # EXACT argv, not a prose command (advisory finding F3).
    argv = receipt["argv"]
    prefix = ["git", "-C", str(work)]
    check(argv[0] == prefix + ["rev-parse", "--verify", "refs/remotes/origin/main"],
          "argv[0] re-pins the target")
    check(argv[1] == prefix + ["update-ref", backup_ref, tip], f"argv update-ref exact: {argv[1]}")
    check(argv[2] == prefix + ["rev-parse", "--verify", backup_ref], "argv local readback exact")
    check(argv[3] == prefix + ["push", "--atomic", "origin",
                               f"refs/heads/feat-ancestry:{backup_ref}"],
          f"argv atomic push exact: {argv[3]}")
    check(argv[4] == prefix + ["ls-remote", "--refs", "origin", backup_ref],
          "argv remote readback exact")
    check(argv[5] == prefix + ["branch", "-d", "feat-ancestry"],
          f"argv deletion is `branch -d` exactly: {argv[5]}")

    for key in ("operation_id", "idempotency_key", "target_kind", "target_identity",
                "observed_preimage", "pinned_target", "started_at", "finished_at",
                "exit_code", "stdout_digest", "stderr_digest", "result", "reason"):
        check(key in receipt, f"receipt carries {key}")
    check(receipt["observed_preimage"] == tip and receipt["current_tip"] == tip,
          "receipt carries preimage and current tip")
    check(sink.receipts and sink.receipts[-1]["result"] == "deleted", "sink received the receipt")


def test_ancestry_guard_mutation_is_caught(root: Path) -> None:
    """Disable the ancestry re-verification and an unmerged branch is deleted.
    The guard, not the fixture name, is what keeps unmerged work alive."""
    work, _ = build_repo(root / "ancestry-mutation")
    git(work, "checkout", "-b", "feat-unmerged")
    tip = commit(work, "u.txt", "unmerged")
    git(work, "checkout", "main")

    jan = janitor(work)
    pinned = jan.pin_target()
    check(jan.classify_branch(J.BranchCandidate("feat-unmerged", tip), pinned).reason
          == "unmerged_without_pull_request", "unmerged branch survives with its own reason")

    mutated = janitor(work)
    mutated._is_ancestor = lambda tip_, pin_: True            # the guard, removed
    flipped = mutated.classify_branch(J.BranchCandidate("feat-unmerged", tip), pinned)
    check(flipped.action == "delete_ancestry",
          "with the ancestry guard removed the unmerged branch would be deleted")


# ── allowed case 2: squash ───────────────────────────────────────────────────


def squash_fixture(root: Path, head_oid: str | None = None, state: str = "merged",
                   base: str = "main"):
    work, _ = build_repo(root)
    git(work, "checkout", "-b", "feat-squash")
    tip = commit(work, "s.txt", "squash work")
    git(work, "checkout", "main")
    commit(work, "other.txt", "unrelated main commit")
    git(work, "push", "origin", "main")
    git(work, "fetch", "origin", "main")
    pr = J.PullRequest(number=42, state=state, base_ref=base,
                       head_oid=head_oid or tip, evidence_id="gh-pr-42")
    return work, tip, FakePullRequests({"feat-squash": [pr]})


def test_squash_delete_requires_exact_recorded_head(root: Path) -> None:
    work, tip, prs = squash_fixture(root / "squash-ok")
    jan = janitor(work, prs=prs)
    plan = jan.plan(branches=[J.BranchCandidate(name="feat-squash", tip=tip)])
    check(plan.rows[0].action == "delete_squash", "squash-merged PR at the exact tip plans a delete")

    receipt = jan.apply(plan, execute=True)[0]
    check(receipt["result"] == "deleted", f"squash branch deleted (saw {receipt['reason']})")
    check(receipt["argv"][-1] == ["git", "-C", str(work), "branch", "-D", "feat-squash"],
          f"forced deletion argv is exact: {receipt['argv'][-1]}")
    check(receipt["evidence"]["pull_request"]["number"] == 42, "PR evidence rides in the receipt")
    check(receipt["evidence"]["pull_request"]["evidence_id"] == "gh-pr-42", "evidence id recorded")

    # GUARD MUTATION: point the PR head one commit away and the same branch,
    # same PR, same merged state, must survive.
    work2, tip2, prs2 = squash_fixture(root / "squash-mismatch", head_oid="a" * 40)
    row = janitor(work2, prs=prs2).plan(
        branches=[J.BranchCandidate(name="feat-squash", tip=tip2)]).rows[0]
    check(row.action == "keep" and row.reason == "reused_branch_name",
          f"a merged PR whose head is not this tip is a reused name (saw {row.reason})")

    # GUARD MUTATION: right head, wrong base branch.
    work3, tip3, prs3 = squash_fixture(root / "squash-wrong-base", base="release")
    row3 = janitor(work3, prs=prs3).plan(
        branches=[J.BranchCandidate(name="feat-squash", tip=tip3)]).rows[0]
    check(row3.action == "keep", f"a PR merged into another base does not delete (saw {row3.action})")

    # GUARD MUTATION: right head, PR closed rather than merged.
    work4, tip4, prs4 = squash_fixture(root / "squash-closed", state="closed")
    row4 = janitor(work4, prs=prs4).plan(
        branches=[J.BranchCandidate(name="feat-squash", tip=tip4)]).rows[0]
    check(row4.action == "keep" and row4.reason == "closed_unmerged_pull_request",
          f"a closed-unmerged PR keeps the branch (saw {row4.reason})")


def test_backup_failure_refuses_before_any_deletion(root: Path) -> None:
    work, tip, prs = squash_fixture(root / "backup-fail")
    jan = janitor(work, prs=prs)
    plan = jan.plan(branches=[J.BranchCandidate(name="feat-squash", tip=tip)])

    # Break the remote half of the backup only. The local update-ref still
    # succeeds, so this isolates the readback conjunction.
    git(work, "remote", "set-url", "origin", str(root / "backup-fail" / "no-such-remote.git"))
    receipt = jan.apply(plan, execute=True)[0]
    check(receipt["result"] == "refused", f"refused without a verified backup (saw {receipt['result']})")
    check(receipt["reason"] == "backup_remote_push_failed",
          f"refusal names the backup failure (saw {receipt['reason']})")
    check(git(work, "rev-parse", "--verify", "refs/heads/feat-squash") == tip,
          "the branch survived the failed backup")
    check(not any(a[3:5] == ["branch", "-D"] for a in receipt["argv"]),
          "no deletion command was ever issued")


def test_tip_movement_between_plan_and_apply_refuses(root: Path) -> None:
    work, tip, prs = squash_fixture(root / "drift")
    jan = janitor(work, prs=prs)
    plan = jan.plan(branches=[J.BranchCandidate(name="feat-squash", tip=tip)])
    check(plan.rows[0].action == "delete_squash", "planned a deletion against the snapshot")

    git(work, "checkout", "feat-squash")
    moved = commit(work, "s2.txt", "work arrived after the snapshot")
    git(work, "checkout", "main")

    receipt = jan.apply(plan, execute=True)[0]
    check(receipt["result"] == "refused", "a tip that moved refuses")
    check(receipt["reason"] == "branch_tip_moved_since_plan", f"named (saw {receipt['reason']})")
    check(receipt["current_tip"] == moved, "the receipt records the tip it actually observed")
    check(git(work, "rev-parse", "--verify", "refs/heads/feat-squash") == moved,
          "the newly arrived work is intact")


def test_pinned_target_drift_refuses(root: Path) -> None:
    work, tip, prs = squash_fixture(root / "pin-drift")
    jan = janitor(work, prs=prs)
    plan = jan.plan(branches=[J.BranchCandidate(name="feat-squash", tip=tip)])
    git(work, "checkout", "main")
    commit(work, "main-moved.txt", "main advanced")
    git(work, "push", "origin", "main")
    git(work, "fetch", "origin", "main")
    receipt = jan.apply(plan, execute=True)[0]
    check(receipt["result"] == "refused" and receipt["reason"] == "pinned_target_drifted_since_plan",
          f"a moved pin refuses (saw {receipt['result']}/{receipt['reason']})")


def test_unavailable_fresh_target_refuses_the_plan(root: Path) -> None:
    work, _ = build_repo(root / "no-pin")
    git(work, "update-ref", "-d", "refs/remotes/origin/main")
    expect_raises(J.JanitorRefusal, lambda: janitor(work).plan(),
                  "an unreadable pinned target refuses the whole plan")


# ── survivor cases that need no merge evidence at all ────────────────────────


def test_branch_survivor_matrix(root: Path) -> None:
    work, _ = build_repo(root / "survivors")
    git(work, "checkout", "-b", "remote-unmerged")
    remote_tip = commit(work, "r.txt", "remote work")
    git(work, "push", "origin", "remote-unmerged")
    git(work, "checkout", "main")

    open_pr = J.PullRequest(1, "open", "main", remote_tip, "gh-pr-1")
    jan = janitor(work, prs=FakePullRequests({"open-pr": [open_pr]}))
    pinned = jan.pin_target()

    cases = [
        (J.BranchCandidate("remote-unmerged", remote_tip, remote_ref_exists=True),
         "remote_unmerged"),
        (J.BranchCandidate("open-pr", remote_tip), "open_pull_request"),
        (J.BranchCandidate("held", remote_tip, worktree_held=True), "worktree_held"),
        (J.BranchCandidate("gone", None), "missing_evidence_unreadable_tip"),
        (J.BranchCandidate("no-pr", remote_tip), "unmerged_without_pull_request"),
    ]
    for candidate, expected in cases:
        row = jan.classify_branch(candidate, pinned)
        check(row.action == "keep" and row.reason == expected,
              f"{candidate.name} survives as {expected} (saw {row.action}/{row.reason})")

    # Exact argv for the remote-unmerged survivor: the assertion that matters is
    # that NO command was issued against it (advisory finding F4).
    plan = jan.plan(branches=[cases[0][0]])
    receipt = jan.apply(plan, execute=True)[0]
    check(receipt["argv"] == [], "remote-unmerged survivor receipt records an empty argv")
    check(receipt["result"] == "kept" and receipt["reason"] == "remote_unmerged",
          "remote-unmerged survivor is receipted as kept for its named reason")
    check(git(work, "rev-parse", "--verify", "refs/heads/remote-unmerged") == remote_tip,
          "the remote-unmerged branch is untouched")

    # GUARD MUTATION: strip the remote-ref fact and the same branch is no longer
    # protected by that clause — it falls through to its own unmerged reason.
    without = jan.classify_branch(
        J.BranchCandidate("remote-unmerged", remote_tip, remote_ref_exists=False), pinned)
    check(without.reason == "unmerged_without_pull_request",
          "the remote-unmerged reason is caused by the remote-ref fact")


# ── worktrees ────────────────────────────────────────────────────────────────


def r09_root(root: Path, entrant: dict | None) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "r09-registry.lock").write_text("", encoding="utf-8")
    (root / "r09-state.json").write_text(
        json.dumps({"allocations": {}, "entrant": entrant, "receipts": []}), encoding="utf-8")
    return root


def test_worktree_survivor_matrix_and_removal(root: Path) -> None:
    work, _ = build_repo(root / "worktrees")
    trees = root / "worktrees" / "trees"
    trees.mkdir(parents=True)

    def add(name: str) -> Path:
        path = trees / name
        git(work, "worktree", "add", "-b", f"wt-{name}", str(path), "main")
        return path

    stale, dirty, live, locked = add("stale"), add("dirty"), add("live"), add("locked")
    entrant_tree, uncertain_tree = add("entrant"), add("uncertain")
    (dirty / "scratch.txt").write_text("uncommitted work", encoding="utf-8")
    git(work, "worktree", "lock", str(locked))

    alive_root = r09_root(root / "r09-alive", {"owner": "session-a|wt|sha"})
    empty_root = r09_root(root / "r09-empty", None)
    reader_alive = lambda p: R09.read_lock_posture(p, owner_alive=lambda _o: True)
    reader_unknown = lambda p: R09.read_lock_posture(p, owner_alive=lambda _o: None)

    old = J.DEFAULT_WORKTREE_TTL_SECONDS * 2
    jan = janitor(work, entrant_reader=reader_alive)
    survivors = [
        (J.WorktreeCandidate(str(dirty), True, dirty=True, idle_seconds=old), "dirty_worktree"),
        (J.WorktreeCandidate(str(live), True, dirty=False, idle_seconds=60.0),
         "recently_live_worktree"),
        (J.WorktreeCandidate(str(locked), True, locked=True, dirty=False, idle_seconds=old),
         "locked_worktree"),
        (J.WorktreeCandidate(str(trees / "bare"), True, bare=True, dirty=False, idle_seconds=old),
         "bare_worktree"),
        (J.WorktreeCandidate(str(trees / "ghost"), False, dirty=False, idle_seconds=old),
         "unregistered_worktree"),
        (J.WorktreeCandidate("/private/tmp/scratch-tree", True, dirty=False, idle_seconds=old),
         "volatile_private_tmp_path"),
        (J.WorktreeCandidate(str(J.CANONICAL_CHECKOUT), True, dirty=False, idle_seconds=old),
         "canonical_tree"),
        (J.WorktreeCandidate(str(stale), True, dirty=False, idle_seconds=None),
         "missing_evidence_unknown_liveness"),
        (J.WorktreeCandidate(str(entrant_tree), True, dirty=False, idle_seconds=old,
                             isolation_root=str(alive_root)), "active_r09_entrant"),
    ]
    for candidate, expected in survivors:
        row = jan.classify_worktree(candidate)
        check(row.action == "keep" and row.reason == expected,
              f"worktree {Path(candidate.path).name} survives as {expected} "
              f"(saw {row.action}/{row.reason})")

    # An entrant whose owner liveness is UNKNOWN preserves the tree too, and it
    # does so regardless of TTL: this candidate is far past the TTL.
    uncertain = J.RepoHygieneJanitor(
        repository=work, git=J.GitPort(work, env=ENV), pull_requests=FakePullRequests(),
        receipt_sink=FakeSink(), tail=J.load_settlement_tail(TAIL_MANIFEST),
        register=J.load_never_cleanable_register(REGISTER), entrant_reader=reader_unknown,
        clock=FakeClock(), effect_packet="EFFECT-PACKET-FIXTURE")
    row = uncertain.classify_worktree(J.WorktreeCandidate(
        str(uncertain_tree), True, dirty=False, idle_seconds=old, isolation_root=str(alive_root)))
    check(row.reason == "uncertain_r09_entrant_liveness",
          f"uncertain entrant liveness preserves regardless of TTL (saw {row.reason})")

    # An unreadable registry is missing evidence, never an absent entrant.
    broken = root / "r09-broken"
    broken.mkdir()
    (broken / "r09-registry.lock").write_text("", encoding="utf-8")
    (broken / "r09-state.json").write_text("{not json", encoding="utf-8")
    row = jan.classify_worktree(J.WorktreeCandidate(
        str(stale), True, dirty=False, idle_seconds=old, isolation_root=str(broken)))
    check(row.reason == "missing_evidence_entrant_unreadable",
          f"an unreadable R09 registry preserves (saw {row.reason})")

    # ALLOWED CASE: registered, clean, stale, no entrant -> removal through the
    # governed door. The fixture substitutes a fixture-local door for
    # bin/worktree.sh so that no canonical worktree is ever addressed.
    door = trees / "fixture-remove.sh"
    door.write_text(f'#!/bin/sh\nexec git -C "{work}" worktree remove "$1"\n', encoding="utf-8")
    door.chmod(0o755)
    removing = janitor(work, entrant_reader=reader_alive, remove_command=("sh", str(door)))
    plan = removing.plan(worktrees=[J.WorktreeCandidate(
        str(stale), True, dirty=False, idle_seconds=old, isolation_root=str(empty_root))])
    check(plan.rows[0].action == "remove_worktree",
          f"a stale clean unentered tree plans removal (saw {plan.rows[0].reason})")

    dry = removing.apply(plan)
    check(dry[0]["result"] == "kept" and stale.exists(), "dry run leaves the worktree in place")

    receipt = removing.apply(plan, execute=True, idempotency_prefix="exec")[0]
    check(receipt["result"] == "deleted", f"worktree removed (saw {receipt['reason']})")
    check(not stale.exists(), "the fixture worktree is really gone")
    check(receipt["argv"][-1] == ["sh", str(door), str(stale)],
          f"the governed removal argv is exact: {receipt['argv'][-1]}")

    # GUARD MUTATION: the same stale, clean, registered tree with an ACTIVE
    # entrant would have been removed had the entrant clause not fired.
    check(jan.classify_worktree(J.WorktreeCandidate(
        str(entrant_tree), True, dirty=False, idle_seconds=old,
        isolation_root=str(empty_root))).action == "remove_worktree",
        "with no entrant the same tree is removable — the entrant clause is what saves it")


def test_worktree_dirty_at_apply_refuses(root: Path) -> None:
    work, _ = build_repo(root / "wt-drift")
    trees = root / "wt-drift" / "trees"
    trees.mkdir(parents=True)
    path = trees / "drifter"
    git(work, "worktree", "add", "-b", "wt-drifter", str(path), "main")

    jan = janitor(work, remove_command=("false",))
    plan = jan.plan(worktrees=[J.WorktreeCandidate(
        str(path), True, dirty=False, idle_seconds=J.DEFAULT_WORKTREE_TTL_SECONDS * 2)])
    check(plan.rows[0].action == "remove_worktree", "planned removal against the snapshot")

    (path / "late.txt").write_text("work arrived after the snapshot", encoding="utf-8")
    receipt = jan.apply(plan, execute=True)[0]
    check(receipt["result"] == "refused" and receipt["reason"] == "worktree_dirty_at_apply",
          f"work arriving after the snapshot refuses (saw {receipt['result']}/{receipt['reason']})")
    check(path.exists() and (path / "late.txt").exists(), "the late work is intact")


# ── caches ───────────────────────────────────────────────────────────────────


def test_cache_matrix(root: Path) -> None:
    base = root / "caches"
    work, _ = build_repo(base)
    cache = base / "cache-root"
    cache.mkdir()
    (cache / "blob").write_text("reproducible bytes", encoding="utf-8")
    digest = hashlib.sha256(b"reproducible bytes").hexdigest()

    register = J.NeverCleanableRegister(
        never_clean=frozenset({str(base / "credentials")}),
        exact_root_only=frozenset({str(cache)}))
    jan = J.RepoHygieneJanitor(
        repository=work, git=J.GitPort(work, env=ENV), pull_requests=FakePullRequests(),
        receipt_sink=FakeSink(), tail=J.load_settlement_tail(TAIL_MANIFEST), register=register,
        clock=FakeClock(), effect_packet="EFFECT-PACKET-FIXTURE")

    survivors = [
        (J.CacheCandidate(str(base / "credentials" / "token"), digest, digest, ["refetch"], True),
         "never_cleanable_register"),
        (J.CacheCandidate(str(base / "elsewhere"), digest, digest, ["refetch"], True),
         "cache_root_not_registered"),
        (J.CacheCandidate("/private/tmp/whatever", digest, digest, ["refetch"], True),
         "volatile_private_tmp_path"),
        (J.CacheCandidate(str(cache), None, digest, ["refetch"], True),
         "cache_reproducibility_missing"),
        (J.CacheCandidate(str(cache), "b" * 64, digest, ["refetch"], True),
         "cache_reproducibility_mismatch"),
        (J.CacheCandidate(str(cache), digest, digest, None, False),
         "cache_refetch_proof_missing"),
    ]
    for candidate, expected in survivors:
        row = jan.classify_cache(candidate)
        check(row.action == "keep" and row.reason == expected,
              f"cache survives as {expected} (saw {row.action}/{row.reason})")

    good = J.CacheCandidate(str(cache), digest, digest, ["fetch", "--exact", digest], True)
    plan = jan.plan(caches=[good])
    check(plan.rows[0].action == "prune_cache", "a reproducible registered cache plans a prune")
    check(jan.apply(plan)[0]["result"] == "kept" and cache.exists(), "dry run leaves the cache")
    receipt = jan.apply(plan, execute=True, idempotency_prefix="exec")[0]
    check(receipt["result"] == "deleted" and not cache.exists(), "the fixture cache is pruned")
    check(receipt["argv"][-1] == ["rm", "-rf", "--", str(cache)],
          f"cache removal argv is exact: {receipt['argv'][-1]}")
    check(receipt["evidence"]["refetch_command"] == ["fetch", "--exact", digest],
          "the receipt says how to re-fetch what it removed")


def test_canonical_cache_cleanup_is_structurally_forbidden(root: Path) -> None:
    """The register's own law: never run ignored-files cleanup against canonical."""
    jan = J.RepoHygieneJanitor(
        repository=J.CANONICAL_CHECKOUT, git=J.GitPort(root, env=ENV),
        pull_requests=FakePullRequests(), receipt_sink=FakeSink(),
        tail=J.load_settlement_tail(TAIL_MANIFEST),
        register=J.load_never_cleanable_register(REGISTER),
        clock=FakeClock(), effect_packet="EFFECT-PACKET-FIXTURE")
    row = jan.classify_cache(J.CacheCandidate(
        str(J.CANONICAL_CHECKOUT / ".DS_Store"), "a" * 64, "a" * 64, ["x"], True))
    check(row.action == "keep" and row.reason == "canonical_cache_cleanup_forbidden",
          f"canonical cache cleanup is refused (saw {row.reason})")


# ── mutex, receipts, replay, disabled runtime ────────────────────────────────


def test_maintenance_mutex_contention_and_staleness(root: Path) -> None:
    canonical = root / "mutex-root"
    clock = FakeClock()
    first = J.MaintenanceMutex(canonical, clock=clock)
    check(first.acquire(), "the first holder acquires the mutex")
    check(first.path == canonical / "out" / "worktree-reap.lock",
          "the mutex is the existing out/worktree-reap.lock, not a second one")

    second = J.MaintenanceMutex(canonical, clock=clock)
    check(not second.acquire(), "a second holder is refused while the first is live")
    expect_raises(J.JanitorRefusal, lambda: J.MaintenanceMutex(canonical, clock=clock).__enter__(),
                  "the context manager refuses under contention")
    check(first.path.exists(), "the refused contender did not steal the lock")

    first.release()
    check(not first.path.exists(), "release removes the lock file")

    # A lock older than the existing two-hour threshold belongs to a dead
    # reaper and is reclaimed — the same rule hooks/worktree-self-plumb.py uses.
    third = J.MaintenanceMutex(canonical, clock=clock)
    check(third.acquire(), "reacquire after release")
    old_clock = lambda: time.time() + J.LOCK_STALE_SECONDS + 60
    fourth = J.MaintenanceMutex(canonical, clock=old_clock)
    check(fourth.acquire(), "a stale lock is reclaimed")
    fourth.release()


def test_receipt_sink_availability_is_checked_before_mutation(root: Path) -> None:
    work, tip, prs = squash_fixture(root / "sink")
    sink = FakeSink(available=False)
    jan = janitor(work, prs=prs, sink=sink)
    plan = jan.plan(branches=[J.BranchCandidate(name="feat-squash", tip=tip)])
    expect_raises(J.JanitorRefusal, lambda: jan.apply(plan, execute=True),
                  "an unavailable receipt sink refuses before any mutation")
    check(git(work, "rev-parse", "--verify", "refs/heads/feat-squash") == tip,
          "nothing was deleted into an unavailable sink")
    check(sink.receipts == [], "no receipt was emitted")


def test_execute_without_effect_packet_refuses(root: Path) -> None:
    work, tip, prs = squash_fixture(root / "no-packet")
    jan = janitor(work, prs=prs, effect_packet=None)
    plan = jan.plan(branches=[J.BranchCandidate(name="feat-squash", tip=tip)])
    expect_raises(J.JanitorRefusal, lambda: jan.apply(plan, execute=True),
                  "execute without a reviewed live-effect packet refuses")
    check(git(work, "rev-parse", "--verify", "refs/heads/feat-squash") == tip,
          "the branch survives an unauthorised execute")
    check(jan.apply(plan)[0]["result"] == "kept", "the same plan still dry-runs")


def test_idempotent_replay_does_not_remutate(root: Path) -> None:
    work, tip, prs = squash_fixture(root / "replay")
    jan = janitor(work, prs=prs)
    plan = jan.plan(branches=[J.BranchCandidate(name="feat-squash", tip=tip)])
    first = jan.apply(plan, execute=True, idempotency_prefix="run-1")
    check(first[0]["result"] == "deleted", "first apply deletes")

    replay = jan.apply(plan, execute=True, idempotency_prefix="run-1")
    check(replay[0]["operation_id"] == first[0]["operation_id"],
          "a replay under the same idempotency key returns the recorded receipt")
    check(len(jan.receipt_sink.receipts) == 1, "the replay emitted no second receipt")

    # A DIFFERENT key is a genuinely new operation, and it must refuse rather
    # than silently succeed: the branch is already gone.
    fresh = jan.apply(plan, execute=True, idempotency_prefix="run-2")
    check(fresh[0]["result"] == "refused" and fresh[0]["reason"] == "branch_disappeared_before_apply",
          f"a new operation over deleted state refuses (saw {fresh[0]['reason']})")


def test_partial_operation_is_unknown_and_never_retried(root: Path) -> None:
    """Exit code and observed state disagreeing is UNKNOWN, not success and not
    a retry. Simulated by a git port that reports success without deleting."""
    work, tip, prs = squash_fixture(root / "partial")
    jan = janitor(work, prs=prs)
    plan = jan.plan(branches=[J.BranchCandidate(name="feat-squash", tip=tip)])

    real_run = jan.git.run

    def lying_run(argv):
        if tuple(argv[:2]) == ("branch", "-D"):
            return J.GitResult(("git", "-C", str(work), *argv), 0, "", "")
        return real_run(argv)

    jan.git.run = lying_run
    receipt = jan.apply(plan, execute=True)[0]
    check(receipt["result"] == "unknown", f"the disagreement is UNKNOWN (saw {receipt['result']})")
    check(receipt["reason"] == "partial_deletion_state_unknown_no_automatic_retry",
          f"and it names no automatic retry (saw {receipt['reason']})")
    check(receipt["backup_local_readback"] == tip,
          "the receipt still carries the backup a human needs to recover from")
    check(git(work, "rev-parse", "--verify", "refs/heads/feat-squash") == tip,
          "the branch is in fact still present")


def test_entrypoint_is_disabled_by_default() -> None:
    argv = ["--repository", str(ROOT), "--tail-manifest", str(TAIL_MANIFEST),
            "--never-cleanable-register", str(REGISTER)]
    check(J.main(argv) == 0, "the entrypoint plans and reports")
    env_key = "CARR_REPO_HYGIENE_EFFECT_PACKET"
    saved = os.environ.pop(env_key, None)
    try:
        check(J.main(argv + ["--execute"]) == 2,
              "--execute without a reviewed live-effect packet is refused")
    finally:
        if saved is not None:
            os.environ[env_key] = saved
    parser = J.build_parser()
    check(parser.parse_args(argv).execute is False, "execute defaults to off")


# ── runner ───────────────────────────────────────────────────────────────────


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="r07-repo-hygiene-janitor-") as temporary:
        root = Path(temporary)
        test_immutable_inputs_parse_to_their_known_shape()
        test_tail_is_never_a_deletion_allowlist(root)
        test_ancestry_delete_with_verified_backup(root)
        test_ancestry_guard_mutation_is_caught(root)
        test_squash_delete_requires_exact_recorded_head(root)
        test_backup_failure_refuses_before_any_deletion(root)
        test_tip_movement_between_plan_and_apply_refuses(root)
        test_pinned_target_drift_refuses(root)
        test_unavailable_fresh_target_refuses_the_plan(root)
        test_branch_survivor_matrix(root)
        test_worktree_survivor_matrix_and_removal(root)
        test_worktree_dirty_at_apply_refuses(root)
        test_cache_matrix(root)
        test_canonical_cache_cleanup_is_structurally_forbidden(root)
        test_maintenance_mutex_contention_and_staleness(root)
        test_receipt_sink_availability_is_checked_before_mutation(root)
        test_execute_without_effect_packet_refuses(root)
        test_idempotent_replay_does_not_remutate(root)
        test_partial_operation_is_unknown_and_never_retried(root)
        test_entrypoint_is_disabled_by_default()

    if FAILURES:
        for failure in FAILURES:
            print(f"FAIL  {failure}", file=sys.stderr)
        print(f"repo-hygiene-janitor-selftest: {len(FAILURES)} FAILED", file=sys.stderr)
        return 1
    print("repo-hygiene-janitor-selftest: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
