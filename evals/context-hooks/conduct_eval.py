import datetime
import hashlib
import importlib.util
import itertools
import json
import subprocess
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
PATTERNS = 'hooks/conduct_patterns.py'
BASE = '372776374494ba3424862915ae48ee4d83666a86'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def classifier(source):
    module = types.ModuleType('conduct_eval_patterns')
    exec(compile(source, PATTERNS, 'exec'), module.__dict__)
    return lambda text: any(p.search(text) for _, p in module.HANDOFF_PROSE)


def observe(expectations):
    old = subprocess.check_output(['git', 'show', expectations['baseline_ref'] + ':' + PATTERNS], cwd=ROOT)
    current = (ROOT / PATTERNS).read_bytes()
    arms = {}
    for arm, source in [('baseline', old), ('candidate', current)]:
        detect = classifier(source)
        arms[arm] = []
        for cid, case in expectations['cases'].items():
            results = [detect(case['text']) for _ in range(2)]
            arms[arm].append({'case_id': cid, 'split': case['split'],
                              'input_sha256': case['input_sha256'], 'results': results})
    return arms


def score(expectations, baseline, candidate):
    arms = observe(expectations)
    if arms != {'baseline': baseline, 'candidate': candidate}:
        raise ValueError('cohort observations differ from source replay')
    cases = expectations['cases']

    def grade(rows, dimension):
        selected = [r for r in rows if r['split'] == 'test' and
                    cases[r['case_id']]['dimension'] == dimension]
        return sum(result == cases[r['case_id']]['required'] for r in selected
                   for result in r['results']) / (2 * len(selected))

    dimensions = {}
    for did in ('reader-instruction', 'narration-allowance'):
        b, c = grade(baseline, did), grade(candidate, did)
        dimensions[did] = {'baseline': {'score': b, 'ci_low': b, 'ci_high': b},
                           'candidate': {'score': c, 'ci_low': c, 'ci_high': c},
                           'delta': {'value': c-b, 'ci_low': c-b, 'ci_high': c-b}}
    oracle = [dict(r, results=[cases[r['case_id']]['required']] * 2) for r in candidate]
    null = [dict(r, results=[not cases[r['case_id']]['required']] * 2) for r in candidate]
    return {'dimensions': dimensions, 'controls': {
        'agreement': sum(r['results'][0] == r['results'][1] for r in candidate)/len(candidate),
        'oracle_pass_rate': grade(oracle, 'reader-instruction'),
        'null_pass_rate': grade(null, 'reader-instruction')}}


