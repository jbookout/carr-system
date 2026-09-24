import test from 'node:test';
import assert from 'node:assert/strict';
import { dealReadingState, readDealWithJev } from '../src/jev-deal-reading.js';

const record = (text) => ({
  phase: 'negotiation', type: 'renewal', next_step: '', last_touch: '2026-09-19',
  thread: [{ text }], activities: [], next_actions: [], negotiation_rounds: [], critical_dates: [], premises: [],
});
const eligible = (text) => ({ ...record(text),
  next_step: 'The tenant must decide whether to accept the landlord counter and reply to CARR.',
  next_actions: [{ status: 'open', description: 'Ask the tenant to review the revised rent and approve a response to the landlord.' }],
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
  const answer = await readDealWithJev(eligible(text), {
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
  const answer = await readDealWithJev(eligible('The landlord sent a counter with a revised rental rate and a shorter response period. '.repeat(3)), {
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

test('verbose irrelevant note abstains before vendor egress', async () => {
  let calls = 0;
  const answer = await readDealWithJev(record('A'.repeat(220)), {
    apiKey: 'synthetic', fetchImpl: async () => { calls++; throw Error('unexpected vendor call'); },
  });
  assert.equal(answer.judged, false);
  assert.equal(answer.reason, 'insufficient_recorded_evidence');
  assert.equal(calls, 0);
});

test('current operational facts survive an older long thread', () => {
  const found = dealReadingState({ ...record('old note '.repeat(80)),
    thread: Array.from({ length: 40 }, (_, i) => ({ text: `Old note ${i}: tenant once discussed a generic location.` })),
    activities: [{ summary: 'Today the landlord sent a revised proposal', detail: 'The tenant asked CARR to compare it with the offer.' }],
    next_actions: [{ status: 'open', description: 'Client must approve the response to the revised proposal today.' }],
    negotiation_rounds: [{ note: 'Current counter proposes the revised rate and term.' }],
    critical_dates: [{ note: 'The response must arrive before the offer expires.' }],
    premises: [{ label: 'Recorded property' }],
  }, new Date('2026-09-21'));
  const history = found.state.deal.history.join('\n');
  assert.match(history, /Today the landlord sent a revised proposal/);
  assert.match(history, /Client must approve the response/);
  assert.match(history, /Current counter proposes/);
  assert.match(history, /offer expires/);
  assert.equal(found.state.deal.premises_recorded, 1);
  assert.equal(found.state.deal.negotiation_rounds_recorded, 1);
});

test('current structured deal facts can support a short explicit next step', async () => {
  const deal = { ...record(''),
    next_step: 'Tenant must review the current landlord proposal and tell CARR whether to send a counter before Friday.',
    premises: [{ label: 'Private address', address: 'Private address' }],
    negotiation_rounds: [{ round_no: 2, side: 'landlord', proposed_on: '2026-09-19',
      expires_on: '2026-09-25', rate_amount: 999, note: '' }],
    critical_dates: [{ kind: 'response', due_on: '2026-09-25', status: 'open', note: '' }],
    documents: [{ sent_status: 'sent', note: 'Private document' }],
  };
  let payload;
  const answer = await readDealWithJev(deal, { apiKey: 'synthetic',
    fetchImpl: async (_url, init) => {
      payload = JSON.parse(init.body);
      return { ok: true, json: async () => ({ model: 'jev-test', answers: {
        movement: { score: 3 }, waiting_on: { choice: 'client' }, silence_is_bad: { noul: 0.2 },
      } }) };
    },
  });
  assert.equal(answer.judged, true);
  assert.equal(payload.state.deal.latest_negotiation.side, 'landlord');
  assert.equal(payload.state.deal.active_dates[0].due_on, '2026-09-25');
  assert.equal(payload.state.deal.sent_documents_recorded, 1);
  assert.equal(JSON.stringify(payload).includes('Private address'), false);
  assert.equal(JSON.stringify(payload).includes('999'), false);
  assert.equal(JSON.stringify(payload).includes('Private document'), false);
});

test('old completed dates cannot hide a current open deadline', () => {
  const found = dealReadingState({ ...eligible('The landlord has sent a proposal for the tenant to review.'),
    critical_dates: [
      ...Array.from({ length: 4 }, (_, i) => ({ kind: 'old', due_on: `2025-01-0${i + 1}`,
        status: 'completed', note: 'Historic deadline was already satisfied.' })),
      { kind: 'offer_expiry', due_on: '2026-09-25', status: 'open',
        note: 'The current offer expires unless the tenant replies.' },
    ],
  }, new Date('2026-09-21'));
  assert.deepEqual(found.state.deal.active_dates.map(d => d.due_on), ['2026-09-25']);
  assert.match(found.state.deal.history.join('\n'), /current offer expires/);
  assert.doesNotMatch(found.state.deal.history.join('\n'), /Historic deadline/);
});
