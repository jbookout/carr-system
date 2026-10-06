#!/usr/bin/env python3
"""Exercise the existing reaper against isolated git repositories."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ops"))
from git_env import fixture_env

spec = importlib.util.spec_from_file_location("reaper", ROOT / "hooks/worktree-self-plumb.py")
assert spec and spec.loader
hook = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = hook
spec.loader.exec_module(hook)


class ReaperTest(unittest.TestCase):
    def test_interrupted_staging_is_read_back_before_another_run(self):
        fixture = Fixture()
        tree = fixture.tree("old")
        reaper = fixture.reaper()
        original = reaper.apply
        def interrupted(*args):
            original(*args)
            raise RuntimeError("fixture interruption after effect")
        reaper.apply = interrupted
        first = reaper.run({"fixture/repo": fixture.repo}, execute=True)
        self.assertTrue(first["errors"])
        self.assertFalse(tree.exists())
        second = fixture.reaper().run({"fixture/repo": fixture.repo}, execute=True)
        self.assertFalse(second["errors"])
        self.assertEqual([r["status"] for r in second["recovered"]], ["observed_staged"])

    def test_dead_stale_maintenance_lock_recovers_but_live_owner_lock_survives(self):
        for pid, should_fail in ((99999999, False), (os.getpid(), True)):
            with self.subTest(pid=pid):
                fixture = Fixture()
                lock = fixture.repo / "out/worktree-reap.lock"
                lock.parent.mkdir()
                lock.write_text(str(pid))
                old = time.time() - 12 * 3600
                os.utime(lock, (old, old))
                report = fixture.reaper().run({"fixture/repo": fixture.repo})
                self.assertEqual(bool(report["errors"]), should_fail)
                self.assertEqual(lock.exists(), should_fail)

    def test_inherited_git_location_cannot_redirect_census(self):
        from unittest.mock import patch
        target, other = Fixture(), Fixture()
        with patch.dict(os.environ, {"GIT_DIR": str(other.repo / ".git"),
                                     "GIT_WORK_TREE": str(other.repo)}):
            self.assertEqual(hook.canonical_root(str(target.repo)), str(target.repo))

    def test_scheduler_runs_existing_reaper_once_per_interval_and_preserves_live_job(self):
        sys.path.insert(0, str(ROOT / "lib"))
        import job_watchdog as watchdog
        fixture = Fixture()
        config = watchdog.load_config(ROOT / "ops/config/job-watchdog.json")
        config["repository_roots"]["jbookout/carr-system"] = str(fixture.repo)
        calls = []
        class Effects:
            def launch(self, finding, argv, cwd, *, job_id):
                calls.append(argv)
                self.job_id = job_id
                return {"wrapper_pid": os.getpid()}
        watchdog.schedule_reaper(fixture.repo, config, Effects(), 10000)
        watchdog.schedule_reaper(fixture.repo, config, Effects(), 10001)
        watchdog.schedule_reaper(fixture.repo, config, Effects(), 19000)
        self.assertEqual(len(calls), 1)
        self.assertIn(str(ROOT / "hooks/worktree-self-plumb.py"), calls[0])
        self.assertIn("--fleet", calls[0])

    def test_dead_schedule_wrapper_does_not_preserve_itself_as_a_none_identity(self):
        from unittest.mock import patch
        import job_watchdog as watchdog
        fixture = Fixture()
        config = watchdog.load_config(ROOT / "ops/config/job-watchdog.json")
        config["repository_roots"]["jbookout/carr-system"] = str(fixture.repo)
        ledger = fixture.repo / "out/orch/branch-janitor-schedule.jsonl"
        watchdog.append(ledger, {"key": "schedule", "at": 1000, "wrapper_pid": 99999999,
                                 "process_identity": None, "status": "started"})
        calls = []
        class Effects:
            def launch(self, finding, argv, cwd, *, job_id):
                calls.append(argv)
                return {"wrapper_pid": os.getpid()}
        watchdog.schedule_reaper(fixture.repo, config, Effects(), 9000)
        self.assertEqual(len(calls), 1)

    def test_fixture_scan_cannot_launch_the_live_fleet_scheduler(self):
        import job_watchdog as watchdog
        from unittest.mock import Mock
        fixture = Fixture()
        config = watchdog.load_config(ROOT / "ops/config/job-watchdog.json")
        effects = Mock()
        watchdog.schedule_reaper(fixture.repo, config, effects, 20000)
        effects.launch.assert_not_called()
        self.assertEqual(hook.reap_main(["--reap", "--fleet", "--repo", str(fixture.repo)]), 1)

    def test_merged_branch_can_retire_but_abandoned_branch_cannot(self):
        sys.path.insert(0, str(ROOT / "lib"))
        from branch_retirement import branch_verdict
        merged = branch_verdict(merged=True, successor=None, open_pr=False, idle=True)
        abandoned = branch_verdict(merged=False, successor=None, open_pr=False, idle=True)
        self.assertEqual(merged, "merged")
        self.assertEqual(abandoned, "abandoned")

    def test_dry_run_then_stage_preserves_orphan_bytes_and_branch(self):
        base = Path(tempfile.mkdtemp(prefix="branch-janitor-fixture-")).resolve()
        repo, remote, tree = base / "repo", base / "remote.git", base / "orphan"
        repo.mkdir()
        def git(*args, cwd=repo):
            return subprocess.run(["git", *map(str, args)], cwd=cwd, env=fixture_env(),
                                  text=True, capture_output=True, check=True).stdout.strip()
        git("init", "-q", "-b", "main")
        git("config", "user.email", "fixture@example.invalid")
        git("config", "user.name", "Fixture")
        (repo / "file").write_text("committed\n")
        git("add", "file")
        git("commit", "-qm", "initial")
        git("init", "--bare", remote)
        git("remote", "add", "origin", remote)
        git("push", "-u", "origin", "main")
        git("worktree", "add", "-b", "old", tree)
        (tree / "file").write_text("unsaved work\n")
        gitdir = Path(hook.index_gitdir(str(tree)))
        old = time.time() - 10 * 86400
        for p in (tree / "file", tree / ".git", gitdir / "index"):
            os.utime(p, (old, old))
        reaper = hook.fleet_reaper(repo, provider=FakeProvider(),
                                   process_probe=lambda paths: set(),
                                   ownership_probe=lambda path: "orphaned")
        report = reaper.run({"fixture/repo": repo}, execute=False)
        self.assertTrue(tree.exists())
        self.assertEqual(report["counts"]["fixture/repo"]["worktree"]["merged"], 1)
        report = reaper.run({"fixture/repo": repo}, execute=True)
        self.assertFalse(tree.exists())
        manifest = next((repo.parent / "_to_delete" / repo.name).glob("*/manifest.json"))
        staged = manifest.parent / "worktree"
        self.assertEqual((staged / "file").read_text(), "unsaved work\n")
        self.assertEqual(json.loads(manifest.read_text())["original_path"], str(tree))
        self.assertEqual(git("rev-parse", "old"), git("rev-parse", "main"))
        self.assertEqual(git("rev-parse", "--show-toplevel", cwd=staged), str(staged))

    def test_changed_remote_tip_is_not_deleted(self):
        fixture = Fixture()
        fixture.git("push", "origin", "main:refs/heads/merged")
        reaper = fixture.reaper()
        row = next(r for r in reaper.snapshot("fixture/repo", fixture.repo)
                   if r.get("ref") == "refs/remotes/origin/merged")
        fixture.commit("new work")
        fixture.git("push", "origin", "main:refs/heads/merged")
        result = reaper.apply("fixture/repo", fixture.repo, row, [])
        self.assertEqual(result["status"], "preserved")
        self.assertIn("refs/heads/merged", fixture.git("ls-remote", "origin"))

    def test_running_process_and_unknown_dirty_owner_preserve_worktree(self):
        for busy, owner in ((True, "orphaned"), (False, "unknown"), (False, "owned")):
            with self.subTest(busy=busy, owner=owner):
                fixture = Fixture()
                tree = fixture.tree("old")
                (tree / "file").write_text("unsaved\n")
                fixture.age(tree)
                reaper = fixture.reaper(process_probe=lambda paths: {str(tree)} if busy else set(),
                                        ownership_probe=lambda path: owner)
                report = reaper.run({"fixture/repo": fixture.repo}, execute=True)
                self.assertFalse(report["errors"])
                self.assertTrue(tree.exists())
                self.assertEqual(next(r for r in report["rows"] if r.get("path") == str(tree))["class"], "live")

    def test_process_starting_during_content_verification_preserves_tree(self):
        fixture = Fixture()
        tree = fixture.tree("old")
        calls = []
        def probe(paths):
            calls.append(paths)
            return {str(tree)} if len(calls) >= 3 else set()
        report = fixture.reaper(process_probe=probe).run({"fixture/repo": fixture.repo}, execute=True)
        self.assertTrue(tree.exists())
        self.assertTrue(report["errors"])

    def test_remote_retirement_is_read_back_and_local_branch_survives(self):
        fixture = Fixture()
        fixture.git("branch", "merged")
        fixture.git("push", "origin", "merged")
        report = fixture.reaper().run({"fixture/repo": fixture.repo}, execute=True)
        self.assertFalse(report["errors"])
        self.assertNotIn("refs/heads/merged", fixture.git("ls-remote", "origin"))
        self.assertEqual(fixture.git("rev-parse", "merged"), fixture.git("rev-parse", "main"))
        retired = next(a for a in report["actions"] if a["status"] == "retired")
        self.assertEqual(fixture.git("rev-parse", retired["backup_ref"]), fixture.git("rev-parse", "main"))

    def test_remote_advertisement_guard_rejects_race_or_any_update(self):
        guard = ROOT / "ops/branch-janitor-hooks/pre-push"
        env = {**fixture_env(), "CARR_RETIRE_REF": "refs/heads/old", "CARR_RETIRE_HEAD": "a" * 40}
        for local, head in (("0" * 40, "b" * 40), ("b" * 40, "a" * 40)):
            result = subprocess.run([sys.executable, str(guard)], env=env, text=True,
                input=f"(delete) {local} refs/heads/old {head}\n", capture_output=True)
            self.assertEqual(result.returncode, 1)
        result = subprocess.run([sys.executable, str(guard)], env=env, text=True,
            input=f"(delete) {'0' * 40} refs/heads/old {'a' * 40}\n", capture_output=True)
        self.assertEqual(result.returncode, 0)

    def test_superseded_pr_closes_with_merged_successor_by_link_or_full_patch(self):
        for body in ("Superseded by #2", ""):
            with self.subTest(body=body):
                fixture = Fixture()
                base = fixture.git("rev-parse", "main")
                tree = fixture.tree("old")
                old = fixture.commit("feature", cwd=tree)
                landed = fixture.commit("feature")
                fixture.git("commit", "--amend", "-qm", "successor")
                landed = fixture.git("rev-parse", "main")
                fixture.git("push", "origin", "main")
                provider = FakeProvider([
                    pull(1, old, base, body=body),
                    pull(2, landed, base, merged=landed)])
                reaper = fixture.reaper(provider=provider)
                report = reaper.run({"fixture/repo": fixture.repo}, execute=True)
                self.assertFalse(report["errors"], report["errors"])
                self.assertEqual(provider.closed, [(1, 2)])
                self.assertEqual(report["counts"]["fixture/repo"]["pr"]["superseded"], 1)

    def test_unmerged_successor_and_partial_patch_cannot_close_pr(self):
        fixture = Fixture()
        base = fixture.git("rev-parse", "main")
        tree = fixture.tree("old")
        old = fixture.commit("unique work", cwd=tree)
        provider = FakeProvider([pull(1, old, base, body="Superseded by #2"), pull(2, base, base)])
        report = fixture.reaper(provider=provider).run({"fixture/repo": fixture.repo}, execute=True)
        self.assertFalse(report["errors"])
        self.assertFalse(provider.closed)
        self.assertEqual(report["counts"]["fixture/repo"]["pr"]["live"], 2)

    def test_patch_match_preserves_semantic_whitespace_and_full_file_content(self):
        fixture = Fixture()
        base = fixture.git("rev-parse", "main")
        tree = fixture.tree("different")
        first = fixture.commit("value = 1")
        second = fixture.commit("value=1", cwd=tree)
        reaper = fixture.reaper()
        self.assertNotEqual(reaper.patch(fixture.repo, base, first),
                            reaper.patch(fixture.repo, base, second))

    def test_watchdog_clears_idle_finding_after_fixture_remote_retirement(self):
        import job_watchdog as watchdog
        fixture = Fixture()
        fixture.git("push", "origin", "main:refs/heads/claude/old")
        config = watchdog.load_config(ROOT / "ops/config/job-watchdog.json")
        class Effects:
            def report(self, finding):
                return {}
        facts = {"branches": [{"repo": "fixture/repo", "name": "claude/old", "updated": 0}]}
        found = watchdog.detect(facts, config, 100000)
        watchdog.reconcile(fixture.repo, config, found, Effects(), 100000)
        report = fixture.reaper().run({"fixture/repo": fixture.repo}, execute=True)
        self.assertFalse(report["errors"])
        self.assertFalse(fixture.git("ls-remote", "origin", "refs/heads/claude/old"))
        watchdog.reconcile(fixture.repo, config, watchdog.detect({"branches": []}, config, 100100), Effects(), 100100)
        latest = watchdog.read_latest(fixture.repo / config["paths"]["findings"])
        self.assertEqual(latest[found[0]["key"]]["cleared_at"], watchdog.stamp(100100))

    def test_health_row_has_bound_action_and_clears_on_complete_evidence(self):
        from branch_retirement import health, save, DEFAULT_ROOTS
        fixture = Fixture()
        line, failed = health(fixture.repo, now=1000)
        self.assertTrue(failed)
        self.assertIn("owner orchestrator", line)
        self.assertIn("auto-clear", line)
        save(fixture.repo / "out/orch/branch-janitor-report.json", {
            "execute": True, "at": 1000, "errors": [], "rows": [], "actions": [],
            "counts": {repo: {} for repo in DEFAULT_ROOTS}})
        line, failed = health(fixture.repo, now=1001)
        self.assertFalse(failed)
        self.assertTrue(line.startswith("OK"))


def pull(number, head, base, *, body="", merged=None):
    return {"number": number, "state": "closed" if merged else "open", "body": body,
            "merged_at": "2026-01-01T00:00:00Z" if merged else None, "merge_commit_sha": merged,
            "html_url": f"https://github.com/fixture/repo/pull/{number}",
            "head": {"sha": head, "ref": "old" if number == 1 else "successor",
                     "repo": {"full_name": "fixture/repo"}},
            "base": {"sha": base, "ref": "main", "repo": {"full_name": "fixture/repo"}}}


class Fixture:
    def __init__(self):
        self.base = Path(tempfile.mkdtemp(prefix="branch-janitor-fixture-")).resolve()
        self.repo, self.remote = self.base / "repo", self.base / "remote.git"
        self.repo.mkdir()
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("config", "user.name", "Fixture")
        self.commit("initial")
        self.git("init", "--bare", str(self.remote))
        self.git("remote", "add", "origin", str(self.remote))
        self.git("push", "-u", "origin", "main")

    def git(self, *args, cwd=None):
        return subprocess.run(["git", *args], cwd=cwd or self.repo, env=fixture_env(),
                              text=True, capture_output=True, check=True).stdout.strip()

    def commit(self, text, cwd=None):
        path = cwd or self.repo
        (path / "file").write_text(text + "\n")
        self.git("add", "file", cwd=path)
        self.git("commit", "-qm", text, cwd=path)
        return self.git("rev-parse", "HEAD", cwd=path)

    def tree(self, name):
        tree = self.base / name
        self.git("worktree", "add", "-b", name, str(tree))
        self.age(tree)
        return tree

    def age(self, tree):
        old = time.time() - 10 * 86400
        for p in (tree / "file", tree / ".git", Path(hook.index_gitdir(str(tree))) / "index"):
            os.utime(p, (old, old))

    def reaper(self, **kwargs):
        options = {"provider": FakeProvider(), "process_probe": lambda paths: set(),
                   "ownership_probe": lambda path: "orphaned"}
        return hook.fleet_reaper(self.repo, **{**options, **kwargs})


class FakeProvider:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.closed = []

    def pulls(self, repo):
        return self.rows

    def pull(self, repo, number):
        return next(r for r in self.rows if r["number"] == number)

    def open_pulls(self, repo, branch):
        return [r for r in self.rows if r["state"] == "open" and r["head"]["ref"] == branch]

    def close(self, repo, number, successor):
        self.closed.append((number, successor))
        self.pull(repo, number)["state"] = "closed"

    def verify_repository(self, repo, path):
        pass


if __name__ == "__main__":
    unittest.main()
