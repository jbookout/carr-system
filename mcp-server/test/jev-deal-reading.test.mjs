import test from 'node:test';
import assert from 'node:assert/strict';
import { dealReadingState, readDealWithJev } from '../src/jev-deal-reading.js';

const record = (text) => ({
  phase: 'negotiation', type: 'renewal', next_step: '', last_touch: '2026-09-19',
  thread: [{ text }], activities: [], next_actions: [], negotiation_rounds: [], critical_dates: [],
});

test('thin Deal Room evidence abstains without a vendor call', async () => {
  let calls = 0;
  const answer = await readDealWithJev(record('Brief note'), {
    apiKey: 'synthetic', fetchImpl: async () => { calls++; throw Error('unexpected call'); },
  });
  assert.equal(answer.judged, false);
  assert.equal(answer.reason, 'insufficient_recorded_evidence');
  assert.equal(calls, 0);
});

test('one bounded request returns typed advice without source text', async () => {
  const text = 'The landlord sent a counter; the tenant must decide whether to accept the revised rate. '.repeat(4);
  let payload;
  const answer = await readDealWithJev(record(text), {
    apiKey: 'synthetic', now: new Date('2026-09-21'),
    fetchImpl: async (_url, init) => {
      payload = JSON.parse(init.body);
      return { ok: true, json: async () => ({ model: 'jev-1.13.0', answers: {
        movement: { score: 3.1 }, waiting_on: { choice: 'client', confidence: 0.91 },
        silence_is_bad: { noul: 0.18 },
      } }) };
    },
  });
  assert.equal(payload.model, 'jev-latest');
  assert.equal(Object.keys(payload.questions).length, 3);
  assert.equal(answer.judged, true);
  assert.equal(answer.movement_rung, 4);
  assert.equal(answer.waiting_on, 'client');
  assert.equal(answer.silence_is_bad, 0.18);
  assert.equal(JSON.stringify(answer).includes(text), false);
});

test('invalid vendor answers abstain', async () => {
  const answer = await readDealWithJev(record('A'.repeat(220)), {
    apiKey: 'synthetic', fetchImpl: async () => ({ ok: true, json: async () => ({
      answers: { movement: { score: 12 }, waiting_on: { choice: 'unknown' }, silence_is_bad: { noul: 1.2 } },
    }) }),
  });
  assert.equal(answer.judged, false);
  assert.equal(answer.reason, 'invalid_jev_answer');
});

test('evidence count uses source text rather than phase labels', () => {
  const found = dealReadingState(record(''), new Date('2026-09-21'));
  assert.equal(found.evidenceChars, 0);
  assert.equal(found.state.deal.days_since_last_recorded_touch, 2);
});
