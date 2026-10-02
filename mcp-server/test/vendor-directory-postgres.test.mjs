import test from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { TOOLS } from '../src/tools.js';
import { parseBusinessQuery, readBusinessList, readBusinessRecord } from '../src/workspace-business-read.js';

test('W5 PostgreSQL: sourced stats, partner override, audit, replay, CAS and reader grants', async t => {
  const dsn = process.env.DATABASE_URL;
  if (!dsn) { assert.notEqual(process.env.CARR_VENDOR_DIRECTORY_DB_REQUIRED, '1'); t.skip('disposable PostgreSQL required'); return; }
  assert.match(dsn, /^postgres(?:ql)?:\/\/[^@]+@127\.0\.0\.1:\d+\//, 'loopback only');
  const { Client } = (await import('pg')).default;
  const db = new Client({connectionString:dsn}); await db.connect();
  await db.query('begin');
  try {
    const actor = {...(await db.query("select id,slug from public.actor where slug='joe'")).rows[0],human:true};
    const partner = {...(await db.query("select id,slug from public.actor where slug='dell'")).rows[0],human:true};
    const party = async name => (await db.query("insert into public.party(kind,name,created_by,updated_by) values ('org',$1,$2,$2) returning id",[name,actor.id])).rows[0].id;
    const suffix=randomUUID(), vp=await party('Demo vendor '+suffix), cp=await party('Demo practice '+suffix);
    const client=(await db.query("insert into public.client(party_id,roster_ref,created_by,updated_by,vertical,deal_type_label,owner_id) values ($1,$2,$3,$3,'dental','Lease',$3) returning id",[cp,'C-DEMO-'+suffix,actor.id])).rows[0].id;
    const vendor=(await db.query("insert into public.vendor(party_id,vendor_ref,category,territory,owner_id,created_by,updated_by,verticals) values ($1,$2,'banker','Demo North',$3,$3,$3,array['dental']) returning id",[vp,'V-DEMO-'+suffix,actor.id])).rows[0].id;
    const deal=(await db.query("insert into public.deal(client_id,name,deal_type,phase,outcome,closed_on,created_by,updated_by,salesforce_id) values ($1,$2,'lease','closed','won','2026-09-01',$3,$3,$4) returning id",[client,'Demo deal '+suffix,actor.id,'DEMO-SF-'+suffix])).rows[0].id;
    await db.query("insert into public.activity(occurred_at,actor_id,kind,summary,detail,vendor_id,source) values ('2026-09-30',$1,'email_in','Demo financing conversation','Original demo email entry',$2,'email'),('2026-09-29',$1,'meeting','Demo calendar meeting','Original demo calendar entry',$2,'calendar')",[actor.id,vendor]);
    await db.query("insert into public.party_link(from_party,to_party,kind,note,source,created_by,occurred_on) values ($1,$2,'intro','Demo introduction','stated',$3,'2026-09-23'),($1,$2,'can_introduce','Demo suggested introduction','stated',$3,null)",[vp,cp,actor.id]);
    const call = async (who, args) => {await db.query('savepoint action');try {const r=await TOOLS['update-vendor'].handler(db,who,structuredClone(args));await db.query('release savepoint action');return r;}catch(e){await db.query('rollback to savepoint action');throw e;}};
    const version = async () => (await db.query('select version from public.vendor where id=$1',[vendor])).rows[0].version;
    const request = async fields => ({vendor,base_version:await version(),fields,idempotency_key:randomUUID()});
    const read = async () => readBusinessRecord({client:db,actor,dataset:'vendors',id:vendor,contract:'vendor-directory.v1',correlationId:'demo',now:()=>new Date('2026-10-01')});
    assert.equal((await read()).record.relationship.deals_worked,null,'unknown history does not become zero');
    const evidence={deal_id:deal,role:'worked',occurred_at:'2022-01-01',evidence_kind:'salesforce',evidence_ref:'DEMO-SF-'+suffix};
    await call(actor,await request({deal_evidence:[evidence,{...evidence,role:'referred',evidence_kind:'mail',evidence_ref:'demo-mail-entry'}],verify_deal_history:true,loan_programs:['Demo equipment financing']}));
    let result=await read();assert.equal(result.record.relationship.deals_worked,1);assert.equal(result.record.relationship.deals_referred,1);assert.equal(result.record.relationship.win_rate,1);assert.equal(result.record.relationship.computed_tier,'Trial');assert.equal(result.record.relationship.last_contact_note,'Demo financing conversation');assert.equal(result.record.relationship.recent_entries[0].detail,'Original demo email entry');assert.deepEqual(result.record.loan_programs,['Demo equipment financing']);assert.deepEqual(result.record.relationship.introductions.map(item=>item.kind).sort(),['can_introduce','intro']);
    const override=await request({trust_override:{tier:'Established',reason:'Demo reviewed exception'}});
    await call(partner,override);const firstVersion=await version();assert.equal((await call(partner,override)).replayed,true);assert.equal(await version(),firstVersion);
    result=await read();assert.equal(result.record.relationship.override.recorded_by,'dell');assert.equal(result.record.relationship.override.reason,'Demo reviewed exception');assert.equal(result.record.relationship.computed_tier,'Trial');
    const audit=await db.query("select new_value from public.event where subject_id=$1 and field='trust_override'",[vendor]);assert.equal(audit.rows.length,1);assert.equal(audit.rows[0].new_value.trust_override.recorded_by,'dell');
    await assert.rejects(call(actor,{...override,idempotency_key:randomUUID()}),e=>e.payload?.error==='version_conflict');
    await assert.rejects(call({...actor,slug:'automation'},await request({trust_override:{tier:'Proven',reason:'Demo'}})),e=>e.payload?.error==='AUTHORIZATION_REFUSED');
    await call(actor,await request({deal_evidence:[{...evidence,evidence_kind:'calendar',evidence_ref:'demo-calendar-entry'}]}));assert.equal((await read()).record.relationship.deals_worked,null,'new evidence invalidates coverage');
    await call(actor,await request({trust_override:null}));assert.equal((await read()).record.relationship.override,null);
    await db.query('set local role carr_reader');
    for(const dataset of ['clients','vendors']) for(const sort of ['name','vertical','deal_type','last_deal_desc','last_deal_asc',...(dataset==='vendors'?['territory']:[])]) {
      const query=parseBusinessQuery(dataset,new URLSearchParams({contract:'vendor-directory.v1',q:'Demo',sort,owner:'joe',...(dataset==='vendors'?{territory:'Demo North'}:{})}),'joe');
      const list=await readBusinessList({client:db,actor,query,correlationId:'demo'});assert.ok(list.rows.some(row=>row.id===(dataset==='vendors'?vendor:client)),sort);assert.equal(list.query.owner,'joe');
    }
    assert.equal((await read()).record.id,vendor,'detail read uses reader grants');
  } finally {await db.query('rollback');await db.end();}
});
