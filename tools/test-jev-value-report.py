"""Pins tools/jev-value-report.py: Jev cost against outcomes it can prove.

The report must never count an unlinked verdict as value, never invent a
price the repo does not carry, and never bill a call the vendor refused.
"""
import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("jev_value_report", HERE / "jev-value-report.py")
assert spec is not None and spec.loader is not None
jvr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(jvr)

START = datetime(2026, 9, 27, tzinfo=timezone.utc)
END = datetime(2026, 10, 4, tzinfo=timezone.utc)
PRICE = {"price_usd_per_million_input_tokens": 0.042}


def call(caller, ts="2026-10-01T00:00:00Z", **extra):
    row = {"ts": ts, "caller": caller, "cache_hit": False, "usable": True, "ok": True,
           "input_tokens": 1_000_000, "output_tokens": 100_000}
    row.update(extra)
    return row


def judge(kind, at="2026-10-01T00:00:00+00:00", **extra):
    row = {"at": at, "kind": kind, "answers": {"q": {"noul": 0.5}}, "elapsed_ms": 300,
           "usage": {"input_tokens": 1000, "output_tokens": 10}, "subject_ref": None}
    row.update(extra)
    return row


def ci_run(branch, minutes, conclusion="success", started="2026-10-01T00:00:00Z", name="CI",
           event="pull_request"):
    start = datetime.fromisoformat(started.replace("Z", "+00:00"))
    end = start.timestamp() + minutes * 60
    return {"name": name, "event": event, "conclusion": conclusion, "head_branch": branch,
            "run_started_at": started,
            "updated_at": datetime.fromtimestamp(end, timezone.utc).isoformat().replace("+00:00", "Z")}


def sources(**over):
    base = {"calls": [], "judge": [], "gate": [], "commits": [], "ci": None, "price": PRICE}
    base.update(over)
    return base


class CostTests(unittest.TestCase):
    def test_vendor_refusals_and_cache_hits_are_not_billed(self):
        rows = [call("jev_code_review"),
                call("jev_code_review", cache_hit=True),
                call("jev_code_review", usable=False, ok=False, error="HTTP 402",
                     input_tokens=None, output_tokens=None),
                call("jev_code_review", usable=False, ok=False, error="daily_paid_call_cap",
                     input_tokens=None, output_tokens=None),
                call("jev_code_review", usable=False, ok=False, error="vendor_failed_at_worker",
                     input_tokens=None, output_tokens=None)]
        site = jvr.build_report(sources(calls=rows), START, END)["sites"]["review"]
        self.assertEqual(site["attempts"], 4)
        self.assertEqual(site["not_billed"], 2)
        self.assertEqual(site["paid"], 2)
        self.assertEqual(site["usable"], 1)
        self.assertEqual(site["unmetered_paid"], 1)
        self.assertEqual(site["input_tokens"], 1_000_000)
        self.assertAlmostEqual(site["usd_input"], 0.042)

    def test_window_excludes_rows_outside_it(self):
        rows = [call("jev_code_review", ts="2026-09-20T00:00:00Z"), call("jev_code_review")]
        site = jvr.build_report(sources(calls=rows), START, END)["sites"]["review"]
        self.assertEqual(site["paid"], 1)

    def test_legacy_rows_without_caller_use_ok_and_usage(self):
        rows = [{"ts": "2026-10-01T00:00:00Z", "ok": True,
                 "usage": {"input_tokens": 500, "output_tokens": 5}}]
        site = jvr.build_report(sources(calls=rows), START, END)["sites"]["legacy_unattributed"]
        self.assertEqual((site["paid"], site["usable"], site["input_tokens"]), (1, 1, 500))

    def test_one_off_callers_group_into_review_or_ad_hoc(self):
        rows = [call("pr1465-full-review"), call("jev_code_review"), call("w5_builder")]
        report = jvr.build_report(sources(calls=rows), START, END)
        self.assertEqual(report["sites"]["review"]["paid"], 2)
        self.assertEqual(report["sites"]["ad_hoc_named"]["paid"], 1)

    def test_judge_hub_is_reconciled_not_double_counted(self):
        report = jvr.build_report(sources(calls=[call("jev_judge")], judge=[judge("build_advisory")]),
                                  START, END)
        self.assertEqual(report["totals"]["input_tokens"], 1_000_000)
        self.assertNotIn("jev_judge", report["sites"])
        self.assertEqual(report["judge_hub"]["calls_log_input_tokens"], 1_000_000)
        self.assertEqual(report["judge_hub"]["judge_log_input_tokens"], 1000)
        residual = report["sites"]["judge:unattributed"]
        self.assertEqual((residual["paid"], residual["usable"], residual["input_tokens"]), (0, 0, 999_000))
        self.assertEqual(sum(s["input_tokens"] for s in report["sites"].values()),
                         report["totals"]["input_tokens"])

    def test_output_price_is_reported_unknown_never_invented(self):
        report = jvr.build_report(sources(calls=[call("jev_code_review")]), START, END)
        self.assertIsNone(report["price"]["usd_per_million_output"])
        self.assertIn("ops/config/jev-cost-guard.v1.json", report["price"]["output_source"])
        text = jvr.render(report)
        self.assertIn("output-token price: UNKNOWN", text)

    def test_missing_price_config_leaves_dollars_unknown(self):
        report = jvr.build_report(sources(calls=[call("jev_code_review")], price=None), START, END)
        self.assertIsNone(report["sites"]["review"]["usd_input"])
        self.assertIn("dollars: UNKNOWN", jvr.render(report))

    def test_judge_hub_splits_by_kind_and_sums_wait_time(self):
        rows = [judge("supervise.stuck_and_drift"), judge("supervise.stuck_and_drift"),
                judge("supervise.stuck_and_drift", answers=None,
                      error="TypeSafeError: TypeSafe returned HTTP 402: billing", usage=None),
                judge("supervise.stuck_and_drift", answers=None, error="build_advisory:timeout",
                      usage=None)]
        report = jvr.build_report(sources(judge=rows), START, END)
        site = report["sites"]["judge:supervise.stuck_and_drift"]
        self.assertEqual((site["paid"], site["usable"], site["not_billed"]), (3, 2, 1))
        self.assertEqual(site["input_tokens"], 2000)
        self.assertEqual(site["wait_ms"], 1200)


    def test_residual_carries_usable_calls(self):
        report = jvr.build_report(sources(calls=[call("jev_judge"), call("jev_judge")],
                                          judge=[judge("build_advisory")]), START, END)
        residual = report["sites"]["judge:unattributed"]
        self.assertEqual((residual["paid"], residual["usable"]), (1, 1))

    def test_residual_usable_never_exceeds_paid(self):
        judges = [judge("build_advisory", answers=None, error="build_advisory:timeout", usage=None)]
        report = jvr.build_report(sources(calls=[call("jev_judge")], judge=judges), START, END)
        residual = report["sites"]["judge:unattributed"]
        self.assertEqual((residual["paid"], residual["usable"]), (0, 0))

    def test_judge_rows_before_the_hub_named_itself_are_already_in_legacy(self):
        calls = [{"ts": "2026-09-28T00:00:00Z", "ok": True, "usage": {"input_tokens": 700}},
                 call("jev_judge", ts="2026-10-01T00:00:00Z")]
        judges = [judge("build_advisory", at="2026-09-28T00:00:00+00:00"),
                  judge("build_advisory", at="2026-10-02T00:00:00+00:00")]
        report = jvr.build_report(sources(calls=calls, judge=judges), START, END)
        self.assertEqual(report["sites"]["judge:build_advisory"]["paid"], 1)
        self.assertEqual(report["judge_hub"]["pre_hub_judge_rows"], 1)
        self.assertEqual(report["sites"]["judge:build_advisory"]["wait_ms"], 600)
        self.assertEqual(sum(s["input_tokens"] for s in report["sites"].values()),
                         report["totals"]["input_tokens"])


