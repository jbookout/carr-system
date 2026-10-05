#!/usr/bin/env python3
"""Replay ordered seals using the real Git, allocator, generator guard and DB adapter."""
import importlib.util
import io
from contextlib import redirect_stdout, redirect_stderr
import json
import os
import subprocess
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(REPO/'tools'))
import integration_candidate as integration
from migration_number_contract import MigrationNumberError

class CandidateTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='integration-candidate-')
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name); self.repo=self.root/'repo'; self.repo.mkdir()
        from git_env import fixture_env
        self.env=fixture_env()
        self.g('init','-q'); self.g('config','user.name','Fixture'); self.g('config','user.email','fixture@example.invalid')
        (self.repo/'migrations').mkdir(); (self.repo/'mcp-server/src').mkdir(parents=True)
        (self.repo/'db').mkdir(); (self.repo/'db/schema.sql').write_text('-- fixture restore\n')
        self.write('migrations/0748_base.sql','select 1;')
        self.registry(97)
        self.commit(); self.base=self.g('rev-parse','HEAD')
        self.g('update-ref','refs/remotes/origin/main',self.base)
        self.receipt=self.root/'receipt.json'
        self.generator=self.root/'generator.py'
        self.generator.write_text('''import json,os,pathlib
p=json.loads(os.environ['CARR_INTEGRATION_ALLOCATION']);root=pathlib.Path('.')
count=pathlib.Path(__file__).parent/'integration-render-count'
count.write_text(str(int(count.read_text())+1) if count.exists() else '1')
drafts={old:(root/'migrations'/old).read_text() for old in p['migration_names']}
for old in drafts: (root/'migrations'/old).unlink()
for old,new in p['migration_names'].items(): (root/'migrations'/new).write_text(drafts[old])
v=p['registry_successor']
(root/f'mcp-server/src/scac-mutation-registry.v{v}.generated.js').write_text(f'export const SCAC_MUTATION_REGISTRY_VERSION = "scac-mutation-registry.v{v}";\\n')
''')
    def g(self,*args):
        return subprocess.check_output(['git',*args],cwd=self.repo,env=self.env,text=True,stderr=subprocess.DEVNULL).strip()
    def write(self,p,value): (self.repo/p).write_text(value)
    def registry(self,v): self.write(f'mcp-server/src/scac-mutation-registry.v{v}.generated.js',f'export const SCAC_MUTATION_REGISTRY_VERSION = "scac-mutation-registry.v{v}";\n')
    def commit(self):
        self.g('add','migrations','mcp-server','db'); self.g('commit','-qm','Fixture source')
    def render(self,pending,argv=None):
        with patch.dict(os.environ,{'CANARY_TOKEN':'private-canary-123'}):
            return integration.regenerate_once(self.repo,self.base,pending,argv or [sys.executable,str(self.generator)],self.receipt)
    def test_same_number_and_version_contenders_render_once_in_order(self):
        self.write('migrations/0749_first.sql','select 2;')
        first=self.render(['0749_first.sql']); self.assertEqual(first['allocation']['registry_successor'],98)
        # Retry authenticates outputs, never reexecutes the generator.
        self.assertEqual(self.render(['0749_first.sql']),first)
        self.assertEqual((self.root/'integration-render-count').read_text(),'1')
        self.commit(); self.base=self.g('rev-parse','HEAD'); self.g('update-ref','refs/remotes/origin/main',self.base)
        sealed=(self.repo/'mcp-server/src/scac-mutation-registry.v98.generated.js').read_bytes()
        self.write('migrations/0749_second.sql','select 3;')
        second=self.render(['0749_second.sql'])
        self.assertEqual(second['allocation']['migration_names'],{'0749_second.sql':'0750_second.sql'})
        self.assertEqual(second['allocation']['registry_predecessor'],98)
        self.assertEqual(second['allocation']['registry_successor'],99)
        self.assertEqual((self.repo/'mcp-server/src/scac-mutation-registry.v98.generated.js').read_bytes(),sealed)
        self.assertEqual((self.root/'integration-render-count').read_text(),'2')
        self.commit(); self.assertEqual(integration.validate_candidate(self.repo,self.base)['pending_migrations'],['0750_second.sql'])
    def test_actual_generator_sink_rejects_reseal_and_wrong_successor(self):
        target=self.repo/'mcp-server/src/scac-mutation-registry.v97.generated.js'
        integration.check_generated_write(self.repo,target,target.read_bytes(),self.base)
        for path,data in [(target,b'edited'),(self.repo/'mcp-server/src/scac-mutation-registry.v99.generated.js',b'wrong')]:
            with self.assertRaises(MigrationNumberError): integration.check_generated_write(self.repo,path,data,self.base)
    def test_inventory_write_caller_preserves_sealed_bytes_and_sanitizes_errors(self):
        self.install_sink()
        target=self.repo/'mcp-server/src/scac-mutation-registry.v97.generated.js'
        original=target.read_bytes()
        script="import {writeIntegratedArtifact} from './ops/integration-generation.mjs'; await writeIntegratedArtifact(process.argv[1],process.argv[2]);"
        def call(content):
            return subprocess.run(['node','--input-type=module','-e',script,str(target),content],cwd=self.repo,env=self.env,capture_output=True,text=True)
        self.assertEqual(call(original.decode()).returncode,0)
        refusal=call('private-canary-123')
        self.assertNotEqual(refusal.returncode,0)
        self.assertNotIn('private-canary-123',refusal.stdout+refusal.stderr)
        self.assertEqual(target.read_bytes(),original)

    def test_empty_zero_refusal_nonzero_partial_exception_and_acknowledgement(self):
        for code in ['pass','print("private-canary-123");raise SystemExit(75)', 'raise SystemExit(2)',
                     'open("migrations/0749_only.sql","w").write("partial")','raise Exception("private-canary-123")']:
            self.receipt=self.root/(str(abs(hash(code)))+'.json')
            self.write('migrations/0749_pending.sql','select 2;')
            with self.assertRaises(MigrationNumberError): self.render(['0749_pending.sql'],[sys.executable,'-c',code])
            raw=self.receipt.read_bytes(); self.assertNotIn(b'private-canary-123',raw)
            self.assertEqual(json.loads(raw)['state'],'refused')
            with self.assertRaises(MigrationNumberError): self.render(['0749_pending.sql'],[sys.executable,'-c',code])
            if (self.repo/'migrations/0749_only.sql').exists(): (self.repo/'migrations/0749_only.sql').unlink()
    def install_sink(self):
        (self.repo/'ops').mkdir(exist_ok=True); (self.repo/'tools').mkdir(exist_ok=True)
        for name in ['integration_candidate.py','migration_number_contract.py']:
            shutil.copyfile(REPO/'tools'/name,self.repo/'tools'/name)
        for name in ['git_env.py','integration-generation.mjs']:
            shutil.copyfile(REPO/'ops'/name,self.repo/'ops'/name)
    def sink(self,target_expr,content,env=None):
        script=("import {writeIntegratedArtifact} from './ops/integration-generation.mjs';"
                f"await writeIntegratedArtifact({target_expr},process.argv[1]);")
        return subprocess.run(['node','--input-type=module','-e',script,content],cwd=self.repo,
                              env={**self.env,**(env or {})},capture_output=True,text=True)
    def test_export_targets_outside_canonical_paths_need_no_git_context(self):
        self.install_sink()
        export=self.root/'export'/'migrations'/'0001_historical.sql'; export.parent.mkdir(parents=True)
        # Exports outside the repository render without a current integration base.
        self.g('update-ref','-d','refs/remotes/origin/main')
        result=self.sink(json.dumps(str(export)),'historical bytes')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(export.read_text(),'historical bytes')
    def test_file_url_targets_keep_the_filesystem_writer_contract(self):
        self.install_sink()
        target=self.repo/'mcp-server/src/scac-mutation-registry.v97.generated.js'
        result=self.sink(f'new URL({json.dumps(target.as_uri())})',target.read_text())
        self.assertEqual(result.returncode,0,result.stderr)
        seals=self.repo/'ops/config/seals.json'; seals.parent.mkdir()
        result=self.sink(f'new URL({json.dumps(seals.as_uri())})','{}\n')
        self.assertEqual(result.returncode,0,result.stderr); self.assertEqual(seals.read_text(),'{}\n')
    def test_sink_refuses_while_another_owner_holds_the_generation_lock(self):
        import fcntl
        self.install_sink()
        target=self.repo/'mcp-server/src/scac-mutation-registry.v98.generated.js'
        content='export const SCAC_MUTATION_REGISTRY_VERSION = "scac-mutation-registry.v98";\n'
        with (self.repo/'.git/integration-generation.lock').open('a') as held:
            fcntl.flock(held,fcntl.LOCK_EX|fcntl.LOCK_NB)
            refused=self.sink(json.dumps(str(target)),content)
            forged=self.sink(json.dumps(str(target)),content,{'CARR_INTEGRATION_OWNER':'forged'})
        self.assertNotEqual(refused.returncode,0); self.assertNotEqual(forged.returncode,0)
        self.assertFalse(target.exists())
        self.assertEqual(self.sink(json.dumps(str(target)),content).returncode,0)
        self.assertEqual(target.read_text(),content)
    def test_base_advance_between_check_and_write_cannot_overwrite_a_new_seal(self):
        target=self.repo/'mcp-server/src/scac-mutation-registry.v98.generated.js'
        stale=b'export const SCAC_MUTATION_REGISTRY_VERSION = "scac-mutation-registry.v98";\n// stale\n'
        sealed=b'export const SCAC_MUTATION_REGISTRY_VERSION = "scac-mutation-registry.v98";\n'
        original=integration.check_generated_write
        def advance_after_check(*args):
            original(*args)
            target.write_bytes(sealed); self.commit()
            self.g('update-ref','refs/remotes/origin/main',self.g('rev-parse','HEAD'))
        with patch.object(integration,'check_generated_write',side_effect=advance_after_check):
            with self.assertRaises(MigrationNumberError): integration.write_generated_artifact(self.repo,target,stale)
        self.assertEqual(target.read_bytes(),sealed)
    def test_coordinator_owned_renderer_writes_through_the_real_sink(self):
        self.install_sink()
        self.generator.write_text('''import json,os,pathlib,subprocess
p=json.loads(os.environ['CARR_INTEGRATION_ALLOCATION'])
for old,new in p['migration_names'].items():
 source=pathlib.Path('migrations')/old; data=source.read_text(); source.unlink()
 pathlib.Path('migrations',new).write_text(data)
v=p['registry_successor']
script="import {writeIntegratedArtifact} from './ops/integration-generation.mjs'; await writeIntegratedArtifact(process.argv[1],process.argv[2]);"
subprocess.run(['node','--input-type=module','-e',script,f'mcp-server/src/scac-mutation-registry.v{v}.generated.js',
 f'export const SCAC_MUTATION_REGISTRY_VERSION = "scac-mutation-registry.v{v}";\\n'],check=True)
''')
        self.write('migrations/0749_first.sql','select 2;')
        self.assertEqual(self.render(['0749_first.sql'])['state'],'generated')
    def test_partial_failure_cannot_authorize_a_second_execution(self):
        counter=self.root/'partial-count'
        code=(f'import pathlib;c=pathlib.Path({str(counter)!r});c.write_text(str(int(c.read_text())+1) if c.exists() else "1");'
              'open("migrations/0749_pending.sql","a").write("-- partial");raise SystemExit(2)')
        self.write('migrations/0749_pending.sql','select 2;')
        for _ in range(2):
            with self.assertRaises(MigrationNumberError): self.render(['0749_pending.sql'],[sys.executable,'-c',code])
        self.assertEqual(counter.read_text(),'1')
    def test_timeout_stops_renderer_descendants_before_releasing_ownership(self):
        draft=self.repo/'migrations/0749_pending.sql'
        self.write('migrations/0749_pending.sql','select 2;')
        code=('import subprocess,sys,time;'
              'subprocess.Popen([sys.executable,"-c","import time;time.sleep(0.6);open(\\"migrations/0749_pending.sql\\",\\"a\\").write(\\"late\\")"],'
              'stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);time.sleep(30)')
        with patch.object(integration,'GENERATOR_TIMEOUT_SECONDS',0.2):
            with self.assertRaises(MigrationNumberError): self.render(['0749_pending.sql'],[sys.executable,'-c',code])
        import time; time.sleep(1.0)
        self.assertEqual(draft.read_text(),'select 2;')
    def test_timeout_diagnostics_never_print_renderer_argv(self):
        self.write('migrations/0749_pending.sql','select 2;')
        argv=[sys.executable,'-c','import time;time.sleep(30)','private-canary-123']
        with patch.object(integration,'GENERATOR_TIMEOUT_SECONDS',0.2):
            with self.assertRaises(MigrationNumberError) as raised: self.render(['0749_pending.sql'],argv)
        self.assertNotIn('private-canary-123',str(raised.exception))
        output=io.StringIO()
        injected=subprocess.TimeoutExpired(argv,300,output=b'private-canary-123',stderr=b'private-canary-123')
        with patch.object(integration,'regenerate_once',side_effect=injected),redirect_stdout(output),redirect_stderr(output):
            code=integration.main(['--base','a'*40,'--regenerate',json.dumps(argv),'--receipt',str(self.receipt)])
        self.assertEqual(code,78)
        self.assertNotIn('private-canary-123',output.getvalue())
    def test_renderer_that_moves_head_cannot_attest_generation(self):
        self.write('migrations/0749_first.sql','select 2;')
        code=(self.generator.read_text()+
              "import subprocess\nsubprocess.run(['git','add','-A'],check=True)\nsubprocess.run(['git','commit','-qm','renderer commit'],check=True)\n")
        self.generator.write_text(code)
        with patch.dict(os.environ,{'GIT_AUTHOR_NAME':'F','GIT_AUTHOR_EMAIL':'f@example.invalid','GIT_COMMITTER_NAME':'F','GIT_COMMITTER_EMAIL':'f@example.invalid'}):
            with self.assertRaises(MigrationNumberError): self.render(['0749_first.sql'])
        self.assertEqual(json.loads(self.receipt.read_text())['state'],'refused')
    def main_at_0749(self):
        self.write('migrations/0749_main.sql','select 0;'); self.commit()
        self.base=self.g('rev-parse','HEAD'); self.g('update-ref','refs/remotes/origin/main',self.base)
    def test_overlapping_allocation_outputs_are_not_obsolete_drafts(self):
        self.main_at_0749()
        self.write('migrations/0749_same.sql','select 49;'); self.write('migrations/0750_same.sql','select 50;')
        result=self.render(['0749_same.sql','0750_same.sql'])
        self.assertEqual(result['allocation']['migration_names'],{'0749_same.sql':'0750_same.sql','0750_same.sql':'0751_same.sql'})
        self.assertEqual((self.repo/'migrations/0750_same.sql').read_text(),'select 49;')
        self.assertEqual((self.repo/'migrations/0751_same.sql').read_text(),'select 50;')
    def test_overlapping_output_left_with_another_inputs_bytes_is_refused(self):
        self.main_at_0749()
        self.write('migrations/0749_same.sql','select 49;'); self.write('migrations/0750_same.sql','select 50;')
        code=('import json,os,pathlib;p=json.loads(os.environ["CARR_INTEGRATION_ALLOCATION"]);'
              'pathlib.Path("migrations/0751_same.sql").write_text(pathlib.Path("migrations/0750_same.sql").read_text());'
              'pathlib.Path("migrations/0749_same.sql").unlink();v=p["registry_successor"];'
              'pathlib.Path(f"mcp-server/src/scac-mutation-registry.v{v}.generated.js").write_text('
              'f\'export const SCAC_MUTATION_REGISTRY_VERSION = "scac-mutation-registry.v{v}";\\n\')')
        with self.assertRaises(MigrationNumberError): self.render(['0749_same.sql','0750_same.sql'],[sys.executable,'-c',code])
    def test_poisoned_git_environment_cannot_move_the_bound_repository(self):
        with patch.dict(os.environ,{'GIT_DIR':'/nonexistent-poison','GIT_INDEX_FILE':'/nonexistent-index'}):
            self.assertEqual(integration.allocation_plan(self.repo,self.base,['0749_pending.sql'])['base'],self.base)

    def test_wrong_base_dirty_proof_and_interrupted_receipt_refuse(self):
        with self.assertRaises(MigrationNumberError): integration.validate_candidate(self.repo,'f'*40)
        self.write('migrations/0749_pending.sql','select 2;')
        with self.assertRaises(MigrationNumberError): integration.validate_candidate(self.repo,self.base)
        self.receipt.write_text('{"state":"running"}')
        with self.assertRaises(MigrationNumberError): self.render(['0749_pending.sql'])
        self.assertFalse((self.root/'integration-render-count').exists())


