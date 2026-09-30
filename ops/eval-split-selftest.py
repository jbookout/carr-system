#!/usr/bin/env python3
"""Behavioral checks for frozen final evidence and guarded tuning access."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import eval_split as E


class SplitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(self.tmp)
        self.cases = [{"id": f"c{i}", "group": f"source{i}", "label": i % 2,
                       "input": f"example {i}"} for i in range(30)]
        self.manifest = E.freeze(self.cases, self.root / "bundle", seed="test",
                                 source="fresh temporal cohort", previously_seen=[])

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
            self.assertEqual(module.load_cases("train"), E.load_partition(self.manifest, "train"))
            with self.assertRaisesRegex(E.SplitError, "final"):
                module.load_cases("final")


if __name__ == "__main__":
    unittest.main()
