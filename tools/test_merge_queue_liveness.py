#!/usr/bin/env python3
"""External-fault liveness through Queue.run, durable restarts and reconciliation."""
import ast
from collections import Counter
from contextlib import ExitStack
from dataclasses import dataclass
import importlib.util
import json
from pathlib import Path
import signal
import subprocess
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('queue_fixture', ROOT / 'tools/test_merge_queue.py')
fixture_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture_module)
mq = fixture_module.module


@dataclass(frozen=True)
class Effect:
    name: str
    sites: tuple
    scenario: str = 'normal'


EFFECTS = (
    Effect('pr_read', (('pr', 'api'),)),
    Effect('approval', (('approval', 'pages'),)),
    Effect('check_runs', (('green', 'api_pages'),)),
    Effect('statuses', (('green', 'pages'),)),
    Effect('required_checks', (('green', 'gh'),)),
    Effect('update', (('action', 'api'),), 'behind'),
    Effect('retarget', (('action', 'gh'),), 'retarget'),
    Effect('stamp', (('_tick_repo', 'api'),)),
    Effect('ready', (('_tick_repo', 'gh'),), 'draft'),
    Effect('merge', (('_tick_repo', 'gh'),)),
    Effect('merge_intent', (('merge_pending', 'api'),), 'rejected'),
    Effect('open_prs', (('discover', 'pages'), ('refresh', 'pages'), ('migrate_holds', 'pages'))),
    Effect('files', (('migrate_holds', 'pages'),), 'migration'),
    Effect('create_label', (('migrate_holds', 'gh'),), 'migration'),
    Effect('apply_label', (('migrate_holds', 'api'),), 'migration'),
    Effect('fetch', (('fetch', 'git'),), 'fetch'),
    Effect('board', (('flush_events', 'command'),)),
)


def census(source):
    """Each external call site must belong to an exercised fault-table row."""
    calls = Counter()
    tree = ast.parse(source)
    for method in ast.walk(tree):
        if not isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(method):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id == 'self':
                if func.attr in ('gh', 'api', 'pages', 'api_pages') and method.name not in ('api', 'pages', 'api_pages'):
                    calls[(method.name, func.attr)] += 1
                elif func.attr == 'git' and len(node.args) > 1 and isinstance(node.args[1], ast.Constant) and node.args[1].value == 'fetch':
                    calls[(method.name, 'git')] += 1
            elif isinstance(func, ast.Name) and func.id == 'command' and 'progress_board.py' in ast.unparse(node):
                calls[(method.name, 'command')] += 1
    return calls


def transports(source):
    calls = Counter()
    for method in ast.walk(ast.parse(source)):
        if not isinstance(method, ast.FunctionDef):
            continue
        for node in ast.walk(method):
            if not isinstance(node, ast.Call):
                continue
            name = ast.unparse(node.func)
            if name in ('command', 'subprocess.run', 'subprocess.Popen'):
                calls[(method.name, name)] += 1
            elif name == 'self.git':
                calls[(method.name, 'git:' + ast.unparse(node.args[1]))] += 1
    return calls


def operation(argv):
    if argv[0] == 'gh':
        a = argv[1:]
        if a[0] == 'api':
            if a[1] == 'graphql':
                repo = next(x[6:] for x in a if x.startswith('owner=')) + '/' + next(x[5:] for x in a if x.startswith('repo='))
                return 'merge_intent', repo, int(next(x[2:] for x in a if x.startswith('n=')))
            bits = a[1].split('?')[0].split('/')
            repo = '/'.join(bits[1:3])
            if bits[3] == 'pulls' and len(bits) == 4:
                return 'open_prs', repo, None
            if bits[3] == 'commits':
                return ('check_runs' if bits[-1] == 'check-runs' else 'statuses'), repo, bits[4]
            n = int(bits[4])
            if bits[3] == 'pulls':
                return ('pr_read' if len(bits) == 5 else 'update' if bits[5] == 'update-branch' else 'files'), repo, n
            if bits[-1] == 'labels':
                return 'apply_label', repo, n
            return ('stamp' if any(x.startswith('body=') for x in a) else 'approval'), repo, n
        return {'checks': 'required_checks', 'edit': 'retarget', 'ready': 'ready', 'merge': 'merge', 'create': 'create_label'}[a[1]], a[a.index('-R') + 1], None if a[0] == 'label' else int(a[2])
    if argv[0] == 'git' and 'fetch' in argv:
        name = Path(argv[argv.index('-C') + 1]).name.removesuffix('.git')
        return 'fetch', 'jbookout/' + name, None
    if len(argv) > 1 and argv[1].endswith('/tools/progress_board.py'):
        return 'board', None, None
    return None


