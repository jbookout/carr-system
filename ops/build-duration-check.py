#!/usr/bin/env python3
"""Read GitHub build evidence. Only --record-loops writes, through record verbs.

Baseline: nearest-rank p90 of up to 30 prior successful workflow runs.
An interrupted or failed step at its configured deadline is timeout evidence;
short manual/concurrency cancellations are not. Missing evidence is unavailable.
The scheduled receipt is the health input, with a 20-minute freshness limit.
"""
from __future__ import annotations

import argparse
import base64
import concurrent.futures
import fcntl
import hashlib
import itertools
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
REPOS = ('jbookout/carr-system', 'jbookout/doctorcre-app', 'jbookout/software-factory')
sys.path.insert(0, str(ROOT))
from lib.carr_paths import canonical_checkout

STATE = Path(canonical_checkout()) / 'out/build-duration-check.json'
# The scheduler runs the canonical copy; receipts are judged against it, never the reader's copy.
DEPLOYED = Path(canonical_checkout()) / 'ops/build-duration-check.py'
FRESHNESS_SECONDS = 1200
ACTION = ('on breach: open/update one build-duration loop per workflow · owner orchestrator '
          '(Platform Engineer fixes workflow) · remediation inspect named job/step and remove '
          'the stall or duration regression · verify next run green at or below prior p90 '
          '· auto-clear after that run on each affected branch; unavailable/stale: '
          'orchestrator restore GitHub/record access or scheduled job, rerun checker')


def timestamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def seconds(start, end):
    return max(0, (timestamp(end) - timestamp(start)).total_seconds())


def duration(run, now):
    return seconds(run['run_started_at'], run['updated_at'] if run['status'] == 'completed' else now)


def baseline(workflow, run):
    previous = sorted((r for r in workflow['successes']
                       if r['id'] != run['id'] and r['conclusion'] == 'success'
                       and timestamp(r['run_started_at']) < timestamp(run['run_started_at'])),
                      key=lambda r: r['run_started_at'], reverse=True)[:30]
    values = sorted(duration(r, r['updated_at']) for r in previous)
    return (values[math.ceil(len(values) * .9) - 1] if values else None), len(values)


def name_matches(template, name):
    pattern = re.escape(str(template))
    pattern = re.sub(r'\\\$\\\{\\\{.*?\\\}\\\}', '.*', pattern)
    return re.fullmatch(pattern + r'(?: \(.*\))?', name) is not None


def resolved_job(key, config, job):
    matrix = config.get('strategy', {}).get('matrix', {})
    axes = {k: v for k, v in matrix.items() if k not in ('include', 'exclude')}
    combinations = [dict(zip(axes, values)) for values in itertools.product(*axes.values())] if axes else []
    combinations = [row for row in combinations if not any(
        all(row.get(k) == v for k, v in excluded.items()) for excluded in matrix.get('exclude', []))]
    originals = [dict(row) for row in combinations]
    for included in matrix.get('include', []):
        compatible = [row for row, original in zip(combinations, originals)
                      if all(k not in axes or original.get(k) == v for k, v in included.items())]
        if compatible:
            for row in compatible:
                row.update(included)
        else:
            combinations.append(included)
    if not matrix:
        return config if name_matches(config.get('name', key), job['name']) else None
    candidates = []
    for row in combinations:
        template = config.get('name', key)
        expanded = re.sub(r'\$\{\{\s*matrix\.(\w+)\s*\}\}',
                          lambda match: str(row.get(match[1], match[0])), template)
        if 'name' not in config:
            expanded += ' (' + ', '.join(str(v) for v in row.values()) + ')'
        if job['name'] == expanded:
            candidates.append(row)
    if len(candidates) != 1:
        return None
    return {**config, '_matrix': candidates[0]}


def job_definition(definition, job):
    matches = [resolved_job(key, value, job) for key, value in definition['jobs'].items()]
    matches = [value for value in matches if value is not None]
    if len(matches) != 1:
        raise ValueError('job timeout mapping unavailable: ' + job['name'])
    return matches[0]


