import importlib.util
import json
import unittest
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path

SPEC = importlib.util.spec_from_file_location('system_costs', Path(__file__).with_name('system_costs.py'))
assert SPEC is not None and SPEC.loader is not None
costs = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(costs)


class MonthlyCosts(unittest.TestCase):
    def test_ai_fixture_costs_use_vendor_money_units(self):
        fixture = json.loads((Path(__file__).parent / 'fixtures/system-costs/ai.json').read_text())
        self.assertEqual(costs.normalize('claude', fixture['claude'])['rows'][0]['usd'], 1.25)
        self.assertEqual(costs.normalize('openai', fixture['openai'])['rows'][0]['usd'], 2.5)

    def test_first_of_month_checks_last_complete_day_for_spike(self):
        config = {'budget_usd': 100, 'providers': {'jev': {'label': 'Jev', 'plan': 'usage', 'monthly_usd': 0}}}
        rows = [{'day': f'2026-09-{day:02}', 'usd': 1, 'driver': 'review'} for day in range(16, 30)]
        rows.append({'day': '2026-09-30', 'usd': 8, 'driver': 'review'})
        report = costs.summarize(config, {'jev': {'state': 'ready', 'rows': rows}}, date(2026, 9, 30), month='2026-10')
        self.assertEqual(report['alerts'][0]['kind'], 'daily_spike')
        self.assertEqual(report['providers'][0]['mtd_usd'], 0)

    def test_pending_add_recovers_lost_response_using_same_intent(self):
        report = {'state': 'ready', 'through': '2026-10-05', 'alerts': [
            {'provider': 'jev', 'driver': 'review', 'kind': 'daily_spike', 'amount_usd': 8, 'threshold_usd': 2}],
            'providers': [{'provider': 'jev', 'state': 'ready'}]}
        calls = []
        def interrupted(name, payload):
            calls.append((name, payload))
            raise TimeoutError('lost response')
        def recovered(name, payload):
            calls.append((name, payload))
            return {'ok': True, 'replayed': True, 'loop_id': 'loop-1'}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'loops.json'
            with self.assertRaises(TimeoutError):
                costs.reconcile(report, path, interrupted)
            costs.reconcile(report, path, recovered)
            self.assertEqual(calls[0], calls[1])
            self.assertIsNone(json.loads(path.read_text())['pending'])

    def test_old_jev_missing_receipt_does_not_poison_current_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'jev.jsonl'
            path.write_text(json.dumps({'ts': '2026-01-01T00:00:00Z', 'ok': True, 'usage': {}}))
            self.assertEqual(costs.jev_usage(path, .042, start=date(2026, 9, 1))['state'], 'ready')

    def test_scalar_jev_receipt_is_partial_and_does_not_abort(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'jev.jsonl'
            path.write_text('null')
            self.assertEqual(costs.jev_usage(path, .042)['state'], 'partial')

    def test_malformed_nested_snapshot_is_unavailable(self):
        report = costs.summarize({'budget_usd': 100, 'providers': {
            'github': {'label': 'GitHub', 'plan': 'Pro', 'monthly_usd': 4}}},
            {'github': {'state': 'ready', 'rows': []}}, date(2026, 10, 4), observed_at='2026-10-05T00:00:00Z')
        report['providers'][0]['daily'][0]['drivers'] = []
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'costs.json'
            path.write_text(json.dumps(report))
            self.assertEqual(costs.load_snapshot(path, datetime(2026, 10, 5, tzinfo=timezone.utc))['state'], 'unavailable')

    def test_billing_fixture_becomes_daily_cost_without_payment_details(self):
        fixture = json.loads((Path(__file__).parent / 'fixtures/system-costs/github.json').read_text())
        report = costs.summarize(
            {'budget_usd': 100, 'providers': {'github': {'label': 'GitHub', 'plan': 'Pro', 'monthly_usd': 4, 'budget_usd': 10}}},
            {'github': costs.normalize('github', fixture)}, through=date(2026, 10, 4))
        github = report['providers'][0]
        self.assertAlmostEqual(github['mtd_usd'], 2.516129, places=6)
        day = next(row for row in github['daily'] if row['day'] == '2026-10-03')
        self.assertEqual(day['drivers']['Actions Linux'], 2)
        self.assertNotIn('payment_metadata', json.dumps(report))
        self.assertNotIn('do-not-persist', json.dumps(report))

    def test_neon_fixture_prices_all_project_timeframes(self):
        fixture = json.loads((Path(__file__).parent / 'fixtures/system-costs/neon.json').read_text())
        normalized = costs.normalize('neon', fixture, {'compute_unit_seconds': {'usd': .106, 'units': 3600}})
        self.assertEqual(normalized['rows'], [{'day': '2026-10-04', 'usd': .212, 'driver': 'compute_unit_seconds'}])

    def test_neon_extra_branch_meter_is_hours_and_egress_allowance_is_monthly(self):
        fixture = {'projects': [{'periods': [{'consumption': [
            {'timeframe_start': '2026-10-01T00:00:00Z', 'metrics': [
                {'metric_name': 'extra_branches_month', 'value': 24},
                {'metric_name': 'public_network_transfer_bytes', 'value': 90000000000}]},
            {'timeframe_start': '2026-10-02T00:00:00Z', 'metrics': [
                {'metric_name': 'public_network_transfer_bytes', 'value': 20000000000}]}]}]}]}
        normalized = costs.normalize('neon', fixture, {
            'extra_branches_month': {'usd': .002, 'units': 1},
            'public_network_transfer_bytes': {'usd': .1, 'units': 1000000000, 'included_units_per_month': 100000000000}})
        self.assertEqual([r['usd'] for r in normalized['rows']], [.048, 0, 1])

    def test_jev_only_counts_billed_receipts_and_uses_call_sites(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'usage.jsonl'
            path.write_text('\n'.join(json.dumps(r) for r in [
                {'ts': '2026-10-04T01:00:00Z', 'caller': 'rule_selector', 'usage': {'input_tokens': 1000000}, 'ok': True},
                {'ts': '2026-10-04T01:00:00Z', 'usage': {}, 'ok': False, 'http_status': 403},
                {'ts': '2026-10-04T02:00:00Z', 'cache_hit': True, 'usage': {'input_tokens': 1000000}, 'ok': True}]))
            source = costs.jev_usage(path, .042)
            self.assertEqual(source['state'], 'ready')
            self.assertEqual(source['rows'], [{'day': '2026-10-04', 'usd': .042, 'driver': 'rule_selector'}])

    def test_spikes_use_previous_fourteen_complete_days_and_budget(self):
        config = {'budget_usd': 100, 'providers': {'jev': {'label': 'Jev', 'plan': 'usage', 'monthly_usd': 0, 'budget_usd': 60}}}
        rows = [{'day': f'2026-09-{day:02}', 'usd': 1, 'driver': 'review'} for day in range(21, 31)]
        rows += [{'day': f'2026-10-{day:02}', 'usd': 1, 'driver': 'review'} for day in range(1, 5)]
        rows += [{'day': '2026-10-05', 'usd': 8, 'driver': 'rule-selector'}]
        report = costs.summarize(config, {'jev': {'state': 'ready', 'rows': rows}}, date(2026, 10, 5))
        self.assertEqual(report['providers'][0]['call_sites'], {'review': 4, 'rule-selector': 8})
        self.assertEqual([(a['provider'], a['kind'], a['threshold_usd']) for a in report['alerts']],
                         [('jev', 'daily_spike', 2), ('jev', 'budget_projection', 60)])
        self.assertEqual(report['providers'][0]['projection_usd'], 74.4)

    def test_snapshot_load_rejects_stale_and_drops_unknown_fields(self):
        config = {'budget_usd': 100, 'providers': {'github': {'label': 'GitHub', 'plan': 'Pro', 'monthly_usd': 4}}}
        report = costs.summarize(config, {'github': {'state': 'ready', 'rows': []}}, date(2026, 10, 4),
                                 observed_at='2026-10-05T00:00:00Z')
        report['secret'] = 'never-print'
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'costs.json'
            path.write_text(json.dumps(report))
            self.assertNotIn('secret', costs.load_snapshot(path, datetime(2026, 10, 5, tzinfo=timezone.utc)))
            self.assertEqual(costs.load_snapshot(path, datetime(2026, 10, 7, tzinfo=timezone.utc))['state'], 'unavailable')

    def test_loop_reconciliation_deduplicates_and_reopens_after_clear(self):
        report = {'state': 'ready', 'through': '2026-10-05', 'alerts': [
            {'provider': 'jev', 'driver': 'review', 'kind': 'daily_spike', 'amount_usd': 8, 'threshold_usd': 2}],
            'providers': [{'provider': 'jev', 'state': 'ready'}]}
        writes = []
        def verb(name, payload):
            if name == 'read-loop':
                return {'loop': {'loop_id': payload['loop_id'], 'version': 1, 'status': 'open'}}
            writes.append((name, payload))
            return {'ok': True, 'loop_id': 'loop-1'}
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / 'loops.json'
            costs.reconcile(report, state, verb)
            costs.reconcile(report, state, verb)
            self.assertEqual([w[0] for w in writes], ['add-loop'])
            report['alerts'] = []
            report['through'] = '2026-10-06'
            report['providers'][0]['state'] = 'partial'
            self.assertEqual(costs.reconcile(report, state, verb), 1)
            self.assertEqual([w[0] for w in writes], ['add-loop'])
            report['providers'][0]['state'] = 'ready'
            costs.reconcile(report, state, verb)
            self.assertEqual([w[0] for w in writes], ['add-loop', 'close-loop'])
            report['alerts'] = [{'provider': 'jev', 'driver': 'review', 'kind': 'daily_spike', 'amount_usd': 9, 'threshold_usd': 2}]
            costs.reconcile(report, state, verb)
            self.assertNotEqual(writes[0][1]['idempotency_key'], writes[-1][1]['idempotency_key'])

    def test_collector_paginates_neon_without_persisting_raw_response(self):
        config = {'budget_usd': 100, 'providers': {'neon': {'label': 'Neon', 'plan': 'Launch', 'monthly_usd': 0}},
                  'neon': {'org_id': 'fixture-org', 'rates': {'compute_unit_seconds': {'usd': .106, 'units': 3600}}}}
        urls = []
        def get(url, headers):
            urls.append(url)
            if 'cursor=' not in url:
                return {'projects': [], 'pagination': {'cursor': 'page-two'}}
            return json.loads((Path(__file__).parent / 'fixtures/system-costs/neon.json').read_text())
        report = costs.collect(config, {'NEON_API_KEY': 'fixture-credential'}, get,
                               now=datetime(2026, 10, 5, tzinfo=timezone.utc))
        self.assertEqual(len(urls), 2)
        self.assertEqual(report['providers'][0]['mtd_usd'], .212)
        self.assertNotIn('fixture-credential', json.dumps(report))

    def test_neon_changed_plan_refuses_launch_price_estimate(self):
        config = {'budget_usd': 100, 'providers': {'neon': {'label': 'Neon', 'plan': 'Launch', 'monthly_usd': 0}},
                  'neon': {'org_id': 'fixture-org', 'plan': 'launch', 'rates': {}}}
        report = costs.collect(config, {'NEON_API_KEY': 'fixture-credential'},
            lambda *args: {'projects': [{'periods': [{'period_plan': 'scale', 'consumption': []}]}]},
            now=datetime(2026, 10, 5, tzinfo=timezone.utc))
        self.assertEqual(report['providers'][0]['state'], 'unavailable')

    def test_missing_billing_token_is_named_and_never_zero_coverage(self):
        config = {'budget_usd': 100, 'providers': {'cloudflare': {'label': 'Cloudflare', 'plan': 'unconfirmed', 'monthly_usd': None}}}
        report = costs.collect(config, {}, lambda *a: self.fail('no token means no request'),
                               now=datetime(2026, 10, 5, tzinfo=timezone.utc))
        self.assertEqual(report['state'], 'partial')
        self.assertIn('CLOUDFLARE_BILLING_READ_TOKEN', report['providers'][0]['reason'])

    def test_health_surface_prints_bound_response_for_unreadable_source(self):
        import subprocess
        result = subprocess.run([str(Path(__file__).resolve().parents[1] / '.venv/bin/python'),
                                 str(Path(__file__).with_name('health-check.py')), '--section', 'costs', '--fixture', '/dev/null'],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 1)
        self.assertIn('UNKNOWN system costs', result.stdout)
        for text in ('deduplicated loop', 'owner orchestrator', 'remediation', 'verify', 'auto-clear'):
            self.assertIn(text, result.stdout)

    def test_unknown_provider_text_invalidates_snapshot(self):
        report = costs.summarize({'budget_usd': 100, 'providers': {
            'github': {'label': 'GitHub', 'plan': 'Pro', 'monthly_usd': 4}}},
            {'github': {'state': 'ready', 'rows': []}}, date(2026, 10, 4), observed_at='2026-10-05T00:00:00Z')
        report['providers'][0]['reason'] = {'payment_metadata': 'never-persist'}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'costs.json'
            path.write_text(json.dumps(report))
            self.assertEqual(costs.load_snapshot(path, datetime(2026, 10, 5, tzinfo=timezone.utc))['state'], 'unavailable')


if __name__ == '__main__':
    unittest.main()
