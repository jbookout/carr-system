#!/usr/bin/env python3
"""Exercise the checker at its fixture JSON command-line seam."""
import json
import copy
import base64
import importlib.util
import tempfile
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECK = ROOT / 'ops/build-duration-check.py'
FIXTURE = ROOT / 'ops/fixtures/build-duration/five-main-timeouts.json'
spec = importlib.util.spec_from_file_location('build_duration', CHECK)
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


class LoopStore:
    def __init__(self):
        self.loops = []
        self.writes = []

    def __call__(self, verb, args):
        if verb == 'loop-board':
            return {'loops': [{'number': l['number'], 'kind': l['kind'],
                               'label': l['body'].splitlines()[0], 'version': l['version']}
                              for l in self.loops if l['status'] == 'open' and args['search'] in l['body']]}
        if verb == 'read-loop':
            return {'loop': copy.deepcopy(next(l for l in self.loops if l['number'] == args['number']))}
        self.writes.append((verb, args))
        if verb == 'add-loop':
            self.loops.append({**args, 'loop_id': 'loop-1', 'number': '716', 'version': 1, 'status': 'open'})
            return {'ok': True, 'loop_id': 'loop-1'}
        loop = next(l for l in self.loops if l['loop_id'] == args['loop_id'])
        assert loop['version'] == args['base_version']
        loop.update(args)
        loop['version'] += 1
        if verb == 'close-loop':
            loop['status'] = 'done'
        return {'ok': True}


