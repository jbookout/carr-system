#!/usr/bin/env python3
# ci: selftest
import hashlib
import importlib.util
import json
import sys
import unittest
import tempfile
import io
from unittest.mock import patch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib import rule_recall
from lib import rule_routes


class RecallContract(unittest.TestCase):
    def test_complete_proof_is_invalidated_by_changed_or_missing_sources(self):
        R = rule_recall
        rid='24e10ee8'; statement='Binding text'; moment='2026-10-05T12:00:00Z'
        sources=R.PROOF_SOURCES
        with tempfile.TemporaryDirectory() as d:
         root=Path(d)
         def put(name,value):
          p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(value));return {'path':name,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
         route={'routes':[{'kind':'trigger','tools':['Write']}]}
         put('ops/config/rule-selection-corpus.v1.json',{'rules':[{'id':rid,'statement':statement}]})
         put('ops/config/rule-routes.v1.json',{'rules':{rid:route}})
         cases=json.loads((ROOT/'ops/fixtures/rule-delivery-eval/cases.v2.json').read_text()); fixture=put('ops/fixtures/rule-delivery-eval/cases.v2.json',cases)
         def row(ref,requires):
          receipt={'receipt_id':ref,'observed_at':moment,'rules':[{'id':rid,'statement':statement}]}
          return {'source_ref':ref,'requires':requires,'event_at':moment,'receipt_id':ref,'receipt':receipt}
         benchmark=[row(c['id'],rid in c['gold']) for c in cases['cases'] if c['split']=='test']
         real=[row('turn-'+str(i),i<10) for i in range(200)]
         frame=put('frame.json',{'schema':'rule-recall-native-frame/v1','turns':[{'source_ref':r['source_ref'],'event_at':r['event_at'],'receipt':r['receipt']} for r in real]})
         common={'schema':'rule-recall-observations/v1','rule_id':rid,'statement_sha256':hashlib.sha256(statement.encode()).hexdigest(),'route_sha256':R.digest(route)}
         binding={k:common[k] for k in ('statement_sha256','route_sha256')}
         binding['benchmark']=put('benchmark.json',dict(common,source='benchmark',observations=benchmark,fixture_sha256=fixture['sha256']))
         binding['real_turns']=put('real.json',dict(common,source='real_turns',observations=real,sampling_frame=frame))
         binding['sources']={}
         for name in sources:
          p=root/name;p.parent.mkdir(parents=True,exist_ok=True)
          if not p.exists(): p.write_text('original source')
          binding['sources'][name]=hashlib.sha256(p.read_bytes()).hexdigest()
         put(R.POLICY,{'schema':'rule-recall-policy/v1','proofs':{rid:binding}})
         assert not R.retain_in_boot(rid,{},R.load_proofs(root)), 'valid proof rejected'
         (root/sources[0]).write_text('changed source')
         assert R.retain_in_boot(rid,{},R.load_proofs(root)), 'STALE SOURCE STILL REMOVES RULE FROM BOOT'
         binding.pop('sources')
         put(R.POLICY,{'schema':'rule-recall-policy/v1','proofs':{rid:binding}})
         assert R.retain_in_boot(rid,{},R.load_proofs(root)), 'MISSING SOURCE BINDINGS REMOVE RULE'

    def test_hook_internal_error_does_not_allow_an_effect(self):
        spec = importlib.util.spec_from_file_location("recall_hook", ROOT / "hooks/rule-boot-gate.py")
        hook = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(hook)
        output = io.StringIO()
        with patch("lib.rule_boot_gate.verdict", side_effect=ValueError("invalid state")), \
                patch("sys.stdin", io.StringIO(json.dumps({"tool_name": "Write", "hook_event_name": "PreToolUse"}))), \
                patch("sys.stdout", output), patch.object(hook, "log"):
            hook.main()
        self.assertEqual(json.loads(output.getvalue())["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_boot_without_length_evidence_cannot_unlock(self):
        spec = importlib.util.spec_from_file_location("boot_missing_length", ROOT / "ops/rule-boot-gate-selftest.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as folder:
            case = module.Case(str(ROOT), folder)
            case.stub("a", pages=1)
            case.arm()
            answer = case.boot(1)
            answer.pop("total_chars", None)
            case.fetch_cmd(module.abs_cmd(1), 1, boot=answer)
            self.assertTrue(module.denied(case.call(*module.READ)))

    def test_boot_cannot_escape_after_repeated_holds_or_outage(self):
        spec = importlib.util.spec_from_file_location("boot_test_cases", ROOT / "ops/rule-boot-gate-selftest.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as folder:
            case = module.Case(str(ROOT), folder)
            case.stub("a", pages=3)
            case.arm()
            for _ in range(8):
                self.assertTrue(module.denied(case.call(*module.READ)))
            pre, _ = case.fetch(1, answer="error")
            self.assertFalse(module.denied(pre))
            self.assertTrue(module.denied(case.call(*module.READ)))
            for page in (1, 2, 3):
                case.fetch(page)
            self.assertIsNone(case.call(*module.READ))
    def test_absent_or_vacuous_proof_keeps_full_boot(self):
        self.assertTrue(rule_recall.retain_in_boot("abcdef12", {}, {}))
        self.assertTrue(rule_recall.retain_in_boot("abcdef12", {}, {
            "abcdef12": {"benchmark": [], "real_turns": []}}))

    def test_real_turn_claim_needs_a_bound_unique_sampling_frame(self):
        rows = [{"source_ref": "turn", "requires": True}] * 200
        self.assertFalse(rule_recall.complete_real_frame({}, rows, ROOT))

    def test_one_missed_or_late_delivery_prevents_removal(self):
        row = {"requires": True, "complete_text": True, "before_event": True}
        proof = {"benchmark": [row], "real_turns": [dict(row, before_event=False)]}
        self.assertTrue(rule_recall.retain_in_boot("abcdef12", {}, {"abcdef12": proof}))

    def test_counts_and_asserted_verdict_are_not_proof(self):
        self.assertTrue(rule_recall.retain_in_boot("abcdef12", {}, {
            "abcdef12": {"recall": 1, "verdict": "pass", "benchmark": 100, "real_turns": 100}}))

    def test_current_boot_can_never_be_removed_by_a_route(self):
        self.assertTrue(rule_recall.retain_in_boot("abcdef12", {"always_on": True}, {}))

    def test_claimed_receipt_is_not_a_full_text_observation(self):
        self.assertFalse(rule_recall.observation_delivered({"requires": True,
            "complete_text": True, "before_event": True, "receipt_id": "claimed"}, "abcdef12", "rule text"))

    def test_generator_retains_unproven_action_rule(self):
        spec = importlib.util.spec_from_file_location("boot_sync", ROOT / "ops/sync-rule-boot-classes.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        doc = {"rules": {"abcdef12": {"class": "b", "always_on": False,
               "chars": 360, "summary": "Test event rule", "when": "before test event"}}}
        self.assertIn('"on": true', module.render(doc))
        self.assertGreater(module.estimate(doc)[0], 2800)

    def test_receipt_does_not_count_a_selector_match_as_delivery(self):
        summary = rule_recall.delivery_counts([{"at": "2026-10-05T12:00:00Z",
            "matched": ["abcdef12"], "delivered": [], "overflow": ["abcdef12"]}],
            ["abcdef12"], "2026-10-05T13:00:00Z", 14)
        self.assertEqual(summary["counts"]["abcdef12"], 0)

    def test_known_full_text_receipt_formats_are_counted(self):
        for schema in ("rule-jit-trigger-delivery/v1", "rule-delivery-preuse-reselection/v1"):
            with self.subTest(schema=schema):
                self.assertEqual(rule_recall.delivered_ids({"schema": schema,
                    "rules": [{"id": "abcdef12", "statement": "Binding text"}]}), ["abcdef12"])

    def test_eval_grades_availability_without_erasing_jit_labels(self):
        spec = importlib.util.spec_from_file_location("recall_eval", ROOT / "evals/rule-delivery/run_eval.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        case = {"id": "example", "split": "test", "input_sha256": "input"}
        obs = module.observe(case, [], boot_ids={"abcdef12"})
        self.assertEqual(obs["available"], ["abcdef12"])
        self.assertEqual(obs["delivered"], [])

    def test_health_cannot_clear_from_missing_or_invalid_logs(self):
        self.assertFalse(rule_recall.delivery_counts([], ["abcdef12"],
                         "2026-10-05T13:00:00Z", 14)["readable"])

    def test_missing_event_routes_deliver_before_the_call(self):
        routes = rule_routes.load_routes(ROOT)
        for rid, tool, args in [
            ("2a3ff869", "Bash", {"command": "./run.sh call update-document-status '{}'"}),
            ("4dd44fd7", "mcp__carr__update_document_status", {}),
            ("14181e60", "Write", {"file_path": "DNA/test.md"}),
            ("4a53ff82", "Edit", {"file_path": "lib/test.py"}),
            ("5409731b", "Write", {"file_path": "migrations/test.sql"}),
            ("24e10ee8", "Bash", {"command": "git -C /tmp/example commit -F /tmp/message"}),
        ]:
            with self.subTest(rule=rid):
                self.assertIn(rid, rule_routes.matched_rule_ids(routes, tool, args))


if __name__ == "__main__":
    unittest.main()
