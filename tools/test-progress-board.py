#!/usr/bin/env python3
"""Behavioral tests for the progress-board command line surface.

The board has exactly one UI, the interactive app page at
app.doctorcre.com/progress-board. This tool owns the task/question/answer
commands and publishes the JSON data contract that page renders.
"""

import copy
import json
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timedelta, timezone
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "tools" / "progress_board.py"
LAUNCHD_SCRIPT = REPO / "ops" / "progress-board-render.sh"
SPEC = importlib.util.spec_from_file_location("progress_board", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
BOARD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BOARD)

# One dispatcher for every gh call the board makes. The fixture file says what
# each call returns; tests rewrite it between runs.
FAKE_GH = r'''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
fixture = json.loads(Path(os.environ["BOARD_GH_FIXTURE"]).read_text())
args = sys.argv[1:]
log = os.environ.get("BOARD_GH_LOG")
if log:
    with open(log, "a") as fh:
        fh.write(json.dumps(args) + "\n")
def value(flag):
    return args[args.index(flag) + 1] if flag in args else None
if args[:2] == ["pr", "view"]:
    view = fixture.get("view")
    if view is None:
        sys.exit(1)
    print(json.dumps(view) if not isinstance(view, str) else view)
elif args[:2] == ["repo", "list"]:
    if fixture.get("repo_list_fails"):
        sys.exit(1)
    print(json.dumps([{"nameWithOwner": name} for name in fixture.get("repos", [])]))
elif args[:2] == ["pr", "list"]:
    repo = value("--repo")
    if repo in fixture.get("fail", []):
        sys.exit(1)
    print(json.dumps(fixture.get(value("--state"), {}).get(repo, [])))
elif args[0] == "api":
    path = args[1]
    status = fixture.get("compare", {}).get(path)
    if status is None:
        sys.exit(1)
    print(status)
else:
    sys.exit(2)
'''

SHA_A = "a" * 40
SHA_M = "1" * 40
SHA_R = "2" * 40


def iso(delta: timedelta = timedelta()) -> str:
    return (datetime.now(timezone.utc) - delta).isoformat(timespec="seconds").replace("+00:00", "Z")


class BoardCase(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.env = os.environ.copy()
        self.env["PROGRESS_BOARD_ROOT"] = str(self.root)
        self.env["PROGRESS_BOARD_SKIP_GH"] = "1"
        self.env["PROGRESS_BOARD_SKIP_PROBE"] = "1"
        self.env["PROGRESS_BOARD_RELEASES"] = str(self.root / "releases.jsonl")
        self.fixture = self.root / "gh.json"
        self.gh_log = self.root / "gh.log"

    def tearDown(self):
        self.tempdir.cleanup()

    def run_board(self, *args, input_text=None, check=True):
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            cwd=REPO,
            env=self.env,
            input=input_text,
            text=True,
            capture_output=True,
            check=check,
        )

    def read_state(self, project):
        return json.loads((self.root / "boards" / f"{project}.json").read_text())

    def write_state(self, project, state):
        (self.root / "boards" / f"{project}.json").write_text(json.dumps(state))

    def fake_gh(self, fixture):
        self.env.pop("PROGRESS_BOARD_SKIP_GH", None)
        bin_dir = self.root / "bin"
        bin_dir.mkdir(exist_ok=True)
        gh = bin_dir / "gh"
        gh.write_text(FAKE_GH)
        gh.chmod(0o755)
        self.env["PATH"] = str(bin_dir) + os.pathsep + self.env["PATH"]
        self.env["BOARD_GH_FIXTURE"] = str(self.fixture)
        self.env["BOARD_GH_LOG"] = str(self.gh_log)
        self.fixture.write_text(json.dumps(fixture))
        return gh

    def set_fixture(self, fixture):
        self.fixture.write_text(json.dumps(fixture))

    def gh_calls(self):
        if not self.gh_log.exists():
            return []
        return [json.loads(line) for line in self.gh_log.read_text().splitlines()]

    def shipped(self, lane, sha, ts="2026-09-29T12:00:00+00:00"):
        with open(self.root / "releases.jsonl", "a") as fh:
            fh.write(json.dumps({"ts": ts, "lane": lane, "sha": sha, "status": "shipped"}) + "\n")


