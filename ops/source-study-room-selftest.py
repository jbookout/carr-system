#!/usr/bin/env python3
"""Research desk confinement and provider evidence, without model calls."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ResearchDeskTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('research_room', ROOT / 'tools/room-bridge/source_study.py')
        self.room = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.room)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name).resolve()

    def test_route_requires_named_model_effort_and_no_added_authority(self):
        entry = {'name': 'source-study', 'kind': 'codex-session', 'model': 'fixture-model',
                 'effort': 'high', 'sandbox': 'read-only'}
        self.assertEqual(self.room.validate_desk(entry), entry)
        for change in ({'model': None}, {'effort': None}, {'sandbox': 'danger-full-access'},
                       {'kind': 'claude-session'}, {'add_dirs': ['/']}, {'name': 'unreviewed'}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.room.validate_desk({**entry, **change})

    def test_model_readback_is_bound_to_result_thread(self):
        sessions = self.folder / 'sessions'
        sessions.mkdir()
        path = sessions / 'rollout.jsonl'
        records = [{'type': 'session_meta', 'payload': {'id': 'thread-a'}},
                   {'type': 'turn_context', 'payload': {'model': 'fixture-model', 'effort': 'high'}}]
        path.write_text('\n'.join(json.dumps(v) for v in records))
        entry = {'model': 'fixture-model', 'effort': 'high'}
        self.assertEqual(self.room.observed_model(self.folder, 'thread-a', entry),
                         {'model': 'fixture-model', 'effort': 'high', 'thread_id': 'thread-a'})
        for thread, model in (('other-thread', 'fixture-model'), ('thread-a', 'wrong-model')):
            with self.subTest(thread=thread, model=model), self.assertRaises(ValueError):
                self.room.observed_model(self.folder, thread, {**entry, 'model': model})
        records.append({'type': 'turn_context', 'payload': {'model': 'wrong-model', 'effort': 'high'}})
        path.write_text('\n'.join(json.dumps(v) for v in records))
        with self.assertRaises(ValueError):
            self.room.observed_model(self.folder, 'thread-a', entry)

    def test_runtime_drops_ambient_credentials_and_mcp_configuration(self):
        home = self.folder / 'ambient'
        home.mkdir()
        (home / 'auth.json').write_text('{"provider_auth":"synthetic"}')
        (home / 'config.toml').write_text('[mcp_servers.carr]\ncommand="mutation-tool"')
        env = self.room.runtime_env(self.folder / 'job', home,
                                    {'PATH': '/usr/bin:/bin', 'DATABASE_URL': 'synthetic-dsn',
                                     'CARR_TOKEN': 'synthetic-token', 'OPENAI_API_KEY': 'synthetic-key'})
        self.assertNotIn('DATABASE_URL', env)
        self.assertNotIn('CARR_TOKEN', env)
        self.assertNotIn('OPENAI_API_KEY', env)
        config = (Path(env['CODEX_HOME']) / 'config.toml').read_text()
        self.assertNotIn('mcp_servers', config)
        self.assertNotIn('mutation-tool', config)
        self.assertIn('multi_agent=false', config)
        self.assertNotEqual(Path(env['HOME']), home)

    def test_retrieval_runtime_needs_only_grok_provider_auth(self):
        auth = self.folder / 'grok-auth'
        auth.mkdir()
        (auth / 'auth.json').write_text('{"provider_auth":"synthetic"}')
        env = self.room.runtime_env(self.folder / 'job', auth, {'PATH': '/usr/bin:/bin'}, provider='grok')
        self.assertNotIn('CODEX_HOME', env)
        self.assertTrue((Path(env['HOME']) / '.grok/auth.json').is_file())
        self.assertFalse((Path(env['HOME']) / '.codex').exists())

    @unittest.skipUnless(shutil.which('sandbox-exec'), 'macOS confinement probe')
    def test_os_scope_denies_repo_write_secret_read_and_socket_access(self):
        job = self.folder / 'job'
        job.mkdir()
        outside = self.folder / 'secret'
        outside.write_text('synthetic-secret')
        listener = socket.socket(socket.AF_UNIX)
        sockpath = str(self.folder / 'carr.sock')
        listener.bind(sockpath)
        listener.listen()
        self.addCleanup(listener.close)
        code = ('from pathlib import Path; import socket\n'
                f'job=Path({str(job)!r}); outside=Path({str(outside)!r})\n'
                "job.joinpath('allowed').write_text('report')\n"
                "for action in (lambda: outside.read_text(), lambda: outside.write_text('bad'), "
                f"lambda: socket.socket(socket.AF_UNIX).connect({sockpath!r})):\n"
                " try: action()\n"
                " except OSError: pass\n"
                " else: raise SystemExit('authority escaped')\n")
        result = subprocess.run(self.room.confine([sys.executable, '-c', code], job),
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(outside.read_text(), 'synthetic-secret')
        self.assertEqual((job / 'allowed').read_text(), 'report')

    @unittest.skipUnless(shutil.which('sandbox-exec'), 'macOS confinement probe')
    def test_real_room_dispatch_with_fake_provider_binds_model_and_result(self):
        job = self.folder / 'job'
        (job / 'bin').mkdir(parents=True)
        provider = job / 'bin/codex'
        provider.write_text('#!/usr/bin/env python3\n' + '''
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
assert args[args.index('-s') + 1] == 'read-only'
assert 'DATABASE_URL' not in os.environ and 'CARR_TOKEN' not in os.environ
config = (Path(os.environ['CODEX_HOME']) / 'config.toml').read_text()
assert 'mcp_servers' not in config and 'shell_tool=false' in config
Path(args[args.index('-o') + 1]).write_text('verified fixture report')
sessions = Path(os.environ['CODEX_HOME']) / 'sessions'
sessions.mkdir()
rows = [{'type': 'session_meta', 'payload': {'id': 'fixture-thread'}},
        {'type': 'turn_context', 'payload': {'model': 'fixture-model', 'effort': 'high'}}]
(sessions / 'rollout.jsonl').write_text('\\n'.join(json.dumps(r) for r in rows))
print(json.dumps({'type': 'thread.started', 'thread_id': 'fixture-thread'}))
print(json.dumps({'type': 'turn.completed'}))
''')
        provider.chmod(0o755)
        auth = self.folder / 'auth'
        auth.mkdir()
        (auth / 'auth.json').write_text('{"provider_auth":"synthetic"}')
        registry = self.folder / 'registry.json'
        registry.write_text(json.dumps({'desks': {'source-study': {
            'kind': 'codex-session', 'model': 'fixture-model', 'effort': 'high', 'sandbox': 'read-only'}}}))
        env = dict(os.environ, CARR_HERMES_DESKS=str(registry), CODEX_HOME=str(auth),
                   DATABASE_URL='synthetic-dsn', CARR_TOKEN='synthetic-token',
                   PATH=str(job / 'bin') + os.pathsep + os.environ['PATH'])
        command = [sys.executable, str(ROOT / 'tools/room-bridge/source_study.py')]
        route = subprocess.run([*command, '--preflight'], env=env, capture_output=True, text=True, check=True)
        (job / 'request.json').write_text(json.dumps({'route': json.loads(route.stdout),
                                                     'prompt': 'Synthetic fixture', 'timeout': 10}))
        result = subprocess.run([*command, '--job', str(job)], env=env, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        row = json.loads(result.stdout)
        self.assertEqual(row['result'], 'verified fixture report')
        self.assertEqual(row['observed'], {'model': 'fixture-model', 'effort': 'high', 'thread_id': 'fixture-thread'})
        self.assertFalse(list(job.rglob('auth.json')), 'provider credentials must not persist after dispatch')

    @unittest.skipUnless(shutil.which('sandbox-exec'), 'macOS confinement probe')
    def test_grok_room_wire_uses_confinement_and_provider_readback(self):
        job = self.folder / 'job'
        (job / 'bin').mkdir(parents=True)
        outside = self.folder / 'host-secret'
        outside.write_text('synthetic')
        provider = job / 'bin/grok'
        provider.write_text('#!/usr/bin/env python3\n' +
                            'from pathlib import Path; import json, sys\n' +
                            f'try: Path({str(outside)!r}).read_text()\n' +
                            'except OSError: pass\nelse: sys.exit(99)\n' +
                            "print(json.dumps({'type': 'text', 'data': 'RAW SOURCE'}))\n" +
                            "print(json.dumps({'type': 'end', 'stopReason': 'end_turn', "
                            "'requestId': 'fixture-request', 'sessionId': 'fixture-session', "
                            "'modelUsage': {'grok-4.7-build': {'modelCalls': 1}}}))\n")
        provider.chmod(0o755)
        ambient = self.folder / 'ambient'
        (ambient / '.grok').mkdir(parents=True)
        (ambient / '.grok/auth.json').write_text('{"provider_auth":"synthetic"}')
        registry = self.folder / 'registry.json'
        registry.write_text(json.dumps({'desks': {'grok-build': {
            'kind': 'grok-cli', 'model': 'grok-4.7', 'effort': 'high', 'sandbox': 'read-only'}}}))
        env = dict(os.environ, HOME=str(ambient), CARR_HERMES_DESKS=str(registry),
                   PATH=str(job / 'bin') + os.pathsep + os.environ['PATH'])
        command = [sys.executable, str(ROOT / 'tools/room-bridge/source_study.py'), '--phase', 'retrieval']
        route = subprocess.run([*command, '--preflight'], env=env, capture_output=True, text=True, check=True)
        (job / 'request.json').write_text(json.dumps({'route': json.loads(route.stdout),
                                                     'prompt': 'Synthetic public retrieval', 'timeout': 10}))
        result = subprocess.run([*command, '--job', str(job)], env=env, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        row = json.loads(result.stdout)
        self.assertEqual(row['result'], 'RAW SOURCE')
        self.assertEqual(row['observed']['model'], 'grok-4.7-build')
        self.assertFalse(list(job.rglob('auth.json')))


if __name__ == '__main__':
    unittest.main()
