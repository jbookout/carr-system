import { acquirePostgresFixtureGroup } from './helpers/disposable-postgres.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { readFileSync, mkdtempSync, existsSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import pg from 'pg';
import { TOOLS } from '../src/tools.js';
import { requireFeature } from '../src/feature-switches.js';

const root = fileURLToPath(new URL('../../', import.meta.url));
const schema = readFileSync(path.join(root, 'db/schema.sql'), 'utf8');
let bin;
for (const config of ['pg_config', '/opt/homebrew/opt/postgresql@17/bin/pg_config', '/usr/lib/postgresql/17/bin/pg_config']) {
  try {
    const candidate = execFileSync(config, ['--bindir'], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }).trim();
    if (existsSync(path.join(candidate, 'postgres'))) { bin = candidate; break; }
  } catch { /* Try the other supported PostgreSQL installations. */ }
}
const id = n => `aa000000-0000-4000-8000-${String(n).padStart(12, '0')}`;
const actor = { id: id(1), slug: 'joe', human: true, via: 'dealroom-cookie', client_id: 'dealroom-pwa' };

test('Feature switch record lifecycle on disposable PostgreSQL', { skip: !bin && !process.env.CARR_CI_DATABASE_URL && 'PostgreSQL unavailable' }, async t => {
  const ciDsn = process.env.CARR_CI_DATABASE_URL;
  const dir = ciDsn ? null : mkdtempSync('/tmp/feature-switches-');
  let admin;
  let database;
  let running = false;
  let c;
  // A provided CI cluster is owned and budgeted by its caller.
  const releaseBudget = ciDsn ? async () => {} : await acquirePostgresFixtureGroup();
  try {
    let connection;
    if (ciDsn) {
      const url = new URL(ciDsn);
      assert.ok(['postgres:', 'postgresql:'].includes(url.protocol));
      assert.ok(['127.0.0.1', 'localhost'].includes(url.hostname), 'fixture requires disposable loopback PostgreSQL');
      admin = new pg.Client({ connectionString: ciDsn });
      await admin.connect();
      const name = `feature_switches_${randomUUID().replaceAll('-', '')}`;
      await admin.query(`create database "${name}" template template0`);
      database = name;
      url.pathname = `/${database}`;
      connection = { connectionString: url.href };
    } else {
      execFileSync(path.join(bin, 'initdb'), ['-D', dir, '-U', 'fixture', '--auth=trust', '--no-locale'], { stdio: 'pipe' });
      execFileSync(path.join(bin, 'pg_ctl'), ['-D', dir, '-l', path.join(dir, 'server.log'), '-o', `-k ${dir} -h ''`, '-w', 'start'], { stdio: 'pipe' });
      running = true;
      connection = { host: dir, user: 'fixture', database: 'postgres' };
    }
    c = new pg.Client({ ...connection,
      // Preserve PostgreSQL microseconds, as the production HTTP driver does.
      types: { getTypeParser: (oid, format) => oid === 1184 ? value => value : pg.types.getTypeParser(oid, format) },
    });
    await c.connect();
    if (process.env.CARR_CI_DATABASE_URL) {
      const isolated = (await c.query('select current_database() name')).rows[0].name;
      assert.match(isolated, /^feature_switches_/);
      assert.notEqual(isolated, new URL(process.env.CARR_CI_DATABASE_URL).pathname.slice(1));
      assert.equal((await c.query("select to_regclass('public.deal') existing")).rows[0].existing, null);
    }
    // Roles belong to the cluster; an isolated database does not provide them.
    // Preserve existing shared-cluster roles and their attributes.
    await c.query(`do $$ begin
      begin create role carr_reader; exception when duplicate_object then null; end;
      begin create role carr_writer; exception when duplicate_object then null; end;
    end $$;`);
    for (const name of ['actor','event','tool_call','loop_item','loop_block','loop_domain']) {
      await c.query(schema.match(new RegExp(`CREATE TABLE public\\.${name} \\([\\s\\S]*?\\n\\);`))[0]);
    }
    await c.query('alter table tool_call add primary key(idempotency_key)');
    // This fixture tests registration with the existing guard, whose behavior
    // is covered by the canonical reference-monitor database acceptance gate.
    await c.query(`create schema ops;
      create function ops.scac_reference_monitor_guard() returns trigger
      language plpgsql as $$ begin return new; end $$;`);
    await c.query(readFileSync(path.join(root,'migrations/0847_feature_switches.sql'),'utf8'));
    const guards = (await c.query(`select tgname,tgtype,tgenabled from pg_trigger
      where tgrelid='public.feature_switch'::regclass
      and tgfoid='ops.scac_reference_monitor_guard()'::regprocedure order by tgname`)).rows;
    assert.deepEqual(guards.map(row=>[row.tgname,row.tgtype,row.tgenabled]),[
      ['scac_reference_monitor_guard_row',31,'O'],
      ['scac_reference_monitor_guard_truncate',34,'O'],
    ],'switch writes must register row and truncate guards with the existing reference monitor');
    await c.query('grant select,insert on event,tool_call to carr_writer');
    await c.query('grant select on actor to carr_writer');
    await c.query("insert into actor(id,slug,display_name,kind) values($1,'joe','Synthetic Joe','human')",[actor.id]);
    const invoke = async (verb,args,as=actor) => {
      await c.query('begin');
      try {
        await c.query('set local role carr_writer');
        const result = await TOOLS[verb].handler(c,as,args);
        await c.query('commit'); return result;
      } catch (error) { await c.query('rollback'); throw error; }
    };
    await c.query("grant select,insert,update on loop_item,loop_block,loop_domain to carr_writer");
    await c.query("insert into loop_domain(slug,label,sort) values('system','System',1)");
    await c.query("insert into loop_block(id,kind,block_key,rel_path,seq,col_order,renders_closed,created_by,updated_by) values(gen_random_uuid(),'open_loop','backlog','synthetic',1,array[]::text[],false,$1,$1)",[actor.id]);
    const input = { idempotency_key:randomUUID(), name:'synthetic-feature', description:'Show a synthetic test control',
      base_version:0, default_enabled:false, audience:'joe', owner:'claude', expected_removal_on:'2099-01-01' };
    const created = await invoke('set-feature-switch',input);
    assert.equal(created.switch.version,1);
    assert.ok(created.switch.created_at);
    const flip = { idempotency_key:randomUUID(), name:input.name, base_version:1, enabled:true, audience:'team' };
    const changed = await invoke('flip-feature-switch',flip);
    assert.equal(changed.switch.version,2);
    assert.equal((await invoke('flip-feature-switch',flip)).replayed,true);
    await assert.rejects(invoke('flip-feature-switch',{ ...flip,idempotency_key:randomUUID() }), /feature_switch_version_conflict/);
    const read = await invoke('list-feature-switches',{name:input.name}, { ...actor,slug:'dell' });
    assert.equal(read.schema,'feature-switches.v1');
    assert.equal(read.switches[0].available,true);
    assert.equal(read.history.length,2);
    assert.equal(read.history[0].actor,'joe');
    assert.equal(read.history[0].before.enabled,null);
    assert.equal(read.history[0].after.enabled,true);
    assert.ok(read.history[0].recorded_at);
    await requireFeature(c,{...actor,slug:'dell'},input.name);
    await invoke('flip-feature-switch',{...flip, idempotency_key:randomUUID(),base_version:2,enabled:false});
    await assert.rejects(requireFeature(c,{...actor,slug:'dell'},input.name), /feature_disabled/);
    await invoke('set-feature-switch',{...input,idempotency_key:randomUUID(),base_version:3,expected_removal_on:'2000-01-01'});
    const health=await invoke('check-feature-switches',{idempotency_key:randomUUID()});
    assert.equal(health.overdue.length,1);
    assert.equal(health.overdue[0].owner,'claude');
    assert.match(health.overdue[0].line,/on breach:.*owner claude.*retire.*verify.*auto-clear/);
    const again=await invoke('check-feature-switches',{idempotency_key:randomUUID()});
    assert.equal(again.overdue[0].loop_id,health.overdue[0].loop_id);
    const retired = await invoke('set-feature-switch',{ ...input,idempotency_key:randomUUID(),base_version:4,retired:true });
    assert.ok(retired.switch.retired_at);
    const cleared=await invoke('check-feature-switches',{idempotency_key:randomUUID()});
    assert.equal(cleared.overdue.length,0);
    assert.deepEqual(cleared.cleared,[health.overdue[0].loop_id]);
    assert.equal((await invoke('list-feature-switches',{name:input.name})).switches[0].available,false);
  } finally {
    if(c) await c.end();
    if(admin) await admin.end();
    if(running) execFileSync(path.join(bin,'pg_ctl'),['-D',dir,'-m','immediate','-w','stop'],{stdio:'pipe'});
    await releaseBudget();
  }
});
