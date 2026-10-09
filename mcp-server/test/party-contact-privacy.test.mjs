import test from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { TOOLS } from '../src/tools.js';
import { withPostgresFixture } from './helpers/disposable-postgres.mjs';

const protectedPhone = '(202) 555-0123';

function client(phones = [protectedPhone], configured = []) {
  return { query: async (sql, params) => {
    if (/select phone from actor/.test(sql)) return { rows: phones.map(phone => ({ phone })) };
    if (/select value from system_config/.test(sql)) return { rows: [{ value: configured }] };
    if (/select merged_into from party/.test(sql)) return { rows: [{ merged_into: null }] };
    if (/select version from party/.test(sql)) return { rows: [{ version: 1 }] };
    if (/select request_hash/.test(sql)) return { rows: [] };
    if (/select subject_type, subject_id from v_ref_index/.test(sql))
      return { rows: [{ subject_type: 'party', subject_id: params[0] }] };
    throw new Error('past-contact-guard');
  } };
}

for (const name of ['add-party', 'update-party-contact']) {
  test(`${name} preserves configured placeholders absent from actor contact data`, async () => {
    const args = { idempotency_key: randomUUID(), name: 'Synthetic person', phone: '+1 202-555-0123',
      party: randomUUID(), base_version: 1, fields: { phone: '+1 202-555-0123' }, source: 'Synthetic proof' };
    await assert.rejects(() => TOOLS[name].handler(client([], ['2025550123']),
      { id: randomUUID(), slug: 'synthetic' }, args), error => {
      assert.equal(error.payload?.error, 'placeholder_phone');
      return true;
    });
  });
  test(`${name} refuses a private actor contact without exposing it in the response`, async () => {
    const args = { idempotency_key: randomUUID(), name: 'Synthetic person', phone: '+1 202-555-0123',
      party: randomUUID(), base_version: 1, fields: { cell: '+1 202-555-0123' }, source: 'Synthetic proof' };
    await assert.rejects(() => TOOLS[name].handler(client(), { id: randomUUID(), slug: 'synthetic' }, args), error => {
      assert.equal(error.payload?.error, 'placeholder_phone');
      assert.equal(JSON.stringify(error.payload).includes('555'), false);
      return true;
    });
  });
  test(`${name} lets an unrelated contact pass the private actor guard`, async () => {
    const args = { idempotency_key: randomUUID(), name: 'Synthetic person', phone: '202-555-0199',
      party: randomUUID(), base_version: 1, fields: { phone: '202-555-0199' }, source: 'Synthetic proof' };
    await assert.rejects(() => TOOLS[name].handler(client(), { id: randomUUID(), slug: 'synthetic' }, args), /past-contact-guard/);
  });
}

test('public declarations and contact guards contain no literal personal phone number', () => {
  for (const name of ['party-tools', 'verb-support']) {
    const source = readFileSync(new URL(`../src/${name}.js`, import.meta.url), 'utf8');
    const numbers = source.match(/\b\d{3}[-.]\d{3}[-.]\d{4}\b|\b\d{10}\b/g) || [];
    assert.equal(numbers.length, 0, `${name} contains a literal personal phone number`);
  }
});

test('public duplicate-organization examples use synthetic business names', () => {
  const party = readFileSync(new URL('../src/party-tools.js', import.meta.url), 'utf8');
  const support = readFileSync(new URL('../src/verb-support.js', import.meta.url), 'utf8');
  assert.equal(/\/\/ Synthetic [^\n]+ as 10,/.test(party), true);
  assert.equal(/\/\/ Synthetic [^\n]+ — 17 rows,/.test(support), true);
});

test('public lead source uses a synthetic person in its failure example', () => {
  const source = readFileSync(new URL('../src/lead-tools.js', import.meta.url), 'utf8');
  const examples = [...source.matchAll(/creating Dr\. ([^'\n]+)'s lead/g)].map(match => match[1]);
  assert.equal(examples.length, 1, 'the lead failure example is covered');
  assert.equal(examples.every(name => name === 'Example'), true, 'lead examples must be synthetic');
});

test('contact refusals read canonical private actor phones on PostgreSQL', () =>
  withPostgresFixture({ tables: ['actor', 'party', 'tool_call', 'system_config'].map(name => `public.${name}`) }, async ({ c, command }) => {
    const actor = { id: randomUUID(), slug: 'joe', human: true };
    const party = randomUUID();
    await c.query("insert into actor(id,slug,kind,display_name,phone) values($1,'joe','human','Synthetic partner',$2)", [actor.id, protectedPhone]);
    await c.query("insert into party(id,kind,name,created_by,updated_by) values($1,'person','Synthetic person',$2,$2)", [party, actor.id]);
    await c.query("create view v_ref_index as select 'party'::text subject_type,id subject_id from party");
    for (const name of ['add-party', 'update-party-contact']) {
      const args = { idempotency_key: randomUUID(), ...(name === 'add-party'
        ? { name: 'Synthetic person', phone: '+1 202-555-0123' }
        : { party, base_version: 1, fields: { phone: '+1 202-555-0123' }, source: 'Synthetic proof' }) };
      await assert.rejects(() => command(c, actor, name, args), error => error.payload?.error === 'placeholder_phone');
    }
    assert.equal((await c.query('select count(*)::int n from tool_call')).rows[0].n, 0);
    assert.equal((await c.query('select phone from party where id=$1', [party])).rows[0].phone, null);
  }));

test('private placeholder configuration is seeded from approved rule data without public contact literals', () =>
  withPostgresFixture({
    tables: ['actor', 'party', 'tool_call', 'system_config'].map(name => `public.${name}`),
    setup: `alter table system_config add primary key(key);
      create table rule(id uuid primary key, status text, statement text);`,
  }, async ({ c, command }) => {
    const migration = readFileSync(new URL('../../migrations/0854_private_contact_placeholder_config.sql', import.meta.url), 'utf8');
    await c.query("insert into rule values('54e2bcb9-0000-4000-8000-000000000000','active',$1)",
      [`Known placeholders: the phone ${protectedPhone}. Existing contact ${protectedPhone}.`]);
    await c.query("insert into system_config(key,value) values('contacts.protected_phone_numbers','[\"2025550199\"]')");
    await c.query(migration);
    await c.query(migration);
    const configured = (await c.query("select value from system_config where key='contacts.protected_phone_numbers'")).rows[0].value;
    assert.deepEqual(configured.sort(), ['2025550123', '2025550199']);
    const actor = { id: randomUUID(), slug: 'dell', human: true };
    const party = randomUUID();
    await c.query("insert into actor(id,slug,kind,display_name) values($1,'dell','human','Synthetic partner')", [actor.id]);
    await c.query("insert into party(id,kind,name,created_by,updated_by) values($1,'person','Synthetic person',$2,$2)", [party, actor.id]);
    await c.query("create view v_ref_index as select 'party'::text subject_type,id subject_id from party");
    for (const name of ['add-party', 'update-party-contact']) {
      const args = { idempotency_key: randomUUID(), ...(name === 'add-party'
        ? { name: 'Synthetic person', phone: '+1 202-555-0123' }
        : { party, base_version: 1, fields: { cell: '+1 202-555-0123' }, source: 'Synthetic proof' }) };
      await assert.rejects(() => command(c, actor, name, args), error => error.payload?.error === 'placeholder_phone');
    }
    assert.equal((await c.query('select count(*)::int n from tool_call')).rows[0].n, 0);
  }));
