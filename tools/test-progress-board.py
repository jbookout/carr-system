#!/usr/bin/env python3
"""Behavioral tests for the progress-board command line surface."""

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "tools" / "progress_board.py"


class ProgressBoardCLI(unittest.TestCase):
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

    def test_html_has_all_panels_and_no_external_urls(self):
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
        self.assertNotRegex(html, r"https?://")
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
        )

        state = self.read_state("demo")
        self.assertEqual(state["tasks"]["ci-task"]["stage"], "ci")
        self.assertEqual(state["tasks"]["blocked-task"]["stage"], "merged")
        html = (self.root / "boards" / "demo.html").read_text()

        self.assertEqual(html.count('class="pipeline-node '), 8)  # desk and phone SVGs
        for task_id, stage in (
            ("queued-task", "queued"), ("ci-task", "ci"),
            ("blocked-task", "merged"), ("question-task", "measured"),
        ):
            self.assertEqual(html.count(f'data-task-id="{task_id}" data-stage="{stage}"'), 2)
        for label in ("Queued", "Building", "Review", "CI", "Merged", "Measured"):
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
                "statusCheckRollup": [{"conclusion": "SUCCESS", "status": "COMPLETED"}],
                "comments": []}
        cases = [
            ({"state": "MERGED"}, "done", "merged", "Merged"),
            ({"state": "CLOSED"}, "failed", "ci", "Closed unmerged"),
            ({"isDraft": True}, "running", "build", "Draft"),
            ({"statusCheckRollup": [{"conclusion": "FAILURE", "status": "COMPLETED"}]}, "blocked", "ci", "Checks failing"),
            ({"statusCheckRollup": [{"conclusion": "", "status": "IN_PROGRESS"}]}, "running", "ci", "CI"),
            ({"comments": [{"body": "APPROVE\nReviewed-SHA: " + "b" * 40 + "\n"}]}, "review", "review", "Awaiting review"),
            ({"comments": [{"body": "APPROVE\nReviewed-SHA: " + sha + "\n"}]}, "review", "review", "Ready to merge"),
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
        self.assertIn("GitHub unreachable", (self.root / "boards" / "demo.html").read_text())


if __name__ == "__main__":
    unittest.main(verbosity=2)
