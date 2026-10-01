// Schema-shape integration: run against isolated fixtures, never production.
import test from 'node:test';
import assert from 'node:assert/strict';
import pg from 'pg';
import {readSystemWorkCensus,SYSTEM_WORK_LEGS} from '../src/system-work-census.v5.js';
const url=process.env.SYSTEM_WORK_TEST_DATABASE_URL;
test('actual PostgreSQL legs compile, grants execute and private/business loops are excluded',{skip:!url},async()=>{
 const client=new pg.Client({connectionString:url});await client.connect();
 try{
 await client.query('begin');
 const ids=['00000000-0000-4000-8000-000000000001','00000000-0000-4000-8000-000000000002'];
 await client.query(`insert into public.actor(id,slug,kind,display_name) values ($1,'joe','human','Synthetic owner one'),($2,'dell','human','Synthetic owner two')`,ids);
 for(const [index,tier,personal,domain,subject] of [[1,'shared',null,'system',{}],[2,'personal',ids[0],'system',{}],[3,'personal',ids[1],'system',{}],[4,'shared',null,'deals',{}],[5,'shared',null,'system',{subject_type:'client'}]]){
  await client.query(`insert into public.loop_item(kind,number,block_id,render_seq,title,tier,personal_to,domain,extra_cells,created_by,updated_by)
  values('idea',$1,$2,1,'Synthetic loop',$3,$4,$5,$6,$2,$2)`,[String(index),ids[0],tier,personal,domain,subject]);
 }
 await client.query('set local role carr_reader');
 for(const slug of ['joe','dell']){
 const result=await readSystemWorkCensus({client,actor:{slug,human:true},correlationId:'synthetic',kinds:SYSTEM_WORK_LEGS.map(l=>l.kind).join(',')});
 for(const c of result.coverage) assert.notEqual(c.state,'unavailable',`${slug} ${c.kind}: ${c.reason}`);
 const loops=result.items.filter(i=>i.kind==='loop');assert.equal(loops.length,2);assert.ok(loops.every(i=>i.identity.kind==='idea'));
 const only=await readSystemWorkCensus({client,actor:{slug,human:true},correlationId:'synthetic',kinds:'loop',limit:1});
 const second=await readSystemWorkCensus({client,actor:{slug,human:true},correlationId:'synthetic',kinds:'loop',limit:1,cursor:only.next_cursor});
 assert.notEqual(only.items[0].id,second.items[0].id);assert.equal(second.next_cursor,null);
 }
 await client.query('rollback');
 }finally{await client.end();}
});
