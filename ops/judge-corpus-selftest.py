#!/usr/bin/env python3
"""Offline tests at the corpus, capture and paired evaluation boundaries."""
import copy
import json
import sys
import unittest
import tempfile
import os
import io
import subprocess
import importlib.util
from unittest.mock import patch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class GoldCorpusTests(unittest.TestCase):
    def test_historical_drive_literals_are_classified_as_judge_test_fixtures(self):
        spec = importlib.util.spec_from_file_location("judge_drive_inventory", ROOT / "ops/drive-dependency-inventory.py")
        inventory = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = inventory
        spec.loader.exec_module(inventory)
        entries = json.loads((ROOT / "ops/config/drive-dependencies.v1.json").read_text())["entries"]
        for name in ("system-work-gold.v1.json", "source-projections.v1.json"):
            ref = inventory.Reference("ops/fixtures/judge-provider/" + name, 1, "CARR_VAULT", "{{VAULT}}", "historical source excerpt")
            matches = [e for e in entries if inventory.matches(e, ref)]
            self.assertEqual([e["class"] for e in matches], ["test_fixture"])

    def test_frozen_source_projections_match_original_code_and_intake_calls(self):
        import hashlib
        from tools.judge.corpus import redact
        projection = json.loads((ROOT / "ops/fixtures/judge-provider/source-projections.v1.json").read_text())
        for locator, item in projection["pilot"].items():
            with self.subTest(locator=locator):
                ref = item["source"]
                raw = subprocess.check_output(["git", "show", ref["revision"] + ":" + ref["path"]], cwd=ROOT)
                self.assertEqual(hashlib.sha256(raw).hexdigest(), ref["sha256"])
                lines = raw.decode().splitlines()
                lo, hi = ref["lines"]
                expected = redact({"region": {"path": ref["path"], "code": "\n".join(lines[lo-1:hi])},
                                   "module_context": "\n".join(lines[:35])})
                self.assertEqual(item["state"], expected)
        spec = importlib.util.spec_from_file_location("intake_source_assertions", ROOT / "ops/jev-intake-selftest.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        seen = []
        original = module.FakeClient.ask
        def observe(client, state, questions, **options):
            seen.append({"state": state, "questions": questions, "model": "jev-1.13.0"})
            return original(client, state, questions, **options)
        judge = module.intake._judge()
        with patch.object(module.FakeClient, "ask", observe), patch.object(module.intake, "_judge", return_value=judge), patch.object(judge, "record"):
            for locator, request in projection["intake"].items():
                with self.subTest(locator=locator):
                    cls, method = locator.split(".")
                    before = len(seen)
                    getattr(getattr(module, cls)(method), method)()
                    self.assertEqual(len(seen), before + 1)
                    self.assertEqual(seen[-1], request)

    def test_source_scenario_cannot_cross_splits_under_a_new_group_or_rule(self):
        from tools.judge.corpus import validate
        from tools.judge.paired_eval import digest
        corpus = json.loads((ROOT / "ops/fixtures/judge-provider/system-work-gold.v1.json").read_text())
        first, second = copy.deepcopy(corpus["cases"][:2])
        self.assertNotEqual(first["request"]["state"]["rule"], second["request"]["state"]["rule"])
        second.update(split="final", group="renamed-source-family")
        cases = [first, second]
        with self.assertRaisesRegex(ValueError, "split leakage"):
            validate({"schema": corpus["schema"], "cases": cases, "corpus_sha256": digest(cases)}, source_root=ROOT)

    def test_rehashed_unrelated_requests_cannot_borrow_source_gold(self):
        from tools.judge.corpus import validate
        from tools.judge.paired_eval import digest
        original = json.loads((ROOT / "ops/fixtures/judge-provider/system-work-gold.v1.json").read_text())
        representatives = {}
        for case in original["cases"]:
            representatives.setdefault(case["source_ref"]["path"], case)
        for source, case in representatives.items():
            for field in ("state", "questions"):
                with self.subTest(source=source, field=field):
                    bad = copy.deepcopy(case)
                    if field == "state":
                        bad["request"][field] = {"turn": "unrelated request", "rule": "unrelated rule"}
                    else:
                        qid = next(iter(bad["request"][field]))
                        bad["request"][field][qid]["instructions"] = "Judge an unrelated property."
                    bad["request_sha256"] = digest(bad["request"])
                    candidate = {"schema": original["schema"], "cases": [bad], "corpus_sha256": digest([bad])}
                    with self.assertRaisesRegex(ValueError, "source request"):
                        validate(candidate, source_root=ROOT)

    def test_redacted_carry_fixture_preserves_replay_identity_and_announcement(self):
        from tools.judge.corpus import redact
        from ops.business_data_patterns import REPLAY_SESSION_ID
        corpus = json.loads((ROOT / "ops/fixtures/judge-provider/system-work-gold.v1.json").read_text())
        case = next(c for c in corpus["cases"] if c["receipt_id"] == "replay-scenario:b2d3e4f5a6b7:3")
        source = json.loads((ROOT / case["source_ref"]["path"]).read_text().splitlines()[3])
        for seed in (redact(source["out_files"]), case["request"]["state"]["input"]["out_files"]):
            with tempfile.TemporaryDirectory() as tmp:
                for rel, note in seed.items():
                    path = Path(tmp) / "out" / rel
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(note)
                result = subprocess.run([sys.executable, str(ROOT / "hooks/chat-lint-carryover.py")],
                    input=json.dumps({"session_id": REPLAY_SESSION_ID}), text=True, capture_output=True,
                    env={**os.environ, "CARR_REPO_ROOT": tmp}, check=True)
                self.assertTrue(result.stdout, "transformed seed lost the carry-note announcement")
                self.assertEqual(json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"],
                                 "Keep replies to one paragraph unless asked for more detail.")

    def test_committed_corpus_is_complete_labelled_and_replayable(self):
        from tools.judge.corpus import load_corpus
        corpus = load_corpus(ROOT / "ops/fixtures/judge-provider/system-work-gold.v1.json")
        self.assertGreaterEqual(len(corpus["cases"]), 150)
        shapes = {q["type"] for c in corpus["cases"] for q in c["request"]["questions"].values()}
        self.assertEqual(shapes, {"noul", "choice", "score"})
        self.assertEqual({c["split"] for c in corpus["cases"]}, {"dev", "final"})
        self.assertGreaterEqual(len({c["purpose"] for c in corpus["cases"]}), 8)
        inventory = json.loads((ROOT / "out/judge-inventory.json").read_text())
        purposes = {call["purpose"] for call in inventory["calls"] if call["class"] == "system_work"}
        self.assertLessEqual({c["purpose"] for c in corpus["cases"]}, purposes)
        for case in corpus["cases"]:
            self.assertEqual(set(case["gold"]), set(case["request"]["questions"]))
            self.assertTrue(case["source_ref"]["sha256"])
            self.assertTrue(case["label_basis"])


    def test_schema_labels_source_integrity_and_split_leakage_are_rejected(self):
        from tools.judge.corpus import load_corpus, validate
        from tools.judge.paired_eval import digest
        original = load_corpus(ROOT / "ops/fixtures/judge-provider/system-work-gold.v1.json", source_root=ROOT)
        for mutate, expected in [
            (lambda c: c["cases"][0].pop("gold"), "gold"),
            (lambda c: c["cases"][0].update(split="train"), "split"),
            (lambda c: c["cases"][0]["source_ref"].update(sha256="0" * 64), "source digest"),
            (lambda c: c["cases"][0]["gold"].update(binds="yes"), "gold label outside requested answer domain"),
            (lambda c: c["cases"][0]["gold"].update(binds=not c["cases"][0]["gold"]["binds"]), "source gold"),
        ]:
            bad = copy.deepcopy(original)
            mutate(bad)
            bad["corpus_sha256"] = digest(bad["cases"])
            with self.assertRaisesRegex(ValueError, expected):
                validate(bad, source_root=ROOT)
        bad = copy.deepcopy(original)
        duplicate = copy.deepcopy(bad["cases"][0])
        duplicate["receipt_id"] = "leaking-copy"
        duplicate["split"] = "final" if duplicate["split"] == "dev" else "dev"
        duplicate["group"] += "-copied"
        duplicate["request"]["state"]["turn"]["prompt"] += " "
        duplicate["request_sha256"] = digest(duplicate["request"])
        bad["cases"].append(duplicate)
        bad["corpus_sha256"] = digest(bad["cases"])
        with self.assertRaisesRegex(ValueError, "split leakage"):
            validate(bad)

    def test_public_fixtures_have_no_real_names_pii_or_credential_values(self):
        from tools.judge.corpus import load_corpus, redact
        from ops import business_data_patterns as privacy
        corpus = load_corpus(ROOT / "ops/fixtures/judge-provider/system-work-gold.v1.json")
        self.assertEqual(privacy.scan_value(corpus), [])
        # Plant a roster-only name in keys, prose, and a path. The name is a
        # synthetic stand-in, not a client copied into this public test.
        roster = privacy.Roster(["Xylo Syntheticname"])
        with patch.object(privacy, "roster", return_value=roster):
            planted = {"Xylo Syntheticname": {"nested": "ask xylo_syntheticname", "path": "/home/private/XyloSyntheticname/data", "password": "private" * 9}, "contact": "hidden" + "@" + "example.invalid"}
            safe = redact(planted)
            self.assertEqual(privacy.scan_value(safe), [])
            self.assertNotIn("Xylo", json.dumps(safe))
            self.assertNotIn("privateprivate", json.dumps(safe))
        for case in corpus["cases"]:
            self.assertNotIn("synthetic_operator", case["receipt_id"])

    def test_paired_eval_dry_run_uses_fake_providers_and_only_the_selected_split(self):
        from tools.judge import paired_eval
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "report.json"
            with patch("urllib.request.urlopen", side_effect=AssertionError("dry run reached network")):
                report = paired_eval.cli([str(ROOT / "ops/fixtures/judge-provider/system-work-gold.v1.json"), "--split", "final", "--dry-run", "--output", str(path)])
            self.assertEqual(report["status"], "complete")
            self.assertEqual(report["agreement"], 1)
            self.assertGreater(report["providers"]["decisions"]["labelled_questions"], 0)
            self.assertEqual(report["providers"]["decisions"]["models"], ["fake-decisions"])
            self.assertEqual(report["promotion"], "not_performed")
            self.assertEqual(json.loads(path.read_text()), report)
            gold = json.loads((ROOT / "ops/fixtures/judge-provider/system-work-gold.v1.json").read_text())
            self.assertEqual(report["paired_successes"], sum(c["split"] == "final" for c in gold["cases"]))


class CaptureTests(unittest.TestCase):
    def test_missing_roster_capture_preserves_hook_refusal_streams(self):
        from tools.judge import interface
        from ops import business_data_patterns as privacy
        def refuse(*args, **kwargs):
            print("gate refuses the scoped action", file=sys.stderr)
            return {"decision": "deny"}
        with tempfile.TemporaryDirectory() as tmp:
            for switch in ("0", "1", None):
                with self.subTest(switch=switch):
                    env = {"CI": "", "GITHUB_ACTIONS": "", "CARR_JUDGE_CAPTURE_PATH": str(Path(tmp) / "traffic.jsonl")}
                    if switch is not None:
                        env["CARR_JUDGE_CAPTURE"] = switch
                    err, out = io.StringIO(), io.StringIO()
                    with patch.dict(os.environ, env, clear=True), patch.object(privacy, "roster", return_value=None), patch("sys.stderr", err), patch("sys.stdout", out):
                        self.assertEqual(interface.ask("review", {"q": {"type": "noul"}}, jev=refuse), {"decision": "deny"})
                    self.assertEqual(err.getvalue(), "gate refuses the scoped action\n")
                    self.assertEqual(out.getvalue(), "")

    def test_seam_captures_redacted_frozen_inputs_without_changing_response(self):
        from tools.judge import interface
        from tools.judge.paired_eval import freeze, digest
        from ops import business_data_patterns as privacy
        request_state = {"owner": "Xylo Syntheticname", "text": "review a retry helper", "email": "private" + "@" + "example.invalid", "api_key": "opaque" * 8}
        questions = {"q": {"type": "noul", "instructions": "Does the helper retry?"}}
        result = {"model": "fake", "answers": {"q": {"type": "noul", "noul": .8}}, "usage": {"input_tokens": 1, "output_tokens": 1}}
        def transport(state, offered, **options):
            state["text"] = "mutated by provider"
            return result
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "traffic.jsonl"
            roster_path = Path(tmp) / "roster.txt"
            roster_path.write_text("Xylo Syntheticname\n")
            with patch.dict(os.environ, {"CARR_JUDGE_CAPTURE": "1", "CARR_JUDGE_CAPTURE_PATH": str(path), "CARR_CLIENT_ROSTER": str(roster_path)}, clear=False), patch.object(privacy, "roster", return_value=privacy.read_roster(roster_path)):
                returned = interface.ask(request_state, questions, jev=transport, model="jev-1.13.0", caller="review")
                self.assertIs(returned, result)
                rows = [json.loads(line) for line in path.read_text().splitlines()]
                self.assertEqual(len(rows), 1)
                row = rows[0]
                self.assertEqual(row["request"]["state"]["text"], "review a retry helper")
                self.assertNotIn("gold", row)
                self.assertNotIn("api_key", row["request"]["state"])
                self.assertNotIn("opaque", path.read_text())
                self.assertNotIn("Xylo", path.read_text())
                self.assertEqual(row["request_sha256"], digest(row["request"]))
                self.assertEqual(len(freeze(rows)["cases"]), 1)

    def test_ci_and_runtime_capture_are_disabled_and_write_failure_is_visible(self):
        from tools.judge import interface
        from ops import business_data_patterns as privacy
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "traffic.jsonl"
            options = {"CARR_JUDGE_CAPTURE_PATH": str(path), "CI": "true", "CARR_JUDGE_CAPTURE": ""}
            with patch.dict(os.environ, options), patch.object(privacy, "roster", return_value=privacy.Roster(["Synthetic Subject"])):
                interface.ask("review helper", {"q": {"type": "noul"}}, jev=lambda *a, **k: {})
                self.assertFalse(path.exists())
            with patch.dict(os.environ, {"CARR_JUDGE_CAPTURE": "1", "CARR_JUDGE_CAPTURE_PATH": str(path)}):
                interface.ask("runtime", {"q": {}}, work_class="app_runtime", jev=lambda *a, **k: {})
                self.assertFalse(path.exists())
            err = io.StringIO()
            with patch.dict(os.environ, {"CARR_JUDGE_CAPTURE": "1", "CARR_JUDGE_CAPTURE_PATH": tmp}), patch.object(privacy, "roster", return_value=privacy.Roster(["Synthetic Subject"])), patch("sys.stderr", err):
                self.assertEqual(interface.ask("review", {"q": {"type": "noul"}}, jev=lambda *a, **k: {"ok": True}), {"ok": True})
            self.assertEqual(err.getvalue(), "")
            diagnostics = Path(tmp).with_name(Path(tmp).name + ".diagnostics.jsonl")
            self.assertEqual(json.loads(diagnostics.read_text())["status"], "append_failed")
            diagnostics.unlink()


if __name__ == "__main__":
    unittest.main()
