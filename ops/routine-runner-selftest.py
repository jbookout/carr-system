#!/usr/bin/env python3
from datetime import datetime
import importlib.util
import io
import json
from pathlib import Path
import sys
import unittest
import subprocess
from unittest.mock import patch
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location('routine_runtime', ROOT / 'tools/routines/runtime.py')
assert spec and spec.loader
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


class RunnerTests(unittest.TestCase):
    def test_week_gate(self):
        now = datetime(2026, 10, 8, 9, tzinfo=ZoneInfo('America/Chicago'))
        self.assertTrue(runtime.due('contact-enrichment-weekly', now, False))
        self.assertFalse(runtime.due('contact-enrichment-weekly', now, True))
        self.assertFalse(runtime.due('lead-signals-weekly', now, False))

    def test_jsonl_parser(self):
        data = '{"type":"thread.started"}\n{"type":"item.completed","item":{"type":"agent_message","text":"{\\"facts\\":[]}"}}\n'
        self.assertEqual(runtime.parse_model_output(data), {'facts': []})
        for bad in ('', '{}', '{"type":"item.completed","item":{"type":"agent_message","text":"not JSON"}}'):
            with self.assertRaises(ValueError):
                runtime.parse_model_output(bad)

    def test_dry_run_has_no_effects(self):
        ctx = runtime.Context('contact-enrichment-weekly', dry_run=True, fixture={})
        with patch.object(runtime, 'call_verb', side_effect=AssertionError('production write')):
            self.assertEqual(ctx.write('record-finding', {'kind': 'email'}, 'finding:1')['dry_run'], True)
            ctx.review_item('Review a correction', 'Evidence body', 'review:1')
        self.assertEqual(len(ctx.effects), 2)
        with self.assertRaises(RuntimeError):
            ctx.model('ops/routines/prompts/contact-enrichment.txt', {})

    def test_no_work_does_not_model_or_stamp(self):
        class Module:
            @staticmethod
            def prepare(ctx): return {'work': False}
            @staticmethod
            def execute(ctx, plan): raise AssertionError('no-work execution')
        ctx = runtime.Context('contact-enrichment-weekly', dry_run=True, fixture={})
        with patch.object(ctx, 'stamp', side_effect=AssertionError('no-work stamp')):
            with patch('sys.stdout', new_callable=io.StringIO) as out:
                self.assertEqual(runtime.run_module(ctx, Module), 0)
                self.assertEqual(out.getvalue(), '')

    def test_blocked_run_cannot_stamp(self):
        class Module:
            @staticmethod
            def prepare(ctx): return {'work': True}
            @staticmethod
            def execute(ctx, plan): return {'blocked': True, 'reason': 'missing draft transport'}
        ctx = runtime.Context('social-weekly', dry_run=True, fixture={})
        with patch.object(ctx, 'stamp', side_effect=AssertionError('blocked stamp')):
            with patch('sys.stdout', new_callable=io.StringIO):
                self.assertEqual(runtime.run_module(ctx, Module), 78)

    def test_blocked_plan_rechecks_preflight_after_repair(self):
        from tools.routines import social_weekly
        ctx = runtime.Context('social-weekly', dry_run=True, fixture={}, now=datetime.fromisoformat('2026-10-09T08:00:00-05:00'))
        ctx.state['plan'] = {'work': True, 'reason': 'missing_blotato_key', 'week': '2026-10-12'}
        ctx.state['blocked'] = 'missing_blotato_key'
        with patch.object(ctx, 'secret', return_value=True), patch('sys.stdout', new_callable=io.StringIO) as out:
            ctx.dry_run = False
            with patch.object(ctx, 'save'), patch.object(ctx, 'review_item'):
                self.assertEqual(runtime.run_module(ctx, social_weekly), 78)
            self.assertEqual(json.loads(out.getvalue())['blocked'], 'draft_transport_unavailable')

    def test_subscription_cli_no_paid_credentials(self):
        argv, env = runtime.model_command(ROOT, {'OPENAI_API_KEY': 'forbidden', 'CARR_DB_JOBS_URL': 'forbidden', 'PATH': '/bin'})
        self.assertIn('gpt-6.1-sol', argv)
        self.assertIn('forced_login_method="chatgpt"', argv)
        self.assertIn('model_reasoning_effort="medium"', argv)
        self.assertNotIn('OPENAI_API_KEY', env)
        self.assertNotIn('CARR_DB_JOBS_URL', env)

    def test_subscription_auth_failure_reaches_app_before_model_spend(self):
        ctx = runtime.Context('contact-enrichment-weekly')
        status = subprocess.CompletedProcess(['codex', 'login', 'status'], 1, stdout='', stderr='Not logged in')
        with patch.object(runtime.subprocess, 'run', return_value=status) as calls, patch.object(ctx, 'review_item') as review:
            with self.assertRaises(RuntimeError):
                ctx.model('ops/routines/prompts/contact-enrichment.txt', {})
            self.assertEqual(calls.call_count, 1)
            review.assert_called_once()

    def test_replaced_claude_entrypoints_are_code_owned(self):
        from lib.headless_tasks import code_owned_tasks
        owned = code_owned_tasks(ROOT)
        self.assertIn('radar-weekly', owned)
        self.assertIn('health-audit-monthly', owned)
        self.assertNotIn('deal-history-research-weekly', owned)

    def test_dedicated_launchd_jobs_are_primary_only(self):
        import importlib.util
        from lib.launchd_scope import allowed_on_machine
        spec = importlib.util.spec_from_file_location('routine_config', ROOT / 'ops/config-as-code.py')
        config = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(config)
        for job in runtime.JOBS.values():
            name = job['label'] + '.plist'
            self.assertIn(name, config.DEDICATED_INSTALL)
            self.assertNotIn(name, config.DEFINITION_ONLY)
            self.assertFalse(allowed_on_machine(name, False))


if __name__ == '__main__':
    unittest.main()
