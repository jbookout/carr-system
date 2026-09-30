import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
FIXTURES = ROOT / "tools/fixtures/job-watchdog"


class ReplayTests(unittest.TestCase):
    def test_tonights_stdin_hangs(self):
        import job_watchdog as w
        config = w.load_config(ROOT / "ops/config/job-watchdog.json")
        for name in ("rv-1423.log", "src10.log"):
            with self.subTest(name=name):
                facts = {"jobs": [{"id": name, "card": name, "alive": True,
                         "start": 1000, "limit": 3600, "log_mtime": 1990,
                         "log_tail": (FIXTURES / name).read_text()}]}
                self.assertIn("job_hang", {f["kind"] for f in w.detect(facts, config, 2000)})

    def test_review_and_queue_replay(self):
        import job_watchdog as w
        config = w.load_config(ROOT / "ops/config/job-watchdog.json")
        prs = [dict(json.loads((FIXTURES / f"pr-{n}.json").read_text()),
                    repo="jbookout/doctorcre-app") for n in range(95, 101)]
        found = w.detect({"prs": prs, "queue": "", "logs": [
            {"path": "queue.log", "type": "queue", "mtime": 2000000000,
             "tail": (FIXTURES / "queue.log").read_text()}]}, config, 2000000000)
        blocked = {f["pr"] for f in found if f["kind"] == "pr_blocked_review"}
        self.assertEqual(blocked, {95, 96, 97, 99, 100})
        self.assertIn("queue_error", {f["kind"] for f in found})
        # PR98 is approved and green, but GitHub reports UNKNOWN mergeability.
        # It must not be queued until a fresh mergeability read can establish it.
        self.assertFalse(any(f.get("pr") == 98 for f in found))

    def test_clean_fixture_has_no_findings(self):
        import job_watchdog as w
        config = w.load_config(ROOT / "ops/config/job-watchdog.json")
        self.assertEqual(w.detect(json.loads((FIXTURES / "clean.json").read_text()), config, 2000), [])

    def test_remaining_failure_classes_and_threshold_boundaries(self):
        import job_watchdog as w
        c = w.load_config(ROOT / "ops/config/job-watchdog.json")
        facts = {"jobs": [
            {"id": "dead", "start": 1900, "limit": 3600, "alive": False},
            {"id": "silent", "start": 1000, "limit": 3600, "alive": True, "log_mtime": 1400},
            {"id": "over", "start": 1000, "limit": 999, "alive": True, "log_mtime": 2000},
            {"id": "failed", "exit_code": 1, "log_tail": "authentication required"}],
            "branches": [{"repo": "repo", "name": "claude/old", "updated": -10000}],
            "logs": [{"type": "release", "path": "release.log", "mtime": 1000, "tail": "BLOCKED worker"}]}
        found = w.detect(facts, c, 2000)
        self.assertEqual({f["kind"] for f in found}, {"job_dead", "job_silent", "job_over_limit", "job_failed", "branch_idle", "pipeline_blocked", "pipeline_stale"})
        self.assertEqual(next(f["needs_joe"] for f in found if f["kind"] == "job_failed"), "credentials")
        facts["jobs"][1]["log_mtime"] = 1400.1
        self.assertNotIn("job_silent", {f["kind"] for f in w.detect(facts, c, 2000)})

    def test_current_head_latest_review_and_active_fixer(self):
        import job_watchdog as w
        c = w.load_config(ROOT / "ops/config/job-watchdog.json")
        p = {"repo": "jbookout/doctorcre-app", "number": 8, "headRefOid": "a" * 40,
             "updatedAt": "2026-01-01T00:00:00Z", "comments": [], "mergeable": "MERGEABLE",
             "statusCheckRollup": [{"conclusion": "SUCCESS", "status": "COMPLETED"}]}
        def comment(verdict, head, minute):
            return {"body": verdict + "\nReviewed-SHA: " + head,
                    "createdAt": f"2026-01-01T00:{minute}:00Z"}
        p["comments"] = [comment("REVIEW: BLOCKED", "b" * 40, "00")]
        self.assertEqual(w.detect({"prs": [p]}, c, 2000000000), [])
        p["comments"].append(comment("REVIEW: BLOCKED", "a" * 40, "01"))
        self.assertEqual(w.detect({"prs": [p]}, c, 2000000000)[0]["kind"], "pr_blocked_review")
        p["comments"].append(comment("APPROVE", "a" * 40, "02"))
        self.assertEqual(w.detect({"prs": [p]}, c, 2000000000)[0]["kind"], "pr_ready")
        p["comments"].pop()
        job = {"id": "fix", "card": "fix", "repo": p["repo"], "pr": 8, "head": p["headRefOid"],
               "alive": True, "start": 1999999900, "limit": 3600, "log_mtime": 2000000000}
        self.assertEqual(w.detect({"prs": [p], "jobs": [job]}, c, 2000000000), [])


