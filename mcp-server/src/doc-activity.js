import { organizationTenantForActor, personalScopeForActor } from './identity.js';

export const DOC_ACTIVITY_SCHEMA = 'doc-activity.v1';
export const UNDO_FIELDS = ['phase', 'owner', 'attention', 'next_date', 'operating_state'];
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const word = value => String(value || '').replace(/[_-]+/g, ' ').trim();
const scalar = value => ['string', 'number', 'boolean'].includes(typeof value) ? value : null;
// A missing inverse is explicitly unavailable; it is not proof that the real
// world action is irreversible. Evidence contains the original audit entry,
// never a fabricated email/call body or an expanded sensitive-value pointer.
export function docActivityEntry(row) {
  const before = scalar(row.old_value?.[row.field]);
  const after = scalar(row.new_value?.[row.field]);
  const oldValue = row.old_value?.[row.field];
  const reversibleValue = oldValue === null || ['string', 'number', 'boolean'].includes(typeof oldValue);
  const undo = row.verb === 'revert-deal-field' ? { state: 'undone' }
    : row.subject_type === 'deal' && UNDO_FIELDS.includes(row.field) && row.has_old_value === true && reversibleValue
      ? row.is_latest === true ? { state: 'available', verb: 'revert-deal-field', event_id: row.id }
        : { state: 'superseded' }
      : { state: row.irreversible === true ? 'irreversible' : 'unavailable' };
  return {
    id: row.id, at: row.recorded_at, actor: row.actor_name, partner: row.partner || null,
    record: { id: row.subject_id, type: row.subject_type, name: row.record_name || word(row.subject_type) },
    what: row.field ? `${word(row.field)} changed` : word(row.verb),
    why: row.agent_rationale || null, before, after, undo,
    evidence: { kind: 'entry', at: row.occurred_at, quote: row.human_quote || null,
      summary: row.summary || null, reason: row.agent_rationale || null, before, after },
  };
}

