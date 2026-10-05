import importlib.util
import json
import os
from pathlib import Path
import tempfile
import subprocess
import sys
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location('precision_health', ROOT / 'ops/ruleprecision_health.py')
assert spec is not None and spec.loader is not None
health = importlib.util.module_from_spec(spec)
spec.loader.exec_module(health)


class HealthTest(unittest.TestCase):
    def test_health_cli_requires_separate_write_opt_in(self):
        script = """
import runpy, sys, types
module = types.ModuleType('ops.ruleprecision_health')
def health_row(repo, *, apply=False):
    return {'status': 'OK', 'line': 'apply=' + str(apply)}
module.health_row = health_row
sys.modules['ops.ruleprecision_health'] = module
sys.path.insert(0, sys.argv[1] + '/tools')
sys.argv = [sys.argv[1] + '/tools/health-check.py', '--section', 'ruleprecision']
runpy.run_path(sys.argv[0], run_name='__main__')
"""
        for apply in (None, '0', '1'):
            environment = {key: value for key, value in os.environ.items()
                           if key != 'CARR_RULEPRECISION_HEALTH_APPLY'}
            environment['CARR_RULEPRECISION_SHADOW'] = '1'
            if apply is not None:
                environment['CARR_RULEPRECISION_HEALTH_APPLY'] = apply
            result = subprocess.run([sys.executable, '-c', script, str(ROOT)],
                                    capture_output=True, text=True, timeout=15,
                                    env=environment)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('apply=' + str(apply == '1'), result.stdout)

    def test_fresh_old_generation_cannot_clear_health(self):
        now = datetime.now(timezone.utc)
        logs = [{'ts': now.isoformat(), 'input_sha256': str(i),
                 'selector_digest': 'old-generation', 'candidate_ids': ['4a53ff82']}
                for i in range(20)]
        labels = [{**row, 'gold': ['4a53ff82'], 'judged_rules': ['4a53ff82'],
                   'boot_ids': [], 'boot_verified': True, 'auditor': 'peer'} for row in logs]
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / 'out/orch/ruleprecision'
            directory.mkdir(parents=True)
            for name, rows in (('shadow.jsonl', logs), ('live-labels.jsonl', labels)):
                (directory / name).write_text(''.join(json.dumps(row) + '\n' for row in rows))
            with patch.dict(os.environ, {'CARR_RULEPRECISION_SHADOW': '1',
                                         'CARR_RULEPRECISION_CONFIG': str(ROOT / 'ops/config/ruleprecision.v1.json')}):
                with patch.object(health, 'respond') as respond:
                    result = health.health_row(tmp, now=now)
            self.assertEqual(result['status'], 'UNKNOWN')
            self.assertIn('read-only; loop action pending', result['line'])
            respond.assert_not_called()

    def test_only_fresh_current_generation_can_clear_health(self):
        from lib.ruleprecision_shadow import selector_snapshot
        now = datetime.now(timezone.utc)
        with patch.dict(os.environ, {'CARR_RULEPRECISION_SHADOW': '1',
                                     'CARR_RULEPRECISION_CONFIG': str(ROOT / 'ops/config/ruleprecision.v1.json')}):
            _, digest = selector_snapshot(ROOT)
            logs = [{'ts': now.isoformat(), 'input_sha256': str(i),
                     'selector_digest': digest, 'candidate_ids': ['4a53ff82']}
                    for i in range(20)]
            labels = [{**row, 'gold': ['4a53ff82'], 'judged_rules': ['4a53ff82'],
                       'boot_ids': [], 'boot_verified': True, 'auditor': 'peer'} for row in logs]
            with tempfile.TemporaryDirectory() as tmp:
                directory = Path(tmp) / 'out/orch/ruleprecision'
                directory.mkdir(parents=True)
                for name, rows in (('shadow.jsonl', logs), ('live-labels.jsonl', labels)):
                    (directory / name).write_text(''.join(json.dumps(row) + '\n' for row in rows))
                with patch.object(health, 'respond') as respond:
                    result = health.health_row(tmp, apply=True, now=now)
                    self.assertEqual(result['status'], 'OK')
                    respond.assert_called_once()
                    respond.reset_mock()
                    result = health.health_row(tmp, now=now.replace(year=now.year + 1))
                    self.assertEqual(result['status'], 'UNKNOWN')
                    respond.assert_not_called()

    def test_malformed_live_rows_are_unknown(self):
        sample = {'ts': datetime.now(timezone.utc).isoformat()}
        for logs, labels in (([1], [{}]), ([sample], [1]), ([{'ts': 1}], [{}])):
            with self.subTest(logs=logs, labels=labels), tempfile.TemporaryDirectory() as tmp:
                directory = Path(tmp) / 'out/orch/ruleprecision'
                directory.mkdir(parents=True)
                for name, rows in (('shadow.jsonl', logs), ('live-labels.jsonl', labels)):
                    (directory / name).write_text(''.join(json.dumps(row) + '\n' for row in rows))
                with patch.dict(os.environ, {'CARR_RULEPRECISION_SHADOW': '1',
                                             'CARR_RULEPRECISION_CONFIG': str(ROOT / 'ops/config/ruleprecision.v1.json')}):
                    result = health.health_row(tmp)
                self.assertEqual(result['status'], 'UNKNOWN')
                self.assertIn('live sample unreadable', result['line'])

    def test_failed_close_ack_does_not_clear_local_loop(self):
        for failure in ({'ok': False}, {'ok': True, 'error': 'refused'}):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as tmp:
                state = Path(tmp) / 'state.json'
                original = {'loop_id': 'loop', 'identity': 'fixed-test', 'body': 'breach'}
                state.write_text(json.dumps(original))
                responses = [subprocess.CompletedProcess([], 0, stdout=json.dumps({'loop_id': 'loop', 'version': 2})),
                             subprocess.CompletedProcess([], 0, stdout=json.dumps(failure))]
                with patch.object(health.subprocess, 'run', side_effect=responses):
                    with self.assertRaises(RuntimeError):
                        health.respond({'status': 'OK', 'line': 'recovered'}, state,
                                       lambda verb, args: health._run_verb(ROOT, verb, args))
                self.assertEqual(json.loads(state.read_text()), original)

    def test_injected_response_cannot_false_clear(self):
        for failure in ({'ok': False}, {'ok': True, 'error': 'refused'},
                        {'ok': True, 'loop_id': 'other', 'status': 'done'},
                        {'ok': True, 'loop_id': 'loop', 'status': 'open'}):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as tmp:
                state = Path(tmp) / 'state.json'
                original = {'loop_id': 'loop', 'identity': 'fixed-test', 'body': 'breach'}
                state.write_text(json.dumps(original))
                def record(verb, args):
                    return {'loop_id': 'loop', 'version': 2} if verb == 'read-loop' else failure
                with self.assertRaises(RuntimeError):
                    health.respond({'status': 'OK', 'line': 'recovered'}, state, record)
                self.assertEqual(json.loads(state.read_text()), original)

    def test_health_cli_routes_without_production_access_when_off(self):
        result = subprocess.run([sys.executable, str(ROOT / 'tools/health-check.py'),
                                 '--section', 'ruleprecision'], capture_output=True, text=True,
                                env={**os.environ, 'CARR_RULEPRECISION_SHADOW': '0'}, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('OFF rule delivery precision', result.stdout)

    def test_no_gold_is_unknown(self):
        row = health.evaluate([{'input_sha256': 'x', 'candidate_ids': ['a']}], [], selector_digest='s', minimum=1)
        self.assertEqual(row['status'], 'UNKNOWN')
        self.assertIn('on breach:', row['line'])
        self.assertIn('owner', row['line'])
        self.assertIn('auto-clear', row['line'])

    def test_sampled_counterfactual_precision_and_availability(self):
        logs = [{'input_sha256': 'x', 'selector_digest': 's', 'candidate_ids': ['a', 'noise']}]
        labels = [{'input_sha256': 'x', 'selector_digest': 's', 'gold': ['a', 'b'],
                   'judged_rules': ['a', 'b', 'noise'], 'boot_ids': ['b'],
                   'boot_verified': True, 'auditor': 'independent'}]
        row = health.evaluate(logs, labels, selector_digest='s', minimum=1)
        self.assertEqual(row['precision_proxy'], 0.5)
        self.assertEqual(row['availability_proxy'], 1)
        self.assertEqual(row['status'], 'WARN')

    def test_stale_selector_labels_cannot_clear(self):
        logs = [{'input_sha256': 'x', 'selector_digest': 'new', 'candidate_ids': ['a']}]
        labels = [{'input_sha256': 'x', 'selector_digest': 'old', 'gold': ['a'],
                   'judged_rules': ['a'], 'boot_ids': [], 'boot_verified': True, 'auditor': 'peer'}]
        self.assertEqual(health.evaluate(logs, labels, selector_digest='new', minimum=1)['status'], 'UNKNOWN')

    def test_response_dedup_and_clear_are_verified(self):
        calls = []
        def record(verb, args):
            calls.append((verb, args))
            if verb == 'add-loop':
                return {'ok': True, 'loop_id': 'loop'}
            if verb == 'read-loop':
                return {'loop_id': 'loop', 'version': 2}
            return {'ok': True, 'loop_id': 'loop', 'status': 'done'}
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / 'state.json'
            health.respond({'status': 'WARN', 'line': 'breach'}, state, record)
            health.respond({'status': 'WARN', 'line': 'breach'}, state, record)
            self.assertEqual([v for v, _ in calls].count('add-loop'), 1)
            health.respond({'status': 'OK', 'line': 'verified recovery'}, state, record)
            self.assertEqual([v for v, _ in calls].count('close-loop'), 1)
            self.assertEqual(json.loads(state.read_text())['loop_id'], None)

    def test_response_names_outside_monitor_work(self):
        for status, expected in (('WARN', 'tested selector revision through a PR'),
                                 ('UNKNOWN', 'current selector-bound labels and verified boot evidence')):
            calls = []
            def record(verb, args):
                calls.append((verb, args))
                return {'ok': True, 'loop_id': 'loop'}
            with self.subTest(status=status), tempfile.TemporaryDirectory() as tmp:
                health.respond({'status': status, 'line': 'breach'}, Path(tmp) / 'state.json', record)
            verb, args = calls[0]
            self.assertEqual(verb, 'add-loop')
            self.assertEqual(args['owner'], 'claude')
            self.assertEqual(args['blocker'], 'other_lane')
            self.assertIn(expected, args['blocker_detail'])


if __name__ == '__main__':
    unittest.main()
