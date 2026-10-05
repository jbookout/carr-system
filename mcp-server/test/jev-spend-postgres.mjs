import test from 'node:test';
import assert from 'node:assert/strict';
import pg from 'pg';
import { readFileSync } from 'node:fs';
import { checkJevSpend, holdJevBilling, spendPolicy, costPolicy, SPEND_LOCK, attributedJevState } from '../src/jev-spend-authority.js';
import { jevAskBinding, reserveJevCallAttempt } from '../src/jev-call-receipt.js';

const host = process.env.JEV_TEST_PG_SOCKET;
if (!host?.startsWith('/')) throw Error('disposable PostgreSQL fixture required');
const pool = new pg.Pool({ host, user: 'carr_ci', database: 'postgres', max: 12 });
const actor = { id: '10000000-0000-4000-8000-000000000001' };
const who = { caller: 'jev_deal_read', session_id: 'native-session-123', job_id: null, unattended: false };
await pool.query(`create table tool_call (
  idempotency_key text primary key, verb text not null, actor_id uuid not null,
  request_hash text not null, response jsonb not null,
  created_at timestamptz not null default now())`);
await pool.query(readFileSync(new URL('../../migrations/0825_jev_spend_attempt_index.sql', import.meta.url), 'utf8'));
// The receipt sink is a database fixture; admission/insertion uses the
// production reservation function, and no provider is configured.
await pool.query(`create schema ops;
  create function ops.record_jev_call_receipt(text,text,text[],text[],text,text,
    text,text,text,text,jsonb,jsonb,uuid,text,text) returns table(receipt_id uuid)
    language sql as 'select gen_random_uuid()'`);
test.after(() => pool.end());

async function reserve(attribution = who, registry = spendPolicy, cost = costPolicy) {
  const c = await pool.connect();
  try {
    await c.query('begin');
    const site = await checkJevSpend(c, attribution, registry, cost);
    const key = crypto.randomUUID();
    await c.query(`insert into tool_call values ($1,'ask-jev-attempt',$2,$1,$3,clock_timestamp())`,
      [key, actor.id, { jev_site: site.caller, jev_caller: attribution.caller }]);
    await c.query('commit');
    return { key, receipt_id: key };
  } catch (e) { await c.query('rollback'); throw e; }
  finally { c.release(); }
}

test('empty ledger admits; unrelated records and old UTC-day attempts do not spend', async () => {
  await reserve();
  await pool.query('truncate tool_call');
  await pool.query(`insert into tool_call values ('old','ask-jev-attempt',$1,'old','{}',
    (date_trunc('day',clock_timestamp() at time zone 'UTC') - interval '1 second') at time zone 'UTC')`, [actor.id]);
  await reserve(who, spendPolicy, { ...costPolicy, daily_paid_call_cap: 1 });
  await assert.rejects(() => reserve(who, spendPolicy, { ...costPolicy, daily_paid_call_cap: 1 }),
    e => e.payload?.error === 'daily_paid_call_cap');
});

test('12 concurrent callers from different sites share exactly 3 global slots', async () => {
  await pool.query('truncate tool_call');
  const results = await Promise.allSettled(Array.from({ length: 12 }, (_, i) => reserve(
    { ...who, caller: i % 2 ? 'jev_deal_read' : 'adhoc:bounded-review' }, spendPolicy,
    { ...costPolicy, daily_paid_call_cap: 3 })));
  assert.equal(results.filter(r => r.status === 'fulfilled').length, 3);
  assert.equal(results.filter(r => r.status === 'rejected' && r.reason.payload?.error === 'daily_paid_call_cap').length, 9);
});

test('wildcard callers share site capacity; global site counters survive completed receipts', async () => {
  await pool.query('truncate tool_call');
  const registry = structuredClone(spendPolicy);
  Object.assign(registry.sites.find(s => s.caller === 'adhoc:*'), { hourly_budget: 1, daily_budget: 2 });
  const attempt = await reserve({ ...who, caller: 'adhoc:first' }, registry);
  await pool.query(`update tool_call set response = response || '{"cache_hit":true,"settled_by":"answer"}'::jsonb
    where idempotency_key=$1`, [attempt.key]);
  await assert.rejects(() => reserve({ ...who, caller: 'adhoc:second' }, registry),
    e => e.payload?.error === 'site_hourly_budget');
  await reserve(who, registry);
});

test('old untagged attempts are conservatively charged to every site', async () => {
  await pool.query('truncate tool_call');
  await pool.query(`insert into tool_call values ('legacy','ask-jev-attempt',$1,'legacy','{}',clock_timestamp())`, [actor.id]);
  const registry = structuredClone(spendPolicy);
  registry.sites.find(s => s.caller === 'jev_deal_read').hourly_budget = 1;
  await assert.rejects(() => reserve(who, registry), e => e.payload?.error === 'site_hourly_budget');
});

