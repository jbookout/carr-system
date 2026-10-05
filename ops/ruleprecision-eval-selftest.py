import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('precision_eval', ROOT / 'tools/ruleprecision-eval.py')
assert spec is not None and spec.loader is not None
ev = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ev)


class EvaluationTest(unittest.TestCase):
    def test_fixed_denominator_and_boot_overlap(self):
        cases = [{'id': 'a', 'gold': ['boot', 'needed'], 'disputed': []}]
        result = ev.metrics(cases, {'a': {'boot', 'needed', 'noise'}}, {'boot'},
                            {'boot', 'needed', 'noise'}, {'boot': 40, 'needed': 80, 'noise': 20})
        self.assertEqual(result['jit_tp'], 1)
        self.assertEqual(result['jit_fp'], 1)
        self.assertEqual(result['availability_hits'], 2)
        self.assertEqual(result['applications'], 2)
        self.assertEqual(result['text_tokens'], 35)

    def test_null_and_oracle_controls(self):
        cases = [{'id': 'a', 'gold': ['a', 'b'], 'disputed': []}]
        null = ev.metrics(cases, {'a': set()}, set(), {'a', 'b'}, {})
        oracle = ev.metrics(cases, {'a': {'a', 'b'}}, set(), {'a', 'b'}, {})
        self.assertEqual(null['availability'], 0)
        self.assertEqual(oracle['availability'], 1)
        self.assertEqual(oracle['precision'], 1)

    def test_unjudged_is_counted_separately(self):
        cases = [{'id': 'a', 'gold': ['a'], 'disputed': ['b'], 'judged_rules': ['a', 'b']}]
        result = ev.metrics(cases, {'a': {'a', 'b', 'new'}}, set(), {'a', 'b', 'new'}, {})
        self.assertEqual(result['jit_fp'], 0)
        self.assertEqual(result['unjudged'], 2)


if __name__ == '__main__':
    unittest.main()
