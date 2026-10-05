import { FEATURES, RETENTION_SECONDS, USAGE_SCHEMA, validUsageEvent } from './usage-contract.v1.js';

export const USAGE_PATH = '/api/v1/usage-signals';
const DAY = 86400000;
const json = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json', 'cache-control': 'no-store' } });
const invalid = () => json({ error: 'invalid_usage_event' }, 400);

export async function usageResponse(request, env, session, dependencies, guardPost) {
  const url = new URL(request.url), now = dependencies.now();
  const enabled = env.DOCTORCRE_USAGE_CAPTURE_ENABLED !== 'false';
  if (!['joe', 'dell'].includes(session.actor.slug)) return json({ error: 'forbidden' }, 403);
  if (url.pathname === `${USAGE_PATH}/session`) {
    if (request.method !== 'GET' || url.search) return json({ error: 'invalid_usage_request' }, 400);
    return json({ schema: USAGE_SCHEMA, enabled, partner: session.actor.slug, csrf_token: session.csrfToken });
  }
  if (url.pathname !== USAGE_PATH) return json({ error: 'not_found' }, 404);
  if (request.method === 'POST') {
    if (url.search) return invalid();
    const guarded = await guardPost(request, env, session, { readBody: true, maxBytes: 512 });
    if (guarded.error) return guarded.error;
    const event = guarded.value;
    if (!validUsageEvent(event) || event.partner !== session.actor.slug || Math.abs(Date.parse(event.timestamp) - now) > 5 * 60000) return invalid();
    if (!enabled) return json({ captured: false }, 202);
    event.timestamp = new Date(now).toISOString();
    const prefix = `doctorcre_usage:v1:${url.origin}:${event.release_sha}:`;
    try {
      await env.OAUTH_KV.put(`${prefix}event:${new Date(now).toISOString()}:${crypto.randomUUID()}`, JSON.stringify(event), {
        expirationTtl: RETENTION_SECONDS, metadata: event,
      });
      try {
        const releaseKey = `${prefix}release`;
        if (!await env.OAUTH_KV.get(releaseKey)) await env.OAUTH_KV.put(releaseKey, JSON.stringify({ first_observed_at: new Date(Math.floor(now / DAY) * DAY).toISOString() }));
      } catch { /* Missing release metadata makes never-used history unknown. */ }
      return json({ captured: true }, 202);
    } catch { return json({ error: 'usage_unavailable' }, 503); }
  }
  if (request.method !== 'GET') return json({ error: 'method_not_allowed' }, 405);
  const sha = url.searchParams.get('release_sha');
  if (!/^[a-f0-9]{40}$/.test(sha || '') || [...url.searchParams.keys()].length !== 1) return json({ error: 'invalid_usage_request' }, 400);
  const prefix = `doctorcre_usage:v1:${url.origin}:${sha}:`;
  try {
    const release = await env.OAUTH_KV.get(`${prefix}release`, { type: 'json' });
    const complete = !!release && Date.parse(release.first_observed_at) > now - RETENTION_SECONDS * 1000;
    const rows = FEATURES.map(feature => ({ ...feature, uses: { joe: 0, dell: 0 }, last_used: { joe: null, dell: null }, never_used: { joe: complete ? true : null, dell: complete ? true : null } }));
    const features = new Map(rows.map(row => [`${row.event_name}:${row.screen || '*'}`, row]));
    let cursor;
    do {
      const page = await env.OAUTH_KV.list({ prefix: `${prefix}event:`, ...(cursor ? { cursor } : {}) });
      for (const { metadata: event } of page.keys) {
        if (!validUsageEvent(event) || event.release_sha !== sha) continue;
        const time = Date.parse(event.timestamp);
        if (time > now || time <= now - RETENTION_SECONDS * 1000) continue;
        for (const row of [features.get(`${event.event_name}:${event.screen}`), features.get(`${event.event_name}:*`)].filter(Boolean)) {
          row.never_used[event.partner] = false;
          if (!row.last_used[event.partner] || event.timestamp > row.last_used[event.partner]) row.last_used[event.partner] = event.timestamp;
          if (time >= now - 7 * DAY) row.uses[event.partner]++;
        }
      }
      cursor = page.list_complete ? null : page.cursor;
      if (!page.list_complete && !cursor) throw new Error('Incomplete list');
    } while (cursor);
    return json({ schema: USAGE_SCHEMA, release_sha: sha, enabled, observed_at: new Date(now).toISOString(),
      week_start: new Date(now - 7 * DAY).toISOString(), first_observed_at: release?.first_observed_at || null,
      coverage: complete ? 'since_capture_started' : release ? 'retained_window' : 'awaiting_capture', features: rows });
  } catch { return json({ error: 'usage_unavailable' }, 503); }
}
