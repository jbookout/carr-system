#!/usr/bin/env python3
"""REST-only PR refresh, terminal retention and recorded GraphQL parity."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("board", Path(__file__).with_name("progress_board.py"))
assert SPEC is not None and SPEC.loader is not None
B = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(B)
REPO = "jbookout/carr-system"
SHA = "a" * 40


def pull(number=1, **extra):
    return {"number": number, "state": "open", "draft": False, "head": {"sha": SHA, "ref": "fix"},
            "user": {"login": "builder"}, "merge_commit_sha": None, "merged_at": None,
            "mergeable": True, "mergeable_state": "clean", "title": "Fix", "body": "Fix it.",
            "html_url": f"https://github.com/{REPO}/pull/{number}", "changed_files": 1,
            "created_at": "2026-10-01T00:00:00Z", "updated_at": "2026-10-04T00:00:00Z", **extra}


class RestRefresh(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"PROGRESS_BOARD_ROOT": self.tmp.name,
                                         "PROGRESS_BOARD_SKIP_PROBE": "1"})
        self.env.start()
        os.environ.pop("PROGRESS_BOARD_SKIP_GH", None)
        self.calls = []

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def github(self, args, timeout=30):
        self.calls.append(args)
        self.assertEqual(args[0], "api", "every GitHub read must use REST")
        path = args[1].split("?", 1)[0]
        if path.endswith("/check-runs"):
            return {"check_runs": [{"name": "CI", "status": "completed", "conclusion": "success"}]}
        if path.endswith("/statuses") or path.endswith("/comments") or path.endswith("/reviews"):
            return []
        if path.endswith("/files"):
            return [{"filename": "mcp-server/src/index.js"}]
        if "/pulls/" in path:
            return pull(int(path.rsplit("/", 1)[1]))
        raise AssertionError(path)

    def board(self, name, tasks):
        B.write_json({"project": name, "tasks": tasks})

    def test_two_open_twenty_merged_never_query_terminal_or_graphql(self):
        tasks = {str(n): {"pr": n, "repo": REPO, "status": "done", "stage": "merged",
                         "pr_phase": "Merged", "merge_sha": f"{n:040x}", "pr_head": SHA}
                 for n in range(3, 23)}
        tasks.update({str(n): {"pr": n, "repo": REPO, "status": "running"} for n in (1, 2)})
        self.board("demo", tasks)
        with patch.object(B, "gh_json", self.github), patch.object(B, "gh_binary", return_value="gh"):
            B.render("demo")
        details = [c[1] for c in self.calls if "/pulls/" in c[1] and c[1].split("?")[0].rsplit("/", 1)[1].isdigit()]
        self.assertEqual(details, [f"repos/{REPO}/pulls/1", f"repos/{REPO}/pulls/2"])
        self.assertFalse(any("graphql" in str(c).lower() for c in self.calls))

    def test_one_hydration_across_boards_and_persisted_unchanged_version(self):
        for name in ("one", "two"):
            self.board(name, {"a": {"pr": 1, "repo": REPO, "status": "running"}})
        with patch.object(B, "gh_json", self.github), patch.object(B, "gh_binary", return_value="gh"):
            with B.github_read_pass():
                B.render("one")
                B.render("two")
            self.assertEqual(len(self.calls), 5)
            self.calls.clear()
            B.render("one")
            self.assertEqual(self.calls, [["api", f"repos/{REPO}/pulls/1"]])

    def test_read_failure_keeps_last_state_and_marks_snapshot_stale(self):
        self.board("demo", {"a": {"pr": 1, "repo": REPO, "status": "running"}})
        with patch.object(B, "gh_json", self.github):
            B.render("demo")
        before = B.read_state("demo")["tasks"]
        with patch.object(B, "gh_json", side_effect=RuntimeError("REST rate limited")):
            B.render("demo")
        snapshot = B.board_snapshot(B.read_state("demo"))
        self.assertEqual(snapshot["tasks"]["a"]["status"], before["a"]["status"])
        self.assertEqual(B.read_state("demo")["tasks"], before)
        self.assertEqual(snapshot["github_sync"]["failed"][0]["card"], "a")
        self.assertIn("REST rate limited", snapshot["github_sync"]["failed"][0]["error"])

    def test_terminal_observation_survives_restart_and_never_refetches(self):
        for state in ("MERGED", "CLOSED"):
            with self.subTest(state=state):
                raw = pull(40 if state == "MERGED" else 41, state="closed",
                           merged_at="2026-10-04T00:00:00Z" if state == "MERGED" else None,
                           merge_commit_sha="b" * 40 if state == "MERGED" else None)
                def github(args, timeout=30):
                    return raw if args[1].endswith(str(raw["number"])) else self.github(args, timeout)
                with patch.object(B, "gh_json", github):
                    result, error = B.fetch_pr(raw["number"], REPO)
                self.assertIsNone(error)
                self.assertEqual(result["state"], state)
                with patch.object(B, "gh_json", side_effect=AssertionError("terminal refetched")):
                    again, error = B.fetch_pr(raw["number"], REPO)
                self.assertEqual(again, result)
                self.assertIsNone(error)

    def test_all_repos_discovery_uses_rest_and_keeps_terminal_snapshots(self):
        def github(args, timeout=30):
            self.assertEqual(args[0], "api")
            path = args[1]
            if path.startswith("user/repos?"):
                return [{"full_name": REPO, "archived": False}]
            if "/pulls?" in path:
                return [pull()] if REPO in path else []
            if "/issues?" in path:
                return []
            return self.github(args, timeout)
        with patch.object(B, "gh_json", github):
            snapshot = B.build_all_repos()
        self.assertEqual(snapshot["tasks"]["carr-system-1"]["pr_head"], SHA)

    def test_recorded_gh_pr_view_parity_on_the_same_live_head_and_version(self):
        root = Path(__file__).with_name("fixtures") / "progress-board-rest"
        recorded = json.loads((root / "gh-pr-view-open.json").read_text())
        rest = json.loads((root / "rest-open.json").read_text())
        self.assertEqual(recorded["headRefOid"], rest["pull"]["head"]["sha"])
        self.assertEqual(recorded["updatedAt"], rest["pull"]["updated_at"])
        def github(args, timeout=30):
            self.assertEqual(args[0], "api")
            path = args[1].split("?")[0]
            endpoint = path.rsplit("/", 1)[1]
            return rest["pull" if endpoint == "1510" else endpoint]
        with patch.object(B, "gh_json", github):
            observed, error = B.fetch_pr(1510, REPO)
        self.assertIsNone(error)
        for field in ("isDraft", "headRefOid", "headRefName", "updatedAt", "mergeable"):
            self.assertEqual(observed[field], recorded[field], field)
        def rendered_checks(pr):
            return sorted((c.get("name") or c.get("context") or "", c.get("status"),
                           c.get("conclusion") or c.get("state")) for c in pr["statusCheckRollup"])
        self.assertEqual(rendered_checks(observed), rendered_checks(recorded))
        self.assertEqual(B.checks_summary(observed), B.checks_summary(recorded))
        def rendered_comments(pr):
            return [(c["author"]["login"], c["authorAssociation"], c["body"], c["createdAt"]) for c in pr["comments"]]
        self.assertEqual(rendered_comments(observed), rendered_comments(recorded))
        self.assertEqual(B.review_verdict(observed), B.review_verdict(recorded))

    def test_cancelled_pending_legacy_statuses_reviews_and_mergeability_parity(self):
        expected = {"state": "OPEN", "isDraft": False, "headRefOid": SHA,
                    "author": {"login": "builder"}, "mergeCommit": None, "mergeable": "CONFLICTING",
                    "reviewDecision": "CHANGES_REQUESTED", "comments": [],
                    "statusCheckRollup": [{"name": "cancel", "status": "COMPLETED", "conclusion": "CANCELLED"},
                                          {"name": "wait", "status": "QUEUED", "conclusion": None},
                                          {"context": "legacy", "state": "PENDING"}]}
        def github(args, timeout=30):
            endpoint = args[1].split("?")[0].rsplit("/", 1)[1]
            if endpoint == "1":
                return pull(mergeable=False)
            if endpoint == "check-runs":
                return {"check_runs": [{"name": "cancel", "status": "completed", "conclusion": "cancelled"},
                                       {"name": "wait", "status": "queued", "conclusion": None}]}
            if endpoint == "statuses":
                return [{"context": "legacy", "state": "pending"}, {"context": "legacy", "state": "success"}]
            if endpoint == "reviews":
                return [{"id": 1, "user": {"login": "reviewer"}, "author_association": "COLLABORATOR", "state": "CHANGES_REQUESTED"},
                        {"id": 2, "user": {"login": "reviewer"}, "author_association": "COLLABORATOR", "state": "COMMENTED"}]
            return self.github(args, timeout)
        with patch.object(B, "gh_json", github):
            observed, error = B.fetch_pr(1, REPO)
        self.assertIsNone(error)
        for field in ("state", "isDraft", "headRefOid", "author", "mergeCommit", "mergeable", "reviewDecision"):
            self.assertEqual(observed[field], expected[field], field)
        self.assertEqual(B.checks_summary(observed), "0 pass · 2 pending · 1 fail")
        self.assertEqual(B.derived_pr_state(observed), B.derived_pr_state(expected))

    def test_pending_checks_complete_without_a_pr_updated_at_change(self):
        pending = True
        def github(args, timeout=30):
            if "/check-runs?" in args[1]:
                return {"check_runs": [{"name": "CI", "status": "in_progress" if pending else "completed",
                                        "conclusion": None if pending else "success"}]}
            return self.github(args, timeout)
        with patch.object(B, "gh_json", github):
            first, error = B.fetch_pr(1, REPO)
            self.assertIsNone(error)
            self.assertEqual(B.checks_summary(first), "0 pass · 1 pending · 0 fail")
            pending = False
            second, error = B.fetch_pr(1, REPO)
            self.assertIsNone(error)
            self.assertEqual(first["updatedAt"], second["updatedAt"])
            self.assertEqual(B.checks_summary(second), "1 pass · 0 pending · 0 fail")

    def test_all_repository_reads_failing_render_previous_cards_marked_stale(self):
        previous = {"a": {"pr": 1, "repo": REPO, "status": "review", "stage": "review"}}
        self.board("all-repos", previous)
        with patch.object(B, "gh_json", side_effect=RuntimeError("REST unavailable")):
            state = B.build_all_repos()
        self.assertEqual(state["tasks"], previous)
        self.assertTrue(state["github_sync"]["stale"])
        self.assertEqual(len(state["github_sync"]["failed"]), len(B.CORE_REPOS))

    def test_formal_outsider_review_cannot_make_a_pr_approved_or_blocked(self):
        for number, state in enumerate(("APPROVED", "CHANGES_REQUESTED"), 1):
            def github(args, timeout=30):
                if "/reviews?" in args[1]:
                    return [{"id": 1, "user": {"login": "outsider"}, "author_association": "NONE", "state": state}]
                return self.github(args, timeout)
            with self.subTest(state=state), patch.object(B, "gh_json", github):
                observed, error = B.fetch_pr(number, REPO)
                self.assertIsNone(error)
                self.assertEqual(observed["reviewDecision"], "")
                self.assertEqual(B.derived_pr_state(observed), ("review", "review", "Awaiting review"))


if __name__ == "__main__":
    unittest.main()
