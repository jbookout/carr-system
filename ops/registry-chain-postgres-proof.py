#!/usr/bin/env python3
"""Prove one successor publication on a disposable database and isolated source clone."""
from pathlib import Path
import sys,json,subprocess,tempfile,hashlib
root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root/'ops'));sys.path.insert(0,str(root/'tools'))
from git_env import fixture_env
from integration_candidate import allocation_plan
from successor_generation import regenerate
repo=Path(tempfile.mkdtemp(prefix='arch-c1-successor-proof-'))/'repo';env=fixture_env()
def git(*args,cwd=repo):return subprocess.check_output(['git',*args],cwd=cwd,env=env,stderr=subprocess.DEVNULL,text=True).strip()
head=git('rev-parse','HEAD',cwd=root);git('clone','--quiet','--shared','--no-checkout',str(root),str(repo),cwd=root);git('switch','-qc','proof',head);git('update-ref','refs/remotes/origin/main',head)
(repo/'mcp-server/node_modules').symlink_to(root/'mcp-server/node_modules',target_is_directory=True)
old=json.loads((repo/'ops/config/scac-registry-chain.json').read_text());frontier=old['versions'][-1];tail=max(int(p.name[:4]) for p in (repo/'migrations').glob('*.sql'))
domain=repo/f'migrations/{tail+1:04d}_chain_fixture_domain.sql';domain.write_text('create table public.chain_fixture_domain(id integer);\n')
seal=repo/f'migrations/{tail+2:04d}_chain_fixture_scac_successor.sql'
plan=allocation_plan(repo,head,[domain.name,seal.name])
result=regenerate(repo,plan,[domain],seal,repo/frontier['migration'])
new=json.loads((repo/'ops/config/scac-registry-chain.json').read_text())
assert new['versions'][:-1]==old['versions']
assert new['versions'][-1]['number']==frontier['number']+1
assert hashlib.sha256((repo/'mcp-server/src/scac-mutation-registry.current.generated.js').read_bytes()).hexdigest()==new['versions'][-1]['artifact_sha256']
assert not (repo/f'mcp-server/src/scac-mutation-registry.v{frontier["number"]+1}.generated.js').exists()
subprocess.run(['node','--input-type=module','-e',"import {checkRegistryChain} from './ops/registry-chain-check.mjs'; checkRegistryChain();"],cwd=repo,env=env,check=True)
print(json.dumps({'repo':str(repo),'version':result['version'],'historical_pins_preserved':len(old['versions']),'runtime_pin_verified':True,'sql_disposable_readback':True}))
