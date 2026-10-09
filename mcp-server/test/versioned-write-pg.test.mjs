import test from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { withLeadVerbFixture as fixture } from './helpers/lead-verb-fixture.mjs';
import { executeRegisteredTool } from '../src/tools.js';

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

test('stage review preserves evidence attribution and refuses cross-lead evidence', () => fixture(async ({ c, actor, lead, command }) => {
  const activity = randomUUID();
  await c.query("insert into activity(id,lead_id,occurred_at,kind,connected,actor_id,summary) values($1,$2,'2026-10-01T10:00:00Z','call',true,$3,'Synthetic call')", [activity, lead, actor.id]);
  await assert.rejects(() => command(c, { fields: { stage: 'engaged' }, stage_review: { reason: 'Synthetic evidence', evidence_ids: [randomUUID()] } }), e => e.payload?.error === 'stage_evidence_mismatch');
  const args = { fields: { stage: 'engaged' }, stage_review: { reason: 'Reply received', evidence_ids: [activity], human_quote: 'Synthetic confirmation' }, idempotency_key: randomUUID() };
  const result = await command(c, args);
  assert.deepEqual(result, { ok: true, updated: ['stage'] });
  const event = (await c.query("select new_value,cause,human_quote,agent_rationale from event where subject_id=$1 and field='stage'", [lead])).rows[0];
  assert.deepEqual(event, { new_value: { stage: 'engaged', stage_review: { ...args.stage_review, evidence_date: '2026-10-01T10:00:00.000Z' } }, cause: 'human_stated', human_quote: 'Synthetic confirmation', agent_rationale: 'Reply received' });
  assert.deepEqual(await command(c, args), { replayed: true, ...result });
}));

test('Undo accepts only the latest automatic move and preserves correction attribution', () => fixture(async ({ c, actor, lead, command }) => {
  const event = randomUUID();
  await c.query(`create view v_lead_stage_transition as select subject_id lead_id,id event_id,
    row_number() over(order by recorded_at) mutation_order,cause='automation_job' automatic,
    old_value->>'stage' prior_stage,new_value->>'stage' stage from event where field='stage'`);
  await c.query("insert into event(id,occurred_at,actor_id,verb,subject_type,subject_id,field,old_value,new_value,cause) values($1,now(),$2,'advance-leads','lead',$3,'stage','{\"stage\":\"engaged\"}','{\"stage\":\"new\"}','automation_job')", [event, actor.id, lead]);
  const args = { fields: { stage: 'engaged' }, stage_review: { reason: 'Undo synthetic move', evidence_ids: [], undo_event_id: event, human_quote: 'Restore previous stage' } };
  await assert.rejects(() => command(c, { ...args, stage_review: { ...args.stage_review, undo_event_id: randomUUID() } }), e => e.payload?.error === 'undo_changed');
  await assert.rejects(() => command(c, { ...args, fields: { stage: 'new' } }), e => e.payload?.error === 'undo_changed');
  await command(c, args);
  const recorded = (await c.query("select cause,new_value from event where verb='update-lead' and subject_id=$1", [lead])).rows[0];
  assert.equal(recorded.cause, 'human_correction');
  assert.equal(recorded.new_value.stage_review.undo_event_id, event);
}));

test('field and authority guards preserve refusal payloads without a write', () => fixture(async ({ c, command }) => {
  await assert.rejects(() => command(c, { expected_actor: 'dell' }), e => e.payload?.error === 'account_changed');
  await assert.rejects(() => command(c, { fields: { stage: 'missing' } }), e => e.payload?.error === 'unknown_stage' && e.payload.valid.includes('new'));
  await assert.rejects(() => command(c, { fields: { identity: 'ignored' } }), e => e.payload?.error === 'no_updatable_fields');
  await assert.rejects(() => command(c, { fields: { stage: 'do_not_contact' } }), e => e.payload?.error === 'do_not_contact_requires_suppression');
  assert.equal((await c.query('select count(*)::int n from event')).rows[0].n, 0);
}));