class ProgressBoardCLI(BoardCase):
    def test_init_task_ask_answer_and_deliver(self):
        self.run_board("init", "demo", "--title", "Demo project")
        self.run_board("task", "demo", "build", "--title", "Build board", "--status", "running",
                       "--executor", "codex gpt-5.6-luna high", "--pr", "42", "--note", "working")
        self.run_board("ask", "demo", "q1", "--question", "Ship this?", "--default", "continue on the default")
        self.run_board("answer", "demo", "q1", input_text="yes\n")
        self.run_board("deliver", "demo", "--title", "Board ready", "--link", "/tmp/board.json")
        self.run_board("note", "demo", "--text", "Waiting on the release window")

        state = self.read_state("demo")
        self.assertEqual(state["title"], "Demo project")
        self.assertEqual(state["tasks"]["build"]["status"], "running")
        self.assertEqual(state["tasks"]["build"]["pr"], 42)
        self.assertEqual(state["questions"]["q1"]["answer"], "yes")
        self.assertTrue(state["questions"]["q1"]["answered_at"])
        self.assertEqual(state["deliverables"][0]["link"], "/tmp/board.json")
        self.assertEqual(state["notes"][0]["text"], "Waiting on the release window")
        self.assertTrue(state["updated_at"])

    def test_static_html_renderer_is_retired(self):
        for name in ("render_state", "pipeline_svg", "html_path", "esc", "local_updated"):
            self.assertFalse(hasattr(BOARD, name), f"{name} belongs to the retired static page")
        source = SCRIPT.read_text()
        self.assertNotIn("<!doctype html>", source.lower())
        self.assertNotIn("<svg", source)
        self.run_board("init", "demo", "--title", "Demo")
        leftover = self.root / "boards" / "demo.html"
        leftover.write_text("<html>stale copy</html>")
        self.run_board("task", "demo", "a", "--title", "A", "--status", "running", "--executor", "Codex")
        self.run_board("render", "demo")
        self.assertFalse(leftover.exists(), "render removes the retired static copy")
        self.assertEqual(sorted(p.name for p in (self.root / "boards").iterdir()), ["demo.json"])
        help_text = self.run_board("--help").stdout
        self.assertNotIn(".html", help_text)

    def test_launchd_render_script_publishes_json_only(self):
        script = LAUNCHD_SCRIPT.read_text()
        self.assertIn("tools/progress_board.py render carr-v5 --publish", script)
        for retired in (".html", "iCloud", "Mobile Documents", "CloudDocs"):
            self.assertNotIn(retired, script)
        self.assertTrue(os.access(LAUNCHD_SCRIPT, os.X_OK))
        agents = (REPO / "AGENTS.md").read_text()
        self.assertNotIn("out/boards/<project>.html", agents)
        self.assertIn("app.doctorcre.com/progress-board", agents)

    def test_snapshot_is_the_versioned_data_contract(self):
        self.run_board("init", "demo", "--title", "Demo")
        self.run_board("task", "demo", "a", "--title", "Build card", "--status", "running",
                       "--executor", "gpt-6-sol high (Codex)", "--summary", "Show the card.")
        self.run_board("task", "demo", "b", "--title", "Seat", "--status", "queued",
                       "--executor", "claude-plan Opus subagent")
        self.run_board("ask", "demo", "q1", "--question", "Ship?", "--default", "Proceed")
        self.run_board("ask", "demo", "q2", "--question", "Colour?", "--default", "Blue")
        self.run_board("answer", "demo", "q2", "--answer", "Green")
        self.run_board("deliver", "demo", "--title", "Spec", "--link", "https://example.com/spec")
        self.run_board("note", "demo", "--text", "Deploy freeze after 5pm")
        snapshot = BOARD.board_snapshot(self.read_state("demo"))
        self.assertEqual(snapshot["schema"], "carr-progress-board.v2")
        self.assertEqual(snapshot["kind"], "project")
        self.assertEqual(set(snapshot), {"schema", "kind", "project", "title", "tasks", "deliverables",
                                         "notes", "decisions", "ledger", "repos", "updated_at"})
        self.assertEqual(snapshot["tasks"]["a"]["provider"], "Codex")
        self.assertEqual(snapshot["tasks"]["a"]["model"], "gpt-6-sol")
        self.assertEqual(snapshot["tasks"]["a"]["effort"], "high")
        self.assertEqual(snapshot["tasks"]["a"]["summary"], "Show the card.")
        self.assertEqual(snapshot["decisions"], [{"id": "q2", "question": "Colour?", "answer": "Green",
                                                  "default": "Blue", "answered_at": snapshot["decisions"][0]["answered_at"]}])
        self.assertEqual(snapshot["notes"][0]["text"], "Deploy freeze after 5pm")
        pools = {row["pool"]: row for row in snapshot["ledger"]}
        self.assertEqual(list(pools)[:5], ["codex", "grok", "flash-next", "claude-cloud", "orchestrator"])
        self.assertEqual(pools["codex"]["count"], 1)
        self.assertEqual(pools["codex"]["glyph"], "C")
        self.assertEqual(pools["codex"]["models"], [{"provider": "Codex", "model": "gpt-6-sol",
                                                     "effort": "high", "count": 1}])
        self.assertTrue(pools["claude-cloud"]["violation"])
        self.assertFalse(pools["codex"]["violation"])
        self.assertNotIn("questions", snapshot)
        self.assertLess(len(json.dumps(snapshot)), 262144)

    def test_stuck_is_derived_for_blocked_and_old_running_tasks(self):
        self.run_board("init", "demo", "--title", "Demo")
        self.run_board("task", "demo", "blocked", "--title", "Blocked", "--status", "blocked",
                       "--executor", "codex", "--reason", "Waiting on API key", "--next-action", "Joe adds the key")
        self.run_board("task", "demo", "running", "--title", "Old", "--status", "running", "--executor", "codex")
        state = self.read_state("demo")
        state["tasks"]["running"]["updated_at"] = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
        self.assertTrue(BOARD.is_stuck(state["tasks"]["blocked"]))
        self.assertTrue(BOARD.is_stuck(state["tasks"]["running"]))
        self.assertEqual(BOARD.task_health(state["tasks"]["running"]), "blocked")
        self.assertEqual(BOARD.pulse_state(state["tasks"]["running"]), "critical")

    def test_blocked_requires_reason_and_next_action_and_clears_when_unblocked(self):
        self.run_board("init", "demo", "--title", "Demo")
        self.run_board("task", "demo", "a", "--title", "A", "--status", "running", "--executor", "Codex")
        for extra in ([], ["--reason", "Needs a key"], ["--next-action", "Add it"]):
            with self.subTest(extra=extra):
                refused = self.run_board("task", "demo", "a", "--status", "blocked", *extra, check=False)
                self.assertNotEqual(refused.returncode, 0)
                self.assertIn("--reason and --next-action", refused.stderr)
        refused = self.run_board("task", "demo", "a", "--health", "blocked", check=False)
        self.assertNotEqual(refused.returncode, 0)
        self.run_board("task", "demo", "a", "--status", "blocked", "--reason", "Needs a key",
                       "--next-action", "Joe adds the key")
        task = self.read_state("demo")["tasks"]["a"]
        self.assertEqual(BOARD.blocked_detail(task), ("Needs a key", "Joe adds the key"))
        self.run_board("task", "demo", "a", "--note", "still waiting")  # keeps the recorded reason
        self.assertEqual(self.read_state("demo")["tasks"]["a"]["blocked_reason"], "Needs a key")
        self.run_board("task", "demo", "a", "--status", "running")
        task = self.read_state("demo")["tasks"]["a"]
        self.assertNotIn("blocked_reason", task)
        self.assertNotIn("next_action", task)
        self.assertIsNone(BOARD.blocked_detail(task))

    def test_every_blocked_card_has_a_reason_and_next_action(self):
        at = datetime(2026, 9, 29, 12, tzinfo=timezone.utc)
        fresh = "2026-09-29T11:30:00+00:00"
        cases = {
            "checks": ({"status": "blocked", "pr": 1, "pr_phase": "Checks failing", "updated_at": fresh},
                       "CI checks are failing on the PR head"),
            "review": ({"status": "blocked", "pr": 1, "pr_phase": "Review blocked", "updated_at": fresh},
                       "An independent reviewer posted BLOCK"),
            "conflict": ({"status": "blocked", "pr": 1, "pr_phase": "Merge conflict", "updated_at": fresh},
                         "Merge conflict with the base branch"),
            "changes": ({"status": "blocked", "pr": 1, "pr_phase": "Changes requested", "updated_at": fresh},
                        "A reviewer requested changes"),
            "closed": ({"status": "failed", "pr": 1, "pr_phase": "Closed unmerged", "updated_at": fresh},
                       "PR closed without merging"),
            "failed": ({"status": "failed", "updated_at": fresh}, "Task failed"),
            "stuck": ({"status": "running", "updated_at": "2026-09-29T08:45:00+00:00"},
                      "No update for 3h 15m"),
            "legacy": ({"status": "blocked", "updated_at": fresh}, "Marked blocked without a recorded reason"),
            "health": ({"status": "running", "health": "blocked", "updated_at": fresh},
                       "Marked blocked without a recorded reason"),
        }
        for name, (task, reason) in cases.items():
            with self.subTest(name=name):
                self.assertEqual(BOARD.task_health(task, at), "blocked")
                detail = BOARD.blocked_detail(task, at)
                self.assertIsNotNone(detail)
                self.assertEqual(detail[0], reason)
                self.assertTrue(detail[1].strip())
        self.assertIsNone(BOARD.blocked_detail({"status": "running", "updated_at": fresh}, at))
        explicit = {"status": "blocked", "pr_phase": "Checks failing", "blocked_reason": "Flaky runner",
                    "next_action": "Re-run once", "updated_at": fresh}
        self.assertEqual(BOARD.blocked_detail(explicit, at), ("Flaky runner", "Re-run once"))

    def test_live_or_done_cards_drop_leftover_blocked_health(self):
        self.run_board("init", "demo", "--title", "Demo")
        self.run_board("task", "demo", "a", "--title", "A", "--status", "running", "--executor", "Codex",
                       "--pr", "42", "--health", "blocked", "--reason", "Review round 1",
                       "--next-action", "Fix the findings")
        self.run_board("task", "demo", "a", "--stage", "live", "--evidence", "Served in production")
        task = self.read_state("demo")["tasks"]["a"]
        for field in ("health", "blocked_reason", "next_action"):
            self.assertNotIn(field, task)
        self.assertEqual(BOARD.task_health(task), "healthy")
        # State written before this rule: five live cards still carried blocked.
        state = self.read_state("demo")
        for n in range(5):
            state["tasks"][f"old{n}"] = {"title": f"Old {n}", "status": "done", "stage": "live",
                                         "evidence": "Measured", "health": "blocked", "executor": "Codex",
                                         "updated_at": iso(), "completed_at": iso()}
        state["tasks"]["merged"] = {"title": "Merged", "status": "done", "stage": "merged", "pr": 7,
                                    "pr_phase": "Merged", "health": "blocked", "executor": "Codex",
                                    "updated_at": iso()}
        self.write_state("demo", state)
        for n in range(5):
            self.assertEqual(BOARD.task_health(state["tasks"][f"old{n}"]), "healthy")
        self.run_board("render", "demo")
        after = self.read_state("demo")["tasks"]
        for task_id in [f"old{n}" for n in range(5)] + ["merged"]:
            self.assertNotIn("health", after[task_id], task_id)

    def test_stale_flag_after_six_hours_without_update(self):
        at = datetime(2026, 9, 29, 12, tzinfo=timezone.utc)
        running = {"status": "running", "updated_at": "2026-09-29T05:30:00+00:00"}
        self.assertTrue(BOARD.is_stale(running, at))
        self.assertEqual(BOARD.age_text(running["updated_at"], at), "6h 30m")
        self.assertFalse(BOARD.is_stale({**running, "updated_at": "2026-09-29T06:00:01+00:00"}, at))
        self.assertTrue(BOARD.is_stale({**running, "updated_at": "2026-09-29T06:00:00+00:00"}, at))
        self.assertTrue(BOARD.is_stale({"status": "review", "updated_at": "2026-09-28T12:00:00Z"}, at))
        self.assertEqual(BOARD.age_text("2026-09-28T10:00:00Z", at), "1d 2h")
        for status in ("done", "queued", "failed"):
            self.assertFalse(BOARD.is_stale({"status": status, "updated_at": "2026-09-20T00:00:00Z"}, at), status)
        self.assertFalse(BOARD.is_stale({"status": "running", "stage": "live", "evidence": "x",
                                         "updated_at": "2026-09-20T00:00:00Z"}, at))
        self.assertEqual(BOARD.STALE_AFTER, timedelta(hours=6))

    def test_ledger_flags_claude_plan_executor(self):
        self.run_board("init", "demo", "--title", "Demo")
        self.run_board("task", "demo", "violation", "--title", "Wrong seat", "--status", "queued",
                       "--executor", "claude-plan Opus subagent")
        ledger = {row["pool"]: row for row in BOARD.executor_ledger(self.read_state("demo")["tasks"])}
        self.assertTrue(ledger["claude-cloud"]["violation"])
        self.assertEqual(ledger["claude-cloud"]["label"], "Claude cloud credits")

    def test_executor_metadata_parser_handles_legacy_spellings(self):
        cases = {
            "gpt-6-sol high (Codex)": ("Codex", "gpt-6-sol", "high"),
            "codex gpt-6-sol high": ("Codex", "gpt-6-sol", "high"),
            "Codex gpt-6-sol high x2": ("Codex", "gpt-6-sol", "high"),
            "orchestrator": ("Anthropic", "Claude Opus 5.5", "unknown"),
            "Claude Opus 5.5 (orchestrator)": ("Anthropic", "Claude Opus 5.5", "unknown"),
            "grok 4.7 medium": ("xAI", "grok 4.7 medium", "medium"),
        }
        for executor, expected in cases.items():
            with self.subTest(executor=executor):
                self.assertEqual(BOARD.executor_metadata(executor), expected)

    def test_explicit_metadata_overrides_executor(self):
        self.run_board("init", "demo", "--title", "Demo")
        self.run_board("task", "demo", "a", "--title", "A", "--status", "queued",
                       "--executor", "orchestrator", "--provider", "Codex",
                       "--model", "gpt-6-sol", "--effort", "xhigh", "--summary", "Check the route.")
        task = self.read_state("demo")["tasks"]["a"]
        self.assertEqual((task["provider"], task["model"], task["effort"], task["summary"]),
                         ("Codex", "gpt-6-sol", "xhigh", "Check the route."))
        self.assertEqual(task["stage_history"][0]["stage"], "queued")

    def test_pr_summary_is_one_plain_line(self):
        body = ("<!-- template -->\n## Summary\n\n- **Show** the [board](https://x.example) on phones.\n"
                "Second line.\n")
        self.assertEqual(BOARD.pr_summary(body), "Show the board on phones.")
        self.assertEqual(BOARD.pr_summary(""), "")
        self.assertEqual(BOARD.pr_summary("## What changed\n\n| a | b |\n```\ncode\n```\nReal line here"),
                         "Real line here")
        long = BOARD.pr_summary("word " * 80)
        self.assertLessEqual(len(long), 160)
        self.assertTrue(long.endswith("…"))

    def test_legacy_live_without_evidence_and_done_without_pr_fail_closed(self):
        self.run_board("init", "demo", "--title", "Demo")
        self.run_board("task", "demo", "legacy", "--title", "Legacy", "--status", "done",
                       "--executor", "Codex", "--pr", "42")
        state = self.read_state("demo")
        state["tasks"]["legacy"]["stage"] = "measured"
        state["tasks"]["legacy"]["status"] = "measured"
        self.write_state("demo", state)
        self.run_board("task", "demo", "plain", "--title", "Plain", "--status", "done", "--executor", "Codex")
        tasks = self.read_state("demo")["tasks"]
        self.assertEqual(BOARD.task_stage(tasks["legacy"]), "build")
        self.assertEqual(BOARD.task_stage(tasks["plain"]), "build")

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

    def test_live_requires_explicit_evidence_and_merge_does_not_complete(self):
        self.run_board("init", "demo", "--title", "Demo")
        self.run_board("task", "demo", "a", "--title", "A", "--status", "running", "--executor", "Codex", "--pr", "42")
        missing = self.run_board("task", "demo", "a", "--stage", "live", check=False)
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("evidence", missing.stderr.lower())
        self.assertEqual(self.read_state("demo")["tasks"]["a"].get("stage"), None)
        self.run_board("task", "demo", "a", "--stage", "live", "--evidence", "Page returned expected data")
        task = self.read_state("demo")["tasks"]["a"]
        self.assertEqual(task["stage"], "live")
        self.assertEqual(task["evidence"], "Page returned expected data")
        self.assertTrue(task["completed_at"])

    def test_legacy_measured_maps_to_live(self):
        self.run_board("init", "demo", "--title", "Demo")
        self.run_board("task", "demo", "legacy", "--title", "Legacy", "--status", "done",
                       "--executor", "Codex", "--pr", "42", "--stage", "measured", "--evidence", "Legacy verified")
        self.assertEqual(self.read_state("demo")["tasks"]["legacy"]["stage"], "live")


