// Trust uses verified deal history; missing coverage never becomes zero.
export function computedTrust(stats, now) {
  if (!stats?.coverage_verified_at || !Number.isInteger(stats.deals_worked) || !Number.isInteger(stats.won) || !Number.isInteger(stats.lost)) return 'Unrated';
  const years = stats.first_worked_at ? (Date.parse(now) - Date.parse(stats.first_worked_at)) / (365.25 * 86400000) : 0;
  const recency = stats.last_contacted_at ? (Date.parse(now) - Date.parse(stats.last_contacted_at)) / 86400000 : Infinity;
  const resolved = stats.won + stats.lost;
  const rate = resolved ? stats.won / resolved : null;
  if (years >= 2 && stats.deals_worked >= 8 && rate >= .7 && recency >= 0 && recency <= 180) return 'Proven';
  if (years >= 1 && stats.deals_worked >= 3 && rate >= .5 && recency >= 0 && recency <= 365) return 'Established';
  return 'Trial';
}
export function enrichRelationship(payload, now) {
  const stats = payload || {};
  const verified = Boolean(stats.coverage_verified_at);
  return { ...stats, deals_referred: verified ? stats.deals_referred : null, deals_worked: verified ? stats.deals_worked : null,
    win_rate: verified && stats.won + stats.lost > 0 ? stats.won / (stats.won + stats.lost) : null,
    computed_tier: computedTrust(stats, now), formula_version: 'vendor-trust.v1' };
}
export function trustedOverride(value, actor, now = new Date().toISOString()) {
  if (actor?.human !== true || !['joe', 'dell'].includes(actor?.slug)) throw Object.assign(new Error('AUTHORIZATION_REFUSED'), { code: 'AUTHORIZATION_REFUSED' });
  if (value === null) return null;
  if (!value || !['Proven', 'Established', 'Trial'].includes(value.tier) || typeof value.reason !== 'string' || !value.reason.trim() || value.reason.length > 500 || Object.keys(value).some(k => !['tier', 'reason'].includes(k))) throw Object.assign(new Error('trust_override_invalid'), { code: 'trust_override_invalid' });
  return { tier: value.tier, reason: value.reason.trim(), recorded_by: actor.slug, recorded_at: now };
}
export function dealEvidenceEntries(value) {
  if (!Array.isArray(value) || value.length > 100) throw Object.assign(new Error('deal_evidence_invalid'), { code: 'deal_evidence_invalid' });
  const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  const seen = new Set();
  return value.map(entry => {
    if (!entry || !uuid.test(entry.deal_id) || !['referred','worked'].includes(entry.role) || !['mail','calendar','salesforce','entry'].includes(entry.evidence_kind) || !Number.isFinite(Date.parse(entry.occurred_at)) || typeof entry.evidence_ref !== 'string' || !entry.evidence_ref.trim() || entry.evidence_ref.length > 500 || Object.keys(entry).some(k => !['deal_id','role','evidence_kind','evidence_ref','occurred_at'].includes(k))) throw Object.assign(new Error('deal_evidence_invalid'), { code: 'deal_evidence_invalid' });
    const key = `${entry.deal_id}:${entry.role}`;
    if (seen.has(key)) throw Object.assign(new Error('deal_evidence_duplicate'), { code: 'deal_evidence_duplicate' });
    seen.add(key); return { ...entry, evidence_ref: entry.evidence_ref.trim() };
  });
}
// Exact IDs only. Neither a relationship edge nor a matching name proves a
// vendor worked a deal. Historical Salesforce records remain canonical deals.
export const vendorRelationshipJoin = `left join lateral (
  select max(e.occurred_at) as last_deal_at,
    json_build_object(
      'coverage_verified_at', v.deal_history_verified_at,
      'deals_referred', count(distinct e.deal_id) filter (where e.role='referred'),
      'deals_worked', count(distinct e.deal_id) filter (where e.role='worked'),
      'won', count(distinct e.deal_id) filter (where e.role='worked' and d.outcome='won'),
      'lost', count(distinct e.deal_id) filter (where e.role='worked' and d.outcome='lost'),
      'first_worked_at', min(e.occurred_at) filter (where e.role='worked'),
      'last_contacted_at', (select max(a.occurred_at) from public.activity a where a.vendor_id=v.id and a.kind in ('call','email_out','email_in','meeting','text')),
      'last_contact_note', (select a.summary from public.activity a where a.vendor_id=v.id and a.kind in ('call','email_out','email_in','meeting','text') order by a.occurred_at desc,a.id desc limit 1),
      'override', v.trust_override,
      'recent_entries', (select coalesce(json_agg(t.entry order by t.occurred_at desc), '[]'::json) from (select a.occurred_at, json_build_object('id',a.id,'kind',a.kind,'when',a.occurred_at,'summary',a.summary,'detail',a.detail) as entry from public.activity a where a.vendor_id=v.id order by a.occurred_at desc,a.id desc limit 20) t),
      'introductions', (select coalesce(json_agg(json_build_object('id',l.id,'kind',l.kind,'from_name',fp.name,'to_name',tp.name,'note',l.note,'occurred_at',l.occurred_on,'via_party',l.via_party) order by l.created_at desc), '[]'::json) from public.party_link l join public.party fp on fp.id=l.from_party join public.party tp on tp.id=l.to_party where (l.from_party=p.id or l.to_party=p.id or l.via_party=p.id) and l.kind in ('intro','intro_received','introduced','can_introduce','intro_requested') and fp.merged_into is null and tp.merged_into is null and fp.deleted_at is null and tp.deleted_at is null)
    ) as payload
  from jsonb_to_recordset(coalesce(v.deal_evidence, '[]'::jsonb)) as e(deal_id uuid,role text,occurred_at timestamptz,evidence_kind text,evidence_ref text) join public.deal d on d.id=e.deal_id join public.client dc on dc.id=d.client_id and dc.merged_into is null join public.party dp on dp.id=dc.party_id and dp.merged_into is null and dp.deleted_at is null
) vr on true`;
