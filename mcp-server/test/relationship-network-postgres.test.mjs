import test from 'node:test';
import assert from 'node:assert/strict';
import pg from 'pg';
import { randomUUID } from 'node:crypto';
import { TOOLS } from '../src/tools.js';
import { networkStatement, projectNetwork } from '../src/relationship-network.js';

test('W14 PostgreSQL: exact referrals, deduplication, broker attribution, holds and reader grants', async t => {
  const dsn=process.env.DATABASE_URL;
  if(!dsn){assert.notEqual(process.env.CARR_RELATIONSHIP_DB_REQUIRED,'1');t.skip('disposable PostgreSQL required');return;}
  const url=new URL(dsn),db=new pg.Client({connectionString:dsn});
  assert.ok(['postgres:','postgresql:'].includes(url.protocol)&&['localhost','127.0.0.1'].includes(url.hostname)&&url.port&&!url.search&&!url.hash&&db.connectionParameters.host===url.hostname&&db.connectionParameters.port===Number(url.port),'loopback database with explicit port required');
  await db.connect();await db.query('begin');
  try {
    const actor={...(await db.query("select id,slug from public.actor where slug='joe'")).rows[0],human:true};
    const suffix=randomUUID();
    const party=async label=>(await db.query("insert into public.party(kind,name,city,created_by,updated_by) values ('org',$1,'Demo North',$2,$2) returning id",['Demo '+label+' '+suffix,actor.id])).rows[0].id;
    const vp=await party('vendor'),cp=await party('client'),target=await party('target'),broker=await party('broker');
    const client=async p=>(await db.query("insert into public.client(party_id,roster_ref,created_by,updated_by,vertical,owner_id) values($1,$2,$3,$3,'dental',$3) returning id",[p,'C-DEMO-'+randomUUID(),actor.id])).rows[0].id;
    const ci=await client(cp),ti=await client(target);
    const vendor=(await db.query("insert into public.vendor(party_id,vendor_ref,category,territory,verticals,owner_id,created_by,updated_by) values($1,$2,'banker','Demo North',array['dental'],$3,$3,$3) returning id",[vp,'V-DEMO-'+suffix,actor.id])).rows[0].id;
    const deal=async(c,outcome)=>(await db.query("insert into public.deal(client_id,name,deal_type,phase,outcome,created_by,updated_by) values($1,$2,'lease',$3,$4,$5,$5) returning id",[c,'Demo deal '+randomUUID(),outcome?'closed':'site_selection',outcome,actor.id])).rows[0].id;
    const won=await deal(ti,'won'),lost=await deal(ti,'lost'),open=await deal(ti,null),unattributed=await deal(ti,'won'),wrong=await deal(ci,'won');
    const call=async args=>{await db.query('savepoint action');try{const r=await TOOLS['link-parties'].handler(db,actor,args);await db.query('release savepoint action');return r;}catch(e){await db.query('rollback to savepoint action');throw e;}};
    const base={from_party:broker,to_party:target,via_party:cp,kind:'referred',note:'Demo exact referral entry',occurred_on:'2026-10-01'};
    const first={...base,deal_id:won,idempotency_key:randomUUID()};
    await db.query('set local role carr_writer');
    const result=await call(first);assert.equal((await call(first)).replayed,true);
    await call({...base,deal_id:lost,idempotency_key:randomUUID()});await call({...base,deal_id:open,idempotency_key:randomUUID()});
    await call({...base,deal_id:won,idempotency_key:randomUUID()});
    await assert.rejects(call({...base,deal_id:wrong,idempotency_key:randomUUID()}),e=>e.payload?.error==='referral_deal_target_mismatch');
    await assert.rejects(call({...base,via_party:vp,deal_id:won,idempotency_key:randomUUID()}),e=>e.payload?.error==='referral_broker_mismatch');
    await db.query('reset role');
    assert.equal((await db.query('select count(*)::int n from public.party_link_deal where link_id=$1',[result.link_id])).rows[0].n,3);
    await db.query('update public.vendor set deal_evidence=$2 where id=$1',[vendor,JSON.stringify([{deal_id:won,role:'worked',occurred_at:'2026-10-01T00:00:00Z',evidence_ref:'Demo original work entry'},{deal_id:won,role:'referred',occurred_at:'2026-10-01T00:00:00Z',evidence_ref:'Demo vendor referral'}])]);
    const offer=await call({from_party:vp,to_party:target,kind:'can_introduce',note:'Demo offer to connect',idempotency_key:randomUUID()});
    const read=async()=>{await db.query('savepoint read');await db.query('set local role carr_reader');try{return projectNetwork((await db.query(networkStatement)).rows[0].snapshot,new Date().toISOString());}catch(e){await db.query('rollback to savepoint read');throw e;}finally{await db.query('reset role');}};
    let network=await read();
    const row=network.referrals.find(r=>r.node_id==='party:'+cp);assert.deepEqual(row,{node_id:'party:'+cp,deals:3,won:1,lost:1,win_rate:.5});
    assert.equal(network.referrals.find(r=>r.node_id==='party:'+vp).deals,1);
    assert.ok(network.edges.some(e=>e.from==='party:'+vp&&e.to==='deal:'+won&&e.kind==='worked'));
    assert.ok(!network.edges.some(e=>e.kind==='referred'&&e.to==='deal:'+unattributed));
    assert.equal(network.nodes.find(n=>n.id==='party:'+vp).owner,'joe');
    assert.ok(network.suggestions.some(s=>s.id==='link:'+offer.link_id&&s.reason==='Demo offer to connect'));
    await db.query("update public.party set contact_state='paused' where id=$1",[target]);network=await read();assert.ok(!network.suggestions.some(s=>s.id==='link:'+offer.link_id));
    await db.query('update public.party set merged_into=$2 where id=$1',[vp,broker]);network=await read();assert.ok(!network.nodes.some(n=>n.id==='party:'+vp));assert.ok(network.edges.every(e=>network.nodes.some(n=>n.id===e.from)&&network.nodes.some(n=>n.id===e.to)));
    await db.query('set local role carr_reader');await assert.rejects(db.query('insert into public.party_link_deal(link_id,deal_id,created_by,note) values($1,$2,$3,$4)',[result.link_id,wrong,actor.id,'Demo refused']),e=>e.code==='42501');
  } finally {await db.query('rollback');await db.end();}
});
