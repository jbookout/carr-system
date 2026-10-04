import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
FIXTURES = ROOT / "tools/fixtures/job-watchdog"


def setUpModule():
    from unittest.mock import patch
    global board_publication
    board_publication = patch.dict(os.environ, {"PROGRESS_BOARD_LOCAL_ONLY": "1"})
    board_publication.start()


def tearDownModule():
    board_publication.stop()


class ReplayTests(unittest.TestCase):
    def test_ci_replacement_attempts_restore_ready_without_false_red(self):
        import job_watchdog as w
        config = w.load_config(ROOT / "ops/config/job-watchdog.json")
        head = "a" * 40
        def check(name, conclusion, minute):
            return {"__typename": "CheckRun", "name": name, "workflowName": "CI",
                    "provider": "github-actions", "status": "COMPLETED",
                    "conclusion": conclusion, "startedAt": f"2026-01-01T00:{minute}:00Z"}
        checks = [check("gates", "CANCELLED", "01"), check("strict", "FAILURE", "01"),
                  check("gates", "SUCCESS", "02"), check("strict", "SUCCESS", "02")]
        pr = {"repo": "jbookout/carr-system", "number": 1, "headRefOid": head,
              "updatedAt": "2026-01-01T00:00:00Z", "mergeable": "MERGEABLE",
              "comments": [{"body": "REVIEW: APPROVED\nReviewed-SHA: " + head,
                            "createdAt": "2026-01-01T00:03:00Z"}]}
        for order in (checks, list(reversed(checks))):
            with self.subTest(order=order):
                self.assertTrue(w.green(order))
                found = w.detect({"prs": [{**pr, "statusCheckRollup": order}]}, config, 2000000000)
                self.assertEqual([f["kind"] for f in found], ["pr_ready"])

    def test_ci_current_pending_or_failed_attempt_does_not_inherit_success(self):
        import job_watchdog as w
        config = w.load_config(ROOT / "ops/config/job-watchdog.json")
        old = {"__typename": "CheckRun", "provider": "github-actions", "workflowName": "CI",
               "name": "strict", "startedAt": "2026-01-01T00:01:00Z",
               "completedAt": "2026-01-01T00:05:00Z", "status": "COMPLETED", "conclusion": "SUCCESS"}
        pr = {"repo": "jbookout/carr-system", "number": 1, "headRefOid": "a" * 40,
              "updatedAt": "2026-01-01T00:00:00Z"}
        for status, conclusion in (("IN_PROGRESS", None), ("COMPLETED", "FAILURE")):
            with self.subTest(status=status):
                checks = [old, {**old, "startedAt": "2026-01-01T00:02:00Z",
                                "completedAt": None, "status": status, "conclusion": conclusion}]
                self.assertFalse(w.green(checks))
                kinds = {f["kind"] for f in w.detect({"prs": [{**pr, "statusCheckRollup": checks}]}, config, 2000000000)}
                self.assertEqual("pr_ci_red" in kinds, conclusion == "FAILURE")

    def test_ci_identity_keeps_providers_workflows_and_context_types_separate(self):
        import job_watchdog as w
        old = {"__typename": "CheckRun", "provider": "app-one", "workflowName": "CI",
               "name": "strict", "startedAt": "2026-01-01T00:01:00Z",
               "status": "COMPLETED", "conclusion": "FAILURE"}
        for identity in ({"provider": "app-two"}, {"workflowName": "DB"},
                         {"__typename": "StatusContext", "context": "strict", "state": "SUCCESS"}):
            with self.subTest(identity=identity):
                checks = [old, {**old, **identity, "startedAt": "2026-01-01T00:02:00Z", "conclusion": "SUCCESS"}]
                self.assertFalse(w.green(checks))

    def test_ci_gh_export_resolves_actions_reruns_and_status_contexts(self):
        import job_watchdog as w
        actions = {"__typename": "CheckRun", "workflowName": "CI", "name": "strict",
                   "status": "COMPLETED", "conclusion": "FAILURE", "startedAt": "2026-01-01T00:01:00Z",
                   "detailsUrl": "https://github.com/example/repo/actions/runs/100/job/101"}
        status = {"__typename": "StatusContext", "context": "lint", "state": "ERROR",
                  "startedAt": "2026-01-01T00:01:00Z", "targetUrl": "https://checks.example/lint/100"}
        checks = [actions, {**actions, "conclusion": "SUCCESS", "startedAt": "2026-01-01T00:02:00Z",
                            "detailsUrl": "https://github.com/example/repo/actions/runs/200/job/201"},
                  status, {**status, "state": "SUCCESS", "startedAt": "2026-01-01T00:02:00Z",
                           "targetUrl": "https://checks.example/lint/200"}]
        self.assertTrue(w.green(checks))

    def test_ci_ambiguous_attempts_stay_fail_closed_and_ids_break_time_ties(self):
        import job_watchdog as w
        old = {"__typename": "CheckRun", "provider": "app-one", "workflowName": "CI",
               "name": "strict", "status": "COMPLETED", "conclusion": "FAILURE"}
        self.assertFalse(w.green([old, {**old, "conclusion": "SUCCESS"}]))
        self.assertFalse(w.green([]))
        old = {**old, "startedAt": "2026-01-01T00:01:00Z", "databaseId": 1}
        self.assertTrue(w.green([{**old, "databaseId": 2, "conclusion": "SUCCESS"}, old]))

    def test_collected_ci_keeps_provider_workflow_and_head_bindings(self):
        import job_watchdog as w
        from unittest.mock import patch
        head = "a" * 40
        raw = {"__typename": "CheckRun", "name": "strict", "databaseId": 2,
               "status": "COMPLETED", "conclusion": "SUCCESS", "startedAt": "2026-01-01T00:02:00Z",
               "checkSuite": {"app": {"id": "app-one"},
                              "workflowRun": {"workflow": {"id": "workflow-one"}}}}
        response = {"data": {"repository": {"pullRequest": {"mergeQueueEntry": None,
                    "commits": {"nodes": [{"commit": {"oid": head, "statusCheckRollup": {
                        "contexts": {"nodes": [raw], "pageInfo": {"hasNextPage": False}}}}}]}}}}}
        with patch.object(w, "command", side_effect=[json.dumps({"headRefOid": head}), json.dumps(response)]) as command:
            pr = w.collect_pr("example/repo", 1, w.load_config(ROOT / "ops/config/job-watchdog.json"))
            self.assertEqual(pr["statusCheckRollup"], [raw])
            self.assertIn("checkSuite", command.call_args.args[0][4])
        response["data"]["repository"]["pullRequest"]["commits"]["nodes"][0]["commit"]["oid"] = "b" * 40
        with patch.object(w, "command", side_effect=[json.dumps({"headRefOid": head}), json.dumps(response)]):
            with self.assertRaisesRegex(RuntimeError, "head changed"):
                w.collect_pr("example/repo", 1, w.load_config(ROOT / "ops/config/job-watchdog.json"))

    def test_fixtures_have_only_synthetic_name_vocabulary(self):
        # No record-layer access or client-name literals. Unknown name-like
        # words form the denylist relative to this closed synthetic vocabulary.
        synthetic_set = {
            "REVIEW: BLOCKED", "REVIEW: APPROVED", "Reviewed-SHA",
            "Reading additional input from stdin...",
            "parse error: synthetic queue", "synthetic fixture",
            "SUCCESS", "COMPLETED", "FAILURE", "MERGEABLE", "CONFLICTING",
            "UNKNOWN", "DIRTY", "CLEAN", "APPROVED", "CHANGES_REQUESTED",
        }
        allowed = set(re.findall(r"[A-Z][a-z]+", " ".join(synthetic_set)))

        def denylist(text):
            return set(re.findall(r"\b[A-Z][a-z]+\b", text)) - allowed

        # Prove that the scanner catches client-like strings without embedding
        # a real client identity or deriving a list from business records.
        self.assertTrue(denylist("Invented Dental C-000"))
        for path in sorted(FIXTURES.rglob("*")):
            if path.is_file():
                with self.subTest(fixture=path.name):
                    self.assertFalse(bool(denylist(path.read_text())),
                                     "fixture has name-like words outside the synthetic set")
                    if path.name.startswith("pr-"):
                        row = json.loads(path.read_text())
                        self.assertEqual(set(row), {"number", "headRefOid", "updatedAt",
                                                   "comments", "commits", "isDraft",
                                                   "mergeable", "statusCheckRollup"})
                        for comment in row["comments"]:
                            self.assertEqual(set(comment), {"body", "createdAt"})
                            self.assertRegex(comment["body"],
                                             r"\AREVIEW: (?:BLOCKED|APPROVED)\nReviewed-SHA: [0-9a-f]{40}\Z")

    def test_synthetic_stdin_hangs(self):
        import job_watchdog as w
        config = w.load_config(ROOT / "ops/config/job-watchdog.json")
        for name in ("stdin-a.log", "stdin-b.log"):
            with self.subTest(name=name):
                facts = {"jobs": [{"id": name, "card": name, "alive": True,
                         "start": 1000, "limit": 3600, "log_mtime": 1990,
                         "log_tail": (FIXTURES / name).read_text()}]}
                self.assertIn("job_hang", {f["kind"] for f in w.detect(facts, config, 2000)})

    def test_review_and_queue_replay(self):
        import job_watchdog as w
        config = w.load_config(ROOT / "ops/config/job-watchdog.json")
        prs = [dict(json.loads((FIXTURES / f"pr-{n}.json").read_text()),
                    repo="jbookout/doctorcre-app") for n in range(1, 7)]
        found = w.detect({"prs": prs, "queue": "", "logs": [
            {"path": "queue.log", "type": "queue", "mtime": 2000000000,
             "tail": (FIXTURES / "queue.log").read_text()}]}, config, 2000000000)
        blocked = {f["pr"] for f in found if f["kind"] == "pr_blocked_review"}
        self.assertEqual(blocked, {1, 2, 3, 5, 6})
        self.assertIn("queue_error", {f["kind"] for f in found})
        # Synthetic PR4 is approved and green, but GitHub reports UNKNOWN mergeability.
        # It must not be queued until a fresh mergeability read can establish it.
        self.assertFalse(any(f.get("pr") == 4 for f in found))

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
    def test_model_room_streams_progress_before_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "codex"
            executable.write_text("#!" + sys.executable + "\nimport sys,time,json\nfrom pathlib import Path\n"
                                  "assert sys.stdin.read() == ''\n"
                                  "print(json.dumps({'type':'thread.started','thread_id':'fixture-thread'}), flush=True)\n"
                                  "time.sleep(1)\n"
                                  "Path(sys.argv[sys.argv.index('-o')+1]).write_text('fixture result')\n")
            executable.chmod(0o755)
            dispatch = ROOT / "tools/room-bridge/dispatch.py"
            registry = root / "desk.json"
            env = dict(os.environ, PATH=str(root) + os.pathsep + os.environ["PATH"])
            registration = subprocess.run([sys.executable, str(dispatch), "--registry", str(registry),
                                           "register", "fixture", "--kind", "codex-session", "--model", "gpt-6.1-sol",
                                           "--effort", "high", "--sandbox", "workspace-write", "--cwd", directory],
                                          env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True)
            self.assertEqual(registration.returncode, 0, registration.stderr)
            log = root / "log"
            with log.open("w") as output:
                proc = subprocess.Popen([sys.executable, str(dispatch), "--registry", str(registry),
                                         "--results", str(root / "results.jsonl"), "send", "fixture", "fixture",
                                         "--fresh", "--stream-output"], env=env, stdin=subprocess.DEVNULL,
                                        stdout=output, stderr=subprocess.STDOUT)
                try:
                    deadline = time.monotonic() + 3
                    while "thread.started" not in log.read_text() and proc.poll() is None and time.monotonic() < deadline:
                        time.sleep(0.01)
                    self.assertIn("thread.started", log.read_text())
                    self.assertIsNone(proc.poll(), "progress must be visible before completion")
                    self.assertEqual(proc.wait(timeout=5), 0)
                finally:
                    if proc.poll() is None:
                        proc.kill()
                    proc.wait()
            self.assertEqual(json.loads((root / "results.jsonl").read_text())["status"], "completed")

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
            self.assertIn("REVIEW: BLOCKED", dispatch[dispatch.index("send") + 2])
            self.assertIn("--stream-output", dispatch)

    def test_detected_credential_waits_never_restart(self):
        import job_watchdog as w
        c = w.load_config(ROOT / "ops/config/job-watchdog.json")
        c["actions"]["file_defects"] = False
        for tail in ("Waiting for authentication...\nauthentication required",
                     "Waiting for authentication...", "token expired"):
            with self.subTest(tail=tail), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                effects = w.Effects(root, c)
                # Boundary fake: any process recovery is a failing effect.
                def forbidden(action, row):
                    self.fail("credential wait attempted recovery: " + action)
                effects.act = forbidden
                job = {"id": "credentials", "card": "credentials", "alive": True,
                       "start": 1000, "limit": 999, "log_mtime": 1000,
                       "log_tail": tail}
                found = w.detect({"jobs": [job]}, c, 2000)
                self.assertTrue(found)
                self.assertTrue(all(f["needs_joe"] == "credentials" for f in found))
                self.assertTrue(all(tail in f["reason"] for f in found))
                w.reconcile(root, c, found, effects, 2000)
                self.assertEqual(w.read_latest(root / c["paths"]["actions"]), {})
                state = json.loads((root / "out/boards/carr-v5.json").read_text())
                self.assertEqual(state["tasks"]["credentials"]["lane"], "needs-joe")
                self.assertEqual(state["tasks"]["credentials"]["status"], "blocked")

    def test_credential_escalation_reaches_production_record_gate(self):
        import job_watchdog as w
        from unittest.mock import patch
        c = w.load_config(ROOT / "ops/config/job-watchdog.json")
        self.assertTrue(c["actions"]["file_defects"])
        probe = """
          import {executeRegisteredTool} from './mcp-server/src/tools.js';
          import fs from 'node:fs';
          const payload = JSON.parse(fs.readFileSync(0, 'utf8'));
          let queried = false;
          const client = {query: async () => {
            queried = true; throw new Error('test database boundary');
          }};
          try {
            await executeRegisteredTool(client,
              {slug:'joe', human:true, kind:'human', via:'break-glass/local-verb'},
              'add-loop', payload);
          } catch (error) {
            console.log(JSON.stringify({queried, refusal:error.payload || error.message}));
          }
        """
        payloads = []
        def record_boundary(argv, config, cwd=None):
            self.assertEqual(argv[1:3], ["call", "add-loop"])
            payload = json.loads(argv[3])
            result = subprocess.run(["node", "--input-type=module", "-e", probe],
                                    cwd=ROOT, input=json.dumps(payload),
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            observed = json.loads(result.stdout)
            self.assertTrue(observed["queried"], observed)
            payloads.append(payload)
            return json.dumps({"ok": True, "loop_id": "synthetic-loop"})
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            effects = w.Effects(root, c)
            effects.act = lambda *args: self.fail("credential escalation attempted recovery")
            job = {"id": "credential-record", "card": "credential-record", "alive": True,
                   "start": 1000, "limit": 3600, "log_mtime": 1990,
                   "log_tail": "Waiting for authentication...\nauthentication required"}
            found = w.detect({"jobs": [job]}, c, 2000)
            with patch.object(w, "command", side_effect=record_boundary):
                result = w.reconcile(root, c, found, effects, 2000)
                self.assertFalse(any(f["kind"] == "record_error" for f in result), result)
                w.reconcile(root, c, found, effects, 2001)
            self.assertEqual(len(payloads), 1, "successful escalation must not be retried")
            self.assertEqual(payloads[0]["blocker"], "capability")
            self.assertEqual(payloads[0]["marker"], "none")
            self.assertTrue(w.read_latest(root / c["paths"]["findings"])[found[0]["key"]]["reported"])

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
            self.assertIn("authentication", state["tasks"]["credential"]["blocked_reason"])

    def test_recovery_reconciles_watchdog_owned_board_state(self):
        import job_watchdog as w
        c = w.load_config(ROOT / "ops/config/job-watchdog.json")
        c["actions"]["file_defects"] = False
        c["actions"]["job_hang"] = "report"
        for mode in ("created", "collection", "existing", "other-finding", "external-update", "incomplete"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                effects = w.Effects(root, c)
                card = "shared-card"
                if mode == "existing":
                    w.board_task(root, c, card, "executor", "running", "Original work in progress")
                f = w.finding("job_hang", "synthetic-job", "Waiting for authentication...", c, card=card)
                if mode == "collection":
                    f = w.detect({"errors": [{"source": "synthetic-source", "reason": "evidence unavailable"}]}, c, 100)[0]
                    card = effects.card(f)
                other = w.finding("job_over_limit", "synthetic-job", "another active failure", c, card=card)
                found = [f, other] if mode == "other-finding" else [f]
                w.reconcile(root, c, found, effects, 100)
                board = root / "out/boards/carr-v5.json"
                self.assertEqual(json.loads(board.read_text())["tasks"][card]["status"], "blocked")
                if mode == "external-update":
                    w.board_task(root, c, card, "executor", "review", "New executor evidence")
                remaining = [f] if mode == "other-finding" else []
                w.reconcile(root, c, remaining, effects, 200, complete=mode != "incomplete")
                w.reconcile(root, c, remaining, effects, 300, complete=mode != "incomplete")
                task = json.loads(board.read_text())["tasks"][card]
                if mode == "other-finding":
                    self.assertEqual(task["status"], "blocked")
                    self.assertEqual(task["lane"], "needs-joe")
                    self.assertIn(f["reason"], task["note"])
                    self.assertNotIn(other["reason"], task["note"])
                elif mode == "incomplete":
                    self.assertEqual(task["status"], "blocked")
                else:
                    self.assertEqual(task["status"], {"created":"done", "collection":"done", "existing":"running", "external-update":"review"}[mode])
                    self.assertEqual(task.get("health", "healthy"), "healthy")
                    self.assertIsNone(task["lane"])
                    self.assertNotIn(f["reason"], task["note"])
                    self.assertNotIn("Next action:", task["note"])
                    if mode == "existing":
                        self.assertEqual(task["note"], "Original work in progress")
                    if mode == "external-update":
                        self.assertEqual(task["note"], "New executor evidence")

    def test_recovery_keeps_executor_update_after_ownership_read(self):
        from unittest.mock import patch
        import job_watchdog as w
        c = w.load_config(ROOT / "ops/config/job-watchdog.json")
        for sibling in (False, True):
            with self.subTest(sibling=sibling), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                env = dict(os.environ, PROGRESS_BOARD_ROOT=str(root / "out"),
                           PROGRESS_BOARD_LOCAL_ONLY="1")
                def cli(*args):
                    subprocess.run([sys.executable, str(ROOT / "tools/progress_board.py"), *args],
                                   env=env, capture_output=True, text=True, check=True)
                cli("init", c["board"], "--title", "Synthetic board")
                cli("task", c["board"], "shared-card", "--title", "Synthetic work",
                    "--executor", "old-executor", "--status", "running", "--note", "Old evidence")
                board = root / "out/boards" / (c["board"] + ".json")
                before = json.loads(board.read_text())["tasks"]["shared-card"]
                cli("task", c["board"], "shared-card", "--status", "blocked", "--health", "blocked",
                    "--note", "Watchdog overlay", "--reason", "Synthetic failure", "--next-action", "Recover")
                f = w.finding("job_hang", "synthetic-job", "Synthetic failure", c, card="shared-card")
                f["board_recovery"] = {"card": "shared-card", "before": before,
                                       "note": "Watchdog overlay", "lane": None}
                w.append(root / c["paths"]["findings"], f)
                original = w.board_task
                def interleaving_write(*args, **kwargs):
                    # The ownership read has happened. Another participating writer
                    # commits under the production board lock before recovery writes.
                    cli("task", c["board"], "shared-card", "--executor", "new-executor",
                        "--status", "review", "--health", "healthy", "--note", "New executor evidence")
                    return original(*args, **kwargs)
                with patch.object(w, "board_task", side_effect=interleaving_write), \
                     patch.dict(os.environ, {"PROGRESS_BOARD_LOCAL_ONLY": "1"}):
                    remaining = [w.finding("job_failed", "sibling", "Another failure", c,
                                           card="shared-card")] if sibling else []
                    effects = w.Effects(root, c)
                    effects.report = lambda f: {}
                    w.reconcile(root, c, remaining, effects, 200)
                task = json.loads(board.read_text())["tasks"]["shared-card"]
                self.assertEqual(task["status"], "review")
                self.assertEqual(task["executor"], "new-executor")
                self.assertEqual(task["note"], "New executor evidence")
                self.assertEqual(w.read_latest(root / c["paths"]["findings"])[f["key"]]["cleared_at"], w.stamp(200))

    def test_reopened_finding_restores_the_new_board_owner_state(self):
        import job_watchdog as w
        c = w.load_config(ROOT / "ops/config/job-watchdog.json")
        c["actions"]["file_defects"] = False
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            effects = w.Effects(root, c)
            f = w.finding("collection_error", "synthetic-source", "evidence unavailable", c, card="reopened")
            w.reconcile(root, c, [f], effects, 100)
            w.reconcile(root, c, [], effects, 200)
            w.board_task(root, c, "reopened", "executor", "review", "New verification in progress")
            w.reconcile(root, c, [f], effects, 300)
            w.reconcile(root, c, [], effects, 400)
            task = json.loads((root / "out/boards/carr-v5.json").read_text())["tasks"]["reopened"]
            self.assertEqual(task["status"], "review")
            self.assertEqual(task["note"], "New verification in progress")

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