class RestoreForwardTests(unittest.TestCase):
    def setUp(self):
        spec=importlib.util.spec_from_file_location('integration_local_pg',REPO/'ops/local-pg-ci.py')
        self.pg=importlib.util.module_from_spec(spec);sys.modules[spec.name]=self.pg;spec.loader.exec_module(self.pg)
        self.tmp=tempfile.TemporaryDirectory(prefix='integration-db-adapter-');self.addCleanup(self.tmp.cleanup)
        self.events=[];self.envs=[]
        self.bins=self.pg.PostgresBinaries(*[Path('/fake')/n for n in ['initdb','pg_ctl','createdb','psql']])
    def run_case(self,fail=None,moved=False):
        outer=self;pg=self.pg
        class Runner:
            def run(self,command,*,env=None,cwd=None,capture=False):
                args=tuple(map(str,command));outer.events.append(args);outer.envs.append(dict(env or {}))
                if fail and fail(args,env): return pg.CommandResult(4,'','private-canary-123')
                return pg.CommandResult(0,'{}' if args[-1]=='--fingerprint-only' else '','')
        binding={'base':'a'*40,'head':'b'*40,'tree':'c'*40}
        source=([binding,binding,{**binding,'tree':'d'*40}] if moved=='canonical' else
                [binding,{**binding,'tree':'d'*40}] if moved else [binding,binding,binding])
        with (patch.object(pg,'find_postgres_binaries',return_value=self.bins),
              patch.object(pg,'port_is_available',return_value=True),
              patch.object(pg,'refuse_hosted_execution'),
              patch.object(pg.tempfile,'mkdtemp',return_value=self.tmp.name),
              patch.object(integration,'validate_candidate',side_effect=source),
              patch.object(integration,'git',return_value=b'-- exact current main schema'),
              patch.dict(os.environ,{'CANARY_TOKEN':'private-canary-123'})):
            return pg.run_local_ci(repo=REPO,ci_class='migration',port=55432,runner=Runner(),integration_base='a'*40)
    def test_restore_forward_consumers_precede_canonical_candidate_proof(self):
        self.assertEqual(self.run_case(),0)
        restore=next(i for i,a in enumerate(self.events) if a[-1].endswith('integration-main-schema.sql'))
        forward=next(i for i,a in enumerate(self.events) if a[-3:] == (str(REPO/'tools/migrate.py'),'--apply','--yes'))
        consumers=[i for i,a in enumerate(self.events) if a[-1].endswith(('find-rule-supersedes.test.mjs','catch-me-up-writer-route.test.mjs'))]
        canonical=next(i for i,a in enumerate(self.events) if str(REPO/'ops/ci.sh') in a)
        self.assertLess(restore,forward);self.assertEqual(len(consumers),2)
        # Database names do not isolate cluster-global roles created by migrations.
        self.assertEqual(sum(a[0]=='/fake/initdb' for a in self.events),2)
        self.assertIn(':55433/',self.envs[forward]['DATABASE_URL'])
        self.assertIn(':55432/',self.envs[canonical]['CARR_CI_DATABASE_URL'])
        self.assertEqual(sum(a[0]=='/fake/pg_ctl' and a[-1]=='stop' for a in self.events),3)
        stop_primary=next(i for i,a in enumerate(self.events) if a[0]=='/fake/pg_ctl' and a[-1]=='stop')
        init_integration=[i for i,a in enumerate(self.events) if a[0]=='/fake/initdb'][1]
        self.assertLess(stop_primary,init_integration)
        stop_integration=next(i for i,a in enumerate(self.events) if a[0]=='/fake/pg_ctl' and a[-1]=='stop' and 'integration-data' in ' '.join(a))
        self.assertLess(consumers[-1],stop_integration)
        self.assertLess(stop_integration,canonical)
        self.assertTrue(all(forward<i<canonical for i in consumers))
        self.assertTrue(all('CANARY_TOKEN' not in env for env in self.envs))
    def test_restore_forward_or_consumer_failure_stops_and_disposes(self):
        predicates=[lambda a,e:a[0]=='/fake/pg_ctl' and a[-1]=='stop' and 'integration-data' not in ' '.join(a),
                    lambda a,e:a[0]=='/fake/initdb' and 'integration-data' in ' '.join(a),
                    lambda a,e:a[0]=='/fake/pg_ctl' and a[-1]=='start' and 'integration-data' in ' '.join(a),
                    lambda a,e:a[0]=='/fake/pg_ctl' and a[-1]=='stop' and 'integration-data' in ' '.join(a),
                    lambda a,e:a[-1].endswith('integration-main-schema.sql'),
                    lambda a,e:a[-3:]==(str(REPO/'tools/migrate.py'),'--apply','--yes'),
                    lambda a,e:a[-1].endswith('find-rule-supersedes.test.mjs'),
                    lambda a,e:a[-1].endswith('catch-me-up-writer-route.test.mjs')]
        # Each case gets a new temporary cluster directory after disposal.
        for fail in predicates:
            with self.subTest(fail=fail):
                self.events.clear();self.envs.clear();Path(self.tmp.name).mkdir(exist_ok=True)
                output=io.StringIO()
                with redirect_stdout(output),redirect_stderr(output): self.assertEqual(self.run_case(fail),4)
                self.assertNotIn('private-canary-123',output.getvalue())
                self.assertFalse(any(str(REPO/'ops/ci.sh') in a for a in self.events))
                self.assertTrue(any(a[0]=='/fake/pg_ctl' and a[-1]=='stop' for a in self.events))
    def test_changed_source_refuses_after_canonical_proof(self):
        with self.assertRaises(self.pg.LocalPGRefusal): self.run_case(moved='canonical')
        self.assertTrue(any(str(REPO/'ops/ci.sh') in a for a in self.events))
        self.assertTrue(any(a[0]=='/fake/pg_ctl' and a[-1]=='stop' for a in self.events))

    def test_changed_source_refuses_after_consumer_proof(self):
        with self.assertRaises(self.pg.LocalPGRefusal): self.run_case(moved=True)
        self.assertFalse(any(str(REPO/'ops/ci.sh') in a for a in self.events))
        self.assertTrue(any(a[0]=='/fake/pg_ctl' and a[-1]=='stop' for a in self.events))

if __name__=='__main__': unittest.main()