class ValueTests(unittest.TestCase):
    def test_commit_naming_jev_as_finder_is_a_verified_outcome(self):
        commits = [{"sha": "965c6991", "date": "2026-10-01T00:00:00Z",
                    "body": "Jev flagged a real bug in the re-raise condition"},
                   {"sha": "aaaa", "date": "2026-10-01T00:00:00Z",
                    "body": "Also fixes the gap Jev's semantic_creation flagged in round 1"},
                   {"sha": "bbbb", "date": "2026-10-01T00:00:00Z",
                    "body": "Stopgap for the Jev spend Joe flagged."},
                   {"sha": "cccc", "date": "2026-10-01T00:00:00Z",
                    "body": "relabelled it, which CI's jev-build-advisory selftest caught."}]
        report = jvr.build_report(sources(commits=commits), START, END)
        review = report["sites"]["review"]
        self.assertEqual(review["outcomes_verified"], 2)
        self.assertEqual(sorted(e["ref"] for e in review["evidence"]), ["965c6991", "aaaa"])

    def test_required_actions_gate_blocks_are_self_referential_not_value(self):
        gate = [{"ts": "2026-10-01T00:00:00Z", "status": "required", "missing": ["x"]},
                {"ts": "2026-10-01T00:00:00Z", "status": "required", "missing": []}]
        report = jvr.build_report(sources(gate=gate), START, END)
        site = report["sites"]["gate:jev_required_actions"]
        self.assertEqual(site["blocks"], 1)
        self.assertEqual(site["outcomes_verified"], 0)
        self.assertIn("forces a Jev call", site["note"])

    def test_unlinked_site_prints_insufficient_evidence_and_no_value(self):
        report = jvr.build_report(sources(judge=[judge("supervise.failure_triage")]), START, END)
        site = report["sites"]["judge:supervise.failure_triage"]
        self.assertEqual(site["outcomes_verified"], 0)
        self.assertIsNone(site["minutes_saved"])
        text = jvr.render(report)
        self.assertIn("judge:supervise.failure_triage: insufficient evidence", text)

    def test_value_range_uses_measured_ci_data(self):
        ci = {"runs": [ci_run("a", 10), ci_run("a", 20, "failure"), ci_run("a", 30),
                       ci_run("b", 20), ci_run("b", 20, "cancelled")], "pulls": []}
        commits = [{"sha": "f1", "date": "2026-10-01T00:00:00Z", "body": "Jev caught a bug"}]
        report = jvr.build_report(sources(commits=commits, ci=ci), START, END)
        self.assertEqual(report["baseline"]["ci_round_minutes_median"], 20)
        self.assertEqual(report["baseline"]["ci_rounds_per_pr_median"], 2)
        review = report["sites"]["review"]
        self.assertEqual(review["minutes_saved"], (20, 40))

    def test_no_ci_snapshot_leaves_value_unknown_with_source(self):
        commits = [{"sha": "f1", "date": "2026-10-01T00:00:00Z", "body": "Jev caught a bug"}]
        report = jvr.build_report(sources(commits=commits), START, END)
        self.assertIsNone(report["sites"]["review"]["minutes_saved"])
        self.assertIn("--fetch-ci", jvr.render(report))


