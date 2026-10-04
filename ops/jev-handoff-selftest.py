"""Exact-action handoff predicates; no model or credential."""
import importlib.util
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'hooks'))
from conduct_patterns import handoff_was_denied
spec=importlib.util.spec_from_file_location('handoff',ROOT/'ops/jev_handoff.py')
assert spec and spec.loader
handoff=importlib.util.module_from_spec(spec);spec.loader.exec_module(handoff)

class NoModel:
    def __getattr__(self,name): raise AssertionError('paid '+name)

class HandoffTests(unittest.TestCase):
    def test_exact_denial_does_not_exempt_different_action(self):
        self.assertFalse(handoff_was_denied('Run `python ops/install.py --apply`.', ['python ops/install.py --dry-run']))
        self.assertTrue(handoff_was_denied('Run `python ops/install.py --apply`.', ['python ops/install.py --apply']))

    def test_structured_allowed_capability_and_attempts(self):
        evidence={'action':'install','attempts':[],'permission':'allowed','capability':'available'}
        self.assertTrue(handoff.hands_off('please install',evidence=evidence,surface='stop',judge_module=NoModel()))
        for state,expected in [('completed','attempted'),('permission_denied','human_required'),('human_auth_required','human_required'),('timeout','needs_review')]:
            with self.subTest(state=state):
                self.assertEqual(handoff.evaluate_handoff({**evidence,'attempts':[{'action':'install','status':state}]})['status'],expected)
        self.assertEqual(handoff.evaluate_handoff({'action':'install','attempts':[]})['status'],'needs_review')

    def test_prose_without_evidence_abstains(self):
        for text in ['please install','The meeting went well.','']:
            self.assertIsNone(handoff.judge(text,surface='stop',judge_module=NoModel()))

    def test_latest_exact_attempt_resolves_prior_denial(self):
        result=handoff.evaluate_handoff({'action':'x','attempts':[{'action':'x','status':'permission_denied'},{'action':'x','status':'completed'}]})
        self.assertEqual(result['status'],'attempted')

if __name__=='__main__': unittest.main()
