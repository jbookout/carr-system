#!/usr/bin/env python3
"""Every production client route uses the Worker; injected runners stay offline."""
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import sqlite3
from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('jev_one_client', Path(__file__).with_name('typesafe_client.py'))
assert spec and spec.loader
client = importlib.util.module_from_spec(spec)
spec.loader.exec_module(client)
ANSWER = {'model': 'jev-1.13.0', 'answers': {'q': {'type': 'noul', 'noul': .8}},
          'usage': {'input_tokens': 100, 'output_tokens': 1}}

class AuthorityTests(unittest.TestCase):
    def test_spend_index_migration_extends_current_main(self):
        root = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location("migration_order", root / "ops/migration-order-gate.py")
        gate = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(gate)
        base = gate.tree_paths(root, "origin/main")
        local = {"migrations/" + path.name for path in (root / "migrations").glob("*.sql")}
        self.assertEqual(gate.violations(base, base, local), [])
        migrations = list((root / "migrations").glob("*_jev_spend_attempt_index.sql"))
        self.assertEqual(len(migrations), 1)
        fixture = (root / "mcp-server/test/jev-spend-postgres.mjs").read_text()
        self.assertIn(migrations[0].name, fixture)

    def test_hook_and_runtime_use_worker_without_local_key_or_vendor(self):
        for kwargs in ({}, {'work_class': 'app_runtime'}):
            with self.subTest(kwargs=kwargs), tempfile.TemporaryDirectory() as tmp:
                calls = []
                def worker(argv, **options):
                    args = json.loads(argv[3])
                    if args.get('transport_mode') == 'cache_only':
                        return subprocess.CompletedProcess(argv, 1, '', 'TOOL ERROR '+json.dumps(
                            {'error':'jev_cache_miss','spend_authority':client.SPEND_AUTHORITY}))
                    calls.append(args)
                    return subprocess.CompletedProcess(argv, 0, json.dumps({**ANSWER, 'ok': True, 'receipt_id': 'r'}), '')
                with patch.dict(os.environ, CARR_JEV_IN_HOOK='1', CARR_JEV_OFFLINE='0', CARR_HOOK_FIXTURE='0'), \
                     patch.object(__import__("urllib.request", fromlist=["request"]), 'urlopen', side_effect=AssertionError('vendor bypass')):
                    answer = client.ask('state', {'q': client.noul('uncertain?')}, caller='jev_deal_read',
                      session_id='native-session-123', server_runner=worker, calls_log=tmp+'/log', **kwargs)
                self.assertEqual(answer['answers'], ANSWER['answers'])
                self.assertEqual(len(calls), 1)
                self.assertEqual(calls[0]['state']['jev_attribution']['caller'], 'jev_deal_read')
                self.assertEqual(calls[0]['state']['input'], 'state')

    def test_worker_failure_never_falls_back_or_retries_paid_transport(self):
        with tempfile.TemporaryDirectory() as tmp:
            seen = []
            def fail(argv, **options):
                if json.loads(argv[3]).get('transport_mode') == 'cache_only':
                    return subprocess.CompletedProcess(argv, 1, '', 'TOOL ERROR '+json.dumps(
                        {'error':'jev_cache_miss','spend_authority':client.SPEND_AUTHORITY}))
                seen.append(argv)
                return subprocess.CompletedProcess(argv, 1, '', 'TOOL ERROR {"error":"vendor_credit_exhausted"}')
            with patch.dict(os.environ, CARR_JEV_OFFLINE='0', CARR_HOOK_FIXTURE='0'), \
                 patch.object(__import__("urllib.request", fromlist=["request"]), 'urlopen', side_effect=AssertionError('vendor bypass')):
                with self.assertRaises(client.TypeSafeError):
                    client.ask('state', {'q': client.noul('uncertain?')}, caller='jev_deal_read',
                      session_id='native-session-123', server_runner=fail, calls_log=tmp+'/log')
            self.assertEqual(len(seen), 1)
            self.assertIn('vendor_credit_exhausted', Path(tmp+'/log').read_text())


    def test_old_worker_refuses_before_attribution_or_paid_call(self):
        seen = []
        def old_worker(argv, **options):
            seen.append(json.loads(argv[3]))
            return subprocess.CompletedProcess(argv, 1, '', 'TOOL ERROR {"error":"jev_cache_miss"}')
        with patch.dict(os.environ, CARR_JEV_OFFLINE='0', CARR_HOOK_FIXTURE='0'):
            result, error = client.server_ask('private state', {'q': client.noul('judge')},
                model='jev-1.13.0', facets=[], purpose='call', session_id='private-session',
                timeout=10, transport_mode='paid_once', caller='jev_deal_read', runner=old_worker)
        self.assertIsNone(result)
        self.assertEqual(error, 'jev_spend_authority_unavailable')
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0]['transport_mode'], 'cache_only')
        self.assertNotIn('private', json.dumps(seen[0]))

    def test_local_registry_cannot_refuse_worker_admission(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ,
                CARR_JEV_OFFLINE='0', CARR_HOOK_FIXTURE='0'), patch.object(client,
                'load_call_sites', side_effect=AssertionError('duplicate local authority')), patch.object(
                client, 'server_ask', return_value=(dict(ANSWER), None)):
            answer = client.ask('state', {'q': client.noul('judge')}, caller='new-worker-site',
                session_id='native-session', calls_log=tmp+'/log')
        self.assertEqual(answer['answers'], ANSWER['answers'])

    def test_client_has_no_direct_vendor_transport(self):
        source = Path(client.__file__).read_text()
        self.assertNotIn('Authorization', source)
        self.assertNotIn('urllib.request.Request', source)
        self.assertFalse(hasattr(client, 'read_api_key'))

    def test_pause_is_worker_observation_not_retired_local_counter(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(client, 'JEV_DAILY_CAP_LOG', tmp+'/log'):
            with sqlite3.connect(client._cap_db_path()) as db:
                db.execute('create table daily_cap (day text, attempts integer)')
                db.execute('insert into daily_cap values (?, 9999)',
                           (datetime.now(timezone.utc).date().isoformat(),))
            self.assertIsNone(client.active_pause())
            resets = (datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()
            def refuse(argv, **options):
                if json.loads(argv[3]).get('transport_mode') == 'cache_only':
                    return subprocess.CompletedProcess(argv, 1, '', 'TOOL ERROR '+json.dumps(
                        {'error':'jev_cache_miss','spend_authority':client.SPEND_AUTHORITY}))
                return subprocess.CompletedProcess(argv, 1, '', 'TOOL ERROR '+json.dumps(
                    {'error':'vendor_credit_exhausted','resets_at':resets}))
            with patch.dict(os.environ, CARR_JEV_OFFLINE='0', CARR_HOOK_FIXTURE='0'):
                with self.assertRaises(client.JevCallRefused) as raised:
                    client.ask('state', {'q': client.noul('uncertain?')}, caller='jev_deal_read',
                        session_id='native-session-123', server_runner=refuse, calls_log=tmp+'/log')
            self.assertEqual(raised.exception.resets_at, resets)
            self.assertEqual(client.active_pause()['scope'], 'vendor_credit_exhausted')


class SpendTests(unittest.TestCase):
    def test_daily_cost_names_rate_and_counts_worker_receipt_once(self):
        import sys
        sys.path.insert(0, str(Path(__file__).parent))
        import jev_spend_health as spend
        from datetime import datetime, timezone
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp)/'calls.jsonl'
            log.write_text(json.dumps({'ts':'2026-10-04T01:00:00Z', 'ok':True,
              'server_receipt_id':'r', 'usage':{'input_tokens':1000000}})+'\n')
            line = spend.check_spend(log, state_path=Path(tmp)/'state',
                run_verb=lambda *a: self.fail('no alarm below threshold'),
                now=datetime(2026,10,4,tzinfo=timezone.utc),
                worker_usage=lambda day:{'calls':1,'input_tokens':1000000,'unknown':0})
        self.assertIn('$0.042 estimated', line)
        self.assertIn('$0.042/M', line)

    def test_worker_usage_failure_keeps_local_lower_bound_and_daily_cost_line(self):
        import jev_spend_health as spend
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp)/'calls.jsonl'
            log.write_text(json.dumps({'ts':'2026-10-04T01:00:00Z', 'ok':True,
              'server_receipt_id':'r', 'usage':{'input_tokens':1000000}})+'\n')
            def unavailable(day): raise RuntimeError('offline fake')
            line = spend.check_spend(log, state_path=Path(tmp)/'state',
                run_verb=lambda *a: self.fail('unavailable authority must retain alarm'),
                now=datetime(2026,10,4,tzinfo=timezone.utc), worker_usage=unavailable)
        self.assertTrue(line.startswith('UNKNOWN jev spend'))
        self.assertIn('$0.042', line)
        self.assertIn('1000000 input tokens', line)
        self.assertIn('Worker usage unavailable', line)

if __name__ == '__main__': unittest.main()
