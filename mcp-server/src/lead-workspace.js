// Optional Leads workspace projection. The legacy lead-board read stays intact.
export const LEAD_WORKSPACE_VERSION = "lead-workspace.v1";
export const LEAD_WORKSPACE_SCHEMA = {
  type: "object", additionalProperties: false,
  properties: { workspace: { type: "string", enum: ["leads"] }, lead_id: { type: "string", pattern: "^[0-9a-fA-F-]{36}$" } },
};

export async function readLeadWorkspace(c, args = {}) {
  const leads = (await c.query(`
    select b.*, l.party_id, l.client_id,
      case when p.kind='person' then p.name end as doctor_name,
      org.name as practice_name, p.name as entity_name,
      exists(select 1 from client cl where cl.party_id=p.id and cl.merged_into is null) as is_client,
      exists(select 1 from v_lead_client_best bridge where bridge.lead_ref=l.registry_ref and not bridge.either_merged) as linked_client,
      exists(select 1 from deal d where d.client_id=l.client_id) as is_deal,
      coalesce(matches.items,'[]'::json) as possible_clients,
      last_move.item as last_stage_move
    from v_lead_board b join lead l on l.id=b.id
    join party p on p.id=l.party_id left join party org on org.id=p.org_id
    left join lateral (
      select json_agg(json_build_object('client_id',cl.id,'name',cp.name) order by cl.id) as items
      from client cl join party cp on cp.id=cl.party_id
      where lower(trim(cp.name))=lower(trim(p.name)) and cp.id<>p.id
        and cl.merged_into is null and cp.merged_into is null and cp.deleted_at is null
    ) matches on true
    left join lateral (
      select json_build_object('event_id',e.id,'idempotency_key',e.idempotency_key,'from',e.old_value->>'stage','to',e.new_value->>'stage',
        'occurred_at',e.occurred_at,'automatic',e.cause in ('automation_job','ingest_email','ingest_calendar','system'),
        'reason',e.agent_rationale,'evidence_date',e.new_value->'stage_review'->>'evidence_date',
        'undone',e.new_value->'stage_review'->>'undo_event_id') as item
      from event e where e.subject_type='lead' and e.subject_id=l.id and e.field='stage'
      order by e.occurred_at desc,e.id desc limit 1
    ) last_move on true
    where not l.suppressed and p.merged_into is null and p.deleted_at is null
    order by b.stage_sort,b.score desc nulls last,b.name,l.id`, [])).rows;
  const result = { schema_version: LEAD_WORKSPACE_VERSION, generated_at: new Date().toISOString(),
    leads, last_search_at: null, search_run_coverage: "unavailable" };
  if (!args.lead_id) return result;
  const lead = leads.find(row => row.id === args.lead_id);
  if (!lead) return { ...result, detail: null };
  const record = (await c.query(`select p.phone,p.email,l.notes,l.est_lease_event,l.segment
    from lead l join party p on p.id=l.party_id where l.id=$1`, [lead.id])).rows[0];
  const correspondence = (await c.query(`select id,kind,occurred_at,summary,detail,source
    from activity where lead_id=$1 order by occurred_at desc,id desc limit 100`, [lead.id])).rows;
  const history = (await c.query(`select id as event_id,occurred_at,old_value->>'stage' as prior_stage,
    new_value->>'stage' as stage,cause,agent_rationale as reason,new_value->'stage_review' as stage_review
    from event where subject_type='lead' and subject_id=$1 and field='stage'
    order by occurred_at desc,id desc limit 100`, [lead.id])).rows;
  result.detail = { ...lead, ...record, correspondence, stage_history: history,
    correspondence_coverage: "captured_entries_only", history_limit: 100 };
  return result;
}
