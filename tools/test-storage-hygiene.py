#!/usr/bin/env python3
import importlib.util
import ast
import json
import os
import plistlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "storage_hygiene", ROOT / "tools" / "storage_hygiene.py")
assert SPEC is not None and SPEC.loader is not None
storage_hygiene = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = storage_hygiene
SPEC.loader.exec_module(storage_hygiene)


class StorageHygieneTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="storage-hygiene-test-")
        self.root = Path(self.temp.name)
        self.tmp = self.root / "T"
        self.clones = self.root / "X" / "com.google.Chrome.code_sign_clone"
        self.replay = self.root / "cache" / "carr-gate-replay"
        for path in (self.tmp, self.clones, self.replay):
            path.mkdir(parents=True)
        self.now = 2_000_000.0

    def tearDown(self):
        self.temp.cleanup()

    def old_dir(self, parent, name):
        path = parent / name
        path.mkdir()
        os.utime(path, (self.now - 90_000, self.now - 90_000))
        return path

    def test_plan_is_allowlisted_old_closed_and_bounded(self):
        clone = self.old_dir(self.clones, "code_sign_clone.old")
        active = self.old_dir(self.clones, "code_sign_clone.active")
        scratch = self.old_dir(self.tmp, "flash-old")
        unknown = self.old_dir(self.tmp, "other-old")
        replay = self.old_dir(self.replay, "run-old")
        plan = storage_hygiene.plan_cleanup(
            clone_root=self.clones, tmp_root=self.tmp, replay_root=self.replay,
            now=self.now, older_than_seconds=86_400, max_items=10,
            is_open=lambda path: path == active,
            is_locked=lambda path: False,
            deadline=lambda: False,
        )
        self.assertEqual({item.path for item in plan.removable}, {clone, scratch, replay})
        self.assertIn(active, plan.protected)
        self.assertIn(unknown, plan.unrecognized)

    def test_dry_run_never_removes_and_live_run_only_removes_plan(self):
        target = self.old_dir(self.tmp, "carr-old")
        ledger = self.root / "ledger.jsonl"
        plan = storage_hygiene.plan_cleanup(
            clone_root=self.clones, tmp_root=self.tmp, replay_root=self.replay,
            now=self.now, older_than_seconds=86_400, max_items=10,
            is_open=lambda path: False, is_locked=lambda path: False,
            deadline=lambda: False,
        )
        dry = storage_hygiene.apply_plan(plan, dry_run=True, ledger_path=ledger,
                                         record_finding=lambda payload: self.fail("dry run wrote record"))
        self.assertTrue(target.exists())
        self.assertEqual(dry.removed_count, 0)
        live = storage_hygiene.apply_plan(plan, dry_run=False, ledger_path=ledger,
                                          record_finding=lambda payload: {"ok": True},
                                          is_open=lambda path: False,
                                          is_locked=lambda path: False)
        self.assertFalse(target.exists())
        self.assertEqual(live.removed_count, 1)
        rows = [json.loads(line) for line in ledger.read_text().splitlines()]
        self.assertEqual([row["mode"] for row in rows], ["dry-run", "apply"])

    def test_live_run_rechecks_open_state_before_removal(self):
        target = self.old_dir(self.tmp, "carr-became-active")
        ledger = self.root / "ledger.jsonl"
        plan = storage_hygiene.plan_cleanup(
            clone_root=self.clones, tmp_root=self.tmp, replay_root=self.replay,
            now=self.now, older_than_seconds=86_400, max_items=10,
            is_open=lambda path: False, is_locked=lambda path: False,
            deadline=lambda: False,
        )
        result = storage_hygiene.apply_plan(
            plan, dry_run=False, ledger_path=ledger,
            record_finding=lambda payload: {"ok": True},
            is_open=lambda path: path == target,
            is_locked=lambda path: False)
        self.assertTrue(target.exists())
        self.assertIn(target, plan.protected)
        self.assertEqual(result.removed_count, 0)

    def test_unrecognized_count_is_exact_and_path_report_is_bounded(self):
        for index in range(28):
            self.old_dir(self.tmp, f"unknown-{index}")
        plan = storage_hygiene.plan_cleanup(
            clone_root=self.clones, tmp_root=self.tmp, replay_root=self.replay,
            now=self.now, older_than_seconds=86_400, max_items=10,
            is_open=lambda path: False, is_locked=lambda path: False,
            deadline=lambda: False,
        )
        self.assertEqual(plan.unrecognized_count, 28)
        self.assertEqual(len(plan.unrecognized), storage_hygiene.UNKNOWN_SAMPLE_LIMIT)

    def test_item_cap_rotates_across_authorized_categories(self):
        clones = {self.old_dir(self.clones, f"code_sign_clone.{index}")
                  for index in range(4)}
        scratch = self.old_dir(self.tmp, "carr-old")
        replay = self.old_dir(self.replay, "run-old")
        plan = storage_hygiene.plan_cleanup(
            clone_root=self.clones, tmp_root=self.tmp, replay_root=self.replay,
            now=self.now, older_than_seconds=86_400, max_items=3,
            is_open=lambda path: False, is_locked=lambda path: False,
            deadline=lambda: False,
        )
        selected = {item.path for item in plan.removable}
        self.assertEqual(len(selected & clones), 1)
        self.assertIn(scratch, selected)
        self.assertIn(replay, selected)

    def test_item_cap_stops_before_extra_candidates(self):
        for index in range(3):
            self.old_dir(self.tmp, f"carr-{index}")
        plan = storage_hygiene.plan_cleanup(
            clone_root=self.clones, tmp_root=self.tmp, replay_root=self.replay,
            now=self.now, older_than_seconds=86_400, max_items=2,
            is_open=lambda path: False, is_locked=lambda path: False,
            deadline=lambda: False,
        )
        self.assertEqual(len(plan.removable), 2)
        self.assertEqual(plan.stop_reason, "item-cap")

    def test_live_run_rechecks_age_before_removal(self):
        target = self.old_dir(self.tmp, "carr-freshened")
        ledger = self.root / "ledger.jsonl"
        plan = storage_hygiene.plan_cleanup(
            clone_root=self.clones, tmp_root=self.tmp, replay_root=self.replay,
            now=self.now, older_than_seconds=86_400, max_items=10,
            is_open=lambda path: False, is_locked=lambda path: False,
            deadline=lambda: False,
        )
        os.utime(target, (self.now, self.now))
        result = storage_hygiene.apply_plan(
            plan, dry_run=False, ledger_path=ledger,
            record_finding=lambda payload: {"ok": True},
            is_open=lambda path: False, is_locked=lambda path: False,
            now=lambda: self.now, older_than_seconds=86_400)
        self.assertTrue(target.exists())
        self.assertIn(target, plan.protected)
        self.assertEqual(result.removed_count, 0)

    def test_live_run_stops_deletion_at_the_time_cap(self):
        first = self.old_dir(self.tmp, "carr-first")
        second = self.old_dir(self.tmp, "carr-second")
        ledger = self.root / "ledger.jsonl"
        plan = storage_hygiene.plan_cleanup(
            clone_root=self.clones, tmp_root=self.tmp, replay_root=self.replay,
            now=self.now, older_than_seconds=86_400, max_items=10,
            is_open=lambda path: False, is_locked=lambda path: False,
            deadline=lambda: False,
        )
        checks = iter((False, True))
        result = storage_hygiene.apply_plan(
            plan, dry_run=False, ledger_path=ledger,
            record_finding=lambda payload: {"ok": True},
            is_open=lambda path: False, is_locked=lambda path: False,
            deadline=lambda: next(checks))
        self.assertEqual(result.removed_count, 1)
        self.assertEqual(sum(path.exists() for path in (first, second)), 1)
        self.assertEqual(plan.stop_reason, "time-cap")

    def test_health_row_binds_threshold_to_investigation(self):
        row = storage_hygiene.health_row(
            used_bytes=1_000_000_000_001, clone_count=8465,
            threshold_bytes=1_000_000_000_000)
        self.assertIn("WARN", row)
        self.assertIn("8465", row)
        self.assertIn("opens the storage investigation loop", row)

    def test_live_clean_run_is_not_labeled_dry_run(self):
        ledger = self.root / "ledger.jsonl"
        result = storage_hygiene.apply_plan(
            storage_hygiene.CleanupPlan(), dry_run=False, ledger_path=ledger,
            record_finding=lambda payload: self.fail("clean run wrote a record"),
            disk_used=0)
        self.assertEqual(result.record_status, "not-needed")
        self.assertEqual(json.loads(ledger.read_text())["record_status"], "not-needed")

    def test_record_failure_still_writes_episode_ledger(self):
        self.old_dir(self.tmp, "carr-old")
        ledger = self.root / "ledger.jsonl"
        plan = storage_hygiene.plan_cleanup(
            clone_root=self.clones, tmp_root=self.tmp, replay_root=self.replay,
            now=self.now, older_than_seconds=86_400, max_items=10,
            is_open=lambda path: False, is_locked=lambda path: False,
            deadline=lambda: False)
        result = storage_hygiene.apply_plan(
            plan, dry_run=False, ledger_path=ledger,
            record_finding=lambda payload: (_ for _ in ()).throw(RuntimeError("offline")),
            is_open=lambda path: False, is_locked=lambda path: False)
        row = json.loads(ledger.read_text())
        self.assertEqual(result.record_status, "record-failed")
        self.assertEqual(row["record_status"], "record-failed")
        self.assertTrue(row["finding_episode"])

    def test_gate_replay_lock_is_observed_without_creating_one(self):
        run = self.old_dir(self.replay, "run-locked")
        lock = run / ".active.lock"
        lock.write_text("")
        with lock.open("r+") as handle:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertTrue(storage_hygiene.replay_is_locked(run))
        unlocked = self.old_dir(self.replay, "run-unlocked")
        self.assertFalse(storage_hygiene.replay_is_locked(unlocked))
        self.assertFalse((unlocked / ".active.lock").exists())

    def test_health_check_exposes_storage_row(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "health-check.py"),
             "--section", "storage"], cwd=ROOT, capture_output=True, text=True)
        self.assertIn(result.returncode, (0, 1), result.stderr)
        self.assertIn("storage hygiene", result.stdout)
        if sys.platform == "darwin":
            self.assertIn("opens the storage investigation loop", result.stdout)
        else:
            self.assertIn("not applicable outside macOS", result.stdout)

    def test_launchd_job_uses_scheduled_wrapper_and_is_not_run_at_load(self):
        path = ROOT / "ops" / "launchd" / "com.carr.storage-hygiene.plist"
        job = plistlib.loads(path.read_bytes())
        self.assertEqual(job["Label"], "com.carr.storage-hygiene")
        self.assertEqual(job["ProgramArguments"][:4], [
            "/bin/zsh", "{{REPO}}/bin/run-scheduled.sh",
            "storage-hygiene", "launchd.run"])
        self.assertIn("{{REPO}}/tools/storage_hygiene.py", job["ProgramArguments"])
        self.assertFalse(job["RunAtLoad"])
        self.assertEqual(job["StandardOutPath"], "{{REPO}}/out/storage-hygiene.log")

    def test_flash_tests_use_managed_temporary_directories(self):
        for relative in ("tools/test-flash-run-sandbox.py",
                         "tools/test-flash-run-escalate.py",
                         "tools/test-flash-script.py"):
            tree = ast.parse((ROOT / relative).read_text())
            bare = [node.lineno for node in ast.walk(tree)
                    if isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "tempfile"
                    and node.func.attr == "mkdtemp"]
            self.assertEqual(bare, [], f"{relative} has bare mkdtemp calls at {bare}")


if __name__ == "__main__":
    unittest.main()
