#!/usr/bin/env python3
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('dot_review', ROOT / 'bin/dot-review.py')
dot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dot)
SHA = 'a' * 40

class RoutingTests(unittest.TestCase):
    def test_free_first_and_every_exception(self):
        cases = [({}, 'dot', 'free capacity'),
                 ({'delay': 1801}, 'codex', 'queue delay'),
                 ({'delay': 1800}, 'dot', 'free capacity'),
                 ({'labels': ['urgent']}, 'codex', 'urgent'),
                 ({'listed': True}, 'codex', 'urgent'),
                 ({'diff': '+with threading.Lock():'}, 'codex', 'concurrency/locking'),
                 ({'files': ['lib/locking.py']}, 'codex', 'concurrency/locking'),
                 ({'labels': ['hands-on-testing']}, 'codex', 'hands-on testing')]
        for args, seat, reason in cases:
            with self.subTest(args=args):
                self.assertEqual(dot.choose_route(**args), (seat, reason))

    def test_idle_adoption_requires_explicit_opt_in_and_keeps_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            orch = Path(tmp)
            source = orch / 'queue/codex'
            source.mkdir(parents=True)
            (source / 'yes.txt').write_text('dot-ok: true\nTask: read-only design critique')
            (source / 'no.txt').write_text('dot-ok: false\nTask: build')
            self.assertEqual(dot.adopt(orch), 1)
            self.assertEqual(dot.adopt(orch), 0)
            self.assertTrue((source / 'no.txt').exists())
            self.assertFalse((source / 'yes.txt').exists())
            self.assertIn('design critique', next((orch / 'dot/queue').glob('*.md')).read_text())

class RelayTests(unittest.TestCase):
    def test_authenticated_multipart_completion_posts_once_and_survives_restart(self):
        class Slack:
            channel = 'fixture-channel'
            messages = []
            def post(self, text, thread=None):
                return '1.000001'
            def replies(self, thread):
                return self.messages
        with tempfile.TemporaryDirectory() as tmp:
            state, repo = Path(tmp) / 'state', Path(tmp) / 'repo'
            repo.mkdir()
            slack = Slack()
            engine = dot.ReviewRelay(slack, state, repo, 'dot-user')
            meta = {'repo': 'jbookout/carr-system', 'pr': 1, 'sha': SHA}
            thread = engine.send_job('Dot-Review: ' + json.dumps(meta))
            slack.messages = [{'ts': '2.000001', 'user': 'other',
                               'text': 'REVIEW: BLOCKED\nReviewed-SHA: '+SHA+'\nDOT-REPORT-END'},
                              {'ts': '3.000001', 'user': 'dot-user', 'text': 'Still reading'},
                              {'ts': '4.000001', 'user': 'dot-user', 'text': 'APPROVE\nReviewed-SHA: '+SHA+'\nPart one'},
                              {'ts': '5.000001', 'user': 'dot-user', 'text': 'Part two\nDOT-REPORT-END'}]
            posts = []
            comments = []
            def api(path, **kw):
                if kw:
                    posts.append(kw['body'])
                    comments.append({'body': kw['body']})
                    return {'id': 1}
                if '/comments?' in path:
                    return comments
                return {'head': {'sha': SHA}, 'state': 'open'}
            actual = dot.publish
            def publish(*args, **kw):
                kw['api'] = api
                return actual(*args, **kw)
            with patch.object(dot, 'publish', side_effect=publish):
                self.assertTrue(engine.poll(thread, execute=True))
                restarted = dot.ReviewRelay(slack, state, repo, 'dot-user')
                self.assertTrue(restarted.poll(thread, execute=True))
            self.assertEqual(len(posts), 1)
            self.assertIn('Part one\nPart two', posts[0])
            self.assertNotIn('Still reading', posts[0])
            self.assertNotIn('REVIEW: BLOCKED', posts[0])


class EvidenceTests(unittest.TestCase):
    def test_runs_module_in_scratch_at_bound_head_with_bounded_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'source'
            source.mkdir()
            def git(*args):
                return subprocess.run(['git', '-C', str(source), *args], check=True,
                                      capture_output=True, text=True).stdout.strip()
            git('init', '-q')
            git('config', 'user.name', 'Fixture')
            git('config', 'user.email', 'fixture@example.invalid')
            (source / 'tools').mkdir()
            module = source / 'tools/test_area.py'
            module.write_text('import subprocess\nprint(subprocess.check_output(["git", "rev-parse", "HEAD"]).decode())\nprint("BOUND-HEAD-TEST")\n')
            git('add', 'tools/test_area.py')
            git('commit', '-qm', 'fixture')
            sha = git('rev-parse', 'HEAD')
            module.write_text('raise RuntimeError("wrong working tree")')
            evidence = dot.test_evidence('jbookout/carr-system', sha, ['tools/area.py'], origin=str(source))
            self.assertIn('BOUND-HEAD-TEST', evidence)
            self.assertIn('exit: 0', evidence)
            self.assertIn(sha, evidence)
            self.assertNotIn('wrong working tree', evidence)
            self.assertLessEqual(len(evidence), 12100)