class PullRequestStatus(BoardCase):
    VALID = {
        "state": "OPEN", "isDraft": False, "headRefOid": SHA_A,
        "author": {"login": "builder"},
        "statusCheckRollup": [{"conclusion": "SUCCESS", "status": "COMPLETED"}],
        "comments": [{"author": {"login": "reviewer"}, "authorAssociation": "COLLABORATOR",
                      "body": "APPROVE\nReviewed-SHA: " + SHA_A, "createdAt": "2026-09-28T10:00:00Z"}],
    }

    def start(self, **task_args):
        self.run_board("init", "demo", "--title", "Demo")
        self.run_board("task", "demo", "a", "--title", "A", "--status", "running",
                       "--executor", "Codex", "--pr", "42", *task_args.get("extra", []))

    def test_malformed_github_payload_keeps_previous_status(self):
        self.start()
        prior = self.read_state("demo")
        self.fake_gh({"view": self.VALID})
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
                    payload = copy.deepcopy(self.VALID)
                    parent = payload
                    for part in path[:-1]:
                        parent = parent[part]
                    if missing:
                        del parent[path[-1]]
                    else:
                        parent[path[-1]] = wrong_type
                    self.set_fixture({"view": payload})
                    self.run_board("render", "demo")
                    self.assertEqual(self.read_state("demo"), prior)
        self.set_fixture({"view": {**self.VALID, "mergeCommit": "not-an-object"}})
        self.run_board("render", "demo")
        self.assertEqual(self.read_state("demo"), prior)
        self.set_fixture({"view": []})
        self.run_board("render", "demo")
        self.assertEqual(self.read_state("demo"), prior)

    def test_review_readiness_requires_independent_trusted_latest_verdict(self):
        self.start()
        self.fake_gh({})
        base = {k: v for k, v in self.VALID.items() if k != "comments"}

        def comment(login, association, body, created):
            return {"author": {"login": login}, "authorAssociation": association, "body": body, "createdAt": created}
        approve = "APPROVE\nReviewed-SHA: " + SHA_A + "\n"
        cases = [
            ([comment("builder", "OWNER", approve, "2026-09-28T10:00:00Z")], "Awaiting review"),
            ([comment("outsider", "NONE", approve, "2026-09-28T10:00:00Z")], "Awaiting review"),
            ([comment("reviewer", "COLLABORATOR", approve, "2026-09-28T10:00:00Z")], "Ready to merge"),
            ([comment("reviewer", "COLLABORATOR", approve, "2026-09-28T10:00:00Z"),
              comment("reviewer", "COLLABORATOR", "BLOCK\nNeeds a fix", "2026-09-28T11:00:00Z")], "Review blocked"),
        ]
        for comments, expected in cases:
            with self.subTest(expected=expected):
                self.set_fixture({"view": {**base, "comments": comments}})
                self.run_board("render", "demo")
                self.assertEqual(self.read_state("demo")["tasks"]["a"]["pr_phase"], expected)
        self.assertEqual(self.read_state("demo")["tasks"]["a"]["review_verdict"], "BLOCK")

    def test_same_pr_number_in_two_repositories_and_legacy_state(self):
        self.run_board("init", "demo", "--title", "Demo")
        self.run_board("task", "demo", "carr", "--title", "CARR fix", "--status", "running", "--executor", "Codex", "--pr", "85")
        self.run_board("task", "demo", "app", "--title", "App fix", "--status", "running", "--executor", "Codex",
                       "--pr", "85", "--repo", "jbookout/doctorcre-app")
        state = self.read_state("demo")
        self.assertEqual(state["tasks"]["carr"]["repo"], "jbookout/carr-system")
        self.assertEqual(state["tasks"]["app"]["repo"], "jbookout/doctorcre-app")
        del state["tasks"]["carr"]["repo"]  # JSON written before --repo existed
        self.write_state("demo", state)
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
        self.assertEqual(BOARD.task_repo(state["tasks"]["carr"]), "jbookout/carr-system")

    def test_changing_pr_identity_offline_discards_previous_pr_status(self):
        self.start()
        self.fake_gh({"view": {**self.VALID, "state": "MERGED", "comments": []}})
        self.run_board("render", "demo")
        old = self.read_state("demo")["tasks"]["a"]
        self.assertEqual((old["status"], old["stage"], old["pr_phase"], old["pr_head"]),
                         ("done", "merged", "Merged", SHA_A))
        self.set_fixture({})
        self.run_board("task", "demo", "a", "--repo", "jbookout/doctorcre-app")
        moved = self.read_state("demo")["tasks"]["a"]
        self.assertEqual((moved["repo"], moved["pr"]), ("jbookout/doctorcre-app", 42))
        self.assertEqual((moved["status"], moved["stage"]), ("running", "build"))
        for field in ("pr_phase", "pr_checks", "pr_head", "evidence", "completed_at", "merge_sha"):
            self.assertNotIn(field, moved)

    def test_pr_derivation_and_offline_retention(self):
        self.start(extra=["--note", "Keep this note"])
        self.fake_gh({})
        base = {**self.VALID, "comments": []}

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
            ({"comments": [review("APPROVE\nReviewed-SHA: " + SHA_A + "\n")]}, "review", "review", "Ready to merge"),
        ]
        previous_update = self.read_state("demo")["tasks"]["a"]["updated_at"]
        for change, status, stage, phase in cases:
            with self.subTest(phase=phase):
                self.set_fixture({"view": {**base, **change}})
                self.run_board("render", "demo")
                task = self.read_state("demo")["tasks"]["a"]
                self.assertEqual((task["status"], task["stage"], task["pr_phase"]), (status, stage, phase))
                self.assertEqual(task["note"], "Keep this note")
                self.assertGreater(task["updated_at"], previous_update)
                previous_update = task["updated_at"]
                if status == "blocked":
                    self.assertEqual(BOARD.blocked_detail(task)[0], "CI checks are failing on the PR head")
        self.run_board("render", "demo")
        self.assertEqual(self.read_state("demo")["tasks"]["a"]["updated_at"], previous_update)
        prior = self.read_state("demo")
        self.set_fixture({})
        self.run_board("render", "demo")
        self.assertEqual(self.read_state("demo"), prior)
        self.set_fixture({"view": {**base, "state": "MERGED"}})
        self.run_board("render", "demo")
        self.assertEqual(self.read_state("demo")["tasks"]["a"]["stage"], "merged")
        self.run_board("task", "demo", "a", "--stage", "live", "--evidence", "Production response measured")
        completed_at = self.read_state("demo")["tasks"]["a"]["completed_at"]
        self.run_board("render", "demo")
        task = self.read_state("demo")["tasks"]["a"]
        self.assertEqual(task["stage"], "live")
        self.assertEqual(task["completed_at"], completed_at)

    def test_merged_card_goes_live_automatically_once_in_a_verified_release(self):
        self.start()
        merged = {**self.VALID, "state": "MERGED", "comments": [], "mergeCommit": {"oid": SHA_M}}
        self.fake_gh({"view": merged})
        self.run_board("render", "demo")
        task = self.read_state("demo")["tasks"]["a"]
        self.assertEqual((task["stage"], task["merge_sha"]), ("merged", SHA_M))
        # A release that does not contain the merge keeps it merged-not-live.
        self.shipped("worker", "9" * 40, ts="2026-09-29T10:00:00+00:00")
        older = f"repos/jbookout/carr-system/compare/{'9' * 40}...{SHA_M}"
        compare = f"repos/jbookout/carr-system/compare/{SHA_R}...{SHA_M}"
        self.set_fixture({"view": merged, "compare": {older: "ahead", compare: "behind"}})
        self.run_board("render", "demo")
        self.assertEqual(self.read_state("demo")["tasks"]["a"]["stage"], "merged")
        # The app lane's releases never count for a carr-system merge.
        self.shipped("app", SHA_M)
        self.run_board("render", "demo")
        self.assertEqual(self.read_state("demo")["tasks"]["a"]["stage"], "merged")
        # Once a verified worker release contains the merge commit, it is Live.
        self.shipped("worker", SHA_R, ts="2026-09-29T12:00:00+00:00")
        self.run_board("render", "demo")
        task = self.read_state("demo")["tasks"]["a"]
        self.assertEqual((task["status"], task["stage"]), ("done", "live"))
        self.assertIn(SHA_M[:12], task["evidence"])
        self.assertIn(SHA_R[:12], task["evidence"])
        self.assertIn("verified worker release", task["evidence"])
        self.assertTrue(task["completed_at"])
        self.assertEqual(task["stage_history"][-1]["stage"], "live")
        # The ancestry answer is cached; a later render does not ask again.
        calls = len([c for c in self.gh_calls() if c[0] == "api"])
        self.run_board("render", "demo")
        self.assertEqual(len([c for c in self.gh_calls() if c[0] == "api"]), calls)

    def test_exact_release_sha_needs_no_compare(self):
        self.start()
        merged = {**self.VALID, "state": "MERGED", "comments": [], "mergeCommit": {"oid": SHA_M}}
        self.fake_gh({"view": merged})
        self.shipped("worker", SHA_M)
        self.run_board("render", "demo")
        self.assertEqual(self.read_state("demo")["tasks"]["a"]["stage"], "live")
        self.assertEqual([c for c in self.gh_calls() if c[0] == "api"], [])

    def test_latest_verified_release_comes_from_shipped_rows_then_live_probe(self):
        releases = self.root / "releases.jsonl"
        releases.write_text("\n".join([
            json.dumps({"ts": "2026-09-28T00:00:00+00:00", "lane": "worker", "sha": "0" * 40, "status": "shipped"}),
            json.dumps({"ts": "2026-09-28T06:00:00+00:00", "lane": "worker", "sha": SHA_R, "status": "shipped"}),
            json.dumps({"ts": "2026-09-29T00:00:00+00:00", "lane": "worker", "sha": "3" * 40, "status": "blocked",
                        "reason": "canary_pending", "detail": "main canary has not finished on 333333333333"}),
            "not json",
        ]) + "\n")
        probes = {"https://api.doctorcre.com/release": {"git_sha": {"value": "6" * 40}},
                  "https://app.doctorcre.com/app-release": {"source_commit": "7" * 40, "environment": "production"}}
        with patch.dict(os.environ, {"PROGRESS_BOARD_RELEASES": str(releases)}):
            os.environ.pop("PROGRESS_BOARD_SKIP_PROBE", None)
            with patch.object(BOARD, "probe_json", lambda url: probes.get(url)):
                BOARD.RELEASE_CACHE.clear()
                carr = BOARD.latest_release("jbookout/carr-system")
                app = BOARD.latest_release("jbookout/doctorcre-app")
                wait = BOARD.release_wait_reason("jbookout/carr-system")
        BOARD.RELEASE_CACHE.clear()
        self.assertEqual((carr["sha"], carr["lane"]), (SHA_R, "worker"))
        self.assertEqual((app["sha"], app["source"]), ("7" * 40, "live probe"))
        self.assertEqual(wait, "release pipeline blocked (canary_pending): main canary has not finished on 333333333333")

    def test_merged_card_not_yet_released_shows_waiting_on_release_with_pipeline_reason(self):
        self.start()
        merged = {**self.VALID, "state": "MERGED", "comments": [], "mergeCommit": {"oid": SHA_M}}
        compare = f"repos/jbookout/carr-system/compare/{SHA_R}...{SHA_M}"
        self.fake_gh({"view": merged, "compare": {compare: "ahead"}})
        self.shipped("worker", SHA_R, ts="2026-09-29T08:00:00+00:00")
        with open(self.root / "releases.jsonl", "a") as fh:
            fh.write(json.dumps({"ts": "2026-09-29T09:00:00+00:00", "lane": "worker", "sha": "3" * 40,
                                 "status": "failed", "step": "canary", "detail": "canary pending"}) + "\n")
        self.run_board("render", "demo")
        task = self.read_state("demo")["tasks"]["a"]
        self.assertEqual(task["stage"], "merged")
        self.assertEqual(task["release_wait"], "release pipeline failed at canary: canary pending")
        # Once live, the wait note goes away.
        self.shipped("worker", SHA_M, ts="2026-09-29T11:00:00+00:00")
        self.run_board("render", "demo")
        task = self.read_state("demo")["tasks"]["a"]
        self.assertEqual(task["stage"], "live")
        self.assertNotIn("release_wait", task)

    def test_stage_changes_record_history_and_the_timer_reads_stage_entered_at(self):
        self.run_board("init", "demo", "--title", "Demo")
        self.run_board("task", "demo", "a", "--title", "A", "--status", "queued", "--executor", "Codex")
        first = self.read_state("demo")["tasks"]["a"]
        self.assertEqual([h["stage"] for h in first["stage_history"]], ["queued"])
        self.assertEqual(first["stage_entered_at"], first["stage_history"][-1]["entered_at"])
        self.run_board("task", "demo", "a", "--note", "same stage")
        self.assertEqual(len(self.read_state("demo")["tasks"]["a"]["stage_history"]), 1)
        self.run_board("task", "demo", "a", "--status", "running")
        task = self.read_state("demo")["tasks"]["a"]
        self.assertEqual([h["stage"] for h in task["stage_history"]], ["queued", "build"])
        self.assertEqual(task["stage_entered_at"], task["stage_history"][-1]["entered_at"])
        at = datetime(2026, 9, 29, 12, tzinfo=timezone.utc)
        timed = {**task, "stage_entered_at": "2026-09-29T09:46:00+00:00", "updated_at": "2026-09-29T11:59:00+00:00"}
        self.assertEqual(BOARD.stage_timer(timed, at), "build 2h 14m")
        self.assertEqual(BOARD.stage_timer({"status": "review", "stage_entered_at": "2026-09-29T11:25:00Z"}, at),
                         "review 35m")
        legacy = {"status": "review", "updated_at": "2026-09-29T11:59:00Z",
                  "stage_history": [{"stage": "review", "at": "2026-09-29T11:25:00Z"}]}
        self.assertEqual(BOARD.stage_timer(legacy, at), "review 35m")
        durations = BOARD.stage_durations({"stage_history": [
            {"stage": "queued", "entered_at": "2026-09-29T10:00:00Z"},
            {"stage": "build", "entered_at": "2026-09-29T10:30:00Z"}]}, at)
        self.assertEqual(durations, [("queued", "2026-09-29T10:00:00Z", "30m"), ("build", "2026-09-29T10:30:00Z", "1h 30m")])

    def test_render_backfills_missing_stage_timestamps_from_last_update(self):
        self.run_board("init", "demo", "--title", "Demo")
        state = self.read_state("demo")
        state["tasks"]["legacy"] = {"title": "Legacy", "status": "running", "executor": "Codex",
                                    "updated_at": "2026-09-29T07:00:00+00:00"}
        state["tasks"]["partial"] = {"title": "Partial", "status": "running", "executor": "Codex",
                                     "updated_at": "2026-09-29T07:00:00+00:00",
                                     "stage_history": [{"stage": "build", "status": "running", "at": "2026-09-29T06:00:00+00:00"}]}
        self.write_state("demo", state)
        self.run_board("render", "demo")
        tasks = self.read_state("demo")["tasks"]
        self.assertEqual(tasks["legacy"]["stage_entered_at"], "2026-09-29T07:00:00+00:00")
        self.assertEqual(tasks["legacy"]["stage_history"], [{"stage": "build", "entered_at": "2026-09-29T07:00:00+00:00"}])
        self.assertEqual(tasks["partial"]["stage_history"][0]["entered_at"], "2026-09-29T06:00:00+00:00")
        self.assertEqual(tasks["partial"]["stage_entered_at"], "2026-09-29T06:00:00+00:00")
        self.assertEqual(tasks["legacy"]["updated_at"], "2026-09-29T07:00:00+00:00")


