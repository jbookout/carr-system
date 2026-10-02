#!/usr/bin/env python3
"""Behavioral headless runner tests; no model, login, launchd, or record writes."""
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


class HeadlessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.repo = self.home / 'carr-system'
        (self.repo / 'ops/headless-tasks').mkdir(parents=True)
        (self.repo / 'ops/headless-tasks/tasks.json').write_text(json.dumps({
            'timezone': 'America/Chicago', 'tasks': {
                'test-task': {'cron': '0 * * * *', 'timeout_seconds': 5400,
                              'grace_seconds': 60}}}))
        prompt = self.home / '.claude/scheduled-tasks/test-task/SKILL.md'
        prompt.parent.mkdir(parents=True)
        prompt.write_text('Run the test prompt.\n')
        self.fakebin = self.home / 'fakebin'
        self.fakebin.mkdir()
        fake = self.fakebin / 'claude'
        fake.write_text('''#!/usr/bin/env python3
import json, os, pathlib, sys, time
if sys.argv[1:3] == ['auth', 'status']:
    print(json.dumps({'loggedIn':True, 'authMethod':
          'api_key' if os.environ.get('FAKE_MODE') == 'api-auth' else 'claude.ai'}))
    sys.exit(0)
pathlib.Path(os.environ['FAKE_ARGS']).write_text(json.dumps({
    'argv': sys.argv[1:], 'prompt': sys.stdin.read(), 'cwd': os.getcwd(),
    'api_key_present': 'ANTHROPIC_API_KEY' in os.environ}))
mode = os.environ.get('FAKE_MODE', 'success')
if mode == 'timeout':
    time.sleep(60)
if mode == 'denied':
    print(json.dumps({'type':'system','subtype':'permission_denied'}), flush=True)
    time.sleep(60)
if mode == 'denied-result':
    print(json.dumps({'type':'result','subtype':'success','is_error':False,
                     'permission_denials':[{'tool_name':'Bash'}]}))
elif mode == 'invalid':
    print('not a result')
else:
    print(json.dumps({'type':'result','subtype':'success','is_error':False,
                     'permission_denials':[], 'result':'done'}))
sys.exit(7 if mode == 'failure' else 0)
''')
        fake.chmod(0o755)
        recorder = self.repo / 'run.sh'
        recorder.write_text('''#!/usr/bin/env python3
import json, os, pathlib, sys
p = pathlib.Path(os.environ['FAKE_RECORDS'])
with p.open('a') as f: f.write(json.dumps(sys.argv[1:])+'\\n')
print(json.dumps({'ok':True}))
''')
        recorder.chmod(0o755)
        self.env = {**os.environ, 'HOME': str(self.home),
                    'PATH': str(self.fakebin) + os.pathsep + os.environ['PATH'],
                    'FAKE_ARGS': str(self.home / 'args.json'),
                    'FAKE_RECORDS': str(self.home / 'records.jsonl')}

    def run_task(self, mode='success', timeout='3'):
        return subprocess.run([str(REPO / 'bin/headless-task'), 'test-task',
                               '--repo', str(self.repo), '--timeout-seconds', timeout],
                              env={**self.env, 'FAKE_MODE': mode},
                              capture_output=True, text=True, timeout=12)

    def ledger(self):
        return [json.loads(s) for s in (self.repo / 'out/headless/test-task/ledger.jsonl').read_text().splitlines()]

    def test_success_cli_and_private_log(self):
        result = self.run_task()
        self.assertEqual(result.returncode, 0, result.stderr)
        row = self.ledger()[0]
        self.assertEqual(row['exit_code'], 0)
        self.assertTrue(row['start'] <= row['end'])
        log = Path(row['log_path'])
        self.assertTrue(log.is_file())
        self.assertEqual(log.stat().st_mode & 0o777, 0o600)
        args = json.loads((self.home / 'args.json').read_text())
        self.assertIn('-p', args['argv'])
        self.assertIn('--no-session-persistence', args['argv'])
        self.assertEqual(args['argv'][args['argv'].index('--permission-mode')+1], 'dontAsk')
        self.assertEqual(args['argv'][args['argv'].index('--permission-prompts')+1], 'none')
        self.assertIn('--allowedTools', args['argv'])
        self.assertEqual(args['prompt'], 'Run the test prompt.\n')
        self.assertEqual(Path(args['cwd']), self.repo.resolve())
        self.assertFalse((self.home / 'records.jsonl').exists())

    def test_denial_event_fails_promptly(self):
        result = self.run_task('denied')
        self.assertEqual(result.returncode, 77, result.stderr)
        self.assertEqual(self.ledger()[0]['exit_code'], 77)
        self.assertIn('permission_denied', result.stdout)
        self.assertTrue((self.home / 'records.jsonl').exists())

    def test_denial_result_overrides_zero_exit(self):
        result = self.run_task('denied-result')
        diagnostic = result.stdout + result.stderr
        for log in (self.repo/'out/headless/test-task').glob('*.log'):
            diagnostic += log.read_text()
        self.assertEqual(result.returncode, 77, diagnostic)

    def test_timeout_is_logged_and_recorded(self):
        self.assertEqual(self.run_task('timeout', '0.15').returncode, 124)
        self.assertEqual(self.ledger()[0]['exit_code'], 124)
        self.assertTrue((self.home / 'records.jsonl').exists())

    def test_nonzero_preserved_and_failure_deduped_by_task(self):
        self.assertEqual(self.run_task('failure').returncode, 7)
        self.assertEqual(self.run_task('failure').returncode, 7)
        calls = [json.loads(s) for s in (self.home / 'records.jsonl').read_text().splitlines()]
        self.assertEqual(len(calls), 2)
        a, b = (json.loads(c[-1]) for c in calls)
        self.assertEqual(a['idempotency_key'], b['idempotency_key'])
        self.assertEqual(a, b)
        self.assertIn('test-task', a['idempotency_key'])
        self.assertEqual(calls[0][:2], ['call', 'add-loop'])

    def test_double_fire_skips_but_failure_can_retry(self):
        self.assertEqual(self.run_task('failure').returncode, 7)
        self.assertEqual(self.run_task().returncode, 0)
        (self.home / 'args.json').unlink()
        result = self.run_task()
        self.assertEqual(result.returncode, 0)
        self.assertIn('SKIP', result.stdout)
        self.assertFalse((self.home / 'args.json').exists())
        self.assertEqual(len(self.ledger()), 3)
        self.assertEqual(self.ledger()[-1]['status'], 'skipped')

    def test_invalid_zero_exit_is_failure(self):
        self.assertNotEqual(self.run_task('invalid').returncode, 0)

    def test_api_environment_not_forwarded(self):
        self.env['ANTHROPIC_API_KEY'] = 'synthetic-fixture-value'
        self.assertEqual(self.run_task().returncode, 0)
        self.assertFalse(json.loads((self.home / 'args.json').read_text())['api_key_present'])

    def test_log_redacts_credentials_before_storage(self):
        from lib.headless_tasks import log_text
        fixture_value = ''.join(['synthetic', '-private-value'])
        for text in [json.dumps({'access_token': fixture_value}),
                     json.dumps({'result': 'PASSWORD='+fixture_value}),
                     'api_key='+fixture_value, 'known='+fixture_value]:
            self.assertNotIn(fixture_value, log_text(text, [fixture_value]))
        self.assertNotIn(fixture_value, log_text(json.dumps({'access_token': fixture_value}), []))

    def test_api_login_is_refused_before_model_work(self):
        result = self.run_task('api-auth')
        self.assertEqual(result.returncode, 78, result.stderr)
        self.assertFalse((self.home/'args.json').exists())
        self.assertEqual(self.ledger()[0]['exit_code'], 78)

    def test_missed_run_warn_names_action_and_install_grace(self):
        from lib.headless_tasks import health_rows
        agents = self.home / 'Library/LaunchAgents'
        agents.mkdir(parents=True)
        installed = agents / 'com.carr.headless.test-task.plist'
        installed.write_bytes(plistlib.dumps({'Label': 'com.carr.headless.test-task'}))
        now = datetime.now(timezone.utc)
        rows = health_rows(self.repo, self.home, now)
        self.assertEqual(rows[0]['status'], 'OK')
        os.utime(installed, (now.timestamp()-7200, now.timestamp()-7200))
        rows = health_rows(self.repo, self.home, now)
        self.assertEqual(rows[0]['status'], 'WARN')
        self.assertIn('bin/headless-task test-task', rows[0]['line'])
        self.assertIn('owner:', rows[0]['line'])
        self.assertIn('verify:', rows[0]['line'])
        self.assertIn('auto-clear:', rows[0]['line'])
        self.assertEqual(self.run_task().returncode, 0)
        self.assertEqual(health_rows(self.repo, self.home, now+timedelta(seconds=1))[0]['status'], 'OK')
        rows = health_rows(self.repo, self.home, now+timedelta(hours=2))
        self.assertEqual(rows[0]['status'], 'WARN')

    def test_uninstalled_task_is_not_monitored(self):
        from lib.headless_tasks import health_rows
        self.assertEqual(health_rows(self.repo, self.home), [])

    def test_run_sh_health_entrypoint_warns_without_database(self):
        agents = self.home / 'Library/LaunchAgents'
        agents.mkdir(parents=True)
        installed = agents / 'com.carr.headless.radar-weekly.plist'
        installed.write_bytes(plistlib.dumps({'Label': 'com.carr.headless.radar-weekly'}))
        old = datetime.now(timezone.utc).timestamp()-9*86400
        os.utime(installed, (old, old))
        result = subprocess.run([str(REPO/'run.sh'), 'health', '--section', 'headless'],
                                env=self.env, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('WARN headless/radar-weekly', result.stdout)
        self.assertIn('bin/headless-task radar-weekly', result.stdout)

    def test_schedule_windows_weekend_and_month_gap(self):
        from lib.headless_tasks import schedule_window, schedule_interval
        friday = datetime(2026, 10, 2, 16, tzinfo=timezone.utc)
        monday = datetime(2026, 10, 5, 16, tzinfo=timezone.utc)
        self.assertEqual(schedule_window('0 11 * * 1-5', monday, 'America/Chicago'), monday)
        self.assertEqual(schedule_interval('0 11 * * 1-5', friday, 'America/Chicago'), 3*86400)
        self.assertGreater(schedule_interval('0 9 15-21 * *', datetime(2026, 10, 21, 14, tzinfo=timezone.utc), 'America/Chicago'), 20*86400)

    def test_committed_templates_match_manifest(self):
        from lib.headless_tasks import calendar_entries
        config = json.loads((REPO / 'ops/headless-tasks/tasks.json').read_text())
        for task_id, task in config['tasks'].items():
            plist = plistlib.loads((REPO / f'ops/headless-tasks/com.carr.headless.{task_id}.plist').read_bytes())
            self.assertEqual(plist['StartCalendarInterval'], calendar_entries(task['cron']))
            self.assertEqual(plist['ProgramArguments'][-1], task_id)
            self.assertNotIn('RunAtLoad', plist)
            self.assertNotIn('KeepAlive', plist)


if __name__ == '__main__':
    unittest.main(verbosity=2)
