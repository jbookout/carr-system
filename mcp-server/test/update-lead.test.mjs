import test from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { withLeadVerbFixture as fixture } from './helpers/lead-verb-fixture.mjs';

async function seed(c, lead, stage, suppressed = false) {
  return (await c.query('update lead set stage=$1,suppressed=$2 where id=$3 returning version', [stage, suppressed, lead])).rows[0].version;
}
const refuses = error => e => e.payload?.error === error;

test('registered update-lead moves an existing nurture lead into an active deal with a field event', () => fixture(async ({ c, lead, command }) => {
  const base_version = await seed(c, lead, 'nurture_drip');
  assert.deepEqual(await command(c, { base_version, fields: { stage: 'active_deal' } }), { ok: true, updated: ['stage'] });
  assert.equal((await c.query('select stage from lead where id=$1', [lead])).rows[0].stage, 'active_deal');
  assert.deepEqual((await c.query("select old_value,new_value from event where subject_id=$1 and field='stage'", [lead])).rows,
    [{ old_value: { stage: 'nurture_drip' }, new_value: { stage: 'active_deal' } }]);
}));

test('unknown stage lists valid slugs and leaves the lead unchanged', () => fixture(async ({ c, lead, command }) => {
  await assert.rejects(() => command(c, { fields: { stage: 'won' } }), e => e.payload?.error === 'unknown_stage' && e.payload.got === 'won' && e.payload.valid.includes('closed_won'));
  assert.equal((await c.query('select version,stage from lead where id=$1', [lead])).rows[0].version, 1);
}));

test('stale version refuses without overwriting the intervening change', () => fixture(async ({ c, lead, command }) => {
  await seed(c, lead, 'nurture_drip');
  await assert.rejects(() => command(c, { fields: { stage: 'active_deal' } }), e => e.payload?.error === 'version_conflict' && e.payload.current_version === 2);
  assert.equal((await c.query('select stage from lead where id=$1', [lead])).rows[0].stage, 'nurture_drip');
}));

test('identity and conversion fields are outside the registered patch interface', () => fixture(async ({ c, command }) => {
  await assert.rejects(() => command(c, { fields: { party_id: randomUUID(), client_id: randomUUID() } }), e => e.payload?.error === 'no_updatable_fields' && !['party_id', 'client_id', 'registry_ref'].some(f => e.payload.allowed.includes(f)));
  assert.equal((await c.query('select count(*)::int n from event')).rows[0].n, 0);
}));

test('a resolved vendor is refused as not a lead', () => fixture(async ({ c, command }) => {
  const vendor = randomUUID();
  await c.query('create table synthetic_vendor(id uuid,ref text)');
  await c.query("insert into synthetic_vendor values($1,'V-SYNTHETIC')", [vendor]);
  await c.query("create or replace view v_ref_index as select 'lead'::text subject_type,id subject_id,registry_ref ref from lead union all select 'vendor',id,ref from synthetic_vendor");
  await assert.rejects(() => command(c, { lead: 'V-SYNTHETIC' }), refuses('not_a_lead'));
}));

test('multiple business fields commit together with one event per changed field', () => fixture(async ({ c, lead, command }) => {
  const fields = { stage: 'active_deal', segment: 'dental', notes: 'Synthetic transition' };
  assert.deepEqual(await command(c, { fields }), { ok: true, updated: Object.keys(fields) });
  assert.deepEqual((await c.query('select stage,segment,notes from lead where id=$1', [lead])).rows[0], fields);
  assert.deepEqual((await c.query('select field from event where subject_id=$1 order by field', [lead])).rows.map(e => e.field), Object.keys(fields).sort());
}));

test('do-not-contact requires suppression in the same patch', () => fixture(async ({ c, lead, command }) => {
  await assert.rejects(() => command(c, { fields: { stage: 'do_not_contact' } }), refuses('do_not_contact_requires_suppression'));
  await command(c, { fields: { stage: 'do_not_contact', suppressed: true } });
  assert.deepEqual((await c.query('select stage,suppressed from lead where id=$1', [lead])).rows[0], { stage: 'do_not_contact', suppressed: true });
}));

test('only a partner may clear a standing suppression instruction', () => fixture(async ({ c, actor, lead, command }) => {
  const base_version = await seed(c, lead, 'do_not_contact', true);
  const fields = { stage: 'engaged', suppressed: false };
  await assert.rejects(() => command(c, { base_version, fields }, { ...actor, slug: 'synthetic-agent', human: false }), refuses('suppression_clear_requires_human'));
  await command(c, { base_version, fields });
  assert.deepEqual((await c.query('select stage,suppressed from lead where id=$1', [lead])).rows[0], fields);
}));

test('archive transitions require a partner; agent notes on archived leads remain editable', () => fixture(async ({ c, actor, lead, command }) => {
  const machine = { ...actor, slug: 'synthetic-agent', human: false };
  await assert.rejects(() => command(c, { fields: { stage: 'archived' } }, machine), refuses('archive_requires_partner'));
  const base_version = await seed(c, lead, 'archived');
  await assert.rejects(() => command(c, { base_version, fields: { stage: 'new' } }, machine), refuses('archive_requires_partner'));
  assert.deepEqual(await command(c, { base_version }, machine), { ok: true, updated: ['notes'] });
}));
