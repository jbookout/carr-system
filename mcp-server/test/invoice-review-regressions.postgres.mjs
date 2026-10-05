// Production handlers and real competing transactions, synthetic loopback DB only.
import test from 'node:test';
import assert from 'node:assert/strict';
import {randomUUID} from 'node:crypto';
import pg from 'pg';
import {TOOLS,executeRegisteredTool} from '../src/tools.js';
const url=process.env.CARR_CI_DATABASE_URL||process.env.DATABASE_URL;
if(!url||!['localhost','127.0.0.1'].includes(new URL(url).hostname))throw Error('disposable_loopback_database_required');
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve};};
async function fixture(t) {
  const clients=await Promise.all([0,1,2].map(async()=>{const c=new pg.Client({connectionString:url});await c.connect();return c;}));
  const [a,b,o]=clients;
  const actor={id:randomUUID(),slug:'joe',human:true,via:'synthetic-test'};
  const party=randomUUID(),client=randomUUID(),deal=randomUUID(),building=randomUUID(),space=randomUUID(),premises=randomUUID();
  t.after(async()=>{
    try {
      for(const c of clients)await c.query('rollback');
      await o.query('begin');
      await o.query('delete from deal_invoice_email where created_by=$1',[actor.id]);
      await o.query('delete from tool_call where actor_id=$1',[actor.id]);
      await o.query('delete from event where actor_id=$1',[actor.id]);
      await o.query('delete from lead_stage_move where created_by=$1',[actor.id]);
      await o.query('delete from lead_contact_draft where created_by=$1',[actor.id]);
      await o.query('delete from activity where actor_id=$1',[actor.id]);
      await o.query('delete from lead where created_by=$1',[actor.id]);
      await o.query('delete from premises_space where premises_id=$1',[premises]);
      await o.query('delete from premises where id=$1',[premises]);
      await o.query('delete from space where id=$1',[space]);
      await o.query('delete from building where id=$1',[building]);
      await o.query('delete from deal where client_id=$1',[client]);
      await o.query('delete from client where created_by=$1',[actor.id]);
      await o.query('delete from party where id=$1',[party]);
      await o.query('delete from record_flag where created_by=$1',[actor.id]);
      await o.query('delete from actor where id=$1',[actor.id]);
      await o.query('commit');
    } finally {await Promise.all(clients.map(c=>c.end()));}
  });
  const name='Synthetic Review '+deal;
  await o.query("insert into actor(id,slug,kind,display_name)values($1,$2,'human','Synthetic Review')",[actor.id,'synthetic-'+actor.id]);
  await o.query("insert into party(id,kind,name,created_by,updated_by)values($1,'person','Synthetic Practice',$2,$2)",[party,actor.id]);
  await o.query("insert into client(id,party_id,roster_ref,status,created_by,updated_by)values($1,$2,$3,'active_deal',$4,$4)",[client,party,'C-SYNTH-'+client,actor.id]);
  async function addDeal(c,id=deal){await c.query("insert into deal(id,client_id,name,deal_type,phase,created_by,updated_by)values($1,$2,$3,'other','legal',$4,$4)",[id,client,name,actor.id]);}
  await addDeal(o);
  await o.query("insert into building(id,address,city,state,zip,created_by,updated_by)values($1,'123 Example Avenue','Pensacola','FL','32501',$2,$2)",[building,actor.id]);
  await o.query("insert into space(id,building_id,suite,created_by,updated_by)values($1,$2,'Suite 2',$3,$3)",[space,building,actor.id]);
  await o.query("insert into premises(id,deal_id,label,created_by)values($1,$2,'Synthetic Suite 2',$3)",[premises,deal,actor.id]);
  await o.query('insert into premises_space(premises_id,space_id)values($1,$2)',[premises,space]);
  const call=(c,verb,args={})=>TOOLS[verb].handler(c,actor,{idempotency_key:randomUUID(),...args});
  async function capture(extra={}){return call(o,'record-deal-invoice',{native_ref:'local-mail:'+randomUUID(),from_address:'invoices@example.test',deal_name:name,client_name:'Synthetic Practice',occurred_at:new Date(Date.now()-86400000).toISOString(),...extra});}
  async function run(){await a.query('begin');try{const r=await call(a,'advance-leads');await a.query('commit');return r;}catch(e){await a.query('rollback');throw e;}}
  function pause(match){const reached=deferred(),resume=deferred(),query=a.query.bind(a);let used=false;a.query=async(sql,...args)=>{const r=await query(sql,...args);if(!used&&match(sql)){used=true;reached.resolve();await resume.promise;}return r;};return {reached:reached.promise,resume:()=>resume.resolve()};}
  return {a,b,o,actor,party,client,deal,building,space,premises,call,capture,run,pause,addDeal};
}
async function blocked(f,finished){const deadline=Date.now()+3000;while(!finished()&&Date.now()<deadline){if((await f.o.query('select cardinality(pg_blocking_pids($1))>0 blocked',[f.b.processID])).rows[0].blocked)return true;await new Promise(r=>setTimeout(r,10));}return false;}
test('review 1: newly matching deal between snapshots prevents an ambiguous close',async t=>{
  const f=await fixture(t),i=await f.capture(),p=f.pause(sql=>sql.startsWith('select d.id,d.name')&&!sql.includes('for update'));
  const work=f.run();work.catch(()=>{});await p.reached;
  try{await f.addDeal(f.b,randomUUID());}finally{p.resume();}
  const m=(await work).invoice_closes.find(m=>m.invoice_id===i.invoice_id);
  assert.equal(m.status,'proposed');assert.equal(m.needs_confirmation,'Multiple deals match');
  assert.equal((await f.o.query('select phase from deal where id=$1',[f.deal])).rows[0].phase,'legal');
});
for(const [label,sql,key] of [
  ['candidate insertion',null,null],
  ['party name','update party set name=\'Changed Practice\' where id=$1','party'],
  ['party retirement','update party set deleted_at=now() where id=$1','party'],
  ['client retirement','update client set merged_into=$1 where id=$1','client'],
  ['premises membership','delete from premises_space where premises_id=$1','premises'],
  ['premises deal','update premises set deal_id=null where id=$1','premises'],
  ['space suite','update space set suite=\'Suite 9\' where id=$1','space'],
  ['building address','update building set address=\'Other Avenue\' where id=$1','building'],
  ['building retirement','update building set merged_into=$1 where id=$1','building'],
])test('review 1/2: final matching snapshot protects '+label+' through close',async t=>{
  const f=await fixture(t),i=await f.capture(),p=f.pause(sql=>sql.startsWith('select d.id,d.name')&&sql.includes('for update'));
  const work=f.run();work.catch(()=>{});await p.reached;let finished=false;
  const edit=(sql?f.b.query(sql,[f[key]]):f.addDeal(f.b,randomUUID())).finally(()=>{finished=true;});edit.catch(()=>{});
  let waited;try{waited=await blocked(f,()=>finished);}finally{p.resume();await Promise.all([work,edit]);}
  assert.equal(waited,true,label+' must wait until close commits');
  assert.equal((await f.o.query('select status from deal_invoice_email where id=$1',[i.invoice_id])).rows[0].status,'applied');
});
test('review 3: SQL projection matches full premises identity, refuses street and wrong suite',async t=>{
  const f=await fixture(t);
  const full=await f.capture({client_name:undefined,property_address:'123 Example Avenue, Suite 2, Pensacola, FL 32501'});
  const street=await f.capture({client_name:undefined,property_address:'123 Example Avenue'});
  const other=await f.capture({client_name:undefined,property_address:'123 Example Avenue, Suite 9, Pensacola, FL 32501'});
  const preview=(await f.call(f.o,'lead-stage-preview')).invoice_closes;
  assert.equal(preview.find(m=>m.invoice_id===full.invoice_id).status,'applied');
  for(const i of [street,other])assert.equal(preview.find(m=>m.invoice_id===i.invoice_id).status,'proposed');
});
test('review 4: ordered preview and apply agree on transitions, prior values and conflicts',async t=>{
  const f=await fixture(t);
  const first=await f.capture({occurred_at:new Date(Date.now()-172800000).toISOString()});
  const second=await f.capture();
  const ids=new Set([first.invoice_id,second.invoice_id]);
  const preview=(await f.call(f.o,'lead-stage-preview')).invoice_closes.filter(m=>ids.has(m.invoice_id));
  const applied=(await f.run()).invoice_closes.filter(m=>ids.has(m.invoice_id));
  assert.deepEqual(preview,applied);
  assert.equal(preview[1].status,'proposed');assert.equal(preview[1].from_phase,'closed');
  const prior=(await f.o.query('select prior_phase,prior_invoiced_on from deal_invoice_email where id=$1',[first.invoice_id])).rows[0];
  assert.deepEqual(prior,{prior_phase:preview[0].from_phase,prior_invoiced_on:null});
});
test('review 5: migrated predecessor approval can be undone, forged associations and later edits cannot',async t=>{
  const f=await fixture(t),lead=randomUUID(),activity=randomUUID(),move=randomUUID();
  await f.o.query("insert into lead(id,registry_ref,party_id,stage,created_by,updated_by)values($1,$2,$3,'new',$4,$4)",[lead,'L-SYNTH-'+lead,f.party,f.actor.id]);
  await f.o.query("insert into activity(id,occurred_at,actor_id,kind,summary,lead_id,source)values($1,now(),$2,'email_in','Synthetic predecessor',$3,'local_mail')",[activity,f.actor.id,lead]);
  await f.o.query('begin');
  await f.o.query("update lead set stage='qualified' where id=$1",[lead]);
  await f.o.query("insert into lead_stage_move(id,lead_id,from_stage,to_stage,activity_id,evidence_ref,strength,status,approved_by,approved_at,created_by,reason)values($1,$2,'new','qualified',$3,'local-mail:predecessor','weak','applied',$4,now(),$4,'Reply received')",[move,lead,activity,f.actor.id]);
  const event=(await f.o.query("insert into event(occurred_at,actor_id,verb,subject_type,subject_id,field,old_value,new_value,cause)values(now(),$1,'approve-lead-move','lead',$2,'stage',$3,$4,'human_stated') returning id",[f.actor.id,lead,{stage:'new'},{stage:'qualified',evidence_ref:'local-mail:predecessor',activity_id:activity}])).rows[0].id;
  await f.o.query('commit');
  const version=(await f.o.query('select version from lead where id=$1',[lead])).rows[0].version;
  // Alter each association fact under a savepoint; the immutable history is restored afterwards.
  for(const sql of ["update event set verb='update-lead' where id=$1","update event set occurred_at=occurred_at+interval '1 second' where id=$1","update event set old_value='{\"stage\":\"engaged\"}' where id=$1","update event set new_value=jsonb_set(new_value,'{activity_id}','\"wrong\"') where id=$1"]){
    await f.o.query('begin');await f.o.query(sql,[event]);
    await assert.rejects(()=>f.call(f.o,'undo-lead-move',{move_id:move,base_version:version}),/newer_stage/);await f.o.query('rollback');
  }
  await f.o.query('begin');
  await f.o.query("insert into event(occurred_at,recorded_at,actor_id,verb,subject_type,subject_id,field,new_value,cause)values(now(),clock_timestamp(),$1,'update-lead','lead',$2,'stage','{\"stage\":\"qualified\"}','human_stated')",[f.actor.id,lead]);
  await assert.rejects(()=>f.call(f.o,'undo-lead-move',{move_id:move,base_version:version}),/newer_stage/);await f.o.query('rollback');
  const result=await f.call(f.o,'undo-lead-move',{move_id:move,base_version:version});
  assert.equal(result.stage,'new');
  assert.deepEqual((await f.o.query('select new_value from event where id=$1',[event])).rows[0].new_value,{stage:'qualified',evidence_ref:'local-mail:predecessor',activity_id:activity});
});

