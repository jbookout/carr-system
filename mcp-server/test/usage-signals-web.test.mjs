import test from 'node:test';
import assert from 'node:assert/strict';
import { createDealroomHandler, isDealroomRequest } from '../src/dealroom-web.js';

const origin = 'https://app.doctorcre.com';
const kv = () => {
  const values = new Map();
  return { values, async get(key, options) { const value = values.get(key); return value == null ? null : options?.type === 'json' ? JSON.parse(value) : value; }, async put(key, value) { values.set(key, value); }, async delete(key) { values.delete(key); } };
};
test('usage route requires cookie identity and the existing origin, metadata and CSRF guard', async () => {
  const env = { DEALROOM_HOST: 'app.doctorcre.com', GOOGLE_CLIENT_ID: 'fixture', GOOGLE_CLIENT_SECRET: 'fixture', OAUTH_KV: kv() };
  const handler = createDealroomHandler({ exchangeGoogleCodeFn: async () => ({ id_token: 'fixture' }), verifyGoogleIdTokenFn: async () => ({ email: 'joe.bookout.carr.us@gmail.com', email_verified: true, sub: 'fixture' }) });
  const request = (path, options) => handler.fetch(new Request(`${origin}${path}`, options), env, {});
  assert.equal(isDealroomRequest(new Request(`${origin}/api/v1/usage-signals`), env), true);
  assert.equal((await request('/api/v1/usage-signals')).status, 401);
  const start = await request('/auth/login');
  const state = new URL(start.headers.get('location')).searchParams.get('state');
  const cookies = response => response.headers.getSetCookie();
  const login = await request(`/auth/callback?state=${state}&code=fixture`, { headers: { cookie: cookies(start)[0].split(';')[0] } });
  const cookie = cookies(login).find(value => value.startsWith('__Host-dealroom_session=')).split(';')[0];
  const bootstrap = await (await request('/api/v1/usage-signals/session', { headers: { cookie } })).json();
  assert.equal(bootstrap.partner, 'joe');
  const body = JSON.stringify({ event_name: 'chat_sent', screen: 'home', partner: 'joe', release_sha: 'a'.repeat(40), timestamp: new Date().toISOString() });
  const headers = { cookie, origin, 'sec-fetch-site': 'same-origin', 'content-type': 'application/json', 'x-carr-csrf': bootstrap.csrf_token };
  for (const changed of [{ origin: 'https://other.doctorcre.com' }, { 'sec-fetch-site': 'cross-site' }, { 'x-carr-csrf': 'invalid' }]) assert.equal((await request('/api/v1/usage-signals', { method: 'POST', body, headers: { ...headers, ...changed } })).status, 403);
  assert.equal((await request('/api/v1/usage-signals', { method: 'POST', body, headers })).status, 202);
  assert.equal([...env.OAUTH_KV.values.keys()].filter(key => key.includes(':event:')).length, 1);
});
