#!/usr/bin/env python3
"""Exercise the checker at its fixture JSON command-line seam."""
import json
import copy
import base64
import importlib.util
import tempfile
import subprocess
import sys
import os
import hashlib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECK = ROOT / 'ops/build-duration-check.py'
FIXTURE = ROOT / 'ops/fixtures/build-duration/five-main-timeouts.json'
# Receipts are judged against the canonical (scheduler-run) checker, so tests deploy one of their own.
CANONICAL = tempfile.TemporaryDirectory()
(Path(CANONICAL.name) / 'ops').mkdir()
(Path(CANONICAL.name) / 'ops/build-duration-check.py').write_bytes(CHECK.read_bytes())
os.utime(Path(CANONICAL.name) / 'ops/build-duration-check.py', (0, 0))
os.environ['CARR_ROOT'] = CANONICAL.name
spec =importlib.util.spec_from_file_location('build_duration', CHECK)
assert spec is not None and spec.loader is not None
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


class LoopStore:
    def __init__(self):
        self.loops = []
        self.writes = []

    def __call__(self, verb, args):
        if verb == 'loop-board':
            return {'loops': [{'number': l['number'], 'kind': l['kind'],
                               'label': l.get('label', l['body'].splitlines()[0]), 'body': l['body'], 'version': l['version']}
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


class ReviewRegressionTests(unittest.TestCase):
    def data(self):
        return json.loads(FIXTURE.read_text())

    def evaluate(self, data):
        return checker.evaluate(data, '2026-10-05T17:00:00Z')

    def green(self, workflow, ident, start, end):
        run = copy.deepcopy(workflow['runs'][0])
        run.update(id=ident, conclusion='success', status='completed', run_started_at=start,
                   updated_at=end, html_url='green-' + str(ident))
        workflow['runs'].insert(0, run)
        return run

    def health(self, canonical, now='2026-10-05T17:00:00Z'):
        return subprocess.run([sys.executable, str(CHECK), '--health', '--now', now],
                              env={**os.environ, 'CARR_ROOT': str(canonical)}, capture_output=True, text=True)

    def deploy(self, canonical, source, deployed_at):
        deployed = canonical / 'ops/build-duration-check.py'
        deployed.parent.mkdir(parents=True, exist_ok=True)
        deployed.write_bytes(source)
        stamp = checker.timestamp(deployed_at).timestamp()
        os.utime(deployed, (stamp, stamp))

    def test_1_release_before_monitor_deployment_is_not_a_hard_error(self):
        with tempfile.TemporaryDirectory() as raw:
            proc = self.health(Path(raw))
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn('not deployed', proc.stdout)

    def test_1_newly_deployed_checker_gets_one_freshness_window(self):
        with tempfile.TemporaryDirectory() as raw:
            canonical = Path(raw)
            self.deploy(canonical, CHECK.read_bytes(), '2026-10-05T16:50:00Z')
            self.assertEqual(self.health(canonical).returncode, 0)
            self.assertEqual(self.health(canonical, '2026-10-05T17:11:00Z').returncode, 1)
            (canonical / 'out').mkdir()
            report = {'status': 'WARN', 'observed_at': '2026-10-05T16:45:00Z', 'scan_cursor': '2026-10-05T16:45:00Z',
                      'workflows': [{'repo': 'r', 'id': 1, 'name': 'n', 'path': 'p', 'flags': [{'kind': 'slow'}]}],
                      'errors': [], 'source_sha256': '0' * 64}
            (canonical / 'out/build-duration-check.json').write_text(json.dumps(report))
            proc = self.health(canonical)
            self.assertEqual(proc.returncode, 1)
            self.assertTrue(proc.stdout.startswith('WARN'), proc.stdout)
            report['observed_at'] = '2026-10-05T16:55:00Z'
            (canonical / 'out/build-duration-check.json').write_text(json.dumps(report))
            self.assertIn('receipt source differs', self.health(canonical).stdout)

    def test_1_health_reads_canonical_receipt_from_another_checkout(self):
        with tempfile.TemporaryDirectory() as raw:
            canonical = Path(raw)
            self.deploy(canonical, CHECK.read_bytes(), '2026-10-05T10:00:00Z')
            (canonical / 'out').mkdir()
            report = {'status': 'OK', 'observed_at': '2026-10-05T16:59:00Z',
                      'scan_cursor': '2026-10-05T16:59:00Z', 'workflows': [], 'errors': [],
                      'source_sha256': hashlib.sha256(CHECK.read_bytes()).hexdigest()}
            receipt = canonical / 'out/build-duration-check.json'
            receipt.write_text(json.dumps(report))
            proc = subprocess.run([sys.executable, str(CHECK), '--health', '--now', '2026-10-05T17:00:00Z'],
                                  env={**os.environ, 'CARR_ROOT': str(canonical)}, capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            report['source_sha256'] = '0' * 64
            receipt.write_text(json.dumps(report))
            proc = subprocess.run([sys.executable, str(CHECK), '--health', '--now', '2026-10-05T17:00:00Z'],
                                  env={**os.environ, 'CARR_ROOT': str(canonical)}, capture_output=True, text=True)
            self.assertEqual(proc.returncode, 1)
            self.assertIn('UNAVAILABLE', proc.stdout)

    def test_1_secondary_health_skips_primary_monitor(self):
        with tempfile.TemporaryDirectory() as raw:
            home = Path(raw)
            (home / '.config/carr').mkdir(parents=True)
            (home / '.config/carr/machine-role.json').write_text('{"role":"secondary"}')
            proc = subprocess.run([sys.executable, str(ROOT / 'tools/health-check.py'), '--section', 'builds'],
                                  env={**os.environ, 'HOME': raw}, capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn('secondary', proc.stdout)

    def test_3_later_green_does_not_hide_active_job(self):
        data = self.data()
        w = data['workflows'][0]
        w['runs'][0].update(status='in_progress', conclusion=None)
        w['jobs']['105'][0].update(status='in_progress', conclusion=None, completed_at=None)
        self.green(w, 106, '2026-10-05T15:00:00Z', '2026-10-05T15:09:00Z')
        flags = self.evaluate(data)['workflows'][0]['flags']
        self.assertTrue(any(f['kind'] == 'near_timeout' and f['run_id'] == 105 for f in flags))

    def test_4_failed_step_at_configured_deadline_is_timeout(self):
        for named in (True, False):
            with self.subTest(named=named):
                data = self.data()
                w = data['workflows'][0]
                w['runs'] = w['runs'][:1]
                w['runs'][0].update(conclusion='failure', updated_at='2026-10-05T13:05:00Z')
                job = w['jobs']['105'][0]
                job.update(conclusion='failure', completed_at='2026-10-05T13:05:00Z')
                job['steps'][0].update(conclusion='failure', started_at=job['started_at'], completed_at=job['completed_at'], number=3)
                configured = w['definitions'][w['runs'][0]['head_sha']]['jobs']['canary']
                configured['steps'] = [{'uses': 'actions/checkout@v4'},
                    {'run': 'canary command', 'timeout-minutes': 5}]
                if named:
                    configured['steps'][1]['name'] = job['steps'][0]['name']
                else:
                    job['steps'][0]['name'] = 'Run canary command'
                flags = self.evaluate(data)['workflows'][0]['flags']
                self.assertEqual(next(f for f in flags if f['kind'] == 'timeout')['timeout_seconds'], 300)
                job['steps'][0]['completed_at'] = '2026-10-05T13:01:00Z'
                self.assertEqual(self.evaluate(data)['status'], 'OK')

    def test_4_unnamed_step_maps_by_display_name_not_shifted_number(self):
        data = self.data()
        w = data['workflows'][0]
        w['runs'] = w['runs'][:1]
        w['runs'][0].update(conclusion='failure', updated_at='2026-10-05T13:05:00Z')
        job = w['jobs']['105'][0]
        job.update(conclusion='failure', completed_at='2026-10-05T13:05:00Z')
        job['steps'][0].update(name='Run canary command', conclusion='failure', number=4,
                               started_at=job['started_at'], completed_at=job['completed_at'])
        w['definitions'][w['runs'][0]['head_sha']]['jobs']['canary']['steps'] = [
            {'uses': 'some/action-with-pre@v1'},
            {'run': 'canary command\nsecond line', 'timeout-minutes': 5},
            {'run': 'cleanup'}]
        flags = self.evaluate(data)['workflows'][0]['flags']
        self.assertEqual(next(f for f in flags if f['kind'] == 'timeout')['timeout_seconds'], 300)

    def test_5_missing_jobs_are_unavailable(self):
        data = self.data()
        w = data['workflows'][0]
        w['successes'] = []
        w['jobs'] = {key: [] for key in w['jobs']}
        self.assertEqual(self.evaluate(data)['status'], 'UNAVAILABLE')

    def test_5_null_incident_note_is_a_named_read_failure(self):
        store = LoopStore()
        checker.reconcile(self.evaluate(self.data()), store)
        store.loops[0]['source_note'] = None
        with self.assertRaisesRegex(ValueError, 'incident'):
            checker.reconcile(self.evaluate(self.data()), store)

    def test_6_include_only_and_step_matrix_deadlines(self):
        for step_limit in (False, True):
            with self.subTest(step_limit=step_limit):
                data = self.data()
                w = data['workflows'][0]
                for definition in w['definitions'].values():
                    job = definition['jobs']['canary']
                    job.update(name='check ${{ matrix.lane }}', strategy={'matrix': {'include': [
                        {'lane': 'gates', 'limit': 20}, {'lane': 'migration', 'limit': 35}]}})
                    job['timeout-minutes'] = 60 if step_limit else '${{ matrix.limit }}'
                    if step_limit:
                        job['steps'] = [{'name': w['jobs']['105'][0]['steps'][0]['name'],
                                         'timeout-minutes': '${{ matrix.limit }}'}]
                for jobs in w['jobs'].values():
                    jobs[0]['name'] = 'check gates'
                report = self.evaluate(data)
                self.assertEqual(report['errors'], [])
                flag = next(f for f in report['workflows'][0]['flags'] if f['kind'] == 'timeout')
                self.assertEqual(flag['timeout_seconds'], 1200)

    def test_7_recovery_uses_captured_baseline_for_all_candidates(self):
        store = LoopStore()
        checker.reconcile(self.evaluate(self.data()), store)
        data = self.data()
        w = data['workflows'][0]
        w['successes'] = [self.green(w, 200, '2026-10-04T10:00:00Z', '2026-10-04T10:20:00Z')]
        w['runs'] = [r for r in w['runs'] if r['id'] != 200]
        self.green(w, 106, '2026-10-05T14:00:00Z', '2026-10-05T14:08:00Z')
        self.green(w, 107, '2026-10-05T15:00:00Z', '2026-10-05T15:15:00Z')
        checker.reconcile(self.evaluate(data), store)
        self.assertEqual(store.loops[0]['status'], 'done')
        self.assertIn('480s', store.writes[-1][1]['outcome'])

    def test_8_absent_workflow_retains_unresolved_incident(self):
        store = LoopStore()
        checker.reconcile(self.evaluate(self.data()), store)
        report = self.evaluate({'workflows': []})
        checker.reconcile(report, store)
        self.assertEqual(report['status'], 'WARN')
        self.assertTrue(report['workflows'][0]['flags'])

    def test_9_descriptive_label_does_not_defeat_body_marker(self):
        store = LoopStore()
        checker.reconcile(self.evaluate(self.data()), store)
        store.loops[0]['label'] = 'Fix canary duration'
        report = self.evaluate(self.data())
        report['workflows'][0]['flags'][0]['duration_seconds'] += 1
        checker.reconcile(report, store)
        self.assertEqual([v for v, _ in store.writes], ['add-loop', 'update-loop'])

    def test_6_excluded_axes_keep_their_original_include_binding(self):
        data = self.data()
        w = data['workflows'][0]
        for definition in w['definitions'].values():
            job = definition['jobs']['canary']
            job.update(name='check ${{ matrix.lane }}', strategy={'matrix': {
                'lane': ['gates', 'migration'], 'exclude': [{'lane': 'gates'}],
                'include': [{'lane': 'migration', 'limit': 35}]}})
            job['timeout-minutes'] = '${{ matrix.limit }}'
        for jobs in w['jobs'].values():
            jobs[0]['name'] = 'check migration'
        report = self.evaluate(data)
        self.assertEqual(report['errors'], [])
        self.assertEqual(next(f for f in report['workflows'][0]['flags'] if f['kind'] == 'timeout')['timeout_seconds'], 2100)

    def test_5_scheduled_failure_replaces_previous_green_and_preserves_cursor(self):
        from unittest.mock import patch
        data = self.data()
        store = LoopStore()
        checker.reconcile(self.evaluate(data), store)
        store.loops[0]['source_note'] = None
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / 'receipt.json'
            path.write_text(json.dumps({'status': 'OK', 'scan_cursor': '2026-10-05T12:00:00Z'}))
            with patch.object(checker.GitHub, 'collect', return_value=data), patch.object(checker, 'record_verb', lambda verb, args, **kwargs: store(verb, args)), patch.object(sys, 'argv', [str(CHECK), '--record-loops', '--state-file', str(path), '--now', '2026-10-05T17:00:00Z']):
                self.assertEqual(checker.main(), 1)
            report = json.loads(path.read_text())
            self.assertEqual(report['status'], 'UNAVAILABLE')
            self.assertEqual(report['scan_cursor'], '2026-10-05T12:00:00Z')
            self.assertIn('incident', report['errors'][0])

    def test_5_malformed_nested_flags_are_unavailable(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / 'receipt.json'
            report = self.evaluate(self.data())
            report.update(source_sha256=hashlib.sha256(CHECK.read_bytes()).hexdigest(),
                          scan_cursor=report['observed_at'])
            report['workflows'][0]['flags'] = [None]
            path.write_text(json.dumps(report))
            proc = subprocess.run([sys.executable, str(CHECK), '--health', '--state-file', str(path),
                                   '--now', report['observed_at']], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 1)
            self.assertIn('UNAVAILABLE', proc.stdout)
            self.assertNotIn('Traceback', proc.stderr)

    def test_5_malformed_receipt_shapes_never_report_green(self):
        for data in (None, [], {'status': 'OK', 'workflows': None, 'errors': []},
                     {'status': 'OK', 'workflows': [], 'errors': ['lost evidence']},
                     {'status': 'WARN', 'workflows': [{}], 'errors': []}):
            with self.subTest(data=data), tempfile.TemporaryDirectory() as raw:
                path = Path(raw) / 'receipt.json'
                path.write_text(json.dumps(data))
                proc = subprocess.run([sys.executable, str(CHECK), '--health', '--state-file', str(path)],
                                      capture_output=True, text=True)
                self.assertEqual(proc.returncode, 1)
                self.assertIn('UNAVAILABLE', proc.stdout)

    def test_10_exhausted_coverage_budget_is_unavailable(self):
        data = self.data()['workflows'][0]
        class Reader(checker.GitHub):
            def api(self, endpoint):
                if '/runs?' in endpoint:
                    return {'workflow_runs': [data['runs'][0]] * 30}
                return {'workflows': [{'id': 1, 'name': data['name'], 'path': data['path'], 'state': 'active'}]}
        snapshot = Reader(since='2026-10-05T12:00:00Z').collect()
        self.assertEqual(self.evaluate(snapshot)['status'], 'UNAVAILABLE')
        self.assertTrue(any('coverage incomplete' in e for e in snapshot['errors']))

    def test_10_invalid_previous_cursor_cannot_authorize_green_coverage(self):
        from unittest.mock import patch
        for previous in (None, {'scan_cursor': '2026-10-06T17:00:00Z'}):
            with self.subTest(previous=previous), tempfile.TemporaryDirectory() as raw:
                path = Path(raw) / 'receipt.json'
                path.write_text(json.dumps(previous))
                with patch.object(checker.GitHub, 'collect', return_value={'workflows': []}), patch.object(checker, 'record_verb', lambda verb, args, **kwargs: {'loops': []}), patch.object(sys, 'argv', [str(CHECK), '--record-loops', '--state-file', str(path), '--now', '2026-10-05T17:00:00Z']):
                    self.assertEqual(checker.main(), 1)
                report = json.loads(path.read_text())
                self.assertEqual(report['status'], 'UNAVAILABLE')
                self.assertIsNone(report['scan_cursor'])

    def test_10_scan_paginates_completed_non_main_runs(self):
        data = self.data()['workflows'][0]
        calls = []
        class Reader(checker.GitHub):
            def api(self, endpoint):
                calls.append(endpoint)
                if '/contents/' in endpoint:
                    return {'content': base64.b64encode(json.dumps(data['definitions'][endpoint.split('ref=')[1]]).encode()).decode()}
                if '/jobs?' in endpoint:
                    return {'jobs': data['jobs'][endpoint.split('/runs/')[1].split('/')[0]]}
                if 'status=success' in endpoint:
                    return {'workflow_runs': data['successes']}
                if 'branch=main' in endpoint or 'status=in_progress' in endpoint:
                    return {'workflow_runs': []}
                if 'page=2' in endpoint:
                    run = copy.deepcopy(data['runs'][0])
                    run['head_branch'] = 'timed-out-branch'
                    return {'workflow_runs': [run]}
                runs = []
                for n in range(30):
                    run = copy.deepcopy(data['runs'][0])
                    run.update(id=200+n, conclusion='success', head_branch='busy',
                               run_started_at='2026-10-05T15:00:00Z', updated_at='2026-10-05T15:01:00Z')
                    runs.append(run)
                return {'workflow_runs': runs}
        reader = Reader()
        reader.since = '2026-10-05T12:00:00Z'
        report = self.evaluate({'workflows': [reader.workflow(data['repo'], data)]})
        self.assertTrue(any('page=2' in c for c in calls))
        self.assertTrue(any(f['kind'] == 'timeout' for f in report['workflows'][0]['flags']))

    def active_run_reader(self, outcome='failure', read_error=False, inactive=False):
        data = self.data()['workflows'][0]
        run = copy.deepcopy(data['runs'][0])
        run.update(workflow_id=data['id'], head_branch='previously-active',
                   created_at='2026-10-05T15:59:00Z', run_started_at='2026-10-05T15:59:00Z',
                   updated_at='2026-10-05T16:04:00Z', conclusion=outcome,
                   status='in_progress' if outcome is None else 'completed')
        job = data['jobs'][str(run['id'])][0]
        job.update(started_at=run['run_started_at'], completed_at=run['updated_at'],
                   conclusion=outcome, status=run['status'])
        job['steps'][0].update(started_at=job['started_at'], completed_at=job['completed_at'],
                              conclusion=outcome, status=run['status'])
        data['definitions'][run['head_sha']]['jobs']['canary']['steps'] = [
            {'name': job['steps'][0]['name'], 'timeout-minutes': 5}]
        definition = {k: data[k] for k in ('id', 'name', 'path')}
        calls = []

        class Reader(checker.GitHub):
            def api(self, endpoint):
                calls.append(endpoint)
                if '/contents/' in endpoint:
                    return {'content': base64.b64encode(json.dumps(data['definitions'][run['head_sha']]).encode()).decode()}
                if '/jobs?' in endpoint:
                    return {'jobs': [job]}
                if endpoint.endswith('/actions/runs/105'):
                    if read_error:
                        raise RuntimeError('tracked run read failed')
                    return copy.deepcopy(run)
                if 'status=success' in endpoint:
                    return {'workflow_runs': data['successes']}
                if 'branch=main' in endpoint or 'status=in_progress' in endpoint:
                    return {'workflow_runs': []}
                if '/runs?' in endpoint:
                    if '&page=3' in endpoint:
                        return {'workflow_runs': [run]}
                    page = 2 if '&page=2' in endpoint else 1
                    start = '2026-10-05T15:59:30Z' if page == 2 else '2026-10-05T16:05:00Z'
                    return {'workflow_runs': [{**run, 'id': 200 + page * 30 + i,
                        'head_branch': 'busy', 'status': 'completed', 'conclusion': 'success',
                        'created_at': start, 'run_started_at': start, 'updated_at': start}
                        for i in range(30)]}
                return {'workflows': [] if inactive or not endpoint.startswith('repos/' + data['repo'] + '/')
                        else [{**definition, 'state': 'active'}]}

        previous = {'status': 'OK', 'observed_at': '2026-10-05T16:00:00Z',
                    'scan_cursor': '2026-10-05T16:00:00Z', 'errors': [],
                    'workflows': [{**definition, 'repo': data['repo'], 'flags': [],
                        'active_runs': [{'branch': run['head_branch'], 'run_id': run['id']}]}]}
        return Reader, previous, calls

    def scheduled_scan(self, path, reader, now='2026-10-05T16:10:00Z'):
        from unittest.mock import patch
        from contextlib import redirect_stdout
        from io import StringIO
        store = LoopStore()
        with patch.object(checker, 'GitHub', reader), patch.object(checker, 'record_verb',
                lambda verb, args, **kwargs: store(verb, args)), patch.object(sys, 'argv',
                [str(CHECK), '--record-loops', '--state-file', str(path), '--now', now]), redirect_stdout(StringIO()):
            rc = checker.main()
        return rc, json.loads(path.read_text()), store

    def test_10_scheduled_scan_accounts_for_active_runs_beyond_creation_cutoff(self):
        for outcome, expected in (('failure', 'WARN'), (None, 'OK'), ('success', 'OK')):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as raw:
                reader, previous, calls = self.active_run_reader(outcome)
                path = Path(raw) / 'receipt.json'
                path.write_text(json.dumps(previous))
                rc, report, store = self.scheduled_scan(path, reader)
                self.assertEqual(report['status'], expected, report)
                self.assertEqual(rc, int(expected == 'WARN'))
                self.assertEqual(report['scan_cursor'], '2026-10-05T16:10:00Z')
                row = report['workflows'][0]
                if outcome == 'failure':
                    flag = next(f for f in row['flags'] if f['kind'] == 'timeout')
                    self.assertEqual((flag['run_id'], flag['timeout_seconds']), (105, 300))
                    self.assertEqual(store.loops[0]['status'], 'open')
                elif outcome == 'success':
                    self.assertEqual(row['recoveries']['previously-active'][0]['run_id'], 105)
                self.assertEqual(row['active_runs'], previous['workflows'][0]['active_runs'] if outcome is None else [])
                self.assertIn('repos/jbookout/carr-system/actions/runs/105', calls)

    def test_10_failed_tracked_read_preserves_inventory_for_next_scan(self):
        with tempfile.TemporaryDirectory() as raw:
            reader, previous, _ = self.active_run_reader(read_error=True)
            path = Path(raw) / 'receipt.json'
            path.write_text(json.dumps(previous))
            rc, report, _ = self.scheduled_scan(path, reader)
            self.assertEqual((rc, report['status']), (1, 'UNAVAILABLE'))
            self.assertEqual(report['scan_cursor'], previous['scan_cursor'])
            self.assertEqual(report['workflows'][0]['active_runs'], previous['workflows'][0]['active_runs'])
            reader, _, _ = self.active_run_reader()
            rc, report, _ = self.scheduled_scan(path, reader, '2026-10-05T16:20:00Z')
            self.assertEqual((rc, report['status']), (1, 'WARN'))
            self.assertEqual(next(f for f in report['workflows'][0]['flags'] if f['kind'] == 'timeout')['run_id'], 105)

    def test_10_disabled_workflow_still_accounts_for_prior_active_run(self):
        with tempfile.TemporaryDirectory() as raw:
            reader, previous, _ = self.active_run_reader(inactive=True)
            path = Path(raw) / 'receipt.json'
            path.write_text(json.dumps(previous))
            rc, report, _ = self.scheduled_scan(path, reader)
            self.assertEqual((rc, report['status']), (1, 'WARN'))
            self.assertEqual(next(f for f in report['workflows'][0]['flags'] if f['kind'] == 'timeout')['run_id'], 105)


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
        row['recoveries'] = {'other': [{'run_id': 106, 'run_attempt': 1, 'started_at': '2026-10-05T15:00:00Z',
                                    'duration_seconds': 500, 'baseline_seconds': 600, 'run_url': 'green'}]}
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