def limit_seconds(value, matrix=None):
    if isinstance(value, str):
        direct = re.fullmatch(r'\$\{\{\s*matrix\.(\w+)\s*\}\}', value)
        choice = re.fullmatch(r"\$\{\{\s*matrix\.(\w+)\s*==\s*'([^']*)'\s*&&\s*(\d+)\s*\|\|\s*(\d+)\s*\}\}", value)
        if direct and matrix and direct[1] in matrix:
            value = matrix[direct[1]]
        elif choice and matrix and choice[1] in matrix:
            value = int(choice[3] if str(matrix[choice[1]]) == choice[2] else choice[4])
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ValueError('dynamic or invalid timeout requires resolved evidence')
    return value * 60


def display_name(configured):
    """GitHub's label for a step: its name, else `Run <action>` or `Run <first script line>`."""
    if configured.get('name'):
        return str(configured['name'])
    if configured.get('uses'):
        return 'Run ' + str(configured['uses'])
    lines = [line.strip() for line in str(configured.get('run', '')).splitlines() if line.strip()]
    return 'Run ' + lines[0] if lines else None


def configured_step(config, step):
    # Step numbers shift with Set up job and action Pre steps, so only the label identifies a step.
    steps = config.get('steps', [])
    matches = [s for s in steps if s.get('name') == step['name']]
    if not matches:
        matches = [s for s in steps if not s.get('name') and display_name(s) == step['name']]
    if len(matches) == 1:
        return matches[0]
    if any('timeout-minutes' in s for s in steps):
        raise ValueError('step timeout mapping unavailable: ' + step['name'])
    return {}


def job_evidence(workflow, run, now):
    jobs = workflow['jobs'][str(run['id'])]
    if not isinstance(jobs, list) or not jobs:
        raise ValueError('missing job evidence for run ' + str(run['id']))
    evidence = []
    for job in jobs:
        if job.get('conclusion') == 'skipped' or job['status'] == 'queued':
            continue
        if not job.get('started_at'):
            raise ValueError('missing job start: ' + job['name'])
        steps = job.get('steps')
        if not isinstance(steps, list):
            raise ValueError('missing step evidence: ' + job['name'])
        interrupted = [s for s in steps if s.get('conclusion') in ('cancelled', 'timed_out', 'failure')]
        if job['status'] != 'in_progress' and not interrupted and job.get('conclusion') not in ('timed_out', 'failure', 'cancelled'):
            continue
        definition = workflow['definitions'][run['head_sha']]
        config = job_definition(definition, job)
        timeout = limit_seconds(config.get('timeout-minutes', 360), config.get('_matrix'))
        if job['status'] == 'completed' and not job.get('completed_at'):
            raise ValueError('missing job completion: ' + job['name'])
        elapsed = seconds(job['started_at'], job.get('completed_at') or now)
        timed_out = job.get('conclusion') == 'timed_out' or any(s.get('conclusion') == 'timed_out' for s in interrupted)
        if job.get('conclusion') in ('cancelled', 'failure') and elapsed >= timeout:
            timed_out = True
        for step in interrupted:
            configured = configured_step(config, step)
            if 'timeout-minutes' not in configured:
                continue
            if not step.get('started_at') or not step.get('completed_at'):
                raise ValueError('missing interrupted step timestamps: ' + step['name'])
            step_timeout = limit_seconds(configured['timeout-minutes'], config.get('_matrix'))
            if seconds(step['started_at'], step['completed_at']) >= step_timeout:
                timed_out = True
                timeout = min(timeout, step_timeout)
        if timed_out:
            evidence.append({'kind': 'timeout', 'job': job['name'],
                             'steps': [s['name'] for s in interrupted],
                             'job_duration_seconds': elapsed, 'timeout_seconds': timeout})
        if job['status'] == 'in_progress' and elapsed > timeout * .8:
            evidence.append({'kind': 'near_timeout', 'job': job['name'],
                             'job_duration_seconds': elapsed, 'timeout_seconds': timeout})
    return evidence


