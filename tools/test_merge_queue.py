#!/usr/bin/env python3
"""Queue contract tests at the command/gh seam."""
import importlib.util
import json
import os
import fcntl
import signal
import time
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / 'tools/merge_queue/main.py'
sys.path.insert(0, str(ROOT / 'ops'))
from git_env import fixture_env

FAKE_GH = r'''#!/usr/bin/env python3
import json, os, subprocess, sys
from pathlib import Path
f=Path(os.environ['FAKE_GH_STATE']); d=json.loads(f.read_text()); a=sys.argv[1:]
d['calls'].append(a)
def save(): f.write_text(json.dumps(d))
def emit(x):
 save()
 if '--include' in a:
  if isinstance(x,list) and len(x)==1 and (isinstance(x[0],list) or isinstance(x[0],dict) and 'check_runs' in x[0]): x=x[0]
  print('HTTP/2.0 200 OK\n\n'+json.dumps(x))
 else: print(json.dumps(x))
if a[:2]==['label','create']: emit({'name':'do_not_merge'});sys.exit()
if a[:2]==['api','graphql']:
 repo=next(x[5:] for x in a if x.startswith('repo='));owner=next(x[6:] for x in a if x.startswith('owner='));n=next(x[2:] for x in a if x.startswith('n='));p=d['prs'][owner+'/'+repo+'#'+n]
 emit({'data':{'repository':{'pullRequest':{'headRefOid':p['head']['sha'],'state':'OPEN','mergeQueueEntry':{'id':'queued'} if d.get('server_queued') else None,'autoMergeRequest':None}}}});sys.exit()
if a[0]=='api':
 path=a[1]; bits=path.split('/'); repo='/'.join(bits[1:3]); n=bits[4].split('?')[0] if len(bits)>4 else ''
 if bits[3].startswith('pulls?'): emit([[p for p in d['prs'].values() if p['repo']==repo and p['state']=='open']]); sys.exit()
 if bits[3]=='labels': emit({'name':'do_not_merge'});sys.exit()
 if bits[3]=='pulls':
  p=d['prs'][repo+'#'+n]
  if len(bits)>5 and bits[5]=='update-branch':
   if d.get('update_reject'): save();print('gh: Update rejected (HTTP 422)',file=sys.stderr);sys.exit(1)
   if d.get('update_async'): emit({'message':'Updating'}); sys.exit()
   p['head']['sha']=d['updated_head']; p['mergeable_state']='clean'; emit({'message':'Updated'}); sys.exit()
  if len(bits)>5 and bits[5].startswith('files'): emit([p.get('files',[])]);sys.exit()
  emit(p); sys.exit()
 if bits[3]=='issues':
  key=repo+'#'+n
  if bits[5]=='labels':
   d['prs'][key]['labels']=[{'name':'do_not_merge'}];emit([{'name':'do_not_merge'}]);sys.exit()
  body=next((x[5:] for x in a if x.startswith('body=')),None)
  if body:
   d['comments'].setdefault(key,[]).append({'body':body,'author_association':'OWNER'}); emit({'id':1})
  else: emit([[dict(c,id=i+1) for i,c in enumerate(d['comments'].get(key,[]))]])
  sys.exit()
 if bits[3]=='commits':
  if bits[-1].startswith('check-runs'): emit([{'check_runs':d.get('runs',[{'id':1,'name':'CI','app':{'id':1},'status':'completed','conclusion':'success'}])}])
  elif bits[-1].startswith('statuses'): emit([d.get('statuses',[])])
  else: raise AssertionError(a)
  sys.exit()
if a[:2]==['pr','checks']: emit(d.get('required',[{'bucket':'pass'}])); sys.exit()
if a[:2]==['pr','ready']:
 repo=a[a.index('-R')+1];d['prs'][repo+'#'+a[2]]['draft']=False; save();sys.exit()
if a[:2]==['pr','edit']:
 repo=a[a.index('-R')+1];p=d['prs'][repo+'#'+a[2]];p['base']['ref']=a[a.index('--base')+1];p['base']['sha']=d['main'];p['mergeable_state']=d.get('retarget_state','behind');save();sys.exit()
if a[:2]==['pr','merge']:
 if d.get('merge_reject'): save();print('GraphQL: Pull request is not mergeable (mergePullRequest)',file=sys.stderr);sys.exit(1)
 repo=a[a.index('-R')+1];p=d['prs'][repo+'#'+a[2]]
 assert '--squash' in a and a[a.index('--match-head-commit')+1]==p['head']['sha']
 p['merged']=True;p['state']='closed';p['merge_commit_sha']=d['merge_sha']
 subprocess.run(['git','-C',d['remote'],'update-ref','refs/heads/main',d['merge_sha']],check=True)
 save();sys.exit()
raise AssertionError(a)
'''

