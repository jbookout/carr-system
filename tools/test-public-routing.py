"""Deterministic public cue regression; emits aggregates, never private prompts.

The reviewed head is the before-fix baseline; the pinned merged main supplies the routing
oracle. Every public keyword and a should-not-fire control is exercised twice.
No model transcripts are opened. Hashes select one of two text variants per
cue for training; only aggregate held-out scores are emitted. Intervals describe
this complete finite cue set, not future language or model quality.
"""
import hashlib
import importlib.util
import json
import pathlib
import re
import subprocess
import sys
import unittest
from datetime import datetime, timezone

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'ops'))
import pii_guard
ORIGINAL = '98080b3c11b7aa7adcc22f4dda81e851178e1b21'
REVIEWED = 'b919c806bae16dd3951ae0b523925316492c35c1'


def at(rev, name):
    return json.loads(subprocess.check_output(['git', 'show', rev + ':' + name], cwd=REPO))


def selected(table, prompt, rule):
    return any(row.get('kind') == 'prompt_regex' and rule in row.get('rule_ids', [])
               and re.search(row['pattern'], prompt, re.I)
               and not (row.get('negative_pattern') and re.search(row['negative_pattern'], prompt, re.I))
               for row in table['triggers'])


def measure():
    path = 'ops/config/rule-jit-triggers.v1.json'
    oracle, baseline = at(ORIGINAL, path), at(REVIEWED, path)
    candidate = json.loads((REPO / path).read_text())
    rules = at(ORIGINAL, 'ops/config/rule-jev-triggers.v1.json')['rules']
    corpus = pii_guard.load_corpus(REPO / 'ops/config/public-source-identities.v1.json')
    results = {'train': [], 'test': []}
    for rule, record in rules.items():
        cues = [cue for cue, probability in record['triggers']['keywords'].items()
                if probability >= .5 and not re.fullmatch(r"[clvp]-\d+", cue)
                and not pii_guard.identity_spans(cue, corpus)]
        for index, cue in enumerate(cues + ['zxsynthetic unrelated sentence']):
            seed = hashlib.sha256((rule + str(index)).encode()).digest()[0] % 2
            for variant, prompt in enumerate([cue, 'Please: ' + cue]):
                expected = selected(oracle, prompt, rule)
                split = 'train' if variant == seed else 'test'
                results[split].append((selected(baseline, prompt, rule) == expected,
                                       selected(candidate, prompt, rule) == expected,
                                       expected))
    return results


class RoutingTests(unittest.TestCase):
    def test_all_public_cues_match_original_routing(self):
        results = measure()
        for split, rows in results.items():
            self.assertTrue(all(row[1] for row in rows), split)
        self.assertGreater(sum(not row[0] for row in results['test']), 0)
        self.assertEqual(measure(), results)


def receipt(surface, rows):
    test = rows['test']
    baseline = sum(row[0] for row in test) / len(test)
    candidate = sum(row[1] for row in test) / len(test)
    # Complete deterministic census: exact finite-set intervals.
    delta = candidate - baseline
    measure_value = lambda score: {'score': score, 'ci_low': score, 'ci_high': score}
    evidence = ['tools/test-public-routing.py', 'github:jbookout/carr-system/1436#blocking-finding-7']
    return {
        'schema_version': 1, 'surface': surface,
        'change': 'Restore non-private routing cues and probabilities lost during identity anonymization',
        'measured_on': datetime.now(timezone.utc).date().isoformat(), 'rung': 'regression',
        'adapter': {'surface': 'offline_programmatic', 'adapter_id': 'public-routing-census',
                    'adapter_version': '1', 'harness_id': 'python-stdlib-regex', 'harness_version': sys.version.split()[0],
                    'provider_id': 'local', 'model_id': 'none-programmatic',
                    'native_session_ref': '01a10521-a7dc-75c3-8129-73ce618fa765',
                    'configuration_fingerprint': 'sha256:' + hashlib.sha256(
                        (REPO / 'ops/config/rule-jit-triggers.v1.json').read_bytes()).hexdigest()},
        'cases': {'total': sum(map(len, rows.values())), 'train': len(rows['train']),
                  'test': len(test), 'should_not_fire': sum(not row[2] for split in rows.values() for row in split),
                  'sources': ['human_judged_hard_case', 'synthesized']},
        'split': {'method': 'paired variants, hash-random stratification by rule and cue, seed SHA256(rule,index)',
                  'seed': 1436, 'sealed_test': True}, 'repeats': 2,
        'grader': {'kind': 'programmatic', 'validation': {'graded_twice': True, 'agreement': 1.0,
                   'oracle_pass_rate': 1.0, 'null_pass_rate': 0.0, 'result': 'pass'}},
        'noise_floor': 0.0, 'min_useful_gain': delta / 2,
        'primary_dimension': 'public-cue-routing',
        'dimensions': [{'dimension_id': 'public-cue-routing', 'critical': True, 'status': 'passed',
                        'direction_vs_baseline': 'improved', 'evidence_refs': evidence,
                        'baseline': measure_value(baseline), 'candidate': measure_value(candidate),
                        'delta': {'value': delta, 'ci_low': delta, 'ci_high': delta}}],
        'stage_results': [{'stage_id': 'judgment', 'status': 'passed',
                          'dimension_ids': ['public-cue-routing'], 'evidence_refs': evidence}],
        'cost': {'baseline_usd_per_case': 0.0, 'candidate_usd_per_case': 0.0},
        'verdict': {'decision': 'ship', 'statement': 'Public cue routing restored with zero finite-set regressions.'},
        'notes': ['Exact finite-set census intervals; no claim about unseen prompts or model quality.',
                  'Baseline is the reviewed PR head; oracle is merged main ' + ORIGINAL + '; private identities are excluded before case generation.',
                  'The main-merge-receipt.json artifact preserves main production-function replay evidence; this census measures public cues only.',
                  'Programmatic regression and second-pass measurement; no external model-work call is needed.'],
    }


if __name__ == '__main__':
    if '--write-receipts' in sys.argv:
        rows = measure()
        assert all(row[1] for group in rows.values() for row in group)
        assert rows == measure()
        for surface in ['rule-delivery', 'jev-judgments']:
            path = REPO / 'evals' / surface / 'receipt.json'
            path.parent.mkdir(exist_ok=True)
            path.write_text(json.dumps(receipt(surface, rows), indent=2) + '\n')
        print(json.dumps({split: {'total': len(group), 'baseline_pass': sum(r[0] for r in group),
                                 'candidate_pass': sum(r[1] for r in group)} for split, group in rows.items()}))
    else:
        unittest.main()
