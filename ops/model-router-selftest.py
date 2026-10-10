#!/usr/bin/env python3
"""Deterministic budget, independence, telemetry and learning contracts."""
import copy
import importlib.util
import json
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib import model_router as router

spec = importlib.util.spec_from_file_location("nightly", ROOT / "ops/model-router-nightly.py")
assert spec is not None and spec.loader is not None
nightly = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nightly)

NOW = 1800000000


def window(used=20, hours=24, duration=168):
    return {"used_percent": used, "resets_at": NOW + hours * 3600,
            "window_minutes": duration * 60}


def usage(codex=20, claude=20, codex_hours=24, claude_hours=24):
    return {"codex": {"observed_at": NOW, "source": "fixture", "windows": {"weekly": window(codex, codex_hours)}},
            "claude": {"observed_at": NOW, "source": "fixture", "windows": {
                "weekly": window(claude, claude_hours), "five_hour": window(20, 1, 5)}}}


class RouterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.budget = pathlib.Path(self.tmp.name)
        self.fit = router.load_json(ROOT / "ops/config/model-fit.v1.json")

    def route(self, kind="non-trivial build", **kwargs):
        return router.route(kind, now=NOW, budget_dir=self.budget,
                            fit=self.fit, usage=kwargs.pop("usage", usage()), **kwargs)

    def test_cutoff_and_reserve(self):
        self.assertEqual(self.route(usage=usage(codex=90))["pool"], "claude")
        self.assertEqual(self.route(usage=usage(claude=75))["pool"], "codex")
        self.assertEqual(self.route(usage=usage(claude=80), reserve_pct=0)["pool"], "codex")
        with self.assertRaises(router.RouteUnavailable):
            self.route(usage=usage(codex=90, claude=90))
        rows = [json.loads(s) for s in (self.budget / "router.jsonl").read_text().splitlines()]
        self.assertEqual(rows[-1]["status"], "blocked")
        self.assertIn("cutoff", rows[-1]["reason"])

    def test_reserve_changes_decision(self):
        u = usage(codex=40, claude=70, claude_hours=4)
        self.assertEqual(self.route(usage=u)["pool"], "codex")
        self.assertEqual(self.route(usage=u, reserve_pct=0)["pool"], "claude")

    def test_spend_expiring_pool(self):
        self.assertEqual(self.route(usage=usage(codex_hours=2))["pool"], "codex")
        self.assertEqual(self.route(usage=usage(claude_hours=2))["pool"], "claude")

    def test_tie_uses_sooner_reset(self):
        for entry in self.fit["models"].values():
            entry["fit"]["non-trivial build"] = 1
        u = usage(codex=50, claude=50, codex_hours=12, claude_hours=6)
        u["claude"]["windows"].pop("five_hour")
        self.assertEqual(self.route(usage=u)["pool"], "claude")

    def test_alternation_persists_and_fix_keeps_builder(self):
        builder = self.route(pr_id="carr-system:branch", usage=usage(codex_hours=2))
        reviewer = self.route("review", pr_id="carr-system:branch", usage=usage(codex_hours=2))
        fixer = self.route("review-fix", pr_id="carr-system:branch", usage=usage(claude_hours=2))
        self.assertEqual(builder["pool"], "codex")
        self.assertEqual(reviewer["pool"], "claude")
        self.assertEqual(fixer["pool"], "codex")
        with self.assertRaises(router.RouteUnavailable):
            self.route("review", pr_id="carr-system:branch", usage=usage(claude=90))

    def test_pr_build_cannot_choose_a_third_family(self):
        u = usage()
        u["grok"] = {"observed_at": NOW, "source": "fixture", "windows": {"weekly": window(0, .1)}}
        self.assertIn(self.route("mechanical build", pr_id="pr", usage=u)["pool"], ("codex", "claude"))

    def test_reverse_alternation_and_missing_context(self):
        builder = self.route(pr_id="reverse", usage=usage(claude_hours=2))
        self.assertEqual(builder["pool"], "claude")
        self.assertEqual(self.route("review", pr_id="reverse")["pool"], "codex")
        with self.assertRaises(router.RouteUnavailable):
            self.route("review", pr_id="unknown")
        with self.assertRaises(router.RouteUnavailable):
            self.route("review", pr_id="reverse", builder_model="gpt-6.1-sol")

    def test_stale_future_and_expired_usage(self):
        for observed in (NOW - 1801, NOW + 1):
            u = usage(); u["claude"]["observed_at"] = observed
            self.assertEqual(self.route(usage=u)["pool"], "codex")
        u = usage(); u["claude"]["windows"]["weekly"]["resets_at"] = NOW
        self.assertEqual(self.route(usage=u)["pool"], "codex")

    def test_floor_reuse_and_mechanical_models_excluded(self):
        self.assertEqual(self.route("x-reply-run-daily", floor="opus")["model"], "opus-5.5")
        for kind in ("review", "research", "non-trivial build"):
            result = self.route(kind, builder_model="opus-5.5" if kind == "review" else None)
            self.assertEqual(result["model"], "gpt-6.1-sol")
            self.assertEqual(result["effort"], "high")

    def test_latest_codex_observation_across_files(self):
        d = self.budget / "sessions"; d.mkdir()
        for name, timestamp, used in (("a", NOW - 10, 5), ("b", NOW, 91)):
            (d / f"{name}.jsonl").write_text(json.dumps({"timestamp": timestamp,
                "payload": {"rate_limits": {"primary": window(used)}}}) + "\n{partial")
        self.assertEqual(router.read_codex_usage(d)["windows"]["primary"]["used_percent"], 91)

    def test_macbook_is_same_pool_and_does_not_sum_allowances(self):
        local = usage()["codex"]
        remote = copy.deepcopy(local)
        remote.update(observed_at=NOW + 1)
        remote["windows"]["weekly"]["used_percent"] = 88
        response = type("Response", (), {"returncode": 0, "stdout": json.dumps(remote)})()
        with patch.object(router, "read_codex_usage", return_value=local), patch.object(router.subprocess, "run", return_value=response):
            snapshots, sources = router.collect_usage(self.budget)
        self.assertEqual(snapshots["codex"]["windows"]["weekly"]["used_percent"], 88)
        self.assertEqual(snapshots["codex"]["source"], "codex-macbook")
        self.assertEqual(sources["macbook"], "observed")

    def test_cli_sanitizes_claude_statusline(self):
        import subprocess
        payload = {"rate_limits": {"five_hour": {"used_percentage": 12, "resets_at": NOW + 3600},
                                  "seven_day": {"used_percentage": 40, "resets_at": NOW + 86400}},
                   "unrelated": "do not persist"}
        result = subprocess.run([sys.executable, str(ROOT / "ops/model-router.py"), "--budget-dir", str(self.budget),
                                 "capture-claude"], input=json.dumps(payload), text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("unrelated", (self.budget / "claude-usage.json").read_text())

    def test_config_and_log_reason_bind_selected_score(self):
        picked = self.route()
        audit = json.loads((self.budget / "router.jsonl").read_text().splitlines()[-1])
        self.assertEqual(audit["result"], picked)
        selected = next(c for c in audit["candidates"] if c["model"] == picked["model"])
        self.assertAlmostEqual(selected["score"], selected["fit"] * selected["headroom"])
        for model in self.fit["models"].values():
            self.assertTrue(model["seed_source"])
            self.assertEqual(set(model["fit"]), set(self.fit["task_routes"]))

    def test_claude_payload_and_manual_fallback(self):
        raw = {"rate_limits": {"five_hour": {"used_percentage": 12, "resets_at": NOW + 3600},
                               "seven_day": {"used_percentage": 40, "resets_at": NOW + 86400}}}
        snap = router.claude_snapshot(raw, NOW)
        self.assertEqual(snap["weekly_pct"], 40)
        self.assertEqual(router.claude_usage(snap)["windows"]["weekly"]["used_percent"], 40)
        with self.assertRaises(ValueError):
            router.claude_snapshot({"rate_limits": {}}, NOW)

    def test_short_window_limits_weekly_headroom(self):
        u = usage(codex_hours=2, claude_hours=2)
        u["codex"]["windows"]["short"] = window(90, 1, 5)
        self.assertEqual(self.route(usage=u)["pool"], "claude")

    def test_zero_fit_stays_ineligible(self):
        u = usage(); u["codex"]["windows"]["weekly"]["used_percent"] = 95
        u["claude"]["windows"]["weekly"]["used_percent"] = 95
        u["grok"] = {"observed_at": NOW, "source": "fixture", "windows": {"weekly": window()}}
        with self.assertRaises(router.RouteUnavailable):
            self.route("research", usage=u)


class LearningTests(unittest.TestCase):
    def setUp(self):
        self.fit = router.load_json(ROOT / "ops/config/model-fit.v1.json")

    def outcome(self, n, **metrics):
        return {"id": str(n), "model": "gpt-6.1-sol", "task_kind": "non-trivial build", **metrics}

    def test_minimum_sample_bounded_update_dedup_and_no_replay(self):
        rows = [self.outcome(i, first_pass_approve=True, ci_first_push=True, rounds_to_approve=1, no_progress=False) for i in range(5)]
        candidate = router.learn(self.fit, rows[:4], day="2026-10-05")
        self.assertEqual(candidate["models"], self.fit["models"])
        candidate = router.learn(self.fit, rows + rows, day="2026-10-05")
        before = self.fit["models"]["gpt-6.1-sol"]["fit"]["non-trivial build"]
        after = candidate["models"]["gpt-6.1-sol"]["fit"]["non-trivial build"]
        self.assertGreater(after, before)
        self.assertLessEqual(after - before, .05 + 1e-9)
        self.assertEqual(router.learn(candidate, rows, day="2026-10-06")["models"], candidate["models"])

    def test_bad_outcomes_lower_fit_but_keep_tier_and_score_floors(self):
        rows = [self.outcome(i, first_pass_approve=False, ci_first_push=False, rounds_to_approve=5, no_progress=True) for i in range(5)]
        candidate = router.learn(self.fit, rows, day="2026-10-05")
        entry = candidate["models"]["gpt-6.1-sol"]
        self.assertEqual(entry["tier"], "opus")
        self.assertGreaterEqual(entry["fit"]["non-trivial build"], entry["min_fit"]["non-trivial build"])
        self.assertLess(entry["fit"]["non-trivial build"], self.fit["models"]["gpt-6.1-sol"]["fit"]["non-trivial build"])

    def test_unattributed_log_is_never_training_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = pathlib.Path(tmp) / "loop-1.log"
            p.write_text("ROUND 1 APPROVE\nCI green\n")
            self.assertIsNone(router.log_outcome(p, {}))
            p.write_text("ROUND 1 BLOCKED\nROUND 2 APPROVE\nCI red\nCI green\nNO-PROGRESS\n")
            row = router.log_outcome(p, {"model": "gpt-6.1-sol", "task_kind": "review-fix"})
            self.assertFalse(row["first_pass_approve"])
            self.assertFalse(row["ci_first_push"])
            self.assertEqual(row["rounds_to_approve"], 2)
            self.assertTrue(row["no_progress"])

    def test_equal_logs_are_distinct_jobs_and_append_is_not_a_new_sample(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows = []
            for i in range(5):
                path = pathlib.Path(tmp) / f"loop-{i}.log"
                path.write_text("APPROVE\nCI green\n")
                rows.append(router.log_outcome(path, {"model": "gpt-6.1-sol", "task_kind": "non-trivial build"}))
            self.assertEqual(len({row["id"] for row in rows}), 5)
            candidate = router.learn(self.fit, rows, day="2026-10-05")
            self.assertEqual(len(candidate["learning_changes"]), 1)
            path.write_text("APPROVE\nCI green\nadditional evidence\n")
            updated = router.log_outcome(path, {"model": "gpt-6.1-sol", "task_kind": "non-trivial build"})
            self.assertEqual(updated["id"], rows[-1]["id"])

    def test_partial_loop_waits_for_terminal_outcome(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "loop-1.log"
            path.write_text("ROUND 1 BLOCKED\nCI red\n")
            self.assertIsNone(router.log_outcome(path, {"model": "gpt-6.1-sol", "task_kind": "non-trivial build"}))

    def test_nightly_writes_dated_revision_consumed_by_router_and_no_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            rows = [self.outcome(i, first_pass_approve=True, ci_first_push=True) for i in range(5)]
            source = root / "outcomes.jsonl"
            source.write_text("".join(json.dumps(row) + "\n" for row in rows))
            report = nightly.run(root, source, day="2026-10-05")
            self.assertTrue((root / "budget" / report["revision_file"]).exists())
            self.assertEqual(router.current_fit(root / "budget")["revision"], "learned-2026-10-05")
            self.assertEqual(nightly.run(root, source, day="2026-10-06")["changes"], [])

    def test_canonical_attempts_are_read_before_learning(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = pathlib.Path(tmp) / "outcomes.jsonl"
            source.write_text(json.dumps({**self.outcome(1, ci_first_push=True), "attempt_id": "attempt-one"}) + "\n")
            read = []
            def reader(attempt):
                read.append(attempt)
                return {"reliability": {"state": "insufficient_evidence"}}
            rows, skipped = nightly.outcomes_from(tmp, source, reader)
            self.assertEqual(read, ["attempt-one"])
            self.assertEqual(rows[0]["id"], "attempt:attempt-one")
            self.assertEqual(skipped, [])

    def test_floor_never_relaxed_by_outcomes(self):
        rows = [{"id": str(i), "model": "gpt-5.6-luna", "task_kind": "review", "first_pass_approve": True} for i in range(10)]
        self.assertEqual(router.learn(self.fit, rows, day="2026-10-05")["models"], self.fit["models"])


if __name__ == "__main__":
    unittest.main()