class AllRepositoriesBoard(BoardCase):
    def pr(self, number, **fields):
        base = {"number": number, "title": f"PR {number}", "body": f"## Summary\nDoes thing {number}.\n",
                "author": {"login": "jbookout"}, "headRefName": f"claude/branch-{number}",
                "headRefOid": f"{number:040d}", "isDraft": False, "createdAt": iso(timedelta(hours=2)),
                "updatedAt": iso(timedelta(minutes=5)), "mergeable": "MERGEABLE", "reviewDecision": "",
                "statusCheckRollup": [{"conclusion": "SUCCESS", "status": "COMPLETED"}],
                "url": f"https://github.com/jbookout/x/pull/{number}"}
        base.update(fields)
        return base

    def fixture_all(self):
        carr = "jbookout/carr-system"
        app = "jbookout/doctorcre-app"
        return {
            "repos": ["jbookout/carr-system", "jbookout/tour-lab"],
            "open": {
                carr: [
                    self.pr(1, isDraft=True, headRefName="codex/draft-work"),
                    self.pr(2, statusCheckRollup=[{"conclusion": "FAILURE", "status": "COMPLETED"}]),
                    self.pr(3, mergeable="CONFLICTING"),
                    self.pr(4, reviewDecision="CHANGES_REQUESTED"),
                    self.pr(5, statusCheckRollup=[{"conclusion": None, "status": "IN_PROGRESS"}]),
                    self.pr(6, updatedAt=iso(timedelta(hours=7))),
                ],
                app: [self.pr(1, title="App one", body="Fixes the phone layout.\n\nMore.")],
            },
            "merged": {
                carr: [self.pr(7, mergedAt=iso(timedelta(hours=1)), mergeCommit={"oid": SHA_M}),
                       self.pr(8, mergedAt=iso(timedelta(hours=1)), mergeCommit={"oid": "8" * 40})],
            },
            "compare": {f"repos/jbookout/carr-system/compare/{SHA_M}...{'8' * 40}": "ahead"},
        }

    def test_builds_every_repository_from_gh_grouped_with_stages(self):
        self.fake_gh(self.fixture_all())
        self.shipped("worker", SHA_M)
        self.run_board("render", "all-repos")
        state = self.read_state("all-repos")
        self.assertEqual(state["kind"], "all-repos")
        self.assertEqual(state["project"], "all-repos")
        repos = [row["repo"] for row in state["repos"]]
        self.assertEqual(repos[:3], ["jbookout/carr-system", "jbookout/doctorcre-app", "jbookout/software-factory"])
        self.assertIn("jbookout/tour-lab", repos)
        tasks = state["tasks"]
        expect = {
            "carr-system-1": ("build", "running", "Draft", False),
            "carr-system-2": ("ci", "blocked", "Checks failing", True),
            "carr-system-3": ("review", "blocked", "Merge conflict", True),
            "carr-system-4": ("review", "blocked", "Changes requested", True),
            "carr-system-5": ("ci", "running", "CI", False),
            "carr-system-6": ("review", "review", "Awaiting review", False),
            "carr-system-7": ("live", "done", "Merged", False),
            "carr-system-8": ("merged", "done", "Merged", False),
            "doctorcre-app-1": ("review", "review", "Awaiting review", False),
        }
        self.assertEqual(set(tasks), set(expect))
        for task_id, (stage, status, phase, blocked) in expect.items():
            with self.subTest(task_id=task_id):
                task = tasks[task_id]
                self.assertEqual(BOARD.task_stage(task), stage)
                self.assertEqual((task["status"], task["pr_phase"]), (status, phase))
                self.assertEqual(BOARD.task_health(task) == "blocked", blocked)
                if blocked:
                    reason, action = BOARD.blocked_detail(task)
                    self.assertTrue(reason and action)
        app = tasks["doctorcre-app-1"]
        self.assertEqual((app["repo"], app["pr"], app["title"]), ("jbookout/doctorcre-app", 1, "App one"))
        self.assertEqual(app["summary"], "Fixes the phone layout.")
        self.assertEqual(app["author"], "jbookout")
        self.assertEqual(app["executor"], "Claude cloud")
        self.assertEqual(app["pr_head"], f"{1:040d}")
        self.assertTrue(app["created_at"])
        self.assertEqual(tasks["carr-system-1"]["executor"], "Codex")
        self.assertEqual(tasks["carr-system-2"]["summary"], "Does thing 2.")
        self.assertTrue(BOARD.is_stale(tasks["carr-system-6"]))
        self.assertFalse(BOARD.is_stale(tasks["carr-system-5"]))
        self.assertIn(SHA_M[:12], tasks["carr-system-7"]["evidence"])
        counts = {row["repo"]: (row["open"], row["merged"]) for row in state["repos"]}
        self.assertEqual(counts["jbookout/carr-system"], (6, 2))
        self.assertEqual(counts["jbookout/doctorcre-app"], (1, 0))
        snapshot = BOARD.board_snapshot(state)
        self.assertEqual(snapshot["kind"], "all-repos")
        self.assertEqual(snapshot["repos"], state["repos"])
        merged_call = [c for c in self.gh_calls() if c[:2] == ["pr", "list"] and "merged" in c]
        self.assertTrue(merged_call and any(arg.startswith("merged:>=") for arg in merged_call[0]))

    def test_same_pr_number_in_two_repositories_never_collides(self):
        fixture = self.fixture_all()
        fixture["open"] = {"jbookout/carr-system": [self.pr(93, title="CARR 93")],
                           "jbookout/doctorcre-app": [self.pr(93, title="App 93")]}
        fixture["merged"] = {}
        self.fake_gh(fixture)
        self.run_board("render", "all-repos")
        tasks = self.read_state("all-repos")["tasks"]
        self.assertEqual(tasks["carr-system-93"]["title"], "CARR 93")
        self.assertEqual(tasks["doctorcre-app-93"]["title"], "App 93")
        self.assertEqual({(t["repo"], t["pr"]) for t in tasks.values()},
                         {("jbookout/carr-system", 93), ("jbookout/doctorcre-app", 93)})
        self.assertEqual(BOARD.card_key("jbookout/doctorcre-app", 93), "doctorcre-app-93")
        self.assertNotEqual(BOARD.card_key("jbookout/doctorcre-app", 93), BOARD.card_key("jbookout/carr-system", 93))

    def test_one_repository_failing_keeps_its_previous_cards(self):
        fixture = self.fixture_all()
        self.fake_gh(fixture)
        self.run_board("render", "all-repos")
        before = {k: v for k, v in self.read_state("all-repos")["tasks"].items() if k.startswith("doctorcre-app-")}
        self.set_fixture({**fixture, "fail": ["jbookout/doctorcre-app"], "open": {"jbookout/carr-system": []}})
        self.run_board("render", "all-repos")
        state = self.read_state("all-repos")
        after = {k: v for k, v in state["tasks"].items() if k.startswith("doctorcre-app-")}
        self.assertEqual(after, before)
        row = next(r for r in state["repos"] if r["repo"] == "jbookout/doctorcre-app")
        self.assertIn("error", row)
        self.assertNotIn("carr-system-1", state["tasks"])

    def test_without_gh_the_board_is_not_rebuilt_or_wiped(self):
        fixture = self.fixture_all()
        self.fake_gh(fixture)
        self.run_board("render", "all-repos")
        prior = self.read_state("all-repos")
        self.set_fixture({**fixture, "repo_list_fails": True,
                          "fail": list(fixture["open"]) + ["jbookout/software-factory", "jbookout/tour-lab"]})
        result = self.run_board("render", "all-repos", check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.read_state("all-repos"), prior)

    def test_snapshot_stays_under_the_server_limit(self):
        tasks = {f"carr-system-{n}": {"title": "t" * 200, "summary": "s" * 160, "status": "done", "stage": "live",
                                      "evidence": "e" * 200, "repo": "jbookout/carr-system", "pr": n,
                                      "completed_at": f"2026-09-{1 + n % 28:02d}T00:00:00Z"} for n in range(600)}
        tasks["carr-system-open"] = {"title": "Open", "status": "running", "stage": "build", "repo": "jbookout/carr-system", "pr": 9999}
        trimmed = BOARD.fit_snapshot_tasks(tasks)
        self.assertIn("carr-system-open", trimmed)
        self.assertLess(len(json.dumps(trimmed)), BOARD.SNAPSHOT_BUDGET)
        self.assertLess(len(trimmed), len(tasks))

    def test_render_publish_builds_and_publishes_the_system_board(self):
        events = []
        args = type("Args", (), {"project": "all-repos", "publish": True})()
        with patch.object(BOARD, "build_all_repos", lambda: events.append(("build", "all-repos"))), \
             patch.object(BOARD, "render", lambda project: events.append(("render", project))), \
             patch.object(BOARD, "publish_board", lambda project: events.append(("publish", project))):
            BOARD.command_render(args)
        self.assertEqual(events, [("build", "all-repos"), ("publish", "all-repos")])


