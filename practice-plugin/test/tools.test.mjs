import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import Ajv from 'ajv';
import { TOOLS, callTool, INFORMATIONAL_PAGE } from '../src/tools.mjs';
import cases from './review-cases.json' with { type: 'json' };

const ajv = new Ajv({ strict: true });
const byName = Object.fromEntries(TOOLS.map(t => [t.name, t]));
const result = c => callTool(c.tool, c.input);
const urlPattern = /https?:\/\/[^\s"<>]+/g;
const strings = value => typeof value === 'string' ? [value]
  : value && typeof value === 'object' ? Object.values(value).flatMap(strings) : [];

for (const c of cases) {
  test(`review golden: ${c.name}`, () => {
    const r = result(c);
    assert.equal(Boolean(r.isError), c.error);
    assert.deepEqual(r.structuredContent, c.expected);
    assert.equal(r.content[0].text, JSON.stringify(c.expected));
  });
}

for (const tool of TOOLS) {
  test(`${tool.name}: closed input and output schemas`, () => {
    const input = ajv.compile(tool.inputSchema);
    const output = ajv.compile(tool.outputSchema);
    const c = cases.find(c => c.tool === tool.name && !c.error);
    assert.ok(c, 'positive schema fixture required');
    assert.ok(input(c.input), JSON.stringify(input.errors));
    assert.ok(output(result(c).structuredContent), JSON.stringify(output.errors));
    assert.equal(input({ ...c.input, contact: 'synthetic-forbidden-field' }), false);
    assert.equal(input({}), false);
    assert.equal(output({ ...result(c).structuredContent, arbitrary: 1 }), false);
    assert.deepEqual(tool.annotations, {
      readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false,
    });
    assert.match(tool.description, /educational|informational/i);
  });
}

test('no URL in any success or refusal except informational-page constant', () => {
  for (const c of cases) {
    const urls = strings(result(c)).flatMap(value => value.match(urlPattern) || []);
    assert.ok(urls.every(url => url === INFORMATIONAL_PAGE), c.name);
    if (c.tool !== 'get_broker_search_help' || c.error) assert.equal(urls.length, 0);
  }
  const hostile = callTool('https://synthetic.invalid', { target: 'https://synthetic.invalid' });
  assert.equal((JSON.stringify(hostile).match(urlPattern) || []).length, 0);
  assert.match(byName.get_broker_search_help.description, /explicit.*actual.*search/i);
});

test('space planner rejects incompatible and unbounded rooms', () => {
  const base = cases[0].input;
  for (const input of [
    { ...base, providers: 0 }, { ...base, providers: 1.5 }, { ...base, providers: Infinity },
    { ...base, operatories: -1 }, { ...base, operatories: 101 },
    { ...base, operatories: 0 }, { ...base, exam_rooms: 1 },
    { ...base, practice_type: 'medical' }, { ...base, practice_type: 'veterinary' },
  ]) assert.equal(callTool('plan_practice_space', input).isError, true);
  for (const practice_type of ['medical', 'veterinary']) {
    const r = callTool('plan_practice_space', { practice_type, providers: 2, operatories: 0, exam_rooms: 4 });
    assert.ok(!r.isError);
    const data = r.structuredContent;
    for (const side of ['low', 'high']) {
      const roomSum = data.rooms.reduce((sum, row) => sum + row.total_square_feet[side], 0);
      assert.equal(roomSum, data.usable_square_feet[side]);
      assert.ok(data.rentable_square_feet[side] >= roomSum);
    }
  }
});

test('occupancy components sum and lease types change allocations', () => {
  for (const lease_type of ['full_service', 'modified_gross', 'triple_net']) {
    const r = callTool('estimate_occupancy_cost', { ...cases[1].input, lease_type }).structuredContent;
    for (const side of ['low', 'high']) {
      assert.equal(r.annual_cost[side], r.components.reduce((n, c) => n + c.annual_cost[side], 0));
      assert.equal(r.monthly_cost[side], Math.round(r.annual_cost[side] / 12 * 100) / 100);
    }
    assert.match(r.source.method, /assum|sensitivity/i);
    assert.match(r.notice, /estimate/i);
    assert.ok(r.source.data_date < r.source.retrieved_date);
  }
  for (const square_feet of [0, -1, 100001, NaN, '2000']) {
    assert.equal(callTool('estimate_occupancy_cost', { ...cases[1].input, square_feet }).isError, true);
  }
});

test('lease/buy checks zero-interest, all-cash, long holds and finite arithmetic', () => {
  const base = cases[2].input;
  const zero = callTool('compare_lease_buy', { ...base, interest_rate_percent: 0 }).structuredContent;
  assert.equal(zero.monthly_loan_payment, 2000);
  const cash = callTool('compare_lease_buy', { ...base, down_payment_percent: 100 }).structuredContent;
  assert.equal(cash.monthly_loan_payment, 0);
  assert.equal(cash.remaining_loan_balance, 0);
  const long = callTool('compare_lease_buy', { ...base, holding_years: 40 }).structuredContent;
  assert.equal(long.remaining_loan_balance, 0);
  for (const field of ['down_payment_percent', 'interest_rate_percent', 'closing_cost_percent', 'sale_cost_percent']) {
    assert.equal(callTool('compare_lease_buy', { ...base, [field]: -1 }).isError, true);
  }
});

test('source table is dated, source cited and contains no licensed data rows', () => {
  const data = JSON.parse(fs.readFileSync(new URL('../data/rates.json', import.meta.url)));
  assert.match(data.source.url, /^https:\/\/www\.downtownmobile\.org\//);
  assert.match(data.source.authorization, /aggregate factual/i);
  assert.equal(data.source.observed_annual_rate, 21.1);
  assert.match(data.source.data_date, /^\d{4}-\d{2}-\d{2}$/);
  assert.match(data.source.retrieved_date, /^\d{4}-\d{2}-\d{2}$/);
  assert.ok(!Object.hasOwn(data, 'listings'));
  for (const row of Object.values(data.lease_types)) {
    for (const component of row) {
      assert.ok(component.basis);
      assert.ok(component.annual_rate.low <= component.annual_rate.high);
    }
  }
});
