"""Grade the finite registry/catalog matrix, without running a model."""
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ROOM = ROOT / 'tools/room-bridge'


def observe(expectations, arm):
    sys.path.insert(0, str(ROOM))
    import codex_models
    import desks
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        registry_class = desks.Registry
        if arm == 'baseline':
            source = subprocess.check_output(['git', 'show', expectations['baseline_ref'] +
                ':tools/room-bridge/desks.py'], cwd=ROOT, text=True)
            module_path = root / 'baseline_desks.py'
            module_path.write_text(source)
            spec = importlib.util.spec_from_file_location('routing_baseline_desks', module_path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            registry_class = module.Registry
        rows = []
        for cid, case in expectations['cases'].items():
            value = case['input']
            registry_path = root / 'registry.json'
            registry_path.write_text(json.dumps({'desks': {'sol': value['entry']}}))
            (root / 'models_cache.json').write_text(json.dumps(value['catalog']))
            registry = registry_class(registry_path)
            entry = registry.entries()['sol']
            try:
                if arm == 'baseline':
                    model = registry.resolve('sol').get('model')
                else:
                    model = codex_models.resolve_model(entry.get('family'), {'CODEX_HOME': str(root)})
                result = {'model': model}
            except desks.DeskError as exc:
                result = {'error': exc.code}
            rows.append({'case_id': cid, 'split': case['split'],
                'input_sha256': case['input_sha256'], 'result': result,
                'metadata': {k: entry.get(k) for k in case['expected_metadata']}})
        return rows


def score(expectations, baseline, candidate):
    cases = expectations['cases']
    for arm, rows in (('baseline', baseline), ('candidate', candidate)):
        if rows != observe(expectations, arm):
            raise ValueError('cohort differs from independently replayed registry/catalog observations')
    def grade(rows, dimension):
        selected = [r for r in rows if r['split'] == 'test']
        return sum(r['result' if dimension == 'model-resolution' else 'metadata'] ==
            cases[r['case_id']]['expected_result' if dimension == 'model-resolution' else 'expected_metadata']
            for r in selected) / len(selected)
    dimensions = {}
    for name in ('model-resolution', 'registry-metadata'):
        b, c = grade(baseline, name), grade(candidate, name)
        dimensions[name] = {'baseline': {'score': b, 'ci_low': b, 'ci_high': b},
            'candidate': {'score': c, 'ci_low': c, 'ci_high': c},
            'delta': {'value': c-b, 'ci_low': c-b, 'ci_high': c-b}}
    oracle = [{**r, 'result': cases[r['case_id']]['expected_result']} for r in candidate]
    null = [{**r, 'result': {'model': 'null'}} for r in candidate]
    return {'dimensions': dimensions, 'controls': {'agreement': 1.0,
        'oracle_pass_rate': grade(oracle, 'model-resolution'),
        'null_pass_rate': grade(null, 'model-resolution')}}
