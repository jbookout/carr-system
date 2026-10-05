import test from 'node:test';
import assert from 'node:assert/strict';
import { jevAskBinding } from '../src/jev-call-receipt.js';

test('a Worker vendor binding without spend authority refuses before fetch', async () => {
  let fetched = 0;
  const warnings = [];
  const warning = console.warn;
  console.warn = line => warnings.push(JSON.parse(line));
  const ask = jevAskBinding({ TYPESAFE_API_KEY: 'fixture' }, async () => {
    fetched++;
    return new Response(JSON.stringify({ model: 'jev-1.13.0', answers: {} }));
  }, { cache: null });
  await assert.rejects(() => ask({ state: 'bounded question', model: 'jev-1.13.0', questions: {} }),
    e => e.payload?.error === 'jev_spend_authority_unavailable');
  console.warn = warning;
  assert.equal(fetched, 0);
  assert.equal(warnings[0]?.error, 'jev_spend_authority_unavailable');
});

const attribution = { caller: 'jev_deal_read', session_id: 'worker-deal-123', job_id: null, unattended: false };
const stats = { day_used: 0, hour_used: 0, site_day: 0, site_hour: 0,
  resets_day: '2026-10-05T00:00:00Z', resets_hour: '2026-10-04T23:00:00Z', hold_until: null };

async function check(overrides = {}, who = attribution, costOverrides = {}) {
  const { checkJevSpend, spendPolicy, costPolicy } = await import('../src/jev-spend-authority.js');
  const queried = [];
  const client = { query: async (sql, params) => {
    queried.push({ sql, params });
    return { rows: sql.includes('day_used') ? [{ ...stats, ...overrides }] : [] };
  } };
  const site = await checkJevSpend(client, who, spendPolicy, { ...costPolicy, ...costOverrides });
  return { site, queried };
}

test('caller attribution and fixture/unattended policies are mandatory', async () => {
  for (const [who, code] of [
    [null, 'unattributed_call'], [{ ...attribution, caller: 'missing' }, 'unregistered_caller'],
    [{ ...attribution, session_id: '' }, 'unattributed_call'],
    [{ ...attribution, session_id: 'fixture-selftest' }, 'fixture_offline'],
    [{ ...attribution, caller: 'jev_code_review', unattended: true }, 'unattended_worker_off'],
  ]) await assert.rejects(() => check({}, who), e => e.payload?.error === code);
});

test('daily, hourly, site-day and site-hour caps refuse at the boundary', async () => {
  for (const [counts, code] of [
    [{ day_used: 500 }, 'daily_paid_call_cap'], [{ hour_used: 200 }, 'hourly_paid_call_cap'],
    [{ site_day: 60 }, 'site_daily_budget'], [{ site_hour: 30 }, 'site_hourly_budget'],
    [{ hold_until: '2026-10-04T23:30:00Z' }, 'vendor_credit_exhausted'],
  ]) await assert.rejects(() => check(counts), e => e.payload?.error === code && Boolean(e.payload.resets_at));
  const { queried, site } = await check();
  assert.equal(site.caller, 'jev_deal_read');
  assert.match(queried[2].sql, /pg_advisory_xact_lock/);
});

test('global daily cap cannot exceed hard 1000 or use malformed config', async () => {
  for (const cap of [1001, -1, 1.5, '500', null])
    await assert.rejects(() => check({}, attribution, { daily_paid_call_cap: cap }),
      e => e.payload?.error === 'call_site_registry_invalid');
  await check({ day_used: 999 }, attribution, { daily_paid_call_cap: 1000 });
});


test('admission bounds lock and statement waits before taking the global lock', async () => {
  const { queried } = await check();
  assert.match(queried[0].sql, /set local lock_timeout = '1s'/);
  assert.match(queried[1].sql, /set local statement_timeout = '3s'/);
  assert.match(queried[2].sql, /pg_advisory_xact_lock/);
});

test('the admission ledger has a partial time index migration', async () => {
  const { readdirSync, readFileSync } = await import('node:fs');
  const dir = new URL('../../migrations/', import.meta.url);
  const sql = readdirSync(dir).filter(name => name.endsWith('.sql'))
    .map(name => readFileSync(new URL(name, dir), 'utf8')).join('\n');
  assert.ok(/create index[^;]+on (?:public\.)?tool_call\s*\(created_at\)\s*where verb = 'ask-jev-attempt'/i.test(sql), 'missing admission index');
});

test('a cache-only capability probe advertises spend authority without vendor fetch', async () => {
  let fetched = 0;
  const ask = jevAskBinding({ TYPESAFE_API_KEY: 'fixture' }, async () => { fetched++; }, {cache:null});
  await assert.rejects(ask({state:'probe',questions:{},transport_mode:'cache_only'}),
    e => e.payload?.error === 'jev_cache_miss' && e.payload.spend_authority === 'carr-jev-spend/v1');
  assert.equal(fetched, 0);
});
