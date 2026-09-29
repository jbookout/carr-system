#!/usr/bin/env python3
"""Selftest for ops/check-eval-receipt.py, the LLM-steering eval-receipt gate.

Written before the check. Half of these cases MUST FAIL: a receipt whose gain
sits inside the noise and still says "ship", a critical dimension that regressed
while the headline rose, a grader nobody graded twice, a test split that was
not sealed, a no-eval line that gives no reason. A gate that only ever passes
its fixtures proves nothing about what it refuses.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ops"))
from git_env import fixture_env  # noqa: E402

SPEC = importlib.util.spec_from_file_location("check_eval_receipt", ROOT / "ops" / "check-eval-receipt.py")
assert SPEC and SPEC.loader
cer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cer)

REGISTRY = cer.load_registry(ROOT / "evals" / "surfaces.json")
SURFACE_IDS = {s["id"] for s in REGISTRY["surfaces"]}


def score(s, lo, hi):
    return {"score": s, "ci_low": lo, "ci_high": hi}


def dimension(dim_id, *, critical, base, cand, delta, direction, status="passed"):
    return {
        "dimension_id": dim_id,
        "critical": critical,
        "status": status,
        "direction_vs_baseline": direction,
        "evidence_refs": [f"evals/jev-judgments/runs/v3/{dim_id}.jsonl"],
        "baseline": score(*base),
        "candidate": score(*cand),
        "delta": {"value": delta[0], "ci_low": delta[1], "ci_high": delta[2]},
    }


def good_receipt():
    """A clear, attributable win on the primary dimension, nothing critical lost."""
    return {
        "schema_version": 1,
        "surface": "jev-judgments",
        "change": "State the refusal condition once, before the examples, in the done-claim question family.",
        "measured_on": "2026-09-29",
        "rung": "hill_climb",
        "adapter": {
            "surface": "claude_code_cli",
            "adapter_id": "adapter:claude-api-hillclimb",
            "adapter_version": "2.1.284",
            "harness_id": "harness:ops-jev-judge",
            "harness_version": "c1fb71b",
            "provider_id": "provider:anthropic",
            "model_id": "model:claude-sonnet-5-5",
            "native_session_ref": "session:hillclimb-jev-v3",
            "configuration_fingerprint": "sha256:" + "a" * 64,
        },
        "cases": {"total": 80, "train": 40, "test": 40, "should_not_fire": 16,
                  "sources": ["production_trace", "human_judged_hard_case"]},
        "split": {"method": "random, stratified by first tag", "seed": 11, "sealed_test": True},
        "repeats": 3,
        "grader": {"kind": "programmatic",
                   "validation": {"graded_twice": True, "agreement": 0.98, "oracle_pass_rate": 1.0,
                                  "null_pass_rate": 0.0, "result": "pass"}},
        "noise_floor": 0.06,
        "min_useful_gain": 0.10,
        "primary_dimension": "correct-judgment",
        "dimensions": [
            dimension("correct-judgment", critical=True, base=(0.62, 0.55, 0.69), cand=(0.81, 0.75, 0.87),
                      delta=(0.19, 0.12, 0.26), direction="improved"),
            dimension("should-not-fire-precision", critical=True, base=(0.94, 0.89, 0.98), cand=(0.95, 0.90, 0.99),
                      delta=(0.01, -0.03, 0.05), direction="equivalent"),
        ],
        "stage_results": [
            {"stage_id": "intake", "status": "passed", "dimension_ids": ["should-not-fire-precision"],
             "evidence_refs": ["evals/jev-judgments/runs/v3/intake.jsonl"]},
            {"stage_id": "judgment", "status": "passed", "dimension_ids": ["correct-judgment"],
             "evidence_refs": ["evals/jev-judgments/runs/v3/judgment.jsonl"]},
        ],
        "cost": {"baseline_usd_per_case": 0.0041, "candidate_usd_per_case": 0.0043},
        "verdict": {"decision": "ship", "statement": "Test-split judgment accuracy rose 0.62 to 0.81, outside noise."},
    }


def in_noise_receipt():
    r = good_receipt()
    r["dimensions"][0] = dimension("correct-judgment", critical=True, base=(0.62, 0.55, 0.69),
                                   cand=(0.65, 0.58, 0.72), delta=(0.03, -0.03, 0.09), direction="equivalent")
    return r


class Registry(unittest.TestCase):
    def test_real_registry_names_every_family_the_task_listed(self):
        must_cover = [
            "ops/jev_judge.py", "ops/typesafe_client.py", "mcp-server/src/jev-call-receipt.js",
            "ops/rule_trigger_delivery.py", "lib/rule_delivery_preuse.py",
            "hooks/session-brief.py", "hooks/rule-pack-preuse-reselection.py",
            "CLAUDE.md", "AGENTS.md", ".claude/skills/any/SKILL.md", "claude-tree/skills/council/SKILL.md",
            "pipelines/run_codex_review.py", "ops/config/model-routes.v1.json",
        ]
        for path in must_cover:
            self.assertTrue(cer.surfaces_for(path, REGISTRY), f"{path} is not registered")

    def test_tests_and_fixtures_are_not_surfaces(self):
        for path in ["ops/jev-judge-selftest.py", "tools/test-flash-prompt-rules.py",
                     "ops/fixtures/rule-delivery-eval/cases.v2.json", "hooks/__pycache__/x.pyc",
                     "evals/README.md", "evals/surfaces.json", "evals/jev-judgments/receipt.json"]:
            self.assertEqual(cer.surfaces_for(path, REGISTRY), [], path)

    def test_every_literal_path_exists_and_every_surface_matches_something(self):
        tracked = subprocess.run(["git", "-C", str(ROOT), "ls-files"], capture_output=True, text=True).stdout.split()
        for surface in REGISTRY["surfaces"]:
            for glob in surface["globs"]:
                if not any(ch in glob for ch in "*?["):
                    self.assertTrue((ROOT / glob).exists(), f"{surface['id']}: {glob} does not exist")
            self.assertTrue(any(surface["id"] in cer.surfaces_for(p, REGISTRY) for p in tracked),
                            f"{surface['id']} matches no tracked file")

    def test_every_context_emitting_hook_is_registered(self):
        self.assertEqual(cer.unregistered_context_hooks(ROOT, REGISTRY), [])

    def test_a_new_context_hook_is_caught(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "hooks").mkdir()
            (Path(tmp) / "hooks" / "new-nudge.py").write_text('print({"additionalContext": "x"})\n')
            self.assertEqual(cer.unregistered_context_hooks(Path(tmp), REGISTRY), ["hooks/new-nudge.py"])

    def test_malformed_registries_fail(self):
        base = json.loads((ROOT / "evals" / "surfaces.json").read_text())
        for mutate, why in [
            (lambda d: d["surfaces"].append(copy.deepcopy(d["surfaces"][0])), "duplicate"),
            (lambda d: d["surfaces"][0].update(id="Bad Id"), "id"),
            (lambda d: d["surfaces"][0].update(globs=[]), "globs"),
        ]:
            d = copy.deepcopy(base)
            mutate(d)
            with self.assertRaisesRegex(cer.GateError, why):
                cer.validate_registry(d)


class Globs(unittest.TestCase):
    def test_double_star_crosses_directories_single_star_does_not(self):
        self.assertTrue(cer.glob_match("a/b/c/CLAUDE.md", "**/CLAUDE.md"))
        self.assertTrue(cer.glob_match("CLAUDE.md", "**/CLAUDE.md"))
        self.assertTrue(cer.glob_match(".claude/skills/x/y/SKILL.md", ".claude/skills/**"))
        self.assertTrue(cer.glob_match("ops/jev_judge.py", "ops/jev_*.py"))
        self.assertFalse(cer.glob_match("ops/sub/jev_judge.py", "ops/jev_*.py"))
        self.assertFalse(cer.glob_match("ops/jev-judge-selftest.py", "ops/jev_*.py"))


class Receipts(unittest.TestCase):
    def errors(self, receipt, surface="jev-judgments"):
        return cer.validate_receipt(receipt, surface, ROOT)

    def test_clear_win_passes(self):
        self.assertEqual(self.errors(good_receipt()), [])

    def test_in_noise_gain_that_says_ship_fails(self):
        errs = self.errors(in_noise_receipt())
        self.assertTrue(any("do not merge on quality grounds" in e for e in errs), errs)

    def test_in_noise_gain_that_says_do_not_merge_passes(self):
        r = in_noise_receipt()
        r["verdict"] = {"decision": "do_not_merge",
                        "statement": "Gain is inside the noise: do not merge on quality grounds."}
        self.assertEqual(self.errors(r), [])

    def test_cost_at_parity_is_allowed_only_when_cheaper(self):
        r = in_noise_receipt()
        r["verdict"] = {"decision": "ship_cost_at_parity",
                        "statement": "Quality within noise, do not merge on quality grounds; cheaper per case."}
        r["cost"] = {"baseline_usd_per_case": 0.0041, "candidate_usd_per_case": 0.0020}
        self.assertEqual(self.errors(r), [])
        r["cost"] = {"baseline_usd_per_case": 0.0041, "candidate_usd_per_case": 0.0050}
        self.assertTrue(any("cheaper" in e for e in self.errors(r)))

    def test_critical_regression_blocks_even_when_the_overall_score_rises(self):
        r = good_receipt()
        r["dimensions"][1] = dimension("should-not-fire-precision", critical=True, base=(0.94, 0.89, 0.98),
                                       cand=(0.80, 0.74, 0.86), delta=(-0.14, -0.20, -0.08), direction="regressed")
        r["overall"] = {"baseline": 0.70, "candidate": 0.82}
        errs = self.errors(r)
        self.assertTrue(any("should-not-fire-precision" in e and "blocking" in e for e in errs), errs)
        r["verdict"] = {"decision": "do_not_merge",
                        "statement": "Blocked: critical dimension should-not-fire-precision regressed."}
        self.assertEqual(self.errors(r), [])

    def test_a_regression_cannot_be_declared_equivalent(self):
        r = good_receipt()
        r["dimensions"][1] = dimension("should-not-fire-precision", critical=False, base=(0.94, 0.89, 0.98),
                                       cand=(0.80, 0.74, 0.86), delta=(-0.14, -0.20, -0.08), direction="equivalent")
        self.assertTrue(any("direction" in e for e in self.errors(r)))

    def test_noncritical_regression_is_reported_but_does_not_block(self):
        r = good_receipt()
        r["dimensions"].append(dimension("brevity", critical=False, base=(0.70, 0.64, 0.76),
                                         cand=(0.60, 0.54, 0.66), delta=(-0.10, -0.16, -0.04), direction="regressed"))
        r["stage_results"][1]["dimension_ids"].append("brevity")
        self.assertEqual(self.errors(r), [])

    def test_failed_critical_dimension_status_blocks(self):
        r = good_receipt()
        r["dimensions"][1]["status"] = "failed"
        self.assertTrue(any("blocking" in e for e in self.errors(r)))

    def test_primary_regression_must_say_do_not_merge(self):
        r = good_receipt()
        r["dimensions"][0] = dimension("correct-judgment", critical=False, base=(0.62, 0.55, 0.69),
                                       cand=(0.50, 0.43, 0.57), delta=(-0.12, -0.18, -0.06), direction="regressed")
        self.assertTrue(self.errors(r))

    def test_every_dimension_is_bound_to_a_user_job_stage(self):
        r = good_receipt()
        r["stage_results"] = r["stage_results"][1:]
        self.assertTrue(any("stage" in e for e in self.errors(r)))

    def test_stage_naming_an_unknown_dimension_fails(self):
        r = good_receipt()
        r["stage_results"][0]["dimension_ids"].append("ghost")
        self.assertTrue(any("ghost" in e for e in self.errors(r)))

    def test_no_dimensions_means_a_blended_score_and_fails(self):
        r = good_receipt()
        r["dimensions"] = []
        self.assertTrue(self.errors(r))

    def test_primary_dimension_must_exist(self):
        r = good_receipt()
        r["primary_dimension"] = "overall"
        self.assertTrue(any("primary_dimension" in e for e in self.errors(r)))

    def test_model_harness_and_adapter_are_required(self):
        for field in ["model_id", "harness_id", "adapter_id", "harness_version", "configuration_fingerprint"]:
            r = good_receipt()
            del r["adapter"][field]
            self.assertTrue(self.errors(r), field)
        r = good_receipt()
        r["adapter"]["model_id"] = " "
        self.assertTrue(self.errors(r))

    def test_rung_uses_the_kernel_ladder(self):
        r = good_receipt()
        r["rung"] = "vibes"
        self.assertTrue(any("rung" in e for e in self.errors(r)))
        r["rung"] = "launch"
        self.assertEqual(self.errors(r), [])

    def test_required_fields_each_fail_when_missing(self):
        for field in ["cases", "split", "repeats", "grader", "noise_floor", "min_useful_gain",
                      "dimensions", "stage_results", "cost", "verdict", "change", "adapter", "rung"]:
            r = good_receipt()
            del r[field]
            self.assertTrue(self.errors(r), f"missing {field} was accepted")

    def test_split_arithmetic_and_sealing(self):
        r = good_receipt(); r["cases"]["train"] = 41
        self.assertTrue(self.errors(r))
        r = good_receipt(); r["split"]["sealed_test"] = False
        self.assertTrue(any("sealed" in e for e in self.errors(r)))
        r = good_receipt(); r["cases"]["test"] = 0; r["cases"]["train"] = 80
        self.assertTrue(self.errors(r))

    def test_should_not_fire_and_real_sources_are_required(self):
        r = good_receipt(); r["cases"]["should_not_fire"] = 0
        self.assertTrue(any("should_not_fire" in e for e in self.errors(r)))
        r = good_receipt(); r["cases"]["should_not_fire"] = 99
        self.assertTrue(any("should_not_fire" in e and "total" in e for e in self.errors(r)))
        r = good_receipt(); r["cases"]["should_not_fire"] = r["cases"]["total"]
        self.assertEqual(self.errors(r), [])
        r = good_receipt(); r["cases"]["sources"] = ["synthesized"]
        self.assertTrue(any("production" in e for e in self.errors(r)))

    def test_grader_must_be_graded_twice_and_pass_validation(self):
        r = good_receipt(); r["grader"]["validation"]["graded_twice"] = False
        self.assertTrue(any("twice" in e for e in self.errors(r)))
        r = good_receipt(); r["grader"]["validation"]["result"] = "fail"
        self.assertTrue(any("grader" in e for e in self.errors(r)))
        r = good_receipt(); r["grader"]["kind"] = "vibes"
        self.assertTrue(self.errors(r))

    def test_noise_must_be_smaller_than_the_smallest_useful_gain_to_ship(self):
        r = good_receipt(); r["noise_floor"] = 0.12
        self.assertTrue(any("noise" in e for e in self.errors(r)))

    def test_score_outside_its_own_interval_fails(self):
        r = good_receipt(); r["dimensions"][0]["candidate"] = score(0.81, 0.83, 0.87)
        self.assertTrue(self.errors(r))

    def test_delta_must_match_the_scores(self):
        r = good_receipt(); r["dimensions"][0]["delta"]["value"] = 0.40
        self.assertTrue(any("delta" in e for e in self.errors(r)))

    def test_surface_mismatch_fails(self):
        self.assertTrue(any("surface" in e for e in self.errors(good_receipt(), "rule-delivery")))

    def test_repeats_must_be_positive(self):
        r = good_receipt(); r["repeats"] = 0
        self.assertTrue(self.errors(r))

    def test_offline_suite_binds_to_the_ai_eval_kernel_digest(self):
        suite_path = "evals/ai/model-boundary.v1.json"
        digest = cer.ai_eval.load_suite(ROOT / suite_path)["_digest"]
        r = good_receipt(); r["offline_suite"] = {"path": suite_path, "suite_digest": digest}
        self.assertEqual(self.errors(r), [])
        r["offline_suite"]["suite_digest"] = "0" * 64
        self.assertTrue(any("digest" in e for e in self.errors(r)))


class NoEvalLines(unittest.TestCase):
    REASON = ("the only consumer is Joe's local Flash seat and no transcript of it is retained, "
              "so there is nothing to replay")

    def test_valid_line_is_parsed(self):
        body = f"Summary\n\nno-eval: rule-delivery: {self.REASON}\n"
        found, errs = cer.parse_no_eval(body, SURFACE_IDS)
        self.assertEqual(errs, [])
        self.assertIn("rule-delivery", found)

    def test_unreasoned_lines_are_rejected(self):
        for reason in ["n/a", "trivial", "docs only", "not needed here", "tbd"]:
            found, errs = cer.parse_no_eval(f"no-eval: rule-delivery: {reason}", SURFACE_IDS)
            self.assertNotIn("rule-delivery", found, reason)
            self.assertTrue(errs, reason)

    def test_unknown_surface_is_an_error(self):
        found, errs = cer.parse_no_eval(f"no-eval: rules: {self.REASON}", SURFACE_IDS)
        self.assertEqual(found, {})
        self.assertTrue(any("rules" in e for e in errs))

    def test_lines_in_comments_and_code_fences_do_not_count(self):
        body = (f"<!--\nno-eval: rule-delivery: {self.REASON}\n-->\n"
                f"```\nno-eval: context-hooks: {self.REASON}\n```\n"
                f"~~~markdown\nno-eval: session-instructions: {self.REASON}\n~~~\n")
        found, errs = cer.parse_no_eval(body, SURFACE_IDS)
        self.assertEqual(found, {})

    def test_longer_fence_does_not_close_on_shorter_marker(self):
        body = (f"~~~~\n~~~\nno-eval: rule-delivery: {self.REASON}\n~~~~\n"
                f"no-eval: context-hooks: {self.REASON}\n")
        found, errs = cer.parse_no_eval(body, SURFACE_IDS)
        self.assertEqual(errs, [])
        self.assertEqual(set(found), {"context-hooks"})

    def test_quoted_and_indented_examples_are_not_exemptions(self):
        body = (f"> no-eval: rule-delivery: {self.REASON}\n"
                f"    no-eval: context-hooks: {self.REASON}\n"
                f"- no-eval: session-instructions: {self.REASON}\n")
        found, errs = cer.parse_no_eval(body, SURFACE_IDS)
        self.assertEqual(errs, [])
        self.assertEqual(set(found), {"session-instructions"})


class EndToEnd(unittest.TestCase):
    """A real throwaway repository, outside the checkout, git scrubbed."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        self.env = fixture_env()
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "selftest@example.invalid")
        self.git("config", "user.name", "selftest")
        (self.repo / "evals").mkdir()
        (self.repo / "evals" / "surfaces.json").write_text((ROOT / "evals" / "surfaces.json").read_text())
        (self.repo / "hooks").mkdir()
        (self.repo / "AGENTS.md").write_text("boot\n")
        (self.repo / "README.md").write_text("readme\n")
        self.git("add", "-A"); self.git("commit", "-qm", "base")
        self.git("branch", "base")
        self.git("checkout", "-qb", "work")

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *args):
        subprocess.run(["git", *args], cwd=self.repo, env=self.env, check=True, capture_output=True)

    def run_check(self, body=None, pr=True, event_text=None, missing_event=False):
        args = [sys.executable, str(ROOT / "ops" / "check-eval-receipt.py"), "--root", str(self.repo), "--base", "base"]
        env = dict(self.env)
        env.pop("GITHUB_EVENT_NAME", None); env.pop("GITHUB_EVENT_PATH", None)
        if pr:
            event = self.repo.parent / "event.json"
            if not missing_event:
                event.write_text(event_text if event_text is not None else
                                 json.dumps({"pull_request": {"body": body}}))
            env.update(GITHUB_EVENT_NAME="pull_request", GITHUB_EVENT_PATH=str(event))
        return subprocess.run(args, cwd=self.repo, env=env, capture_output=True, text=True)

    def commit(self, path, text):
        p = self.repo / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        self.git("add", "-A"); self.git("commit", "-qm", f"edit {path}")

    def test_unrelated_change_passes(self):
        self.commit("README.md", "changed\n")
        self.assertEqual(self.run_check("").returncode, 0)

    def test_surface_change_without_receipt_or_line_fails_in_a_pr(self):
        self.commit("AGENTS.md", "boot, changed\n")
        out = self.run_check("Just a tweak.")
        self.assertEqual(out.returncode, 1, out.stdout + out.stderr)
        self.assertIn("session-instructions", out.stdout + out.stderr)

    def test_null_pr_body_is_treated_as_empty(self):
        self.commit("AGENTS.md", "boot, changed\n")
        self.assertEqual(self.run_check(None).returncode, 1)

    def test_unreadable_or_malformed_pr_event_fails_closed(self):
        self.commit("AGENTS.md", "boot, changed\n")
        for opts in ({"missing_event": True}, {"event_text": "{bad json"},
                     {"event_text": "{}"}, {"event_text": '{"pull_request": {"body": 12}}'}):
            out = self.run_check(**opts)
            self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
            self.assertIn("event", out.stdout + out.stderr)

    def test_outside_a_pr_it_is_advisory(self):
        self.commit("AGENTS.md", "boot, changed\n")
        out = self.run_check(pr=False)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("session-instructions", out.stdout + out.stderr)

    def test_reasoned_no_eval_line_passes(self):
        self.commit("AGENTS.md", "boot, changed\n")
        out = self.run_check(f"no-eval: session-instructions: {NoEvalLines.REASON}")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_valid_receipt_changed_in_the_pr_passes(self):
        r = good_receipt(); r["surface"] = "session-instructions"
        self.commit("AGENTS.md", "boot, changed\n")
        self.commit("evals/session-instructions/receipt.json", json.dumps(r))
        out = self.run_check("")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_nonshipping_receipt_cannot_satisfy_a_changed_surface(self):
        for decision in ("do_not_merge", "inconclusive"):
            with self.subTest(decision=decision):
                r = good_receipt(); r["surface"] = "session-instructions"
                r["verdict"] = {"decision": decision, "statement": "Measured result does not authorize shipping."}
                self.commit("AGENTS.md", f"boot, changed for {decision}\n")
                self.commit("evals/session-instructions/receipt.json", json.dumps(r))
                out = self.run_check(f"no-eval: session-instructions: {NoEvalLines.REASON}")
                self.assertEqual(out.returncode, 1, out.stdout + out.stderr)
                self.assertIn(decision, out.stdout + out.stderr)

    def test_critical_regression_do_not_merge_receipt_blocks_pr(self):
        r = good_receipt(); r["surface"] = "session-instructions"
        r["dimensions"][1] = dimension("should-not-fire-precision", critical=True,
                                       base=(0.94, 0.89, 0.98), cand=(0.80, 0.74, 0.86),
                                       delta=(-0.14, -0.20, -0.08), direction="regressed")
        r["verdict"] = {"decision": "do_not_merge",
                        "statement": "Blocked: critical dimension should-not-fire-precision regressed."}
        self.assertEqual(cer.validate_receipt(r, "session-instructions", ROOT), [])
        self.commit("AGENTS.md", "boot, changed\n")
        self.commit("evals/session-instructions/receipt.json", json.dumps(r))
        out = self.run_check("")
        self.assertEqual(out.returncode, 1, out.stdout + out.stderr)
        self.assertIn("do_not_merge", out.stdout + out.stderr)

    def test_stale_receipt_from_an_earlier_change_does_not_count(self):
        r = good_receipt(); r["surface"] = "session-instructions"
        self.git("checkout", "-q", "--detach", "base")
        self.commit("evals/session-instructions/receipt.json", json.dumps(r))
        self.git("branch", "-f", "base", "HEAD")
        self.git("checkout", "-qb", "work2")
        self.commit("AGENTS.md", "boot, changed again\n")
        self.assertEqual(self.run_check("").returncode, 1)

    def test_in_noise_receipt_saying_ship_fails(self):
        r = in_noise_receipt(); r["surface"] = "session-instructions"
        self.commit("AGENTS.md", "boot, changed\n")
        self.commit("evals/session-instructions/receipt.json", json.dumps(r))
        out = self.run_check("")
        self.assertEqual(out.returncode, 1)
        self.assertIn("do not merge on quality grounds", out.stdout + out.stderr)

    def test_malformed_receipt_fails_even_when_its_surface_did_not_change(self):
        self.commit("evals/rule-delivery/receipt.json", "{not json")
        self.assertEqual(self.run_check("").returncode, 1)