async function leadFixture(f,stage='new') {
  const id=randomUUID(),ref='L-SYNTH-'+id;
  await f.o.query("insert into lead(id,registry_ref,party_id,stage,created_by,updated_by)values($1,$2,$3,$4,$5,$5)",[id,ref,f.party,stage,f.actor.id]);
  return {id,ref};
}
for(const slug of ['joe-local','claude'])test('PR1550 1: registered '+slug+' approves archive and successfully undoes lead and invoice',async t=>{
  const f=await fixture(t),l=await leadFixture(f),activity=randomUUID(),move=randomUUID();
  const actor={...f.actor,slug,human:false,native_agent_verified:true,sponsoring_human_slug:'joe',via:slug==='joe-local'?'local-token':'oauth-agent'};
  const call=(verb,args)=>executeRegisteredTool(f.o,actor,verb,{idempotency_key:randomUUID(),...args});
  await f.o.query("insert into activity(id,occurred_at,actor_id,kind,summary,lead_id,source)values($1,now(),$2,'email_in','Synthetic archive',$3,'local_mail')",[activity,f.actor.id,l.id]);
  await f.o.query("insert into lead_stage_move(id,lead_id,from_stage,to_stage,activity_id,evidence_ref,strength,status,created_by,reason)values($1,$2,'new','archived',$3,'local-mail:archive','weak','proposed',$4,'Retired')",[move,l.id,activity,f.actor.id]);
  const v=async()=>(await f.o.query('select version from lead where id=$1',[l.id])).rows[0].version;
  assert.equal((await call('approve-lead-move',{move_id:move,base_version:await v()})).stage,'archived');
  assert.equal((await call('undo-lead-move',{move_id:move,base_version:await v()})).stage,'new');
  const i=await f.capture();await f.run();
  const dv=(await f.o.query('select version from deal where id=$1',[f.deal])).rows[0].version;
  assert.equal((await call('undo-invoice-close',{invoice_id:i.invoice_id,base_version:dv})).phase,'legal');
  assert.equal((await f.o.query('select status from deal_invoice_email where id=$1',[i.invoice_id])).rows[0].status,'undone');
});
const research=fields=>({sources:[{url:'https://example.test/synthetic',observed_at:new Date().toISOString()}],field_evidence:Object.fromEntries(fields.map(k=>[k,[0]])),discrepancies:[]});
test('PR1550 2: every archive stage writer requires partner authority, notes stay editable',async t=>{
  const f=await fixture(t),l=await leadFixture(f,'archived');
  const agent={...f.actor,slug:'synthetic-automation',human:false};
  const call=(verb,args)=>executeRegisteredTool(f.o,agent,verb,{idempotency_key:randomUUID(),...args});
  await assert.rejects(()=>call('new-lead',{party_id:f.party,stage:'archived'}),/archive_requires_partner/);
  await assert.rejects(()=>call('promote-pool',{pool_id:randomUUID(),base_version:1,stage:'archived',research_evidence:research(['name','company','phone','specialty','market'])}),/archive_requires_partner/);
  for(const outcome of ['not_interested','do_not_contact'])await assert.rejects(()=>call('log-outreach',{ref:l.id,outcome,summary:'Synthetic archive exit'}),/archive_requires_partner/);
  const version=(await f.o.query('select version from lead where id=$1',[l.id])).rows[0].version;
  assert.equal((await call('update-lead',{lead:l.id,base_version:version,fields:{notes:'Synthetic archived note'}})).ok,true);
  assert.equal((await f.o.query('select stage from lead where id=$1',[l.id])).rows[0].stage,'archived');
  assert.equal((await f.call(f.o,'new-lead',{party_id:f.party,stage:'archived'})).ok,true);
});
test('PR1550 3: client intake and empty invoice job avoid a foreign-key lock cycle',async t=>{
  const f=await fixture(t),l=await leadFixture(f),reached=deferred(),resume=deferred();
  const query=f.a.query.bind(f.a);let paused=false;
  f.a.query=async(sql,...args)=>{
    if(!paused&&sql.startsWith('lock table client')){paused=true;reached.resolve();await resume.promise;}
    return query(sql,...args);
  };
  const work=f.run();work.catch(()=>{});await reached.promise;
  await f.b.query('begin');
  let intakeFinished=false;
  const intake=f.call(f.b,'new-client',{party_id:f.party,status:'active_deal',acquisition_source:'Synthetic',research_evidence:research(['practice_name','address','phone','specialty','practitioners','hours'])}).then(async r=>{await f.b.query('commit');intakeFinished=true;return r;},async e=>{await f.b.query('rollback');intakeFinished=true;throw e;});
  intake.catch(()=>{});
  // Intake takes the client table before its party foreign-key read. SHARE
  // locks on party facts must allow that read to finish before the job resumes.
  const deadline=Date.now()+3000;
  while(!intakeFinished&&Date.now()<deadline){
    const locked=(await f.o.query("select exists(select 1 from pg_locks where pid=$1 and relation='client'::regclass and mode='RowExclusiveLock' and granted) held",[f.b.processID])).rows[0].held;
    if(locked)break;
    await new Promise(r=>setTimeout(r,10));
  }
  resume.resolve();
  const results=await Promise.allSettled([work,intake]);
  assert.deepEqual(results.map(r=>r.status),['fulfilled','fulfilled'],results.map(r=>String(r.reason)).join('\n'));
});
for(const approval of ['move','draft'])test('PR1550 3: empty invoice job and lead '+approval+' approval complete without a lock cycle',async t=>{
  const f=await fixture(t),l=await leadFixture(f,approval==='draft'?'qualified':'new');
  let verb,args;
  if(approval==='move') {
    const activity=randomUUID(),move=randomUUID();
    await f.o.query("insert into activity(id,occurred_at,actor_id,kind,summary,lead_id,source)values($1,now(),$2,'email_in','Synthetic archive approval',$3,'local_mail')",[activity,f.actor.id,l.id]);
    await f.o.query("insert into lead_stage_move(id,lead_id,from_stage,to_stage,activity_id,evidence_ref,strength,status,created_by,reason)values($1,$2,'new','archived',$3,'local-mail:archive','weak','proposed',$4,'Retired')",[move,l.id,activity,f.actor.id]);
    verb='approve-lead-move';
    args={move_id:move,base_version:(await f.o.query('select version from lead where id=$1',[l.id])).rows[0].version};
  } else {
    await f.run();
    verb='approve-lead-draft';
    args={draft_id:(await f.o.query('select id from lead_contact_draft where lead_id=$1',[l.id])).rows[0].id};
  }
  const pause=f.pause(sql=>sql.startsWith('select p.id from party p where exists'));
  const work=f.run();work.catch(()=>{});await pause.reached;
  await f.b.query('begin');
  let finished=false;
  const approve=executeRegisteredTool(f.b,f.actor,verb,{idempotency_key:randomUUID(),...args})
    .then(async result=>{await f.b.query('commit');return result;},async error=>{await f.b.query('rollback');throw error;})
    .finally(()=>{finished=true;});
  approve.catch(()=>{});
  let waited;
  try {waited=await blocked(f,()=>finished);}finally{pause.resume();}
  const results=await Promise.allSettled([work,approve]);
  assert.equal(waited,true,'approval must wait for the job transaction');
  assert.deepEqual(results.map(r=>r.status),['fulfilled','fulfilled'],results.map(r=>String(r.reason)).join('\n'));
  if(approval==='move')assert.equal((await f.o.query('select stage from lead where id=$1',[l.id])).rows[0].stage,'archived');
  else assert.equal((await f.o.query('select approved_by from lead_contact_draft where id=$1',[args.draft_id])).rows[0].approved_by,f.actor.id);
});
for(const table of ['deal','deal_invoice_email'])for(const action of ['apply','undo'])test('PR1550 5: '+action+' refuses zero-row '+table+' effect and rolls back history',async t=>{
  const f=await fixture(t),i=await f.capture();
  if(action==='undo')await f.run();
  await f.o.query('begin');
  try{
    const before=(await f.o.query('select phase,invoiced_on,version from deal where id=$1',[f.deal])).rows[0];
    const status=action==='apply'?'captured':'applied';
    const events=(await f.o.query('select count(*)::int n from event where actor_id=$1',[f.actor.id])).rows[0].n;
    const envelopes=(await f.o.query('select count(*)::int n from tool_call where actor_id=$1',[f.actor.id])).rows[0].n;
    await f.o.query(`create function pg_temp.reject_effect() returns trigger language plpgsql as $$begin if new.id='${table==='deal'?f.deal:i.invoice_id}'::uuid then return null; end if; return new; end$$`);
    await f.o.query(`create trigger synthetic_reject_effect before update on ${table} for each row execute function pg_temp.reject_effect()`);
    await f.o.query('savepoint effect');
    await assert.rejects(()=>f.call(f.o,action==='apply'?'advance-leads':'undo-invoice-close',action==='apply'?{}:{invoice_id:i.invoice_id,base_version:before.version}),/not_applied/);
    await f.o.query('rollback to savepoint effect');
    assert.deepEqual((await f.o.query('select phase,invoiced_on,version from deal where id=$1',[f.deal])).rows[0],before);
    assert.equal((await f.o.query('select status from deal_invoice_email where id=$1',[i.invoice_id])).rows[0].status,status);
    assert.equal((await f.o.query('select count(*)::int n from event where actor_id=$1',[f.actor.id])).rows[0].n,events);
    assert.equal((await f.o.query('select count(*)::int n from tool_call where actor_id=$1',[f.actor.id])).rows[0].n,envelopes);
  }finally{await f.o.query('rollback');}
  if(action==='apply')assert.equal((await f.run()).invoice_closes.find(m=>m.invoice_id===i.invoice_id).status,'applied');
});