class RunnerTests(unittest.TestCase):
    def test_clean_scan_cli_completes_without_effects(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "bin/gh"
            executable.parent.mkdir()
            executable.write_text("#!/bin/sh\nprintf '[[]]\\n'\n")
            executable.chmod(0o755)
            config = json.loads((ROOT / "ops/config/job-watchdog.json").read_text())
            config["paths"]["merge_queue"] = "queue.txt"
            config["paths"]["queue_logs"] = []
            config["actions"]["file_defects"] = False
            cp = root / "config.json"
            cp.write_text(json.dumps(config))
            env = dict(os.environ, PATH=str(executable.parent) + os.pathsep + os.environ["PATH"])
            result = subprocess.run([sys.executable, str(ROOT / "tools/job-watchdog.py"), "--root", directory, "--config", str(cp), "scan"],
                                    env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "")
            ledger = [json.loads(s) for s in (root / "out/watchdog/runs.jsonl").read_text().splitlines()]
            self.assertEqual(ledger[-1]["status"], "completed")
            self.assertEqual(ledger[-1]["findings"], 0)

    def test_command_gets_eof_and_exit_is_recorded_on_board(self):
        with tempfile.TemporaryDirectory() as directory:
            env = dict(os.environ, CARR_JOB_ROOT=directory, CARR_JOB_BOARD="test")
            result = subprocess.run(["bash", str(ROOT / "bin/agent-run.sh"), "stdin-card",
                                     "deterministic", "1", "--", sys.executable, "-c",
                                     "import sys; print('EOF=' + repr(sys.stdin.read())); sys.exit(7)"],
                                    input="must not reach child", text=True, capture_output=True, env=env)
            self.assertEqual(result.returncode, 7, result.stderr)
            rows = [json.loads(s) for s in (Path(directory) / "out/jobs/registry.jsonl").read_text().splitlines()]
            self.assertEqual(rows[0]["card"], "stdin-card")
            self.assertGreater(rows[0]["pid"], 0)
            self.assertEqual(rows[-1]["exit_code"], 7)
            self.assertIn("EOF=''", rows[-1]["log_tail"])
            board = json.loads((Path(directory) / "out/boards/test.json").read_text())
            self.assertEqual(board["tasks"]["stdin-card"]["status"], "blocked")

    def test_registered_hang_is_terminated_and_relaunched_once(self):
        import job_watchdog as w
        c = w.load_config(ROOT / "ops/config/job-watchdog.json")
        c["board"] = "test"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cp = root / "config.json"
            cp.write_text(json.dumps(c))
            env = dict(os.environ, CARR_JOB_ROOT=directory, CARR_WATCHDOG_CONFIG=str(cp), CARR_JOB_BOARD="test")
            proc = subprocess.Popen(["bash", str(ROOT / "bin/agent-run.sh"), "hung-card", "deterministic", "1", "--",
                                     sys.executable, "-u", "-c", "import time; print('Reading additional input from stdin...'); time.sleep(60)"],
                                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, env=env)
            replacement = None
            try:
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    rows = w.read_latest(root / c["paths"]["registry"])
                    if rows:
                        break
                    time.sleep(0.02)
                self.assertTrue(rows)
                job = next(iter(rows.values()))
                effects = w.Effects(root, c)
                effects.config_path = cp
                replacement = effects.restart({"job": job})
                proc.wait(timeout=5)
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    rows = w.read_latest(root / c["paths"]["registry"])
                    if replacement["job_id"] in rows:
                        break
                    time.sleep(0.02)
                retry = rows[replacement["job_id"]]
                self.assertEqual(retry["restart_count"], 1)
                self.assertEqual(retry["root_id"], job["id"])
                self.assertTrue(rows[job["id"]]["superseded"])
                self.assertIsNone(w.process_identity(job["pid"]))
            finally:
                if proc.poll() is None:
                    proc.terminate()
                    proc.wait(timeout=5)
                if replacement:
                    import signal
                    os.kill(replacement["wrapper_pid"], signal.SIGTERM)
                    deadline = time.monotonic() + 5
                    while time.monotonic() < deadline:
                        rows = w.read_latest(root / c["paths"]["registry"])
                        if "exit_code" in rows.get(replacement["job_id"], {}):
                            break
                        time.sleep(0.02)
                    effects.children[-1].wait(timeout=5)
                if proc.stderr:
                    proc.stderr.close()


class StateTests(unittest.TestCase):
    def test_fixer_uses_verified_model_room_desk_and_agent_runner(self):
        import job_watchdog as w
        from unittest.mock import patch
        c = w.load_config(ROOT / "ops/config/job-watchdog.json")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = root / "authorized"
            repository.mkdir()
            c["repository_roots"]["jbookout/carr-system"] = str(repository)
            f = w.finding("pr_blocked_review", "jbookout/carr-system#1@" + "a" * 40, "REVIEW: BLOCKED\nrepair stdin", c,
                          repo="jbookout/carr-system", pr=1, head="a" * 40, card="pr-test")
            calls = []
            def fake_command(argv, config, cwd=None):
                calls.append(argv)
                if argv[1:3] == ["remote", "get-url"]:
                    return "https://github.com/jbookout/carr-system.git\n"
                if argv[1:3] == ["rev-parse", "FETCH_HEAD"]:
                    return "a" * 40
                if "register" in argv:
                    name = argv[argv.index("register") + 1]
                    Path(argv[argv.index("--registry") + 1]).write_text(json.dumps({"desks": {name: {
                        **c["fixer"], "cwd": argv[argv.index("--cwd") + 1]}}}))
                return ""
            effects = w.Effects(root, c)
            with patch.object(w, "command", side_effect=fake_command), patch.object(effects, "launch", return_value={"ok": True}) as launch:
                effects.fix(f)
            register = next(argv for argv in calls if "register" in argv)
            self.assertEqual(register[register.index("--model") + 1], "gpt-6.1-sol")
            self.assertEqual(register[register.index("--effort") + 1], "high")
            dispatch = launch.call_args.args[1]
            self.assertIn("send", dispatch)
            self.assertIn("--fresh", dispatch)
            self.assertIn("REVIEW: BLOCKED", dispatch[-2])

    def test_credentials_stay_blocked_in_needs_joe_lane(self):
        import job_watchdog as w
        c = w.load_config(ROOT / "ops/config/job-watchdog.json")
        c["actions"]["file_defects"] = False
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            f = w.finding("job_failed", "credential", "authentication required", c,
                          card="credential", needs_joe="credentials")
            w.Effects(root, c).report(f)
            state = json.loads((root / "out/boards/carr-v5.json").read_text())
            self.assertEqual(state["tasks"]["credential"]["status"], "blocked")
            self.assertEqual(state["tasks"]["credential"]["lane"], "needs-joe")
            self.assertIn("Needs Joe", (root / "out/boards/carr-v5.html").read_text())

    def test_fixer_and_enqueue_are_once_per_head_and_findings_clear(self):
        import job_watchdog as w
        c = w.load_config(ROOT / "ops/config/job-watchdog.json")
        class Effects:
            calls = []
            def act(self, action, f):
                self.calls.append((action, f["key"]))
                return {"ok": True}
            def report(self, f):
                return {"ok": True}
        with tempfile.TemporaryDirectory() as directory:
            effects = Effects()
            found = [w.finding("pr_blocked_review", "repo#1@abc", "review", c, repo="repo", pr=1, head="abc")]
            w.reconcile(Path(directory), c, found, effects, 100)
            w.reconcile(Path(directory), c, found, effects, 200)
            self.assertEqual(effects.calls, [("fix_once", found[0]["key"])])
            w.reconcile(Path(directory), c, [], effects, 300)
            states = w.read_latest(Path(directory) / c["paths"]["findings"])
            self.assertEqual(states[found[0]["key"]]["first_seen"], w.stamp(100))
            self.assertEqual(states[found[0]["key"]]["cleared_at"], w.stamp(300))
            w.reconcile(Path(directory), c, found, effects, 400)
            self.assertEqual(len(effects.calls), 1)

    def test_interrupted_action_is_not_reexecuted_and_collection_error_does_not_clear(self):
        import job_watchdog as w
        c = w.load_config(ROOT / "ops/config/job-watchdog.json")
        class Effects:
            def act(self, action, f):
                raise AssertionError("must never retry ambiguous intent")
            def report(self, f):
                return {"ok": True}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            f = w.finding("pr_blocked_review", "repo#1@abc", "review", c)
            w.append(root / c["paths"]["actions"], {"key": f["key"], "status": "intent", "action": "fix_once"})
            w.reconcile(root, c, [f], Effects(), 100)
            state = w.read_latest(root / c["paths"]["findings"])
            self.assertTrue(any(x["kind"] == "action_error" for x in state.values()))
            w.reconcile(root, c, [w.finding("collection_error", "repo", "network unavailable", c)], Effects(), 200)
            self.assertIsNone(w.read_latest(root / c["paths"]["findings"])[f["key"]]["cleared_at"])

    def test_second_hang_is_blocked_without_another_restart(self):
        import job_watchdog as w
        c = w.load_config(ROOT / "ops/config/job-watchdog.json")
        class Effects:
            calls = []
            def report(self, f):
                self.calls.append("report")
            def act(self, action, f):
                self.calls.append(action)
        with tempfile.TemporaryDirectory() as directory:
            f = w.finding("job_hang", "retry", "stdin hang", c,
                          job={"id": "retry", "root_id": "original", "restart_count": 1})
            effects = Effects()
            w.reconcile(Path(directory), c, [f], effects, 100)
            self.assertEqual(effects.calls, ["report"])

    def test_enqueue_is_deduplicated_and_digest_reads_only_open_findings(self):
        import job_watchdog as w
        c = w.load_config(ROOT / "ops/config/job-watchdog.json")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            c["paths"]["merge_queue"] = "queue.txt"
            effects = w.Effects(root, c)
            p = {"repo": "jbookout/carr-system", "pr": 5, "head": "a" * 40}
            effects.enqueue(p)
            effects.enqueue(p)
            self.assertEqual(len((root / "queue.txt").read_text().splitlines()), 1)
            w.append(root / c["paths"]["findings"], {"key": "closed", "reason": "gone", "cleared_at": "now"})
            w.append(root / c["paths"]["findings"], {"key": "open", "kind": "job_hang", "reason": "stdin", "next_action": "inspect", "cleared_at": None})
            digest = w.digest(root, c)
            self.assertIn("stdin", digest)
            self.assertNotIn("gone", digest)

    def test_reused_pid_is_never_killed(self):
        import job_watchdog as w
        from unittest.mock import patch
        c = w.load_config(ROOT / "ops/config/job-watchdog.json")
        with tempfile.TemporaryDirectory() as directory, patch.object(w, "process_identity", return_value="new process"), patch.object(w.os, "killpg") as kill:
            effects = w.Effects(Path(directory), c)
            with self.assertRaisesRegex(RuntimeError, "identity"):
                effects.restart({"job": {"id": "old", "pid": 42, "pgid": 42,
                                          "process_identity": "old process"}})
            kill.assert_not_called()

    def test_restart_waits_for_descendants_after_leader_exits(self):
        import job_watchdog as w
        from unittest.mock import patch
        c = w.load_config(ROOT / "ops/config/job-watchdog.json")
        c["thresholds"]["kill_grace_seconds"] = 0.01
        with tempfile.TemporaryDirectory() as directory:
            effects = w.Effects(Path(directory), c)
            effects.config_path = ROOT / "ops/config/job-watchdog.json"
            job = {"id": "old", "pid": 42, "pgid": 42, "process_identity": "old", "command": ["true"],
                   "card": "test", "cwd": directory}
            import signal
            with patch.object(w, "process_identity", return_value="old"), patch.object(w.os, "getpgid", return_value=42), patch.object(w.os, "killpg") as kill, patch.object(effects, "launch", return_value={}), patch.object(w, "process_group_alive", side_effect=lambda pgid: not any(call.args[1] == signal.SIGKILL for call in kill.call_args_list)):
                effects.restart({"job": job})
            self.assertEqual([call.args[1] for call in kill.call_args_list], [signal.SIGTERM, signal.SIGKILL])

    def test_action_failure_is_reported_on_board_and_to_record_same_scan(self):
        import job_watchdog as w
        c = w.load_config(ROOT / "ops/config/job-watchdog.json")
        class Effects:
            reported = []
            def report(self, f):
                self.reported.append(f["kind"])
            def act(self, action, f):
                raise RuntimeError("head changed")
        with tempfile.TemporaryDirectory() as directory:
            effects = Effects()
            w.reconcile(Path(directory), c, [w.finding("pr_ready", "repo#1@abc", "ready", c)], effects, 100)
            self.assertEqual(effects.reported, ["pr_ready", "action_error"])


if __name__ == "__main__":
    unittest.main()