test('402 or billing error stops every site for 1 hour through one durable marker', async () => {
  for (const [status, body] of [[402, 'out of credit'], [403, 'insufficient_credit'], [429, 'billing account suspended']]) {
    await pool.query('truncate tool_call');
    let sent = 0;
    const c = await pool.connect();
    try {
      const ask = jevAskBinding({ TYPESAFE_API_KEY: 'offline' }, async () => {
        sent++; return new Response(body, { status });
      }, { cache: null, reserveAttempt: () => reserve(), billingHold: () => holdJevBilling(c, actor) });
      await assert.rejects(() => ask({ state: 'question', model: 'jev-1.13.0', questions: {} }),
        e => e.payload?.status === status);
      await assert.rejects(() => reserve({ ...who, caller: 'adhoc:other-route' }),
        e => e.payload?.error === 'vendor_credit_exhausted');
      const row = (await pool.query(`select count(*)::int n,
        min(extract(epoch from ((response->>'resets_at')::timestamptz-clock_timestamp())))::int seconds
        from tool_call where verb='jev-billing-hold'`)).rows[0];
      assert.equal(row.n, 1);
      assert.ok(row.seconds >= 3595 && row.seconds <= 3600);
      assert.equal(sent, 1);
      await pool.query(`update tool_call set response=jsonb_build_object('resets_at',clock_timestamp()-interval '1 second')
        where verb='jev-billing-hold'`);
      await reserve({ ...who, caller: 'adhoc:recovered' });
    } finally { c.release(); }
  }
});

test('each retry reserves; an exhausted retry budget prevents the second vendor fetch', async () => {
  await pool.query('truncate tool_call');
  let sent = 0;
  const ask = jevAskBinding({ TYPESAFE_API_KEY: 'offline' }, async () => {
    sent++; return new Response('throttled', { status: 429, headers: { 'retry-after': '0' } });
  }, { cache: null, sleep: async () => {}, billingHold: async () => assert.fail('not billing'),
    reserveAttempt: () => reserve(who, spendPolicy, { ...costPolicy, daily_paid_call_cap: 1 }) });
  await assert.rejects(() => ask({ state: 'question', model: 'jev-1.13.0', questions: {} }),
    e => e.payload?.error === 'daily_paid_call_cap');
  assert.equal(sent, 1);
});


test('production reservation records the post-wait clock across a delayed lock', async () => {
  await pool.query('truncate tool_call');
  const blocker = await pool.connect();
  const worker = await pool.connect();
  try {
    await blocker.query('begin');
    await blocker.query('select pg_advisory_xact_lock(hashtextextended($1,0))', [SPEND_LOCK]);
    const pid = (await worker.query('select pg_backend_pid() pid')).rows[0].pid;
    const pending = reserveJevCallAttempt(worker, { ...actor, slug: 'synthetic' }, {
      session_id: who.session_id, purpose: 'call', facets: [], model: 'jev-1.13.0',
      state: attributedJevState('bounded question', who),
      questions: { q: { type: 'noul', instructions: 'uncertain?' } },
    });
    // Authenticate that the production reservation actually waits on the lock.
    let waiting = false;
    for (let i = 0; i < 100; i++) {
      const row = (await blocker.query('select wait_event from pg_stat_activity where pid=$1', [pid])).rows[0];
      if (row?.wait_event === 'advisory') { waiting = true; break; }
      await new Promise(resolve => setTimeout(resolve, 5));
    }
    assert.equal(waiting, true);
    const threshold = (await blocker.query('select clock_timestamp() stamp')).rows[0].stamp;
    await blocker.query('commit');
    const attempt = await pending;
    const row = (await worker.query('select created_at >= $2::timestamptz after_wait, response from tool_call where idempotency_key=$1',
      [attempt.key, threshold])).rows[0];
    assert.equal(row.after_wait, true);
    assert.equal(row.response.jev_site, who.caller);
  } finally {
    await blocker.query('rollback');
    blocker.release(); worker.release();
  }
});

test('an abandoned lock owner causes bounded refusal without a reservation', async () => {
  await pool.query('truncate tool_call');
  const blocker = await pool.connect();
  try {
    await blocker.query('begin');
    await blocker.query('select pg_advisory_xact_lock(hashtextextended($1,0))', [SPEND_LOCK]);
    const started = performance.now();
    await assert.rejects(() => reserve(), e => e.payload?.error === 'jev_spend_authority_unavailable');
    assert.ok(performance.now() - started < 2500, 'lock wait escaped its one-second limit');
    assert.equal((await blocker.query('select count(*)::int n from tool_call')).rows[0].n, 0);
  } finally { await blocker.query('rollback'); blocker.release(); }
});

test('admission uses the partial index with a large unrelated idempotency ledger', async () => {
  await pool.query('truncate tool_call');
  await pool.query(`insert into tool_call select 'unrelated-'||n, 'other-tool', $1, 'hash', '{}',
    clock_timestamp() from generate_series(1,20000) n`, [actor.id]);
  await reserve();
  await pool.query('analyze tool_call');
  const c = await pool.connect();
  try {
    await c.query('begin');
    let plan;
    await checkJevSpend({query: async (sql, params) => {
      if (sql.includes('with clock as'))
        plan = (await c.query('explain (format json) '+sql, params)).rows[0]['QUERY PLAN'];
      return c.query(sql, params);
    }}, who);
    assert.ok(JSON.stringify(plan).includes('tool_call_jev_attempt_created_idx'), 'admission ignored partial index');
    await c.query('rollback');
  } finally { c.release(); }
});
