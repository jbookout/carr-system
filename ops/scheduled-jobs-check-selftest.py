#!/usr/bin/env python3
"""Fixture evidence exercises the scheduled-job report used by health and watchdog."""
import copy
import sys
import unittest
import json
import subprocess
import tempfile
from unittest.mock import patch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import scheduled_jobs as jobs


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.manifest = {
            "schema_version": 1, "canonical_checkout": "/machine/carr-system",
            "jobs": [{"label": "com.carr.test", "scheduler": "launchd",
                      "program_path": "/machine/carr-system/bin/task.sh",
                      "required_checkout": "/machine/carr-system",
                      "interval": {"StartInterval": 120}, "expected_enabled": True,
                      "expected_installed": True, "log_path": "/logs/test.log",
                      "log_max_age_seconds": 600, "owner": "orchestrator",
                      "done_signal": "ops.run task/launchd.run"}]}
        self.snapshot = {
            "plists": {"com.carr.test": {
                "ProgramArguments": ["/bin/sh", "/machine/carr-system/bin/task.sh"],
                "StartInterval": 120, "StandardOutPath": "/logs/test.log"}},
            "launchctl_list": "PID\tStatus\tLabel\n-\t0\tcom.carr.test\n",
            "launchctl_disabled": 'disabled services = { "com.carr.test" => enabled }',
            "launchctl_print": {"com.carr.test": "service = {\n program = /bin/sh\n arguments = {\n 0 = /bin/sh\n 1 = /machine/carr-system/bin/task.sh\n }\n last exit code = 0\n}"},
            "log_mtimes": {"/logs/test.log": 990},
            "cron": "", "errors": [],
            "git": {"ahead": 0, "behind": 0, "branch": "main", "remote_matches": True}}

    def test_wrong_runtime_checkout_is_actionable_even_when_disk_is_correct(self):
        self.snapshot["launchctl_print"]["com.carr.test"] = self.snapshot["launchctl_print"]["com.carr.test"].replace("/machine/carr-system/", "/machine/carr-system-feature/")
        rows = jobs.report(self.manifest, self.snapshot, now=1000)
        self.assertEqual([(r["label"], r["code"]) for r in rows],
                         [("com.carr.test", "wrong_checkout")])
        line = jobs.render(rows)[0]
        for text in ("loop scheduled_jobs:com.carr.test:wrong_checkout", "owner orchestrator",
                     "fix:", "verify:", "auto-clear:", "job-watchdog.py scan"):
            self.assertIn(text, line)

    def test_duration_monitor_is_declared_and_missing_installation_is_detected(self):
        import plistlib
        manifest = json.loads((ROOT / "ops/config/scheduled-jobs.v1.json").read_text())
        job = next((j for j in manifest["jobs"] if j["label"] == "com.carr.build-duration-check"), None)
        self.assertIsNotNone(job)
        plist = plistlib.loads((ROOT / "ops/launchd/com.carr.build-duration-check.plist").read_bytes().replace(b"{{REPO}}", str(Path.home() / "carr-system").encode()))
        snapshot = copy.deepcopy(self.snapshot)
        snapshot.update(machine_role="primary", plists={job["label"]: plist},
                        launchctl_list=f"-\t0\t{job['label']}\n", launchctl_disabled="",
                        launchctl_print={}, log_mtimes={jobs.expand(job["activity_path"]): 990})
        self.assertEqual(jobs.report({**manifest, "jobs": [job]}, snapshot, 1000), [])
        snapshot["plists"] = {}
        snapshot["launchctl_list"] = ""
        self.assertIn("missing_job", [r["code"] for r in jobs.report({**manifest, "jobs": [job]}, snapshot, 1000)])

    def test_shared_scheduler_activity_cannot_refresh_job_specific_completion(self):
        manifest = json.loads((ROOT / "ops/config/scheduled-jobs.v1.json").read_text())
        for label in ("com.carr.rules-refresh", "com.carr.local-briefs", "com.carr.videopipeline"):
            with self.subTest(label=label):
                job = copy.deepcopy(next(j for j in manifest["jobs"] if j["label"] == label))
                # Use the declared cadence and activity source with a known matching job fixture.
                job.update(label="com.carr.test", program_path=self.manifest["jobs"][0]["program_path"],
                           required_checkout="/machine/carr-system", interval={"StartInterval": 120},
                           expected_enabled=True, expected_installed=True)
                snapshot = copy.deepcopy(self.snapshot)
                snapshot["log_mtimes"][jobs.expand(job["log_path"])] = 999
                rows = jobs.report({"jobs": [job]}, snapshot, 1000)
                self.assertIn("stale_log", [r["code"] for r in rows])
                snapshot["log_mtimes"][jobs.expand(job["activity_path"])] = 999
                self.assertNotIn("stale_log", [r["code"] for r in jobs.report({"jobs": [job]}, snapshot, 1000)])

    def test_working_directory_in_a_nested_worktree_or_symlinked_clone_is_drift(self):
        for actual in ("/machine/carr-system/.claude/worktrees/other", "/tmp/other-clone"):
            with self.subTest(actual=actual):
                self.snapshot["plists"]["com.carr.test"]["WorkingDirectory"] = "/shortcut"
                self.snapshot["checkout_roots"] = {"/shortcut": actual}
                self.assertIn("wrong_checkout", [r["code"] for r in jobs.report(self.manifest, self.snapshot, 1000)])

    def test_collector_reads_plist_and_only_runs_observation_commands(self):
        import plistlib
        with tempfile.TemporaryDirectory() as raw:
            home = Path(raw)
            agents = home / "Library/LaunchAgents"
            agents.mkdir(parents=True)
            plist = self.snapshot["plists"]["com.carr.test"]
            plist["Label"] = "com.carr.test"
            (agents / "com.carr.test.plist").write_bytes(plistlib.dumps(plist))
            def run(argv, **kwargs):
                if argv[:2] == ["launchctl", "list"]: output = self.snapshot["launchctl_list"]
                elif argv[:2] == ["launchctl", "print-disabled"]: output = self.snapshot["launchctl_disabled"]
                elif argv[:2] == ["launchctl", "print"]: output = self.snapshot["launchctl_print"]["com.carr.test"]
                elif "--left-right" in argv: output = "0\t2\n"
                elif "symbolic-ref" in argv: output = "main\n"
                elif "ls-remote" in argv: output = "a"*40 + "\trefs/heads/main\n"
                elif "--show-toplevel" in argv: return subprocess.CompletedProcess(argv, 128, "", "not a git repository")
                elif "rev-parse" in argv: output = "a"*40 + "\n"
                else: output = ""
                return subprocess.CompletedProcess(argv, 0, output, "")
            with patch.object(Path, "home", return_value=home), \
                 patch.object(jobs.machine_role, "is_primary", return_value=False), \
                 patch.object(jobs.subprocess, "run", side_effect=run) as calls:
                snap = jobs.collect(self.manifest)
            self.assertEqual(snap["git"]["behind"], 2)
            self.assertEqual(snap["plists"]["com.carr.test"]["Label"], "com.carr.test")
            commands = [c.args[0] for c in calls.call_args_list]
            for argv in commands:
                self.assertNotIn("fetch", argv)
                self.assertNotIn("enable", argv)
                self.assertNotIn("bootstrap", argv)
                self.assertNotIn("load", argv)

    def test_reports_missing_unknown_disabled_failed_stale_and_behind(self):
        missing = copy.deepcopy(self.manifest["jobs"][0])
        missing["label"] = "local.carr-missing"
        self.manifest["jobs"].append(missing)
        self.snapshot["launchctl_list"] += "-\t7\tcom.carr.unknown\n"
        self.snapshot["launchctl_disabled"] = '"com.carr.test" => disabled'
        self.snapshot["launchctl_print"]["com.carr.test"] += "\n last exit code = 9\n"
        self.snapshot["log_mtimes"]["/logs/test.log"] = 10
        self.snapshot["git"]["behind"] = 4
        rows = jobs.report(self.manifest, self.snapshot, now=1000)
        self.assertEqual({(r["label"], r["code"]) for r in rows}, {
            ("local.carr-missing", "missing_job"), ("com.carr.unknown", "unknown_job"),
            ("com.carr.test", "disabled_but_expected"), ("com.carr.test", "failing_exit"),
            ("com.carr.test", "stale_log"), ("canonical", "behind_main")})

    def test_complete_healthy_snapshot_has_no_drift(self):
        self.assertEqual(jobs.report(self.manifest, self.snapshot, now=1000), [])

    def test_launchctl_environment_path_does_not_become_a_runtime_checkout(self):
        self.snapshot["launchctl_print"]["com.carr.test"] += "\n environment = {\n PATH => /opt/homebrew/bin:/usr/bin\n }\n"
        self.snapshot["checkout_roots"] = {"/opt/homebrew/bin:/usr/bin": "/opt/homebrew"}
        self.assertEqual(jobs.report(self.manifest, self.snapshot, now=1000), [])

    def test_declared_retirement_does_not_fail_for_absent_disabled_job(self):
        job = self.manifest["jobs"][0]
        job.update(expected_enabled=False, expected_installed=False)
        self.snapshot["plists"] = {}
        self.snapshot["launchctl_list"] = "PID Status Label\n"
        self.snapshot["launchctl_disabled"] = '"com.carr.test" => true'
        self.assertEqual(jobs.report(self.manifest, self.snapshot, now=1000), [])

    def test_primary_monitor_is_expected_only_on_the_primary_machine(self):
        job = self.manifest["jobs"][0]
        job["expected_enabled_by_role"] = {"primary": True, "secondary": False}
        job["expected_installed_by_role"] = {"primary": True, "secondary": False}
        self.snapshot.update(plists={}, launchctl_list="", machine_role="secondary")
        self.assertEqual(jobs.report(self.manifest, self.snapshot, now=1000), [])
        self.snapshot["machine_role"] = "primary"
        self.assertEqual([r["code"] for r in jobs.report(self.manifest, self.snapshot, now=1000)], ["missing_job"])

    def test_cron_schedule_and_unknown_commands_are_checked_without_exposing_arguments(self):
        self.manifest["jobs"] = [{**self.manifest["jobs"][0], "label": "cron.3f32cbb585d897c7",
                                 "scheduler": "cron", "interval": "0 * * * *"}]
        self.snapshot.update(plists={}, launchctl_list="", launchctl_disabled="", launchctl_print={})
        self.snapshot["cron"] = "TOKEN=private-value\n*/5 * * * * /machine/carr-system/bin/task.sh\n"
        rows = jobs.report(self.manifest, self.snapshot, now=1000)
        self.assertIn("interval_drift", [r["code"] for r in rows])
        self.snapshot["cron"] += "@daily /tmp/unknown.sh --token private-value\n"
        rows = jobs.report(self.manifest, self.snapshot, now=1000)
        self.assertIn("unknown_job", [r["code"] for r in rows])
        self.assertNotIn("private-value", "\n".join(jobs.render(rows)))

    def test_read_failures_stay_visible_and_cli_returns_nonzero(self):
        self.snapshot["errors"] = ["launchctl_disabled"]
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            manifest, snapshot = root / "manifest.json", root / "snapshot.json"
            manifest.write_text(json.dumps(self.manifest))
            snapshot.write_text(json.dumps(self.snapshot))
            result = subprocess.run([sys.executable, str(ROOT / "ops/scheduled-jobs-check.py"),
                                     "--manifest", str(manifest), "--fixture", str(snapshot),
                                     "--now", "1000", "--json"], capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(json.loads(result.stdout)[0]["code"], "evidence_unavailable")

    def test_portable_watch_paths_compare_with_live_absolute_paths(self):
        self.manifest["jobs"][0]["interval"]["WatchPaths"] = ["~/Recordings"]
        self.snapshot["plists"]["com.carr.test"]["WatchPaths"] = [str(Path.home() / "Recordings")]
        self.assertEqual(jobs.report(self.manifest, self.snapshot, now=1000), [])

    def test_watchdog_files_the_same_loop_key_and_does_not_clear_blinded_evidence(self):
        sys.path.insert(0, str(ROOT / "lib"))
        import job_watchdog as watchdog
        config = watchdog.load_config(ROOT / "ops/config/job-watchdog.json")
        self.snapshot["git"]["behind"] = 3
        rows = jobs.report(self.manifest, self.snapshot, now=1000)
        found = watchdog.detect({"scheduled_jobs": rows}, config, 1000)
        self.assertEqual(found[0]["key"], "scheduled_jobs:canonical:behind_main")
        self.assertEqual(found[0]["owner"], "orchestrator")
        self.assertIn("fleet-sync", found[0]["next_action"])

    def test_health_jobs_section_prints_bound_action_and_records_finding(self):
        self.snapshot["git"]["behind"] = 3
        fixture = {"now": "2026-10-05T17:00:00Z", "jobs": [], "job_definitions": [],
                   "scheduled_jobs": self.snapshot}
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            path = root / "health.json"
            path.write_text(json.dumps(fixture))
            result = subprocess.run([sys.executable, str(ROOT / "tools/health-check.py"),
                                     "--section", "jobs", "--fixture", str(path)],
                                    capture_output=True, text=True, timeout=15)
            self.assertIn("scheduled_jobs:canonical:behind_main", result.stdout)
            self.assertIn("auto-clear:", result.stdout)

    def test_incomplete_watchdog_scan_retains_previous_drift(self):
        import job_watchdog as watchdog
        config = watchdog.load_config(ROOT / "ops/config/job-watchdog.json")
        row = {"key": "scheduled_jobs:canonical:behind_main", "kind": "scheduled_job_drift",
               "subject": "canonical", "reason": "behind", "next_action": "repair fleet-sync",
               "owner": "orchestrator", "needs_joe": None}
        class Effects:
            cleared = []
            def report(self, finding):
                return {"loop_id": "fixture-loop"}
            def clear(self, finding, active):
                self.cleared.append(finding["loop_id"])
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            effects = Effects()
            watchdog.reconcile(root, config, [row], effects, 1000)
            error = watchdog.finding("collection_error", "scheduled_jobs", "read failed", config,
                                     blinds=["scheduled_job_drift"])
            watchdog.reconcile(root, config, [error], effects, 1001)
            prior = watchdog.read_latest(root / config["paths"]["findings"])[row["key"]]
            self.assertIsNone(prior["cleared_at"])
            watchdog.reconcile(root, config, [], effects, 1002)
            prior = watchdog.read_latest(root / config["paths"]["findings"])[row["key"]]
            self.assertIsNotNone(prior["cleared_at"])
            self.assertEqual(effects.cleared, ["fixture-loop"])

    def test_recovery_closes_versioned_loop_and_recurrence_gets_a_new_episode(self):
        import job_watchdog as watchdog
        config = watchdog.load_config(ROOT / "ops/config/job-watchdog.json")
        row = {"key": "scheduled_jobs:canonical:behind_main", "kind": "scheduled_job_drift",
               "subject": "canonical", "reason": "behind", "next_action": "repair fleet-sync",
               "owner": "orchestrator", "needs_joe": None, "first_seen": "episode-one"}
        responses = [json.dumps({"ok": True, "loop_id": "fixture-loop"}),
                     json.dumps({"loop": {"loop_id": "fixture-loop", "status": "open", "version": 4}, "amended": False, "amendments": []}),
                     json.dumps({"ok": True, "status": "done"}),
                     json.dumps({"ok": True, "loop_id": "fixture-loop-two"})]
        with tempfile.TemporaryDirectory() as raw:
            effects = watchdog.Effects(Path(raw), config)
            with patch.object(watchdog, "board_task") as board, \
                 patch.object(watchdog, "command", side_effect=responses) as calls:
                receipt = effects.report(row)
                self.assertEqual(receipt["loop_id"], "fixture-loop")
                effects.clear({**row, **receipt}, [])
                payload = json.loads(calls.call_args_list[-1].args[0][-1])
                self.assertEqual(payload["base_version"], 4)
                self.assertEqual(payload["resolution"], "done")
                self.assertIn("complete scheduled-job scan", payload["outcome"])
                effects.report({**row, "first_seen": "episode-two"})
                first = json.loads(calls.call_args_list[0].args[0][-1])
                second = json.loads(calls.call_args_list[-1].args[0][-1])
                self.assertNotEqual(first["idempotency_key"], second["idempotency_key"])
                board.assert_not_called()


if __name__ == "__main__":
    unittest.main()
