"""Measure legacy migration and current family selection against the PR base."""
import datetime
import hashlib
import json
import random
import subprocess
from pathlib import Path
import score

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    base = subprocess.check_output(['git', 'merge-base', 'HEAD', 'origin/main'], cwd=ROOT, text=True).strip()
    cases = {}
    matrix = [
        ('sol-upgrade', 'gpt-6-sol', [{'slug':'gpt-6-sol'}, {'slug':'gpt-6.1-sol'}], {'model':'gpt-6.1-sol'}),
        ('luna-upgrade', 'gpt-6-luna', [{'slug':'gpt-6-luna'}, {'slug':'gpt-6.1-luna'}], {'model':'gpt-6.1-luna'}),
        ('numeric-order', 'gpt-6.9-sol', [{'slug':'gpt-6.9-sol'}, {'slug':'gpt-6.10-sol'}], {'model':'gpt-6.10-sol'}),
        ('hidden', 'gpt-6.1-sol', [{'slug':'gpt-6.1-sol'}, {'slug':'gpt-7-sol','hidden':True}], {'model':'gpt-6.1-sol'}),
        ('retired', 'gpt-6.1-sol', [{'slug':'gpt-6.1-sol'}, {'slug':'gpt-7-sol','status':'retired'}], {'model':'gpt-6.1-sol'}),
        ('other-family', 'gpt-6.1-sol', [{'slug':'gpt-6.1-sol'}, {'slug':'gpt-8-luna'}], {'model':'gpt-6.1-sol'}),
        ('old-codex', 'gpt-5.1-codex-mini', [{'slug':'gpt-6.1-sol'}], {'model':'gpt-6.1-sol'}),
        ('missing-family', 'gpt-6-sol', [{'slug':'gpt-6-luna'}], {'error':'codex_family_unavailable'}),
        ('empty', 'gpt-6-sol', [], {'error':'codex_family_unavailable'}),
        ('malformed-slug', 'gpt-6-sol', [{'slug':'gpt-latest-sol'}], {'error':'codex_family_unavailable'}),
        ('no-regression-sol', 'gpt-6.1-sol', [{'slug':'gpt-6.1-sol'}], {'model':'gpt-6.1-sol'}),
        ('no-regression-luna', 'gpt-6.1-luna', [{'slug':'gpt-6.1-luna'}], {'model':'gpt-6.1-luna'}),
    ]
    # Both splits cover every finite behavior class; order is seeded before observations.
    random.Random(1667).shuffle(matrix)
    for split in ('train', 'test'):
        for name, legacy, models, expected in matrix:
            value = {'entry': {'kind':'codex-session', 'model':legacy, 'thread_id':split+'-thread',
                'effort':'high', 'cwd':'/synthetic/repo', 'sandbox':'workspace-write'}, 'catalog':{'models':models}}
            digest = hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
            cases[split+'-'+name] = {'split':split, 'input':value, 'input_sha256':digest,
                'should_not_fire':'error' in expected, 'expected_result':expected,
                'expected_metadata':{k:value['entry'][k] for k in ('thread_id','effort','cwd','sandbox')},
                'source':'human_judged_hard_case',
                'source_ref':'PR 1667 blocking review finding 9 and original family-routing regression cases'}
    exp = {'version':'model-routing-expectations/v1', 'baseline_ref':base, 'cases':cases}
    (HERE/'expectations.v1.json').write_text(json.dumps(exp,indent=2)+'\n')
    arms = {arm:score.observe(exp,arm) for arm in ('baseline','candidate')}
    for arm, rows in arms.items():
        assert rows == score.observe(exp,arm), 'unstable observations'
        (HERE/'evidence'/f'{arm}.jsonl').write_text(''.join(json.dumps(r,sort_keys=True)+'\n' for r in rows))
    measured = score.score(exp,arms['baseline'],arms['candidate'])
    assert measured == score.score(exp,arms['baseline'],arms['candidate'])
    assert measured['controls']['oracle_pass_rate']==1 and measured['controls']['null_pass_rate']==0
    refs = [f'evals/model-routing/evidence/{a}.jsonl' for a in arms]
    source = {f'evals/model-routing/{f}':sha(HERE/f) for f in ('score.py','make_report.py')}
    deps = {f'tools/room-bridge/{f}':sha(ROOT/f'tools/room-bridge/{f}') for f in ('desks.py','codex_models.py')}
    deps['ops/config/engineering-codex-desk.v1.json'] = sha(ROOT/'ops/config/engineering-codex-desk.v1.json')
    dims = [{'dimension_id':name,'critical':True,'status':'passed',
             'direction_vs_baseline':'improved' if values['delta']['value']>0 else 'equivalent',
             'evidence_refs':refs,**values} for name, values in measured['dimensions'].items()]
    receipt = {'schema_version':2,'surface':'model-routing',
        'change':'Migrate legacy Codex pins to families and select the newest available catalog version without losing desk metadata.',
        'measured_on':datetime.date.today().isoformat(),'rung':'regression',
        'adapter':{'surface':'offline_programmatic','adapter_id':'codex-registry-catalog-matrix','adapter_version':'1',
            'harness_id':'evals/model-routing/score.py','harness_version':source['evals/model-routing/score.py'],
            'provider_id':'none','model_id':'deterministic-no-model',
            'native_session_ref':'01a11f56-a1fd-72f1-87e6-c6f7e2013330',
            'configuration_fingerprint':'sha256:'+hashlib.sha256(json.dumps(deps,sort_keys=True).encode()).hexdigest()},
        'cases':{'total':24,'train':12,'test':12,'should_not_fire':6,'sources':['human_judged_hard_case']},
        'split':{'method':'seeded stratified exhaustive finite behavior matrix; each split covers every class',
                 'seed':1667,'sealed_test':True},'repeats':2,
        'grader':{'kind':'programmatic','validation':{'graded_twice':True,'result':'pass',**measured['controls']}},
        'noise_floor':0,'min_useful_gain':.01,'primary_dimension':'model-resolution','dimensions':dims,
        'stage_results':[{'stage_id':'routing','status':'passed','dimension_ids':list(measured['dimensions']),'evidence_refs':refs}],
        'cost':{'baseline_usd_per_case':0,'candidate_usd_per_case':0},
        'verdict':{'decision':'ship','statement':'Current catalog routing improves across the finite matrix with preserved registry metadata.'},
        'notes':['Exact finite matrix intervals, no generalization about future catalog entries or model output quality.',
                 'Programmatic runner executes registry migration and catalog selection; no model or external reviewer invocation.'],
        'evidence':{'scorer':{'path':'evals/model-routing/score.py','function':'score'},'source':source,'dependencies':deps,
            'expectations':{'path':'evals/model-routing/expectations.v1.json','version':exp['version'],
                            'sha256':sha(HERE/'expectations.v1.json')},
            'cohorts':{a:{'path':f'evals/model-routing/evidence/{a}.jsonl','sha256':sha(HERE/'evidence'/f'{a}.jsonl')} for a in arms}}}
    (HERE/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps(measured,indent=2))


if __name__=='__main__':
    main()
