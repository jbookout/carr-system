import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync,mkdtempSync,existsSync,mkdirSync,renameSync} from 'node:fs';
import {execFileSync} from 'node:child_process';
import path from 'node:path';
import pg from 'pg';
import {LEASE_RADAR_SQL} from '../src/workspace-business-read.js';
let bin;
try{bin=execFileSync('pg_config',['--bindir'],{encoding:'utf8'}).trim();}catch{}
if(!bin || !existsSync(path.join(bin,'postgres'))) for(const candidate of ['/opt/homebrew/opt/postgresql@17/bin','/usr/lib/postgresql/17/bin','/usr/lib/postgresql/16/bin']) if(existsSync(path.join(candidate,'postgres'))){bin=candidate;break;}
const available=bin && existsSync(path.join(bin,'postgres'));
const id=n=>`aa000000-0000-4000-8000-${String(n).padStart(12,'0')}`;
test('real PostgreSQL projection covers horizon, missing dates, tombstones, holds and reader-only view grant',{skip:!available && 'PostgreSQL binaries unavailable'},async()=>{
  const dir=mkdtempSync('/tmp/lease-radar-');let c,running=false;
  try{
    execFileSync(path.join(bin,'initdb'),['-D',dir,'-U','fixture','--auth=trust','--no-locale'],{stdio:'pipe'});
    execFileSync(path.join(bin,'pg_ctl'),['-D',dir,'-l',path.join(dir,'server.log'),'-o',`-k ${dir} -h ''`,'-w','start'],{stdio:'pipe'});running=true;
    c=new pg.Client({host:dir,user:'fixture',database:'postgres'});await c.connect();
    const schema=readFileSync(new URL('../../db/schema.sql',import.meta.url),'utf8');
    for(const name of ['actor','client','party','client_status','lease','next_action','critical_date']){
      const table=schema.match(new RegExp(`CREATE TABLE public.${name} \\([\\s\\S]*?\\n\\);`))?.[0];assert.ok(table,name);await c.query(table);
    }
    await c.query('create role carr_reader; grant usage on schema public to carr_reader;');
    await c.query(readFileSync(new URL('../../migrations/0788_lease_radar_read.sql',import.meta.url),'utf8'));
    await c.query("insert into actor(id,slug,display_name,kind) values ($1,'joe','Demo Broker','human')",[id(1)]);
    await c.query("insert into client_status(slug,label,sort) values ('past_client','Past client',1),('active_deal','Active deal',2)");
    for(let n=1;n<=9;n++){
      await c.query("insert into party(id,name,kind,created_by,updated_by,contact_state) values ($1,$2,'person',$3,$3,$4)",[id(10+n),`Demo Practice ${n}`,id(1),n===7?'do_not_contact':n===8?'paused':'active']);
      await c.query("insert into client(id,party_id,status,created_by,updated_by,owner_id,merged_into) values ($1,$2,$3,$4,$4,$4,$5)",[id(30+n),id(10+n),n===2?'active_deal':'past_client',id(1),n===6?id(31):null]);
      const expiration=["today","today + interval '24 months'","today + interval '24 months 1 day'","today - 1","null","today + 1","today + 2","today + 3","today + 4"][n-1];
      await c.query(`insert into lease(id,client_id,created_by,status,expiration_on) select $1,$2,$3,$4,${expiration} from (select (now() at time zone 'America/Chicago')::date as today) clock`,[id(50+n),id(30+n),id(1),n===9?'superseded':'legacy_unverified']);
      await c.query("insert into next_action(id,subject_type,subject_id,owner_id,created_by,updated_by,status,description,due_on) values ($1,'client',$2,$3,$3,$3,'open','Demo renewal review',(now() at time zone 'America/Chicago')::date)",[id(70+n),id(30+n),id(1)]);
    }
    await c.query('set role carr_reader');
    const read=(await c.query(LEASE_RADAR_SQL)).rows[0];
    assert.deepEqual(read.leases.map(r=>r.id).sort(),[1,2,5,7,8].map(n=>id(50+n)).sort());
    assert.equal(read.leases.find(r=>r.id===id(55)).expiration_on,null);
    assert.equal(read.leases.find(r=>r.id===id(52)).expiration_on,read.ends_on);
    assert.equal(read.leases.find(r=>r.id===id(51)).touch_eligible,true);
    for(const n of [2,7,8]) assert.equal(read.leases.find(r=>r.id===id(50+n)).touch_eligible,false);
    await assert.rejects(c.query('select * from public.lease'),e=>e.code==='42501');
    await assert.rejects(c.query('delete from public.v_client_lease_radar'),e=>e.code==='42501' || e.code==='55000');
  }finally{await c?.end();if(running)execFileSync(path.join(bin,'pg_ctl'),['-D',dir,'-m','fast','-w','stop'],{stdio:'pipe'});mkdirSync('/tmp/_to_delete',{recursive:true});renameSync(dir,path.join('/tmp/_to_delete',path.basename(dir)));}
});