class RecommendationTests(unittest.TestCase):
    def rec(self, site_key, **sources_over):
        return jvr.build_report(sources(**sources_over), START, END)["sites"][site_key]["recommendation"]

    def test_mechanical_site_without_outcomes_is_replace_with_code(self):
        self.assertEqual(self.rec("judge:supervise.stuck_and_drift",
                                  judge=[judge("supervise.stuck_and_drift")]), "replace with code")

    def test_per_turn_judgment_without_outcomes_moves_to_judgment_point(self):
        self.assertEqual(self.rec("judge:build_advisory", judge=[judge("build_advisory")]),
                         "move to a judgment point")

    def test_fixture_traffic_on_live_budget_is_remove(self):
        self.assertEqual(self.rec("judge:supervise.effort_picker",
                                  judge=[judge("supervise.effort_picker")]), "remove")

    def test_verified_site_is_keep(self):
        commits = [{"sha": "f1", "date": "2026-10-01T00:00:00Z", "body": "Jev caught a bug"}]
        self.assertEqual(self.rec("review", commits=commits), "keep")


class BaselineTests(unittest.TestCase):
    def test_sample_size_for_holdout_uses_measured_spread(self):
        ci = {"runs": [ci_run(f"b{i}", 10) for i in range(4)]
                      + [ci_run("b0", 10), ci_run("b1", 10), ci_run("b1", 10)], "pulls": []}
        base = jvr.build_report(sources(ci=ci), START, END)["baseline"]
        # rounds per PR: 2, 3, 1, 1 -> mean 1.75, sample sd 0.957
        self.assertAlmostEqual(base["ci_rounds_per_pr_mean"], 1.75)
        n = base["holdout_n_per_arm"]
        expected = 2 * (1.959964 + 0.841621) ** 2 * (0.9574271 ** 2) / ((0.25 * 1.75) ** 2)
        self.assertEqual(n, int(expected) + 1)

    def test_reverts_and_canary_failures_count_as_escaped_defects(self):
        commits = [{"sha": "r1", "date": "2026-10-01T00:00:00Z", "subject": 'Revert "x"', "body": ""}]
        ci = {"runs": [ci_run("main", 5, "failure", name="Main canary", event="schedule")], "pulls": []}
        base = jvr.build_report(sources(commits=commits, ci=ci), START, END)["baseline"]
        self.assertEqual((base["reverts"], base["main_canary_failures"]), (1, 1))


class FetchTests(unittest.TestCase):
    def test_runs_are_fetched_one_day_at_a_time_under_the_1000_result_cap(self):
        calls = []

        def fake_gh(args):
            calls.append(args)
            return ""

        jvr.fetch_ci(Path(tempfile.mkdtemp()), START, START + jvr.timedelta(days=3), gh=fake_gh)
        days = [a[3].split("created=")[1] for a in calls if "actions/runs" in a[3]]
        self.assertEqual(days, ["2026-09-27", "2026-09-28", "2026-09-29"])


class CliTests(unittest.TestCase):
    def test_main_reads_out_dir_and_prints_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            out.mkdir()
            (out / "jev-calls.jsonl").write_text(json.dumps(call("jev_code_review")) + "\nnot json\n")
            (out / "jev-judge.jsonl").write_text("")
            price = Path(tmp) / "price.json"
            price.write_text(json.dumps(PRICE))
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = jvr.main(["--root", tmp, "--since", "2026-09-27", "--until", "2026-10-04",
                               "--price-config", str(price), "--no-git"])
            self.assertEqual(rc, 0)
            self.assertIn("review", buf.getvalue())
            self.assertIn("1 unreadable line", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
