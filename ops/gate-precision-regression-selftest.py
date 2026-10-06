#!/usr/bin/env python3
"""PR 1578 behavioral regressions, with synthetic records and transports."""
import argparse
import importlib.util
import json
import multiprocessing
import os
from pathlib import Path
import sys
import hashlib
import shlex
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'hooks'), str(ROOT)]
import gate_ledger as gl


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gv = load('gate_verdict', 'tools/gate_verdict.py')
guard = load('guard', 'hooks/guard-unattended.py')
TEXT = 'A sufficiently long document with many distinct words for comparing the same operation target and content.'
NOISY = [{'gate': 'a.py', 'blocks': 5, 'labelled': 5, 'wrong': 3, 'fa_rate': .6,
          'top_wrong': [('rule A', 3)]}]


def fire(path, call, command, outcome='deny', event='PreToolUse', tool='Bash', response=None, hook='a.py'):
    os.environ['CARR_GATE_LEDGER'] = str(path)
    record = dict(session='S', tool_use_id=call, hook=hook, event=event,
                  outcome=outcome, source='live', tool=tool, ts='2026-10-06T00:00:00Z')
    payload = dict(tool_input=command, tool_response=response)
    gl.observe(str(ROOT), record, json.dumps(payload).encode())
    return record


def observe_worker(path, call, barrier):
    original = gl._load_pending
    def slow(p):
        rows = original(p)
        time.sleep(.15)
        return rows
    gl._load_pending = slow
    barrier.wait()
    fire(path, 'invocation', {'command': TEXT}, hook=call + '.py')


def reconcile_worker(path, barrier, calls):
    def verb(name, payload):
        if name == 'read-loop':
            return {'loop_id': 'L', 'version': 1, 'status': 'open', 'body': gv._loop_body(NOISY[0])}
        calls.append((name, payload))
        time.sleep(.15)
        return {'ok': True, 'loop_id': 'L'}
    barrier.wait()
    gv.reconcile_loops(NOISY, verb, path)


