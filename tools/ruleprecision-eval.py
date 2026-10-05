"""Replay observed events through existing delivery and a shadow candidate."""

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from ops import rule_delivery_eval as delivery


def metrics(cases, selected, boot, active, lengths):
    tp = fp = hits = applications = tokens = unjudged = 0
    for case in cases:
        judged = set(case.get('judged_rules', active)) & active
        disputed = set(case.get('disputed', []))
        gold = set(case['gold']) & judged - disputed
        raw = set(selected[case['id']])
        ids = raw & judged - disputed
        jit = ids - boot
        tp += len(jit & gold)
        fp += len(jit - gold)
        hits += len(gold & (ids | boot))
        applications += len(gold)
        tokens += sum(lengths.get(rid, 0) / 4 for rid in raw)
        unjudged += len(raw - ids)
    return {'cases': len(cases), 'jit_tp': tp, 'jit_fp': fp,
            'precision': tp / (tp + fp) if tp + fp else None,
            'availability_hits': hits, 'applications': applications,
            'availability': hits / applications if applications else None,
            'text_tokens': tokens, 'unjudged': unjudged}


def event_payloads(case):
    yield {'hook_event_name': 'UserPromptSubmit', 'prompt': case['prompt']}
    for call in case['tool_calls']:
        yield {'hook_event_name': 'PreToolUse', 'tool_name': call['tool_name'],
               'tool_input': call.get('tool_input', {})}


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def observe(cases, selector=None):
    adapters = {row['name']: row['select'] for row in delivery.build_adapters(ROOT, jev='off')}
    selected = {}
    events = []
    for case in cases:
        union = set()
        for index, payload in enumerate(event_payloads(case)):
            prompt = payload['hook_event_name'] == 'UserPromptSubmit'
            event_case = {**case, 'prompt': payload['prompt'] if prompt else '',
                          'tool_calls': [] if prompt else [case['tool_calls'][index - 1]]}
            ids = adapters['prompt_compiled' if prompt else 'layered_triggers'](event_case)['rules']
            proposed = set(selector(payload, sorted(ids))) if selector else set(ids)
            union |= proposed
            events.append({'case_id': case['id'], 'event': index, 'today': sorted(ids),
                           'candidate': sorted(proposed),
                           'input_sha256': hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()})
        selected[case['id']] = union
    return selected, events


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases', default=str(ROOT / 'ops/fixtures/rule-delivery-eval/cases.v2.json'))
    parser.add_argument('--split', choices=['train', 'test'], default='train')
    parser.add_argument('--corpus', required=True)
    parser.add_argument('--contract', required=True)
    parser.add_argument('--candidate', help='Module exporting select(payload, baseline_ids)')
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    cases = delivery.load_cases(args.cases, split=args.split)
    corpus = json.loads(Path(args.corpus).read_text())['rules']
    active = {row['id'] for row in corpus}
    lengths = {row['id']: len(row['statement']) for row in corpus}
    boot = set(json.loads(Path(args.contract).read_text())['boot_ids'])
    selector = load_module(args.candidate, 'precision_candidate').select if args.candidate else None
    selected, events = observe(cases, selector)
    result = {'schema': 'ruleprecision-eval/v1', 'split': args.split,
              'case_sha256': hashlib.sha256(Path(args.cases).read_bytes()).hexdigest(),
              'metrics': metrics(cases, selected, boot, active, lengths),
              'events_count': len(events), 'errors': 0, 'jev_calls': 0,
              'case_results': {case['id']: sorted(selected[case['id']]) for case in cases},
              'events': events}
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=1) + '\n')
    print(json.dumps({key: result[key] for key in ['split', 'metrics', 'events_count', 'errors', 'jev_calls']}))


if __name__ == '__main__':
    main()