class RunLoop:
    def __init__(self, fixture, effect, fault):
        self.f, self.effect, self.fault = fixture, effect, fault
        self.now = 10000.0
        self.active = True
        self.failures = 0
        self.seen = Counter()
        self.merged = []
        self.reconciliations = 0
        self.polls = 0
        self.discoveries = 0
        self.commands = mq.command
        self.legacy = fixture.root / 'legacy'
        self.legacy.mkdir()
        (self.legacy / 'registry-merge-hold.txt').write_text('2 3 4 5 6 90')

    def target(self, op, argv):
        name, repo, owner = op
        if name != self.effect.name or repo not in (None, mq.REPOS[0]):
            return False
        if name in ('check_runs', 'statuses'):
            return owner == self.f.approved
        if name == 'fetch':
            return self.f.updated in argv
        return owner in (None, 1)

    def request(self, argv, **kwargs):
        op = operation(argv)
        if op is None:
            return self.commands(argv, **kwargs)
        self.seen[op[0]] += 1
        if self.active and self.fault != 'pause' and self.target(op, argv):
            self.failures += 1
            if self.fault == 'timeout':
                raise subprocess.TimeoutExpired(argv, kwargs.get('timeout', 120))
            if argv[0] == 'gh':
                read = mq.gh_api_read(argv) if argv[1] == 'api' else argv[1:3] == ['pr', 'checks']
                raise (mq.ReadRejected if read else mq.ActionRejected)('injected persistent external rejection')
            raise OSError('injected persistent external failure')
        if argv[0] != 'gh':
            return '' if op[0] == 'board' else self.commands(argv, **kwargs)
        a = argv[1:]
        self.f.data['calls'].append(a)
        name, repo, n = op
        p = self.f.data['prs'].get(f'{repo}#{n}')
        if name == 'pr_read':
            result = p
        elif name == 'open_prs':
            result = [p for p in self.f.data['prs'].values() if p['repo'] == repo and p['state'] == 'open']
        elif name == 'approval':
            result = [dict(c, id=i+1) for i, c in enumerate(self.f.data['comments'][f'{repo}#{n}'])]
        elif name == 'check_runs':
            result = {'check_runs': [{'id': 1, 'name': 'CI', 'app': {'id': 1}, 'status': 'completed', 'conclusion': 'success'}]}
        elif name == 'statuses':
            result = []
        elif name == 'required_checks':
            result = [{'bucket': 'pass'}]
            if n == 5:
                p['head']['sha'] = self.f.changed
            elif n == 6:
                p['labels'] = [{'name': mq.HOLD}]
        elif name == 'stamp':
            self.f.data['comments'][f'{repo}#{n}'].append({'body': next(x[5:] for x in a if x.startswith('body=')), 'author_association': 'OWNER'})
            result = {'id': 100}
        elif name == 'update':
            p['head']['sha'], p['mergeable_state'] = self.f.updated, 'clean'
            result = {'message': 'Updated'}
        elif name == 'retarget':
            p['base']['ref'], p['mergeable_state'] = 'main', 'clean'
            result = {}
        elif name == 'ready':
            p['draft'] = False
            result = {}
        elif name == 'merge':
            if self.effect.scenario == 'rejected' and n == 1 and self.active:
                raise mq.MergeRejected('injected provider merge rejection')
            head = a[a.index('--match-head-commit') + 1]
            assert head == p['head']['sha'], 'wrong-head merge'
            assert not any(x['name'] == mq.HOLD for x in p['labels']), 'hold bypass'
            assert n not in (3, 4, 5, 6), 'unapproved or held head merged'
            assert head in (self.f.approved, self.f.updated), 'unapproved patch merged'
            p.update(merged=True, state='closed', merge_commit_sha=self.f.merge_sha)
            self.commands(['git', '-C', str(self.f.remote), 'update-ref', 'refs/heads/main', self.f.merge_sha])
            self.merged.append((repo, n, head))
            result = {}
        elif name == 'merge_intent':
            result = {'data': {'repository': {'pullRequest': {'headRefOid': p['head']['sha'], 'state': 'OPEN', 'autoMergeRequest': None, 'mergeQueueEntry': None}}}}
        elif name == 'files':
            result = [{'filename': 'migrations/test.sql'}] if n == 1 else []
        elif name == 'create_label':
            result = {'name': mq.HOLD}
        elif name == 'apply_label':
            p['labels'] = [{'name': mq.HOLD}]
            result = p['labels']
        else:
            raise AssertionError(f'unregistered effect: {op}')
        self.f.save()
        out = json.dumps(result)
        return 'HTTP/2.0 200 OK\n\n' + out if '--include' in a else out

    def seed(self):
        f, scenario = self.f, self.effect.scenario
        if scenario == 'retarget':
            p = f.pr(90)
            p.update(merged=True, state='closed', merge_commit_sha=f.merge_sha)
            f.save()
            self.commands(['git', '-C', str(f.remote), 'update-ref', 'refs/heads/main', f.merge_sha])
            entry = f.q.enqueue(mq.REPOS[0], 90, f.approved)
            with f.q.db:
                f.q.db.execute("UPDATE entries SET phase='merging',tested=? WHERE id=?", (f.approved, entry))
        f.pr(1, head=f.updated if scenario == 'fetch' else f.approved,
             state='behind' if scenario == 'behind' else 'clean', draft=scenario == 'draft',
             base='branch-90' if scenario == 'retarget' else 'main')
        entry = f.q.enqueue(mq.REPOS[0], 1, f.approved)
        if scenario == 'fetch':
            with f.q.db:
                f.q.db.execute("UPDATE entries SET phase='review' WHERE id=?", (entry,))
        f.pr(2, head=f.approved if scenario == 'fetch' else f.updated)
        f.q.enqueue(mq.REPOS[0], 2, f.approved)
        f.pr(3, held=True)
        f.pr(4, head=f.changed)
        f.pr(5, head=f.updated)
        f.pr(6, head=f.updated)
        for n in (3, 4, 5, 6):
            f.q.enqueue(mq.REPOS[0], n, f.approved)
        for repo in mq.REPOS[1:]:
            f.pr(2, repo=repo)
            f.q.enqueue(repo, 2, f.approved)
        return entry

    def session(self, polls=8):
        q = self.f.q
        deadline = self.now + polls * 30
        saved_handlers = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
        def sleep(seconds):
            self.now += max(seconds, .01)
            if self.now >= deadline:
                q.stopped = True
        def discover():
            self.discoveries += 1
            return original_discover()
        original_discover = q.discover
        original_flush = q.flush_events
        def flush():
            self.polls += 1
            return original_flush()
        with ExitStack() as stack:
            stack.enter_context(patch.object(mq, 'command', self.request))
            stack.enter_context(patch.object(mq, 'time', types.SimpleNamespace(time=lambda: self.now, monotonic=lambda: self.now, sleep=sleep)))
            stack.enter_context(patch.object(q, 'discover', discover))
            stack.enter_context(patch.object(q, 'flush_events', flush))
            if self.fault == 'pause':
                original_request, original_reserve = q._gh_request, q.budget.reserve
                current = []
                def request(args, decode, validate):
                    current[:] = ['gh', *args]
                    return original_request(args, decode, validate)
                def reserve(resource):
                    if self.active and self.target(operation(current), current):
                        raise mq.GitHubReadPaused(self.now + 900)
                    return original_reserve(resource)
                stack.enter_context(patch.object(q, '_gh_request', request))
                stack.enter_context(patch.object(q.budget, 'reserve', reserve))
            if self.effect.scenario == 'migration':
                try:
                    q.migrate_holds(self.legacy, apply=True)
                except (RuntimeError, OSError, subprocess.TimeoutExpired):
                    pass
            q.run(poll=30)
            q.stopped = False
            for e in q.db.execute("SELECT id FROM entries WHERE tested IS NOT NULL AND phase IN ('merging','merge_rejected','exhausted','blocked')").fetchall():
                self.reconciliations += 1
                try:
                    q.reconcile(e['id'], retry=not self.active)
                except (RuntimeError, OSError, subprocess.TimeoutExpired):
                    pass
            for a in q.db.execute("SELECT key FROM actions WHERE kind IN ('update','retarget') AND phase='issued'").fetchall():
                self.reconciliations += 1
                try:
                    q.reconcile_action(a['key'], retry=not self.active)
                except (RuntimeError, OSError, subprocess.TimeoutExpired):
                    pass
        for s, handler in saved_handlers.items():
            signal.signal(s, handler)


