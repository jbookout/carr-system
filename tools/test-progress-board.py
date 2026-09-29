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
