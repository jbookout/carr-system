"""Subscription Claude jobs with local schedule windows and private receipts.

The desktop task prompt remains authoritative. This module owns execution,
timeouts and the local ledger, and never creates a desktop or Model Room session.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import fcntl
import itertools
import json
import os
from pathlib import Path
import plistlib
import queue
import re
import signal
import subprocess
import threading
import time
from zoneinfo import ZoneInfo

from lib.secret_redaction import redact_text, sensitive_env_values

UTC = timezone.utc
PREFIX = 'com.carr.headless.'
DEFAULT_TOOLS = 'Read,Glob,Grep,Bash(./run.sh *),Bash(./bin/*),Bash(./ops/*),WebFetch,WebSearch'
API_ENV = {'ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'ANTHROPIC_BASE_URL',
           'CLAUDE_CODE_USE_BEDROCK', 'CLAUDE_CODE_USE_VERTEX', 'CLAUDE_CODE_USE_FOUNDRY',
           'CLAUDE_CODE_OAUTH_TOKEN', 'CLAUDE_CODE_API_KEY_HELPER_TTL_MS'}
SECRET_LABEL = re.compile(r'(?i)(password|secret|api[_-]?key|token|authorization|credential|private[_-]?key)')


def log_text(text: str, secrets: list[str]) -> str:
    """Redact before writing, including short credential assignments."""
    text = redact_text(text, known_secrets=secrets)
    def clean_string(value):
        return re.sub(r'(?im)\b([\w-]*(?:password|secret|token|api[_-]?key)[\w-]*)\s*[:=]\s*[^\n]+',
                      r'\1=[REDACTED]', value)
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return clean_string(text)
    def clean(item):
        if isinstance(item, dict):
            return {key: '[REDACTED]' if SECRET_LABEL.search(key) else clean(val)
                    for key, val in item.items()}
        if isinstance(item, list):
            return [clean(val) for val in item]
        return clean_string(item) if isinstance(item, str) else item
    return json.dumps(clean(value)) + '\n'


def stamp(instant: datetime) -> str:
    return instant.astimezone(UTC).isoformat().replace('+00:00', 'Z')


def fields(expression: str) -> list[list[int]]:
    specs = expression.split()
    if len(specs) != 5:
        raise ValueError('cron must have five fields')
    expanded = []
    for spec, (low, high) in zip(specs, [(0, 59), (0, 23), (1, 31), (1, 12), (0, 7)]):
        values: set[int] = set()
        for part in spec.split(','):
            base, sep, step = part.partition('/')
            stride = int(step) if sep else 1
            if stride <= 0:
                raise ValueError('cron step must be positive')
            if base == '*':
                lo, hi = low, high
            elif '-' in base:
                lo, hi = map(int, base.split('-'))
            else:
                lo = hi = int(base)
            if not low <= lo <= hi <= high:
                raise ValueError('cron field out of range')
            values.update(range(lo, hi+1, stride))
        expanded.append(sorted(values))
    expanded[4] = sorted({v % 7 for v in expanded[4]})
    # Native cron uses OR when both day fields are restricted. The launchd
    # calendars below implement that union too, instead of turning it into AND.
    return expanded


def calendar_entries(expression: str) -> list[dict]:
    specs = expression.split()
    expanded = fields(expression)
    day_sets = [set(range(5))]
    if specs[2] != '*' and specs[4] != '*':
        day_sets = [set(range(4)), {0, 1, 3, 4}]
    rows = []
    names = ['Minute', 'Hour', 'Day', 'Month', 'Weekday']
    for day_set in day_sets:
        indices = [i for i in sorted(day_set) if specs[i] != '*']
        for values in itertools.product(*(expanded[i] for i in indices)):
            row = dict(zip((names[i] for i in indices), values))
            if row not in rows:
                rows.append(row)
    return rows


def _slots(expression: str, instant: datetime, zone: str, forward: bool):
    minute, hour, days, months, weekdays = fields(expression)
    specs = expression.split()
    tz = ZoneInfo(zone)
    local_date = instant.astimezone(tz).date()
    # A bounded calendar search covers leap-day schedules too.
    for offset in range(8*366):
        day = local_date + timedelta(days=offset if forward else -offset)
        dom = day.day in days
        dow = (day.weekday()+1) % 7 in weekdays
        day_ok = dom or dow if specs[2] != '*' and specs[4] != '*' else dom and dow
        if day.month not in months or not day_ok:
            continue
        slots = set()
        for h, m, fold in itertools.product(hour, minute, (0, 1)):
            local = datetime(day.year, day.month, day.day, h, m, tzinfo=tz, fold=fold)
            utc = local.astimezone(UTC)
            if utc.astimezone(tz).replace(fold=fold) == local:
                slots.add(utc)
        for slot in sorted(slots, reverse=not forward):
            if (forward and slot > instant) or (not forward and slot <= instant):
                yield slot


def schedule_window(expression: str, instant: datetime, zone: str) -> datetime:
    return next(_slots(expression, instant, zone, False))


def schedule_interval(expression: str, instant: datetime, zone: str) -> float:
    previous = schedule_window(expression, instant, zone)
    return (next(_slots(expression, previous, zone, True)) - previous).total_seconds()


def config(repo: Path) -> dict:
    return json.loads((repo / 'ops/headless-tasks/tasks.json').read_text())


def ledger_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text().splitlines():
        if line.strip():
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError('invalid ledger row')
            rows.append(row)
    return rows


def append_ledger(path: Path, row: dict) -> None:
    with path.open('a') as handle:
        handle.write(json.dumps(row, sort_keys=True) + '\n')
        handle.flush()
        os.fsync(handle.fileno())


def _terminate(child: subprocess.Popen) -> None:
    try:
        os.killpg(child.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    child.wait(timeout=5)


def _claude(prompt: Path, repo: Path, model: str, tools: str, timeout: float,
            log, env: dict, secrets: list[str]) -> tuple[int, str]:
    command = ['claude', '-p', '--model', model, '--permission-mode', 'dontAsk',
               '--permission-prompts', 'none', '--allowedTools', tools,
               '--no-session-persistence', '--output-format', 'stream-json', '--verbose']
    # Prevent API-billed credentials/provider overrides from taking precedence
    # over the existing subscription login. Never print the environment.
    child_env = {k: v for k, v in env.items() if k not in API_ENV and k != 'CLAUDECODE'}
    started = time.monotonic()
    for source in (Path.home()/'.claude/settings.json', repo/'.claude/settings.json',
                   repo/'.claude/settings.local.json'):
        if source.exists():
            setting = json.loads(source.read_text())
            if setting.get('apiKeyHelper') or set(setting.get('env', {})) & API_ENV:
                return 78, 'api_configuration_refused'
    try:
        auth = subprocess.run(['claude', 'auth', 'status', '--json'], stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                              cwd=repo, env=child_env, timeout=min(30, timeout))
        status = json.loads(auth.stdout)
    except subprocess.TimeoutExpired:
        return 124, 'timeout'
    except (ValueError, OSError):
        return 78, 'subscription_login_unavailable'
    if auth.returncode or status.get('loggedIn') is not True or status.get('authMethod') != 'claude.ai':
        return 78, 'subscription_login_required'
    # Auth readback may include account identifiers; none are written to logs.
    if time.monotonic()-started >= timeout:
        return 124, 'timeout'
    messages: queue.Queue[tuple[str, str | None]] = queue.Queue()
    def pump(stream, channel):
        try:
            for line in iter(stream.readline, ''):
                messages.put((channel, line))
        finally:
            stream.close()
            messages.put((channel, None))
    with prompt.open() as input_handle:
        child = subprocess.Popen(command, stdin=input_handle, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True, errors='replace',
                                 cwd=repo, env=child_env, start_new_session=True)
    threads = [threading.Thread(target=pump, args=(stream, name), daemon=True)
               for name, stream in [('stdout', child.stdout), ('stderr', child.stderr)]]
    for thread in threads:
        thread.start()
    done = 0
    result = None
    forced = None
    reason = ''
    try:
        while done < 2 or child.poll() is None:
            if time.monotonic()-started >= timeout:
                forced, reason = 124, 'timeout'
                break
            try:
                channel, line = messages.get(timeout=min(0.05, timeout))
            except queue.Empty:
                continue
            if line is None:
                done += 1
                continue
            log.write(log_text(line, secrets))
            log.flush()
            if channel != 'stdout':
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            if event.get('subtype') == 'permission_denied' or event.get('permission_denials'):
                forced, reason = 77, 'permission_denied'
                break
            if event.get('type') == 'result':
                result = event
        if forced is not None:
            return forced, reason
        rc = child.wait()
        if rc:
            return rc if rc > 0 else 128-rc, 'claude_nonzero'
        if not result or result.get('is_error') or result.get('subtype') != 'success':
            return 65, 'missing_or_failed_result'
        return 0, 'success'
    finally:
        # Kill surviving children even when the parent has already exited.
        _terminate(child)
        for thread in threads:
            thread.join(timeout=1)
        while not messages.empty():
            _, line = messages.get_nowait()
            if line is not None:
                log.write(log_text(line, secrets))


def record_failure(repo: Path, task_id: str, code: int, reason: str, log_path: Path) -> bool:
    successes = [r for r in ledger_rows(repo/'out/headless'/task_id/'ledger.jsonl')
                 if r.get('status') == 'success' and r.get('exit_code') == 0]
    episode = max((r['window'] for r in successes), default='initial')
    # The live add-loop taxonomy has no defect kind. Use its supported
    # open_loop kind, explicitly titled as a defect. Success starts a new
    # failure episode; failures within one episode deduplicate by task.
    args = {'idempotency_key': f'headless-defect:{task_id}:{episode}', 'kind': 'open_loop',
            'domain': 'system', 'owner': 'Claude',
            'title': f'Defect: headless task {task_id} failed',
            'body': f'Defect: headless task {task_id} failed. '
                    f'Inspect out/headless/{task_id}/ledger.jsonl and its latest failure log; '
                    f'repair the failure, run bin/headless-task {task_id}, '
                    'and verify a successful ledger row plus run.sh health.',
            'source_note': f'headless-task:{task_id}', 'marker': 'none',
            'blocker': 'capability',
            'blocker_detail': f'The unattended {task_id} executor failed; an attended session must inspect its local ledger and repair the named tool, runtime or login dependency.'}
    # A stable task key makes repeated failures one record-layer write. A
    # failed record write remains visible in the log and never becomes success.
    result = subprocess.run([str(repo/'run.sh'), 'call', 'add-loop', json.dumps(args)],
                            cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, timeout=30)
    if result.returncode:
        return False
    # Verify the record acknowledgement; zero wrapper exit alone is insufficient.
    try:
        return json.loads(result.stdout[result.stdout.index('{'):]).get('ok') is True
    except (ValueError, json.JSONDecodeError):
        return False


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('task_id')
    parser.add_argument('--repo', type=Path, default=Path.home()/'carr-system')
    parser.add_argument('--timeout-seconds', type=float)
    parser.add_argument('--model', default='sonnet')
    parser.add_argument('--allowed-tools', default=DEFAULT_TOOLS)
    args = parser.parse_args(argv)
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]*', args.task_id):
        parser.error('task id must contain lowercase letters, digits and hyphens')
    repo = args.repo.resolve()
    settings = config(repo)
    task = settings['tasks'][args.task_id]
    timeout = args.timeout_seconds if args.timeout_seconds is not None else task.get('timeout_seconds', 5400)
    if timeout <= 0 or timeout != timeout or timeout == float('inf'):
        parser.error('timeout must be finite and positive')
    os.umask(0o077)
    folder = repo/'out/headless'/args.task_id
    folder.mkdir(parents=True, exist_ok=True)
    start = datetime.now(UTC)
    window = stamp(schedule_window(task['cron'], start, settings['timezone']))
    log_path = folder/(start.strftime('%Y%m%dT%H%M%S.%fZ')+'.log')
    ledger = folder/'ledger.jsonl'
    with (folder/'run.lock').open('a') as lock, log_path.open('x') as log:
        row: dict[str, object] = {'start': stamp(start), 'end': None, 'exit_code': None,
               'log_path': str(log_path), 'window': window, 'status': 'running'}
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            row.update(end=stamp(datetime.now(UTC)), exit_code=0, status='skipped', reason='already_running')
            append_ledger(ledger, row)
            log.write('SKIP already_running\n')
            print(f'SKIP {args.task_id}: already_running')
            return 0
        code, reason = 70, 'runner_error'
        try:
            prior = ledger_rows(ledger)
            if any(r.get('window') == window and r.get('status') == 'success' and r.get('exit_code') == 0 for r in prior):
                row.update(end=stamp(datetime.now(UTC)), exit_code=0, status='skipped', reason='window_succeeded')
                append_ledger(ledger, row)
                log.write('SKIP window_succeeded\n')
                print(f'SKIP {args.task_id}: window_succeeded')
                return 0
            log.write(f'START {args.task_id} {stamp(start)} window={window}\n')
            log.flush()
            prompt = Path.home()/'.claude/scheduled-tasks'/args.task_id/'SKILL.md'
            code, reason = _claude(prompt, repo, args.model, args.allowed_tools, timeout,
                                   log, dict(os.environ), sensitive_env_values(os.environ))
        except Exception as exc:
            # The exception type is diagnostic; exception text can quote a
            # secret, prompt, configuration, or raw provider response.
            log.write(f'RUNNER_ERROR {type(exc).__name__}\n')
        row.update(end=stamp(datetime.now(UTC)), exit_code=code,
                   status='success' if code == 0 else 'failed', reason=reason)
        append_ledger(ledger, row)
        log.write(f'END exit={code} reason={reason}\n')
        if code:
            try:
                recorded = record_failure(repo, args.task_id, code, reason, log_path)
            except Exception:
                recorded = False
            notice = 'DEFECT_RECORDED' if recorded else 'ERROR defect_record_failed; retry record from ledger'
            log.write(notice + '\n')
            if not recorded:
                print(notice, flush=True)
        print(f'{"OK" if code == 0 else "FAIL"} {args.task_id}: {reason}; exit={code}; log={log_path}')
        return code


def health_rows(repo: Path, home: Path, now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(UTC)
    settings = config(repo)
    rows = []
    for installed in sorted((home/'Library/LaunchAgents').glob(PREFIX+'*.plist')):
        task_id = installed.name[len(PREFIX):-len('.plist')]
        response = (f'owner: Claude · on breach: inspect out/headless/{task_id}/ and launchd state; '
                    f'repair then run bin/headless-task {task_id} · verify: ./run.sh health and '
                    'successful ledger row · auto-clear: next success within schedule interval + grace')
        try:
            task = settings['tasks'][task_id]
            successes = [r for r in ledger_rows(repo/'out/headless'/task_id/'ledger.jsonl')
                         if r.get('status') == 'success' and r.get('exit_code') == 0]
            # Skips/failures never refresh a success timestamp. Installation
            # mtime is used only until the first success, so a new install has
            # one complete interval in which to prove its first run.
            last = max((datetime.fromisoformat(r['end'].replace('Z', '+00:00')) for r in successes),
                       default=datetime.fromtimestamp(installed.stat().st_mtime, UTC))
            bound = schedule_interval(task['cron'], last, settings['timezone']) + task.get('grace_seconds', 3600)
            age = (now-last).total_seconds()
            status = 'WARN' if age > bound or age < -60 else 'OK'
            detail = f'last success {stamp(last)}' if successes else 'awaiting first success (installation grace)'
        except Exception as exc:
            status, detail = 'WARN', f'unreadable task/ledger ({type(exc).__name__})'
        rows.append({'task_id': task_id, 'status': status,
                     'line': f'{status} headless/{task_id} — {detail} · {response}'})
    return rows


def install_main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Install reviewed headless launchd jobs; no desktop task changes.')
    parser.add_argument('action', choices=['install', 'uninstall'])
    parser.add_argument('task_ids', nargs='*')
    args = parser.parse_args(argv)
    home = Path.home()
    repo = home/'carr-system'
    settings = config(repo)
    selected = args.task_ids or sorted(settings['tasks'])
    if set(selected)-set(settings['tasks']):
        parser.error('unknown task id')
    from lib.claude_scheduler_native import system_timezone
    if args.action == 'install' and system_timezone() != settings['timezone']:
        parser.error('host timezone differs from reviewed desktop schedules')
    agents = home/'Library/LaunchAgents'
    agents.mkdir(parents=True, exist_ok=True)
    domain = f'gui/{os.getuid()}'
    for task_id in selected:
        label = PREFIX+task_id
        target = agents/(label+'.plist')
        if args.action == 'uninstall':
            if not target.exists():
                continue
            subprocess.run(['launchctl', 'bootout', domain+'/'+label], check=True)
            quarantine = repo/'out/headless/uninstalled'
            quarantine.mkdir(parents=True, exist_ok=True)
            target.replace(quarantine/(datetime.now(UTC).strftime('%Y%m%dT%H%M%S.%fZ')+'-'+target.name))
        else:
            if target.exists():
                parser.error(f'{label} already installed; uninstall it before replacing')
            prompt = home/'.claude/scheduled-tasks'/task_id/'SKILL.md'
            if not prompt.is_file():
                parser.error(f'{task_id} prompt is missing')
            (repo/'out/headless'/task_id).mkdir(parents=True, exist_ok=True)
            source = repo/'ops/headless-tasks'/(label+'.plist')
            content = source.read_text().replace('{{HOME}}', str(home)).replace('{{REPO}}', str(repo))
            plist = plistlib.loads(content.encode())
            if plist['StartCalendarInterval'] != calendar_entries(settings['tasks'][task_id]['cron']):
                parser.error('template schedule differs from reviewed manifest')
            target.write_bytes(plistlib.dumps(plist))
            subprocess.run(['launchctl', 'bootstrap', domain, str(target)], check=True)
        print(f'{args.action} {task_id}')
    return 0