CONTROL_KEY = "eval_receipt"
RULE_ID = "6cbaa63a-be57-4c2e-955f-4ea7b5c0405d"
MIGRATION = ROOT / "migrations" / "0753_eval_receipt_control.sql"

_SYNC_SPEC = importlib.util.spec_from_file_location("sync_control_catalog", ROOT / "ops" / "sync_control_catalog.py")
assert _SYNC_SPEC and _SYNC_SPEC.loader
sync = importlib.util.module_from_spec(_SYNC_SPEC)
_SYNC_SPEC.loader.exec_module(sync)


class ControlRegistration(unittest.TestCase):
    """Rule 6cbaa63a names 'eval_receipt CI check' as its carrying control.

    approve-rule accepts a control only when ops.enforcement_control_catalog
    carries it installed and verified, in a class that can enforce (deny_gate,
    stop_gate, schema, transactional_schema). The catalog is compiled from the
    repository's declarations by ops/sync_control_catalog.py and seeded by a
    migration rendered from the same compiler.
    """

    @classmethod
    def setUpClass(cls):
        cls.rows = {r["control_key"]: r for r in sync.compile_catalog()}

    def test_the_control_is_declared_and_compiles_as_installed(self):
        row = self.rows.get(CONTROL_KEY)
        self.assertIsNotNone(row, f"{CONTROL_KEY} is not declared")
        self.assertTrue(row["installed"], row["not_installed_reason"])

    def test_it_is_a_class_approve_rule_accepts(self):
        self.assertIn(self.rows[CONTROL_KEY]["enforcement_class"],
                      {"deny_gate", "stop_gate", "schema", "transactional_schema"})

    def test_it_names_this_check_and_this_selftest(self):
        row = self.rows[CONTROL_KEY]
        self.assertIn("ops/check-eval-receipt.py", row["implementation_ref"].split("; "))
        self.assertIn("ops/check-eval-receipt-selftest.py", row["test_ref"].split("; "))

    def test_the_declaration_names_the_rule_it_carries(self):
        side = json.loads((ROOT / "ops" / "config" / "control-enforcement-classes.v1.json").read_text())
        entry = side["controls_absent_from_the_map"][CONTROL_KEY]
        self.assertIn(RULE_ID, entry.get("_note", ""))

    def test_the_enforcement_map_is_not_touched(self):
        # audits/guidance-situation-curation-review.v1.json pins the map by
        # sha256 while that review is still proposed; declaring the control
        # beside the map is the route that keeps the pin true.
        review = json.loads((ROOT / "audits" / "guidance-situation-curation-review.v1.json").read_text())
        pinned = next(i["sha256"] for i in _walk(review)
                      if isinstance(i, dict) and i.get("path") == "ops/config/rule-enforcement-map.json")
        import hashlib
        actual = hashlib.sha256((ROOT / "ops" / "config" / "rule-enforcement-map.json").read_bytes()).hexdigest()
        self.assertEqual(actual, pinned)

    def test_the_seed_migration_is_exactly_the_compiler_output(self):
        text = MIGRATION.read_text()
        self.assertIn(sync.render_control_upsert_sql(self.rows[CONTROL_KEY]), text)
        self.assertNotRegex(text, r"(?im)^\s*(begin|commit)\s*;")  # the runner owns the transaction

    def test_the_migration_slot_is_not_shared(self):
        slot = MIGRATION.name[:4]
        same = [p.name for p in (ROOT / "migrations").glob(f"{slot}*.sql")]
        self.assertEqual(same, [MIGRATION.name])


def _walk(value):
    if isinstance(value, dict):
        yield value
        for v in value.values():
            yield from _walk(v)
    elif isinstance(value, list):
        for v in value:
            yield from _walk(v)


if __name__ == "__main__":
    unittest.main(verbosity=1)