spec = importlib.util.spec_from_file_location("merge_queue", ENTRY)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class QueueTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.dict(os.environ, fixture_env(), clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state = self.root / 'state'
        self.remote = self.root / 'remote'
        self.remote.mkdir()
        self.git('init', '-q', '-b', 'main')
        self.git('config', 'user.email', 'queue-test@example.test')
        self.git('config', 'user.name', 'Queue test')
        (self.remote / 'file.txt').write_text('base\n')
        self.git('add', 'file.txt')
        self.git('commit', '-qm', 'base')
        self.base = self.git('rev-parse', 'HEAD').strip()
        (self.remote / 'file.txt').write_text('reviewed\n')
        self.git('commit', '-qam', 'reviewed')
        self.approved = self.git('rev-parse', 'HEAD').strip()
        (self.remote / 'file.txt').write_text('unreviewed\n')
        self.git('commit', '-qam', 'changed')
        self.changed = self.git('rev-parse', 'HEAD').strip()
        self.git('branch', 'approved', self.approved)
        self.git('switch', '-q', 'main')
        self.git('reset', '--hard', self.base)
        (self.remote / 'other.txt').write_text('main advance\n')
        self.git('add', 'other.txt')
        self.git('commit', '-qm', 'main advance')
        self.main = self.git('rev-parse', 'HEAD').strip()
        self.git('switch', '-q', 'approved')
        self.git('merge', '-qm', 'automatic update', 'main')
        self.updated = self.git('rev-parse', 'HEAD').strip()
        tree = self.git('rev-parse', self.updated+'^{tree}').strip()
        self.merge_sha = self.git('commit-tree', tree, '-p', self.main, '-m', 'squash merge').strip()
        self.git('switch', '-q', 'main')
        self.fake_state = self.root / 'github.json'
        self.data = dict(prs={}, comments={}, calls=[], remote=str(self.remote), main=self.main,
                         updated_head=self.updated, merge_sha=self.merge_sha)
        self.save()
        bin_dir = self.root / 'bin'
        bin_dir.mkdir()
        gh = bin_dir / 'gh'
        gh.write_text(FAKE_GH)
        gh.chmod(0o755)
        ps = bin_dir / 'ps'
        ps.write_text('#!/bin/sh\nexit 0\n')
        ps.chmod(0o755)
        self.env = os.environ.copy()
        self.env['FAKE_GH_STATE'] = str(self.fake_state)
        self.env['PATH'] = str(bin_dir) + os.pathsep + self.env['PATH']
        self.env['CARR_GITHUB_READ_BUDGET'] = str(self.root / 'github-budget.json')
        self.env['GH_LIMITER_DIR'] = str(self.root / 'legacy-budget')
        patcher = patch.dict(os.environ, self.env)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.q = module.Queue(self.state, self.root, gap=0)
        self.addCleanup(self.q.db.close)
        self.addCleanup(lambda: [child.wait(timeout=5) for child in self.q.children.values()])
        for repo in module.REPOS:
            target = self.state / 'repos' / (repo.split('/')[-1] + '.git')
            target.parent.mkdir(exist_ok=True)
            subprocess.run(['git', 'clone', '-q', '--bare', str(self.remote), str(target)], check=True)
            subprocess.run(['git', '-C', str(target), 'fetch', '-q', 'origin',
                            '+refs/heads/main:refs/remotes/origin/main'], check=True)
        (self.root / 'tools').mkdir()
        (self.root / 'tools/progress_board.py').write_text(
            "import os,sys\nfrom pathlib import Path\n"
            "Path(__file__).with_suffix('.calls').open('a').write(repr(sys.argv[1:])+'\\n')\n"
            "sys.exit(1 if os.environ.get('BOARD_FAIL') else 0)\n")

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.remote), *args], text=True,
                              capture_output=True, check=True).stdout

    def save(self):
        self.fake_state.write_text(json.dumps(self.data))

    def load(self):
        self.data = json.loads(self.fake_state.read_text())
        return self.data

    def pr(self, n=1, repo=module.REPOS[0], head=None, state='clean', held=False, draft=False, base='main'):
        p = dict(number=n, repo=repo, head={'sha': head or self.approved, 'ref': f'branch-{n}'},
                 base={'sha': self.main, 'ref': base}, merged=False, state='open', draft=draft,
                 title='Example change', mergeable_state=state,
                 mergeable=False if state=='dirty' else True,
                 labels=[{'name':'do_not_merge'}] if held else [])
        self.data['prs'][f'{repo}#{n}']=p
        self.data['comments'][f'{repo}#{n}']=[{'body':f'APPROVE\nReviewed-SHA: {self.approved}', 'author_association':'OWNER'}]
        self.save()
        return p

    def calls(self, verb):
        return [a for a in self.load()['calls'] if a[:2]==['pr',verb]]

    def restart(self):
        self.q.db.close()
        self.q = module.Queue(self.state, self.root, gap=0)
        self.addCleanup(self.q.db.close)

    def test_block_quoting_queue_stamp_remains_authoritative(self):
        self.pr()
        self.data['comments'][module.REPOS[0]+'#1'].append({
            'body': f'BLOCK\nReviewed-SHA: {self.approved}\nThe "Orchestrator merge queue:" claim is wrong.',
            'author_association': 'OWNER'})
        self.save()
        self.q.enqueue(module.REPOS[0], 1, self.approved)
        self.q.tick()
        self.assertEqual(self.calls('merge'), [])
        self.assertEqual(self.q.db.execute('SELECT phase FROM entries').fetchone()[0], 'review')

    def test_sigterm_during_checks_prevents_every_later_effect(self):
        self.pr(draft=True)
        self.q.enqueue(module.REPOS[0], 1, self.approved)
        green = self.q.green
        def terminate(*args):
            result = green(*args)
            os.kill(os.getpid(), signal.SIGTERM)
            return result
        previous = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT)}
        try:
            with patch.object(self.q, 'green', side_effect=terminate):
                self.q.run(poll=0, discover=False)
        finally:
            for s, handler in previous.items(): signal.signal(s, handler)
        self.assertEqual(self.calls('merge'), [])
        self.assertEqual(self.calls('ready'), [])
        self.assertFalse(any(x.startswith('body=APPROVE') for a in self.load()['calls'] for x in a))
        self.assertEqual(self.q.db.execute('SELECT phase FROM entries').fetchone()[0], 'pending')

    def test_cancellation_during_action_readback_prevents_update_and_retarget(self):
        for kind, base in (('update', 'main'), ('retarget', 'branch-1')):
            with self.subTest(kind=kind):
                self.pr(n=2, state='behind', base=base)
                self.q.stopped = False
                read = self.q.pr
                def cancel(*args):
                    p = read(*args)
                    self.q.stopped = True
                    return p
                with patch.object(self.q, 'pr', side_effect=cancel):
                    self.q.action(kind, module.REPOS[0], 2, self.approved, 'main')
                self.assertEqual(self.calls('edit'), [])
                self.assertFalse(any('update-branch' in str(a) for a in self.load()['calls']))

    def test_reconciliation_uses_runner_lock_for_merge_and_action(self):
        self.pr(state='behind')
        self.q.enqueue(module.REPOS[0], 1, self.approved)
        with self.q.db:
            self.q.db.execute("UPDATE entries SET phase='merging',tested=?", (self.approved,))
            self.q.db.execute('INSERT INTO actions(key,kind,repo,pr,head,payload,phase) VALUES(?,?,?,?,?,?,?)',
                              ('locked', 'update', module.REPOS[0], 1, self.approved, 'main', 'issued'))
        with (self.state / 'agent.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            for reconcile in (lambda: self.q.reconcile(1, retry=True),
                              lambda: self.q.reconcile_action('locked', retry=True)):
                with self.assertRaises(BlockingIOError): reconcile()
        self.assertEqual(self.q.db.execute('SELECT phase FROM entries').fetchone()[0], 'merging')
        self.assertEqual(self.q.db.execute('SELECT phase FROM actions').fetchone()[0], 'issued')
        self.assertEqual(self.load()['calls'], [])

    def test_rejected_update_exhausts_after_three_retries_across_restart(self):
        self.pr(state='behind')
        self.data['update_reject'] = True; self.save()
        self.q.enqueue(module.REPOS[0], 1, self.approved)
        for _ in range(8):
            self.q.tick(); self.restart()
        self.assertEqual(sum('update-branch' in str(a) for a in self.load()['calls']), 4)
        self.assertEqual(self.q.db.execute('SELECT phase,outcome FROM entries').fetchone()[:],
                         ('exhausted', 'action_exhausted'))

    def test_rejected_merge_exhausts_after_three_retries_across_restart(self):
        self.pr(); self.data['merge_reject'] = True; self.save()
        self.q.enqueue(module.REPOS[0], 1, self.approved)
        for _ in range(12):
            self.q.tick(); self.restart()
        self.assertEqual(len(self.calls('merge')), 4)
        self.assertEqual(self.q.db.execute('SELECT phase,outcome FROM entries').fetchone()[:],
                         ('exhausted', 'merge_exhausted'))

    def test_waiting_repo_does_not_block_other_repo_but_preserves_local_fifo(self):
        self.pr(1, state='unknown'); self.pr(2); self.pr(3, repo=module.REPOS[1])
        for repo, n in ((module.REPOS[0], 1), (module.REPOS[0], 2), (module.REPOS[1], 3)):
            self.q.enqueue(repo, n, self.approved)
        self.q.tick()
        self.assertEqual([a[2:5] for a in self.calls('merge')], [['3', '-R', module.REPOS[1]]])

    def test_default_gh_spacing_is_two_seconds_and_shared_between_queues(self):
        self.pr()
        q = module.Queue(self.state, self.root)
        other = module.Queue(self.root / 'other-state', self.root)
        self.addCleanup(q.db.close); self.addCleanup(other.db.close)
        starts = []
        real_command = module.command
        def observe(argv, **kwargs):
            if argv[0] == 'gh': starts.append(time.monotonic())
            return real_command(argv, **kwargs)
        with patch.object(module, 'command', side_effect=observe):
            q.pr(module.REPOS[0], 1)
            other.pr(module.REPOS[0], 1)
        self.assertGreaterEqual(starts[1] - starts[0], 1.99)

    def test_orphan_dispatch_on_restart_releases_desk_without_replay(self):
        registry = self.root / 'desks.json'; registry.write_text('{"desks":{}}')
        os.environ['CARR_HERMES_DESKS'] = str(registry)
        brief = self.root / 'orphan.txt'; brief.write_text('task')
        with self.q.db:
            self.q.db.execute('INSERT INTO actions(key,kind,repo,pr,head,payload,phase,desk) VALUES(?,?,?,?,?,?,?,?)',
                              ('orphan', 'dispatch', module.REPOS[0], 1, self.approved, str(brief), 'issued', 'sol'))
        self.restart(); self.q.dispatch_conflicts()
        self.assertEqual(self.q.db.execute('SELECT phase,desk FROM actions').fetchone()[:], ('uncertain', None))
        self.assertIsNotNone(self.q.db.execute("SELECT 1 FROM events WHERE outcome='dispatch_uncertain'").fetchone())

    def test_changed_stack_base_is_not_overwritten_during_refresh(self):
        self.pr(n=2, base='branch-1', draft=True)
        read = self.q.pr
        reads = 0
        def move_base(*args):
            nonlocal reads
            reads += 1
            if reads == 2:
                self.load(); self.data['prs'][module.REPOS[0]+'#2']['base']['ref'] = 'other-unmerged'; self.save()
            return read(*args)
        with patch.object(self.q, 'pr', side_effect=move_base):
            self.q.refresh(module.REPOS[0], 'branch-1')
        self.assertEqual(self.calls('edit'), [])
        self.assertEqual(self.load()['prs'][module.REPOS[0]+'#2']['base']['ref'], 'other-unmerged')

    def test_upgrade_does_not_replace_an_issued_retarget_intent(self):
        self.pr(n=2, base='branch-1')
        key = f'retarget:{module.REPOS[0]}:2:{self.approved}:main'
        with self.q.db:
            self.q.db.execute('INSERT INTO actions(key,kind,repo,pr,head,payload,phase) VALUES(?,?,?,?,?,?,?)',
                              (key, 'retarget', module.REPOS[0], 2, self.approved, 'main', 'issued'))
        self.restart()
        self.q.refresh(module.REPOS[0], 'branch-1')
        self.assertEqual(self.calls('edit'), [])
        self.assertEqual(self.q.db.execute('SELECT COUNT(*) FROM actions').fetchone()[0], 1)

    def test_ci_wait_deadline_survives_restart(self):
        self.pr(); self.data['required'] = [{'bucket':'pending'}]; self.save()
        self.q.enqueue(module.REPOS[0], 1, self.approved)
        now = time.time()
        with patch.object(module.time, 'time', return_value=now): self.q.tick()
        self.restart()
        with patch.object(module.time, 'time', return_value=now + 75*60): self.q.tick()
        self.assertEqual(self.q.db.execute('SELECT phase,outcome FROM entries').fetchone()[:],
                         ('exhausted', 'ci_timeout'))
        self.assertEqual(self.calls('merge'), [])

    def test_dispatch_cancelled_after_bridge_preflight_never_spawns(self):
        self.pr()
        brief = self.root / 'cancelled.txt'; brief.write_text('task')
        registry = self.root / 'desks.json'
        registry.write_text(json.dumps({'desks': {'sol': {
            'kind':'codex-session', 'model':'gpt-6.1-sol', 'effort':'high',
            'sandbox':'workspace-write', 'last_auth':True, 'thread_id':None}}}))
        os.environ['CARR_HERMES_DESKS'] = str(registry)
        with self.q.db:
            self.q.db.execute('INSERT INTO actions(key,kind,repo,pr,head,payload) VALUES(?,?,?,?,?,?)',
                              ('cancel', 'dispatch', module.REPOS[0], 1, self.approved, str(brief)))
        real_command = module.command
        def cancel(argv, **kwargs):
            if '--help' in argv:
                self.q.stopped = True
                return 'send --fresh'
            return real_command(argv, **kwargs)
        with patch.object(module, 'command', side_effect=cancel): self.q.dispatch_conflicts()
        self.assertEqual(self.q.db.execute('SELECT phase,desk FROM actions').fetchone()[:], ('planned', None))
        self.assertEqual(self.q.children, {})

    def test_restart_adopts_live_dispatcher_identity_but_rejects_pid_reuse(self):
        registry = self.root / 'desks.json'; registry.write_text('{"desks":{}}')
        os.environ['CARR_HERMES_DESKS'] = str(registry)
        brief = self.root / 'live.txt'; brief.write_text('task')
        receipt = brief.with_suffix('.dispatch.jsonl')
        identity = f'4242 Thu Oct 8 12:00:00 2026 python {self.root}/tools/room-bridge/dispatch.py --results {receipt} send sol'
        ps = self.root / 'bin/ps'; ps.write_text('#!/bin/sh\necho "'+identity+'"\n'); ps.chmod(0o755)
        with self.q.db:
            self.q.db.execute('INSERT INTO actions(key,kind,repo,pr,head,payload,phase,desk,dispatcher_pid,dispatcher_identity,receipt) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                ('live', 'dispatch', module.REPOS[0], 1, self.approved, str(brief), 'issued', 'sol',
                 4242, module.hashlib.sha256(identity.encode()).hexdigest(), str(receipt)))
        self.restart(); self.q.dispatch_conflicts()
        self.assertEqual(self.q.db.execute('SELECT phase,desk FROM actions').fetchone()[:], ('issued', 'sol'))
        ps.write_text('#!/bin/sh\necho "4242 unrelated-process"\n')
        self.restart(); self.q.dispatch_conflicts()
        self.assertEqual(self.q.db.execute('SELECT phase,desk FROM actions').fetchone()[:], ('uncertain', None))

    def test_cancellation_while_waiting_for_call_slot_leaves_update_unissued(self):
        self.pr(state='behind')
        self.q.enqueue(module.REPOS[0], 1, self.approved)
        original_sleep = module.time.sleep
        def cancel(seconds):
            self.q.stopped = True
            original_sleep(0)
        real_gh = self.q.gh
        def delay_update(*args, **kwargs):
            if 'update-branch' in str(args): self.q.last_gh = time.monotonic() + 1
            return real_gh(*args, **kwargs)
        with patch.object(self.q, 'gh', side_effect=delay_update), patch.object(module.time, 'sleep', side_effect=cancel):
            self.q.tick()
        self.assertEqual(self.q.db.execute('SELECT phase,attempts FROM actions').fetchone()[:], ('planned', 0))
        self.assertFalse(any('update-branch' in str(a) for a in self.load()['calls']))

    def test_transient_read_failures_have_three_retries(self):
        self.pr(); self.q.enqueue(module.REPOS[0], 1, self.approved)
        for _ in range(8):
            with patch.object(self.q, 'pr', side_effect=RuntimeError('gh api failed (exit 1)')):
                self.q.tick()
            self.restart()
        self.assertEqual(self.q.db.execute('SELECT phase,outcome FROM entries').fetchone()[:],
                         ('exhausted', 'retry_exhausted'))

    def test_issued_merge_observation_exhausts_without_losing_intent(self):
        self.pr(); self.q.enqueue(module.REPOS[0], 1, self.approved)
        with self.q.db:
            self.q.db.execute("UPDATE entries SET phase='merging',tested=?,merge_attempts=1", (self.approved,))
        reads = 0
        read = self.q.pr
        def fail(*args):
            nonlocal reads
            reads += 1
            read(*args)
            raise RuntimeError('provider observation failed')
        with patch.object(self.q, 'pr', side_effect=fail):
            for _ in range(8): self.q.tick()
        self.assertEqual(reads, 4)
        self.assertEqual(self.q.db.execute('SELECT phase,tested,outcome FROM entries').fetchone()[:],
                         ('exhausted', self.approved, 'retry_exhausted'))
        self.q.reconcile(1)
        self.assertEqual(self.calls('merge'), [])

    def test_auto_enqueue_reactivation_is_capped_at_three(self):
        self.pr()
        for _ in range(8):
            self.q.discover()
            with self.q.db: self.q.db.execute("UPDATE entries SET phase='review'")
            self.restart()
        self.q.discover()
        self.assertEqual(self.q.db.execute('SELECT phase FROM entries').fetchone()[0], 'review')
        self.assertIsNotNone(self.q.db.execute("SELECT 1 FROM events WHERE outcome='auto_enqueue_exhausted'").fetchone())

    def test_approved_head_mismatch_requires_fresh_review(self):
        self.pr(head=self.changed)
        self.q.enqueue(module.REPOS[0], 1, self.approved)
        self.q.tick()
        self.assertEqual(self.cli('status').returncode, 0)
        self.assertEqual(self.q.db.execute('SELECT phase FROM entries').fetchone()[0], 'review')
        self.assertEqual(self.calls('merge'), [])

    def test_behind_updates_once_then_retests_equivalent_patch(self):
        self.pr(state='behind')
        self.q.enqueue(module.REPOS[0], 1, self.approved)
        self.q.tick()
        self.q.tick()
        self.assertEqual(len(self.calls('merge')), 1)
        self.assertEqual(self.calls('merge')[0][-1], self.updated)
        posts=[a for a in self.load()['calls'] if any(x.startswith('body=APPROVE') for x in a)]
        self.assertIn('Reviewed-SHA: '+self.updated, next(x for x in posts[0] if x.startswith('body=')))

    def test_conflicting_writes_brief_without_resolving(self):
        (self.remote/'file.txt').write_text('main conflict\n')
        self.git('commit','-qam','conflicting main')
        self.main=self.git('rev-parse','HEAD').strip()
        self.pr(state='dirty', head=self.changed)
        self.data['prs'][module.REPOS[0]+'#1']['base']['sha']=self.main
        self.save()
        self.q.refresh(module.REPOS[0], 'just-merged')
        briefs=list((self.root/'out/orch/queue/codex').glob('*.txt'))
        self.assertEqual(len(briefs), 1)
        self.assertIn('file.txt', briefs[0].read_text())
        self.assertIn(self.changed, briefs[0].read_text())
        self.assertEqual(self.calls('merge'), [])

    def test_held_is_skipped_by_queue_discover_and_refresh(self):
        self.pr(state='behind', held=True)
        self.q.enqueue(module.REPOS[0], 1, self.approved)
        self.q.tick()
        self.q.discover()
        self.q.refresh(module.REPOS[0], 'just-merged')
        self.assertEqual(self.calls('merge'), [])
        self.assertFalse(any('update-branch' in str(a) for a in self.load()['calls']))
        self.assertEqual(self.q.db.execute('SELECT outcome FROM entries').fetchone()[0], 'held')

    def test_stacked_draft_retargets_and_updates_after_parent_merge(self):
        self.pr(n=2, base='branch-1', draft=True)
        self.q.refresh(module.REPOS[0], 'branch-1')
        self.assertEqual(len(self.calls('edit')), 1)
        self.assertEqual(self.load()['prs'][module.REPOS[0]+'#2']['base']['ref'], 'main')
        self.assertTrue(any('update-branch' in str(a) for a in self.load()['calls']))
        self.assertTrue(self.data['prs'][module.REPOS[0]+'#2']['draft'])

    def test_crash_after_merge_reconciles_main_and_refreshes(self):
        self.pr()
        self.q.enqueue(module.REPOS[0], 1, self.approved)
        self.q.tick()
        self.q.db.close()
        self.q=module.Queue(self.state,self.root,gap=0)
        self.addCleanup(self.q.db.close)
        self.q.tick()
        self.assertEqual(len(self.calls('merge')), 1)
        self.assertEqual(self.q.db.execute('SELECT phase FROM entries').fetchone()[0], 'done')
        self.assertIn('confirmed', (self.q.db.execute("SELECT detail FROM events WHERE outcome='merged' ORDER BY id DESC").fetchone()[0]))

    def test_board_failure_does_not_stop_merges_and_is_logged(self):
        os.environ['BOARD_FAIL']='1'
        self.pr()
        self.q.enqueue(module.REPOS[0],1,self.approved)
        self.q.flush_events()
        self.q.tick()
        self.q.tick()
        self.assertEqual(len(self.calls('merge')),1)
        self.assertIn('progress_board_write_failed',(self.state/'queue.log').read_text())
        self.assertEqual(self.q.db.execute('SELECT phase FROM entries').fetchone()[0],'done')

    def test_fifo_across_repositories(self):
        self.pr(n=2, repo=module.REPOS[1])
        self.pr(n=1)
        self.q.enqueue(module.REPOS[1],2,self.approved)
        self.q.enqueue(module.REPOS[0],1,self.approved)
        self.q.tick()
        self.assertEqual(self.calls('merge')[0][2:5], ['2','-R',module.REPOS[1]])

    def test_no_checks_and_required_red_never_merge(self):
        self.pr()
        self.data['runs']=[];self.save()
        self.q.enqueue(module.REPOS[0],1,self.approved)
        self.q.tick()
        self.assertEqual(self.calls('merge'),[])
        self.load(); self.data['runs']=[{'id':1,'name':'CI','app':{'id':1},'status':'completed','conclusion':'success'}]
        self.data['required']=[{'bucket':'fail'}];self.save()
        self.q.tick()
        self.assertEqual(self.calls('merge'),[])

    def test_queued_approval_cannot_authorize_itself(self):
        self.pr(head=self.updated)
        self.data['comments'][module.REPOS[0]+'#1']=[{'body':f'APPROVE\nReviewed-SHA: {self.approved}\n\nOrchestrator merge queue: independent approval of somebody.', 'author_association':'OWNER'}]
        self.save()
        self.q.enqueue(module.REPOS[0],1,self.approved)
        self.q.tick()
        self.assertEqual(self.calls('merge'),[])

    def test_later_release_block_marker_and_malformed_header_require_review(self):
        for body in ('REVIEW: BLOCKED\nReviewed-SHA: '+self.approved,
                     'APPROVE\nExample\nReviewed-SHA: '+self.approved,
                     f'APPROVE\nReviewed-SHA: {self.approved}\nReviewed-SHA: {self.changed}'):
            with self.subTest(body=body):
                self.pr()
                self.data['comments'][module.REPOS[0]+'#1'].append({'body':body,'author_association':'OWNER'})
                self.save()
                self.assertIsNone(self.q.approval(module.REPOS[0],1))

    def test_git_locations_from_a_calling_hook_cannot_redirect_private_repo(self):
        with patch.dict(os.environ, {'GIT_DIR': str(self.root/'wrong.git'), 'GIT_WORK_TREE': str(self.root/'wrong')}):
            self.assertEqual(self.q.git(module.REPOS[0], 'rev-parse', 'origin/main').strip(), self.main)

    def test_exited_dispatch_without_receipt_is_logged_and_not_repeated(self):
        registry=self.root/'desks.json';registry.write_text('{"desks":{}}')
        os.environ['CARR_HERMES_DESKS']=str(registry)
        brief=self.root/'brief.txt';brief.write_text('brief')
        with self.q.db:
            self.q.db.execute('INSERT INTO actions(key,kind,repo,pr,head,payload,phase,desk) VALUES(?,?,?,?,?,?,?,?)',('exited','dispatch',module.REPOS[0],1,self.approved,str(brief),'issued','sol'))
        child=subprocess.Popen([sys.executable,'-c','pass'])
        child.wait();self.q.children['exited']=child
        self.q.dispatch_conflicts()
        self.assertEqual(self.q.db.execute("SELECT phase FROM actions WHERE key='exited'").fetchone()[0], 'uncertain')
        self.assertIsNotNone(self.q.db.execute("SELECT 1 FROM events WHERE outcome='dispatch_uncertain'").fetchone())

    def test_fresh_review_can_reactivate_the_same_exact_head(self):
        self.pr();self.data['comments'][module.REPOS[0]+'#1'].append({'body':'REVIEW: BLOCKED','author_association':'OWNER'});self.save()
        self.q.enqueue(module.REPOS[0],1,self.approved);self.q.tick()
        self.load();self.data['comments'][module.REPOS[0]+'#1'].append({'body':f'APPROVE\nReviewed-SHA: {self.approved}','author_association':'OWNER'});self.save()
        self.q.discover();self.q.tick()
        self.assertEqual(len(self.calls('merge')),1)

    def test_legacy_queue_stamp_is_not_an_independent_review(self):
        self.pr()
        self.data['comments'][module.REPOS[0]+'#1']=[{'body':f'APPROVE\nReviewed-SHA: {self.approved}\nOrchestrator: verified exact head and green.', 'author_association':'OWNER'}]
        self.save()
        self.q.enqueue(module.REPOS[0],1,self.approved)
        self.q.tick()
        self.assertEqual(self.calls('merge'),[])

    def test_interrupted_update_does_not_blindly_repeat(self):
        self.pr(state='behind')
        self.data['update_async']=True;self.save()
        self.q.enqueue(module.REPOS[0],1,self.approved)
        self.q.tick()
        self.q.db.close()
        self.q=module.Queue(self.state,self.root,gap=0);self.addCleanup(self.q.db.close)
        self.q.tick()
        updates=[a for a in self.load()['calls'] if 'update-branch' in str(a)]
        self.assertEqual(len(updates),1)
        self.assertEqual(self.calls('merge'),[])

    def test_rejected_update_can_retry_after_fresh_readback(self):
        self.pr(state='behind');self.data['update_reject']=True;self.save()
        self.q.enqueue(module.REPOS[0],1,self.approved);self.q.tick()
        self.load();self.data['update_reject']=False;self.save();self.q.tick();self.q.tick()
        self.assertEqual(len(self.calls('merge')),1)

    def test_operator_can_reconcile_interrupted_update(self):
        self.pr(state='behind');self.data['update_async']=True;self.save()
        self.q.enqueue(module.REPOS[0],1,self.approved);self.q.tick()
        key=self.q.db.execute("SELECT key FROM actions WHERE kind='update'").fetchone()[0]
        self.q.reconcile_action(key,retry=False)
        self.assertEqual(self.q.db.execute('SELECT phase FROM actions').fetchone()[0],'issued')
        self.q.reconcile_action(key,retry=True)
        self.assertEqual(self.q.db.execute('SELECT phase FROM actions').fetchone()[0],'planned')

    def test_partial_dispatch_receipt_does_not_block_other_briefs(self):
        self.pr(1);self.pr(2)
        registry=self.root/'desks.json';registry.write_text(json.dumps({'desks':{}}));os.environ['CARR_HERMES_DESKS']=str(registry)
        for n,phase in ((1,'issued'),(2,'planned')):
            brief=self.root/f'conflict{n}.txt';brief.write_text('task')
            with self.q.db:
                self.q.db.execute('INSERT INTO actions(key,kind,repo,pr,head,payload,phase,desk) VALUES(?,?,?,?,?,?,?,?)',(f'a{n}','dispatch',module.REPOS[0],n,self.approved,str(brief),phase,'busy-sol' if n==1 else None))
        (self.root/'conflict1.dispatch.jsonl').write_text('{"status":')
        self.q.dispatch_conflicts()
        self.assertIsNotNone(self.q.db.execute("SELECT 1 FROM events WHERE pr=2 AND outcome='dispatch_waiting'").fetchone())

    def test_board_failure_retries_and_all_outcomes_are_logged(self):
        self.pr()
        self.q.enqueue(module.REPOS[0],1,self.approved)
        self.q.tick();self.q.tick()
        os.environ['BOARD_FAIL']='1';self.q.flush_events()
        log=(self.state/'queue.log').read_text()
        self.assertIn('merge_requested',log)
        self.assertIn('post-merge refresh complete',log)
        self.assertGreater(self.q.db.execute('SELECT COUNT(*) FROM events WHERE published=0').fetchone()[0],0)
        os.environ.pop('BOARD_FAIL');self.q.flush_events()
        self.assertEqual(self.q.db.execute('SELECT COUNT(*) FROM events WHERE published=0').fetchone()[0],0)

    def test_dispatch_waits_for_free_sol_desk_then_records_result(self):
        self.pr()
        brief=self.root/'conflict.txt';brief.write_text('bounded task')
        with self.q.db:
            self.q.db.execute('INSERT INTO actions(key,kind,repo,pr,head,payload) VALUES(?,?,?,?,?,?)',('test','dispatch',module.REPOS[0],1,self.approved,str(brief)))
        registry=self.root/'desks.json'
        desk={'kind':'codex-session','model':'gpt-6.1-sol','effort':'high','sandbox':'workspace-write','last_auth':True,'thread_id':None,'busy':True}
        registry.write_text(json.dumps({'desks':{'free-sol':desk}}));os.environ['CARR_HERMES_DESKS']=str(registry)
        bridge=self.root/'tools/room-bridge';bridge.mkdir()
        (bridge/'dispatch.py').write_text("import json,sys\nfrom pathlib import Path\n"
            "a=sys.argv[1:]\nif '--help' in a: print('send --fresh');sys.exit()\n"
            "assert '--family' not in a\n"
            "Path(a[a.index('--results')+1]).write_text(json.dumps({'desk':'free-sol','kind':'codex-session','status':'completed','thread_id':'new-thread','result':'fixed'})+'\\n')\n")
        self.q.dispatch_conflicts()
        self.assertEqual(self.q.db.execute('SELECT phase FROM actions').fetchone()[0],'planned')
        desk['busy']=False;registry.write_text(json.dumps({'desks':{'free-sol':desk}}))
        self.q.dispatch_conflicts()
        import time
        for _ in range(50):
            time.sleep(.01)
            self.q.dispatch_conflicts()
            if self.q.db.execute('SELECT phase FROM actions').fetchone()[0]=='done': break
        self.assertEqual(self.q.db.execute('SELECT phase FROM actions').fetchone()[0],'done')
        self.assertTrue(brief.with_suffix('.dispatch.jsonl').exists())

    def test_partial_legacy_append_does_not_stall_live_queue(self):
        legacy=self.root/'legacy';legacy.mkdir()
        (legacy/'merge-queue.txt').write_text(f'{module.REPOS[0]} 1 {self.approved} okay\n' + module.REPOS[1]+' 2 ')
        self.q.import_legacy(legacy)
        self.assertEqual(self.q.db.execute('SELECT COUNT(*) FROM entries').fetchone()[0],1)

    def test_launchd_is_only_installed_on_primary_machine(self):
        sys.path.insert(0,str(ROOT))
        from lib.launchd_scope import allowed_on_machine
        self.assertFalse(allowed_on_machine('com.carr.merge-queue.plist',primary=False))

    def test_merge_commit_must_be_on_main(self):
        self.pr()
        self.data['prs'][module.REPOS[0]+'#1'].update(merged=True,state='closed',merge_commit_sha=self.changed);self.save()
        self.q.enqueue(module.REPOS[0],1,self.approved)
        self.q.tick()
        self.assertEqual(self.q.db.execute('SELECT phase FROM entries').fetchone()[0],'pending')

    def test_definite_merge_rejection_rearms_only_after_provider_readback(self):
        self.pr();self.data['merge_reject']=True;self.save()
        self.q.enqueue(module.REPOS[0],1,self.approved);self.q.tick()
        self.load();self.data['merge_reject']=False;self.save()
        self.q.tick();self.q.tick()
        self.assertTrue(self.load()['prs'][module.REPOS[0]+'#1']['merged'])

    def test_queued_server_merge_is_never_reissued(self):
        self.pr();self.q.enqueue(module.REPOS[0],1,self.approved)
        with self.q.db:
            self.q.db.execute("UPDATE entries SET phase='merge_rejected',tested=?",(self.approved,))
        self.data['server_queued']=True;self.save();self.q.tick();self.q.tick()
        self.assertEqual(self.calls('merge'),[])

    def test_operator_reconcile_requires_unchanged_head_and_no_server_intent(self):
        self.pr();self.q.enqueue(module.REPOS[0],1,self.approved)
        with self.q.db:
            self.q.db.execute("UPDATE entries SET phase='merging',tested=?",(self.approved,))
        self.q.reconcile(1,retry=False)
        self.assertEqual(self.q.db.execute('SELECT phase FROM entries').fetchone()[0],'merging')
        self.data['server_queued']=True;self.save();self.q.reconcile(1,retry=True)
        self.assertEqual(self.q.db.execute('SELECT phase FROM entries').fetchone()[0],'merging')
        self.load();self.data['server_queued']=False;self.save();self.q.reconcile(1,retry=True)
        self.assertEqual(self.q.db.execute('SELECT phase FROM entries').fetchone()[0],'pending')

    def test_imported_completed_merge_reconciles_without_replaying_refresh(self):
        self.pr();self.pr(2)
        self.data['prs'][module.REPOS[0]+'#1'].update(merged=True,state='closed',merge_commit_sha=self.merge_sha);self.save()
        self.git('update-ref','refs/heads/main',self.merge_sha)
        self.q.enqueue(module.REPOS[0],1,self.approved);self.q.enqueue(module.REPOS[0],2,self.approved)
        self.q.tick()
        self.assertEqual(self.calls('merge')[0][2],'2')
        self.assertEqual(self.q.db.execute('SELECT phase FROM entries WHERE id=1').fetchone()[0],'done')

    def test_successful_archive_keeps_inputs_and_redirects_enqueue(self):
        self.pr();self.q.enqueue(module.REPOS[0],1,self.approved);self.q.tick();self.q.tick()
        legacy=self.root/'legacy';legacy.mkdir()
        (legacy/'merge-queue.txt').write_text(f'{module.REPOS[1]} 2 {self.approved} late append\n')
        (legacy/'merge-one.sh').write_text('old')
        self.q.archive_legacy(legacy)
        self.assertTrue((legacy/'_to_delete/merge-one.sh').exists())
        self.assertTrue((legacy/'merge-queue.txt').exists())
        self.assertIn('tools/merge_queue/main.py',(legacy/'merge-enqueue.sh').read_text())
        self.assertEqual(self.q.db.execute('SELECT COUNT(*) FROM entries').fetchone()[0],2)


    def cli(self, *args):
        return subprocess.run([sys.executable, str(ENTRY), '--state', str(self.state), *args],
                              text=True, capture_output=True)

    def test_legacy_import_recovers_attempted_entries_ignoring_pointers(self):
        legacy=self.root/'legacy';legacy.mkdir()
        line=f'{module.REPOS[0]} 1 {self.approved} old note\n'
        (legacy/'merge-queue.txt').write_text(line+line+f'{module.REPOS[1]} 2 {self.approved} second\n')
        (legacy/'merge-queue.done').write_text(f'{module.REPOS[1]} 2 {self.approved} attempted\n'+line)
        (legacy/'merge-queue.carr-system.ptr').write_text('99')
        self.q.import_legacy(legacy)
        self.q.import_legacy(legacy)
        rows=self.q.db.execute('SELECT repo,pr,phase FROM entries ORDER BY id').fetchall()
        self.assertEqual([tuple(r) for r in rows],[(module.REPOS[0],1,'pending'),(module.REPOS[1],2,'pending')])
        self.assertTrue((self.state/'legacy-import.json').exists())

    def test_outdated_draft_with_blocked_status_is_refreshed(self):
        self.pr(state='blocked', draft=True)
        self.q.refresh(module.REPOS[0], 'just-merged')
        self.assertTrue(any('update-branch' in str(a) for a in self.load()['calls']))

    def test_unknown_pr_does_not_prevent_other_postmerge_updates(self):
        self.pr(1,state='unknown');self.pr(2,state='behind')
        self.q.refresh(module.REPOS[0], 'just-merged')
        self.assertTrue(any('/pulls/2/update-branch' in str(a) for a in self.load()['calls']))

    def test_single_running_agent_owns_the_state(self):
        p=subprocess.Popen([sys.executable,str(ENTRY),'--state',str(self.state),'run','--no-discover','--poll','0.05'])
        try:
            import time
            time.sleep(.2)
            result=self.cli('run','--no-discover','--poll','0.05')
            self.assertNotEqual(result.returncode,0)
            self.assertIsNone(p.poll())
        finally:
            p.terminate();p.wait(timeout=5)

    def test_handover_refuses_to_start_without_hold_migration_receipt(self):
        legacy=self.root/'legacy';legacy.mkdir()
        with self.assertRaises(RuntimeError):
            self.q.run(poll=.01,discover=False,legacy=legacy)

    def test_install_plan_renders_template_without_activating_agent(self):
        self.q.root=ROOT
        target=self.root/'LaunchAgents/com.carr.merge-queue.plist'
        self.q.install_agent(target,apply=False)
        import plistlib
        p=plistlib.loads(target.read_bytes())
        self.assertEqual(p['WorkingDirectory'],str(ROOT))
        self.assertNotIn('{{REPO}}',target.read_text())

    def test_hold_migration_preserves_registry_allowlist_and_titles(self):
        legacy=self.root/'legacy';legacy.mkdir()
        (legacy/'merge-holds.txt').write_text(f'{module.REPOS[1]} Leads waits for cleanup\n')
        (legacy/'registry-merge-hold.txt').write_text('1499\n1550\n1481\n')
        self.pr(1);self.pr(1499);self.pr(3,repo=module.REPOS[1]);self.pr(4,repo=module.REPOS[1])
        for n in (1,1499): self.data['prs'][module.REPOS[0]+f'#{n}']['files']=[{'filename':'migrations/100.sql'}]
        self.data['prs'][module.REPOS[1]+'#3']['title']='Leads workspace'
        self.data['prs'][module.REPOS[1]+'#4']['title']='please do_not_merge this'
        self.save()
        plan=self.q.migrate_holds(legacy,apply=False)
        self.assertEqual({(r['repo'],r['pr']) for r in plan},{(module.REPOS[0],1),(module.REPOS[1],3),(module.REPOS[1],4)})
        self.q.migrate_holds(legacy,apply=True)
        self.assertTrue(self.load()['prs'][module.REPOS[0]+'#1']['labels'])
        self.assertFalse(self.data['prs'][module.REPOS[0]+'#1499']['labels'])

    def test_archive_refuses_without_confirmed_new_agent_merge(self):
        legacy=self.root/'legacy';legacy.mkdir()
        (legacy/'merge-one.sh').write_text('old')
        with self.assertRaises(RuntimeError): self.q.archive_legacy(legacy)
        self.assertTrue((legacy/'merge-one.sh').exists())

    def test_launchd_keepalive_has_throttle_and_no_interval_restart(self):
        import plistlib
        p=plistlib.loads((ROOT/'ops/launchd/com.carr.merge-queue.plist').read_bytes())
        self.assertTrue(p['KeepAlive'])
        self.assertGreaterEqual(p['ThrottleInterval'],30)
        self.assertNotIn('StartInterval',p)
        self.assertIn('run',p['ProgramArguments'])

    def test_empty_queue_idles_without_exiting(self):
        p = subprocess.Popen([sys.executable, str(ENTRY), '--state', str(self.state),
                              'run', '--poll', '0.05', '--no-discover'])
        try:
            import time
            time.sleep(.2)
            self.assertIsNone(p.poll(), 'an empty queue must remain alive')
        finally:
            p.terminate()
            p.wait(timeout=5)

if __name__ == '__main__':
    unittest.main()
