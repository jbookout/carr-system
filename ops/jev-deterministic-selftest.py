#!/usr/bin/env python3
"""Mechanical judgment regression tests. All transports are explosive fakes."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), REPO / 'ops' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class NoModel:
    def __getattr__(self, name):
        raise AssertionError('model access: ' + name)


class DeterministicTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_receipt_reads_bytes_and_expected_json_not_just_status(self):
        from lib.acceptance_checks import evaluate
        artifact = self.root / 'result.json'
        artifact.write_text('{"count":2}')
        criteria = [{'id':'count', 'kind':'json_equals', 'path':'result.json', 'pointer':'/count', 'value':2}]
        evidence = {'artifacts':[{'path':'result.json', 'sha256':hashlib.sha256(artifact.read_bytes()).hexdigest()}]}
        self.assertEqual(evaluate(criteria, evidence, root=self.root)['status'], 'passed')
        artifact.write_text('{"count":1}')
        self.assertEqual(evaluate(criteria, evidence, root=self.root)['status'], 'failed')
        evidence['artifacts'][0]['sha256'] = hashlib.sha256(artifact.read_bytes()).hexdigest()
        self.assertEqual(evaluate(criteria, evidence, root=self.root)['status'], 'failed')

    def test_empty_or_semantic_criteria_never_authorize_completion(self):
        from lib.acceptance_checks import evaluate
        for criteria in ([], ['make it delightful'], [{'kind':'semantic','text':'good'}]):
            self.assertEqual(evaluate(criteria, {}, root=self.root)['status'], 'needs_review')

    def test_check_receipts_bind_command_source_and_output(self):
        from lib.acceptance_checks import evaluate
        criteria = [{'id':'ci','kind':'check','command':'ops/ci.sh','source_sha':'a'*40,'output_contains':'CI passed'}]
        row = {'command':'ops/ci.sh','source_sha':'a'*40,'exit_code':0,'output':'CI passed'}
        evidence = {'checks':[row]}
        self.assertEqual(evaluate(criteria,evidence,root=self.root)['status'],'passed')
        for field, value in [('command','other'),('source_sha','b'*40),('exit_code',1),('output','failed')]:
            with self.subTest(field=field):
                self.assertEqual(evaluate(criteria,{'checks':[{**row,field:value}]},root=self.root)['status'],'failed')

    def test_artifact_expected_digest_and_containment(self):
        from lib.acceptance_checks import evaluate
        path=self.root/'output';path.write_text('content')
        digest=hashlib.sha256(path.read_bytes()).hexdigest()
        evidence={'artifacts':[{'path':'output','sha256':digest}]}
        criterion={'id':'output','kind':'artifact','path':'output'}
        self.assertEqual(evaluate([criterion],evidence,root=self.root)['status'],'needs_review')
        self.assertEqual(evaluate([{**criterion,'sha256':digest}],evidence,root=self.root)['status'],'passed')
        self.assertEqual(evaluate([{**criterion,'sha256':'0'*64}],evidence,root=self.root)['status'],'failed')
        for path_value in ('../outside', str(self.root.parent/'outside')):
            self.assertEqual(evaluate([{**criterion,'path':path_value}],evidence,root=self.root)['status'],'failed')

    def test_duplicate_receipts_and_unknown_criterion_never_pass(self):
        from lib.acceptance_checks import evaluate
        path=self.root/'output';path.write_text('content')
        receipt={'path':'output','sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
        criterion={'id':'output','kind':'contains','path':'output','text':'content'}
        self.assertEqual(evaluate([criterion],{'artifacts':[receipt,receipt]},root=self.root)['status'],'failed')
        self.assertEqual(evaluate([criterion,criterion],{'artifacts':[receipt]},root=self.root)['status'],'failed')
        self.assertEqual(evaluate([{'id':'semantic','kind':'good'}],{},root=self.root)['status'],'needs_review')

    def test_json_boolean_does_not_equal_integer_and_escaped_pointer_matches(self):
        from lib.acceptance_checks import evaluate
        path=self.root/'output';path.write_text('{"a/b":{"~flag":true}}')
        evidence={'artifacts':[{'path':'output','sha256':hashlib.sha256(path.read_bytes()).hexdigest()}]}
        criterion={'id':'output','kind':'json_equals','path':'output','pointer':'/a~1b/~0flag','value':True}
        self.assertEqual(evaluate([criterion],evidence,root=self.root)['status'],'passed')
        self.assertEqual(evaluate([{**criterion,'value':1}],evidence,root=self.root)['status'],'failed')

    def test_check_latest_same_command_source_resolves_but_boolean_exit_fails(self):
        from lib.acceptance_checks import evaluate
        criterion={'id':'ci','kind':'check','command':'ci','source_sha':'a'*40,'output_contains':'passed'}
        passed={'command':'ci','source_sha':'a'*40,'exit_code':0,'output':'passed'}
        failed={**passed,'exit_code':1}
        for rows,expected in [([failed,passed],'passed'),([passed,failed],'failed'),([{**passed,'exit_code':False}],'failed')]:
            self.assertEqual(evaluate([criterion],{'checks':rows},root=self.root)['status'],expected)

    def test_progress_changing_results_edits_and_dedupe(self):
        watch=load('jev_session_watch')
        events=[]
        for i in range(3):
            events += [{'type':'assistant','message':{'content':[{'type':'tool_use','id':str(i),'name':'Read','input':{'file_path':'x'}}]}},
                       {'type':'user','message':{'content':[{'type':'tool_result','tool_use_id':str(i),'content':str(i)}]}}]
        path=self.root/'t';path.write_text('\n'.join(json.dumps(e) for e in events))
        self.assertEqual(watch.watch_progress(str(path),'task',state_dir=str(self.root/'state'))['verdict'],'ok')
        for e in events:
            for b in e['message']['content']:
                if b['type']=='tool_result':b['content']='same'
        path.write_text('\n'.join(json.dumps(e) for e in events))
        self.assertEqual(watch.watch_progress(str(path),'task',state_dir=str(self.root/'state'))['verdict'],'stuck')
        self.assertEqual(watch.watch_progress(str(path),'task',state_dir=str(self.root/'state'))['verdict'],'ok')
        events += [{'type':'assistant','message':{'content':[{'type':'tool_use','id':'edit','name':'Edit','input':{}}]}},
                   {'type':'user','message':{'content':[{'type':'tool_result','tool_use_id':'edit','content':'edited'}]}}]
        path.write_text('\n'.join(json.dumps(e) for e in events))
        self.assertEqual(watch.watch_progress(str(path),'task',state_dir=str(self.root/'state'))['verdict'],'ok')

    def test_failed_edits_do_not_reset_progress_detection(self):
        watch=load('jev_session_watch')
        for unchanged_artifacts in (False,True):
            with self.subTest(unchanged_artifacts=unchanged_artifacts):
                events=[]
                for i in range(3):
                    events += [{'type':'assistant','message':{'content':[{'type':'tool_use','id':str(i),'name':'Edit','input':{'file_path':'x'}}]}},
                               {'type':'user','message':{'content':[{'type':'tool_result','tool_use_id':str(i),'is_error':True,'content':'PermissionError: denied'}]}}]
                path=self.root/'failed_edits';path.write_text('\n'.join(json.dumps(e) for e in events))
                kwargs={'artifact_before':{'x':'same'},'artifact_after':{'x':'same'}} if unchanged_artifacts else {}
                result=watch.watch_progress(str(path),'task',client=NoModel(),state_dir=str(self.root/str(unchanged_artifacts)),**kwargs)
                self.assertEqual(result['verdict'],'stuck')

    def test_progress_detects_repeat_without_artifact_delta(self):
        watch = load('jev_session_watch')
        events = []
        for i in range(3):
            events += [{'type':'assistant','message':{'content':[{'type':'tool_use','id':str(i),'name':'Read','input':{'file_path':'x'}}]}},
                       {'type':'user','message':{'content':[{'type':'tool_result','tool_use_id':str(i),'content':'same'}]}}]
        transcript = self.root/'t.jsonl'
        transcript.write_text('\n'.join(json.dumps(e) for e in events))
        with patch.object(watch,'_judge',side_effect=AssertionError('paid')):
            result = watch.watch_progress(str(transcript),'task',client=NoModel(),state_dir=str(self.root/'state'))
        self.assertEqual(result['verdict'],'stuck')

    def test_progress_elapsed_budget_and_observed_delta(self):
        watch = load('jev_session_watch')
        transcript = self.root/'t'; transcript.write_text('')
        result = watch.watch_progress(str(transcript),'task',elapsed_seconds=61,budget_seconds=60,
                                      artifact_before={'x':'a'},artifact_after={'x':'a'},state_dir=str(self.root))
        self.assertEqual(result['verdict'],'stuck')
        result = watch.watch_progress(str(transcript),'task',elapsed_seconds=61,budget_seconds=60,
                                      artifact_before={'x':'a'},artifact_after={'x':'b'},state_dir=str(self.root))
        self.assertEqual(result['verdict'],'ok')

    def test_repairs_are_candidates_with_no_semantic_guess(self):
        watch = load('jev_session_watch')
        self.assertEqual(watch.repair_path('bad/x.py',str(self.root),files=['src/x.py'],client=NoModel())['detail']['repaired_path'],'src/x.py')
        result = watch.repair_path('bad/x.py',str(self.root),files=['a/x.py','b/x.py'],client=NoModel())
        self.assertEqual(result['verdict'],'needs_review')
        self.assertEqual(watch.repair_name('helo',['hello'],'',client=NoModel())['verdict'],'needs_review')
        self.assertEqual(watch.triage_failure('python x','ModuleNotFoundError: x',1,client=NoModel())['verdict'],'environment')
        self.assertEqual(watch.triage_failure('cmd','Permission denied',1,client=NoModel())['verdict'],'permission')
        self.assertEqual(watch.triage_failure('cmd','mystery',1,client=NoModel())['verdict'],'needs_review')

    def test_changed_path_test_mapping_never_asks_model(self):
        watch = load('jev_session_watch')
        result = watch.pick_tests(['src/foo.py'],str(self.root),files=['ops/foo-selftest.py','ops/bar-selftest.py'],client=NoModel())
        self.assertEqual(result['detail']['tests'],['ops/foo-selftest.py'])

    def test_done_claim_needs_criteria_and_keeps_unresolved_test_failures(self):
        done = load('jev_done_checks')
        self.assertEqual(done.check_done_claim('Done',{'test_exit_code':0},client=NoModel())['verdict'],'needs_review')
        evidence = {'claim_scope':'tests','test_history':[{'command':'a','exit_code':1},{'command':'b','exit_code':0}]}
        self.assertEqual(done.check_done_claim('Tests passed',evidence,client=NoModel())['verdict'],'unsupported')
        evidence['test_history'].append({'command':'a','exit_code':0})
        self.assertEqual(done.check_done_claim('Tests passed',evidence,client=NoModel())['verdict'],'needs_review')

    def test_semantic_test_quality_and_facts_abstain(self):
        done = load('jev_done_checks')
        self.assertEqual(done.check_test_quality('def test_x():\n assert call() == 2','','',client=NoModel())['verdict'],'needs_review')
        self.assertEqual(done.fact_check('X works',[{'ref':'x','text':'X works'}],client=NoModel())['verdict'],'needs_review')

    def test_identical_calls_are_not_tautologies(self):
        done = load('jev_done_checks')
        self.assertEqual(done.check_test_quality('def test_x():\n assert call() == call()','','',client=NoModel())['verdict'],'needs_review')

    def test_requirement_criteria_are_evaluated_without_llm(self):
        req = load('jev_requirements')
        result = req.evaluate_requirements([{'id':'output','kind':'artifact','path':'missing'}],{},root=self.root)
        self.assertEqual(result['status'],'failed')
        self.assertEqual(req.evaluate_requirements(['fix the thing'],{},root=self.root)['status'],'needs_review')

    def test_handoff_matches_exact_action_and_denial(self):
        handoff = load('jev_handoff')
        evidence = {'action':'python ops/x.py','attempts':[], 'capability':'available','permission':'allowed'}
        self.assertEqual(handoff.evaluate_handoff(evidence)['status'],'unattempted')
        evidence['attempts']=[{'action':'python other.py','status':'permission_denied'}]
        self.assertEqual(handoff.evaluate_handoff(evidence)['status'],'unattempted')
        evidence['attempts']=[{'action':'python ops/x.py','status':'permission_denied'}]
        self.assertEqual(handoff.evaluate_handoff(evidence)['status'],'human_required')
        evidence['attempts']=[{'action':'python ops/x.py','status':'timeout'}]
        self.assertEqual(handoff.evaluate_handoff(evidence)['status'],'needs_review')
        self.assertIsNone(handoff.judge('Please run the install',surface='stop',judge_module=NoModel()))

    def test_tolls_use_path_contract_dependencies_not_transport(self):
        tolls = load('jev_change_tolls')
        result = tolls.owed({'files':{'edited':['ops/ci.sh','mcp-server/src/tools.js','hooks/delegation-gate.py']}},client=NoModel())
        self.assertTrue({'ci_sh_reseal','inventory_reseal','gate_rebless'} <= {name for _,name,_ in result})
        self.assertEqual(tolls.owed({'files':{'edited':['README.md']}},client=NoModel()),[])

    def test_tolls_follow_sealed_source_locators_outside_server(self):
        tolls=load('jev_change_tolls')
        result=tolls.owed({'files':{'edited':['bin/deploy-worker.sh']}},client=NoModel())
        self.assertIn('inventory_reseal',[name for _,name,_ in result])

    def test_notebook_uses_exact_error_or_artifact_not_topic_overlap(self):
        notebook = load('jev_notebook')
        path = self.root/'notebook'
        notebook.record_mistake('import','edit code','ModuleNotFoundError: widgets','install it',source='fixture',notebook_path=str(path))
        notebook.record_mistake('path','edit code','missing src/data.json','fix path',source='fixture',notebook_path=str(path))
        self.assertEqual(notebook.recall_mistakes('ModuleNotFoundError: widgets',client=NoModel(),notebook_path=str(path))['verdict'],[0])
        self.assertEqual(notebook.recall_mistakes('edit code',client=NoModel(),notebook_path=str(path))['verdict'],[])
        self.assertEqual(notebook.classify_kind('unknown failure','',client=NoModel(),notebook_path=str(path))['verdict'],'needs_review')

    def test_headless_typed_acceptance_never_asks_model(self):
        from lib.headless_tasks import verify_completion
        output = self.root/'output'; output.write_text('{"count":2}')
        criteria = [{'id':'count','kind':'json_equals','path':str(output),'pointer':'/count','value':2}]
        instructions = json.dumps({'acceptance_contract':{'criteria':criteria}})
        receipt = self.root/'receipt'
        receipt.write_text(json.dumps({'schema':'carr-headless-completion/v1','task_id':'t','run_id':'r','outcome':'completed',
                'artifacts':[{'path':str(output),'sha256':hashlib.sha256(output.read_bytes()).hexdigest()}]}))
        with patch('ops.jev_judge.judge',side_effect=AssertionError('paid')):
            self.assertEqual(verify_completion(receipt,'t','r',instructions),'completed')
            with self.assertRaisesRegex(ValueError,'needs review'):
                verify_completion(receipt,'t','r','make it good')

    def test_supervisor_passes_typed_contract_and_check_receipts(self):
        spec=importlib.util.spec_from_file_location('typed_supervisor',REPO/'hooks/jev-supervisor.py')
        hook=importlib.util.module_from_spec(spec);spec.loader.exec_module(hook)
        criterion={'id':'ci','kind':'check','command':'ci','source_sha':'a'*40,'output_contains':'passed'}
        prompt=json.dumps({'acceptance_contract':{'criteria':[criterion]}})
        from types import SimpleNamespace
        captured=[]
        def inspect(final,evidence,*args):captured.append(evidence);return []
        receipts={'checks':[{'command':'ci','source_sha':'a'*40,'exit_code':0,'output':'passed'}]}
        with patch.object(hook,'_task',return_value=prompt),patch.object(hook,'_git_root',return_value=None),patch.object(hook,'_lib',return_value=SimpleNamespace(inspect_stop_boundary=inspect)):
            hook.stop({'last_assistant_message':'Done','acceptance_evidence':receipts},hook.Run())
        done=load('jev_done_checks')
        self.assertEqual(done.check_done_claim('Done',captured[0])['verdict'],'supported')

    def test_supervisor_runs_deterministic_stop_without_paid_registration(self):
        spec = importlib.util.spec_from_file_location('det_supervisor', REPO/'hooks/jev-supervisor.py')
        hook = importlib.util.module_from_spec(spec); spec.loader.exec_module(hook)
        import io
        with patch.object(hook.POLICY,'call_site_enabled',return_value=False), patch.object(hook,'stop') as stop, patch.object(hook,'judgment_point',return_value=True), patch.object(hook.sys,'stdin',io.StringIO(json.dumps({'hook_event_name':'Stop','session_id':'fixture'}))):
            hook.main()
        stop.assert_called_once()

    def test_slice_matches_typed_criterion_binding_only(self):
        marker = load('slice-done-marker').Marker(call=NoModel(),git_run=NoModel())
        member = {'id':'member','commit_sha':'a'*40,'slice_id':'V5-F08','subject':'does everything',
                  'acceptance_receipts':[{'schema':'slice-acceptance/v1','slice_id':'V5-F08','criterion':'criterion',
                    'source_sha':'a'*40,'status':'passed','candidate_passes':True,'evidence_ref':'server-check:1'}]}
        self.assertEqual(marker.match({'proposed_id':'V5-F08'},['criterion'],[member]),{'criterion':'member'})
        member['acceptance_receipts'][0]['source_sha']='b'*40
        self.assertEqual(marker.match({'proposed_id':'V5-F08'},['criterion'],[member]),{'criterion':None})


if __name__ == '__main__':
    unittest.main()