def evaluate(snapshot, now):
    report = {'observed_at': now, 'status': 'OK', 'errors': list(snapshot.get('errors', [])),
              'workflows': []}
    for workflow in snapshot['workflows']:
        try:
            row = {key: workflow[key] for key in ('repo', 'id', 'name', 'path')}
            row.update(flags=[], recoveries={}, active_runs=[])
            runs = sorted(workflow['runs'], key=lambda r: (r['run_started_at'], r.get('run_attempt', 1)), reverse=True)
            by_branch = {}
            for run in runs:
                by_branch.setdefault(run['head_branch'], []).append(run)
            evidence = {}
            for branch, branch_runs in by_branch.items():
                recovered_history = False
                for run in branch_runs:
                    p90, samples = baseline(workflow, run)
                    measured = duration(run, now)
                    active = run['status'] != 'completed'
                    if active:
                        row['active_runs'].append({'branch': branch, 'run_id': run['id']})
                    if run['status'] == 'completed' and run['conclusion'] == 'success':
                        row['recoveries'].setdefault(branch, []).append({
                            'run_id': run['id'], 'run_attempt': run.get('run_attempt', 1),
                            'started_at': run['run_started_at'], 'run_url': run.get('html_url'),
                            'duration_seconds': measured, 'baseline_seconds': p90})
                        if p90 is not None and measured <= p90:
                            recovered_history = True
                            continue
                    if recovered_history and not active:
                        continue
                    flags = job_evidence(workflow, run, now)
                    evidence[run['id']] = flags
                    if p90 is not None and measured > p90 * 1.5:
                        flags = flags + [{'kind': 'slow', 'threshold_seconds': p90 * 1.5}]
                    for flag in flags:
                        row['flags'].append({**flag, 'run_id': run['id'], 'run_attempt': run.get('run_attempt', 1),
                            'branch': branch, 'started_at': run['run_started_at'], 'run_url': run['html_url'],
                            'duration_seconds': measured, 'baseline_seconds': p90, 'baseline_samples': samples})
            streak = []
            for run in by_branch.get('main', []):
                if run['status'] != 'completed':
                    continue
                flags = evidence.get(run['id'], [])
                if not any(f['kind'] == 'timeout' for f in flags):
                    break
                streak.append(run)
            if len(streak) >= 2:
                source = next(f for f in row['flags'] if f['kind'] == 'timeout' and f['branch'] == 'main')
                row['flags'].append({**source, 'kind': 'main_timeout_streak', 'count': len(streak)})
            report['workflows'].append(row)
        except (KeyError, ValueError, TypeError) as exc:
            report['errors'].append(f'{workflow.get("repo")}/{workflow.get("name")}: {exc}')
    if report['errors']:
        report['status'] = 'UNAVAILABLE'
    elif any(row['flags'] for row in report['workflows']):
        report['status'] = 'WARN'
    return report


def run_command(argv, timeout=30, **kwargs):
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                                **({'stdin': subprocess.DEVNULL} if 'input' not in kwargs else {}), **kwargs)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f'{Path(argv[0]).name} {type(exc).__name__}') from None
    if result.returncode:
        raise RuntimeError(f'{Path(argv[0]).name} exited {result.returncode}')
    return result.stdout


