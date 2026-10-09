#!/usr/bin/env python3
"""Durable per-repository FIFO queues. Run, enqueue and inspect here."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import signal
import shlex
import shutil
import sqlite3
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'ops'))
sys.path.insert(0, str(ROOT))
from git_env import scrubbed_env
from lib.github_rate_limit import GitHubReadBudget, resource_for, split_response
REPOS = ('jbookout/carr-system', 'jbookout/doctorcre-app', 'jbookout/software-factory')
HOLD = 'do_not_merge'
SHA = re.compile(r'^[0-9a-f]{40}$')
REVIEW = runpy.run_path(str(ROOT / 'ops/release-pipeline.py'))
REVIEW_CONFIG = json.loads((ROOT / 'ops/config/release-pipeline.v1.json').read_text())
MAX_ATTEMPTS = 4
AUTO_ENQUEUE_CAP = 3
CI_WAIT_SECONDS = 75 * 60


class MergeRejected(RuntimeError):
    pass


class ActionRejected(RuntimeError):
    pass


class ReadRejected(RuntimeError):
    pass


class ActionExhausted(RuntimeError):
    pass


class Cancelled(Exception):
    pass


def gh_api_read(argv):
    method, fields, body = None, [], False
    for index, arg in enumerate(argv[3:], 3):
        if arg in ('-X', '--method'):
            method = argv[index + 1]
        elif arg.startswith('--method='):
            method = arg.split('=', 1)[1]
        elif arg.startswith('-X'):
            method = arg[2:]
        elif arg in ('-f', '-F', '--field', '--raw-field'):
            fields.append(argv[index + 1])
        elif arg.startswith(('--field=', '--raw-field=')):
            fields.append(arg.split('=', 1)[1])
        elif arg.startswith(('-f', '-F')):
            fields.append(arg[2:])
        elif arg == '--input' or arg.startswith('--input='):
            body = True
    if argv[2] == 'graphql':
        return any(re.match(r'query=\s*(?:query\b|\{)', field) for field in fields)
    return (method or ('POST' if fields or body else 'GET')).upper() in ('GET', 'HEAD')


def command(argv, *, cwd=None, data=None, timeout=120, observe=None):
    p = subprocess.run(argv, cwd=cwd, input=data, text=True, capture_output=True,
                       env=scrubbed_env() if argv[0] == 'git' else None,
                       stdin=None if data is not None else subprocess.DEVNULL, timeout=timeout)
    if observe is not None:
        observe(p)
    if p.returncode:
        if argv[:3] == ['gh', 'pr', 'merge'] and 'GraphQL:' in p.stderr and any(
                phrase in p.stderr.lower() for phrase in ('pull request is not mergeable',
                                                         'required status check',
                                                         'base branch policy prohibits')):
            raise MergeRejected('GitHub rejected the merge; verify provider state before retry')
        status = re.search(r'HTTP (4[0-9]{2})', p.stderr)
        if argv[:2] == ['gh', 'api'] and status and gh_api_read(argv):
            raise ReadRejected(f'GitHub read rejected (HTTP {status[1]}); reread before retry')
        if argv[0] == 'gh' and (argv[1] == 'api' or argv[1:3] == ['pr', 'edit']) and status:
            raise ActionRejected('GitHub rejected the action; reread before retry')
        # Child stderr can contain authenticated URLs. Keep it in the child's domain.
        raise RuntimeError(f'{Path(argv[0]).name} {argv[1]} failed (exit {p.returncode})')
    return p.stdout


class Queue:
    def __init__(self, state: Path, root: Path = ROOT, gap: float = 2.0):
        self.state, self.root, self.gap = state, root, gap
        state.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(state / 'queue.sqlite3', timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS entries (
                id INTEGER PRIMARY KEY, repo TEXT NOT NULL, pr INTEGER NOT NULL,
                approved TEXT NOT NULL, note TEXT NOT NULL, phase TEXT NOT NULL DEFAULT 'pending',
                tested TEXT, outcome TEXT, UNIQUE(repo,pr,approved));
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY, entry_id INTEGER, repo TEXT, pr INTEGER,
                outcome TEXT NOT NULL, detail TEXT NOT NULL, logged INTEGER DEFAULT 0,
                published INTEGER DEFAULT 0, created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS actions (
                key TEXT PRIMARY KEY, kind TEXT NOT NULL, repo TEXT NOT NULL, pr INTEGER NOT NULL,
                head TEXT NOT NULL, payload TEXT NOT NULL, phase TEXT NOT NULL DEFAULT 'planned', desk TEXT);
        ''')
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            additions = {
                'entries': {'merge_attempts': 'INTEGER NOT NULL DEFAULT 0',
                            'transient_attempts': 'INTEGER NOT NULL DEFAULT 0',
                            'read_attempts': 'INTEGER NOT NULL DEFAULT 0',
                            'auto_attempts': 'INTEGER NOT NULL DEFAULT 0',
                            'ci_since': 'REAL', 'ci_head': 'TEXT'},
                'actions': {'attempts': 'INTEGER NOT NULL DEFAULT 0', 'expected_base': 'TEXT',
                            'dispatcher_pid': 'INTEGER', 'dispatcher_identity': 'TEXT', 'receipt': 'TEXT'}}
            for table, columns in additions.items():
                existing = {r['name'] for r in self.db.execute(f'PRAGMA table_info({table})')}
                for name, definition in columns.items():
                    if name not in existing:
                        self.db.execute(f'ALTER TABLE {table} ADD COLUMN {name} {definition}')
        self.last_gh = 0.0
        self.budget = GitHubReadBudget(spacing=gap)
        self.stopped = False
        self._lock_depth = 0
        self._next_page = None
        self.children: dict[str, subprocess.Popen] = {}

    @contextmanager
    def runner_lock(self):
        if self._lock_depth:
            yield
            return
        with (self.state / 'agent.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._lock_depth = 1
            try:
                yield
            finally:
                self._lock_depth = 0

    def check_cancelled(self):
        if self.stopped:
            raise Cancelled()

    def gh(self, *args):
        resource = resource_for(list(args))
        delay = max(self.budget.reserve(resource), self.gap - (time.monotonic() - self.last_gh))
        end = time.monotonic() + max(0, delay)
        while time.monotonic() < end:
            self.check_cancelled()
            time.sleep(min(.2, max(0, end - time.monotonic())))
        self.budget.check(resource)
        self.check_cancelled()
        include = args[0] == 'api' and '--include' not in args
        observed_at = time.time()
        def observe(p):
            headers, _ = split_response(p.stdout)
            self.budget.observe(resource, headers, p.stderr, observed_at)
        try:
            out = command(['gh', *args, *(['--include'] if include else [])], cwd=self.root, observe=observe)
            headers, body = split_response(out)
            self._next_page = bool(re.search(r';\s*rel="next"', headers.get('link', ''))) if out.startswith('HTTP/') else None
            return body if include else out
        finally:
            self.last_gh = time.monotonic()

    def api(self, path, *args):
        return json.loads(self.gh('api', path, *args))

    def api_pages(self, path, collection=None):
        pages = []
        for page in range(1, 161):
            separator = '&' if '?' in path else '?'
            data = self.api(f'{path}{separator}page={page}')
            rows = data[collection] if collection else data
            if not isinstance(rows, list):
                raise ValueError('GitHub did not return a page list')
            pages.extend(rows)
            if self._next_page is False or (self._next_page is None and len(rows) < 100):
                return pages
        raise RuntimeError('GitHub pagination limit reached; refusing partial read')

    def pages(self, path):
        return self.api_pages(path)

    def pr(self, repo, n):
        return self.api(f'repos/{repo}/pulls/{n}')

    def held(self, p):
        return HOLD in {x['name'] for x in p.get('labels', [])}

    def enqueue(self, repo, n, approved, note=''):
        if repo not in REPOS or n <= 0 or not SHA.fullmatch(approved):
            raise ValueError('expected authorized repo, positive PR and full approved SHA')
        with self.db:
            cur = self.db.execute('INSERT OR IGNORE INTO entries(repo,pr,approved,note) VALUES(?,?,?,?)',
                                  (repo, n, approved, note))
            if cur.rowcount:
                self.event(repo, n, 'queued', f'Approved head {approved}', cur.lastrowid)
        return self.db.execute('SELECT id FROM entries WHERE repo=? AND pr=? AND approved=?',
                               (repo, n, approved)).fetchone()[0]

    def event(self, repo, n, outcome, detail, entry_id=None):
        prev = self.db.execute('SELECT outcome,detail FROM events WHERE repo=? AND pr=? ORDER BY id DESC LIMIT 1', (repo, n)).fetchone()
        if prev and tuple(prev) == (outcome, detail):
            return
        self.db.execute('INSERT INTO events(entry_id,repo,pr,outcome,detail,created) VALUES(?,?,?,?,?,?)',
                        (entry_id, repo, n, outcome, detail, time.time()))

    def report(self, e, outcome, detail, phase=None):
        with self.db:
            self.db.execute('UPDATE entries SET outcome=? WHERE id=?', (outcome, e['id']))
            if phase is not None:
                self.db.execute('UPDATE entries SET phase=?,outcome=? WHERE id=?', (phase, outcome, e['id']))
            prev = self.db.execute('SELECT outcome,detail FROM events WHERE entry_id=? ORDER BY id DESC LIMIT 1',
                                   (e['id'],)).fetchone()
            if not prev or tuple(prev) != (outcome, detail):
                self.event(e['repo'], e['pr'], outcome, detail, e['id'])

    def flush_events(self):
        for ev in self.db.execute('SELECT * FROM events WHERE logged=0 ORDER BY id').fetchall():
            with (self.state / 'queue.log').open('a') as f:
                f.write(json.dumps(dict(ev), sort_keys=True) + '\n')
                f.flush()
                os.fsync(f.fileno())
            with self.db:
                self.db.execute('UPDATE events SET logged=1 WHERE id=?', (ev['id'],))
        for ev in self.db.execute('SELECT * FROM events WHERE published=0 ORDER BY id').fetchall():
            if not ev['published']:
                stage = 'merged' if ev['outcome'] in ('merged', 'already_merged') else ('ci' if ev['outcome'] == 'waiting_ci' else 'review')
                health = 'healthy' if ev['outcome'] in ('queued', 'merged', 'updated', 'retargeted') else 'question'
                card = ('app-pr-' if ev['repo'] == REPOS[1] else
                        'factory-pr-' if ev['repo'] == REPOS[2] else 'pr-') + str(ev['pr'])
                try:
                    command([sys.executable, str(self.root / 'tools/progress_board.py'), 'task', 'carr-v5', card,
                             '--repo', ev['repo'], '--pr', str(ev['pr']), '--status', 'review', '--stage', stage,
                             '--health', health, '--note', f"Merge queue: {ev['outcome']}. {ev['detail']}"],
                            cwd=self.root, timeout=60)
                except (RuntimeError, subprocess.TimeoutExpired, OSError):
                    with (self.state / 'queue.log').open('a') as f:
                        f.write(json.dumps({'event_id': ev['id'], 'outcome': 'progress_board_write_failed',
                                            'retry': 'next poll; queue continues'}) + '\n')
                    break
                with self.db:
                    self.db.execute('UPDATE events SET published=1 WHERE id=?', (ev['id'],))

    def git(self, repo, *args, data=None):
        d = self.state / 'repos' / (repo.split('/')[-1] + '.git')
        if not d.exists():
            d.parent.mkdir(parents=True, exist_ok=True)
            command(['git', 'init', '--bare', str(d)])
            command(['git', '-C', str(d), 'remote', 'add', 'origin', f'https://github.com/{repo}.git'])
        return command(['git', '-C', str(d), *args], data=data)

    def fetch(self, repo, *heads):
        self.git(repo, 'fetch', '--quiet', 'origin', '+refs/heads/main:refs/remotes/origin/main',
                 *dict.fromkeys(heads))

    def patch(self, repo, head):
        base = self.git(repo, 'merge-base', 'origin/main', head).strip()
        diff = self.git(repo, 'diff', '--binary', base, head)
        ids = command(['git', 'patch-id', '--stable'], data=diff).split()
        return ids[0] if ids else None

    def covered(self, repo, approved, head):
        if approved == head:
            return True
        self.fetch(repo, approved, head)
        old = self.patch(repo, approved)
        return bool(old and old == self.patch(repo, head))

    def approval(self, repo, n):
        comments = self.pages(f'repos/{repo}/issues/{n}/comments?per_page=100')
        cfg = REVIEW_CONFIG['app' if repo == REPOS[1] else 'worker']
        stamp = re.compile(r'APPROVE\r?\nReviewed-SHA: [0-9a-f]{40}\r?\n(?:\r?\n)?'
                           r'(?:Orchestrator merge queue:|Orchestrator: verified exact head)[^\r\n]*\r?\n?')
        independent = [c for c in comments if not stamp.fullmatch(c.get('body', ''))]
        last = REVIEW['deciding_verdict'](independent, cfg)
        if last and REVIEW['verdict'](last.get('body', ''), cfg) == 'approve':
            return REVIEW['reviewed_header_sha'](last.get('body', ''))
        return None

    def green(self, repo, n, head):
        path = f'repos/{repo}/commits/{head}'
        runs = self.api_pages(path + '/check-runs?per_page=100', 'check_runs')
        statuses = self.pages(path + '/statuses?per_page=100')
        latest = {}
        for run in runs:
            key = (run.get('app', {}).get('id'), run['name'])
            if key not in latest or run['id'] > latest[key]['id']:
                latest[key] = run
        contexts = {}
        for s in statuses:
            if s['context'] not in contexts:
                contexts[s['context']] = s
        if not latest and not contexts:
            return False
        if any(r['status'] != 'completed' or r['conclusion'] not in ('success', 'skipped', 'neutral')
               for r in latest.values()) or any(s['state'] != 'success' for s in contexts.values()):
            return False
        checks = json.loads(self.gh('pr', 'checks', str(n), '-R', repo, '--required', '--json', 'bucket'))
        return all(c['bucket'] in ('pass', 'skipping') for c in checks)

    def action(self, kind, repo, n, head, payload, expected_base=None):
        key = f'{kind}:{repo}:{n}:{head}:{payload}'
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO actions(key,kind,repo,pr,head,payload,expected_base) VALUES(?,?,?,?,?,?,?)',
                            (key, kind, repo, n, head, payload, expected_base))
        a = self.db.execute('SELECT * FROM actions WHERE key=?', (key,)).fetchone()
        if a['phase'] == 'done':
            return
        if a['phase'] == 'exhausted':
            raise ActionExhausted(f'{kind} exhausted {a["attempts"]} attempts at {head}')
        p = self.pr(repo, n)
        if self.stopped:
            return
        if self.held(p):
            with self.db:
                self.event(repo, n, 'held', f'Skipped {kind}; {HOLD} label present')
            return
        if (kind == 'update' and p['head']['sha'] != head) or (kind == 'retarget' and p['base']['ref'] == payload):
            with self.db:
                self.db.execute("UPDATE actions SET phase='done' WHERE key=?", (key,))
            return
        if a['phase'] == 'issued':
            # A crashed or timed-out mutation may still be processing server-side.
            with self.db:
                self.event(repo, n, 'action_uncertain', f'{kind} at {head}; inspect GitHub before retry')
            return
        if p['head']['sha'] != head or p['state'] != 'open':
            return
        if kind == 'retarget' and (not a['expected_base'] or p['base']['ref'] != a['expected_base']):
            with self.db:
                self.db.execute("UPDATE actions SET phase='done' WHERE key=?", (key,))
                self.event(repo, n, 'base_changed', f'Retarget skipped; expected {a["expected_base"]}, observed {p["base"]["ref"]}')
            return
        if kind == 'update' and (not self.behind(repo, p) or p.get('mergeable') is not True):
            return
        with self.db:
            self.db.execute("UPDATE actions SET phase='issued',attempts=attempts+1 WHERE key=?", (key,))
        try:
            if kind == 'update':
                self.api(f'repos/{repo}/pulls/{n}/update-branch', '-X', 'PUT', '-f', f'expected_head_sha={head}')
            else:
                self.gh('pr', 'edit', str(n), '-R', repo, '--base', payload)
        except Cancelled:
            with self.db:
                self.db.execute("UPDATE actions SET phase='planned',attempts=attempts-1 WHERE key=?", (key,))
            raise
        except (ActionRejected, OSError):
            with self.db:
                exhausted = a['attempts'] + 1 >= MAX_ATTEMPTS
                self.db.execute('UPDATE actions SET phase=? WHERE key=?', ('exhausted' if exhausted else 'planned', key))
                self.event(repo, n, 'action_exhausted' if exhausted else 'action_rejected', f'{kind} rejected on attempt {a["attempts"] + 1}')
            if exhausted:
                raise ActionExhausted(f'{kind} exhausted {MAX_ATTEMPTS} attempts at {head}')
            raise
        with self.db:
            self.event(repo, n, 'updated' if kind == 'update' else 'retargeted', f'{kind} requested at {head}')
            # update-branch is asynchronous. Reconcile on the next read, never resend it blindly.
            if kind == 'retarget':
                self.db.execute("UPDATE actions SET phase='done' WHERE key=?", (key,))

    def conflict(self, repo, p):
        n, head = p['number'], p['head']['sha']
        self.fetch(repo, head)
        base = p['base']['sha']
        self.fetch(repo, base)
        d = self.state / 'repos' / (repo.split('/')[-1] + '.git')
        merge = subprocess.run(['git', '-C', str(d), 'merge-tree', '--write-tree', '--name-only',
                                '-z', base, head], text=True, capture_output=True, timeout=120, env=scrubbed_env())
        if merge.returncode != 1:
            raise RuntimeError('conflicting-files readback not ready')
        files = merge.stdout.split('\0')[1:]
        files = files[:files.index('')] if '' in files else files
        if not files:
            raise RuntimeError('conflicting-files readback empty')
        brief_dir = self.root / 'out/orch/queue/codex'
        brief_dir.mkdir(parents=True, exist_ok=True)
        brief = brief_dir / f"merge-conflict-{repo.split('/')[-1]}-{n}-{head}.txt"
        text = (f'Conflict repair for https://github.com/{repo}/pull/{n}\nHead: {head}\n'
                f'Base: {p["base"]["ref"]} {base}\nConflicting files:\n' + '\n'.join(files) +
                '\nUse an isolated worktree. Inspect and resolve by a reviewed source change; never force-push. '
                'Run relevant tests and request fresh review. Do not merge. '
                'Do not read credentials, encrypted stores or production dumps. '
                'Executor: latest GPT Sol, high effort.\n')
        if not brief.exists():
            brief.write_text(text)
        key = f'conflict:{repo}:{n}:{head}'
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO actions(key,kind,repo,pr,head,payload) VALUES(?,?,?,?,?,?)',
                            (key, 'dispatch', repo, n, head, str(brief)))
            self.event(repo, n, 'conflict', f'Fix brief {brief}; files: {", ".join(files)}')

    def dispatch_conflicts(self):
        with self.runner_lock():
            self._dispatch_conflicts()

    def dispatcher_identity(self, pid, receipt):
        if not pid:
            return None
        rows = command(['ps', '-axo', 'pid=,lstart=,command=']).splitlines()
        text = next((line.strip() for line in rows if line.split() and line.split()[0] == str(pid)), '')
        if str(self.root / 'tools/room-bridge/dispatch.py') not in text or str(receipt) not in text:
            return None
        return hashlib.sha256(text.encode()).hexdigest()

    def _dispatch_conflicts(self):
        if self.stopped:
            return
        exited = set()
        for key, child in list(self.children.items()):
            if child.poll() is not None:
                child.wait()
                self.children.pop(key)
                exited.add(key)
        if not self.db.execute("SELECT 1 FROM actions WHERE kind='dispatch' AND phase IN ('planned','issued')").fetchone():
            return
        registry_path = Path(os.environ.get('CARR_HERMES_DESKS', Path.home() / '.config/carr/hermes-desks.json'))
        registry = json.loads(registry_path.read_text())['desks']
        ps = [line.split() for line in command(['ps', '-axo', 'command=']).splitlines()]
        for a in self.db.execute("SELECT * FROM actions WHERE kind='dispatch' AND phase='issued'").fetchall():
            results = Path(a['receipt']) if a['receipt'] else Path(a['payload']).with_suffix('.dispatch.jsonl')
            row = None
            if results.exists():
                try:
                    rows = [json.loads(line) for line in results.read_text().splitlines(keepends=True)
                            if line.strip() and line.endswith('\n')]
                except (ValueError, OSError):
                    with self.db:
                        self.event(a['repo'], a['pr'], 'dispatch_receipt_invalid', f'Readback failed: {results}; other briefs continue')
                    rows = []
                row = rows[-1] if rows else None
                if row and row.get('desk') == a['desk']:
                    outcome = 'dispatch_finished' if row.get('status') == 'completed' and row.get('thread_id') else 'dispatch_failed'
                    with self.db:
                        self.db.execute("UPDATE actions SET phase='done',desk=NULL WHERE key=?", (a['key'],))
                        self.event(a['repo'], a['pr'], outcome, f'{a["desk"]}: {row.get("status")}; receipt {results}')
                    continue
            child = self.children.get(a['key'])
            try:
                identity = self.dispatcher_identity(a['dispatcher_pid'], results) if child is None else None
            except (RuntimeError, OSError, subprocess.TimeoutExpired):
                # An unreadable process inventory is not evidence of an orphan.
                continue
            legacy_live = not a['dispatcher_pid'] and any(
                str(self.root / 'tools/room-bridge/dispatch.py') in args and str(results) in args
                and a['desk'] in args for args in ps)
            live = child is not None or legacy_live or (a['dispatcher_identity'] is not None and identity == a['dispatcher_identity'])
            if a['key'] in exited or not live:
                with self.db:
                    self.db.execute("UPDATE actions SET phase='uncertain',desk=NULL WHERE key=?", (a['key'],))
                    self.event(a['repo'], a['pr'], 'dispatch_uncertain', f'Dispatcher absent without a valid receipt {results}; desk {a["desk"]} released; inspect before retry')
        for a in self.db.execute("SELECT * FROM actions WHERE kind='dispatch' AND phase='planned' ORDER BY rowid").fetchall():
            if self.stopped:
                return
            p = self.pr(a['repo'], a['pr'])
            if self.held(p):
                continue
            if p['head']['sha'] != a['head'] or p['state'] != 'open':
                with self.db:
                    self.db.execute("UPDATE actions SET phase='done' WHERE key=?", (a['key'],))
                continue
            bridge = self.root / 'tools/room-bridge/dispatch.py'
            reserved = {r['desk'] for r in self.db.execute("SELECT desk FROM actions WHERE phase='issued' AND desk IS NOT NULL")}
            free = next((name for name, e in sorted(registry.items())
                         if e.get('kind') == 'codex-session' and re.fullmatch(r'gpt-[0-9.]+-sol', e.get('model', ''))
                         and e.get('effort') == 'high' and e.get('sandbox') == 'workspace-write'
                         and e.get('last_auth') is True and e.get('thread_id') is None
                         and not e.get('busy', False) and name not in reserved
                         and not any(name in args for args in ps)), None)
            if free is None:
                with self.db:
                    if not self.db.execute("SELECT 1 FROM events WHERE repo=? AND pr=? AND outcome='dispatch_waiting'", (a['repo'], a['pr'])).fetchone():
                        self.event(a['repo'], a['pr'], 'dispatch_waiting', 'No free authenticated Codex Sol high desk; brief remains queued')
                continue
            results = Path(a['payload']).with_suffix('.dispatch.jsonl')
            argv = [sys.executable, str(bridge), '--registry', str(registry_path), '--results', str(results), 'send', free,
                    Path(a['payload']).read_text(), '--fresh']
            if '--family' in command([sys.executable, str(bridge), 'send', '--help']):
                argv += ['--family', 'sol']
            if self.stopped:
                return
            # Persist the intent before spawning. Uncertain dispatches are not automatically repeated.
            with self.db:
                self.db.execute("UPDATE actions SET phase='issued',desk=?,receipt=?,attempts=attempts+1 WHERE key=?", (free, str(results), a['key']))
                self.event(a['repo'], a['pr'], 'dispatch_started', f'{free}, {registry[free]["model"]}, {registry[free]["effort"]}; {results}')
            with Path(a['payload']).with_suffix('.dispatch.log').open('a') as log:
                try:
                    self.check_cancelled()
                    self.children[a['key']] = subprocess.Popen(argv, cwd=self.root, stdin=subprocess.DEVNULL, stdout=log, stderr=log)
                    child = self.children[a['key']]
                    with self.db:
                        self.db.execute('UPDATE actions SET dispatcher_pid=? WHERE key=?', (child.pid, a['key']))
                    try:
                        identity = self.dispatcher_identity(child.pid, results)
                    except (RuntimeError, OSError, subprocess.TimeoutExpired):
                        identity = None
                    with self.db:
                        self.db.execute('UPDATE actions SET dispatcher_identity=? WHERE key=?', (identity, a['key']))
                except Cancelled:
                    with self.db:
                        self.db.execute("UPDATE actions SET phase='planned',desk=NULL,attempts=attempts-1 WHERE key=?", (a['key'],))
                    return
                except OSError:
                    with self.db:
                        phase = 'exhausted' if a['attempts'] + 1 >= MAX_ATTEMPTS else 'planned'
                        self.db.execute('UPDATE actions SET phase=?,desk=NULL WHERE key=?', (phase, a['key']))
                        self.event(a['repo'], a['pr'], 'dispatch_not_started', 'Process did not start; brief remains queued')

    def behind(self, repo, p):
        if p['mergeable_state'] == 'behind':
            return True
        # Drafts mask BEHIND with BLOCKED/DRAFT. Check main ancestry only after clean mergeability is proven.
        if p.get('draft') and p.get('mergeable') is True and p['mergeable_state'] in ('blocked', 'draft'):
            self.fetch(repo, p['head']['sha'])
            d = self.state / 'repos' / (repo.split('/')[-1] + '.git')
            result = subprocess.run(['git', '-C', str(d), 'merge-base', '--is-ancestor',
                                     'origin/main', p['head']['sha']], capture_output=True, env=scrubbed_env())
            if result.returncode not in (0, 1):
                raise RuntimeError('draft ancestry readback failed')
            return result.returncode == 1
        return False

    def refresh(self, repo, merged_branch):
        complete = True
        for summary in self.pages(f'repos/{repo}/pulls?state=open&per_page=100'):
            if self.stopped:
                return False
            n = summary['number']
            try:
                p = self.pr(repo, n)
                if self.held(p):
                    with self.db:
                        self.event(repo, n, 'held', 'Skipped post-merge refresh; do_not_merge label present')
                    continue
                if p['base']['ref'] == merged_branch:
                    self.action('retarget', repo, n, p['head']['sha'], 'main', expected_base=p['base']['ref'])
                    p = self.pr(repo, n)
                if p['base']['ref'] != 'main':
                    with self.db:
                        self.event(repo, n, 'stacked', f'Waiting for base {p["base"]["ref"]}')
                    continue
                if p.get('mergeable') is False or p['mergeable_state'] == 'dirty':
                    self.conflict(repo, p)
                elif self.behind(repo, p) and p.get('mergeable') is True:
                    self.action('update', repo, n, p['head']['sha'], 'main')
                elif p['mergeable_state'] == 'unknown' or p.get('mergeable') is None:
                    raise RuntimeError('GitHub is still computing mergeability')
                else:
                    with self.db:
                        self.event(repo, n, 'unchanged', f'Post-merge refresh: {p["mergeable_state"]} at {p["head"]["sha"]}')
            except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
                complete = False
                with self.db:
                    self.event(repo, n, 'refresh_waiting', str(exc))
        return complete

    def discover(self):
        for repo in REPOS:
            if self.stopped:
                return
            try:
                prs = self.pages(f'repos/{repo}/pulls?state=open&per_page=100')
            except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
                with self.db:
                    self.event(repo, 0, 'discovery_unreadable', str(exc))
                continue
            for p in prs:
                if self.held(p):
                    continue
                if self.db.execute('SELECT 1 FROM entries WHERE repo=? AND pr=? AND phase IN (\'pending\',\'merging\',\'refreshing\')',
                                   (repo, p['number'])).fetchone():
                    continue
                try:
                    approved = self.approval(repo, p['number'])
                except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired):
                    continue
                if approved:
                    entry = self.enqueue(repo, p['number'], approved, 'auto-enqueued from independent review')
                    e = self.db.execute('SELECT * FROM entries WHERE id=?', (entry,)).fetchone()
                    if e['auto_attempts'] >= AUTO_ENQUEUE_CAP:
                        with self.db:
                            self.event(repo, p['number'], 'auto_enqueue_exhausted', f'Automatic enqueue cap reached for {approved}', entry)
                        continue
                    if e['phase'] in ('review', 'conflict') and self.covered(repo, approved, p['head']['sha']):
                        with self.db:
                            self.db.execute('UPDATE entries SET auto_attempts=auto_attempts+1 WHERE id=?', (entry,))
                        self.report(e, 'fresh_review_accepted', f'Independent approval covers {p["head"]["sha"]}', 'pending')
                    elif e['phase'] == 'pending' and e['auto_attempts'] == 0:
                        with self.db:
                            self.db.execute('UPDATE entries SET auto_attempts=1 WHERE id=?', (entry,))

    def tick(self):
        with self.runner_lock():
            repos = [r[0] for r in self.db.execute("SELECT repo FROM entries WHERE phase IN ('pending','merging','merge_rejected','refreshing') GROUP BY repo ORDER BY MIN(id)")]
            for repo in repos:
                if self.stopped:
                    return
                self._tick_repo(repo)

    def _tick_repo(self, queued_repo):
        for e in self.db.execute("SELECT * FROM entries WHERE repo=? AND phase IN ('pending','merging','merge_rejected','refreshing') ORDER BY id", (queued_repo,)).fetchall():
            repo, n, approved = e['repo'], e['pr'], e['approved']
            try:
                p = self.pr(repo, n)
                if p.get('merged'):
                    mc = p.get('merge_commit_sha')
                    if not mc or not SHA.fullmatch(mc):
                        raise RuntimeError('merge commit not available yet')
                    self.fetch(repo, mc)
                    self.git(repo, 'merge-base', '--is-ancestor', mc, 'origin/main')
                    if e['tested'] is None:
                        self.report(e, 'already_merged', f'Imported intent reconciled: {mc} on origin/main', 'done')
                        continue
                    self.report(e, 'merged', f'Merge commit {mc} confirmed on origin/main', 'refreshing')
                    if not self.refresh(repo, p['head']['ref']):
                        self.retry(e, 'Post-merge refresh remains incomplete')
                        return
                    self.report(e, 'merged', f'Merge commit {mc} confirmed; post-merge refresh complete', 'done')
                    return
                if p['state'] != 'open':
                    self.report(e, 'closed', 'PR closed without merge', 'done')
                    continue
                if self.held(p):
                    self.report(e, 'held', 'do_not_merge label present')
                    continue
                if e['phase'] == 'merge_rejected':
                    if self.merge_pending(repo, n, e['tested']):
                        self.report(e, 'merge_pending', 'GitHub owns an automatic or queued merge; waiting for confirmation')
                        return
                    if e['merge_attempts'] >= MAX_ATTEMPTS:
                        self.report(e, 'merge_exhausted', f'GitHub rejected {MAX_ATTEMPTS} merge attempts', 'exhausted')
                    else:
                        self.report(e, 'reconciled', 'GitHub rejected the request and now proves no pending merge', 'pending')
                    return
                if p['base']['ref'] != 'main':
                    self.report(e, 'stacked', f'Waiting for base {p["base"]["ref"]} to merge')
                    continue
                if e['phase'] == 'merging':
                    self.report(e, 'merge_uncertain', 'Merge was issued; reconcile GitHub state before any retry')
                    return
                head = p['head']['sha']
                if self.approval(repo, n) != approved:
                    self.report(e, 'fresh_review', 'Latest independent verdict does not approve queued head', 'review')
                    continue
                if not self.covered(repo, approved, head):
                    self.report(e, 'fresh_review', f'Patch differs from approved head; review {head}', 'review')
                    continue
                if p.get('mergeable') is False or p['mergeable_state'] == 'dirty':
                    self.conflict(repo, p)
                    self.report(e, 'conflict', f'Conflict brief filed for {head}', 'conflict')
                    continue
                if self.behind(repo, p) and p.get('mergeable') is True:
                    self.action('update', repo, n, head, 'main')
                    self.report(e, 'updating', f'Waiting for clean update of {head}')
                    return
                if p['mergeable_state'] == 'unknown' or p.get('mergeable') is None:
                    self.report(e, 'waiting_mergeability', 'GitHub mergeability is unknown')
                    return
                if not self.green(repo, n, head):
                    since = e['ci_since'] if e['ci_head'] == head and e['ci_since'] is not None else time.time()
                    with self.db:
                        self.db.execute('UPDATE entries SET ci_since=?,ci_head=? WHERE id=?', (since, head, e['id']))
                    if time.time() - since >= CI_WAIT_SECONDS:
                        self.report(e, 'ci_timeout', f'Hosted checks did not turn green within 75 minutes at {head}', 'exhausted')
                    else:
                        self.report(e, 'waiting_ci', f'Waiting for green hosted checks on {head}')
                    return
                current = self.pr(repo, n)
                if self.held(current) or current['head']['sha'] != head or current['base']['ref'] != 'main':
                    self.report(e, 'head_moved', 'Head, base or hold changed during checks')
                    return
                if self.approval(repo, n) != approved:
                    self.report(e, 'fresh_review', 'Independent verdict changed during checks', 'review')
                    return
                if self.stopped:
                    return
                marker = f'APPROVE\nReviewed-SHA: {head}\n\nOrchestrator merge queue: independent approval of {approved}; patch unchanged; hosted checks green.'
                self.api(f'repos/{repo}/issues/{n}/comments', '-f', f'body={marker}')
                if current.get('draft'):
                    self.gh('pr', 'ready', str(n), '-R', repo)
                current = self.pr(repo, n)
                if self.held(current) or current['head']['sha'] != head or current['base']['ref'] != 'main':
                    return
                if self.stopped:
                    return
                if e['merge_attempts'] >= MAX_ATTEMPTS:
                    self.report(e, 'merge_exhausted', f'Merge attempt cap reached at {head}', 'exhausted')
                    return
                with self.db:
                    self.db.execute("UPDATE entries SET phase='merging',tested=?,merge_attempts=merge_attempts+1 WHERE id=?", (head, e['id']))
                try:
                    self.gh('pr', 'merge', str(n), '-R', repo, '--squash', '--match-head-commit', head)
                except Cancelled:
                    with self.db:
                        self.db.execute("UPDATE entries SET phase='pending',merge_attempts=merge_attempts-1 WHERE id=?", (e['id'],))
                    return
                self.report(e, 'merge_requested', f'Guarded squash merge requested at {head}')
                return
            except MergeRejected as exc:
                self.report(e, 'merge_rejected', str(exc), 'merge_rejected')
                return
            except ActionExhausted as exc:
                self.report(e, 'action_exhausted', str(exc), 'exhausted')
                return
            except ReadRejected as exc:
                with self.db:
                    self.db.execute('UPDATE entries SET read_attempts=read_attempts+1 WHERE id=?', (e['id'],))
                    attempts = self.db.execute('SELECT read_attempts FROM entries WHERE id=?', (e['id'],)).fetchone()[0]
                exhausted = attempts >= MAX_ATTEMPTS
                self.report(e, 'read_exhausted' if exhausted else 'read_rejected',
                            f'{exc}; {attempts} of {MAX_ATTEMPTS} read attempts', 'blocked' if exhausted else None)
                if exhausted:
                    continue
                return
            except ActionRejected as exc:
                self.report(e, 'action_rejected', str(exc))
                return
            except Cancelled:
                return
            except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
                self.retry(e, str(exc))
                return

    def retry(self, e, detail):
        with self.db:
            self.db.execute('UPDATE entries SET transient_attempts=transient_attempts+1 WHERE id=?', (e['id'],))
            attempts = self.db.execute('SELECT transient_attempts FROM entries WHERE id=?', (e['id'],)).fetchone()[0]
        # Exhaustion stops polling; tested and merge_attempts retain any issued intent for reconciliation.
        exhausted = attempts >= MAX_ATTEMPTS
        self.report(e, 'retry_exhausted' if exhausted else 'retry', detail,
                    'exhausted' if exhausted else None)

    def merge_pending(self, repo, n, expected):
        owner, name = repo.split('/')
        query = ('query($owner:String!,$repo:String!,$n:Int!){repository(owner:$owner,name:$repo){'
                 'pullRequest(number:$n){headRefOid state autoMergeRequest{enabledAt} mergeQueueEntry{id}}}}')
        data = self.api('graphql', '-f', f'query={query}', '-f', f'owner={owner}', '-f', f'repo={name}', '-F', f'n={n}')
        if data.get('errors'):
            raise RuntimeError('merge intent readback unavailable')
        p = data['data']['repository']['pullRequest']
        if p['headRefOid'] != expected or p['state'] != 'OPEN':
            raise RuntimeError('merge intent readback changed; retry observation')
        return p['autoMergeRequest'] is not None or p['mergeQueueEntry'] is not None

    def reconcile(self, entry, retry=False):
        with self.runner_lock():
            self._reconcile(entry, retry)

    def _reconcile(self, entry, retry=False):
        e = self.db.execute('SELECT * FROM entries WHERE id=?', (entry,)).fetchone()
        if not e or e['phase'] not in ('merging', 'merge_rejected', 'exhausted', 'blocked') or not e['tested']:
            raise RuntimeError('reconciliation requires a persisted merge intent')
        p = self.pr(e['repo'], e['pr'])
        if p.get('merged'):
            if retry:
                with self.db:
                    self.db.execute('UPDATE entries SET transient_attempts=0,read_attempts=0 WHERE id=?', (entry,))
                self.report(e, 'reconciled', 'Provider reports merged; resume commit confirmation and post-merge refresh', 'merging')
            return
        if self.held(p) or p['base']['ref'] != 'main':
            raise RuntimeError('head is held or base changed; no retry authorized')
        if self.merge_pending(e['repo'], e['pr'], e['tested']):
            self.report(e, 'merge_pending', 'Provider still owns queued or automatic merge; waiting')
        elif retry:
            if e['merge_attempts'] >= MAX_ATTEMPTS:
                self.report(e, 'merge_exhausted', 'Provider readback complete; merge attempt cap reached', 'exhausted')
            else:
                self.report(e, 'reconciled', 'Explicit retry after exact-head provider readback proved no pending merge', 'pending')
        else:
            self.report(e, 'retry_available', 'Provider proved no pending merge; explicit reconcile --retry can rearm it')

    def reconcile_action(self, key, retry=False):
        with self.runner_lock():
            self._reconcile_action(key, retry)

    def _reconcile_action(self, key, retry=False):
        a = self.db.execute('SELECT * FROM actions WHERE key=?', (key,)).fetchone()
        if not a or a['kind'] not in ('update', 'retarget') or a['phase'] != 'issued':
            raise RuntimeError('reconciliation requires an issued update or retarget intent')
        p = self.pr(a['repo'], a['pr'])
        completed = (a['kind'] == 'update' and p['head']['sha'] != a['head']) or (a['kind'] == 'retarget' and p['base']['ref'] == a['payload'])
        if completed or p['state'] != 'open':
            with self.db:
                self.db.execute("UPDATE actions SET phase='done' WHERE key=?", (key,))
            return
        if self.held(p) or p['head']['sha'] != a['head']:
            raise RuntimeError('action head moved or is held; no retry')
        if a['kind'] == 'retarget' and (not a['expected_base'] or p['base']['ref'] != a['expected_base']):
            raise RuntimeError('retarget source base changed or is unknown; no retry')
        if a['kind'] == 'update' and (not self.behind(a['repo'], p) or p.get('mergeable') is not True):
            raise RuntimeError('action no longer has a clean BEHIND head')
        if retry:
            with self.db:
                self.db.execute('UPDATE actions SET phase=? WHERE key=?',
                                ('exhausted' if a['attempts'] >= MAX_ATTEMPTS else 'planned', key))
                self.event(a['repo'], a['pr'], 'action_reconciled', 'Explicit retry after current head/base/label readback; next effect remains expected-head guarded')

    def import_legacy(self, legacy):
        rows, sources = [], {}
        for name in ('merge-queue.txt', 'merge-queue.done'):
            path = legacy / name
            if not path.exists():
                continue
            data = path.read_bytes()
            sources[name] = hashlib.sha256(data).hexdigest()
            # Legacy enqueue appends a complete newline-terminated row. A partial tail is retried next poll.
            for line in data.decode().splitlines(keepends=True):
                if not line.endswith('\n'):
                    continue
                if not line.strip() or line.lstrip().startswith('#'):
                    continue
                parts = line.split(maxsplit=3)
                if len(parts) < 3 or parts[0] not in REPOS or not parts[1].isdigit() or not SHA.fullmatch(parts[2]):
                    raise ValueError(f'Invalid legacy queue row in {name}; no rows imported')
                rows.append((parts[0], int(parts[1]), parts[2], parts[3] if len(parts) == 4 else ''))
        for row in rows:
            self.enqueue(*row)
        receipt = self.state / 'legacy-import.json'
        temporary = receipt.with_suffix('.tmp')
        temporary.write_text(json.dumps({'sources': sources, 'rows': len(rows),
                                         'distinct': len({r[:3] for r in rows}),
                                         'policy': 'Ignore pointers and .done as success; reconcile GitHub.'}, indent=2))
        temporary.replace(receipt)

    def migrate_holds(self, legacy, apply=False):
        patterns = []
        path = legacy / 'merge-holds.txt'
        if path.exists():
            for line in path.read_text().splitlines():
                if not line.strip() or line.lstrip().startswith('#'):
                    continue
                parts = line.split(maxsplit=2)
                if len(parts) < 2 or parts[0] not in REPOS:
                    raise ValueError('Invalid legacy hold; migration requires review')
                patterns.append((parts[0], re.compile(parts[1], re.I)))
        registry = legacy / 'registry-merge-hold.txt'
        allowlist = {int(x) for x in registry.read_text().split() if x.isdigit()} if registry.exists() else set()
        plan = []
        for repo in REPOS:
            for p in self.pages(f'repos/{repo}/pulls?state=open&per_page=100'):
                if self.held(p):
                    continue
                reasons = []
                if re.search(r'\bdo_not_merge\b', p['title'], re.I):
                    reasons.append('legacy title marker')
                if any(r == repo and pattern.search(p['title']) for r, pattern in patterns):
                    reasons.append('legacy merge-holds title pattern')
                if repo == REPOS[0] and registry.exists() and p['number'] not in allowlist:
                    files = self.pages(f'repos/{repo}/pulls/{p["number"]}/files?per_page=100')
                    if any(f['filename'].startswith(('migrations/', 'mcp-server/src/scac-mutation-registry')) for f in files):
                        reasons.append('legacy registry freeze; PR is outside its allowlist')
                if reasons:
                    plan.append({'repo': repo, 'pr': p['number'], 'reasons': reasons})
        if apply:
            for repo in sorted({r['repo'] for r in plan}):
                self.gh('label', 'create', HOLD, '-R', repo, '--color', 'B60205', '--force',
                        '--description', 'Single merge hold; queue and refresh skip this PR')
            for r in plan:
                self.api(f'repos/{r["repo"]}/issues/{r["pr"]}/labels', '-X', 'POST', '-f', f'labels[]={HOLD}')
                if not self.held(self.pr(r['repo'], r['pr'])):
                    raise RuntimeError('hold label readback failed')
                with self.db:
                    self.event(r['repo'], r['pr'], 'held', '; '.join(r['reasons']))
            (self.state / 'hold-migration.json').write_text(json.dumps({'legacy': str(legacy.resolve()),
                                                                      'applied': time.time(), 'plan': plan}))
        return plan

    def archive_legacy(self, legacy):
        success = self.db.execute("SELECT * FROM entries WHERE phase='done' AND tested IS NOT NULL ORDER BY id DESC LIMIT 1").fetchone()
        if success is None:
            raise RuntimeError('archive requires a confirmed merge made by this agent')
        p = self.pr(success['repo'], success['pr'])
        if not p.get('merged') or p['head']['sha'] != success['tested']:
            raise RuntimeError('agent merge readback failed')
        self.fetch(success['repo'], p['merge_commit_sha'])
        self.git(success['repo'], 'merge-base', '--is-ancestor', p['merge_commit_sha'], 'origin/main')
        self.import_legacy(legacy)
        target = legacy / '_to_delete'
        target.mkdir(exist_ok=True)
        for path in legacy.iterdir():
            if path.is_file() and path.name.startswith(('merge-', 'auto-enqueue', 'wait-green-enqueue', 'gate-merge')) and '.sh' in path.name:
                dest = target / path.name
                if dest.exists():
                    dest = target / (path.name + '.' + hashlib.sha256(path.read_bytes()).hexdigest()[:12])
                shutil.move(str(path), str(dest))
        python = self.root / '.venv/bin/python'
        argv = [str(python), str(self.root / 'tools/merge_queue/main.py'), '--state', str(self.state), 'enqueue']
        (legacy / 'merge-enqueue.sh').write_text('#!/bin/zsh\nexec ' + shlex.join(argv) + ' "$@"\n')
        (legacy / 'merge-enqueue.sh').chmod(0o755)

    def install_agent(self, target, apply=False):
        body = (self.root / 'ops/launchd/com.carr.merge-queue.plist').read_text().replace('{{REPO}}', str(self.root))
        target.parent.mkdir(parents=True, exist_ok=True)
        if not apply:
            target.write_text(body)
            return
        from lib.carr_paths import canonical_checkout
        from lib.machine_role import is_primary
        if self.root.resolve() != Path(canonical_checkout()).resolve() or not is_primary(str(self.root)):
            raise RuntimeError('agent activation requires the canonical checkout on the primary machine')
        branch = command(['git', '-C', str(self.root), 'branch', '--show-current']).strip()
        if branch != 'main' or command(['git', '-C', str(self.root), 'rev-parse', 'HEAD']).strip() != command(['git', '-C', str(self.root), 'rev-parse', 'origin/main']).strip():
            raise RuntimeError('agent activation requires current main after review')
        config = runpy.run_path(str(self.root / 'ops/config-as-code.py'))
        matches = target.exists() and target.read_text() == body
        outcome = config['install_launchd_plist'](target.name, str(target), body, matches)
        if outcome not in ('loaded', 'kept'):
            raise RuntimeError(f'agent installation {outcome}; reconcile launchd before retry')

    def run(self, poll=30, discover=True, legacy=None):
        if legacy is not None:
            receipt = self.state / 'hold-migration.json'
            if not receipt.exists() or json.loads(receipt.read_text()).get('legacy') != str(legacy.resolve()):
                raise RuntimeError('handover requires migrate-holds --apply and label readback first')
        with self.runner_lock():
            self.stopped = False
            def stop(*_):
                self.stopped = True
            signal.signal(signal.SIGTERM, stop)
            signal.signal(signal.SIGINT, stop)
            next_discover = 0.0
            while not self.stopped:
                try:
                    if legacy is not None:
                        try:
                            self.import_legacy(legacy)
                        except (ValueError, OSError) as exc:
                            with (self.state / 'queue.log').open('a') as f:
                                f.write(json.dumps({'outcome': 'legacy_import_failed', 'detail': str(exc)}) + '\n')
                    if discover and time.monotonic() >= next_discover:
                        self.discover()
                        next_discover = time.monotonic() + 300
                    self.tick()
                    self.dispatch_conflicts()
                except Cancelled:
                    pass
                except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
                    with (self.state / 'queue.log').open('a') as f:
                        f.write(json.dumps({'outcome': 'poll_failed', 'detail': str(exc)}) + '\n')
                finally:
                    self.flush_events()
                end = time.monotonic() + poll
                while not self.stopped and time.monotonic() < end:
                    time.sleep(min(.2, max(0, end - time.monotonic())))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--state', type=Path, default=ROOT / 'out/merge-queue')
    p.add_argument('--root', type=Path, default=ROOT)
    sub = p.add_subparsers(dest='cmd', required=True)
    run = sub.add_parser('run')
    run.add_argument('--poll', type=float, default=30)
    run.add_argument('--no-discover', action='store_true')
    run.add_argument('--legacy', type=Path)
    enq = sub.add_parser('enqueue')
    enq.add_argument('repo', choices=REPOS)
    enq.add_argument('pr', type=int)
    enq.add_argument('approved')
    enq.add_argument('note', nargs='?', default='')
    sub.add_parser('status')
    reconcile = sub.add_parser('reconcile')
    reconcile.add_argument('entry', type=int)
    reconcile.add_argument('--retry', action='store_true')
    action_reconcile = sub.add_parser('reconcile-action')
    action_reconcile.add_argument('key')
    action_reconcile.add_argument('--retry', action='store_true')
    installer = sub.add_parser('install-agent')
    installer.add_argument('--output', type=Path)
    installer.add_argument('--apply', action='store_true')
    for name in ('import-legacy', 'migrate-holds', 'archive-legacy'):
        migration = sub.add_parser(name)
        migration.add_argument('legacy', type=Path)
        if name == 'migrate-holds':
            migration.add_argument('--apply', action='store_true')
    a = p.parse_args()
    q = Queue(a.state, a.root)
    if a.cmd == 'run':
        q.run(a.poll, not a.no_discover, a.legacy)
    elif a.cmd == 'enqueue':
        print(q.enqueue(a.repo, a.pr, a.approved, a.note))
    elif a.cmd == 'import-legacy':
        q.import_legacy(a.legacy)
    elif a.cmd == 'migrate-holds':
        print(json.dumps(q.migrate_holds(a.legacy, a.apply), indent=2))
    elif a.cmd == 'archive-legacy':
        q.archive_legacy(a.legacy)
    elif a.cmd == 'install-agent':
        target = a.output or (Path.home() / 'Library/LaunchAgents/com.carr.merge-queue.plist' if a.apply else a.state / 'agent-plan.plist')
        q.install_agent(target, a.apply)
        print(target)
    elif a.cmd == 'reconcile':
        q.reconcile(a.entry, a.retry)
    elif a.cmd == 'reconcile-action':
        q.reconcile_action(a.key, a.retry)
    else:
        print(json.dumps([dict(r) for r in q.db.execute('SELECT * FROM entries ORDER BY id')], indent=2))

if __name__ == '__main__':
    main()