class BuildDurationTests(unittest.TestCase):
    def check_fixture(self, fixture=FIXTURE):
        proc = subprocess.run([sys.executable, str(CHECK), '--fixture', str(fixture),
                               '--now', '2026-10-05T17:00:00Z', '--json'],
                              capture_output=True, text=True, timeout=10)
        self.assertIn(proc.returncode, (0, 1), proc.stderr)
        return json.loads(proc.stdout)


    def fixture_data(self):
        return json.loads(FIXTURE.read_text())

    def report_data(self, data, now='2026-10-05T17:00:00Z'):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / 'runs.json'
            path.write_text(json.dumps(data))
            return self.check_fixture(path)

    def test_manual_cancel_breaks_main_timeout_streak(self):
        data = self.fixture_data()
        workflow = data['workflows'][0]
        latest = workflow['runs'][0]
        latest['updated_at'] = '2026-10-05T13:05:00Z'
        job = workflow['jobs']['105'][0]
        job['completed_at'] = latest['updated_at']
        job['steps'][0]['completed_at'] = latest['updated_at']
        report = self.report_data(data)
        kinds = [f['kind'] for f in report['workflows'][0]['flags']]
        self.assertNotIn('main_timeout_streak', kinds)
        timeout = next(f for f in report['workflows'][0]['flags'] if f['kind'] == 'timeout')
        self.assertEqual(timeout['run_id'], 104)

    def test_running_job_crosses_eighty_percent_not_queued_wall_time(self):
        data = self.fixture_data()
        w = data['workflows'][0]
        w['runs'] = w['runs'][:1]
        r = w['runs'][0]
        r.update(status='in_progress', conclusion=None)
        j = w['jobs']['105'][0]
        j.update(status='in_progress', conclusion=None, completed_at=None,
                 started_at='2026-10-05T16:31:59Z')
        j['steps'] = []
        flags = self.report_data(data)['workflows'][0]['flags']
        near = next(f for f in flags if f['kind'] == 'near_timeout')
        self.assertEqual(near['job_duration_seconds'], 1681)
        j['started_at'] = '2026-10-05T16:32:00Z'
        self.assertNotIn('near_timeout', [f['kind'] for f in self.report_data(data)['workflows'][0]['flags']])

    def test_p90_uses_thirty_prior_successes_and_excludes_the_candidate(self):
        data = self.fixture_data()
        w = data['workflows'][0]
        old = copy.deepcopy(w['successes'][-1])
        old.update(id=1, run_started_at='2026-10-01T00:00:00Z', updated_at='2026-10-01T05:00:00Z')
        w['successes'].append(old)
        current = copy.deepcopy(w['runs'][0])
        current.update(conclusion='success')
        w['successes'].insert(0, current)
        flag = self.report_data(data)['workflows'][0]['flags'][0]
        self.assertEqual(flag['baseline_seconds'], 600)
        self.assertEqual(flag['baseline_samples'], 30)

    def test_empty_baseline_is_visible_and_does_not_invent_a_slow_flag(self):
        data = self.fixture_data()
        data['workflows'][0]['successes'] = []
        report = self.report_data(data)
        flags = report['workflows'][0]['flags']
        self.assertNotIn('slow', [f['kind'] for f in flags])
        self.assertIsNone(flags[0]['baseline_seconds'])

    def test_green_inside_prior_baseline_removes_historical_flags(self):
        data = self.fixture_data()
        w = data['workflows'][0]
        green = copy.deepcopy(w['runs'][0])
        green.update(id=106, conclusion='success', run_started_at='2026-10-05T15:00:00Z',
                     updated_at='2026-10-05T15:09:00Z', html_url='green')
        w['runs'].insert(0, green)
        report = self.report_data(data)
        self.assertEqual(report['status'], 'OK')
        self.assertEqual(report['workflows'][0]['flags'], [])
        proc = subprocess.run([sys.executable, str(CHECK), '--fixture', str(FIXTURE), '--health'],
                              capture_output=True, text=True)
        for text in ('on breach:', 'owner orchestrator', 'remediation', 'verify', 'auto-clear'):
            self.assertIn(text, proc.stdout)

    def test_unmapped_job_is_unavailable_not_all_clear(self):
        data = self.fixture_data()
        data['workflows'][0]['jobs']['105'][0]['name'] = 'unmapped job'
        self.assertEqual(self.report_data(data)['status'], 'UNAVAILABLE')

    def test_health_facade_prints_bound_action_and_reports_breach(self):
        proc = subprocess.run([sys.executable, str(ROOT / 'tools/health-check.py'),
                               '--section', 'builds', '--fixture', str(FIXTURE)],
                              capture_output=True, text=True, timeout=10)
        self.assertEqual(proc.returncode, 1, proc.stderr)
        for text in ('WARN build duration', 'on breach:', 'owner orchestrator', 'auto-clear'):
            self.assertIn(text, proc.stdout)

    def test_schedule_runs_every_ten_minutes_through_the_existing_wrapper(self):
        import plistlib
        plist = plistlib.loads((ROOT / 'ops/launchd/com.carr.build-duration-check.plist').read_bytes())
        self.assertEqual([d['Minute'] for d in plist['StartCalendarInterval']], [0, 10, 20, 30, 40, 50])
        self.assertIn('{{REPO}}/bin/run-scheduled.sh', plist['ProgramArguments'])
        self.assertIn('--record-loops', plist['ProgramArguments'])
        self.assertFalse(plist['RunAtLoad'])
        services = json.loads((ROOT / 'ops/config/services.json').read_text())['services']
        self.assertTrue(any(s['key'] == 'build-duration-check' for s in services))

    def test_matrix_deadline_resolves_from_the_observed_job_name(self):
        data = self.fixture_data()
        w = data['workflows'][0]
        for definition in w['definitions'].values():
            job = definition['jobs']['canary']
            job['name'] = 'check ${{ matrix.lane }}'
            job['strategy'] = {'matrix': {'lane': ['gates', 'migration']}}
            job['timeout-minutes'] = "${{ matrix.lane == 'migration' && 35 || 20 }}"
        for jobs in w['jobs'].values():
            jobs[0]['name'] = 'check migration'
        self.assertEqual(self.report_data(data)['status'], 'WARN')

    def test_skipped_job_needs_no_deadline_mapping(self):
        data = self.fixture_data()
        data['workflows'][0]['jobs']['105'].append({'name': 'retired job', 'status': 'completed',
            'conclusion': 'skipped', 'started_at': '2026-10-05T13:00:00Z', 'steps': []})
        self.assertEqual(self.report_data(data)['status'], 'WARN')

    def test_a_manual_cancel_preserves_the_unrecovered_incident_evidence(self):
        report = self.check_fixture()
        store = LoopStore()
        checker.reconcile(report, store)
        before = store.loops[0]['body']
        report['status'] = 'OK'
        report['workflows'][0]['flags'] = []
        report['workflows'][0]['recoveries'] = {}
        checker.reconcile(report, store)
        self.assertEqual(store.loops[0]['body'], before)
        self.assertEqual(report['status'], 'WARN')
        self.assertTrue(report['workflows'][0]['flags'])

    def test_stale_or_malformed_health_receipt_is_unavailable(self):
        with tempfile.TemporaryDirectory() as raw:
            receipt = Path(raw) / 'receipt.json'
            receipt.write_text(json.dumps({'status': 'OK', 'observed_at': '2026-10-05T16:00:00Z',
                                          'workflows': [], 'errors': []}))
            proc = subprocess.run([sys.executable, str(CHECK), '--health', '--state-file', str(receipt),
                                   '--now', '2026-10-05T17:00:00Z'], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 1)
            self.assertIn('UNAVAILABLE', proc.stdout)
            receipt.write_text('{broken')
            proc = subprocess.run([sys.executable, str(CHECK), '--health', '--state-file', str(receipt)],
                                  capture_output=True, text=True)
            self.assertEqual(proc.returncode, 1)
            self.assertIn('UNAVAILABLE', proc.stdout)

    def test_live_reader_covers_three_repositories_and_paginated_jobs(self):
        data = self.fixture_data()['workflows'][0]
        calls = []

        class FixtureGitHub(checker.GitHub):
            def api(self, endpoint):
                calls.append(endpoint)
                if '/contents/' in endpoint:
                    definition = data['definitions'][endpoint.split('ref=')[1]]
                    return {'content': base64.b64encode(json.dumps(definition).encode()).decode()}
                if '/jobs?' in endpoint:
                    run_id = endpoint.split('/runs/')[1].split('/')[0]
                    if endpoint.endswith('page=2'):
                        return {'jobs': [{'name': 'skipped tail', 'status': 'completed', 'conclusion': 'skipped'}]}
                    return {'jobs': data['jobs'][run_id] + [{'name': 'skip', 'status': 'completed',
                                                          'conclusion': 'skipped'}] * 99}
                if '/runs?' in endpoint:
                    if 'status=in_progress' in endpoint:
                        return {'workflow_runs': []}
                    if 'status=success' in endpoint:
                        return {'workflow_runs': data['successes']}
                    return {'workflow_runs': data['runs']}
                return {'workflows': [{'id': 1, 'name': data['name'], 'path': data['path'], 'state': 'active'}]}

        snapshot = FixtureGitHub().collect()
        self.assertEqual(snapshot['errors'], [])
        report = checker.evaluate(snapshot, '2026-10-05T17:00:00Z')
        self.assertEqual({w['repo'] for w in report['workflows']}, set(checker.REPOS))
        self.assertEqual(len(report['workflows']), 3)
        self.assertTrue(any('/jobs?' in c and c.endswith('page=2') for c in calls))
        self.assertTrue(any('branch=main' in c for c in calls))
        self.assertEqual({f['count'] for w in report['workflows'] for f in w['flags']
                          if f['kind'] == 'main_timeout_streak'}, {5})

    def test_clean_run_is_quiet_and_fixture_cannot_write_loops(self):
        data = {'workflows': []}
        with tempfile.TemporaryDirectory() as raw:
            fixture = Path(raw) / 'clean.json'
            fixture.write_text(json.dumps(data))
            proc = subprocess.run([sys.executable, str(CHECK), '--fixture', str(fixture)],
                                  capture_output=True, text=True)
            self.assertEqual((proc.returncode, proc.stdout), (0, ''))
            proc = subprocess.run([sys.executable, str(CHECK), '--fixture', str(fixture), '--record-loops'],
                                  capture_output=True, text=True)
            self.assertEqual(proc.returncode, 2)
            self.assertIn('cannot use fixtures', proc.stderr)

    def test_loop_retries_update_one_incident_and_green_clears_it(self):
        report = self.check_fixture()
        store = LoopStore()
        checker.reconcile(report, store)
        checker.reconcile(report, store)
        self.assertEqual([v for v, a in store.writes], ['add-loop'])
        body = store.loops[0]['body']
        for text in ('Main canary', '2116s', '600s', 'Platform Engineer', 'actions/runs/105'):
            self.assertIn(text, body)
        self.assertEqual(store.loops[0]['owner'], 'claude')
        changed = copy.deepcopy(report)
        changed['workflows'][0]['flags'][0]['run_url'] += '?attempt=2'
        checker.reconcile(changed, store)
        self.assertEqual([v for v, a in store.writes], ['add-loop', 'update-loop'])
        healthy = copy.deepcopy(report)
        row = healthy['workflows'][0]
        row['flags'] = []
        row['recoveries'] = {'other': {'run_id': 106, 'run_attempt': 1, 'started_at': '2026-10-05T15:00:00Z',
                                    'duration_seconds': 500, 'baseline_seconds': 600, 'run_url': 'green'}}
        checker.reconcile(healthy, store)
        self.assertEqual(store.loops[0]['status'], 'open')
        row['recoveries']['main'] = row['recoveries'].pop('other')
        checker.reconcile(healthy, store)
        self.assertEqual(store.loops[0]['status'], 'done')
        self.assertIn('500s', store.writes[-1][1]['outcome'])

    def test_five_cancelled_main_jobs_are_one_workflow_incident(self):
        report = self.check_fixture()
        self.assertEqual(len(report['workflows']), 1)
        flags = report['workflows'][0]['flags']
        self.assertEqual({flag['kind'] for flag in flags}, {'timeout', 'slow', 'main_timeout_streak'})
        streak = next(flag for flag in flags if flag['kind'] == 'main_timeout_streak')
        self.assertEqual(streak['count'], 5)
        self.assertEqual(streak['duration_seconds'], 2116)
        self.assertEqual(streak['baseline_seconds'], 600)
        self.assertEqual(streak['run_url'], 'https://github.com/jbookout/carr-system/actions/runs/105')
        self.assertEqual(report['status'], 'WARN')


if __name__ == '__main__':
    unittest.main()
