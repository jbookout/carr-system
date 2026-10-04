// One authority: the Worker serializes admission and durable attempt insertion
// in the same writer transaction. Every vendor attempt (including retries and
// failures) consumes capacity; cache hits and policy refusals do not.
import { ToolError } from './tool-error.js';
import spendPolicy from '../../ops/config/jev-call-sites.v1.json' with { type: 'json' };
import costPolicy from '../../ops/config/jev-cost-guard.v1.json' with { type: 'json' };
export { spendPolicy, costPolicy };

export const SPEND_LOCK = 'carr-jev-spend-v1';
export const BILLING_MARKER = 'jev-spend:billing-hold';
const FIELDS = ['attribution', 'caller', 'daily_budget', 'hourly_budget', 'owner',
  'runs_in', 'sources', 'trigger', 'unattended', 'value'].sort().join(',');

export function refuseJevSpend(error, caller, resets_at = null) {
  const payload = { error, caller: typeof caller === 'string' ? caller : null, resets_at };
  console.warn(JSON.stringify({ schema: 'carr.jev-spend-refusal/v1', ...payload }));
  throw new ToolError(payload);
}

export function jevCallSite(attribution, registry = spendPolicy, cost = costPolicy) {
  const invalid = () => refuseJevSpend('call_site_registry_invalid', attribution?.caller);
  if (registry?.schema !== 'carr-jev-call-sites/v1' || !Array.isArray(registry.sites) ||
      !Number.isInteger(registry.hourly_paid_call_cap) || registry.hourly_paid_call_cap < 0 ||
      !Number.isInteger(cost?.daily_paid_call_cap) || cost.daily_paid_call_cap < 0 ||
      cost.daily_paid_call_cap > 1000) invalid();
  const names = new Set();
  for (const site of registry.sites) {
    if (!site || Object.keys(site).sort().join(',') !== FIELDS ||
        typeof site.caller !== 'string' || !/^[a-z0-9_.:-]+\*?$/.test(site.caller) ||
        names.has(site.caller) || !['session', 'session_or_job'].includes(site.attribution) ||
        !['off', 'allowed'].includes(site.unattended) ||
        !Number.isInteger(site.hourly_budget) || site.hourly_budget < 0 ||
        !Number.isInteger(site.daily_budget) || site.daily_budget < site.hourly_budget ||
        !Array.isArray(site.sources) || !site.sources.length ||
        site.sources.some(source => typeof source !== 'string' || !source.trim()) ||
        ['trigger', 'runs_in', 'owner', 'value'].some(k => typeof site[k] !== 'string' || !site[k])) invalid();
    names.add(site.caller);
  }
  if (!attribution || typeof attribution.caller !== 'string' ||
      typeof attribution.unattended !== 'boolean') refuseJevSpend('unattributed_call', attribution?.caller);
  const caller = attribution.caller;
  const site = registry.sites.find(s => s.caller === caller) || registry.sites
    .filter(s => s.caller.endsWith('*') && caller.startsWith(s.caller.slice(0, -1)))
    .sort((a, b) => b.caller.length - a.caller.length)[0];
  if (!site) refuseJevSpend('unregistered_caller', caller);
  const session = typeof attribution.session_id === 'string' && attribution.session_id.trim();
  const job = typeof attribution.job_id === 'string' && attribution.job_id.trim();
  if (!session && !(site.attribution === 'session_or_job' && job)) refuseJevSpend('unattributed_call', caller);
  if ([session, job].some(id => id && /(?:^|[-_:])(fixture|selftest|test)(?:$|[-_:])/i.test(id)))
    refuseJevSpend('fixture_offline', caller);
  if (attribution.unattended && site.unattended === 'off') refuseJevSpend('unattended_worker_off', caller);
  return site;
}

export async function checkJevSpend(client, attribution, registry = spendPolicy, cost = costPolicy) {
  const site = jevCallSite(attribution, registry, cost);
  // Separate statements matter under READ COMMITTED: the count's snapshot is
  // taken AFTER the prior lock owner commits its reservation, not before wait.
  let row;
  try {
    await client.query('select pg_advisory_xact_lock(hashtextextended($1,0))', [SPEND_LOCK]);
    row = (await client.query(`
      with clock as (select clock_timestamp() as now), attempts as (
        select t.* from tool_call t, clock c
        where t.verb = 'ask-jev-attempt'
          and t.created_at >= date_trunc('day', c.now at time zone 'UTC') at time zone 'UTC'
      ) select count(attempts.idempotency_key)::int as day_used,
        count(attempts.idempotency_key) filter (where created_at >= date_trunc('hour', c.now at time zone 'UTC') at time zone 'UTC')::int as hour_used,
        count(attempts.idempotency_key) filter (where response->>'jev_site' = $1 or response->>'jev_site' is null)::int as site_day,
        count(attempts.idempotency_key) filter (where (response->>'jev_site' = $1 or response->>'jev_site' is null)
          and created_at >= date_trunc('hour', c.now at time zone 'UTC') at time zone 'UTC')::int as site_hour,
        to_char(date_trunc('day', c.now at time zone 'UTC') + interval '1 day', 'YYYY-MM-DD"T"HH24:MI:SS"Z"') as resets_day,
        to_char(date_trunc('hour', c.now at time zone 'UTC') + interval '1 hour', 'YYYY-MM-DD"T"HH24:MI:SS"Z"') as resets_hour,
        (select response->>'resets_at' from tool_call where idempotency_key = $2
          and (response->>'resets_at')::timestamptz > c.now) as hold_until
      from clock c left join attempts on true group by c.now`, [site.caller, BILLING_MARKER])).rows[0];
  } catch { refuseJevSpend('jev_spend_authority_unavailable', attribution.caller); }
  if (!row || ['day_used', 'hour_used', 'site_day', 'site_hour'].some(k => !Number.isInteger(row[k]) || row[k] < 0))
    refuseJevSpend('jev_spend_authority_unavailable', attribution.caller);
  if (row.hold_until) refuseJevSpend('vendor_credit_exhausted', attribution.caller, row.hold_until);
  if (row.day_used >= cost.daily_paid_call_cap) refuseJevSpend('daily_paid_call_cap', attribution.caller, row.resets_day);
  if (row.hour_used >= registry.hourly_paid_call_cap) refuseJevSpend('hourly_paid_call_cap', attribution.caller, row.resets_hour);
  if (row.site_day >= site.daily_budget) refuseJevSpend('site_daily_budget', attribution.caller, row.resets_day);
  if (row.site_hour >= site.hourly_budget) refuseJevSpend('site_hourly_budget', attribution.caller, row.resets_hour);
  return site;
}

export async function holdJevBilling(client, actor) {
  await client.query('begin');
  try {
    await client.query('select pg_advisory_xact_lock(hashtextextended($1,0))', [SPEND_LOCK]);
    await client.query(`insert into tool_call (idempotency_key, verb, actor_id, request_hash, response)
      values ($1, 'jev-billing-hold', $2, $1,
        jsonb_build_object('resets_at', clock_timestamp() + interval '1 hour'))
      on conflict (idempotency_key) do update set response = excluded.response`, [BILLING_MARKER, actor.id]);
    await client.query('commit');
  } catch (error) { await client.query('rollback'); throw error; }
}

export function attributedJevState(state, attribution) {
  return { jev_attribution: attribution, input: state };
}

export function unpackJevState(state) {
  if (state && typeof state === 'object' && Object.hasOwn(state, 'jev_attribution') &&
      Object.keys(state).sort().join(',') === 'input,jev_attribution')
    return { state: state.input, attribution: state.jev_attribution };
  return { state, attribution: null };
}
