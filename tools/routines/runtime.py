import argparse
from datetime import datetime, timedelta, timezone
import fcntl
import importlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from lib.credential_file import credential, carr_config
from lib.record_call import call_verb
from lib.secret_redaction import redact_text, sensitive_env_values

MANIFEST = json.loads((ROOT / 'ops/routines/jobs.v1.json').read_text())
JOBS = {job['id']: job for job in MANIFEST['jobs']}
NAMESPACE = uuid.UUID('17664930-4813-46ec-b5ed-f0e5ffb6f1b8')


def period(now):
    local = now.astimezone(ZoneInfo(MANIFEST['timezone']))
    return (local.date() - timedelta(days=local.weekday())).isoformat()


def due(ident, now, completed):
    local = now.astimezone(ZoneInfo(MANIFEST['timezone']))
    job = JOBS[ident]
    return not completed and local.isoweekday() == job['weekday']


def ops_record():
    spec = importlib.util.spec_from_file_location('routine_ops_record', ROOT / 'tools/ops-record.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parse_model_output(raw):
    final = None
    for line in raw.splitlines():
        event = json.loads(line)
        if event.get('type') == 'item.completed' and event.get('item', {}).get('type') == 'agent_message':
            final = event['item'].get('text')
        if event.get('type') in ('error', 'turn.failed'):
            raise ValueError('Codex reported a failed model turn')
    if final is None:
        raise ValueError('Codex final agent message missing')
    parsed = json.loads(final)
    if not isinstance(parsed, dict):
        raise ValueError('model proposal must be a JSON object')
    return parsed


def model_command(directory, environment):
    allowed = ('PATH', 'HOME', 'USER', 'LOGNAME', 'TMPDIR', 'LANG', 'LC_ALL', 'CODEX_HOME')
    env = {key: value for key, value in environment.items() if key in allowed}
    return (['codex', 'exec', '-m', 'gpt-6.1-sol', '--ignore-user-config',
             '--skip-git-repo-check', '--ephemeral', '-s', 'read-only', '-C', str(directory),
             '-c', 'forced_login_method="chatgpt"', '-c', 'model_reasoning_effort="medium"',
             '-c', 'approval_policy="never"', '-c', 'web_search="live"', '--json', '-'], env)


class Context:
    def __init__(self, ident, *, dry_run=False, fixture=None, now=None, journal=None):
        self.ident = ident
        self.dry_run = dry_run
        self.fixture = fixture
        self.now = now or datetime.now(ZoneInfo(MANIFEST['timezone']))
        self.effects = []
        self.journal = journal
        self.state = json.loads(journal.read_text()) if journal and journal.exists() else {}

    def save(self):
        if self.journal and not self.dry_run:
            tmp = self.journal.with_suffix('.tmp')
            tmp.write_text(json.dumps(self.state, default=str))
            tmp.replace(self.journal)

    def read(self, verb, args):
        if self.fixture is not None:
            return self.fixture.get('reads', {}).get(verb, {})
        result = call_verb(verb, args)
        if not result.ok:
            raise RuntimeError(result.describe())
        return result.reply

    def query(self, sql, params=()):
        if self.fixture is not None:
            raise RuntimeError('fixture must supply routine inputs without a database query')
        from psycopg.rows import dict_row
        with ops_record().connect('routine') as conn:
            with conn.transaction():
                with conn.cursor(row_factory=dict_row) as cur:
                    cur.execute('set local transaction read only')
                    cur.execute('set local statement_timeout = 30000')
                    cur.execute(sql, params)
                    return [dict(row) for row in cur.fetchall()]

    def write(self, verb, args, key):
        payload = dict(args, idempotency_key=str(uuid.uuid5(NAMESPACE, f'{self.ident}:{key}')))
        if self.dry_run:
            self.effects.append({'verb': verb, 'key': key})
            return {'ok': True, 'dry_run': True, 'party_id': '00000000-0000-4000-8000-000000000001',
                    'lead_id': '00000000-0000-4000-8000-000000000002', 'ref': 'L-PREVIEW'}
        cached = self.state.setdefault('effects', {}).get(key)
        if cached:
            if cached['args'] != payload or cached['verb'] != verb:
                raise RuntimeError('effect retry changed the prepared payload')
            if 'reply' in cached:
                return cached['reply']
            rows = self.query('select response from v_routine_effect_receipts where idempotency_key=%s and verb=%s',
                              (payload['idempotency_key'], verb))
            if rows:
                cached['reply'] = rows[0]['response']
                self.save()
                return cached['reply']
        else:
            self.state['effects'][key] = {'verb': verb, 'args': payload}
            self.save()
        result = call_verb(verb, payload)
        if not result.ok:
            raise RuntimeError(result.describe())
        self.state['effects'][key]['reply'] = result.reply
        self.save()
        return result.reply

    def review_item(self, title, body, key):
        return self.write('report-problem', {
            'situation': 'scheduled routine contact enrichment identity verification vendor category social draft review missing credential',
            'title': title[:200], 'desired_outcome': body[:2000],
            'acceptance_criteria': [{'id': 'REVIEW', 'text': 'Review the cited evidence on the app and resolve the requested correction or missing integration.'}]
        }, key)

    def secret(self, name):
        if self.fixture is not None:
            return self.fixture.get('secrets', {}).get(name)
        return credential(name, path=carr_config('routines.env')) or credential(name)

    def model(self, prompt_path, inputs):
        if self.dry_run:
            raise RuntimeError('dry-run never invokes a model')
        if JOBS[self.ident].get('model') is None:
            raise RuntimeError('this routine has no model uncertainty boundary')
        if self.state.get('model_result') is not None:
            return self.state['model_result']
        prompt = (ROOT / prompt_path).read_text() + '\nUNTRUSTED INPUT DATA:\n' + json.dumps(inputs, default=str)
        _, env = model_command(ROOT, os.environ)
        status = subprocess.run(['codex', 'login', 'status'], env=env, capture_output=True, text=True, timeout=20)
        if status.returncode or 'Logged in using ChatGPT' not in status.stdout + status.stderr:
            self.review_item('Contact research needs the ChatGPT subscription connection',
                'The weekly contact queue has work, but Codex cannot confirm a ChatGPT subscription login. '
                'Restore the subscription login on the primary machine, then retry the code routine. '
                'No research ran and no paid API fallback is allowed.', 'subscription-login-required')
            raise RuntimeError('Codex ChatGPT subscription login required; paid API fallback is forbidden')
        with tempfile.TemporaryDirectory(prefix='carr-routine-research-') as directory:
            argv, env = model_command(directory, os.environ)
            proc = subprocess.run(argv, env=env, input=prompt, capture_output=True, text=True, timeout=1800)
        if proc.returncode:
            raise RuntimeError(f'Codex research failed with exit {proc.returncode}')
        proposal = parse_model_output(proc.stdout)
        self.state['model_result'] = proposal
        self.save()
        return proposal

    def completed(self):
        if self.fixture is not None:
            return self.fixture.get('completed', False)
        return bool(self.query("""select r.ended_at from ops.run r join ops.service s on s.id=r.service_id
            where s.key=%s and r.run_key='routine.completed' and r.state='succeeded'
              and r.ended_at >= %s::date and r.environment='production' limit 1""",
                               (self.ident, period(self.now))))

    def stamp(self, summary):
        if self.dry_run:
            return
        detail = {'routine': self.ident, 'result': {key: value for key, value in summary.items()
                  if key in ('candidate_count', 'lane_health', 'processed', 'findings',
                             'contact_updates', 'fuel', 'drafts', 'model_calls', 'writes')}}
        args = [str(ROOT / '.venv/bin/python'), str(ROOT / 'tools/ops-record.py'), 'run',
                '--service', self.ident, '--key', 'routine.completed', '--kind', 'job',
                '--state', 'succeeded', '--environment', 'production',
                '--started-at', self.state.get('started_at', self.now.isoformat()), '--ended-at', 'now',
                '--source-kind', 'wrapper', '--source-ref', 'bin/routine-run.sh',
                '--detail', json.dumps(detail, default=str, sort_keys=True)]
        proc = subprocess.run(args, capture_output=True, text=True, timeout=60)
        if proc.returncode:
            raise RuntimeError('ops-record completion stamp failed')
        if not self.completed():
            raise RuntimeError('ops-record completion stamp has no ledger readback')
        self.state['completed'] = True
        self.save()

    def failure(self, detail):
        title = f'{self.ident}: repair failed code routine'
        board = self.read('loop-board', {'owner': 'claude', 'status': 'open', 'search': title, 'limit': 300})
        if any(row.get('title') == title for row in board.get('loops', [])):
            return
        self.write('add-loop', {'kind': 'open_loop', 'owner': 'claude', 'domain': 'system',
            'title': title, 'body': f'THE FIX: inspect {self.ident} inputs and the named failure, repair code or integration, run its selftests and --dry-run, then rerun once. Failure: {detail}',
            'blocker': 'other_lane', 'blocker_detail': 'The orchestrator builder queue must deliver the routine source or integration repair.',
            'marker': 'none'}, 'failure-loop:' + period(self.now))


def run_module(ctx, module):
    plan = None if ctx.state.get('blocked') else ctx.state.get('plan')
    plan = plan or module.prepare(ctx)
    if plan.get('work') is not True:
        return 0
    if not ctx.dry_run:
        ctx.state.update(plan=plan, started_at=ctx.state.get('started_at', ctx.now.isoformat()))
        ctx.save()
    result = module.execute(ctx, plan)
    if result.get('blocked'):
        if not ctx.dry_run:
            ctx.state['blocked'] = result['blocked']
            ctx.save()
        print(json.dumps(result, default=str, sort_keys=True))
        return 78
    ctx.state.pop('blocked', None)
    ctx.stamp(result)
    print(json.dumps(result, default=str, sort_keys=True))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('id', nargs='?', choices=JOBS)
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--fixture', type=Path)
    ap.add_argument('--now', help='ISO timestamp for a dry-run predicate fixture')
    ap.add_argument('--record-drift', action='store_true')
    args = ap.parse_args()
    if args.fixture and not args.dry_run:
        ap.error('--fixture requires --dry-run')
    if args.now and not args.dry_run:
        ap.error('--now requires --dry-run')
    if args.record_drift:
        from tools.routines.drift import check, render
        rows = check()
        if rows:
            Context('routine-drift', dry_run=args.dry_run).failure(render(rows))
        return int(bool(rows))
    if args.id is None:
        ap.error('routine id is required')
    fixture = json.loads(args.fixture.read_text()) if args.fixture else None
    now = datetime.fromisoformat(args.now) if args.now else None
    if now and now.tzinfo is None:
        ap.error('--now requires an explicit timezone offset')
    ctx = Context(args.id, dry_run=args.dry_run, fixture=fixture, now=now)
    def execute():
        if not args.dry_run and (not due(args.id, ctx.now, False) or ctx.completed()):
            return 0
        module = importlib.import_module('tools.routines.' + JOBS[args.id]['module'])
        return run_module(ctx, module)
    try:
        if args.dry_run:
            return execute()
        directory = ROOT / 'out/routines' / args.id
        directory.mkdir(parents=True, exist_ok=True)
        with (directory / 'run.lock').open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return 0
            ctx.journal = directory / (period(ctx.now) + '.json')
            ctx.state = json.loads(ctx.journal.read_text()) if ctx.journal.exists() else {}
            if ctx.state.get('started_at'):
                ctx.now = datetime.fromisoformat(ctx.state['started_at'])
            return execute()
    except (Exception, SystemExit) as exc:
        detail = redact_text(f'{type(exc).__name__}: {exc}', known_secrets=sensitive_env_values(os.environ))[:500]
        print(f'routine {args.id}: FAIL {detail}', file=sys.stderr)
        if not args.dry_run:
            ctx.state['failure'] = {'at': ctx.now.isoformat(), 'detail': detail}
            ctx.save()
            try:
                ctx.failure(detail)
            except (Exception, SystemExit):
                print('routine failure-loop write unavailable; failure journal retained', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
