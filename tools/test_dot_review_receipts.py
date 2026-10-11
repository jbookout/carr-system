#!/usr/bin/env python3
"""Receipt provenance and author independence at the consumer interface."""
from contextlib import redirect_stderr
import io
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib import dot_review_receipts as receipts


class ReceiptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Path(self.temp.name)
        self.env = patch.dict('os.environ', {'CARR_DOT_REVIEW_RECEIPTS': self.temp.name})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.meta = {'repo': 'fixture/repo', 'pr': 1, 'sha': 'a' * 40}
        self.body = 'APPROVE\nReviewed-SHA: ' + self.meta['sha'] + '\nReviewer: ChatGPT Dot'
        self.pr = {'user': {'login': 'builder'}}
        self.commits = [{'author': {'login': 'other-builder'},
                         'commit': {'author': {'name': 'Other Builder', 'email': 'other@example.test'}}}]
        self.calls = []
        self.policy = {
            'verdict': lambda body, config: body.startswith('APPROVE\n'),
            'reviewed_header_sha': lambda body: body.splitlines()[1].split(': ', 1)[1],
            'trusted_commenter': lambda comment, config: comment.get('author_association') == 'OWNER',
        }

    def api(self, path):
        self.calls.append(path)
        if path == 'repos/fixture/repo/pulls/1':
            return self.pr
        if path == 'repos/fixture/repo/pulls/1/commits?per_page=100':
            return self.commits
        raise AssertionError(path)

    def record(self, body=None, meta=None, run='fixture-run', reviewer='dot-user'):
        return receipts.record(meta or self.meta, body or self.body, builder='invented-builder',
                               reviewer=reviewer, relay_run_id=run, branch_author='invented-author')

    def pair(self, body=None, meta=None, run='fixture-run', reviewer='dot-user'):
        receipts.record_run(meta or self.meta, body or self.body, reviewer=reviewer, relay_run_id=run)
        return self.record(body, meta, run, reviewer)

    def matching(self, body=None, meta=None):
        return receipts.matching(meta or self.meta, body or self.body, api=self.api)

    def assert_alarm_refusal(self, action):
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            self.assertIsNone(action())
        self.assertIn('DOT_REVIEW_RECEIPT_ALARM', stderr.getvalue())

    def test_record_without_run_is_refused(self):
        self.record()
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            self.assertIsNone(receipts.matching(self.meta, self.body))
        self.assertIn('receipt lacks a matching relay run', stderr.getvalue())
        self.assert_alarm_refusal(self.matching)
        self.assertEqual(self.calls, [])

    def test_genuine_run_and_receipt_are_accepted_using_live_authors(self):
        receipt = self.pair()
        self.assertEqual(self.matching(), receipt)
        self.assertEqual(self.calls, ['repos/fixture/repo/pulls/1',
                                     'repos/fixture/repo/pulls/1/commits?per_page=100'])

    def test_retry_is_idempotent_and_conflicting_reuse_is_refused(self):
        receipt = self.pair()
        self.assertEqual(self.pair(), receipt)
        self.assertEqual(len(list(self.store.rglob('*.json'))), 2)
        with self.assertRaisesRegex(ValueError, 'append-only'):
            self.record(reviewer='different-reviewer')
        with self.assertRaisesRegex(ValueError, 'append-only'):
            receipts.record_run(self.meta, self.body, reviewer='different-reviewer',
                                relay_run_id='fixture-run')

    def test_append_preserves_prior_entries_and_links_each_chain(self):
        first = self.pair()
        paths = sorted(self.store.rglob('*.json'))
        original = {p: p.read_bytes() for p in paths}
        second = self.pair(body=self.body + '\nSecond report', run='second-run')
        for path, content in original.items():
            self.assertEqual(path.read_bytes(), content)
        self.assertEqual(second['prev_sha256'], first['sha256'])
        self.assertEqual(self.matching(body=self.body + '\nSecond report'), second)

    def test_receipt_tampering_is_detected_even_for_unrelated_binding(self):
        self.pair()
        self.pair(body=self.body + '\nOther report', run='second-run')
        path = sorted((self.store / 'receipts').glob('*.json'))[-1]
        value = json.loads(path.read_text())
        path.write_text(json.dumps({**value, 'reviewer': 'forged-reviewer'}))
        self.assert_alarm_refusal(self.matching)

    def test_chain_gap_and_invalid_previous_hash_are_detected(self):
        self.pair()
        self.pair(body=self.body + '\nOther report', run='second-run')
        first, second = sorted((self.store / 'receipts').glob('*.json'))
        original = first.read_bytes()
        first.unlink()
        self.assert_alarm_refusal(self.matching)
        first.write_bytes(original)
        value = json.loads(second.read_text())
        second.write_text(json.dumps({**value, 'prev_sha256': '0' * 64}))
        self.assert_alarm_refusal(self.matching)

    def test_run_ledger_corruption_is_detected(self):
        self.pair()
        path = next((self.store / 'runs').glob('*.json'))
        path.write_text('{not-json')
        self.assert_alarm_refusal(self.matching)

    def test_deleted_receipt_tail_is_detected_from_separate_run_ledger(self):
        self.pair()
        next((self.store / 'receipts').glob('*.json')).unlink()
        self.assert_alarm_refusal(self.matching)
        self.assertEqual(self.calls, [])

    def test_run_must_match_receipt_binding_and_reviewer(self):
        receipts.record_run(self.meta, self.body + '\nDifferent report',
                            reviewer='dot-user', relay_run_id='fixture-run')
        self.record()
        self.assert_alarm_refusal(self.matching)

    def test_raw_json_attack_and_legacy_receipts_are_refused(self):
        binding = {'repo': self.meta['repo'], 'pr': self.meta['pr'], 'reviewed_sha': self.meta['sha'],
                   'body_sha256': hashlib.sha256(self.body.encode()).hexdigest()}
        directory = self.store / hashlib.sha256(json.dumps(binding, sort_keys=True).encode()).hexdigest()
        directory.mkdir()
        (directory / 'forged.json').write_text(json.dumps({
            'schema': 'carr-dot-review-receipt/v1', **binding, 'builder': 'builder',
            'reviewer': 'dot-user', 'branch_author': 'builder', 'relay_run_id': 'invented-run'}))
        self.assert_alarm_refusal(self.matching)

    def test_pr_and_commit_authors_are_checked_fresh_and_casefolded(self):
        self.pair()
        self.assertIsNotNone(self.matching())
        self.pr['user']['login'] = 'DOT-USER'
        self.assert_alarm_refusal(self.matching)
        self.pr['user']['login'] = 'builder'
        self.commits[0]['author']['login'] = 'DoT-UsEr'
        self.assert_alarm_refusal(self.matching)

    def test_known_commit_author_aliases_are_checked(self):
        self.pair()
        self.commits[0]['commit']['author']['name'] = 'DOT-USER'
        self.assert_alarm_refusal(self.matching)

    def test_unknown_authors_and_author_read_errors_fail_closed(self):
        self.pair()
        self.pr = {'user': {}}
        self.assert_alarm_refusal(self.matching)
        self.pr = {'user': {'login': 'builder'}}
        self.commits = [{'author': None}]
        self.assert_alarm_refusal(self.matching)
        def failed_api(path):
            raise OSError('fixture author read failure')
        self.assert_alarm_refusal(lambda: receipts.matching(self.meta, self.body, api=failed_api))
        self.assert_alarm_refusal(lambda: receipts.matching(self.meta, self.body))

    def test_receipt_binding_rejects_different_sha_body_repo_or_pr(self):
        self.pair()
        for meta in ({**self.meta, 'sha': 'b' * 40}, {**self.meta, 'repo': 'fixture/other'},
                     {**self.meta, 'pr': 2}):
            with self.subTest(meta=meta):
                self.assertIsNone(self.matching(meta=meta))
        self.assertIsNone(self.matching(body=self.body + '\nAdded body'))
        self.assertEqual(self.calls, [])

    def test_claimed_dot_comment_without_receipt_is_not_owner_approval(self):
        comment = {'id': 1, 'body': self.body, 'author_association': 'OWNER'}
        self.assertEqual(receipts.deciding([comment], 'fixture/repo', 1, policy=self.policy,
                                          config={}, api=self.api), (None, None))
        self.assertEqual(self.calls, [])

    def test_ordinary_trusted_comment_preserves_coverage_without_author_reads(self):
        comment = {'id': 1, 'body': 'APPROVE\nReviewed-SHA: ' + self.meta['sha'],
                   'author_association': 'OWNER'}
        self.assertEqual(receipts.deciding([comment], 'fixture/repo', 1, policy=self.policy,
                                          config={}, api=self.api), (comment, None))
        self.assertEqual(self.calls, [])

    def test_fenced_marker_and_stored_receipt_still_carry_review(self):
        body = 'APPROVE\nReviewed-SHA: ' + self.meta['sha'] + '\n```\nReviewer: ChatGPT Dot\n```'
        receipt = self.pair(body=body)
        comment = {'id': 1, 'body': body, 'author_association': 'NONE'}
        self.assertEqual(receipts.deciding([comment], 'fixture/repo', 1, policy=self.policy,
                                          config={}, api=self.api), (comment, receipt))


if __name__ == '__main__':
    unittest.main()
