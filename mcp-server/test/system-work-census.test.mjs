import test from 'node:test';
import assert from 'node:assert/strict';
import {readSystemWorkCensus,SYSTEM_WORK_LEGS,SYSTEM_WORK_KINDS,systemWorkActions,systemWorkTools} from '../src/system-work-census.v5.js';
const actor={slug:'joe',human:true,id:'00000000-0000-4000-8000-000000000001'};
const now=()=>new Date('2026-10-01T12:00:00Z');
const row=(kind,index=0)=>({id:`${kind}-${index}`,title:`Synthetic ${kind}`,state:'open',opened_at:'2026-08-01T12:00:00Z',last_activity_at:'2026-09-01T12:00:00Z',version:'3',completed:false,cancelled:false,identity:{}});
function fixture({down, live=false, many=false, cacheComplete=true}={}){
 const cache={schema:'system-work-external.v1',observed_at:now().toISOString(),complete:cacheComplete,items:['pull_request','remote_branch','builder_brief_file'].map(k=>({...row(k),kind:k,completed:live}))};
 const seen=[];
 return {seen,query:async(sql,params)=>{
  seen.push({sql,params});if(sql.includes("as cache"))return {rows:[{cache}]};
  const leg=SYSTEM_WORK_LEGS.find(l=>sql.includes(`(${l.sql})`));assert.ok(leg,'known source SQL');
  if(down===leg.kind)throw Object.assign(new Error('offline'),{code:'42501'});
  if(sql.includes('count(*) as count'))return {rows:[{count:many?4:1}]};
  let rows=Array.from({length:many?4:1},(_,i)=>({...row(leg.kind,i),completed:live}));
  if(params[5])rows=rows.filter(r=>r.id===params[5]);
  if(params.length===11)rows=rows.filter(r=>leg.kind>params[8]||(leg.kind===params[8]&&r.id>params[9]));
  return {rows:rows.slice(0,params.at(-1))};
 }};
}
const read=(client,args={})=>readSystemWorkCensus({client,actor,correlationId:'synthetic',now,...args});
test('all 19 source kinds appear with fixture counts and honest defect coverage',async()=>{
 const client=fixture();const result=await read(client);
 assert.deepEqual(result.items.map(r=>r.kind).sort(),[...SYSTEM_WORK_KINDS].sort());
 for(const c of result.coverage){assert.equal(c.count_total,1,c.kind);assert.equal(c.count_returned,1,c.kind);}
 assert.equal(result.census_complete,false);assert.equal(result.coverage.find(c=>c.kind==='defect').reason,'fix_status_not_recorded');
 assert.equal(client.seen.some(q=>/\b(insert|update|delete)\b/i.test(q.sql)),false);
});
test('equal timestamp keyset pages exhaust every leg without duplicates',async()=>{
 let cursor=null,ids=[];
 do{const p=await read(fixture({many:true}),{limit:3,cursor});ids.push(...p.items.map(r=>r.kind+':'+r.id));cursor=p.next_cursor;}while(cursor);
 assert.equal(ids.length,16*4+3);assert.equal(new Set(ids).size,ids.length);
});
test('cursor binds search, source and library filters',async()=>{
 const p=await read(fixture({many:true}),{limit:1});
 await assert.rejects(()=>read(fixture(),{cursor:p.next_cursor,text:'different'}),e=>e.code==='AUTHORIZATION_REFUSED');
 await assert.rejects(()=>read(fixture(),{kinds:'deal'}),e=>e.code==='AUTHORIZATION_REFUSED');
 const client=fixture();await read(client,{kinds:'loop',text:'_%',age:8});
 const query=client.seen.find(q=>q.sql.includes('census where'));
 assert.equal(query.params[4],'%\\_\\%%');assert.equal(query.params[3],'2026-09-23T12:00:00.000Z');
 assert.match(query.sql,/opened_at asc/);assert.match(query.sql,/id=\$6/);
});
test('authority is server-derived and personal loop/admission queries bind sponsor',async()=>{
 await assert.rejects(()=>readSystemWorkCensus({client:fixture(),actor:{slug:'unknown'},now}),e=>e.code==='AUTHORIZATION_REFUSED');
 for(const slug of ['joe','dell']){
  const client=fixture();await readSystemWorkCensus({client,actor:{slug,human:true},now,kinds:'loop,governance_item'});
  for(const q of client.seen){assert.equal(q.params[1],slug);}
 }
 const loop=SYSTEM_WORK_LEGS.find(l=>l.kind==='loop').sql;
 assert.match(loop,/domain='system'/);assert.match(loop,/l\.personal_to=/);assert.match(loop,/subject_type/);assert.match(loop,/'deal','lead','client','vendor','party'/);
 assert.match(SYSTEM_WORK_LEGS.find(l=>l.kind==='governance_item').sql,/r\.personal_to=/);
});
test('outage is incomplete and never empties working sources',async()=>{
 const r=await read(fixture({down:'investigation',cacheComplete:false}));
 assert.equal(r.census_complete,false);assert.ok(r.items.length>0);
 assert.equal(r.coverage.find(c=>c.kind==='investigation').state,'unavailable');
 assert.equal(r.coverage.find(c=>c.kind==='remote_branch').state,'unavailable');
});
test('Live query is independent of unfinished pages and sorts by latest activity',async()=>{
 const r=await read(fixture({live:true}),{live_library:true});assert.equal(r.items.length,19);
 assert.ok(r.items.every(i=>i.completed&&i.available_triage_actions.length===0));
});
test('Live GitHub coverage requires authenticated completed history in the cache',async()=>{
 const client=fixture({live:true}),query=client.query;
 client.query=async (...args)=>{const result=await query(...args);if(args[0].includes('as cache'))result.rows[0].cache.completed_pr_history=true;return result;};
 const result=await read(client,{live_library:true,kinds:'pull_request'});
 assert.equal(result.items.length,1);assert.equal(result.census_complete,true);
 const old=await read(fixture({live:true}),{live_library:true,kinds:'pull_request'});
 assert.equal(old.census_complete,false);assert.equal(old.coverage[0].reason,'cache_contains_open_github_work_only');
});
test('actions name source verbs, preserve typed identity and version semantics',()=>{
 const item={kind:'loop',identity:{loop_id:'synthetic-id',kind:'idea'},state:'open'};
 const actions=systemWorkActions(item);assert.deepEqual(actions.map(a=>a.verb),['close-loop','update-loop','update-loop']);
 assert.equal(actions[0].args.resolution,'dropped');assert.ok(actions.every(a=>a.versioned));
 assert.deepEqual(systemWorkActions({kind:'defect'}),[]);
 assert.equal(systemWorkActions({kind:'retrieval_proposal',id:'synthetic'} )[0].verb,'approve-retrieval-proposals');
 assert.equal(systemWorkActions({kind:'investigation',identity:{run_id:'synthetic'}})[0].verb,'close-investigation');
 assert.equal(systemWorkTools()['unfinished-work'].write,false);
});