class Regressions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'ledger.jsonl'
        self.state = str(Path(self.tmp.name) / 'loops.json')
        self.env = patch.dict(os.environ, {'CARR_GATE_LEDGER': str(self.path)})
        self.env.start()
        self.addCleanup(self.env.stop)

    def rows(self, kind):
        return [r for r in map(json.loads, self.path.read_text().splitlines()) if r['type'] == kind]

    def test_dispatch_prefixes(self):
        for command in ['env FOO=x wrangler deploy', 'FOO="x" wrangler deploy',
                        'if true; then wrangler deploy; fi',
                        'for x in 1; do gh workflow run ci.yml; done']:
            with self.subTest(command=command):
                self.assertIsNotNone(guard.check(command))

    def test_unknown_or_optioned_wrappers_fail_closed(self):
        for command in ['nice -n 5 wrangler deploy', 'sudo -u x wrangler deploy',
                        'stdbuf -o0 gh workflow run ci.yml', 'caffeinate -i wrangler deploy',
                        "printf '%s' 'wrangler deploy' | sudo -u x bash",
                        "printf '%s' 'wrangler deploy' | nice bash",
                        'echo wrangler deploy | bash']:
            with self.subTest(command=command):
                self.assertIsNotNone(guard.check(command))

    def test_executable_data_modes(self):
        for command in ["git -c alias.ship='!wrangler deploy' ship",
                        "git -c alias.ship=!wrangler\\ deploy ship",
                        "git -c core.pager='wrangler deploy' log",
                        "git -c core.sshCommand='gh workflow run ci.yml' fetch",
                        "rg --pre 'wrangler deploy' x",
                        "printf '%s' 'wrangler deploy' | bash",
                        "printf '%s' 'gh workflow run ci.yml' | bash"]:
            with self.subTest(command=command):
                self.assertIsNotNone(guard.check(command))

    def test_quoted_substitution_delimiter(self):
        self.assertIsNotNone(guard.check('echo "$(printf \')\'; wrangler deploy)"'))

    def test_naming_a_dispatch_as_data_is_allowed(self):
        for command in ['grep -rn "wrangler deploy" hooks', "git grep 'gh workflow run'",
                        'git commit -m "wrangler deploy"', "echo 'x | bash'",
                        "grok-run.sh 'does wrangler deploy have a successor?'"]:
            with self.subTest(command=command):
                self.assertIsNone(guard.check(command))

    def test_quoted_pipe_is_inert(self):
        for body in ['wrangler deploy', 'rm -rf /repo/protected']:
            self.assertIsNone(guard.check("grep 'x | bash' <<'EOF'\n" + body + '\nEOF'))
            self.assertIsNotNone(guard.check("cat <<'EOF' | bash\n" + body + '\nEOF'))

    def test_target_and_success(self):
        for response, target, event in [({'exit_code': 1}, '/canonical', 'PostToolUse'),
                                        ({'exit_code': 0}, '/scratch', 'PostToolUse'),
                                        (None, '/canonical', 'PreToolUse')]:
            with self.subTest(response=response, target=target, event=event):
                self.path.unlink(missing_ok=True)
                fire(self.path, 'A', {'file_path': '/canonical', 'content': TEXT}, tool='Write')
                fire(self.path, 'B', {'file_path': target, 'content': TEXT}, 'allow', event, 'Write', response)
                self.assertEqual(self.rows('verdict'), [])

    def test_successful_retry(self):
        cmd = {'command': 'printf hello world from a repeatable safe operation'}
        fire(self.path, 'A', cmd)
        fire(self.path, 'B', cmd, 'allow')
        fire(self.path, 'B', cmd, 'allow', 'PostToolUse', response={'exit_code': 0})
        self.assertEqual(len(self.rows('verdict')), 1)

    def test_intervening_denial_or_prompt(self):
        for event in ['PreToolUse', 'UserPromptSubmit']:
            with self.subTest(event=event):
                self.path.unlink(missing_ok=True)
                fire(self.path, 'A', {'command': TEXT})
                fire(self.path, 'B', {'command': 'a different course'}, 'deny', event)
                fire(self.path, 'C', {'command': TEXT}, 'allow', 'PostToolUse', response={'exit_code': 0})
                self.assertEqual(self.rows('verdict'), [])

    def test_failed_retry_is_an_intervening_move(self):
        command = {'command': TEXT}
        fire(self.path, 'A', command)
        fire(self.path, 'B', command, 'allow', 'PostToolUse', response={'exit_code': 1})
        fire(self.path, 'C', command, 'allow', 'PostToolUse', response={'exit_code': 0})
        self.assertEqual(self.rows('verdict'), [])

    def test_simultaneous_hooks_same_invocation(self):
        ctx = multiprocessing.get_context('fork')
        barrier = ctx.Barrier(2)
        processes = [ctx.Process(target=observe_worker, args=(self.path, call, barrier)) for call in ('A', 'B')]
        for p in processes:
            p.start()
        for p in processes:
            p.join(5)
            self.assertEqual(p.exitcode, 0)
        pending = json.loads(Path(gl._pending_path(str(self.path), 'S')).read_text())
        self.assertEqual(len(pending), 2)

    def test_ambiguous_open_and_partial_batch(self):
        calls = []
        def timeout(name, payload):
            calls.append(payload)
            raise TimeoutError('ambiguous')
        for _ in range(2):
            try: gv.reconcile_loops(NOISY, timeout, self.state)
            except TimeoutError: pass
        self.assertEqual(calls[0]['idempotency_key'], calls[1]['idempotency_key'])
        self.assertTrue(Path(self.state).exists())
        def partial(name, payload):
            if 'b.py' in payload.get('body', ''): raise TimeoutError('second')
            return {'ok': True, 'loop_id': 'L'}
        try: gv.reconcile_loops(NOISY + [{**NOISY[0], 'gate': 'b.py'}], partial, self.state)
        except TimeoutError: pass
        self.assertEqual(json.loads(Path(self.state).read_text())['a.py']['loop_id'], 'L')

    def test_concurrent_reconciliation(self):
        ctx = multiprocessing.get_context('fork')
        with ctx.Manager() as manager:
            calls = manager.list()
            barrier = ctx.Barrier(2)
            ps = [ctx.Process(target=reconcile_worker, args=(self.state, barrier, calls)) for _ in range(2)]
            for p in ps: p.start()
            for p in ps:
                p.join(5)
                self.assertEqual(p.exitcode, 0)
            self.assertEqual(sum(name == 'add-loop' for name, _ in calls), 1)

    def test_canonical_state_and_corruption(self):
        with patch.dict(os.environ):
            os.environ.pop('CARR_GATE_LEDGER', None)
            self.assertEqual(Path(gv.LOOP_STATE).parent, Path(gv.default_ledger()).parent)
        Path(self.state).write_text('{bad')
        calls = []
        result = gv.reconcile_loops(NOISY, lambda *a: calls.append(a), self.state)
        self.assertIn('error', result.values())
        self.assertEqual(calls, [])
        self.assertEqual(Path(self.state).read_text(), '{bad')

    def test_versioned_recovery_and_refresh(self):
        calls = []
        remote = {'loop_id': 'L', 'status': 'open', 'version': 7, 'body': 'old rule'}
        def verb(name, payload):
            calls.append((name, payload))
            if name == 'read-loop': return dict(remote)
            if name in ('update-loop', 'close-loop'):
                if payload.get('base_version') != remote['version']: return {'ok': False, 'error': 'missing_base_version'}
                remote['version'] += 1
                if name == 'update-loop': remote['body'] = payload['body']
                else: remote['status'] = 'done'
            return {'ok': True, 'loop_id': 'L'}
        gv.reconcile_loops(NOISY, verb, self.state)
        changed = [{**NOISY[0], 'top_wrong': [('rule B', 4)], 'wrong': 4}]
        result = gv.reconcile_loops(changed, verb, self.state)
        self.assertEqual(result, {'a.py': 'updated'})
        self.assertIn('rule B', remote['body'])
        result = gv.reconcile_loops([], verb, self.state)
        self.assertEqual(result, {'a.py': 'closed'})
        self.assertTrue(any(n == 'read-loop' for n, _ in calls))
        remote['status'] = 'done'
        Path(self.state).write_text(json.dumps({'a.py': {'loop_id': 'L'}}))
        self.assertEqual(gv.reconcile_loops(NOISY, verb, self.state), {'a.py': 'opened'})

    def test_unknown_evidence_never_closes(self):
        Path(self.state).write_text(json.dumps({'a.py': {'loop_id': 'L'}}))
        for content in [None, '{bad\n', '[]\n']:
            if content is not None: self.path.write_text(content)
            line, noisy = gv.health_row(str(self.path))
            calls = []
            gv.reconcile_loops(noisy, lambda *a: calls.append(a), self.state)
            self.assertEqual(calls, [])
            self.assertFalse(line.startswith('OK'))

    def test_live_backfill_identity(self):
        record = fire(self.path, 'A', {'command': TEXT})
        telemetry = Path(self.tmp.name) / 'telemetry.jsonl'
        telemetry.write_text(json.dumps(record) + '\n')
        gv._cmd_backfill(argparse.Namespace(telemetry=[str(telemetry)], skip_session=[]), str(self.path))
        self.assertEqual(len(self.rows('decision')), 1)

    def test_codex_adapter_is_metered(self):
        config = json.loads((ROOT / 'ops/config/codex-hooks.json').read_text())
        for event, groups in config['hooks'].items():
            for group in groups:
                for hook in group['hooks']:
                    with self.subTest(command=hook['command']):
                        self.assertIn('hook-meter-run.py', hook['command'])

    def test_declared_codex_guard_records_refusal(self):
        config = json.loads((ROOT / 'ops/config/codex-hooks.json').read_text())
        command = next(h['command'] for group in config['hooks']['PreToolUse']
                       for h in group['hooks'] if h['command'].endswith('/guard-unattended.py'))
        payload = {'hook_event_name': 'PreToolUse', 'session_id': 'S', 'tool_name': 'Bash',
                   'tool_use_id': 'native-guard', 'tool_input': {'command': 'env FOO=x wrangler deploy'}}
        env = {**os.environ, 'CARR_HOOK_FIXTURE': '1',
               'CARR_HOOK_TELEMETRY': str(Path(self.tmp.name) / 'telemetry.jsonl')}
        proc = subprocess.run(shlex.split(command.replace('{{REPO}}', str(ROOT))),
                              input=json.dumps(payload), text=True, capture_output=True,
                              cwd=self.tmp.name, env=env, timeout=30)
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertEqual(self.rows('decision')[0]['gate'], 'guard-unattended.py')

    def test_native_stop_in_meter_outside_checkout(self):
        transcript = Path(self.tmp.name) / 'native.jsonl'
        transcript.write_text(json.dumps({'type': 'response_item', 'payload': {
            'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': TEXT}]}}) + '\n')
        gate = Path(self.tmp.name) / 'stop.py'
        gate.write_text('import sys; print("reply refused", file=sys.stderr); sys.exit(2)')
        proc = subprocess.run([sys.executable, str(ROOT / 'hooks/hook-meter-run.py'), str(gate)],
            input=json.dumps({'hook_event_name': 'Stop', 'session_id': 'S', 'prompt_id': 'p',
                              'transcriptPath': str(transcript)}), cwd=self.tmp.name,
            env={**os.environ, 'CARR_HOOK_FIXTURE': '1', 'PYTHONPATH': '',
                 'CARR_HOOK_TELEMETRY': str(Path(self.tmp.name) / 'telemetry.jsonl')},
            capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(self.rows('decision')[0]['input_digest'],
                         'sha256:' + hashlib.sha256(TEXT.encode()).hexdigest())

    def test_edit_operation_includes_replaced_text(self):
        before = {'file_path': '/canonical', 'old_string': 'old A', 'new_string': TEXT}
        fire(self.path, 'A', before, tool='Edit')
        fire(self.path, 'B', {**before, 'old_string': 'old B'}, 'allow', 'PostToolUse', 'Edit', {'success': True})
        self.assertEqual(self.rows('verdict'), [])

    def test_empty_or_invalid_verdict_evidence_is_unknown(self):
        for body in ['', json.dumps({'type': 'verdict', 'decision_id': 'A', 'label': 'invalid'}) + '\n']:
            self.path.write_text(body)
            line, noisy = gv.health_row(str(self.path))
            self.assertIsNone(noisy)
            self.assertFalse(line.startswith('OK'))

    def test_native_stop(self):
        transcript = Path(self.tmp.name) / 'native.jsonl'
        transcript.write_text(json.dumps({'type': 'response_item', 'payload': {
            'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': TEXT}]}}) + '\n')
        for key in ['transcript_path', 'transcriptPath']:
            self.assertEqual(gl.substance({key: str(transcript)}, 'Stop'), TEXT)



if __name__ == '__main__':
    unittest.main()
