"""Review reproductions for ownership, registry updates and Codex turn receipts."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import codex_checkout
import codex_wire
import desks
import dispatch
import write_ownership as ownership
from ops.git_env import fixture_env
import test_write_ownership_unit as ownership_tests


class ReviewRegressions(unittest.TestCase):
    setUp = ownership_tests.OwnershipTests.setUp
    run_adapter = ownership_tests.OwnershipTests.run_adapter
    completed = ownership_tests.OwnershipTests.completed
    send = ownership_tests.OwnershipTests.send
    active = ownership_tests.OwnershipTests.active

    def test_path_aliases_cannot_bypass_pr_or_job_ownership(self):
        for alias in ('src/./a.py', 'src//a.py', '././src/a.py'):
            for active in (False, True):
                with self.subTest(alias=alias, active=active):
                    self.ledger.unlink(missing_ok=True)
                    prs = [] if active else [{'number': 42, 'title': 'owner', 'files': ['src/a.py']}]
                    if active:
                        self.active(writes=['src/a.py'])
                    with patch.object(ownership, 'open_prs', return_value=('owner/repo', prs)):
                        with self.assertRaises(desks.DeskError):
                            ownership.reserve({'msg_id': 'alias', 'task': 'build'}, '.',
                                              ownership.declaration('', [alias]))

    def test_ownership_and_checkout_ignore_inherited_git_location(self):
        source, other = self.root / 'source', self.root / 'other'
        def git(repo, *args, env=None):
            return subprocess.run(['git', '-C', str(repo), *args], env=env or fixture_env(),
                                  check=True, capture_output=True, text=True).stdout.strip()
        for repo, name in ((source, 'source'), (other, 'other')):
            repo.mkdir()
            git(repo, 'init', '-b', 'main')
            git(repo, 'config', 'user.name', 'Fixture')
            git(repo, 'config', 'user.email', 'fixture@users.noreply.github.com')
            git(repo, 'config', 'room.repo', 'owner/' + name)
            git(repo, 'commit', '--allow-empty', '-m', 'seed')
            git(repo, 'remote', 'add', 'origin', str(repo))
        class Reader:
            def __init__(self, *, cwd, env=None):
                self.cwd, self.env = cwd, env
            def json(self, args):
                return {'nameWithOwner': subprocess.run(['git', 'config', 'room.repo'],
                    cwd=self.cwd, env=self.env, check=True, capture_output=True, text=True).stdout.strip()}
            def api(self, *args, **kwargs):
                return []
        with patch.dict(os.environ, {'GIT_DIR': str(other / '.git'), 'GIT_WORK_TREE': str(other)}), \
             patch.object(ownership, 'GitHubReader', Reader), \
             patch.object(codex_checkout, 'CANONICAL_REPO', source), \
             patch.object(codex_checkout, 'CHECKOUT_ROOT', self.root):
            row = ownership.reserve({'msg_id': 'git-env', 'task': 'build'}, str(source), ['src/a.py'])
            checkout = codex_checkout.prepare('main', str(source))
        self.assertEqual(row['repo'], 'owner/source')
        self.assertEqual(git(checkout['checkout_path'], 'remote', 'get-url', 'origin'), str(source))

    def test_gh_repo_override_does_not_redirect_ownership(self):
        with patch.dict(os.environ, {'GH_REPO': 'different/repo'}), \
             patch.object(ownership, 'GitHubReader') as reader:
            reader.return_value.json.return_value = {'nameWithOwner': 'owner/repo'}
            reader.return_value.api.return_value = []
            ownership.open_prs('.')
            self.assertIsNone(reader.call_args.kwargs['env'].get('GH_REPO'))

    def test_pr_snapshot_and_claim_release_are_serialized(self):
        with patch.object(ownership, 'open_prs', return_value=('owner/repo', [])):
            ownership.reserve({'msg_id': 'first', 'task': 'build'}, '.', ['src/a.py'])
        snapshot, resume, released = threading.Event(), threading.Event(), threading.Event()
        failures = []
        def read(cwd):
            snapshot.set()
            self.assertTrue(resume.wait(3))
            return 'owner/repo', []
        def reserve():
            try:
                ownership.reserve({'msg_id': 'second', 'task': 'build'}, '.', ['src/a.py'])
            except desks.DeskError as exc:
                failures.append(exc.code)
        def handoff():
            ownership.release('first', reason='PR published')
            released.set()
        with patch.object(ownership, 'open_prs', side_effect=read), \
             patch.object(ownership, 'termination_evidence', return_value='terminated fixture'):
            reader = threading.Thread(target=reserve)
            reader.start()
            self.assertTrue(snapshot.wait(3))
            writer = threading.Thread(target=handoff)
            writer.start()
            # The handoff no longer waits for the slow snapshot; a claim
            # released mid-scan still refuses the overlapping reservation.
            self.assertTrue(released.wait(3), 'handoff blocked behind the PR snapshot')
            resume.set()
            reader.join(3)
            writer.join(3)
        self.assertEqual(failures, ['write_set_overlap'])
        self.assertTrue(released.is_set())

    def test_registry_migration_cannot_overwrite_thread_update(self):
        path = self.root / 'legacy.json'
        path.write_text(json.dumps({'desks': {'sol': {'kind': 'codex-session',
            'model': 'gpt-5.1-codex-mini', 'thread_id': 'old'}}}))
        reader, writer = desks.Registry(path), desks.Registry(path)
        paused, resume = threading.Event(), threading.Event()
        save = reader._save
        errors = []
        def paused_save(data):
            paused.set()
            if not resume.wait(3):
                raise AssertionError('migration not resumed')
            save(data)
        def read():
            try:
                reader.entries()
            except Exception as exc:
                errors.append(exc)
        with patch.object(reader, '_save', side_effect=paused_save):
            first = threading.Thread(target=read)
            first.start()
            self.assertTrue(paused.wait(3))
            second = threading.Thread(target=writer.remember_thread, args=('sol', 'new-thread'))
            second.start()
            second.join(.15)
            resume.set()
            first.join(3)
            second.join(3)
        self.assertFalse(errors)
        self.assertEqual(writer.entries()['sol']['thread_id'], 'new-thread')

    def test_unsupported_owned_adapters_refuse_before_reservation(self):
        for kind in ('claude-session', 'claude-desktop', 'claude-remote', 'grok-cli', 'flash-local'):
            with self.subTest(kind=kind), \
                 patch.object(self.reg, 'resolve', return_value={'kind': kind, 'name': 'sol',
                     'model': 'fixture', 'effort': 'high'}), \
                 patch.object(ownership, 'reserve', side_effect=AssertionError('unsupported adapter reserved')) as reserve, \
                 patch.object(dispatch, '_execute') as execute:
                with self.assertRaises(desks.DeskError) as refused:
                    self.send(writes=['src/a.py'])
                self.assertEqual(refused.exception.code, 'unsupported_owned_adapter')
                reserve.assert_not_called()
                execute.assert_not_called()

    def test_desktop_disappears_then_headless_executor_can_bind(self):
        self.reg.remember_thread('sol', 'desktop-thread')
        def process(argv, env, timeout, on_executor, *args, **kwargs):
            on_executor({**ownership.process_owner(), 'kind': 'process_group',
                         'pid': 2147483647, 'pgid': 2147483647, 'start_time': 'fixture'})
            Path(argv[argv.index('-o') + 1]).write_text('headless answer')
            result = subprocess.CompletedProcess(argv, 0, '{"type":"turn.completed"}\n', '')
            result.termination_confirmed = True
            return result
        with patch.object(ownership, 'open_prs', return_value=('owner/repo', [])), \
             patch.object(dispatch.codex_ipc, 'thread_owner', return_value='owner'), \
             patch.object(dispatch.codex_ipc, 'start_turn', return_value={'status': 'not_live'}), \
             patch.object(dispatch, '_run_codex_process', side_effect=process):
            row = self.send(writes=['src/a.py'], live_desktop=True)
        self.assertEqual(row['status'], 'completed')
        self.assertEqual(row['ownership_state'], 'released')

    def test_live_no_launch_receipts_release_but_lost_start_stays_held(self):
        opened = {'id': 'thread-open', 'result': {'thread': {'id': 'thread'}}}
        for name, messages, expected in (
            ('rejected', [opened, {'id': 'turn-start', 'error': {'code': -32602, 'message': 'invalid parameters'}}], 'released'),
            ('setup', [EOFError('setup lost')], 'released'),
            ('lost-start', [opened, EOFError('start response lost')], 'held'),
            ('malformed-error', [opened, {'id': 'turn-start', 'error': None}], 'held')):
            self.ledger.unlink(missing_ok=True)
            def run(*args, **kwargs):
                return TurnRegressions().run_turn(messages, kwargs.get('on_executor'))
            with self.subTest(name=name), \
                 patch.object(self.reg, 'resolve', return_value={'kind': 'codex-live', 'name': 'sol',
                     'family': 'sol', 'effort': 'high', 'socket': '/fixture.sock'}), \
                 patch.object(ownership, 'open_prs', return_value=('owner/repo', [])), \
                 patch.object(codex_wire, 'run_turn', side_effect=run):
                with self.assertRaises((RuntimeError, EOFError)):
                    self.send(writes=['src/a.py'])
            row = json.loads(self.ledger.read_text().splitlines()[-1])
            self.assertEqual(row['ownership_state'], expected)
            self.assertEqual(ownership.reconcile(row['msg_id'])[0]['ownership_state'], expected)


class TurnRegressions(unittest.TestCase):
    wire_run_turn = staticmethod(codex_wire.run_turn)
    def run_turn(self, messages, on_executor=None):
        class Wire:
            def __init__(self, *args, **kwargs):
                self.sock = type('Socket', (), {'close': lambda self: None})()
            thread_id = None
            turn_id = None
            turn_requested = False
            deadline = None
            def send_json(self, value):
                pass
            def receive_json(self):
                value = next(stream)
                if isinstance(value, Exception):
                    raise value
                return value
        stream = iter(messages)
        run = self.wire_run_turn
        with patch.object(codex_wire, '_initialize'), patch.object(codex_wire, 'Wire', Wire):
            return run('/fixture.sock', 'build', thread_id=None, cwd=None,
                model='fixture', sandbox='workspace-write', approval_policy='never',
                timeout=1, on_executor=on_executor)

    def test_rejection_setup_failure_and_lost_start_have_distinct_ownership(self):
        opened = {'id': 'thread-open', 'result': {'thread': {'id': 'thread'}}}
        for name, messages, no_launch in (
            ('rejected', [opened, {'id': 'turn-start', 'error': {'code': -32602, 'message': 'invalid parameters'}}], True),
            ('setup', [EOFError('setup lost')], True),
            ('lost-start', [opened, EOFError('start response lost')], False)):
            identities = []
            with self.subTest(name=name):
                with self.assertRaises((RuntimeError, EOFError)):
                    self.run_turn(messages, identities.append)
                self.assertTrue(identities, 'ownership needs a setup/no-launch receipt')
                self.assertEqual(identities[-1]['kind'] == 'no_launch', no_launch)

    def test_answers_match_exact_thread_and_turn_before_and_after_start_response(self):
        def answer(thread, turn, text):
            return {'method': 'item/completed', 'params': {'threadId': thread,
                'turnId': turn, 'item': {'type': 'agentMessage', 'text': text}}}
        start = {'id': 'turn-start', 'result': {'turn': {'id': 'current'}}}
        opened = {'id': 'thread-open', 'result': {'thread': {'id': 'thread'}}}
        end = {'method': 'turn/completed', 'params': {'threadId': 'thread',
               'turn': {'id': 'current', 'status': 'completed'}}}
        for current in (False, True):
            for early in (False, True):
                with self.subTest(current=current, early=early):
                    notifications = [answer('thread', 'old', 'stale'), answer('other', 'current', 'foreign')]
                    if current:
                        notifications.append(answer('thread', 'current', 'current answer'))
                    messages = [opened, *notifications, start, end] if early else [opened, start, *notifications, end]
                    row = self.run_turn(messages)
                    self.assertEqual(row['result'], 'current answer' if current else '')


if __name__ == '__main__':
    unittest.main()
