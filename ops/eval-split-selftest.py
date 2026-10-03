#!/usr/bin/env python3
"""Behavioral checks for frozen final evidence and guarded tuning access."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
import importlib.util
import ast
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import eval_split as E


def rule_runner():
    spec = importlib.util.spec_from_file_location("split_rule_runner", Path(E.__file__).parents[1] / "evals/rule-delivery/run_eval.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SplitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(self.tmp)
        self.cases = [{"id": f"c{i}", "group": f"source{i}", "label": i % 2,
                       "input": f"example {i}"} for i in range(30)]
        self.manifest = E.freeze(self.cases, self.root / "bundle", seed="test",
                                 source="fresh temporal cohort", previously_seen=[])

    def binding(self):
        return {"candidate_digest": E.digest("candidate"), "baseline_digest": E.digest("base"),
                "harness_digest": E.digest("runner"), "model": "jev-1.13.0"}

    def tune(self, code):
        script = self.root / "tuner.py"
        script.write_text(code)
        return subprocess.run([sys.executable, str(Path(E.__file__).parents[1] / "evals/rule-delivery/freeze_split.py"),
                               "tune", str(self.manifest), str(script)], capture_output=True, text=True)

    def test_every_attempt_persists_and_old_clean_audit_cannot_hide_failure(self):
        self.assertEqual(self.tune("pass").returncode, 0)
        audit_path = self.manifest.parent / "tuning-access.json"
        old = json.loads(audit_path.read_text())
        self.assertEqual(self.tune("pass").returncode, 0)
        final = self.manifest.parent / "final.json"
        self.assertNotEqual(self.tune(f"try:\n open({str(final)!r}).read()\nexcept PermissionError:\n pass\n").returncode, 0)
        current = json.loads(audit_path.read_text())
        self.assertEqual(len(current["attempts"]), 3)
        self.assertEqual(current["status"], "failed")
        with self.assertRaises(E.SplitError):
            E.final_evaluation(self.manifest, self.binding(), old)

    def test_normal_cli_exit_and_exception_are_durably_audited(self):
        self.assertEqual(self.tune("raise SystemExit(0)").returncode, 0)
        audit_path = self.manifest.parent / "tuning-access.json"
        self.assertTrue(audit_path.exists())
        self.assertNotEqual(self.tune("raise RuntimeError('failed tuner')").returncode, 0)
        self.assertEqual(json.loads(audit_path.read_text())["status"], "failed")

    def test_old_success_is_stale_even_after_another_clean_round(self):
        with E.tuning_guard(self.manifest) as old:
            pass
        with E.tuning_guard(self.manifest) as current:
            pass
        self.assertEqual(len(current["attempts"]), 2)
        with self.assertRaisesRegex(E.SplitError, "every.*attempt"):
            E.final_evaluation(self.manifest, self.binding(), old)
        E.final_evaluation(self.manifest, self.binding(), current)

    def test_final_lock_appearing_during_lease_acquisition_closes_tuning(self):
        write = E._write
        def concurrent_final(path, doc):
            write(path, doc)
            if Path(path).name == "evaluation-active.json":
                write(self.manifest.parent / "final-lock.json", {"consumed": True})
        with unittest.mock.patch.object(E, "_write", side_effect=concurrent_final):
            with self.assertRaisesRegex(E.SplitError, "consumed"):
                E.start_tuning(self.manifest)

    def test_historical_normalized_replay_and_reader_cases_are_ineligible(self):
        R = rule_runner()
        replay = R.replay_cases()[:12]
        for rows in (replay, [{**c, "id": f"renamed-{i}", "group": f"synthetic-{i}"} for i, c in enumerate(replay)],
                     R.load_cases("train")[:12]):
            with self.assertRaisesRegex(E.SplitError, "previously.*seen"):
                E.freeze(rows, self.root / "historical", seed="x", source="x", previously_seen=[])

    def test_inherited_final_handles_and_descriptors_refuse_clean_audit(self):
        for opener in (lambda p: p.open(), lambda p: os.fdopen(os.open(p, os.O_RDONLY))):
            with opener(self.manifest.parent / "final.json") as handle:
                with self.assertRaises((E.SplitError, PermissionError)):
                    with E.tuning_guard(self.manifest):
                        handle.read()

    def test_rule_results_and_cli_summary_use_manifest_development_membership(self):
        R = rule_runner()
        rows = [{**c, "prompt": c["input"], "tool_calls": [], "gold": [], "disputed": [],
                 "source": "synthetic", "stratum": "quiet", "kind": "should_not_fire", "note": "fixture",
                 **({"split": "train"} if i % 2 else {})} for i, c in enumerate(self.cases)]
        manifest = E.freeze(rows, self.root / "rules", seed="x", source="x", previously_seen=[])
        with unittest.mock.patch.dict(os.environ, {"CARR_EVAL_SPLIT": str(manifest)}), \
             unittest.mock.patch.object(R, "World", return_value=object()), \
             unittest.mock.patch.object(R, "replay", return_value=[]), \
             unittest.mock.patch.object(R, "grade", return_value={"expected": [], "false": [], "tokens": 0}):
            results = R.run("development", "test")
            self.assertTrue(results)
            self.assertEqual({r["split"] for r in results}, {"development"})
            self.assertTrue(all("development" in r["tags"] for r in results))
        with unittest.mock.patch.object(R, "run", return_value=results), \
             unittest.mock.patch.object(R, "summarize", return_value={"cases": len(results)}), \
             unittest.mock.patch("builtins.print") as output:
            R.main(["--split", "development", "--print"])
            self.assertEqual(output.call_args.args[0], "development")

    def test_frozen_variant_selection_uses_development(self):
        R = rule_runner()
        with unittest.mock.patch.dict(os.environ, {"CARR_EVAL_SPLIT": str(self.manifest)}), \
             unittest.mock.patch.object(R, "load_run", return_value=[]) as load, \
             unittest.mock.patch.object(R, "paired_bootstrap", return_value=(0, 0, 0)):
            R.verdict("base", "candidate", "recall")
            self.assertEqual([c.args[1] for c in load.call_args_list], ["development", "development"])

    def test_partition_labels_are_parsed_from_the_authenticated_read(self):
        target = self.manifest.parent / "development.json"
        original = json.loads(target.read_text())
        digest = E.file_digest
        def racing_digest(path):
            value = digest(path)
            changed = json.loads(target.read_text())
            changed[0]["label"] = "tampered label"
            target.write_text(json.dumps(changed))
            return value
        with unittest.mock.patch.object(E, "file_digest", side_effect=racing_digest):
            self.assertEqual(E.load_partition(self.manifest, "development"), original)

    def test_receipt_seam_rejects_rehashed_invalid_lock_or_audit_contracts(self):
        with E.tuning_guard(self.manifest) as audit:
            pass
        _, provenance = E.final_evaluation(self.manifest, self.binding(), audit)
        lock_path = Path(provenance["final_lock"])
        original = json.loads(lock_path.read_text())
        for field, value in (("schema", "wrong"), ("baseline_digest", ""), ("harness_digest", []), ("model", 42)):
            lock = {**original, field: value}
            lock["digest"] = E.digest({k: v for k, v in lock.items() if k != "digest"})
            lock_path.write_text(json.dumps(lock))
            p = {**provenance, field: value, "final_lock_digest": lock["digest"]}
            self.assertTrue(E.provenance_errors(p), field)
        lock = {**original, "tuning_access": {**original["tuning_access"], "schema": "wrong"}}
        lock["tuning_access"]["digest"] = E.digest({k: v for k, v in lock["tuning_access"].items() if k != "digest"})
        lock["digest"] = E.digest({k: v for k, v in lock.items() if k != "digest"})
        lock_path.write_text(json.dumps(lock))
        self.assertTrue(E.provenance_errors({**provenance, "final_lock_digest": lock["digest"]}))

    def test_full_rerank_and_other_harness_inputs_determine_content(self):
        rows = [{"id": f"rerank{i}", "group": f"synthetic{i}", "situation": "same query",
                 "candidates": [{"id": str(i), "text": f"candidate {i}"}], "gold": [str(i)]} for i in range(6)]
        manifest = E.freeze(rows, self.root / "rerank", seed="x", source="x", previously_seen=[])
        self.assertTrue(E.load_partition(manifest))
        self.assertNotEqual(E._content({"input": "same", "extra_input": "a"}),
                            E._content({"input": "same", "extra_input": "b"}))
        relabeled = [{**rows[0], "candidates": [{**rows[0]["candidates"][0], "relevance": rel}],
                      "id": f"relabeled-{rel}", "group": f"synthetic-label-{rel}"} for rel in range(3)]
        with self.assertRaisesRegex(E.SplitError, "duplicate.*content"):
            E.freeze(relabeled, self.root / "relabeled-rerank", seed="x", source="x", previously_seen=[])

    def test_explicit_valid_source_groups_required(self):
        for invalid in (None, "", "  ", 12, []):
            rows = [{**c, "group": invalid} for c in self.cases]
            with self.assertRaisesRegex(E.SplitError, "source group"):
                E.freeze(rows, self.root / "invalid", seed="x", source="x", previously_seen=[])
        with self.assertRaisesRegex(E.SplitError, "source group"):
            E.freeze([{k: v for k, v in c.items() if k != "group"} for c in self.cases],
                     self.root / "absent", seed="x", source="x", previously_seen=[])

    def test_retired_report_contains_only_used_historical_verifier(self):
        path = Path(E.__file__).parents[1] / "evals/rule-delivery/make_report.py"
        functions = {n.name for n in ast.walk(ast.parse(path.read_text())) if isinstance(n, ast.FunctionDef)}
        self.assertFalse(functions & {"main", "ledger", "state", "table", "source_manifest"})
        self.assertIn("receipt_source_matches", functions)

    def test_historical_verifier_authenticates_original_blobs_after_runtime_evolves(self):
        spec = importlib.util.spec_from_file_location("historical_report", Path(E.__file__).parents[1] / "evals/rule-delivery/make_report.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        original = subprocess.run(["git", "show", "c74fc21a703428c1345049fa398a35b5eca8504f:evals/rule-delivery/receipt.json"],
                                  capture_output=True, text=True, check=True)
        receipt = json.loads(original.stdout)
        self.assertTrue(module.receipt_source_matches(receipt))

    def test_final_is_separate_and_never_loaded_by_tuning(self):
        train = E.load_partition(self.manifest, "train")
        dev = E.load_partition(self.manifest, "development")
        final_path = self.root / "bundle" / "final.json"
        final_path.write_text("not readable JSON")
        self.assertTrue(train and dev)
        self.assertEqual(train, E.load_partition(self.manifest, "train"))
        with self.assertRaisesRegex(E.SplitError, "final.*tuning"):
            E.load_partition(self.manifest, "final")

    def test_frozen_bundle_cannot_be_overwritten(self):
        with self.assertRaises(FileExistsError):
            E.freeze(self.cases, self.root / "bundle", seed="other", source="x", previously_seen=[])

    def test_duplicate_content_and_source_groups_cannot_cross_splits(self):
        cases = self.cases + [{**self.cases[0], "id": "duplicate"}]
        with self.assertRaisesRegex(E.SplitError, "duplicate.*content"):
            E.freeze(cases, self.root / "duplicate", seed="x", source="x", previously_seen=[])
        altered_label = self.cases + [{**self.cases[0], "id": "renamed", "label": "different"}]
        with self.assertRaisesRegex(E.SplitError, "duplicate.*content"):
            E.freeze(altered_label, self.root / "renamed", seed="x", source="x", previously_seen=[])
        groups = {p: set() for p in E.PARTITIONS}
        for p in E.PARTITIONS:
            for row in E.read_manifest(self.manifest)["partitions"][p]["members"]:
                groups[p].add(row["group"])
        self.assertFalse(groups["train"] & groups["final"])
        self.assertFalse(groups["development"] & groups["final"])

    def test_tampered_partition_and_manifest_fail(self):
        path = self.root / "bundle" / "train.json"
        path.write_text("[]")
        with self.assertRaisesRegex(E.SplitError, "digest"):
            E.load_partition(self.manifest, "train")
        doc = json.loads(self.manifest.read_text())
        doc["source"] = "changed"
        self.manifest.write_text(json.dumps(doc))
        with self.assertRaisesRegex(E.SplitError, "digest"):
            E.read_manifest(self.manifest)

    def test_previously_tuned_cases_cannot_be_reclassified_as_final(self):
        with self.assertRaisesRegex(E.SplitError, "previously.*seen"):
            E.freeze(self.cases, self.root / "seen", seed="x", source="x",
                     previously_seen=["c0"])

    def test_a_swallowed_final_read_still_fails_tuning(self):
        final = self.root / "bundle" / "final.json"
        alias = self.root / "alias.json"
        alias.symlink_to(final)
        for path in (final, alias):
            script = self.root / "tuner.py"
            script.write_text(f"try:\n open({str(path)!r}).read()\nexcept PermissionError:\n pass\n")
            run = subprocess.run([sys.executable, str(Path(E.__file__).parents[1] / "evals/rule-delivery/freeze_split.py"), "tune", str(self.manifest),
                                  str(script)], capture_output=True, text=True)
            self.assertNotEqual(run.returncode, 0, run.stdout + run.stderr)
            self.assertIn("final_access", run.stderr)

    def test_raw_mixed_source_and_hardlink_reads_fail_tuning(self):
        final = self.root / "bundle" / "final.json"
        hardlink = self.root / "hardlink.json"
        os.link(final, hardlink)
        with self.assertRaises(E.SplitError):
            with E.tuning_guard(self.manifest):
                try:
                    hardlink.read_text()
                except PermissionError:
                    pass
        raw = self.root / "mixed.json"
        raw.write_text(json.dumps(self.cases))
        manifest = E.freeze(self.cases, self.root / "mixed-bundle", seed="x", source="x",
                            previously_seen=[], blocked_sources=[raw])
        with self.assertRaises(E.SplitError):
            with E.tuning_guard(manifest):
                try:
                    raw.read_text()
                except PermissionError:
                    pass

    def test_final_requires_candidate_lock_and_is_consumed_once(self):
        binding = {"candidate_digest": E.digest("candidate"), "baseline_digest": E.digest("base"),
                   "harness_digest": E.digest("runner"), "model": "jev-1.13.0"}
        with E.tuning_guard(self.manifest) as audit:
            E.load_partition(self.manifest, "development")
        final, provenance = E.final_evaluation(self.manifest, binding, audit)
        self.assertTrue(final)
        self.assertEqual(provenance["score_partition"], "final")
        self.assertEqual(E.provenance_errors(provenance), [])
        with self.assertRaisesRegex(E.SplitError, "consumed"):
            E.final_evaluation(self.manifest, binding, audit)
        provenance["candidate_digest"] = E.digest("different")
        self.assertTrue(E.provenance_errors(provenance))

    def test_registered_rule_runner_loads_only_requested_tuning_partition(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("split_rule_runner", Path(E.__file__).parents[1] / "evals/rule-delivery/run_eval.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        # A raw combined corpus must never be opened when a sealed bundle exists.
        module.V2_CASES = "/unreadable/raw-corpus.json"
        with unittest.mock.patch.dict("os.environ", {"CARR_EVAL_SPLIT": str(self.manifest)}):
            self.assertEqual(module.load_cases("train"), [{**c, "split": "train"} for c in E.load_partition(self.manifest, "train")])
            with self.assertRaisesRegex(E.SplitError, "final"):
                module.load_cases("final")


if __name__ == "__main__":
    unittest.main()
