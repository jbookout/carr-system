"""Reproduce deterministic TRAIN calibration without opening a held-out file."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib import rule_delivery_precision as selector
from ops import rule_delivery_eval as delivery


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def train_cases(path):
    path = Path(path)
    if path.suffix != '.jsonl' or any(word in path.name.lower() for word in ('test', 'heldout', 'sealed')):
        raise ValueError('Calibration accepts a separately extracted TRAIN JSONL only')
    rows = [json.loads(line) for line in path.read_text().splitlines() if line]
    if not rows or any(row.get('split') != 'train' for row in rows):
        raise ValueError('Every calibration case must carry split=train')
    return delivery.load_cases(path, split='train'), rows


def token_estimates(events, rows, lengths):
    groups = {row['id']: row.get('session_group', row['id']) for row in rows}
    totals = Counter()
    seen = defaultdict(set)
    for event in events:
        group = groups[event['case_id']]
        ids = set(event['candidate'])
        totals['event_full_text_estimate'] += sum(lengths.get(rid, 0) / 4 for rid in ids)
        fresh = ids - seen[group]
        totals['once_per_session_full_text_estimate'] += sum(lengths.get(rid, 0) / 4 for rid in fresh)
        seen[group].update(ids)
    totals['session_groups'] = len(set(groups.values()))
    totals['per_session_once_estimate'] = totals['once_per_session_full_text_estimate'] / totals['session_groups']
    return {**totals, 'method': 'statement characters/4; no rendered receipt/envelope overhead',
            'grouping': 'hashed session_group when supplied; otherwise each synthetic case is a session proxy',
            'limit': 'sampled events only; unobserved earlier/later session turns are absent'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases', required=True)
    parser.add_argument('--real-train')
    parser.add_argument('--corpus', required=True)
    parser.add_argument('--contract', required=True)
    parser.add_argument('--config', required=True)
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location('precision_metrics', ROOT / 'tools/ruleprecision-eval.py')
    assert spec is not None and spec.loader is not None
    evaluator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(evaluator)
    cases, rows = train_cases(args.cases)
    contract = json.loads(Path(args.contract).read_text())
    corpus = json.loads(Path(args.corpus).read_text())['rules']
    active = set(contract['active_ids'])
    boot = set(contract['boot_ids'])
    lengths = {rule['id']: len(rule['statement']) for rule in corpus}
    config = json.loads(Path(args.config).read_text())

    def propose(policy):
        return lambda payload, baseline: selector.select(ROOT, payload, baseline, sorted(boot),
                                                         {**policy, 'active_ids': sorted(active)})

    selected, events = evaluator.observe(cases, propose(config))
    raw_config = {key: value for key, value in config.items() if key != 'allow_ids'}
    raw_selected, _ = evaluator.observe(cases, propose(raw_config))
    counts = defaultdict(lambda: [0, 0])
    for case in cases:
        judged = set(case.get('judged_rules', active)) & active - set(case.get('disputed', []))
        for rid in set(raw_selected[case['id']]) & judged - boot:
            counts[rid][rid not in case['gold']] += 1
    allowed = set(config.get('allow_ids', active))
    result = {'schema': 'ruleprecision-calibration/v1', 'split': 'train',
              'source_sha256': digest(ROOT / 'lib/rule_delivery_precision.py'),
              'config_sha256': digest(args.config), 'case_sha256': digest(args.cases),
              'corpus_sha256': digest(args.corpus), 'jev_calls': 0, 'errors': 0,
              'objective': 'maximize relevant JIT deliveries / judged JIT deliveries subject to availability >=583/717',
              'method': 'finite action predicates followed by per-rule empirical accept/reject gates',
              'threshold_is_probability': False,
              'train': evaluator.metrics(cases, selected, boot, active, lengths),
              'train_tokens': token_estimates(events, rows, lengths),
              'per_rule': {}}
    for rule in corpus:
        rid = rule['id']
        tp, fp = counts[rid]
        decision = 'already_full_text_at_boot' if rid in boot else 'selected' if rid in allowed else 'dropped'
        result['per_rule'][rid] = {
            'decision': decision, 'train_tp': tp, 'train_fp': fp,
            'empirical_precision': tp / (tp + fp) if tp + fp else None,
            'condition': 'boot receipt excludes repeated text' if rid in boot else
                         'action predicate or current delivery, then calibrated per-rule gate',
            'rationale': 'retain live boot availability' if rid in boot else
                         'TRAIN candidate had >=0.50 empirical relevance; keep known action coverage and an availability margin' if rid in allowed else
                         'below TRAIN empirical relevance cutoff or no supported TRAIN delivery; shadow-only exclusion',
            'has_action_addition': rid in selector.ADDITIONS,
            'has_baseline_refinement': rid in selector.REFINEMENTS,
            'rule_summary': rule['statement'].split('\n', 1)[0][:180],
        }
    if args.real_train:
        real_cases, real_rows = train_cases(args.real_train)
        real_selected, real_events = evaluator.observe(real_cases, propose(config))
        result['real_train'] = evaluator.metrics(real_cases, real_selected, boot, active, lengths)
        result['real_train_tokens'] = token_estimates(real_events, real_rows, lengths)
        result['real_train_sha256'] = digest(args.real_train)
    Path(args.out).write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({key: result[key] for key in ('train', 'jev_calls', 'errors')}))


if __name__ == '__main__':
    main()