class PublishAndAnswers(BoardCase):
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
        self.assertEqual(calls[1][1]["snapshot"]["schema"], "carr-progress-board.v2")
        self.assertEqual(calls[2][1]["asker_ref"], "one-shot:session-1")
        self.assertNotIn("answer", calls[1][1]["snapshot"])

    def test_legacy_question_revision_retains_free_text_mode(self):
        self.run_board("init", "demo", "--title", "Demo")
        self.run_board("ask", "demo", "q1", "--question", "Original?", "--default", "Proceed")
        state = self.read_state("demo")
        legacy = state["questions"]["q1"]
        for field in ("choices", "free_text", "asker_ref", "revision", "history"):
            legacy.pop(field)
        self.write_state("demo", state)
        self.run_board("ask", "demo", "q1", "--question", "Revised?", "--default", "Proceed")
        old = self.read_state("demo")["questions"]["q1"]["history"][0]
        fields = BOARD.question_revision(old, "demo")
        self.assertEqual(fields["choices"], [])
        self.assertIs(fields["allow_free_text"], True)
        self.assertEqual(fields["asker_ref"], "orchestrator:demo")

    def test_publish_normalizes_question_and_default_on_repeat(self):
        self.run_board("init", "demo", "--title", "Demo")
        self.run_board("ask", "demo", "q1", "--question", "  Choose route?  ", "--default", "  Proceed  ")
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

    def test_existing_launchd_render_runs_publish_poll_and_system_board_without_a_model(self):
        events = []
        args = type("Args", (), {"project": "carr-v5", "publish": False})()
        with patch.object(BOARD, "render", lambda project: events.append(("render", project))), \
             patch.object(BOARD, "publish_board", lambda project: events.append(("publish", project))), \
             patch.object(BOARD, "poll_board_answers", lambda project: events.append(("poll", project))), \
             patch.object(BOARD, "build_all_repos", lambda: events.append(("build", "all-repos"))):
            BOARD.command_render(args)
        self.assertEqual(events, [("render", "carr-v5"), ("publish", "carr-v5"), ("poll", "carr-v5"),
                                  ("build", "all-repos"), ("publish", "all-repos")])

    def test_system_board_failure_does_not_stop_the_project_board(self):
        events = []
        args = type("Args", (), {"project": "carr-v5", "publish": False})()

        def broken():
            raise RuntimeError("gh unavailable")
        with patch.object(BOARD, "render", lambda project: events.append(("render", project))), \
             patch.object(BOARD, "publish_board", lambda project: events.append(("publish", project))), \
             patch.object(BOARD, "poll_board_answers", lambda project: events.append(("poll", project))), \
             patch.object(BOARD, "build_all_repos", broken):
            with self.assertRaisesRegex(RuntimeError, "gh unavailable"):
                BOARD.command_render(args)
        self.assertEqual(events, [("render", "carr-v5"), ("publish", "carr-v5"), ("poll", "carr-v5")])


if __name__ == "__main__":
    unittest.main(verbosity=2)
