import test from 'node:test';
import assert from 'node:assert/strict';
import { usageResponse } from '../src/usage-signals.js';
import { RETENTION_SECONDS } from '../src/usage-contract.v1.js';

const origin = 'https://app.doctorcre.com';
const sha = 'a'.repeat(40);
const now = Date.parse('2026-10-05T12:00:00.000Z');
const session = { actor: { slug: 'joe' }, csrfToken: 'synthetic-token' };
class Kv {
  values = new Map();
  writes = [];
  async get(key, options) { const v = this.values.get(key); return v == null ? null : options?.type === 'json' ? JSON.parse(v.value) : v.value; }
  async put(key, value, options) { this.writes.push({ key, value, options }); this.values.set(key, { value, ...options }); }
  async list({ prefix, cursor }) {
    const keys = [...this.values.entries()].filter(([key]) => key.startsWith(prefix)).map(([name, v]) => ({ name, metadata: v.metadata }));
    const offset = Number(cursor || 0);
    return { keys: keys.slice(offset, offset + 2), list_complete: offset + 2 >= keys.length, cursor: String(offset + 2) };
  }
}
const event = extra => ({ event_name: 'screen_viewed', screen: 'home', partner: 'joe', release_sha: sha, timestamp: new Date(now).toISOString(), ...extra });
const guard = async request => ({ value: await request.json() });
const post = body => new Request(`${origin}/api/v1/usage-signals`, { method: 'POST', body: JSON.stringify(body), headers: { 'content-type': 'application/json' } });
test('server rejects client data, free text, extra fields, forged partners and malformed timestamps', async () => {
  const env = { OAUTH_KV: new Kv() };
  for (const body of [event({ client: { name: 'Synthetic Client', lease: 'Synthetic Lease' } }), event({ screen: 'Synthetic Client' }), event({ event_name: 'Lease for Synthetic Client' }), event({ partner: 'dell' }), event({ timestamp: '2026-02-30T12:00:00.000Z' }), event({ release_sha: 'Synthetic Client' })]) {
    const response = await usageResponse(post(body), env, session, { now: () => now }, guard);
    assert.equal(response.status, 400);
    assert.equal((await response.text()).includes('Synthetic'), false);
  }
  assert.deepEqual(env.OAUTH_KV.writes, []);
  assert.equal((await usageResponse(post(event()), env, session, { now: () => now }, guard)).status, 202);
  const stored = env.OAUTH_KV.writes.find(write => write.key.includes(':event:'));
  assert.equal(stored.options.expirationTtl, RETENTION_SECONDS);
  assert.deepEqual(JSON.parse(stored.value), event());
  assert.equal(env.OAUTH_KV.writes.every(write => write.options?.expirationTtl === RETENTION_SECONDS), true);
});

test('weekly summary separates partners, paginates, records last use and avoids false never-used after retention', async () => {
  const env = { OAUTH_KV: new Kv() };
  for (const [partner, time, screen] of [['joe', now, 'home'], ['joe', now - 8 * 86400000, 'deals'], ['dell', now - 1000, 'home']]) {
    await usageResponse(post(event({ partner, screen, timestamp: new Date(time).toISOString() })), env, { ...session, actor: { slug: partner } }, { now: () => time }, guard);
  }
  let releaseStartedAt = new Date(now - 9 * 86400000).toISOString();
  const get = () => usageResponse(new Request(`${origin}/api/v1/usage-signals?release_sha=${sha}&release_started_at=${releaseStartedAt}`), env, session, { now: () => now }, guard);
  let body = await (await get()).json();
  assert.equal(body.coverage, 'since_release');
  assert.deepEqual(body.features.find(row => row.id === 'home:view').uses, { joe: 1, dell: 1 });
  const deals = body.features.find(row => row.id === 'deals:view');
  assert.deepEqual(deals.uses, { joe: 0, dell: 0 });
  assert.equal(deals.last_used.joe, '2026-09-27T12:00:00.000Z');
  assert.equal(deals.never_used.joe, false);
  assert.equal(deals.never_used.dell, true);
  releaseStartedAt = new Date(now - 181 * 86400000).toISOString();
  body = await (await get()).json();
  assert.equal(body.coverage, 'retained_window');
  assert.equal(body.features.find(row => row.id === 'tours:view').never_used.joe, null);
});

test('server off switch suppresses storage and guard refusals stay refusals', async () => {
  const env = { OAUTH_KV: new Kv(), DOCTORCRE_USAGE_CAPTURE_ENABLED: 'false' };
  const response = await usageResponse(post(event()), env, session, { now: () => now }, guard);
  assert.deepEqual(await response.json(), { captured: false });
  assert.deepEqual(env.OAUTH_KV.writes, []);
  const refused = await usageResponse(post(event()), env, session, { now: () => now }, async () => ({ error: new Response('', { status: 403 }) }));
  assert.equal(refused.status, 403);
});

test('concurrent captures keep distinct events and server time determines retention', async () => {
  const env = { OAUTH_KV: new Kv() };
  await Promise.all([1, 2].map(() => usageResponse(post(event({ timestamp: new Date(now - 60000).toISOString() })), env, session, { now: () => now }, guard)));
  const events = env.OAUTH_KV.writes.filter(write => write.key.includes(':event:'));
  assert.equal(new Set(events.map(write => write.key)).size, 2);
  assert.equal(events.every(write => JSON.parse(write.value).timestamp === new Date(now).toISOString() && write.options.expirationTtl === 15552000), true);
});

test('missing release provenance leaves never-used history unknown', async () => {
  const response = await usageResponse(new Request(`${origin}/api/v1/usage-signals?release_sha=${sha}`), { OAUTH_KV: new Kv() }, session, { now: () => now }, guard);
  const body = await response.json();
  assert.equal(body.coverage, 'retained_window');
  assert.equal(body.features[0].never_used.joe, null);
});

test('weekly uses and last use survive app releases while never-used describes the current release', async () => {
  const env = { OAUTH_KV: new Kv() }, previous = now - 86400000;
  await usageResponse(post(event({ release_sha: 'b'.repeat(40), screen: 'deals', timestamp: new Date(previous).toISOString() })), env, session, { now: () => previous }, guard);
  const response = await usageResponse(new Request(`${origin}/api/v1/usage-signals?release_sha=${sha}&release_started_at=${new Date(now - 1000).toISOString()}`), env, session, { now: () => now }, guard);
  const row = (await response.json()).features.find(row => row.id === 'deals:view');
  assert.deepEqual(row.uses, { joe: 1, dell: 0 });
  assert.equal(row.last_used.joe, '2026-10-04T12:00:00.000Z');
  assert.equal(row.never_used.joe, true);
});
