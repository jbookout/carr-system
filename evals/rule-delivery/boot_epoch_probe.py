"""Offline adverse control: delayed boot answers must not roll back a new arm."""
import argparse, hashlib, json, os, subprocess, sys, tempfile
from pathlib import Path
repo=Path(__file__).resolve().parents[2]
parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--corpus',type=Path,required=True);parser.add_argument('--out',type=Path,required=True);args=parser.parse_args()
sys.path.insert(0,str(repo))
with tempfile.TemporaryDirectory(prefix='slim-epoch-race-probe-') as tmp:
 root=Path(tmp);os.environ['CARR_RULE_BOOT_STATE_DIR']=str(root/'state');os.environ['CARR_RULE_BOOT_FETCH_STUB']=str(root/'page.json')
 from lib import rule_boot_gate as gate
 def render(rows):
  script="""const {ruleBootPage}=await import(process.argv[1]);let raw='';for await(const c of process.stdin)raw+=c;const rows=JSON.parse(raw);const first=await ruleBootPage(rows,'joe',1);const pages=[first];for(let p=2;p<=first.pages_total;p++)pages.push(await ruleBootPage(rows,'joe',p));process.stdout.write(JSON.stringify(pages));"""
  result=subprocess.run(['node','--input-type=module','-e',script,(repo/'mcp-server/src/rule-boot.js').as_uri()],input=json.dumps(rows),text=True,capture_output=True,check=True,cwd=repo,timeout=30)
  return json.loads(result.stdout)
 raw=args.corpus.read_bytes();rows=json.loads(raw)['rules']
 old=render(rows)
 new=render(rows+[{'id':'ffffffff-ffff-4fff-8fff-ffffffffffff','statement':'Offline fixture: an additional binding rule requires the new corpus.','personal_to':None}])
 stub=root/'page.json';stub.write_text(json.dumps({'ok':True,'rule_boot':old[0]}));gate.arm_session('late-answer-control','startup');before=gate.read_arm('late-answer-control')
 def payload(page):return {'session_id':'late-answer-control','cwd':str(repo),'tool_name':'mcp__carr__standing_context','tool_use_id':'probe-old-'+str(page['page']),'tool_input':{'detail':'boot','page':page['page']}}
 for page in old:gate.verdict(payload(page))
 stub.write_text(json.dumps({'ok':True,'rule_boot':new[0]}));gate.arm_session('late-answer-control','compact');compact=gate.read_arm('late-answer-control')
 for page in old:gate.observe(dict(payload(page),tool_response={'ok':True,'rule_boot':page}))
 after=gate.read_arm('late-answer-control');actions=[('Read',{'file_path':str(repo/'AGENTS.md')}),('Bash',{'command':'git push origin HEAD'}),('Agent',{'prompt':'Inspect diagnosis'})]
 decisions={tool:gate.verdict({'session_id':'late-answer-control','cwd':str(repo),'tool_name':tool,'tool_input':args})[0]for tool,args in actions}
 report={'schema':'offline-delayed-boot-answer-control/v1','corpus_sha256':hashlib.sha256(raw).hexdigest(),'probe_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'gate_source_sha256':hashlib.sha256((repo/'lib/rule_boot_gate.py').read_bytes()).hexdigest(),'expected':'After compact into a newer corpus, delayed answers initiated in the old arm must not roll the corpus back or authorize protected actions.','sequence':['initiate all old-corpus page fetches in startup arm','arm compact with actual freshly rendered newer corpus','deliver delayed old page answers','query Read/Bash/Agent verdicts; never execute those actions'],'epoch_changed':before['epoch']!=compact['epoch'],'new_corpus_digest':compact['digest'],'delayed_corpus_digest':old[0]['digest'],'final_corpus_digest':after['digest'],'protected_action_decisions':decisions,'control_passed':after['digest']==compact['digest'] and all(v=='deny'for v in decisions.values()),'limitation':'Source-only native-hook probe. Barrier-driven process concurrency and fresh recovery are separately covered by the hook selftest. No deployed adapter or no-tool-turn proof; activation remains held.'}
 args.out.parent.mkdir(parents=True,exist_ok=True);args.out.write_text(json.dumps(report,indent=2,sort_keys=True)+'\n');print(json.dumps(report,indent=2));sys.exit(0 if report['control_passed'] else 1)
