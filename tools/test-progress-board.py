#!/usr/bin/env python3
"""Behavioral tests for the progress-board command line surface."""

import copy
import json
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "tools" / "progress_board.py"
SPEC = importlib.util.spec_from_file_location("progress_board", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
BOARD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BOARD)


class ProgressBoardCLI(unittest.TestCase):
    def test_executor_metadata_parser_handles_legacy_spellings(self):
        cases = {
            "gpt-6-sol high (Codex)": ("Codex", "gpt-6-sol", "high"),
            "codex gpt-6-sol high": ("Codex", "gpt-6-sol", "high"),
            "Codex gpt-6-sol high x2": ("Codex", "gpt-6-sol", "high"),
            "orchestrator": ("Unknown", "unknown", "unknown"),
            "Codex orchestrator gpt-5.5 xhigh": ("Codex", "gpt-5.5", "xhigh"),
            "Claude Opus 5.5 (orchestrator)": ("Anthropic", "Claude Opus 5.5", "unknown"),
            "Claude Sonnet 4.6 orchestrator high": ("Anthropic", "Claude Sonnet 4.6", "high"),
            "codex": ("Codex", "unknown", "unknown"),
            "claude high": ("Anthropic", "unknown", "high"),
            "grok": ("xAI", "unknown", "unknown"),
            "flash-next": ("Google", "unknown", "unknown"),
            "": ("Unknown", "unknown", "unknown"),
        }
        for executor, expected in cases.items():
            with self.subTest(executor=executor):
                self.assertEqual(BOARD.executor_metadata(executor), expected)

    def test_cards_render_summary_model_effort_and_orchestrator(self):
        self.run_board("init", "demo", "--title", "Demo")
        self.run_board("task", "demo", "codex", "--title", "Build card",
                       "--summary", "Show the delivery details on each card.",
                       "--status", "running", "--executor", "gpt-6-sol high (Codex)")
        self.run_board("task", "demo", "orchestrator", "--title", "Coordinate",
                       "--summary", "Coordinate the delivery review.",
                       "--status", "running", "--executor", "orchestrator")
        html = (self.root / "boards" / "demo.html").read_text()
        self.assertIn("Show the delivery details on each card.", html)
        self.assertIn("Coordinate the delivery review.", html)
        self.assertIn("Codex", html)
        self.assertIn("gpt-6-sol · high", html)
        self.assertIn("Unknown", html)
        self.assertIn("unknown · unknown", html)
        self.assertIn('id="task-detail"', html)
        state = self.read_state("demo")
        self.assertEqual(state["tasks"]["codex"]["provider"], "Codex")
        self.assertEqual(state["tasks"]["codex"]["model"], "gpt-6-sol")
        self.assertEqual(state["tasks"]["codex"]["effort"], "high")
        self.assertEqual(state["tasks"]["codex"]["summary"], "Show the delivery details on each card.")
        self.assertEqual((state["tasks"]["orchestrator"]["provider"],
                          state["tasks"]["orchestrator"]["model"]), ("Unknown", "unknown"))

    def test_explicit_metadata_overrides_executor(self):
        self.run_board("init", "demo", "--title", "Demo")
        self.run_board("task", "demo", "a", "--title", "A", "--status", "queued",
                       "--executor", "orchestrator", "--provider", "Codex",
                       "--model", "gpt-6-sol", "--effort", "xhigh",
                       "--summary", "Check the route.")
        task = self.read_state("demo")["tasks"]["a"]
        self.assertEqual((task["provider"], task["model"], task["effort"]),
                         ("Codex", "gpt-6-sol", "xhigh"))

    def test_backfill_uses_pr_title_and_retains_existing_task_history(self):
        state = {"tasks": {
            "pr": {"title": "PR 42", "executor": "gpt-6-sol high (Codex)",
                   "pr": 42, "repo": "jbookout/carr-system", "status": "done",
                   "stage_history": [{"stage": "review", "at": "earlier"}]},
            "other": {"title": "Check CRM capture", "executor": "orchestrator",
                      "status": "running", "note": "Verify that captured mail reaches the CRM."},
        }}
        calls = []
        def lookup(number, repo):
            calls.append((number, repo))
            return {"title": "Show model and effort on board cards", "body": "The board now names each model.",
                    "url": "https://github.com/jbookout/carr-system/pull/42", "headRefOid": "a" * 40}
        self.assertEqual(BOARD.backfill_state(state, lookup), 2)
        self.assertEqual(calls, [(42, "jbookout/carr-system")])
        self.assertEqual(state["tasks"]["pr"]["summary"], "Show model and effort on board cards.")
        self.assertEqual(state["tasks"]["pr"]["stage_history"], [{"stage": "review", "at": "earlier"}])
        self.assertEqual(state["tasks"]["other"]["summary"], "Verify that captured mail reaches the CRM.")
        self.assertEqual(state["tasks"]["other"]["provider"], "Unknown")
        self.assertEqual(state["tasks"]["other"]["model"], "unknown")

    def test_review_verdict_requires_trusted_comment_on_current_head(self):
        head = "a" * 40
        payload = {"headRefOid": head, "author": {"login": "builder"},
                   "comments": [{"author": {"login": "reviewer"}, "authorAssociation": "COLLABORATOR",
                                 "createdAt": "2026-09-29T10:00:00Z",
                                 "body": "APPROVE\nReviewed-SHA: " + head}]}
        self.assertEqual(BOARD.review_verdict(payload), "APPROVE")
        payload["headRefOid"] = "b" * 40
        self.assertEqual(BOARD.review_verdict(payload), "Not recorded")

    def test_partial_http_failures_keep_merged_and_finish_publish_poll_in_both_lanes(self):
        from argparse import Namespace
        from http.client import IncompleteRead, BadStatusLine
        from unittest.mock import MagicMock
        self.run_board("init", BOARD.LAUNCHD_BOARD, "--title", "Scheduled")
        for repo, target in BOARD.AUTOMATIC_DELIVERY_TARGETS.items():
            for error in (IncompleteRead(b"partial", 10), BadStatusLine("invalid")):
                with self.subTest(repo=repo, error=type(error).__name__), patch.dict(os.environ, self.env):
                    state = BOARD.read_state(BOARD.LAUNCHD_BOARD)
                    state["tasks"] = {"fix": {"title": "Feature", "repo": repo, "pr": 1,
                        "status": "done", "stage": "merged", "delivery_target": target,
                        "executor": "codex", "updated_at": BOARD.stamp()}}
                    BOARD.write_json(state)
                    response = MagicMock()
                    response.__enter__.return_value.read.side_effect = error
                    kwargs = {"return_value": response} if isinstance(error, IncompleteRead) else {"side_effect": error}
                    with patch.object(BOARD, "urlopen", **kwargs), \
                         patch.object(BOARD, "pr_info", return_value={"state": "MERGED", "mergeCommit": {"oid": "a" * 40}}), \
                         patch.object(BOARD, "publish_board") as publish, \
                         patch.object(BOARD, "poll_board_answers") as poll:
                        BOARD.command_render(Namespace(project=BOARD.LAUNCHD_BOARD, publish=True))
                        publish.assert_called_once_with(BOARD.LAUNCHD_BOARD)
                        poll.assert_called_once_with(BOARD.LAUNCHD_BOARD)
                    task = BOARD.read_state(BOARD.LAUNCHD_BOARD)["tasks"]["fix"]
                    self.assertEqual(task["stage"], "merged")
                    self.assertNotIn("completed_at", task)
                    self.assertNotIn("evidence", task)
                    self.assertTrue((self.root / "boards" / f"{BOARD.LAUNCHD_BOARD}.html").exists())

    def test_reverted_change_stays_merged_before_first_release_in_both_lanes(self):
        sys.path.insert(0, str(REPO / "ops"))
        from git_env import fixture_env
        source = self.root / "source"
        source.mkdir()
        def git(*args):
            return subprocess.run(["git", "-C", str(source), *args], env=fixture_env(),
                                  capture_output=True, text=True, check=True).stdout.strip()
        git("init")
        git("config", "user.name", "Fixture")
        git("config", "user.email", "fixture@example.invalid")
        (source / "feature").write_text("off\n")
        git("add", "feature")
        git("commit", "-m", "Baseline")
        (source / "feature").write_text("on\n")
        git("add", "feature")
        git("commit", "-m", "Fix")
        fix = git("rev-parse", "HEAD")
        git("revert", "--no-edit", fix)
        deployed = git("rev-parse", "HEAD")
        self.assertEqual((source / "feature").read_text(), "off\n")
        self.run_board("init", "demo", "--title", "Demo")
        for repo, target in BOARD.AUTOMATIC_DELIVERY_TARGETS.items():
            with self.subTest(repo=repo), patch.dict(os.environ, self.env):
                state = BOARD.read_state("demo")
                state["tasks"] = {"fix": {"title": "Feature", "repo": repo, "pr": 1,
                    "status": "done", "stage": "merged", "delivery_target": target,
                    "executor": "codex", "updated_at": BOARD.stamp()}}
                BOARD.write_json(state)
                with patch.object(BOARD, "pr_info", return_value={"state": "MERGED", "mergeCommit": {"oid": fix}}), \
                     patch.object(BOARD, "deployed_release", return_value=(source, deployed, "https://example.invalid/release")):
                    BOARD.render("demo")
                task = BOARD.read_state("demo")["tasks"]["fix"]
                self.assertEqual(task["stage"], "merged")
                self.assertNotIn("completed_at", task)
                self.assertNotIn("evidence", task)

    def test_worker_release_does_not_complete_other_or_unspecified_targets(self):
        sys.path.insert(0, str(REPO / "ops"))
        from git_env import fixture_env
        source = self.root / "source"
        source.mkdir()
        def git(*args):
            return subprocess.run(["git", "-C", str(source), *args], env=fixture_env(),
                                  capture_output=True, text=True, check=True).stdout.strip()
        git("init")
        (source / "feature").write_text("on\n")
        git("add", "feature")
        git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
            "commit", "-m", "Feature")
        sha = git("rev-parse", "HEAD")
        self.run_board("init", "demo", "--title", "Demo")
        from unittest.mock import MagicMock
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps({
            "ok": True, "env": {"value": "production"}, "git_sha": {"value": sha},
            "schema": {"available": False}}).encode()
        for target in (None, "workstation", "database", "app", "manual"):
            with self.subTest(target=target), patch.dict(os.environ, self.env):
                state = BOARD.read_state("demo")
                state["tasks"] = {"install": {"title": "Install hook", "status": "done",
                    "stage": "merged", "pr": 1, "executor": "codex", "updated_at": BOARD.stamp(),
                    "delivery_target": target}}
                BOARD.write_json(state)
                with patch.object(BOARD, "RELEASE_TARGETS", {BOARD.DEFAULT_PR_REPO: (source, "https://example.invalid/release")}), \
                     patch.object(BOARD, "urlopen", return_value=response), \
                     patch.object(BOARD, "pr_info", return_value={"state": "MERGED", "mergeCommit": {"oid": sha}}):
                    BOARD.render("demo")
                task = BOARD.read_state("demo")["tasks"]["install"]
                self.assertEqual(task["stage"], "merged")
                self.assertNotIn("completed_at", task)
                self.assertNotIn("evidence", task)

    def test_installed_copy_uses_checkout_independent_of_executable(self):
        source = self.root / "carr-system"
        source.mkdir()
        sys.path.insert(0, str(REPO / "ops"))
        from git_env import fixture_env
        def git(*args):
            return subprocess.run(["git", "-C", str(source), *args], env=fixture_env(),
                                  capture_output=True, text=True, check=True).stdout.strip()
        git("init")
        (source / "feature").write_text("on\n")
        git("add", "feature")
        git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
            "commit", "-m", "Feature")
        sha = git("rev-parse", "HEAD")
        installed = self.root / "support" / "carr-progress-board" / "progress_board.py"
        installed.parent.mkdir(parents=True)
        shutil.copyfile(SCRIPT, installed)
        spec = importlib.util.spec_from_file_location("installed_board", installed)
        module = importlib.util.module_from_spec(spec)
        from unittest.mock import MagicMock
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps({
            "ok": True, "env": {"value": "production"}, "git_sha": {"value": sha}}).encode()
        with patch.dict(os.environ, {"HOME": str(self.root)}):
            spec.loader.exec_module(module)
            with patch.object(module, "urlopen", return_value=response):
                release = module.deployed_release(module.DEFAULT_PR_REPO)
        self.assertEqual(release[0], source)
        self.assertIsNotNone(module.deployment_evidence({"mergeCommit": {"oid": sha}}, release))

    def test_render_advances_only_deployed_merge_commits_in_each_repository(self):
        sys.path.insert(0, str(REPO / "ops"))
        from git_env import fixture_env
        git_env = fixture_env()
        source = self.root / "source"
        source.mkdir()
        def git(*args):
            return subprocess.run(["git", "-C", str(source), *args], env=git_env,
                                  capture_output=True, text=True, check=True).stdout.strip()
        git("init")
        (source / "feature").write_text("on\n")
        git("add", "feature")
        git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
            "commit", "-m", "Released")
        released = git("rev-parse", "HEAD")
        (source / "future").write_text("pending\n")
        git("add", "future")
        git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
            "commit", "-m", "Unreleased")
        newer = git("rev-parse", "HEAD")
        for repo, identity in (
            ("jbookout/carr-system", {"ok": True, "env": {"value": "production"},
                                      "git_sha": {"value": released}}),
            ("jbookout/doctorcre-app", {"service": "doctorcre-app", "environment": "production",
                                       "source_commit": released}),
        ):
            with self.subTest(repo=repo):
                project = repo.split("/")[1]
                self.run_board("init", project, "--title", "Demo")
                for task_id, number in (("deployed", 1), ("future", 2)):
                    self.run_board("task", project, task_id, "--title", task_id,
                                   "--status", "done", "--executor", "codex",
                                   "--pr", str(number), "--repo", repo, "--stage", "merged",
                                   "--delivery-target", "worker" if repo == BOARD.DEFAULT_PR_REPO else "app")
                def info(number, _repo):
                    return {"state": "MERGED", "headRefOid": "f" * 40,
                            "mergeCommit": {"oid": released if number == 1 else newer}}
                from unittest.mock import MagicMock
                response = MagicMock()
                response.__enter__.return_value.read.return_value = json.dumps(identity).encode()
                with patch.dict(os.environ, self.env), patch.object(BOARD, "pr_info", info), \
                     patch.object(BOARD, "RELEASE_TARGETS", {repo: (source, "https://example.invalid/release")}, create=True), \
                     patch.object(BOARD, "urlopen", return_value=response):
                    BOARD.render(project)
                tasks = self.read_state(project)["tasks"]
                self.assertEqual(tasks["deployed"]["stage"], "live")
                self.assertIn(released, tasks["deployed"]["evidence"])
                self.assertEqual(tasks["future"]["stage"], "merged")
                self.assertNotIn("evidence", tasks["future"])
                remote = {"snapshot": None}
                def call(verb, args):
                    if verb == "publish-board-snapshot":
                        self.assertEqual(args["base_version"], 0)
                        remote["snapshot"] = {"version": 1, "snapshot_json": args["snapshot"]}
                    return {"ok": True, "questions": [], **remote}
                with patch.dict(os.environ, self.env), patch.object(BOARD, "call_verb", call):
                    BOARD.publish_board(project)
                published = remote["snapshot"]["snapshot_json"]["tasks"]
                self.assertEqual(published["deployed"]["stage"], "live")
                self.assertEqual(published["future"]["stage"], "merged")

    def test_render_keeps_merged_when_production_identity_cannot_be_proven(self):
        self.run_board("init", "demo", "--title", "Demo")
        self.run_board("task", "demo", "pending", "--title", "Pending",
                       "--status", "done", "--executor", "codex", "--pr", "1", "--stage", "merged",
                       "--delivery-target", "worker")
        from unittest.mock import MagicMock
        cases = [
            {"ok": True, "env": {"value": "staging"}, "git_sha": {"value": "a" * 40}},
            {"ok": True, "env": {"value": "production"}, "git_sha": {"value": "abc"}},
            {"ok": True, "env": [], "git_sha": "malformed"},
            ["malformed"],
            OSError("unavailable"),
        ]
        for identity in cases:
            with self.subTest(identity=identity):
                response = MagicMock()
                response.__enter__.return_value.read.return_value = json.dumps(identity, default=str).encode()
                kwargs = {"side_effect": identity} if isinstance(identity, Exception) else {"return_value": response}
                with patch.dict(os.environ, self.env), \
                     patch.object(BOARD, "pr_info", return_value={"state": "MERGED", "mergeCommit": {"oid": "a" * 40}}), \
                     patch.object(BOARD, "urlopen", **kwargs):
                    BOARD.render("demo")
                task = self.read_state("demo")["tasks"]["pending"]
                self.assertEqual(task["stage"], "merged")
                self.assertNotIn("evidence", task)

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.env = os.environ.copy()
        self.env["PROGRESS_BOARD_ROOT"] = str(self.root)
        self.env["PROGRESS_BOARD_SKIP_GH"] = "1"

    def tearDown(self):
        self.tempdir.cleanup()

    def run_board(self, *args, input_text=None):
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            cwd=REPO,
            env=self.env,
            input=input_text,
            text=True,
            capture_output=True,
            check=True,
        )

    def read_state(self, project):
        return json.loads((self.root / "boards" / f"{project}.json").read_text())

    def test_init_task_ask_answer_and_deliver(self):
        self.run_board("init", "demo", "--title", "Demo project")
        self.run_board(
            "task",
            "demo",
            "build",
            "--title",
            "Build board",
            "--status",
            "running",
            "--executor",
            "codex gpt-5.6-luna high",
            "--pr",
            "42",
            "--note",
            "working",
        )
        self.run_board(
            "ask",
            "demo",
            "q1",
            "--question",
            "Ship this?",
            "--default",
            "continue on the default",
        )
        self.run_board("answer", "demo", "q1", input_text="yes\n")
        self.run_board("deliver", "demo", "--title", "Board ready", "--link", "/tmp/board.html")

        state = self.read_state("demo")
        self.assertEqual(state["title"], "Demo project")
        self.assertEqual(state["tasks"]["build"]["status"], "running")
        self.assertEqual(state["tasks"]["build"]["pr"], 42)
        self.assertEqual(state["questions"]["q1"]["answer"], "yes")
        self.assertEqual(state["deliverables"][0]["link"], "/tmp/board.html")
        self.assertTrue(state["updated_at"])

    def test_stuck_is_derived_for_blocked_and_old_running_tasks(self):
        self.run_board("init", "demo", "--title", "Demo")
        self.run_board(
            "task", "demo", "blocked", "--title", "Blocked", "--status", "blocked", "--executor", "codex"
        )
        self.run_board(
            "task", "demo", "running", "--title", "Old", "--status", "running", "--executor", "codex"
        )
        state_path = self.root / "boards" / "demo.json"
        state = json.loads(state_path.read_text())
        old = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
        state["tasks"]["running"]["updated_at"] = old
        state_path.write_text(json.dumps(state))
        self.run_board("render", "demo")
        html = (self.root / "boards" / "demo.html").read_text()
        self.assertIn("Blocked", html)
        self.assertIn("Old", html)
        self.assertIn("Stuck", html)

    def test_ledger_flags_claude_plan_executor(self):
        self.run_board("init", "demo", "--title", "Demo")
        self.run_board(
            "task",
            "demo",
            "violation",
            "--title",
            "Wrong seat",
            "--status",
            "queued",
            "--executor",
            "claude-plan Opus subagent",
        )
        html = (self.root / "boards" / "demo.html").read_text()
        self.assertIn("policy violation", html.lower())
        self.assertIn("ledger-violation", html)
        self.assertIn("Claude cloud credits", html)
        self.assertIn("POLICY VIOLATION", html)

    def test_html_has_all_panels_and_only_the_hosted_board_url(self):
        self.run_board("init", "demo", "--title", "Demo")
        self.run_board("ask", "demo", "q1", "--question", "Question?", "--default", "Proceed")
        self.run_board("deliver", "demo", "--title", "Done", "--link", "board.html")
        html = (self.root / "boards" / "demo.html").read_text()
        for panel in (
            "Questions waiting on Joe",
            "Stuck",
            "Tasks by status",
            "Latest deliverables",
            "Executor ledger",
        ):
            self.assertIn(panel, html)
        self.assertIn('http-equiv="refresh" content="10"', html)
        self.assertIn('href="https://app.doctorcre.com/progress-board?board=demo"', html)
        self.assertEqual(html.count("https://app.doctorcre.com"), 1)
        self.assertNotIn("<script src=", html)
        self.assertIn("sessionStorage", html)
        self.assertIn("scrollY", html)
        self.assertIn("data-fingerprint=", html)

    def test_pipeline_svg_has_one_node_per_task_and_places_each_node(self):
        self.run_board("init", "demo", "--title", "Demo")
        self.run_board(
            "task",
            "demo",
            "queued-task",
            "--title",
            "Queued task",
            "--status",
            "queued",
            "--executor",
            "codex gpt-5.6-luna high",
        )
        self.run_board(
            "task",
            "demo",
            "ci-task",
            "--title",
            "CI task",
            "--status",
            "running",
            "--executor",
            "codex gpt-5.6-luna high",
            "--pr",
            "42",
            "--stage",
            "ci",
        )
        self.run_board(
            "task",
            "demo",
            "blocked-task",
            "--title",
            "Blocked task",
            "--status",
            "blocked",
            "--executor",
            "codex gpt-5.6-luna high",
            "--pr",
            "43",
            "--stage",
            "merged",
        )
        self.run_board(
            "task",
            "demo",
            "question-task",
            "--title",
            "Question task",
            "--status",
            "review",
            "--executor",
            "codex gpt-5.6-luna high",
            "--pr",
            "44",
            "--stage",
            "measured",
            "--health",
            "question",
            "--evidence",
            "Verified in operation",
        )

        state = self.read_state("demo")
        self.assertEqual(state["tasks"]["ci-task"]["stage"], "ci")
        self.assertEqual(state["tasks"]["blocked-task"]["stage"], "merged")
        html = (self.root / "boards" / "demo.html").read_text()

        self.assertEqual(html.count('class="pipeline-node '), 8)  # desk and phone SVGs
        for task_id, stage in (
            ("queued-task", "queued"), ("ci-task", "ci"),
            ("blocked-task", "merged"), ("question-task", "live"),
        ):
            self.assertEqual(html.count(f'data-task-id="{task_id}" data-stage="{stage}"'), 2)
        for label in ("Queued", "Building", "Review", "CI", "Merged", "Live"):
            self.assertIn(label, html)
        self.assertIn("node-blocked", html)
        self.assertIn("node-question", html)
        self.assertIn("executor-glyph", html)
        self.assertIn("prefers-reduced-motion", html)
        self.assertIn('class="pipeline-diagram pipeline-desktop"', html)
        self.assertIn('class="pipeline-diagram pipeline-phone"', html)
        self.assertIn("PR 42", html)
        self.assertIn("pipeline-connector", html)

    def test_headline_counts_and_section_order(self):
        self.run_board("init", "demo", "--title", "Demo")
        self.run_board("task", "demo", "a", "--title", "Running", "--status", "running", "--executor", "Codex")
        self.run_board("task", "demo", "b", "--title", "Blocked", "--status", "blocked", "--executor", "Grok")
        self.run_board("ask", "demo", "q", "--question", "Choose route?", "--default", "Continue")
        html = (self.root / "boards" / "demo.html").read_text()
        self.assertRegex(html, r'1 running.*1 need Joe.*1 blocked')
        labels = ["Questions waiting on Joe", "Stuck", "Delivery pipeline", "Tasks by status", "Latest deliverables", "Executor ledger"]
        positions = [html.index(f"<h2>{label}</h2>") for label in labels]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("CT", html)
        self.assertIn("min ago", html)

    def test_pulse_classes_and_reduced_motion_fallback(self):
        self.run_board("init", "demo", "--title", "Demo")
        for task_id, status, extra in (
            ("a", "running", []), ("b", "review", []),
            ("c", "blocked", []), ("d", "failed", []),
            ("e", "done", []), ("f", "queued", []),
        ):
            self.run_board("task", "demo", task_id, "--title", task_id, "--status", status, "--executor", "Codex", *extra)
        html = (self.root / "boards" / "demo.html").read_text()
        for state in ("healthy", "attention", "critical", "still"):
            self.assertIn(f"pulse-{state}", html)
        self.assertRegex(html, r'data-task-ref="f"[^>]*>.*?<span class="state-mark"[^>]*>◇</span>')
        self.assertRegex(html, r'@media\s*\(prefers-reduced-motion:\s*reduce\)')
        self.assertRegex(html, r'prefers-reduced-motion:reduce[^}]*animation:none')

    def test_two_clocks_and_stall_banner(self):
        self.run_board("init", "demo", "--title", "Demo")
        self.run_board("task", "demo", "a", "--title", "A", "--status", "running", "--executor", "Codex")
        html = (self.root / "boards" / "demo.html").read_text()
        self.assertIn("Live · refreshed", html)
        self.assertIn("Last task change", html)
        self.assertIn("Board refresh stalled", html)
        self.assertIn("Date.now()", html)
        self.assertIn("360000", html)
        self.assertIn("stall-pulse", html)
        self.assertIn("prefers-reduced-motion:reduce", html)

    def test_stall_banner_uses_render_age_and_fails_closed(self):
        if not shutil.which("node"):
            self.skipTest("node is needed to execute the inline browser script")
        self.run_board("init", "demo", "--title", "Demo")
        html = (self.root / "boards" / "demo.html").read_text()
        script = html.split("<script>", 1)[1].split("</script>", 1)[0]
        harness = r"""
const vm = require('node:vm');
const source = require('node:fs').readFileSync(0, 'utf8');
const banner = {hidden: true};
let tick;
let now = Date.parse(source.match(/var renderedAt=Date.parse\('([^']+)'\)/)[1]);
const FakeDate = {parse: Date.parse, now: () => now};
function execute(code, target) {
  vm.runInNewContext(code, {
    Date: FakeDate, Number, document: {
      getElementById: () => target, querySelectorAll: () => []
    }, setInterval: callback => {if (!tick) tick = callback},
    addEventListener: () => {}, location: {pathname: '/demo.html'},
    sessionStorage: {getItem: () => null, setItem: () => {}}
  });
}
execute(source, banner);
if (!banner.hidden) process.exit(1);
now += 360001; tick();
if (banner.hidden) process.exit(2);
const invalid = {hidden: true};
execute(source.replace(/var renderedAt=Date.parse\('[^']+'\)/,
                       "var renderedAt=Date.parse('invalid')"), invalid);
if (invalid.hidden) process.exit(3);
const future = {hidden: true};
execute(source.replace(/var renderedAt=Date.parse\('[^']+'\)/,
                       `var renderedAt=Date.parse('${new Date(now + 3600000).toISOString()}')`), future);
if (future.hidden) process.exit(4);
"""
        result = subprocess.run(["node", "-e", harness], input=script, text=True,
                                capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_completed_html_has_links_but_no_automatic_external_requests(self):
        self.run_board("init", "demo", "--title", "Demo")
        self.run_board("task", "demo", "a", "--title", "A", "--status", "done",
                       "--executor", "Codex", "--pr", "42", "--stage", "live",
                       "--evidence", "Observed outcome")
        self.run_board("deliver", "demo", "--title", "Link", "--link", "https://example.com")
        html = (self.root / "boards" / "demo.html").read_text()
        self.assertIn('href="https://github.com/jbookout/carr-system/pull/42"', html)
        self.assertIn('href="https://example.com"', html)

        class ResourceCollector(HTMLParser):
            def __init__(self):
                super().__init__()
                self.resources = []

            def handle_starttag(self, tag, attrs):
                values = dict(attrs)
                self.resources.extend(values.get(name) for name in
                                      ("src", "srcset", "poster", "data", "action", "formaction")
                                      if values.get(name))
                if tag == "link" and values.get("href"):
                    self.resources.append(values["href"])

        parsed = ResourceCollector()
        parsed.feed(html)
        self.assertEqual(parsed.resources, [])
        self.assertNotRegex(html, r"@import|url\(['\"]?https?://|\b(?:fetch|XMLHttpRequest|WebSocket|EventSource|sendBeacon)\s*\(")

    def test_legacy_live_without_evidence_and_done_without_pr_fail_closed(self):
        self.run_board("init", "demo", "--title", "Demo")
        self.run_board("task", "demo", "legacy", "--title", "Legacy", "--status", "done",
                       "--executor", "Codex", "--pr", "42")
        state = self.read_state("demo")
        state["tasks"]["legacy"]["stage"] = "measured"
        state["tasks"]["legacy"]["status"] = "measured"
        (self.root / "boards" / "demo.json").write_text(json.dumps(state))
        self.run_board("task", "demo", "plain", "--title", "Plain", "--status", "done",
                       "--executor", "Codex")
        html = (self.root / "boards" / "demo.html").read_text()
        self.assertIn("0 completed · 2 remaining", html)
        self.assertNotIn('data-task-id="legacy" data-stage="live"', html)
        self.assertNotIn('data-task-id="plain" data-stage="merged"', html)

    def test_reopening_live_clears_completion(self):
        self.run_board("init", "demo", "--title", "Demo")
        self.run_board("task", "demo", "a", "--title", "A", "--status", "done",
                       "--executor", "Codex", "--stage", "live", "--evidence", "Observed outcome")
        self.run_board("task", "demo", "a", "--status", "running")
        task = self.read_state("demo")["tasks"]["a"]
        self.assertEqual(task["status"], "running")
        self.assertNotIn("stage", task)
        self.assertNotIn("evidence", task)
        self.assertNotIn("completed_at", task)

    def test_malformed_github_payload_keeps_previous_status(self):
        self.run_board("init", "demo", "--title", "Demo")
        self.run_board("task", "demo", "a", "--title", "A", "--status", "running",
                       "--executor", "Codex", "--pr", "42")
        prior = self.read_state("demo")
        self.env.pop("PROGRESS_BOARD_SKIP_GH")
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        gh = bin_dir / "gh"
        fixture = self.root / "gh.json"
        gh.write_text("#!/usr/bin/env python3\nimport os\nfrom pathlib import Path\n"
                      "print(Path(os.environ['BOARD_GH_FIXTURE']).read_text())\n")
        gh.chmod(0o755)
        self.env["PATH"] = str(bin_dir) + os.pathsep + self.env["PATH"]
        self.env["BOARD_GH_FIXTURE"] = str(fixture)
        valid = {
            "state": "OPEN", "isDraft": False, "headRefOid": "a" * 40,
            "author": {"login": "builder"},
            "statusCheckRollup": [{"conclusion": "SUCCESS", "status": "COMPLETED"}],
            "comments": [{"author": {"login": "reviewer"},
                          "authorAssociation": "COLLABORATOR",
                          "body": "APPROVE\nReviewed-SHA: " + "a" * 40,
                          "createdAt": "2026-09-28T10:00:00Z"}],
        }
        fields = [
            (("state",), []), (("isDraft",), []), (("headRefOid",), []),
            (("author",), []), (("author", "login"), []),
            (("statusCheckRollup",), {}), (("statusCheckRollup", 0), []),
            (("statusCheckRollup", 0, "conclusion"), []),
            (("statusCheckRollup", 0, "status"), []),
            (("comments",), {}), (("comments", 0), []),
            (("comments", 0, "author"), []),
            (("comments", 0, "author", "login"), []),
            (("comments", 0, "authorAssociation"), []),
            (("comments", 0, "body"), []),
            (("comments", 0, "createdAt"), []),
        ]
        for path, wrong_type in fields:
            for missing in (False, True):
                if missing and isinstance(path[-1], int):
                    continue
                with self.subTest(path=path, missing=missing):
                    payload = copy.deepcopy(valid)
                    parent = payload
                    for part in path[:-1]:
                        parent = parent[part]
                    if missing:
                        del parent[path[-1]]
                    else:
                        parent[path[-1]] = wrong_type
                    fixture.write_text(json.dumps(payload))
                    self.run_board("render", "demo")
                    self.assertEqual(self.read_state("demo"), prior)
                    self.assertIn("GitHub PR data unavailable or invalid",
                                  (self.root / "boards" / "demo.html").read_text())
        fixture.write_text("[]")
        self.run_board("render", "demo")
        self.assertEqual(self.read_state("demo"), prior)

    def test_review_readiness_requires_independent_trusted_latest_verdict(self):
        self.run_board("init", "demo", "--title", "Demo")
        self.run_board("task", "demo", "a", "--title", "A", "--status", "running",
                       "--executor", "Codex", "--pr", "42")
        self.env.pop("PROGRESS_BOARD_SKIP_GH")
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        gh = bin_dir / "gh"
        fixture = self.root / "gh.json"
        gh.write_text("#!/usr/bin/env python3\nimport os\nfrom pathlib import Path\n"
                      "print(Path(os.environ['BOARD_GH_FIXTURE']).read_text())\n")
        gh.chmod(0o755)
        self.env["PATH"] = str(bin_dir) + os.pathsep + self.env["PATH"]
        self.env["BOARD_GH_FIXTURE"] = str(fixture)
        sha = "a" * 40
        base = {"state": "OPEN", "isDraft": False, "headRefOid": sha,
                "author": {"login": "builder"},
                "statusCheckRollup": [{"conclusion": "SUCCESS", "status": "COMPLETED"}]}
        def comment(login, association, body, created):
            return {"author": {"login": login}, "authorAssociation": association,
                    "body": body, "createdAt": created}
        approve = "APPROVE\nReviewed-SHA: " + sha + "\n"
        cases = [
            ([comment("builder", "OWNER", approve, "2026-09-28T10:00:00Z")], "Awaiting review"),
            ([comment("outsider", "NONE", approve, "2026-09-28T10:00:00Z")], "Awaiting review"),
            ([comment("reviewer", "COLLABORATOR", approve, "2026-09-28T10:00:00Z")], "Ready to merge"),
            ([comment("reviewer", "COLLABORATOR", approve, "2026-09-28T10:00:00Z"),
              comment("reviewer", "COLLABORATOR", "BLOCK\nNeeds a fix", "2026-09-28T11:00:00Z")], "Review blocked"),
        ]
        for comments, expected in cases:
            with self.subTest(expected=expected, comments=comments):
                fixture.write_text(json.dumps({**base, "comments": comments}))
                self.run_board("render", "demo")
                self.assertEqual(self.read_state("demo")["tasks"]["a"]["pr_phase"], expected)

    def test_same_pr_number_in_two_repositories_and_legacy_state(self):
        self.run_board("init", "demo", "--title", "Demo")
        self.run_board("task", "demo", "carr", "--title", "CARR fix", "--status", "running",
                       "--executor", "Codex", "--pr", "85")
        self.run_board("task", "demo", "app", "--title", "App fix", "--status", "running",
                       "--executor", "Codex", "--pr", "85", "--repo", "jbookout/doctorcre-app")
        state_path = self.root / "boards" / "demo.json"
        state = json.loads(state_path.read_text())
        self.assertEqual(state["tasks"]["carr"]["repo"], "jbookout/carr-system")
        self.assertEqual(state["tasks"]["app"]["repo"], "jbookout/doctorcre-app")
        del state["tasks"]["carr"]["repo"]  # JSON written before --repo existed
        state_path.write_text(json.dumps(state))

        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        gh = bin_dir / "gh"
        gh.write_text("#!/usr/bin/env python3\nimport json, sys\n"
                      "args = sys.argv\n"
                      "assert args[1:4] == ['pr', 'view', '85']\n"
                      "repo = args[args.index('--repo') + 1]\n"
                      "assert repo in {'jbookout/carr-system', 'jbookout/doctorcre-app'}\n"
                      "print(json.dumps({'state': 'MERGED' if repo.endswith('carr-system') else 'OPEN', "
                      "'isDraft': False, 'headRefOid': 'a' * 40, 'author': {'login': 'builder'}, "
                      "'statusCheckRollup': [{'conclusion': 'SUCCESS', 'status': 'COMPLETED'}], "
                      "'comments': []}))\n")
        gh.chmod(0o755)
        self.env.pop("PROGRESS_BOARD_SKIP_GH")
        self.env["PATH"] = str(bin_dir) + os.pathsep + self.env["PATH"]
        self.run_board("render", "demo")
        state = self.read_state("demo")
        self.assertEqual(state["tasks"]["carr"]["pr_phase"], "Merged")
        self.assertEqual(state["tasks"]["app"]["pr_phase"], "Awaiting review")
        self.assertNotIn("repo", state["tasks"]["carr"])
        html = (self.root / "boards" / "demo.html").read_text()
        self.assertRegex(html, r'data-task-ref="carr"[^>]*>.*?jbookout/carr-system · PR 85')
        self.assertRegex(html, r'data-task-ref="app"[^>]*>.*?jbookout/doctorcre-app · PR 85')
        self.run_board("task", "demo", "app", "--stage", "live", "--evidence", "Observed live")
        self.assertEqual(self.read_state("demo")["tasks"]["app"]["repo"], "jbookout/doctorcre-app")
        self.assertIn('href="https://github.com/jbookout/doctorcre-app/pull/85"',
                      (self.root / "boards" / "demo.html").read_text())

    def test_changing_pr_identity_offline_discards_previous_pr_status(self):
        self.run_board("init", "demo", "--title", "Demo")
        self.run_board("task", "demo", "a", "--title", "A", "--status", "running",
                       "--executor", "Codex", "--pr", "42")
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        gh = bin_dir / "gh"
        gh.write_text("#!/usr/bin/env python3\nimport json\n"
                      "print(json.dumps({'state': 'MERGED', 'isDraft': False, "
                      "'headRefOid': 'a' * 40, 'author': {'login': 'builder'}, "
                      "'statusCheckRollup': [{'conclusion': 'SUCCESS', 'status': 'COMPLETED'}], "
                      "'comments': []}))\n")
        gh.chmod(0o755)
        self.env.pop("PROGRESS_BOARD_SKIP_GH")
        self.env["PATH"] = str(bin_dir) + os.pathsep + self.env["PATH"]
        self.run_board("render", "demo")
        old = self.read_state("demo")["tasks"]["a"]
        self.assertEqual((old["status"], old["stage"], old["pr_phase"], old["pr_head"]),
                         ("done", "merged", "Merged", "a" * 40))

        gh.write_text("#!/bin/sh\nexit 1\n")
        self.run_board("task", "demo", "a", "--repo", "jbookout/doctorcre-app")
        moved = self.read_state("demo")["tasks"]["a"]
        self.assertEqual((moved["repo"], moved["pr"]), ("jbookout/doctorcre-app", 42))
        self.assertEqual((moved["status"], moved["stage"]), ("running", "build"))
        for field in ("pr_phase", "pr_checks", "pr_head", "evidence", "completed_at"):
            self.assertNotIn(field, moved)
        html = (self.root / "boards" / "demo.html").read_text()
        self.assertIn("jbookout/doctorcre-app · PR 42 · status unavailable · checks unavailable", html)
        self.assertNotIn("jbookout/doctorcre-app · PR 42 · Merged", html)

        gh.write_text("#!/usr/bin/env python3\nimport json\n"
                      "print(json.dumps({'state': 'MERGED', 'isDraft': False, "
                      "'headRefOid': 'b' * 40, 'author': {'login': 'builder'}, "
                      "'statusCheckRollup': [{'conclusion': 'SUCCESS', 'status': 'COMPLETED'}], "
                      "'comments': []}))\n")
        self.run_board("render", "demo")
        self.run_board("task", "demo", "a", "--stage", "live", "--evidence", "Measured result")
        gh.write_text("#!/bin/sh\nexit 1\n")
        self.run_board("task", "demo", "a", "--pr", "43")
        changed_number = self.read_state("demo")["tasks"]["a"]
        self.assertEqual((changed_number["repo"], changed_number["pr"]), ("jbookout/doctorcre-app", 43))
        self.assertEqual((changed_number["status"], changed_number["stage"]), ("running", "build"))
        for field in ("pr_phase", "pr_checks", "pr_head", "evidence", "completed_at"):
            self.assertNotIn(field, changed_number)

    def test_pr_derivation_and_offline_retention(self):
        self.run_board("init", "demo", "--title", "Demo")
        self.run_board("task", "demo", "a", "--title", "A", "--status", "running",
                       "--executor", "Codex", "--pr", "42", "--note", "Keep this note")
        self.env.pop("PROGRESS_BOARD_SKIP_GH")
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        gh = bin_dir / "gh"
        gh.write_text("#!/usr/bin/env python3\nimport os,sys\nfrom pathlib import Path\n"
                      "print(Path(os.environ['BOARD_GH_FIXTURE']).read_text())\n")
        gh.chmod(0o755)
        self.env["PATH"] = str(bin_dir) + os.pathsep + self.env["PATH"]
        fixture = self.root / "gh.json"
        self.env["BOARD_GH_FIXTURE"] = str(fixture)
        sha = "a" * 40
        base = {"state": "OPEN", "isDraft": False, "headRefOid": sha,
                "author": {"login": "builder"},
                "statusCheckRollup": [{"conclusion": "SUCCESS", "status": "COMPLETED"}],
                "comments": []}
        def review(body):
            return {"author": {"login": "reviewer"}, "authorAssociation": "COLLABORATOR",
                    "createdAt": "2026-09-28T10:00:00Z", "body": body}
        cases = [
            ({"state": "MERGED"}, "done", "merged", "Merged"),
            ({"state": "CLOSED"}, "failed", "ci", "Closed unmerged"),
            ({"isDraft": True}, "running", "build", "Draft"),
            ({"statusCheckRollup": [{"conclusion": "FAILURE", "status": "COMPLETED"}]}, "blocked", "ci", "Checks failing"),
            ({"statusCheckRollup": [{"conclusion": "", "status": "IN_PROGRESS"}]}, "running", "ci", "CI"),
            ({"comments": [review("APPROVE\nReviewed-SHA: " + "b" * 40 + "\n")]}, "review", "review", "Awaiting review"),
            ({"comments": [review("APPROVE\nReviewed-SHA: " + sha + "\n")]}, "review", "review", "Ready to merge"),
        ]
        previous_update = self.read_state("demo")["tasks"]["a"]["updated_at"]
        for change, status, stage, phase in cases:
            with self.subTest(phase=phase):
                fixture.write_text(json.dumps({**base, **change}))
                self.run_board("render", "demo")
                task = self.read_state("demo")["tasks"]["a"]
                self.assertEqual((task["status"], task["stage"], task["pr_phase"]),
                                 (status, stage, phase))
                self.assertEqual(task["note"], "Keep this note")
                self.assertGreater(task["updated_at"], previous_update)
                previous_update = task["updated_at"]
                self.assertIn(phase, (self.root / "boards" / "demo.html").read_text())
        self.run_board("render", "demo")
        self.assertEqual(self.read_state("demo")["tasks"]["a"]["updated_at"], previous_update)
        prior = self.read_state("demo")
        gh.write_text("#!/bin/sh\nexit 1\n")
        self.run_board("render", "demo")
        self.assertEqual(self.read_state("demo"), prior)
        self.assertIn("GitHub PR data unavailable or invalid", (self.root / "boards" / "demo.html").read_text())
        gh.write_text("#!/usr/bin/env python3\nimport os\nfrom pathlib import Path\n"
                      "print(Path(os.environ['BOARD_GH_FIXTURE']).read_text())\n")
        fixture.write_text(json.dumps({**base, "state": "MERGED"}))
        self.run_board("render", "demo")
        self.assertEqual(self.read_state("demo")["tasks"]["a"]["stage"], "merged")
        self.assertIn("0 completed · 1 remaining", (self.root / "boards" / "demo.html").read_text())
        self.run_board("task", "demo", "a", "--stage", "live", "--evidence", "Production response measured")
        completed_at = self.read_state("demo")["tasks"]["a"]["completed_at"]
        self.run_board("render", "demo")
        task = self.read_state("demo")["tasks"]["a"]
        self.assertEqual(task["stage"], "live")
        self.assertEqual(task["completed_at"], completed_at)

    def test_stage_palette_is_shared_by_nodes_columns_and_status_chips(self):
        self.run_board("init", "demo", "--title", "Demo")
        for stage in ("queued", "build", "review", "ci", "merged", "live"):
            args = ["task", "demo", stage, "--title", stage, "--status", "queued",
                    "--executor", "Codex"]
            if stage != "queued":
                args += ["--pr", "42", "--stage", stage]
            if stage == "live":
                args += ["--evidence", "Observed successful operation"]
            self.run_board(*args)
        html = (self.root / "boards" / "demo.html").read_text()
        colors = {"queued": "#f2f6fc", "build": "#fb7b32", "review": "#bf9cff",
                  "ci": "#ff88bd", "merged": "#65baff", "live": "#7dddc0"}
        for stage, color in colors.items():
            with self.subTest(stage=stage):
                self.assertIn(f"--stage-{stage}:{color}", html)
                self.assertIn(f'.stage[data-stage="{stage}"]', html)
                self.assertIn(f'.pipeline-node[data-stage="{stage}"]', html)
                self.assertIn(f'.task-card[data-stage="{stage}"]', html)
                self.assertIn(f'data-task-ref="{stage}"', html)
                label = {"build": "Building", "ci": "CI"}.get(stage, stage.title())
                self.assertIn(f'class="stage-chip">{label}</span>', html)

    def test_live_requires_explicit_evidence_and_merge_does_not_complete(self):
        self.run_board("init", "demo", "--title", "Demo")
        self.run_board("task", "demo", "a", "--title", "A", "--status", "running",
                       "--executor", "Codex", "--pr", "42")
        missing = subprocess.run([sys.executable, str(SCRIPT), "task", "demo", "a", "--stage", "live"],
                                 cwd=REPO, env=self.env, text=True, capture_output=True)
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("evidence", missing.stderr.lower())
        self.assertEqual(self.read_state("demo")["tasks"]["a"].get("stage"), None)
        self.run_board("task", "demo", "a", "--stage", "live", "--evidence", "Page returned expected data")
        task = self.read_state("demo")["tasks"]["a"]
        self.assertEqual(task["stage"], "live")
        self.assertEqual(task["evidence"], "Page returned expected data")
        self.assertTrue(task["completed_at"])
        self.assertIn("Page returned expected data", (self.root / "boards" / "demo.html").read_text())

    def test_legacy_measured_maps_to_live(self):
        self.run_board("init", "demo", "--title", "Demo")
        self.run_board("task", "demo", "legacy", "--title", "Legacy", "--status", "done",
                       "--executor", "Codex", "--pr", "42", "--stage", "measured",
                       "--evidence", "Legacy verified")
        state = self.read_state("demo")
        self.assertEqual(state["tasks"]["legacy"]["stage"], "live")
        state["tasks"]["legacy"]["stage"] = "measured"
        (self.root / "boards" / "demo.json").write_text(json.dumps(state))
        self.run_board("render", "demo")
        html = (self.root / "boards" / "demo.html").read_text()
        self.assertIn('data-task-id="legacy" data-stage="live"', html)
        self.assertNotIn("Measured", html)

    def test_completed_order_counts_and_pipeline_retirement(self):
        self.run_board("init", "demo", "--title", "Demo")
        for task_id in ("old", "new", "waiting"):
            self.run_board("task", "demo", task_id, "--title", task_id, "--status", "running",
                           "--executor", "Codex", "--pr", "42")
        for task_id in ("old", "new"):
            self.run_board("task", "demo", task_id, "--stage", "live",
                           "--evidence", f"{task_id} measured")
        state = self.read_state("demo")
        now = datetime.now(timezone.utc)
        state["tasks"]["old"]["completed_at"] = (now - timedelta(hours=25)).isoformat()
        state["tasks"]["new"]["completed_at"] = (now - timedelta(hours=1)).isoformat()
        (self.root / "boards" / "demo.json").write_text(json.dumps(state))
        self.run_board("render", "demo")
        html = (self.root / "boards" / "demo.html").read_text()
        self.assertIn("2 completed · 1 remaining", html)
        completed = html.split('<h2>Completed</h2>', 1)[1]
        self.assertLess(completed.index('data-task-ref="new"'), completed.index('data-task-ref="old"'))
        self.assertIn("old measured", completed)
        self.assertIn("new measured", completed)
        self.assertIn("https://github.com/jbookout/carr-system/pull/42", completed)
        self.assertIn("CT", completed)
        pipeline = html.split('<h2>Delivery pipeline</h2>', 1)[1].split('<h2>Tasks by status</h2>', 1)[0]
        self.assertIn('data-task-id="new"', pipeline)
        self.assertNotIn('data-task-id="old"', pipeline)

    def test_static_board_links_to_signed_in_route_and_preserves_asker(self):
        self.run_board("init", "demo", "--title", "Demo")
        self.run_board("ask", "demo", "q1", "--question", "Choose route?", "--default", "Proceed",
                       "--asker-ref", "one-shot:session-1", "--choice", "Proceed", "--choice", "Hold")
        question = self.read_state("demo")["questions"]["q1"]
        self.assertEqual(question["asker_ref"], "one-shot:session-1")
        self.assertEqual(question["choices"], ["Proceed", "Hold"])
        html = (self.root / "boards" / "demo.html").read_text()
        self.assertIn('href="https://app.doctorcre.com/progress-board?board=demo"', html)
        self.assertNotIn('target="_blank"', html)
        self.assertNotIn("one-shot:session-1", html)

    def test_publish_uses_remote_version_and_checks_readback(self):
        self.run_board("init", "demo", "--title", "Demo")
        self.run_board("ask", "demo", "q1", "--question", "Choose route?", "--default", "Proceed",
                       "--asker-ref", "one-shot:session-1", "--choice", "Proceed", "--choice", "Hold")
        calls = []
        remote = {"snapshot": None, "questions": []}

        def caller(verb, args):
            calls.append((verb, args))
            if verb == "read-progress-board":
                return {"ok": True, **remote}
            if verb == "publish-board-snapshot":
                remote["snapshot"] = {"board_id": "demo", "version": 1, "snapshot_json": args["snapshot"]}
                return {"ok": True, "snapshot": remote["snapshot"]}
            if verb == "ask-board-question":
                remote["questions"] = [{"question_id": "q1", "revision": 1, "prompt": args["prompt"],
                                        "choices": args["choices"], "allow_free_text": args["allow_free_text"],
                                        "default_answer": args["default_answer"], "asker_ref": args["asker_ref"]}]
                return {"ok": True, "question": remote["questions"][0]}
            raise AssertionError(verb)

        with patch.dict(os.environ, {"PROGRESS_BOARD_ROOT": str(self.root)}), patch.object(BOARD, "call_verb", caller):
            result = BOARD.publish_board("demo")
        self.assertEqual(result["snapshot_version"], 1)
        self.assertEqual([verb for verb, _ in calls], ["read-progress-board", "publish-board-snapshot",
                                                       "ask-board-question", "read-progress-board"])
        self.assertEqual(calls[1][1]["base_version"], 0)
        self.assertEqual(calls[2][1]["asker_ref"], "one-shot:session-1")
        self.assertNotIn("answer", calls[1][1]["snapshot"])

    def test_legacy_question_revision_retains_free_text_mode(self):
        self.run_board("init", "demo", "--title", "Demo")
        self.run_board("ask", "demo", "q1", "--question", "Original?", "--default", "Proceed")
        state = self.read_state("demo")
        legacy = state["questions"]["q1"]
        for field in ("choices", "free_text", "asker_ref", "revision", "history"):
            legacy.pop(field)
        (self.root / "boards" / "demo.json").write_text(json.dumps(state))
        self.run_board("ask", "demo", "q1", "--question", "Revised?", "--default", "Proceed")
        old = self.read_state("demo")["questions"]["q1"]["history"][0]
        fields = BOARD.question_revision(old, "demo")
        self.assertEqual(fields["choices"], [])
        self.assertIs(fields["allow_free_text"], True)
        self.assertEqual(fields["asker_ref"], "orchestrator:demo")

    def test_publish_normalizes_question_and_default_on_repeat(self):
        self.run_board("init", "demo", "--title", "Demo")
        self.run_board("ask", "demo", "q1", "--question", "  Choose route?  ",
                       "--default", "  Proceed  ")
        remote = {"snapshot": None, "questions": []}
        writes = []

        def caller(verb, args):
            if verb == "read-progress-board":
                return {"ok": True, **remote}
            writes.append(verb)
            if verb == "publish-board-snapshot":
                remote["snapshot"] = {"version": 1, "snapshot_json": args["snapshot"]}
                return {"ok": True, "snapshot": remote["snapshot"]}
            if verb == "ask-board-question":
                remote["questions"] = [{"question_id": "q1", "revision": 1,
                                        "prompt": args["prompt"].strip(), "choices": args["choices"],
                                        "allow_free_text": args["allow_free_text"],
                                        "default_answer": args["default_answer"].strip(),
                                        "asker_ref": args["asker_ref"]}]
                return {"ok": True, "question": remote["questions"][0]}
            raise AssertionError(verb)

        with patch.dict(os.environ, {"PROGRESS_BOARD_ROOT": str(self.root)}), patch.object(BOARD, "call_verb", caller):
            BOARD.publish_board("demo")
            BOARD.publish_board("demo")
        self.assertEqual(writes, ["publish-board-snapshot", "ask-board-question"])
        self.assertEqual(remote["questions"][0]["prompt"], "Choose route?")
        self.assertEqual(remote["questions"][0]["default_answer"], "Proceed")

    def test_poller_rejects_answer_for_other_board_or_question_before_inbox_or_ack(self):
        self.run_board("init", "demo", "--title", "Demo")
        self.run_board("ask", "demo", "q1", "--question", "Choose route?", "--default", "Proceed",
                       "--asker-ref", "one-shot:shared")
        base = {"id": "11111111-1111-4111-8111-111111111111", "cursor": "7",
                "board_id": "demo", "question_id": "q1", "question_revision": 1,
                "asker_ref": "one-shot:shared", "answer_text": "Hold", "version": 1, "status": "Sent"}
        for mismatch in ({"board_id": "other"}, {"question_id": "q2"}, {"question_revision": 2}):
            calls = []

            def caller(verb, args):
                calls.append(verb)
                if verb == "read-board-answers":
                    return {"ok": True, "answers": [{**base, **mismatch}]}
                raise AssertionError(verb)

            with self.subTest(mismatch=mismatch), patch.dict(os.environ, {"PROGRESS_BOARD_ROOT": str(self.root)}), \
                 patch.object(BOARD, "call_verb", caller):
                with self.assertRaisesRegex(RuntimeError, "does not match"):
                    BOARD.poll_board_answers("demo")
                self.assertFalse((self.root / "boards" / "demo-answers.jsonl").exists())
                self.assertEqual(calls, ["read-board-answers"])

    def test_poller_durably_records_then_acknowledges_and_replays_pending_ack(self):
        self.run_board("init", "demo", "--title", "Demo")
        self.run_board("ask", "demo", "q1", "--question", "Choose route?", "--default", "Proceed",
                       "--asker-ref", "one-shot:session-1")
        answer = {"id": "11111111-1111-4111-8111-111111111111", "cursor": "7", "board_id": "demo",
                  "question_id": "q1", "question_revision": 1, "asker_ref": "one-shot:session-1",
                  "answer_text": "Hold", "answered_by": "joe", "version": 1, "status": "Sent"}
        failed_once = False
        calls = []

        def caller(verb, args):
            nonlocal failed_once
            calls.append((verb, args))
            if verb == "read-board-answers":
                return {"ok": True, "answers": [answer] if args["after_cursor"] < 7 else [], "next_cursor": 7}
            if verb == "acknowledge-board-answer":
                inbox = (self.root / "boards" / "demo-answers.jsonl").read_text()
                self.assertIn(answer["id"], inbox, "inbox must be durable before Received")
                if not failed_once:
                    failed_once = True
                    raise RuntimeError("temporary refusal")
                return {"ok": True, "answer": {**answer, "version": 2, "status": "Received"}}
            raise AssertionError(verb)

        with patch.dict(os.environ, {"PROGRESS_BOARD_ROOT": str(self.root)}), patch.object(BOARD, "call_verb", caller):
            with self.assertRaises(RuntimeError):
                BOARD.poll_board_answers("demo")
            BOARD.poll_board_answers("demo")
        events = [json.loads(line) for line in (self.root / "boards" / "demo-answers.jsonl").read_text().splitlines()]
        self.assertEqual([event["kind"] for event in events], ["answer", "ack"])
        self.assertEqual([args["after_cursor"] for verb, args in calls if verb == "read-board-answers"], [0, 6, 7])
        self.assertEqual(len([verb for verb, _ in calls if verb == "acknowledge-board-answer"]), 2)

    def test_poller_recovers_received_ack_after_crash_before_local_receipt(self):
        self.run_board("init", "demo", "--title", "Demo")
        self.run_board("ask", "demo", "q1", "--question", "Choose route?", "--default", "Proceed",
                       "--asker-ref", "one-shot:session-1")
        answer = {"id": "11111111-1111-4111-8111-111111111111", "cursor": "7", "board_id": "demo",
                  "question_id": "q1", "question_revision": 1, "asker_ref": "one-shot:session-1",
                  "answer_text": "Hold", "answered_by": "joe", "version": 1, "status": "Sent"}

        def caller(verb, args):
            if verb == "read-board-answers":
                return {"ok": True, "answers": [{**answer, "version": 2, "status": "Received"}]
                        if args["after_cursor"] == 6 else [], "next_cursor": 7}
            if verb == "acknowledge-board-answer":
                raise RuntimeError("board_version_conflict")
            raise AssertionError(verb)

        with patch.dict(os.environ, {"PROGRESS_BOARD_ROOT": str(self.root)}), patch.object(BOARD, "call_verb", caller):
            BOARD.append_answer_event("demo", {"kind": "answer", "answer": answer})
            BOARD.poll_board_answers("demo")
        events = [json.loads(line) for line in (self.root / "boards" / "demo-answers.jsonl").read_text().splitlines()]
        self.assertEqual([event["kind"] for event in events], ["answer", "ack"])

    def test_existing_launchd_render_runs_publish_and_poll_without_a_model(self):
        events = []
        args = type("Args", (), {"project": "carr-v5", "publish": False})()
        with patch.object(BOARD, "render", lambda project: events.append(("render", project))), \
             patch.object(BOARD, "publish_board", lambda project: events.append(("publish", project))), \
             patch.object(BOARD, "poll_board_answers", lambda project: events.append(("poll", project))):
            BOARD.command_render(args)
        self.assertEqual(events, [("render", "carr-v5"), ("publish", "carr-v5"),
                                  ("poll", "carr-v5")])


if __name__ == "__main__":
    unittest.main(verbosity=2)
