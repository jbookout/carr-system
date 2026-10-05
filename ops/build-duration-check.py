#!/usr/bin/env python3
"""Read GitHub build evidence. Only --record-loops writes, through record verbs.

Baseline: nearest-rank p90 of up to 30 prior successful workflow runs.
A cancelled step at its configured job/step deadline is timeout evidence;
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
from datetime import datetime, timezone
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
REPOS = ('jbookout/carr-system', 'jbookout/doctorcre-app', 'jbookout/software-factory')
STATE = ROOT / 'out/build-duration-check.json'
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
    if isinstance(config.get('timeout-minutes', 360), (int, float)):
        return config if name_matches(config.get('name', key), job['name']) else None
    matrix = config.get('strategy', {}).get('matrix', {})
    axes = {k: v for k, v in matrix.items() if k not in ('include', 'exclude')}
    combinations = [dict(zip(axes, values)) for values in itertools.product(*axes.values())]
    combinations = [row for row in combinations if not any(
        all(row.get(k) == v for k, v in excluded.items()) for excluded in matrix.get('exclude', []))]
    for included in matrix.get('include', []):
        compatible = [row for row in combinations if all(k not in axes or row.get(k) == v
                                                       for k, v in included.items())]
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
                          lambda match: str(row[match[1]]), template)
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


def job_evidence(workflow, run, now):
    jobs = workflow['jobs'][str(run['id'])]
    evidence = []
    for job in jobs:
        if not job.get('started_at') or job['status'] == 'queued' or job.get('conclusion') == 'skipped':
            continue
        interrupted = [s for s in job.get('steps', [])
                       if s.get('conclusion') in ('cancelled', 'timed_out')]
        if job['status'] != 'in_progress' and not interrupted and job.get('conclusion') != 'timed_out':
            continue
        definition = workflow['definitions'][run['head_sha']]
        config = job_definition(definition, job)
        timeout = limit_seconds(config.get('timeout-minutes', 360), config.get('_matrix'))
        elapsed = seconds(job['started_at'], job.get('completed_at') or now)
        timed_out = job.get('conclusion') == 'timed_out' or any(
            s.get('conclusion') == 'timed_out' for s in interrupted)
        if interrupted and job.get('conclusion') == 'cancelled' and elapsed >= timeout:
            timed_out = True
        for step in interrupted:
            for configured in config.get('steps', []):
                if ('timeout-minutes' in configured and configured.get('name') == step['name']
                        and step.get('started_at') and step.get('completed_at')
                        and seconds(step['started_at'], step['completed_at']) >=
                        limit_seconds(configured['timeout-minutes'], config.get('_matrix'))):
                    timed_out = True
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
            row.update(flags=[], recoveries={})
            runs = sorted(workflow['runs'], key=lambda r: (r['run_started_at'], r.get('run_attempt', 1)), reverse=True)
            by_branch = {}
            for run in runs:
                by_branch.setdefault(run['head_branch'], []).append(run)
            evidence = {}
            for branch, branch_runs in by_branch.items():
                for run in branch_runs:
                    p90, samples = baseline(workflow, run)
                    measured = duration(run, now)
                    if run['status'] == 'completed' and run['conclusion'] == 'success' and p90 is not None and measured <= p90:
                        row['recoveries'][branch] = {'run_id': run['id'], 'run_attempt': run.get('run_attempt', 1),
                                                    'started_at': run['run_started_at'], 'run_url': run.get('html_url'),
                                                    'duration_seconds': measured, 'baseline_seconds': p90}
                        break
                    flags = job_evidence(workflow, run, now)
                    evidence[run['id']] = flags
                    if p90 is not None and measured > p90 * 1.5:
                        flags = flags + [{'kind': 'slow', 'threshold_seconds': p90 * 1.5}]
                    for flag in flags:
                        item = {**flag, 'run_id': run['id'], 'run_attempt': run.get('run_attempt', 1),
                                'branch': branch, 'started_at': run['run_started_at'], 'run_url': run['html_url'],
                                'duration_seconds': measured, 'baseline_seconds': p90, 'baseline_samples': samples}
                        if not any(f['kind'] == item['kind'] and f['branch'] == branch for f in row['flags']):
                            row['flags'].append(item)
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
    def __init__(self):
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

    def workflow(self, repo, definition):
        prefix = f'repos/{repo}/actions/workflows/{definition["id"]}/runs'
        recent = self.api(prefix + '?per_page=30')['workflow_runs']
        main_runs = self.api(prefix + '?branch=main&per_page=30')['workflow_runs']
        active = self.pages(prefix + '?status=in_progress', 'workflow_runs')
        runs = list({r['id']: r for r in recent + main_runs + active}.values())
        successes = self.api(prefix + '?status=success&per_page=100')['workflow_runs']
        row = {**definition, 'repo': repo, 'runs': runs, 'successes': successes, 'jobs': {}, 'definitions': {}}
        branches = {}
        needed = []
        for run in sorted(runs, key=lambda r: r['run_started_at'], reverse=True):
            branch = run['head_branch']
            if branches.get(branch):
                continue
            p90, _ = baseline(row, run)
            if run['status'] == 'completed' and run['conclusion'] == 'success' and p90 is not None and duration(run, run['updated_at']) <= p90:
                branches[branch] = True
                continue
            needed.append(run)
        for run in needed:
            row['jobs'][str(run['id'])] = self.pages(
                f'repos/{repo}/actions/runs/{run["id"]}/attempts/{run.get("run_attempt", 1)}/jobs', 'jobs')
            sha = run['head_sha']
            needs_deadline = any(j['status'] == 'in_progress' or j.get('conclusion') == 'timed_out' or
                                 any(s.get('conclusion') in ('cancelled', 'timed_out') for s in j.get('steps', []))
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
            except (RuntimeError, KeyError, ValueError) as exc:
                result['errors'].append(f'{repo}: {exc}')
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            pending = {pool.submit(self.workflow, repo, w): (repo, w) for repo, w in definitions}
            for future in concurrent.futures.as_completed(pending):
                repo, workflow = pending[future]
                try:
                    result['workflows'].append(future.result())
                except (RuntimeError, KeyError, ValueError) as exc:
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


def reconcile(report, call):
    actions = []
    for workflow in report['workflows']:
        marker = f'[build-duration:{workflow["repo"]}:{workflow["id"]}]'
        loops = call('loop-board', {'kind': 'open_loop', 'domain': 'system',
                                   'search': marker, 'limit': 300})['loops']
        loops = [loop for loop in loops if marker in (loop.get('label') or loop.get('body') or '')]
        if len(loops) > 1:
            raise RuntimeError('duplicate workflow incidents: ' + marker)
        existing = call('read-loop', {'kind': 'open_loop', 'number': loops[0]['number']})['loop'] if loops else None
        if existing and (existing['status'] != 'open' or marker not in existing['body']):
            raise RuntimeError('loop changed during readback: ' + marker)
        stored = json.loads(existing['source_note']) if existing else {'pending': {}}
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
            green = workflow['recoveries'].get(branch)
            recovery_baseline = alert['baseline_seconds'] if alert['baseline_seconds'] is not None else (green or {}).get('baseline_seconds')
            if (green and recovery_baseline is not None
                    and (green['started_at'], green['run_attempt']) >
                    (alert['started_at'], alert['run_attempt'])
                    and green['duration_seconds'] <= recovery_baseline):
                recovered.append(f'{branch}: {green["run_url"]}, {green["duration_seconds"]:g}s <= '
                                 f'{recovery_baseline:g}s prior p90')
                pending.pop(branch)
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
            payload = {'body': '\n'.join(lines), 'source_note': json.dumps({'pending': pending, 'flags': evidence}, sort_keys=True)}
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
    return f'{report["status"]} build duration · {len(flagged)} workflow(s) flagged' + (f' · {details}' if details else '') + f' · {ACTION}'


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
            return 0
        if args.state_file.exists():
            try:
                json.loads(args.state_file.read_text())
            except (OSError, ValueError):
                pass
    if args.health and not args.fixture:
        try:
            report = json.loads(args.state_file.read_text())
            age = seconds(report['observed_at'], now)
            if timestamp(report['observed_at']) > timestamp(now) or age > 1200:
                raise ValueError('scheduled receipt older than 20 minutes')
        except (OSError, ValueError, KeyError):
            report = {'status': 'UNAVAILABLE', 'workflows': [], 'errors': ['missing/stale scheduled receipt']}
    else:
        snapshot = json.loads(args.fixture.read_text()) if args.fixture else GitHub().collect()
        now = args.now or datetime.now(timezone.utc).isoformat()
        report = evaluate(snapshot, now)
        if args.record_loops:
            record_deadline = time.monotonic() + 180

            def bounded_record(verb, payload):
                remaining = record_deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError('loop reconciliation exceeded 180s budget')
                return record_verb(verb, payload, timeout=min(30, remaining))

            try:
                report['loop_actions'] = reconcile(report, bounded_record)
            except (RuntimeError, ValueError, KeyError) as exc:
                report['errors'].append(f'loop reconciliation: {exc}')
                report['status'] = 'UNAVAILABLE'
            args.state_file.parent.mkdir(parents=True, exist_ok=True)
            temporary = args.state_file.with_suffix('.tmp')
            temporary.write_text(json.dumps(report, indent=2) + '\n')
            os.replace(temporary, args.state_file)
    if args.json:
        print(json.dumps(report, indent=2))
    elif args.health or report['status'] != 'OK':
        print(health_line(report))
        for error in report['errors']:
            print('UNAVAILABLE ' + error)
    return int(report['status'] != 'OK')


if __name__ == '__main__':
    sys.exit(main())