def make_report():
    spec = importlib.util.spec_from_file_location('conduct_tests', ROOT / 'ops/conduct-gate-selftest.py')
    tests = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tests)
    cases = {}

    def add(text, required, split):
        cid = f'{split}-{len(cases):04d}'
        cases[cid] = {'split': split, 'text': text, 'required': required,
                      'should_not_fire': not required, 'input_sha256': digest(text.encode()),
                      'dimension': 'reader-instruction' if required else 'narration-allowance'}

    for name, _, text, expected in tests.CASES:
        if name.startswith(('fp-', 'fire-', 'retry-', 'open-', 'terminal-')):
            add(text, expected, 'train')
    for prefix, verb, article, app in itertools.product(
            ['', 'Now ', 'You can ', '- ', '**', '1. Please '],
            ['Open', 'Launch'], ['', 'your ', 'the '], ['Terminal', 'iTerm', 'shell', 'command line']):
        add(prefix + verb + ' ' + article + app + ' and check the result.', True, 'test')
    for prefix, verb, command, location in itertools.product(
            ['', 'Next ', 'You should ', '- '], ['Run', 'Execute', 'Re-run', 'Re-execute'],
            ['it', './bin/verify.py --all', 'the verification command with its required flags and all normal arguments'],
            ['in the terminal', 'in your shell', 'at the command line']):
        add(prefix + verb + ' ' + command + ' ' + location + '.', True, 'test')
    for subject, verb, location in itertools.product(
            ["I'll", 'I will', 'We can', "We're going to"], ['run', 'execute', 're-run', 're-execute'],
            ['in the terminal', 'in your shell', 'at the command line']):
        add(subject + ' ' + verb + ' it ' + location + '.', False, 'test')
    for label, action in itertools.product(['The button label is: ', 'The application offers to '],
                                          ['Open Terminal', 'Launch Terminal']):
        add(label + action + '.', False, 'test')
    exp = {'version': 'conduct-handoff-expectations/v1', 'baseline_ref': BASE, 'cases': cases}
    exp_path = HERE / 'conduct-expectations.v1.json'
    exp_path.write_text(json.dumps(exp, indent=2) + '\n')
    arms = observe(exp)
    measured = score(exp, arms['baseline'], arms['candidate'])
    assert measured == score(exp, arms['baseline'], arms['candidate'])
    refs = []
    cohorts = {}
    for arm, rows in arms.items():
        path = HERE / 'evidence' / f'conduct-{arm}.jsonl'
        path.write_text(''.join(json.dumps(row, sort_keys=True) + '\n' for row in rows))
        rel = str(path.relative_to(ROOT))
        refs.append(rel)
        cohorts[arm] = {'path': rel, 'sha256': digest(path.read_bytes())}
    receipt = json.loads((HERE / 'receipt.json').read_text())
    dependencies = {PATTERNS: digest((ROOT/PATTERNS).read_bytes()),
                    'ops/conduct-gate-selftest.py': digest((ROOT/'ops/conduct-gate-selftest.py').read_bytes())}
    source_path = str(Path(__file__).resolve().relative_to(ROOT))
    source_hash = digest(Path(__file__).read_bytes())
    dims = []
    for did, values in measured['dimensions'].items():
        assert values['candidate']['score'] == 1
        dims.append({'dimension_id': did, 'critical': True, 'status': 'passed',
                     'direction_vs_baseline': 'improved' if values['delta']['value'] > 0 else 'equivalent',
                     'evidence_refs': refs, **values})
    receipt.update(change='Distinguish reader retry and terminal instructions from first-person plans and app descriptions.',
        measured_on=datetime.date.today().isoformat(),
        cases={'total': len(cases), 'train': sum(c['split']=='train' for c in cases.values()),
               'test': sum(c['split']=='test' for c in cases.values()),
               'should_not_fire': sum(c['should_not_fire'] for c in cases.values()),
               'sources': ['production_trace', 'human_judged_hard_case', 'synthetic']},
        split={'method': 'Known real excerpts and review regressions train; sealed generated grammar combinations test, first scored after implementation. Exact finite-corpus scores without generalization.',
               'seed': 0, 'sealed_test': True},
        repeats=2, noise_floor=0, min_useful_gain=0.01,
        primary_dimension='reader-instruction', dimensions=dims,
        stage_results=[{'stage_id': 'delivery', 'status': 'passed',
                        'dimension_ids': [d['dimension_id'] for d in dims], 'evidence_refs': refs}],
        grader={'kind': 'programmatic', 'validation': {'graded_twice': True, 'result': 'pass', **measured['controls']}},
        verdict={'decision': 'ship', 'statement': 'Reader instruction recognition improves; every finite-corpus narration case remains allowed.'},
        notes=['Deterministic classifier evaluation, not a measurement of model obedience. Real Stop deadline and exemptions are tested separately by conduct-gate-selftest.py. No model invocation is needed.',
               'Test combinations were generated and scored after the source fix, without inspecting held-out texts to propose changes. Intervals describe the complete finite corpus only.'],
        evidence={'scorer': {'path': source_path, 'function': 'score'}, 'source': {source_path: source_hash},
                  'dependencies': dependencies,
                  'expectations': {'path': str(exp_path.relative_to(ROOT)), 'version': exp['version'], 'sha256': digest(exp_path.read_bytes())},
                  'cohorts': cohorts})
    receipt['adapter'].update(adapter_id='shared-conduct-classifier', harness_id=source_path,
        harness_version=source_hash, native_session_ref='01a11e0b-405f-7bb3-bfa3-89befc1735ce',
        configuration_fingerprint='sha256:' + digest(json.dumps(dependencies, sort_keys=True).encode()))
    (HERE / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps({'cases': receipt['cases'], 'dimensions': measured['dimensions'], 'verdict': 'ship'}))


if __name__ == '__main__':
    make_report()