class GitHub:
    def __init__(self, since=None):
        self.since = since or (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        self.deadline = time.monotonic() + 300

    def api(self, endpoint):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError('GitHub scan exceeded 300s budget')
        return json.loads(run_command(['gh', 'api', endpoint], timeout=min(20, remaining)))

    def pages(self, endpoint, key, max_pages=20):
        rows = []
        for page in range(1, max_pages + 1):
            value = self.api(endpoint + ('&' if '?' in endpoint else '?') + f'per_page=100&page={page}')
            batch = value[key]
            rows.extend(batch)
            if len(batch) < 100:
                return rows
        raise RuntimeError('GitHub pagination exceeds scan bound')

    def recent_runs(self, prefix):
        rows = []
        for page in range(1, 101):
            response = self.api(prefix + f'?per_page=30&page={page}')
            batch = response['workflow_runs']
            if not isinstance(batch, list):
                raise ValueError('invalid recent-run response')
            rows.extend(batch)
            if len(batch) < 30 or (batch and all(timestamp(r.get('created_at', r['run_started_at'])) < timestamp(self.since)
                                                 for r in batch)):
                return rows
        raise RuntimeError('recent-run coverage incomplete within scan bound')

    def workflow(self, repo, definition):
        prefix = f'repos/{repo}/actions/workflows/{definition["id"]}/runs'
        recent = self.recent_runs(prefix)
        main_runs = self.api(prefix + '?branch=main&per_page=30')['workflow_runs']
        active = self.pages(prefix + '?status=in_progress', 'workflow_runs')
        runs = list({r['id']: r for r in recent + main_runs + active}.values())
        successes = self.api(prefix + '?status=success&per_page=100')['workflow_runs']
        row = {**definition, 'repo': repo, 'runs': runs, 'successes': successes, 'jobs': {}, 'definitions': {}}
        branches = set()
        needed = []
        for run in sorted(runs, key=lambda r: r['run_started_at'], reverse=True):
            branch = run['head_branch']
            p90, _ = baseline(row, run)
            if run['status'] == 'completed' and run['conclusion'] == 'success' and p90 is not None and duration(run, run['updated_at']) <= p90:
                branches.add(branch)
                continue
            if run['status'] != 'completed' or branch not in branches:
                needed.append(run)
        for run in needed:
            row['jobs'][str(run['id'])] = self.pages(
                f'repos/{repo}/actions/runs/{run["id"]}/attempts/{run.get("run_attempt", 1)}/jobs', 'jobs')
            sha = run['head_sha']
            needs_deadline = any(j['status'] == 'in_progress' or j.get('conclusion') in ('timed_out', 'cancelled', 'failure') or
                                 any(s.get('conclusion') in ('cancelled', 'timed_out', 'failure') for s in j.get('steps', []))
                                 for j in row['jobs'][str(run['id'])])
            if not needs_deadline:
                continue
            if sha not in row['definitions']:
                content = self.api(f'repos/{repo}/contents/{quote(definition["path"], safe="/")}?ref={sha}')
                raw = base64.b64decode(content['content']).decode()
                parsed = run_command(['node', '-e',
                    'const fs=require("fs"),yaml=require("yaml"); process.stdout.write(JSON.stringify(yaml.parse(fs.readFileSync(0,"utf8"))));'],
                    input=raw, cwd=ROOT / 'mcp-server')
                row['definitions'][sha] = json.loads(parsed)
        return row

    def collect(self):
        result = {'workflows': [], 'errors': []}
        definitions = []
        for repo in REPOS:
            try:
                definitions.extend((repo, w) for w in self.pages(f'repos/{repo}/actions/workflows', 'workflows')
                                   if w['state'] == 'active')
            except (RuntimeError, KeyError, ValueError, TypeError) as exc:
                result['errors'].append(f'{repo}: {exc}')
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            pending = {pool.submit(self.workflow, repo, w): (repo, w) for repo, w in definitions}
            for future in concurrent.futures.as_completed(pending):
                repo, workflow = pending[future]
                try:
                    result['workflows'].append(future.result())
                except (RuntimeError, KeyError, ValueError, TypeError) as exc:
                    result['errors'].append(f'{repo}/{workflow["name"]}: {exc}')
        return result


def mutation_key(verb, payload):
    return 'build-duration-' + hashlib.sha256(
        json.dumps([verb, payload], sort_keys=True).encode()).hexdigest()


def record_verb(verb, payload, timeout=30):
    raw = run_command([str(ROOT / 'run.sh'), 'call', verb, json.dumps(payload)], cwd=ROOT, timeout=timeout)
    result = json.loads(raw[raw.index('{'):])
    if result.get('error') or (verb in ('add-loop', 'update-loop', 'close-loop') and result.get('ok') is not True):
        raise RuntimeError('record verb ' + verb + ' refused: ' + str(result.get('error', 'unknown')))
    return result


def incident_state(existing):
    try:
        stored = json.loads(existing['source_note'])
        if not isinstance(stored, dict) or not isinstance(stored['pending'], dict) or not isinstance(stored['flags'], list):
            raise ValueError('invalid incident shape')
        for branch, alert in stored['pending'].items():
            if not isinstance(branch, str) or not isinstance(alert, dict):
                raise ValueError('invalid incident pending branch')
            timestamp(alert['started_at'])
            if type(alert['run_id']) is not int or type(alert['run_attempt']) is not int:
                raise ValueError('invalid incident run')
            if alert['baseline_seconds'] is not None and (type(alert['baseline_seconds']) not in (int, float)
                                                        or not math.isfinite(alert['baseline_seconds']) or alert['baseline_seconds'] <= 0):
                raise ValueError('invalid incident baseline')
        for flag in stored['flags']:
            for key in ('kind', 'branch', 'started_at', 'run_attempt', 'baseline_seconds',
                        'baseline_samples', 'duration_seconds', 'run_url'):
                if key not in flag:
                    raise ValueError('incomplete incident flag')
        if stored['pending'] and not stored['flags']:
            raise ValueError('missing incident evidence')
        return stored
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise ValueError('incident read unavailable: ' + str(exc)) from None


def reconcile(report, call):
    actions = []
    inventory = call('loop-board', {'kind': 'open_loop', 'domain': 'system',
                                     'search': '[build-duration:', 'limit': 300})['loops']
    if not isinstance(inventory, list) or len(inventory) >= 300:
        raise ValueError('incident inventory incomplete')
    existing_by_marker = {}
    for loop in inventory:
        text = (loop.get('label') or '') + ' ' + (loop.get('body') or '')
        match = re.search(r'\[build-duration:([^:]+):(\d+)\]', text)
        if not match:
            raise ValueError('invalid incident marker')
        marker = match[0]
        if marker in existing_by_marker:
            raise RuntimeError('duplicate workflow incidents: ' + marker)
        existing = call('read-loop', {'kind': 'open_loop', 'number': loop['number']})['loop']
        if existing['status'] != 'open' or marker not in existing['body']:
            raise RuntimeError('loop changed during readback: ' + marker)
        stored = incident_state(existing)
        existing_by_marker[marker] = (existing, stored)
        if not any(w['repo'] == match[1] and w['id'] == int(match[2]) for w in report['workflows']):
            report['workflows'].append({**stored.get('workflow', {}), 'repo': match[1], 'id': int(match[2]),
                'name': stored.get('workflow', {}).get('name', 'uncollected workflow ' + match[2]),
                'path': stored.get('workflow', {}).get('path', 'uncollected'),
                'flags': [], 'recoveries': {}, 'active_runs': []})
    for workflow in report['workflows']:
        marker = f'[build-duration:{workflow["repo"]}:{workflow["id"]}]'
        existing, stored = existing_by_marker.get(marker, (None, {'pending': {}, 'flags': []}))
        pending = dict(stored['pending'])
        current_flags = workflow['flags']
        changed_branches = {f['branch'] for f in current_flags}
        evidence = [f for f in stored.get('flags', []) if f['branch'] not in changed_branches]
        evidence.extend(workflow['flags'])
        for flag in workflow['flags']:
            prior = pending.get(flag['branch'])
            order = (flag['started_at'], flag['run_attempt'])
            if prior is None or order >= (prior['started_at'], prior['run_attempt']):
                pending[flag['branch']] = {key: flag[key] for key in
                                          ('started_at', 'run_id', 'run_attempt', 'baseline_seconds')}
                if prior and prior['baseline_seconds'] is not None:
                    pending[flag['branch']]['baseline_seconds'] = prior['baseline_seconds']
        recovered = []
        for branch, alert in list(pending.items()):
            if any(r['branch'] == branch for r in workflow.get('active_runs', [])):
                continue
            for green in workflow['recoveries'].get(branch, []):
                recovery_baseline = alert['baseline_seconds'] if alert['baseline_seconds'] is not None else green.get('baseline_seconds')
                if (recovery_baseline is not None
                        and (green['started_at'], green['run_attempt']) > (alert['started_at'], alert['run_attempt'])
                        and green['duration_seconds'] <= recovery_baseline):
                    recovered.append(f'{branch}: {green["run_url"]}, {green["duration_seconds"]:g}s <= '
                                     f'{recovery_baseline:g}s prior p90')
                    pending.pop(branch)
                    break
        evidence = [f for f in evidence if f['branch'] in pending]
        workflow['flags'] = evidence
        if pending and report['status'] != 'UNAVAILABLE':
            report['status'] = 'WARN'
        if not existing and not pending:
            continue
        if existing and not current_flags and not recovered:
            continue
        if existing and not pending:
            verb = 'close-loop'
            payload = {'loop_id': existing['loop_id'], 'base_version': int(existing['version']),
                       'resolution': 'done', 'outcome': 'Build recovered: ' + '; '.join(recovered)}
        else:
            lines = [f'{marker} {workflow["repo"]} / {workflow["name"]} ({workflow["path"]}).',
                     'Fix owner: Platform Engineer via orchestrator. Inspect the named interrupted job/step; '
                     'remove the stall or duration regression. Verify a new green run at or below prior p90 '
                     'on every affected branch; checker then auto-clears.']
            for flag in evidence:
                p90 = flag['baseline_seconds']
                baseline_text = f'{p90:g}s p90 ({flag["baseline_samples"]} successful runs)' if p90 is not None else 'unavailable (no prior successes)'
                lines.append(f'{flag["branch"]}: {flag["kind"]}, {flag["duration_seconds"]:g}s vs '
                             f'{baseline_text}; {flag["run_url"]}' +
                             (f'; job {flag["job"]}' if flag.get('job') else '') +
                             (f'; interrupted steps {", ".join(flag["steps"])}' if flag.get('steps') else '') +
                             (f'; deadline {flag["timeout_seconds"]:g}s, job elapsed {flag["job_duration_seconds"]:g}s' if 'timeout_seconds' in flag else '') +
                             (f'; {flag["count"]} consecutive main timeouts' if 'count' in flag else ''))
            lines.append('Waiting for recovery on: ' + ', '.join(sorted(pending)))
            payload = {'body': '\n'.join(lines), 'source_note': json.dumps({'pending': pending, 'flags': evidence,
                'workflow': {key: workflow[key] for key in ('repo', 'id', 'name', 'path')}}, sort_keys=True)}
            if existing:
                if all(existing.get(key) == value for key, value in payload.items()):
                    continue
                verb = 'update-loop'
                payload.update(loop_id=existing['loop_id'], base_version=int(existing['version']))
            else:
                verb = 'add-loop'
                payload.update(kind='open_loop', domain='system', owner='claude', marker='none',
                               blocker='other_lane', blocker_detail='Platform Engineer workflow repair lane, '
                               'dispatched by the orchestrator for ' + workflow['repo'] + '/' + workflow['name'])
        payload['idempotency_key'] = mutation_key(verb, payload)
        call(verb, payload)
        actions.append({'workflow': marker, 'action': verb})
    return actions


def health_line(report):
    flagged = [w for w in report['workflows'] if w['flags']]
    details = '; '.join(f'{w["repo"]}/{w["name"]}: ' + ', '.join(sorted({f['kind'] for f in w['flags']})) for w in flagged)
    if report['errors']:
        details += ('; ' if details else '') + '; '.join(report['errors'][:3])
    if report.get('note'):
        return f'{report["status"]} build duration · {report["note"]} · {ACTION}'
    return f'{report["status"]} build duration · {len(flagged)} workflow(s) flagged' + (f' · {details}' if details else '') + f' · {ACTION}'


def deployed_at(deployed=DEPLOYED):
    return datetime.fromtimestamp(deployed.stat().st_mtime, timezone.utc)


def awaiting_deployment(path, now, deployed=DEPLOYED):
    """A release cannot hard-fail on a monitor it has not deployed or not yet given one scan window."""
    if not deployed.exists():
        return 'monitor not deployed at canonical checkout'
    since = deployed_at(deployed)
    if not path.exists() and seconds(since.isoformat(), now) <= FRESHNESS_SECONDS:
        return f'checker deployed {since:%Y-%m-%dT%H:%MZ}; first scheduled receipt due within 20 minutes'
    return None


def read_receipt(path, now, deployed=DEPLOYED):
    report = json.loads(path.read_text())
    if not isinstance(report, dict) or report['status'] not in ('OK', 'WARN', 'UNAVAILABLE'):
        raise ValueError('invalid receipt status')
    if not isinstance(report['workflows'], list) or not isinstance(report['errors'], list):
        raise ValueError('invalid receipt collections')
    if any(not isinstance(e, str) for e in report['errors']):
        raise ValueError('invalid receipt errors')
    for row in report['workflows']:
        if not isinstance(row, dict) or not isinstance(row['flags'], list):
            raise ValueError('invalid receipt workflow')
        for key in ('repo', 'name', 'path'):
            if not isinstance(row.get(key), str) or not row[key]:
                raise ValueError('incomplete receipt workflow')
        if type(row.get('id')) is not int:
            raise ValueError('invalid receipt workflow id')
        for flag in row['flags']:
            if not isinstance(flag, dict) or not isinstance(flag.get('kind'), str):
                raise ValueError('invalid receipt flag')
    if report['status'] == 'OK' and (report['errors'] or any(w['flags'] for w in report['workflows'])):
        raise ValueError('green receipt contradicts evidence')
    # A receipt from the previous revision stays valid only until the deployed checker's first scan.
    if (report['source_sha256'] != hashlib.sha256(deployed.read_bytes()).hexdigest()
            and timestamp(report['observed_at']) >= deployed_at(deployed)):
        raise ValueError('receipt source differs from deployed checker')
    if timestamp(report['observed_at']) > timestamp(now) or seconds(report['observed_at'], now) > FRESHNESS_SECONDS:
        raise ValueError('scheduled receipt older than 20 minutes')
    cursor = report.get('scan_cursor')
    if cursor is not None and timestamp(cursor) > timestamp(report['observed_at']):
        raise ValueError('invalid receipt scan cursor')
    return report


def unavailable(now, error):
    return {'observed_at': now, 'status': 'UNAVAILABLE', 'workflows': [], 'errors': [error]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture', type=Path)
    parser.add_argument('--now')
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--health', action='store_true', help='read the scheduled receipt; never query or write records')
    parser.add_argument('--record-loops', action='store_true')
    parser.add_argument('--state-file', type=Path, default=STATE)
    args = parser.parse_args()
    now = args.now or datetime.now(timezone.utc).isoformat()
    if args.record_loops and (args.fixture or args.health):
        parser.error('--record-loops cannot use fixtures or health mode')
    lock = None
    if args.record_loops:
        args.state_file.parent.mkdir(parents=True, exist_ok=True)
        lock = args.state_file.with_suffix('.lock').open('a')
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock.close()
            return 0
    since = None
    cursor_error = None
    if args.record_loops and args.state_file.exists():
        try:
            previous = json.loads(args.state_file.read_text())
            if not isinstance(previous, dict):
                raise ValueError('invalid previous receipt')
            since = previous.get('scan_cursor')
            if since is not None and timestamp(since) > timestamp(now):
                raise ValueError('previous scan cursor is in the future')
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            since = None
            cursor_error = f'previous scan coverage unavailable: {exc}'
    if args.health and not args.fixture:
        try:
            pending = awaiting_deployment(args.state_file, now)
            report = ({'observed_at': now, 'status': 'PENDING', 'workflows': [], 'errors': [], 'note': pending}
                      if pending else read_receipt(args.state_file, now))
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            report = unavailable(now, f'scheduled receipt unavailable: {exc}')
    else:
        scan_started = now
        try:
            snapshot = json.loads(args.fixture.read_text()) if args.fixture else GitHub(since=since).collect()
            now = args.now or datetime.now(timezone.utc).isoformat()
            report = evaluate(snapshot, now)
        except (OSError, RuntimeError, ValueError, KeyError, TypeError, AttributeError) as exc:
            report = unavailable(now, f'build collection: {exc}')
        if cursor_error:
            report['errors'].append(cursor_error)
            report['status'] = 'UNAVAILABLE'
        if args.record_loops:
            record_deadline = time.monotonic() + 180

            def bounded_record(verb, payload):
                remaining = record_deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError('loop reconciliation exceeded 180s budget')
                return record_verb(verb, payload, timeout=min(30, remaining))

            try:
                report['loop_actions'] = reconcile(report, bounded_record)
            except (RuntimeError, ValueError, KeyError, TypeError, AttributeError) as exc:
                report['errors'].append(f'loop reconciliation: {exc}')
                report['status'] = 'UNAVAILABLE'
            report['source_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
            report['scan_cursor'] = scan_started if report['status'] != 'UNAVAILABLE' else since
            args.state_file.parent.mkdir(parents=True, exist_ok=True)
            temporary = args.state_file.with_suffix('.tmp')
            temporary.write_text(json.dumps(report, indent=2) + '\n')
            os.replace(temporary, args.state_file)
    if lock is not None:
        lock.close()
    if args.json:
        print(json.dumps(report, indent=2))
    elif args.health or report['status'] != 'OK':
        print(health_line(report))
        for error in report['errors']:
            print('UNAVAILABLE ' + error)
    return int(report['status'] not in ('OK', 'PENDING'))


if __name__ == '__main__':
    sys.exit(main())
