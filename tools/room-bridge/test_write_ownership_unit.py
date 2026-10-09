#!/usr/bin/env python3
"""Conflicting jobs stop before launch, including paths not created yet."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import desks
import dispatch
import write_ownership
from test_codex_models_unit import catalog_fixture
from test_codex_live_unit import FakeAppServer


class OwnershipTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.reg = desks.Registry(self.root / 'desks.json')
        self.reg.register('sol', 'codex-session', family='sol', effort='high', cwd=str(self.root))
        self.results = self.root / 'results.jsonl'
        self.catalog = catalog_fixture()
        self.catalog.__enter__()
        self.addCleanup(self.catalog.__exit__, None, None, None)

    def send(self, brief='build', **kwargs):
        return dispatch.dispatch('sol', brief, registry=self.reg, results_path=self.results, **kwargs)

    def active(self, **kwargs):
        self.results.write_text(json.dumps({'msg_id': 'other', 'repo': 'owner/repo',
            'desk': 'other-sol', 'status': 'running', 'writes': ['tools/*.py'], 'own_pr': 42,
            **kwargs}) + '\n')

    def test_open_pr_overlap_refuses_before_launch(self):
        with patch('dispatch.write_ownership.open_prs', return_value=('owner/repo', [
                {'number': 42, 'title': 'the other builder', 'files': ['tools/new.py']}])) , \
             patch.object(dispatch, '_to_codex') as run:
            with self.assertRaisesRegex(desks.DeskError, 'PR 42.*build on top of PR 42'):
                self.send(writes=['tools/*.py'])
            run.assert_not_called()
        self.assertFalse(self.results.exists())

    def test_inflight_overlap_catches_not_yet_created_paths(self):
        self.active(writes=['tools/new-*.py'])
        with patch('dispatch.write_ownership.open_prs', return_value=('owner/repo', [])), \
             patch.object(dispatch, '_to_codex') as run:
            with self.assertRaisesRegex(desks.DeskError, 'other-sol.*build on top of PR 42'):
                self.send(writes=['tools/*-worker.py'])
            run.assert_not_called()

    def test_own_pr_is_allowed_and_claim_exists_before_launch(self):
        def run(*args, **kwargs):
            rows = [json.loads(line) for line in self.results.read_text().splitlines()]
            self.assertEqual(rows[-1]['status'], 'running')
            self.assertEqual(rows[-1]['writes'], ['tools/*.py'])
            return {'status': 'completed', 'termination_confirmed': True}
        with patch('dispatch.write_ownership.open_prs', return_value=('owner/repo', [
                {'number': 42, 'title': 'mine', 'files': ['tools/new.py']}])), \
             patch.object(dispatch, '_to_codex', side_effect=run):
            row = self.send('Own PR: #42\nWrites: tools/*.py')
        self.assertEqual(row['own_pr'], 42)
        self.assertEqual(row['repo'], 'owner/repo')

    def test_no_write_set_warns_and_preserves_delivery(self):
        warning = io.StringIO()
        with contextlib.redirect_stderr(warning), \
             patch('dispatch.write_ownership.open_prs') as prs, \
             patch.object(dispatch, '_to_codex', return_value={'status': 'completed', 'termination_confirmed': True}):
            self.assertEqual(self.send()['status'], 'completed')
        prs.assert_not_called()
        self.assertIn('no write set', warning.getvalue())
        self.assertEqual(len(self.results.read_text().splitlines()), 1)

    def test_completed_claim_released_but_async_delivery_stays_owned(self):
        self.active(status='delivered_live')
        with patch('dispatch.write_ownership.open_prs', return_value=('owner/repo', [])), \
             patch.object(dispatch, '_to_codex', return_value={'status': 'completed', 'termination_confirmed': True}):
            with self.assertRaises(desks.DeskError):
                self.send(writes=['tools/a.py'])
            with self.results.open('a') as fh:
                fh.write(json.dumps({'msg_id': 'other', 'status': 'completed'}) + '\n')
            self.assertEqual(self.send(writes=['tools/a.py'])['status'], 'completed')
            self.assertEqual(self.send(writes=['tools/a.py'])['status'], 'completed')

    def test_cli_repeatable_writes_are_combined_with_brief(self):
        with patch('dispatch.write_ownership.open_prs', return_value=('owner/repo', [])), \
             patch.object(dispatch, '_to_codex', return_value={'status': 'completed', 'termination_confirmed': True}), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(dispatch.main(['--registry', str(self.reg.path), '--results', str(self.results),
                'send', 'sol', 'Writes: src/*.py', '--writes', 'tests/*', '--writes', 'docs/*']), 0)
        row = json.loads(self.results.read_text().splitlines()[-1])
        self.assertEqual(row['writes'], ['tests/*', 'docs/*', 'src/*.py'])

    def test_catalog_or_github_failure_starts_zero_executors(self):
        with patch('dispatch.write_ownership.open_prs', side_effect=desks.DeskError('ownership_unreadable', 'no PR read')), \
             patch.object(dispatch, '_to_codex') as run:
            with self.assertRaises(desks.DeskError):
                self.send(writes=['tools/*'])
            run.assert_not_called()

    def test_other_repo_claim_does_not_block_and_invalid_ledger_refuses(self):
        self.active(repo='different/repo')
        with patch('dispatch.write_ownership.open_prs', return_value=('owner/repo', [])), \
             patch.object(dispatch, '_to_codex', return_value={'status': 'completed', 'termination_confirmed': True}) as run:
            self.send(writes=['tools/a.py'])
            self.results.write_text('{broken\n')
            run.reset_mock()
            with self.assertRaises(desks.DeskError):
                self.send(writes=['tools/a.py'])
            run.assert_not_called()

    def test_concurrent_claim_is_visible_to_second_dispatch(self):
        entered = threading.Event()
        release = threading.Event()
        errors = []
        def execute(*args, **kwargs):
            entered.set()
            release.wait(5)
            return {'status': 'completed', 'termination_confirmed': True}
        def first():
            try:
                self.send(writes=['new/*.py'])
            except Exception as exc:
                errors.append(exc)
        with patch('dispatch.write_ownership.open_prs', return_value=('owner/repo', [])), \
             patch.object(dispatch, '_to_codex', side_effect=execute) as run:
            worker = threading.Thread(target=first)
            worker.start()
            try:
                self.assertTrue(entered.wait(5))
                with self.assertRaises(desks.DeskError):
                    self.send(writes=['new/*'])
            finally:
                release.set()
                worker.join(5)
            self.assertEqual(run.call_count, 1)
        self.assertFalse(errors)

    def test_glob_intersection_handles_disjoint_and_unicode_classes(self):
        for left, right, expected in [
                ('src/*.py', 'src/*.js', False), ('src/a*', 'src/*z', True),
                ('src/[a-f].py', 'src/[d-z].py', True),
                ('src/[a-c].py', 'src/[d-z].py', False),
                ('src/[!a-z].py', 'src/a.py', False),
                ('src/[α-ω].py', 'src/[λ-ψ].py', True),
                ('src/?.py', 'src/new.py', False)]:
            with self.subTest(left=left, right=right):
                self.assertEqual(write_ownership.overlaps(left, right), expected)

    def test_executor_exception_without_termination_keeps_claim(self):
        with patch('dispatch.write_ownership.open_prs', return_value=('owner/repo', [])), \
             patch.object(dispatch, '_to_codex', side_effect=RuntimeError('failed')):
            with self.assertRaises(RuntimeError):
                self.send(writes=['src/*'])
        with patch('dispatch.write_ownership.open_prs', return_value=('owner/repo', [])), \
             patch.object(dispatch, '_to_codex') as run:
            with self.assertRaisesRegex(desks.DeskError, 'stuck'):
                self.send(writes=['src/*'])
            run.assert_not_called()

    def test_crash_after_reservation_recovers_without_a_timer(self):
        code = '''
import os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import write_ownership
write_ownership.open_prs = lambda cwd: ('owner/repo', [])
write_ownership.reserve(Path(sys.argv[2]),
    {'msg_id': 'crashed', 'desk': 'sol', 'task': 'build'}, sys.argv[3], ['src/*'])
os._exit(17)
'''
        crashed = subprocess.run([sys.executable, '-c', code,
            str(Path(dispatch.__file__).parent), str(self.results), str(self.root)],
            capture_output=True, text=True)
        self.assertEqual(crashed.returncode, 17, crashed.stderr)
        with patch('dispatch.write_ownership.open_prs', return_value=('owner/repo', [])), \
             patch.object(dispatch, '_to_codex', return_value={'status': 'completed', 'termination_confirmed': True}):
            self.assertEqual(self.send(writes=['src/*'])['status'], 'completed')
        recovered = [json.loads(line) for line in self.results.read_text().splitlines()
                     if json.loads(line).get('msg_id') == 'crashed'][-1]
        self.assertEqual(recovered['ownership_state'], 'released')
        self.assertIn('terminated', recovered['ownership_detail'])

    def test_timeout_without_termination_keeps_claim_and_blocks_second_turn(self):
        sock = str(self.root / 'live.sock')
        server = FakeAppServer(sock, silent_turn=True)
        self.addCleanup(server.close)
        self.reg.register('live', 'codex-live', socket=sock, family='sol',
                          effort='high', cwd=str(self.root))
        with patch('dispatch.write_ownership.open_prs', return_value=('owner/repo', [])):
            row = dispatch.dispatch('live', 'Writes: src/*\nbuild', registry=self.reg,
                results_path=self.results, codex_timeout_s=0.1)
            self.assertEqual(row['status'], 'timed_out')
            self.assertFalse(row.get('termination_confirmed', True))
            self.assertEqual(row['ownership_state'], 'held')
            self.assertIn('stuck', row['ownership_detail'])
            self.assertIn('turn/interrupt', server.methods())
            with self.assertRaisesRegex(desks.DeskError, 'stuck'):
                dispatch.dispatch('live', 'Writes: src/*\nbuild again', registry=self.reg,
                    results_path=self.results, codex_timeout_s=0.1)
        self.assertEqual(server.methods().count('turn/start'), 1)

    def test_a_live_reservation_is_not_recovered_even_if_old(self):
        with patch('dispatch.write_ownership.open_prs', return_value=('owner/repo', [])):
            write_ownership.reserve(self.results, {'msg_id': 'alive', 'desk': 'sol',
                'task': 'build', 'dispatched_at': '1999-01-01T00:00:00Z'}, str(self.root), ['src/*'])
            with patch.object(dispatch, '_to_codex') as run:
                with self.assertRaises(desks.DeskError):
                    self.send(writes=['src/*'])
                run.assert_not_called()

    def test_process_group_recovery_waits_for_surviving_child(self):
        proc = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],
                                start_new_session=True)
        def cleanup():
            if proc.poll() is None:
                proc.kill()
            proc.wait()
        self.addCleanup(cleanup)
        identity = {**write_ownership.process_owner(), 'pid': proc.pid, 'pgid': proc.pid,
                    'kind': 'process_group'}
        self.active(writes=['src/*'], ownership_state='held', executor=identity)
        with patch('dispatch.write_ownership.open_prs', return_value=('owner/repo', [])), \
             patch.object(dispatch, '_to_codex', return_value={'status': 'completed', 'termination_confirmed': True}) as run:
            with self.assertRaises(desks.DeskError):
                self.send(writes=['src/*'])
            run.assert_not_called()
            cleanup()
            self.assertEqual(self.send(writes=['src/*'])['status'], 'completed')

    def test_async_turn_reconciles_only_its_verified_terminal_status(self):
        sock = str(self.root / 'async.sock')
        server = FakeAppServer(sock, silent_turn=True)
        self.addCleanup(server.close)
        self.active(writes=['src/*'], status='delivered_live', ownership_state='held',
                    executor={'kind': 'codex_turn', 'socket': sock,
                              'thread_id': 'thread-live-0001', 'turn_id': 'turn-1'})
        with patch('dispatch.write_ownership.open_prs', return_value=('owner/repo', [])), \
             patch.object(dispatch, '_to_codex', return_value={'status': 'completed', 'termination_confirmed': True}) as run:
            with self.assertRaises(desks.DeskError):
                self.send(writes=['src/*'])
            run.assert_not_called()
            server.silent_turn = False
            self.assertEqual(self.send(writes=['src/*'])['status'], 'completed')
        self.assertEqual(server.methods().count('thread/read'), 2)

    def test_pid_probe_denied_or_from_another_host_keeps_claim(self):
        identity = write_ownership.process_owner()
        self.active(writes=['src/*'], ownership_state='held', owner_process=identity,
                    executor={'kind': 'reservation'})
        with patch('dispatch.write_ownership.open_prs', return_value=('owner/repo', [])), \
             patch('write_ownership.os.kill', side_effect=PermissionError), \
             patch.object(dispatch, '_to_codex') as run:
            with self.assertRaises(desks.DeskError):
                self.send(writes=['src/*'])
            run.assert_not_called()
        identity['host'] = 'somewhere-else'
        self.assertFalse(write_ownership.process_terminated(identity))

    def test_headless_codex_records_a_dedicated_group_and_releases_after_exit(self):
        binary = self.root / 'bin'
        binary.mkdir()
        probe = self.root / 'process.json'
        codex = binary / 'codex'
        codex.write_text(f'''#!{sys.executable}
import json, os, sys
from pathlib import Path
Path({str(probe)!r}).write_text(json.dumps({{'pid': os.getpid(), 'pgid': os.getpgrp()}}))
Path(sys.argv[sys.argv.index('-o') + 1]).write_text('built')
print(json.dumps({{'type': 'thread.started', 'thread_id': 'fake-thread'}}))
print(json.dumps({{'type': 'turn.completed'}}))
''')
        codex.chmod(0o755)
        with patch('dispatch.write_ownership.open_prs', return_value=('owner/repo', [])):
            row = self.send(writes=['src/*'], env={**os.environ, 'PATH': str(binary)})
            self.assertEqual(row['status'], 'completed', row)
            self.assertTrue(row['termination_confirmed'], row)
            self.assertEqual(row['ownership_state'], 'released')
            process = json.loads(probe.read_text())
            self.assertEqual(process['pid'], process['pgid'])
            self.assertEqual(row['executor']['pgid'], process['pgid'])
            self.assertEqual(row['executor']['kind'], 'process_group')
            self.assertEqual(self.send(writes=['src/*'], env={**os.environ, 'PATH': str(binary)})['status'], 'completed')

    def test_pr_reader_pages_files_and_preserves_renamed_path(self):
        reader = unittest.mock.Mock()
        reader.json.return_value = {'nameWithOwner': 'owner/repo'}
        reader.api.side_effect = [[{'number': 42, 'title': 'owner'}],
                                  [{'filename': 'src/new.py', 'previous_filename': 'src/old.py'}]]
        with patch.object(write_ownership, 'GitHubReader', return_value=reader):
            repo, prs = write_ownership.open_prs(str(self.root))
        self.assertEqual(repo, 'owner/repo')
        self.assertEqual(prs[0]['files'], ['src/new.py', 'src/old.py'])
        self.assertTrue(all(call.kwargs['paginate'] for call in reader.api.call_args_list))


if __name__ == '__main__':
    unittest.main(verbosity=2)