class HandoffTests(unittest.TestCase):
    def test_request_wires_routing_dedup_evidence_and_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            orch = Path(tmp)
            def api(path):
                if '/files?' in path:
                    return [{'filename': 'tools/area.py', 'patch': '+print(1)'}]
                if '/comments?' in path:
                    return []
                return {'head': {'sha': SHA}, 'state': 'open', 'labels': []}
            calls = []
            def evidence(*args):
                calls.append(args)
                return 'LOCAL-TEST-OUTPUT exit: 0'
            result = dot.request('jbookout/carr-system', 1, orch=orch, api=api, evidence_runner=evidence)
            self.assertEqual(result['seat'], 'dot')
            brief = Path(result['brief']).read_text()
            self.assertEqual(dot.metadata(brief)['sha'], SHA)
            self.assertIn('LOCAL-TEST-OUTPUT', brief)
            self.assertEqual(dot.request('jbookout/carr-system', 1, orch=orch, api=api, evidence_runner=evidence), result)
            self.assertEqual(len(calls), 1)
            self.assertEqual(len((orch / 'review-routing.jsonl').read_text().splitlines()), 1)

    def test_paid_exception_handoff_names_codex_seat(self):
        with tempfile.TemporaryDirectory() as tmp:
            def api(path):
                if '/files?' in path or '/comments?' in path:
                    return []
                return {'head': {'sha': SHA}, 'state': 'open', 'labels': [{'name': 'urgent'}]}
            result = dot.request('jbookout/carr-system', 1, orch=tmp, api=api,
                                 evidence_runner=lambda *args: self.fail('Dot tests not needed for paid route'),
                                 paid_launcher=lambda receipt: {'status': 'dispatched', 'desk': 'fixture'})
            self.assertEqual(result['seat'], 'codex')
            self.assertEqual(result['reason'], 'urgent')
            self.assertIn('Model Room desk: Codex; family: sol', Path(result['brief']).read_text())


    def test_paid_dispatch_is_named_fresh_and_not_replayed(self):
        with tempfile.TemporaryDirectory() as tmp:
            brief = Path(tmp) / 'review.txt'
            brief.write_text('independent review')
            with patch.object(dot.subprocess, 'Popen') as spawn:
                spawn.return_value.pid = 12345
                first = dot.launch_paid({'brief': str(brief)})
                self.assertEqual(dot.launch_paid({'brief': str(brief)}), first)
                self.assertEqual(spawn.call_count, 1)
                argv = spawn.call_args.args[0]
                self.assertIn('--fresh', argv)
                self.assertEqual(argv[-5:], ['--family', 'sol', '--effort', 'high', '--fresh'])


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name) / 'thread'
        self.directory.mkdir()
        self.meta = {'repo': 'jbookout/carr-system', 'pr': 123, 'sha': SHA}
        self.comments = []
        self.posts = []
        self.head = SHA
        self.requeues = []

    def api(self, path, **kwargs):
        if kwargs:
            self.posts.append(kwargs['body'])
            self.comments.append({'body': kwargs['body']})
            return {'id': 42}
        if '/comments' in path:
            return self.comments
        return {'head': {'sha': self.head}, 'state': 'open'}

    def finish(self, verdict='APPROVE'):
        return dot.publish(self.directory, self.meta,
                           f'{verdict}\nReviewed-SHA: {SHA}\nNo blockers.\nDOT-REPORT-END',
                           self.api, self.requeues.append)

    def test_exact_head_one_comment_on_replay(self):
        self.assertEqual(self.finish(), 'posted')
        self.assertEqual(self.finish(), 'posted')
        self.assertEqual(len(self.posts), 1)
        self.assertEqual(self.posts[0].splitlines()[:2], ['APPROVE', 'Reviewed-SHA: '+SHA])
        self.assertIn('Reviewer: ChatGPT Dot', self.posts[0])

    def test_reposted_thread_shares_publication_receipt(self):
        self.finish()
        second = self.directory.parent / 'reposted-thread'
        second.mkdir()
        dot.publish(second, self.meta, 'APPROVE\nReviewed-SHA: '+SHA, self.api, self.requeues.append)
        self.assertEqual(len(self.posts), 1)

    def test_stale_posts_nothing_and_requeues_once(self):
        self.head = 'b' * 40
        self.assertEqual(self.finish(), 'stale')
        self.finish()
        self.assertEqual(self.posts, [])
        self.assertEqual(len(self.requeues), 1)
        self.assertEqual(self.requeues[0]['sha'], self.head)

    def test_blocked_preserved(self):
        self.finish('REVIEW: BLOCKED')
        self.assertTrue(self.posts[0].startswith('REVIEW: BLOCKED\n'))

    def test_wrong_sha_or_prose_cannot_authorize(self):
        for text in ('No blockers', 'APPROVE\nReviewed-SHA: '+ 'b'*40):
            with self.assertRaises(ValueError):
                dot.publish(self.directory, self.meta, text, self.api, self.requeues.append)
        self.assertEqual(self.posts, [])

    def test_ambiguous_post_is_not_repeated_and_can_reconcile(self):
        def uncertain(path, **kw):
            result = self.api(path, **kw)
            if kw:
                raise OSError('connection lost after write')
            return result
        with self.assertRaises(OSError):
            dot.publish(self.directory, self.meta, 'APPROVE\nReviewed-SHA: '+SHA, uncertain, self.requeues.append)
        self.assertEqual(self.finish(), 'posted')
        self.assertEqual(len(self.posts), 1)

    def test_uncertain_absent_receipt_refuses_retry(self):
        def uncertain(path, **kw):
            if kw:
                raise OSError('uncertain')
            return self.api(path)
        with self.assertRaises(OSError):
            dot.publish(self.directory, self.meta, 'APPROVE\nReviewed-SHA: '+SHA, uncertain, self.requeues.append)
        with self.assertRaisesRegex(ValueError, 'reconcile'):
            self.finish()
        self.assertEqual(self.posts, [])

if __name__ == '__main__':
    unittest.main()
