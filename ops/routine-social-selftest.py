#!/usr/bin/env python3
import copy
import json
from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.routines import social_weekly as social

FIXTURE = json.loads((ROOT / 'ops/fixtures/routines/social-weekly.json').read_text())

class Context:
    now = datetime(2026, 10, 9, 9, 0, tzinfo=timezone.utc)
    dry_run = True
    def __init__(self, fixture=None, key=True):
        self.fixture = copy.deepcopy(FIXTURE if fixture is None else fixture)
        self.key = key
        self.effects = []
    def secret(self, name):
        self.assert_secret = name
        return 'fixture-secret' if self.key else None
    def read(self, verb, args):
        self.effects.append(('read', verb))
        raise AssertionError('dry run must use fixture reads')
    def review_item(self, title, body, key):
        self.effects.append(('review', key))
        return {'ok': True}
    def model(self, *args):
        raise AssertionError('must not invoke model')
    def write(self, *args):
        raise AssertionError('must not write')
    def http_json(self, *args):
        raise AssertionError('must not publish or schedule')

class SocialTests(unittest.TestCase):
    def test_missing_key_before_reads_and_model(self):
        ctx = Context(key=False)
        plan = social.prepare(ctx)
        self.assertEqual(plan['reason'], 'missing_blotato_key')
        self.assertEqual(ctx.effects, [])
        self.assertEqual(social.execute(ctx, plan)['writes'], 0)
    def test_live_transport_unavailable_before_reads_and_model(self):
        ctx = Context()
        ctx.fixture = None
        ctx.dry_run = False
        plan = social.prepare(ctx)
        self.assertEqual(plan['reason'], 'draft_transport_unavailable')
        result = social.execute(ctx, plan)
        self.assertEqual(result['model_calls'], 0)
        self.assertEqual(ctx.effects, [('review', 'social-weekly:draft-transport-unavailable')])
    def test_weekly_completion_skips(self):
        fixture = copy.deepcopy(FIXTURE)
        fixture['completed_week'] = '2026-10-12'
        self.assertFalse(social.prepare(Context(fixture))['work'])
    def test_rotation_has_local_and_one_next_slot(self):
        self.assertEqual(social.rotation('market-data'), ['local', 'practice-economics'])
        self.assertEqual(social.rotation('demographics'), ['local', 'carr-library'])
    def test_dry_run_lints_and_parses_without_effects(self):
        ctx = Context()
        result = social.execute(ctx, social.prepare(ctx))
        self.assertEqual(result['drafts'], 2)
        self.assertEqual(result['fuel'], 1)
        self.assertEqual(result['writes'], 0)
        self.assertEqual(result['model_calls'], 0)
        self.assertEqual(ctx.effects, [])
    def test_parser_cuts_already_banked_source_and_body(self):
        parsed = social.parse_output(FIXTURE['model_output'], ['https://www.census.gov/example'], [], datetime(2026,10,9,tzinfo=timezone.utc))
        self.assertEqual(parsed['fuel'], [])
        self.assertEqual(parsed['posts'], [])
    def test_parser_rejects_missing_citation(self):
        value = copy.deepcopy(FIXTURE['model_output'])
        del value['fuel'][0]['citation']['url']
        with self.assertRaises(ValueError):
            social.parse_output(value, [], [], Context.now)
    def test_parser_rejects_publish_and_unknown_properties(self):
        for key in ('publish', 'scheduledTime', 'target', 'accountId', 'quoteTweetId'):
            value = copy.deepcopy(FIXTURE['model_output'])
            value['posts'][0][key] = 'unsafe'
            with self.subTest(key=key), self.assertRaises(ValueError):
                social.parse_output(value, [], [], Context.now)
    def test_parser_rejects_future_source(self):
        value = copy.deepcopy(FIXTURE['model_output'])
        value['fuel'][0]['citation']['date'] = '2027-01-01'
        with self.assertRaises(ValueError):
            social.parse_output(value, [], [], Context.now)
    def test_parser_rejects_uncited_post(self):
        value = copy.deepcopy(FIXTURE['model_output'])
        value['posts'][0]['citation_urls'] = ['https://unknown.example/claim']
        with self.assertRaises(ValueError):
            social.parse_output(value, [], [], Context.now)
    def test_hard_lint_failure_blocks_preview(self):
        ctx = Context()
        ctx.fixture['model_output']['posts'][0]['text'] = 'Picture this: a lease.'
        with self.assertRaises(ValueError):
            social.execute(ctx, social.prepare(ctx))
    def test_non_friday_returns_no_work(self):
        ctx = Context()
        ctx.now = datetime(2026,10,8,tzinfo=timezone.utc)
        self.assertFalse(social.prepare(ctx)['work'])
    def test_timezone_predicate_uses_chicago_day(self):
        ctx = Context()
        ctx.now = datetime(2026, 10, 9, 1, tzinfo=timezone.utc)
        self.assertFalse(social.prepare(ctx)['work'])
    def test_empty_citable_result_is_valid(self):
        self.assertEqual(social.parse_output({'fuel': [], 'posts': [], 'dry_lanes': ['local: no new source']}, [], [], Context.now),
                         {'fuel': [], 'posts': [], 'dry_lanes': ['local: no new source']})
    def test_parser_rejects_quote_tweet_kind(self):
        value = copy.deepcopy(FIXTURE['model_output'])
        value['posts'][0]['kind'] = 'quote-tweet'
        with self.assertRaises(ValueError):
            social.parse_output(value, [], [], Context.now)
    def test_parser_rejects_stale_local_source(self):
        value = copy.deepcopy(FIXTURE['model_output'])
        value['fuel'][0]['citation']['date'] = '2025-10-01'
        with self.assertRaises(ValueError):
            social.parse_output(value, [], [], Context.now)
    def test_parser_dedups_exact_body(self):
        parsed = social.parse_output(FIXTURE['model_output'], [], [FIXTURE['model_output']['posts'][0]['text']], Context.now)
        self.assertEqual([post['platform'] for post in parsed['posts']], ['linkedin'])
    def test_unselected_rotation_lane_fails_preview(self):
        ctx = Context()
        ctx.fixture['model_output']['fuel'][0]['lane'] = 'demographics'
        with self.assertRaises(ValueError):
            social.execute(ctx, social.prepare(ctx))

if __name__ == '__main__':
    unittest.main()
