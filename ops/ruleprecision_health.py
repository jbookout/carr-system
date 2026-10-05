"""Audited live shadow samples and one response loop; never infer gold from selection."""

import fcntl
import json
import os
from pathlib import Path
import subprocess
import uuid
from datetime import datetime, timedelta, timezone
from lib.ruleprecision_shadow import selector_snapshot

PRECISION_FLOOR = 0.8
AVAILABILITY_FLOOR = 583 / 717
ACTION = ('on breach: open/update one deduplicated rule-delivery-precision loop; '
          'owner Claude orchestration lane; inspect omitted required rules and noisy action predicates, '
          'collect independent labels; verify >=80% sampled precision and >=583/717 '
          'availability on at least20 current live samples; auto-clear on that verified sample')


def evaluate(logs, labels, *, selector_digest, minimum=20):
    reviewed = {(row.get('input_sha256'), row.get('selector_digest')): row for row in labels
                if row.get('auditor') and row.get('boot_verified') is True}
    tp = fp = hits = owed = samples = unknown = 0
    seen = set()
    for row in logs:
        key = (row.get('input_sha256'), row.get('selector_digest'))
        if key in seen:
            continue
        seen.add(key)
        label = reviewed.get(key)
        if row.get('selector_digest') != selector_digest or label is None or row.get('error'):
            unknown += 1
            continue
        judged = set(label.get('judged_rules', []))
        gold = set(label.get('gold', []))
        boot = set(label.get('boot_ids', []))
        selected = set(row.get('candidate_ids', []))
        if not judged or not gold <= judged or not selected <= judged:
            unknown += 1
            continue
        jit = selected - boot
        tp += len(jit & gold)
        fp += len(jit - gold)
        hits += len(gold & (selected | boot))
        owed += len(gold)
        samples += 1
    precision = tp / (tp + fp) if tp + fp else None
    availability = hits / owed if owed else None
    status = ('UNKNOWN' if samples < minimum or precision is None or availability is None
              else 'OK' if precision >= PRECISION_FLOOR and availability >= AVAILABILITY_FLOOR
              else 'WARN')
    p = 'unknown' if precision is None else f'{precision:.1%}'
    a = 'unknown' if availability is None else f'{availability:.1%}'
    line = (f'{status} rule delivery precision — audited live counterfactual proxy {p}; '
            f'availability proxy {a}; samples {samples}/{minimum}, unlabelled {unknown}; '
            f'threshold >=80% and >={AVAILABILITY_FLOOR:.1%} · {ACTION}')
    return {'status': status, 'line': line, 'precision_proxy': precision,
            'availability_proxy': availability, 'samples': samples, 'unknown': unknown}


def _rows(path):
    rows = [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]
    if any(not isinstance(row, dict) for row in rows):
        raise ValueError('invalid live row')
    return rows


def _run_verb(repo, verb, payload):
    call = subprocess.run([str(Path(repo) / 'run.sh'), 'call', verb, json.dumps(payload)],
                          cwd=repo, capture_output=True, text=True, timeout=35)
    if call.returncode:
        raise RuntimeError('record response failed')
    return json.loads(call.stdout[call.stdout.index('{'):])


def _checked_call(run_verb, verb, payload):
    result = run_verb(verb, payload)
    if not isinstance(result, dict) or result.get('error') or (verb != 'read-loop' and result.get('ok') is not True):
        raise RuntimeError('record response unverified')
    if verb in ('update-loop', 'close-loop') and result.get('loop_id') != payload['loop_id']:
        raise RuntimeError('loop response identity unverified')
    if verb == 'close-loop' and result.get('status') != payload['resolution']:
        raise RuntimeError('loop closure unverified')
    return result


def respond(row, state_path, run_verb):
    state_path = Path(state_path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    with Path(str(state_path) + '.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = json.loads(state_path.read_text()) if state_path.exists() else {'loop_id': None}
        loop_id = state.get('loop_id')
        identity = state.get('identity') or str(uuid.uuid4())
        if not state.get('identity'):
            target = state_path.with_suffix('.tmp')
            target.write_text(json.dumps({**state, 'identity': identity}))
            os.replace(target, state_path)
        body = row['line']
        key = str(uuid.uuid5(uuid.NAMESPACE_URL, identity + body))
        if row['status'] != 'OK' and not loop_id:
            blocker_detail = (
                'Rule-delivery-precision source workflow must deliver a tested selector revision through a PR; '
                'the health monitor cannot rewrite deployed source.' if row['status'] == 'WARN' else
                'Independent live-turn auditor must supply current selector-bound labels and verified boot evidence; '
                'the health monitor cannot grade its own proposals.')
            answer = _checked_call(run_verb, 'add-loop', {
                'idempotency_key': key, 'kind': 'open_loop', 'domain': 'system', 'owner': 'claude',
                'body': 'Rule delivery precision shadow requires verified live samples. ' + body,
                'marker': 'none', 'blocker': 'other_lane', 'blocker_detail': blocker_detail})
            loop_id = answer.get('loop_id')
            if not loop_id:
                raise RuntimeError('loop creation unverified')
        elif loop_id and (row['status'] == 'OK' or state.get('body') != body):
            answer = _checked_call(run_verb, 'read-loop', {'loop_id': loop_id})
            if answer.get('loop_id') != loop_id or type(answer.get('version')) is not int:
                raise RuntimeError('loop version unverified')
            update = {'idempotency_key': key, 'loop_id': loop_id, 'base_version': answer['version']}
            if row['status'] == 'OK':
                _checked_call(run_verb, 'close-loop', {**update, 'resolution': 'done', 'outcome': body})
                loop_id = None
                identity = str(uuid.uuid4())
            else:
                _checked_call(run_verb, 'update-loop', {**update, 'body': body})
        target = state_path.with_suffix('.tmp')
        target.write_text(json.dumps({'loop_id': loop_id, 'identity': identity, 'body': body}))
        os.replace(target, state_path)


def health_row(repo, *, apply=False, now=None):
    repo = Path(repo)
    if os.environ.get('CARR_RULEPRECISION_SHADOW') != '1':
        return {'status': 'OFF', 'line': f'OFF rule delivery precision — shadow flag disabled · {ACTION}'}
    now = now or datetime.now(timezone.utc)
    try:
        _, selector_digest = selector_snapshot(repo)
        logs = _rows(repo / 'out/orch/ruleprecision/shadow.jsonl')
        labels = _rows(repo / 'out/orch/ruleprecision/live-labels.jsonl')
        fresh = []
        for row in logs:
            if not isinstance(row['ts'], str):
                raise ValueError('invalid live timestamp')
            stamp = datetime.fromisoformat(row['ts'].replace('Z', '+00:00'))
            if stamp.tzinfo and now - timedelta(hours=24) <= stamp <= now:
                fresh.append(row)
        result = evaluate(fresh, labels, selector_digest=selector_digest)
    except (OSError, ValueError, KeyError, TypeError):
        result = {'status': 'UNKNOWN', 'line': f'UNKNOWN rule delivery precision — live sample unreadable · {ACTION}'}
    if apply:
        try:
            respond(result, repo / 'out/orch/ruleprecision/health-loop.json',
                    lambda verb, args: _run_verb(repo, verb, args))
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
            result['line'] += ' · loop action FAILED'
            result['status'] = 'UNKNOWN'
    else:
        result['line'] += ' · read-only; loop action pending (CARR_RULEPRECISION_HEALTH_APPLY=1 to apply)'
    return result