class LivenessTests(unittest.TestCase):
    def test_fault_table_covers_external_call_sites(self):
        source = '\n'.join(p.read_text() for p in sorted((ROOT / 'tools/merge_queue').rglob('*.py')))
        expected = Counter(site for effect in EFFECTS for site in effect.sites)
        self.assertEqual(census(source), expected,
                         'New external call sites require a fault-table entry and run-loop scenario')
        added = source + '\ndef new_effect(self):\n    self.api("new/external/path")\n'
        self.assertNotEqual(census(added), expected)
        known_transports = Counter({
            ('command', 'subprocess.run'): 1, ('_gh_request', 'command'): 1,
            ('flush_events', 'command'): 1, ('git', 'command'): 3,
            ('patch', 'command'): 1, ('conflict', 'subprocess.run'): 1,
            ('dispatcher_identity', 'command'): 1, ('_dispatch_conflicts', 'command'): 2,
            ('_dispatch_conflicts', 'subprocess.Popen'): 1, ('behind', 'subprocess.run'): 1,
            ('install_agent', 'command'): 3,
            ('fetch', "git:'fetch'"): 1, ('patch', "git:'diff'"): 1,
            ('patch', "git:'merge-base'"): 1, ('_tick_repo', "git:'merge-base'"): 1,
            ('archive_legacy', "git:'merge-base'"): 1,
        })
        self.assertEqual(transports(source), known_transports,
                         'New subprocess routes must be classified and assigned a fault scenario')
        direct = source + '\ndef bypass(self):\n    command(["gh", "api", "new/path"])\n'
        self.assertNotEqual(transports(direct), known_transports)

    def test_failed_discovery_keeps_tick_and_the_discovery_schedule_running(self):
        f = fixture_module.QueueTests()
        f.setUp()
        try:
            loop = RunLoop(f, EFFECTS[0], 'error')
            loop.active = False
            loop.seed()
            original = f.q.discover
            times = []
            def fail_first():
                times.append(loop.now)
                if len(times) == 1:
                    raise OSError('injected discovery failure before tick')
                return original()
            with patch.object(f.q, 'discover', fail_first):
                loop.session(polls=22)
            self.assertEqual(len(times), 3)
            self.assertTrue(all(b - a >= 300 for a, b in zip(times, times[1:])))
            self.assertTrue({(r, 2) for r in mq.REPOS} <= {(r, n) for r, n, _ in loop.merged})
            self.assertIn('discovery_failed', (f.state / 'queue.log').read_text())
        finally:
            f.doCleanups()

    def test_persistent_faults_release_other_work_across_restarts(self):
        for effect in EFFECTS:
            for fault in ('error', 'timeout'):
                with self.subTest(effect=effect.name, fault=fault):
                    f = fixture_module.QueueTests()
                    f.setUp()
                    try:
                        loop = RunLoop(f, effect, fault)
                        entry = loop.seed()
                        for _ in range(4):
                            loop.session()
                            f.restart()
                        self.assertGreater(loop.failures, 0, 'Fault site was never reached')
                        limit = 8 if effect.name == 'merge_intent' else 4
                        self.assertLessEqual(loop.failures, limit, 'Persistent fault exceeded automatic and reconciliation budgets')
                        count = loop.failures
                        loop.session()
                        self.assertEqual(loop.failures, count, 'Restart replenished the failure budget')
                        progressed = {(repo, n) for repo, n, _ in loop.merged}
                        self.assertTrue({(repo, 2) for repo in mq.REPOS} <= progressed,
                                        'Persistent fault starved another repository or the next PR')
                        loop.active = False
                        f.restart()
                        loop.session()
                        f.restart()
                        loop.session()
                        row = f.q.db.execute('SELECT * FROM entries WHERE id=?', (entry,)).fetchone()
                        events = f.q.db.execute('SELECT outcome,detail FROM events WHERE repo=? AND pr IN (0,1)', (mq.REPOS[0],)).fetchall()
                        recovered = (mq.REPOS[0], 1) in {(repo, n) for repo, n, _ in loop.merged}
                        visible = any(e['detail'] and any(word in (e['outcome'] + e['detail']).lower() for word in ('exhaust', 'blocked', 'held')) for e in events)
                        self.assertTrue(recovered or visible, f'Entry silently stuck: {dict(row)}')
                        if effect.name == 'board':
                            self.assertIn('exhaust', (f.state / 'queue.log').read_text().lower())
                        self.assertGreaterEqual(loop.discoveries, 4)
                        self.assertGreaterEqual(loop.polls, 4)
                    finally:
                        f.doCleanups()

    def test_resource_and_mutation_pauses_do_not_charge_or_issue_intents(self):
        for name in ('merge_intent', 'update', 'merge'):
            with self.subTest(effect=name):
                f = fixture_module.QueueTests()
                f.setUp()
                try:
                    effect = next(e for e in EFFECTS if e.name == name)
                    loop = RunLoop(f, effect, 'pause')
                    entry = loop.seed()
                    for _ in range(3):
                        loop.session()
                        f.restart()
                    row = f.q.db.execute('SELECT * FROM entries WHERE id=?', (entry,)).fetchone()
                    self.assertEqual((row['github_attempts'], row['read_attempts'], row['transient_attempts']),
                                     (1 if name == 'merge_intent' else 0, 0, 0))
                    if name in ('update', 'merge'):
                        self.assertEqual(row['merge_attempts'], 0)
                        self.assertNotIn((mq.REPOS[0], 1), {(r, n) for r, n, _ in loop.merged})
                        actions = f.q.db.execute('SELECT phase,attempts FROM actions WHERE pr=1').fetchall()
                        self.assertTrue(all(tuple(a) == ('planned', 0) for a in actions))
                    progressed = {(r, n) for r, n, _ in loop.merged}
                    self.assertTrue({(r, 2) for r in mq.REPOS} <= progressed)
                    loop.active = False
                    loop.session()
                    f.restart()
                    loop.session()
                    self.assertIn((mq.REPOS[0], 1), {(r, n) for r, n, _ in loop.merged})
                finally:
                    f.doCleanups()

    def test_rate_limit_pause_is_scheduling_and_recovers_after_restarts(self):
        f = fixture_module.QueueTests()
        f.setUp()
        try:
            loop = RunLoop(f, EFFECTS[0], 'pause')
            loop.active = False
            loop.seed()
            limiter = Path(f.env['GH_LIMITER_DIR'])
            limiter.mkdir()
            (limiter / 'cooldown').write_text(str(__import__('time').time() + 900))
            for _ in range(6):
                loop.session(polls=1)
                self.assertEqual(f.q.db.execute('SELECT COUNT(*) FROM github_failures').fetchone()[0], 0)
                self.assertEqual(tuple(f.q.db.execute('SELECT SUM(github_attempts),SUM(read_attempts),SUM(transient_attempts) FROM entries').fetchone()), (0, 0, 0))
                f.restart()
            self.assertEqual(loop.merged, [])
            (limiter / 'cooldown').unlink()
            loop.session(polls=12)
            self.assertTrue({(repo, 2) for repo in mq.REPOS} <= {(repo, n) for repo, n, _ in loop.merged})
            self.assertIn((mq.REPOS[0], 1), {(repo, n) for repo, n, _ in loop.merged})
        finally:
            f.doCleanups()


if __name__ == '__main__':
    unittest.main()
