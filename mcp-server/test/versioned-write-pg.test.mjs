import test from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { readFileSync, mkdtempSync, mkdirSync, renameSync } from 'node:fs';
import path from 'node:path';
import pg from 'pg';
import { acquirePostgresFixtureGroup } from './helpers/disposable-postgres.mjs';
import { executeRegisteredTool } from '../src/tools.js';

async function fixture(fn) {
  const bin = execFileSync('/opt/homebrew/opt/postgresql@17/bin/pg_config', ['--bindir'], { encoding: 'utf8' }).trim();
  const dir = mkdtempSync('/tmp/carr-c4-pg-');
  const release = await acquirePostgresFixtureGroup();
  const clients = [];
  let started = false;
  try {
    execFileSync(path.join(bin, 'initdb'), ['-D', dir, '-U', 'fixture', '--auth=trust', '--no-locale'], { stdio: 'pipe' });
    execFileSync(path.join(bin, 'pg_ctl'), ['-D', dir, '-l', path.join(dir, 'server.log'), '-o', `-k ${dir} -h '' -c timezone=UTC`, '-w', 'start'], { stdio: 'pipe' });
    started = true;
    const connect = async () => {
      const c = new pg.Client({ host: dir, user: 'fixture', database: 'postgres' });
      await c.connect(); clients.push(c); return c;
    };
    const c = await connect();
    const schema = readFileSync(new URL('../../db/schema.sql', import.meta.url), 'utf8');
    for (const name of ['actor', 'lead', 'lead_stage', 'lead_lane', 'event', 'tool_call'])
      await c.query(schema.match(new RegExp(`CREATE TABLE public\\.${name} \\([\\s\\S]*?\\n\\);`))[0]);
    await c.query(schema.match(/CREATE FUNCTION public.trg_touch_row\(\)[\s\S]*?end \$\$;/)[0]);
    await c.query(`alter table tool_call add primary key(idempotency_key);
      create trigger lead_touch before update on lead for each row execute function trg_touch_row();
      create view v_ref_index as select 'lead'::text subject_type,id subject_id,registry_ref ref from lead;
      insert into lead_stage(slug,label) values ('new','New'),('engaged','Engaged'),('do_not_contact','Do not contact'),('archived','Archived');
      insert into lead_lane(slug,label) values ('primary','Primary');`);
    const actor = { id: randomUUID(), slug: 'joe', human: true };
    const lead = randomUUID();
    await c.query("insert into actor(id,slug,kind,display_name) values($1,'joe','human','Synthetic partner')", [actor.id]);
    await c.query("insert into lead(id,party_id,registry_ref,stage,notes,created_by,updated_by) values($1,$2,'L-1','new','Original synthetic note',$3,$3)", [lead, randomUUID(), actor.id]);
    const command = async (client, extra = {}) => {
      await client.query('begin');
      try {
        const value = await executeRegisteredTool(client, actor, 'update-lead', { lead, idempotency_key: randomUUID(), base_version: 1, fields: { notes: 'Revised synthetic note' }, ...extra });
        await client.query('commit'); return value;
      } catch (e) { await client.query('rollback'); throw e; }
    };
    await fn({ c, connect, actor, lead, command });
  } finally {
    for (const c of clients) await c.end();
    try { if (started) execFileSync(path.join(bin, 'pg_ctl'), ['-D', dir, '-m', 'fast', '-w', 'stop'], { stdio: 'pipe' }); }
    finally { await release(); mkdirSync('/tmp/_to_delete', { recursive: true }); renameSync(dir, path.join('/tmp/_to_delete', path.basename(dir))); }
  }
}

test('registered lead patch preserves response, field events, version refusals and replay', () => fixture(async ({ c, lead, command }) => {
  const key = randomUUID();
  const result = await command(c, { idempotency_key: key, fields: { notes: 'Revised synthetic note', lane: 'primary', unknown: 'ignored' } });
  assert.deepEqual(result, { ok: true, updated: ['notes', 'lane'] });
  assert.deepEqual(await command(c, { idempotency_key: key, fields: { notes: 'Revised synthetic note', lane: 'primary', unknown: 'ignored' } }), { replayed: true, ...result });
  const events = (await c.query('select field,old_value,new_value,cause from event where subject_id=$1 order by field', [lead])).rows;
  assert.deepEqual(events, [
    { field: 'lane', old_value: { lane: null }, new_value: { lane: 'primary' }, cause: 'automation_job' },
    { field: 'notes', old_value: { notes: 'Original synthetic note' }, new_value: { notes: 'Revised synthetic note' }, cause: 'automation_job' },
  ]);
  await assert.rejects(() => command(c), e => e.payload?.error === 'version_conflict' && e.payload.current_version === 2);
  await assert.rejects(() => command(c, { idempotency_key: key, fields: { notes: 'Different intent' } }), e => e.payload?.error === 'key_reuse');
}));

test('same-key concurrent versioned patches return the committed response', () => fixture(async ({ c, connect, actor, lead, command }) => {
  const other = await connect();
  const args = { lead, base_version: 1, fields: { notes: 'Revised synthetic note' }, idempotency_key: randomUUID() };
  await c.query('begin');
  const first = await executeRegisteredTool(c, actor, 'update-lead', args);
  const pending = command(other, args);
  const observer = await connect();
  const secondPid = (await observer.query("select pid from pg_stat_activity where backend_type='client backend' and pid<>pg_backend_pid() and pid<>$1", [c.processID])).rows;
  assert.ok(secondPid.some(row => row.pid === other.processID));
  // The observer uses fresh snapshots outside the held writer transaction.
  let waited = false;
  for (let n = 0; n < 200; n++) {
    const waits = await observer.query("select wait_event_type from pg_stat_activity where pid=$1", [other.processID]);
    if (waits.rows[0]?.wait_event_type === 'Lock') { waited = true; break; }
    await new Promise(resolve => setTimeout(resolve, 5));
  }
  await c.query('commit');
  assert.deepEqual(await pending, { replayed: true, ...first });
  assert.equal(waited, true, 'the second writer must reach its lock while the first transaction remains open');
}));
