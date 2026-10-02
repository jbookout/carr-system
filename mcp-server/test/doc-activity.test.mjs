import test from 'node:test';
import assert from 'node:assert/strict';
import { docActivityEntry, docActivityTools } from '../src/doc-activity.js';
const tools = docActivityTools({ ToolError: class extends Error { constructor(p) { super(p.error); this.payload = p; } } });
const actor = { slug: 'joe', human: true };
const event = { id: '10000000-0000-4000-8000-000000000001', recorded_at: '2026-10-01T15:00:00Z',
  occurred_at: '2026-10-01T14:00:00Z', subject_id: '20000000-0000-4000-8000-000000000001', subject_type: 'deal',
  actor_name: 'Doc', partner: 'joe', record_name: 'Demo Practice', verb: 'patch-deal-field', field: 'phase',
  old_value: { phase: 'research' }, new_value: { phase: 'legal' }, has_old_value: true, is_latest: true,
  agent_rationale: 'Demo accepted proposal', human_quote: null };

test('exact event identity, original entry, known inverse and missing facts are preserved', () => {
  const row = docActivityEntry(event);
  assert.equal(row.undo.event_id, event.id); assert.equal(row.before, 'research');
  assert.equal(row.evidence.reason, event.agent_rationale); assert.equal(row.evidence.at, event.occurred_at);
  assert.equal(docActivityEntry({ ...event, is_latest: false }).undo.state, 'superseded');
  assert.equal(docActivityEntry({ ...event, has_old_value: false }).undo.state, 'unavailable');
  assert.equal(docActivityEntry({ ...event, verb: 'revert-deal-field' }).undo.state, 'undone');
  assert.equal(docActivityEntry({ ...event, field: null, irreversible: true }).undo.state, 'irreversible');
  assert.equal(docActivityEntry({ ...event, field: null, subject_type: 'lead', verb: 'create-lead', agent_rationale: null }).why, null);
  const safe = docActivityEntry({ ...event, old_value: { phase: { __sensitive_ref: 'hidden' } } });
  assert.equal(safe.before, null); assert.ok(!JSON.stringify(safe).includes('hidden'));
  assert.equal(safe.undo.state, 'unavailable');
});
test('closed read schema, runtime input checks, dates and authority cannot be supplied', async () => {
  const tool = tools['read-doc-activity'];
  assert.equal(tool.inputSchema.additionalProperties, false); assert.equal(tool.write, undefined);
  for (const args of [null, [], { cursor: null }, { cursor: [] }, { actor: 'dell' }, { partner: '' }, { partner: 'other' }, { record_type: "x' or true" },
    { limit: 0 }, { limit: 101 }, { since: 'yesterday' }, { cursor: { at: 'bad', id: event.id } },
    { since: '2026-10-02T00:00:00Z', until: '2026-10-01T00:00:00Z' }]) {
    await assert.rejects(tool.handler({ query() { assert.fail('invalid input reached database'); } }, actor, args), /input_invalid/);
  }
});
test('parameterized tenant/personal scope, cause classification and keyset paging cover every type', async () => {
  const calls = [];
  const c = { async query(sql, params) {
    calls.push({ sql, params });
    if (sql.includes(':types')) return { rows: [{ subject_type: 'deal' }, { subject_type: 'draft' }] };
    if (sql.includes(':clock')) return { rows: [{ as_of: event.recorded_at }] };
    return { rows: [event, { ...event, id: '10000000-0000-4000-8000-000000000002' }] };
  } };
  const args = { partner: 'dell', record_type: 'draft', limit: 1,
    cursor: { at: event.recorded_at, id: event.id }, since: '2026-10-01T00:00:00Z' };
  const result = await tools['read-doc-activity'].handler(c, actor, args);
  assert.deepEqual(result.record_types, ['deal', 'draft']); assert.equal(result.entries.length, 1);
  assert.deepEqual(result.next_cursor, { at: event.recorded_at, id: event.id });
  assert.equal(result.schema_version, 'doc-activity.v1');
  assert.deepEqual(calls[1].params, ['carr-internal', 'joe-personal', 'dell', 'draft', args.since, null, event.recorded_at, event.id, 2]);
  assert.match(calls[1].sql, /personal_scope='none' or e.personal_scope=\$2/);
  assert.match(calls[1].sql, /e.cause in/); assert.ok(!calls[1].sql.includes('human_stated'));
  assert.match(calls[1].sql, /order by e.recorded_at desc,e.id desc/);
  assert.match(calls[1].sql, /not exists.*select 1 from event newer/s);
});