export function docActivityTools({ ToolError }) {
  const refuse = () => { throw new ToolError({ error: 'doc_activity_input_invalid' }); };
  return {
    'read-doc-activity': {
      description: 'Newest-first autonomous changes from the existing event spine: actor, partner scope, record, reason, original entry evidence and guarded deal-field undo eligibility. Includes all recorded autonomous event types; no human-stated changes or inferred communications. Keyset paging and partner/type/date filters. Missing reasons and unsupported inverses remain explicit.',
      inputSchema: { type: 'object', additionalProperties: false, properties: {
        partner: { type: 'string', enum: ['joe', 'dell'] },
        record_type: { type: 'string', maxLength: 100 },
        since: { type: 'string', format: 'date-time' }, until: { type: 'string', format: 'date-time' },
        limit: { type: 'integer', minimum: 1, maximum: 100 },
        cursor: { type: 'object', additionalProperties: false, properties: {
          at: { type: 'string', format: 'date-time' }, id: { type: 'string', format: 'uuid' },
        }, required: ['at', 'id'] },
      } },
      handler: async (c, actor, args = {}) => {
        if (!args || typeof args !== 'object' || Array.isArray(args)) refuse();
        const allowed = ['partner', 'record_type', 'since', 'until', 'limit', 'cursor'];
        if (Object.keys(args).some(key => !allowed.includes(key))) refuse();
        if (args.partner !== undefined && !['joe', 'dell'].includes(args.partner)) refuse();
        if (args.record_type !== undefined && !/^[a-z][a-z0-9_]{0,99}$/.test(args.record_type)) refuse();
        for (const date of [args.since, args.until, args.cursor?.at]) if (date !== undefined &&
          (typeof date !== 'string' || !/^\d{4}-\d{2}-\d{2}T/.test(date) || !Number.isFinite(Date.parse(date)))) refuse();
        if (args.since && args.until && Date.parse(args.since) >= Date.parse(args.until)) refuse();
        if (args.cursor !== undefined && (!args.cursor || typeof args.cursor !== 'object' || Array.isArray(args.cursor) ||
          Object.keys(args.cursor).some(key => !['at','id'].includes(key)) ||
          !args.cursor.at || !UUID.test(args.cursor.id))) refuse();
        const limit = args.limit ?? 50;
        if (!Number.isInteger(limit) || limit < 1 || limit > 100) refuse();
        const scope = personalScopeForActor(actor);
        if (scope.status === 'error') throw new ToolError({ error: scope.error });
        const tenant = organizationTenantForActor(actor);
        const sponsor = scope.status === 'personal' ? scope.sponsor : null;
        // Human accounts also run autonomous jobs: cause, not actor kind or
        // display name, decides whether a change was made on its own.
        const visible = `e.organization_tenant_id=$1 and
          (e.personal_scope='none' or e.personal_scope=$2) and
          e.cause in ('automation_job','learning_job','system','ingest_email',
            'ingest_calendar','ingest_webhook','import_salesforce')`;
        const types = await c.query(`select distinct e.subject_type from event e where ${visible}
          order by e.subject_type /* doc-activity:types */`, [tenant, sponsor ? `${sponsor}-personal` : 'none']);
        const found = await c.query(`select e.id,e.recorded_at,e.occurred_at,e.subject_id,e.subject_type,
          e.verb,e.field,e.old_value,e.new_value,e.agent_rationale,e.human_quote,
          a.display_name as actor_name,
          coalesce(e.sponsoring_human_slug,case when a.slug in ('joe','dell') then a.slug end) as partner,
          coalesce(d.name,p.name,lp.name,cp.name,vp.name,e.new_value->>'name',e.new_value->>'title') as record_name,
          coalesce(e.new_value->>'summary',e.new_value->>'text') as summary,
          e.old_value ? e.field as has_old_value,
          not exists (select 1 from event newer where newer.subject_type=e.subject_type
            and newer.subject_id=e.subject_id and newer.field=e.field
            and (newer.recorded_at,newer.id)>(e.recorded_at,e.id)) as is_latest,
          e.verb in ('confirm-merge','publish-placement','send-message') as irreversible
          from event e join actor a on a.id=e.actor_id
          left join deal d on e.subject_type='deal' and d.id=e.subject_id
          left join party p on e.subject_type='party' and p.id=e.subject_id
          left join lead l on e.subject_type='lead' and l.id=e.subject_id left join party lp on lp.id=l.party_id
          left join client cl on e.subject_type='client' and cl.id=e.subject_id left join party cp on cp.id=cl.party_id
          left join vendor v on e.subject_type='vendor' and v.id=e.subject_id left join party vp on vp.id=v.party_id
          where ${visible}
          and ($3::text is null or coalesce(e.sponsoring_human_slug,case when a.slug in ('joe','dell') then a.slug end)=$3)
          and ($4::text is null or e.subject_type=$4)
          and ($5::timestamptz is null or e.recorded_at >= $5)
          and ($6::timestamptz is null or e.recorded_at < $6)
          and ($7::timestamptz is null or (e.recorded_at,e.id)<($7,$8::uuid))
          order by e.recorded_at desc,e.id desc limit $9 /* doc-activity:entries */`,
        [tenant, sponsor ? `${sponsor}-personal` : 'none', args.partner || null, args.record_type || null,
          args.since || null, args.until || null, args.cursor?.at || null, args.cursor?.id || null, limit + 1]);
        const rows = found.rows.slice(0, limit), last = rows.at(-1);
        const clock = await c.query('select now() as as_of /* doc-activity:clock */');
        return { ok: true, schema_version: DOC_ACTIVITY_SCHEMA, as_of: clock.rows[0].as_of,
          entries: rows.map(docActivityEntry), record_types: types.rows.map(row => row.subject_type),
          next_cursor: found.rows.length > limit ? { at: last.recorded_at, id: last.id } : null };
      },
    },
  };
}
