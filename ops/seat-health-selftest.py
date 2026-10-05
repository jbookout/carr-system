#!/usr/bin/env python3
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import seat_health as health


class SeatHealthTests(unittest.TestCase):
    maxDiff = 800
    def test_only_exact_answer_passes(self):
        for answer, code, expected in [('323\n', 0, True), ('324', 0, False),
                                       ('', 0, False), ('323', 1, False),
                                       ('The answer is 323', 0, False)]:
            with self.subTest(answer=answer, code=code):
                row = health.assess('grok', answer, '', code, 0.2)
                self.assertEqual(row['passed'], expected)
                self.assertEqual(row['dispatchable'], expected)
                self.assertIn('owner orchestrator', row['action'])
                self.assertIn('auto-clear', row['action'])

    def test_builder_wrapper_log_is_the_answer_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runner = root / 'out/orch/sol-run.sh'
            runner.parent.mkdir(parents=True)
            runner.write_text('#!/bin/zsh\nprint -r -- "codex\n323\nrunner receipt noise\ntokens used\n99\n323\nCODEX_EXIT 0" > "$2"\n')
            output = root / 'results'
            command = [sys.executable, str(Path(__file__).with_name('seat-health.py')),
                       '--runtime-repo', str(root), '--output-root', str(output),
                       '--seat', 'sol-studio', '--no-record']
            proc = subprocess.run(command, capture_output=True, text=True, timeout=10)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            row = json.loads((output / 'seat-health.json').read_text())['seats']['sol-studio']
            self.assertTrue(row['passed'])
            runner.write_text('#!/bin/zsh\nprint -r -- "codex\n323\nCODEX_EXIT 7" > "$2"\n')
            proc = subprocess.run(command, capture_output=True, text=True, timeout=10)
            self.assertEqual(proc.returncode, 1)
            self.assertFalse(json.loads((output / 'seat-health.json').read_text())['seats']['sol-studio']['passed'])

    def test_failures_name_the_layer_without_echoing_diagnostics(self):
        for diagnostic, code, layer in [('token expired SECRET', 3, 'auth'),
                ('fetch failed SECRET', 1, 'network'), ('wrapper missing SECRET', 127, 'runner_wrapper'),
                ('wrong model SECRET', 5, 'model')]:
            row = health.assess('grok', '', diagnostic, code, .1)
            self.assertEqual(row['failing_layer'], layer)
            self.assertNotIn('SECRET', json.dumps(row))

    def test_normal_transcript_auth_words_cannot_reclassify_a_wrong_answer(self):
        row = health.assess('sol-studio', '324', 'Rule says sign-in needs credentials.', 0, 1)
        self.assertEqual(row['failing_layer'], 'model')

    def test_usage_drops_credentials_and_unknown_fields(self):
        self.assertEqual(health.numeric_usage({'input_tokens': 4, 'token': 'SECRET',
            'primary': {'used_percent': 21, 'password': 'SECRET'}, 'cost_usd': float('nan')}),
            {'input_tokens': 4, 'primary': {'used_percent': 21}})

    def test_unknown_stale_future_and_failed_seats_are_not_dispatchable(self):
        from datetime import datetime, timezone
        now = datetime(2026, 10, 5, tzinfo=timezone.utc)
        report = {'schema': 'carr-seat-health/v1', 'observed_at': now.isoformat(), 'seats': {
            'grok': {'passed': True, 'dispatchable': True, 'observed_at': now.isoformat()}}}
        self.assertTrue(health.dispatchable(report, 'grok', now=now))
        self.assertFalse(health.dispatchable(report, 'missing', now=now))
        for at in ['2026-10-03T00:00:00+00:00', '2026-10-06T00:00:00+00:00', 'bad']:
            report['seats']['grok']['observed_at'] = at
            self.assertFalse(health.dispatchable(report, 'grok', now=now))

    def test_one_loop_is_updated_then_cleared_on_exact_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / 'loop.json'
            calls = []
            def verb(name, payload):
                calls.append((name, payload))
                if name == 'add-loop':
                    return {'ok': True, 'loop_id': 'fixture-loop'}
                if name == 'read-loop':
                    return {'ok': True, 'loop': {'version': len(calls), 'status': 'open'}}
                self.assertIn('base_version', payload)
                return {'ok': True}
            fail = health.assess('grok', '', '', 0, 1)
            self.assertEqual(health.reconcile(fail, state, verb), 'open')
            self.assertEqual(health.reconcile(fail, state, verb), 'open')
            self.assertEqual(sum(n == 'add-loop' for n, _ in calls), 1)
            self.assertEqual(health.reconcile(health.assess('grok', '323', '', 0, 1), state, verb), 'cleared')
            self.assertEqual(json.loads(state.read_text()), {})
            self.assertEqual(calls[-1][0], 'close-loop')

    def test_uncertain_open_reuses_key_even_after_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / 'loop.json'
            keys = []
            def lost_response(name, payload):
                keys.append(payload['idempotency_key'])
                return {'ok': False}
            fail = health.assess('grok', '', '', 0, 1)
            self.assertEqual(health.reconcile(fail, state, lost_response), 'error')
            self.assertEqual(health.reconcile(health.assess('grok', '323', '', 0, 1), state, lost_response), 'error')
            self.assertEqual(keys[0], keys[1])

    def test_timeout_terminates_the_runner(self):
        import sys, time
        started = time.monotonic()
        output, diagnostic, code, _ = health.execute([sys.executable, '-c', 'import time; time.sleep(30)'], '.', .05)
        self.assertEqual(code, 124)
        self.assertLess(time.monotonic() - started, 4)

    def test_missing_runner_is_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            row = health.probe('opus-studio', root, root, 1, root / 'e2e')
            self.assertFalse(row['passed'])
            self.assertEqual(row['failing_layer'], 'runner_wrapper')

    def test_daily_schedule_uses_existing_chain(self):
        root = Path(__file__).resolve().parents[1]
        nightly = (root / 'bin/nightly.sh').read_text()
        self.assertIn('step "daily AI seat health" ./.venv/bin/python ops/seat-health.py', nightly)
        self.assertIn('ops/seat-health.py', nightly.split('required=(', 1)[1].split(')', 1)[0])

    def test_health_rows_show_bound_actions_without_new_model_calls(self):
        rows = health.health_rows({})
        self.assertEqual(len(rows), 6)
        self.assertTrue(all('on breach:' in line and 'owner orchestrator' in line
                            and 'Verify:' in line and 'auto-clear' in line for line in rows))
        self.assertTrue(all(line.startswith('FAIL') for line in rows))

    def test_actual_read_loop_shape_supplies_cas_and_done_closes(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / 'loop.json'
            state.write_text(json.dumps({'loop_id': 'fixture', 'open_key': 'fixture-key'}))
            calls = []
            def verb(name, payload):
                calls.append((name, payload))
                if name == 'read-loop':
                    return {'loop': {'version': 7, 'status': 'open'}}
                return {'ok': True}
            passed = health.assess('jev', '323', '', 0, .1)
            self.assertEqual(health.reconcile(passed, state, verb), 'cleared')
            self.assertEqual(calls[-1][1]['base_version'], 7)

    def test_e2e_auth_requires_the_builder_model_id(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cli = root / 'e2e'
            for text, passed in [('gpt-6-luna Available', True), ('Other models are available', False)]:
                cli.write_text('#!/bin/sh\nprintf "%s\n" "' + text + '"\n')
                cli.chmod(0o700)
                row = health.probe('e2e-auth', root, root, 2, cli)
                self.assertEqual(row['passed'], passed)

    def test_conflict_cannot_clear_a_loop(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / 'state.json'
            state.write_text(json.dumps({'loop_id': 'fixture', 'open_key': 'key'}))
            def verb(name, payload):
                return {'loop': {'version': 5, 'status': 'open'}} if name == 'read-loop' else {'ok': False, 'error': 'version_conflict'}
            self.assertEqual(health.reconcile(health.assess('grok', '323', '', 0, 1), state, verb), 'error')
            self.assertEqual(json.loads(state.read_text())['loop_id'], 'fixture')

    def test_corrupt_health_is_visible_as_failure(self):
        for report in [[], {'seats': []}, {'seats': {'grok': []}}]:
            self.assertTrue(all(line.startswith('FAIL') for line in health.health_rows(report)))


if __name__ == '__main__':
    unittest.main()
