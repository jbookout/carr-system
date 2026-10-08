#!/usr/bin/env python3
"""Conflicting jobs stop before launch, including paths not created yet."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import desks
import dispatch
import write_ownership
from test_codex_models_unit import catalog_fixture


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
            return {'status': 'completed'}
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
             patch.object(dispatch, '_to_codex', return_value={'status': 'completed'}):
            self.assertEqual(self.send()['status'], 'completed')
        prs.assert_not_called()
        self.assertIn('no write set', warning.getvalue())
        self.assertEqual(len(self.results.read_text().splitlines()), 1)

    def test_completed_claim_released_but_async_delivery_stays_owned(self):
        self.active(status='delivered_live')
        with patch('dispatch.write_ownership.open_prs', return_value=('owner/repo', [])), \
             patch.object(dispatch, '_to_codex', return_value={'status': 'completed'}):
            with self.assertRaises(desks.DeskError):
                self.send(writes=['tools/a.py'])
            with self.results.open('a') as fh:
                fh.write(json.dumps({'msg_id': 'other', 'status': 'completed'}) + '\n')
            self.assertEqual(self.send(writes=['tools/a.py'])['status'], 'completed')
            self.assertEqual(self.send(writes=['tools/a.py'])['status'], 'completed')

    def test_cli_repeatable_writes_are_combined_with_brief(self):
        with patch('dispatch.write_ownership.open_prs', return_value=('owner/repo', [])), \
             patch.object(dispatch, '_to_codex', return_value={'status': 'completed'}), \
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
             patch.object(dispatch, '_to_codex', return_value={'status': 'completed'}) as run:
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
            return {'status': 'completed'}
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

    def test_executor_exception_releases_claim(self):
        with patch('dispatch.write_ownership.open_prs', return_value=('owner/repo', [])), \
             patch.object(dispatch, '_to_codex', side_effect=RuntimeError('failed')):
            with self.assertRaises(RuntimeError):
                self.send(writes=['src/*'])
        with patch('dispatch.write_ownership.open_prs', return_value=('owner/repo', [])), \
             patch.object(dispatch, '_to_codex', return_value={'status': 'completed'}):
            self.send(writes=['src/*'])

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
