#!/usr/bin/env python3
"""Bounded Grok retrieval suppresses session context, never effect guards."""
import importlib.util
import io
import os
from pathlib import Path
import sys
import subprocess
import shutil
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def load(path):
    spec = importlib.util.spec_from_file_location(path.stem.replace('-', '_'), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class GrokHookBoundaryTests(unittest.TestCase):
    def test_real_process_ancestry_without_a_provider_call(self):
        # A native fixture keeps the same argv visible to ps as the Grok CLI.
        # It only launches our probe: no provider, hook side effects or network.
        compiler = shutil.which('cc')
        if compiler is None:
            self.skipTest('native process fixture requires cc')
        with tempfile.TemporaryDirectory(prefix='grok-boundary-') as directory:
            scratch = Path(directory)
            source = scratch / 'launcher.c'
            source.write_text('''
#include <stdlib.h>
#include <unistd.h>
#include <sys/wait.h>
int main(void) {
    pid_t child = fork();
    if (child < 0) return 2;
    if (child == 0) {
        execl(getenv("BOUNDARY_PYTHON"), getenv("BOUNDARY_PYTHON"),
              getenv("BOUNDARY_PROBE"), (char *)0);
        _exit(2);
    }
    int status;
    if (waitpid(child, &status, 0) < 0) return 2;
    return WIFEXITED(status) ? WEXITSTATUS(status) : 2;
}
''')
            binary = scratch / 'grok'
            subprocess.run([compiler, str(source), '-o', str(binary)],
                           check=True, capture_output=True, timeout=30)
            probe = scratch / 'probe.py'
            probe.write_text('import sys\nsys.path.insert(0, ' + repr(str(ROOT)) +
                             ')\nfrom lib.grok_invocation import bounded_grok_read_only\n'
                             'print(bounded_grok_read_only())\n')
            env = dict(os.environ, CARR_GROK_RUN_READ_ONLY='1',
                       BOUNDARY_PYTHON=sys.executable, BOUNDARY_PROBE=str(probe))
            prefix = [str(binary), '--model', 'grok-4.7', '--reasoning-effort',
                      'high', '--max-turns', '60', '--always-approve', '--sandbox']
            for sandbox, expected in (('read-only', 'True'), ('workspace', 'False')):
                result = subprocess.run(prefix + [sandbox, '--output-format',
                    'streaming-json', '--print', 'long prompt ' * 300], env=env,
                    capture_output=True, text=True, check=True, timeout=10)
                self.assertEqual(result.stdout.strip(), expected)
            result = subprocess.run([str(binary)], env=env, capture_output=True,
                                    text=True, check=True, timeout=10)
            self.assertEqual(result.stdout.strip(), 'False')

    def test_marker_requires_exact_read_only_print_ancestor(self):
        from lib import grok_invocation as boundary
        prefix = '/Users/example/.grok/bin/grok-1.0.46 --model grok-4.7 --reasoning-effort high --max-turns 60 --always-approve --sandbox read-only --output-format streaming-json'
        cases = [(prefix + ' --print test', True),
                 (prefix.replace('read-only', 'workspace') + ' --print test', False),
                 ('grok', False), (prefix + ' --printable test', False),
                 ('grok test mentioning --sandbox read-only --print test', False),
                 (prefix + ' --sandbox workspace --print test', False),
                 (prefix + ' --print test mentions --sandbox workspace', True)]
        for command, expected in cases:
            with self.subTest(command=command), mock.patch.dict(
                    os.environ, {'CARR_GROK_RUN_READ_ONLY': '1'}), \
                    mock.patch.object(boundary.subprocess, 'run', side_effect=[
                        subprocess.CompletedProcess([], 0, '123 /bin/sh -c hook', ''),
                        subprocess.CompletedProcess([], 0, '1 ' + command, '')]):
                self.assertEqual(boundary.bounded_grok_read_only(), expected)
                self.assertIn('-ww', boundary.subprocess.run.call_args.args[0])
        with mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch.object(boundary.subprocess, 'run') as ps:
            self.assertFalse(boundary.bounded_grok_read_only())
            ps.assert_not_called()
        with mock.patch.dict(os.environ, {'CARR_GROK_RUN_READ_ONLY': '1'}), \
                mock.patch.object(boundary.subprocess, 'run', side_effect=OSError):
            self.assertFalse(boundary.bounded_grok_read_only())

    def test_invocation_marker_is_read_only_and_not_inherited_by_writable(self):
        wire = load(ROOT / 'tools/room-bridge/grok_wire.py')
        for writable in (False, True):
            with self.subTest(writable=writable), mock.patch.dict(
                    os.environ, {'CARR_GROK_RUN_READ_ONLY': '1'}):
                provider = mock.Mock()
                wire.invoke_cli('test', writable=writable, run=provider)
                env = provider.call_args.kwargs.get('env', {})
                self.assertEqual(env.get('CARR_GROK_RUN_READ_ONLY'), None if writable else '1')

    def test_context_hooks_skip_before_reading_payload_or_running_target(self):
        meter = load(ROOT / 'hooks/hook-meter-run.py')
        for target in ('gate-integrity.py', 'rule-boot-gate.py', 'context-handoff-gate.py',
                       'session-presence-hook.py', 'rule-pack-preuse-reselection.py',
                       'rule-pack-drift-gate.py', 'chat-lint-carryover.py'):
            with self.subTest(target=target), mock.patch.object(sys, 'argv',
                    ['meter', str(ROOT / 'hooks' / target)]), \
                    mock.patch.object(meter, 'bounded_grok_read_only', return_value=True, create=True), \
                    mock.patch.object(sys, 'stdin', io.StringIO('invalid payload')), \
                    mock.patch('builtins.open', side_effect=AssertionError('context hook ran')):
                self.assertEqual(meter.main(), 0)

    def test_direct_session_hooks_skip_before_effects(self):
        for name, downstream in (('hooks/worktree-self-plumb.py', 'resolve_cwd'),
                                 ('hooks/machine-converge.py', 'machine_role_marker'),
                                 ('ops/claude-continuity-hook.py', '_read_mode')):
            module = load(ROOT / name)
            with self.subTest(name=name), mock.patch(
                    'lib.grok_invocation.bounded_grok_read_only', return_value=True), \
                    mock.patch.object(sys, 'stdin', io.StringIO('{"cwd":"/tmp"}')), \
                    mock.patch.object(module, downstream) as effect:
                self.assertEqual(module.main(), 0)
                effect.assert_not_called()

    def test_effect_guard_runs_and_context_hooks_run_without_exemption(self):
        meter = load(ROOT / 'hooks/hook-meter-run.py')
        for target, bounded in (('guard-unattended.py', True),
                                ('rule-boot-gate.py', False)):
            with self.subTest(target=target), mock.patch.object(sys, 'argv',
                    ['meter', str(ROOT / 'hooks' / target)]), \
                    mock.patch.object(meter, 'bounded_grok_read_only', return_value=bounded), \
                    mock.patch.object(sys, 'stdin', io.StringIO('{}')), \
                    mock.patch('builtins.open', mock.mock_open(read_data=b'raise SystemExit(2)')):
                self.assertEqual(meter.main(), 2)


if __name__ == '__main__':
    unittest.main()
