-- 0391_acting_identity_definer_projection.sql
--
-- 0390 GRANTED carr_reader DIRECT public.actor SELECT, AND THAT IS EXACTLY THE
-- BOUNDARY 0382 EXISTS TO HOLD. guidance-registry-db-gate.py asserts it by name:
-- "carr_reader has direct public.actor SELECT" is a hard failure, because 0382
-- made ops.standing_guidance SECURITY DEFINER precisely so the reader could
-- consume a sanctioned projection WITHOUT direct public.rule or public.actor
-- table access. 0390 read the permission error in front of it and widened the
-- role instead of asking why the role was narrow.
--
-- The card needs a projection, not table access. This is the 0382 shape applied
-- to the same question one verb over: the joins move inside a SECURITY DEFINER
-- function with a pinned search_path, and every grant 0389 and 0390 handed out
-- is taken back. The app roles end this migration no wider than they began, and
-- the reader reaches acting identity the only way it reaches standing guidance.
--
-- WHY THE RECEIPT GRANTS GO TOO. A definer function runs as its owner, so it
-- needs nothing from the caller. Leaving 0389's grants in place would leave the
-- reader and writer holding table reads that nothing uses, which is how a
-- boundary erodes: not by a decision, but by a grant nobody later remembers is
-- unnecessary.

revoke select on table public.actor from carr_reader;
revoke select on table public.tool_call from carr_reader;
revoke select on table ops.work_request_triage_receipt from carr_reader, carr_writer;
revoke select on table ops.sourced_work_request_plan_acceptance_receipt from carr_reader, carr_writer;
revoke select on table ops.sourced_work_request_outcome_feedback_acceptance_receipt from carr_reader, carr_writer;

create or replace function ops.work_request_acting_identity(p_ref text)
returns table(act text, recorded_slug text, acted_at timestamptz,
              actor_slug text, authorization_class text, via text)
language sql stable security definer
set search_path to 'pg_catalog', 'ops', 'public', 'pg_temp'
as $$
  select act, recorded_slug, acted_at, actor_slug, authorization_class, via from (
    select 'review-and-triage' as act, ha.slug as recorded_slug, r.triaged_at as acted_at,
           a.slug as actor_slug, t.authorization_class, t.via
      from ops.work_request_triage_receipt r
      join ops.work_request w on w.id = r.work_request_id
      join public.actor ha on ha.id = r.triaged_by_actor_id
      left join public.tool_call t on t.idempotency_key = r.idempotency_key::text
      left join public.actor a on a.id = t.actor_id
     where w.ref = p_ref
    union all
    select 'accept-ready-plan', ha.slug, r.accepted_at, a.slug, t.authorization_class, t.via
      from ops.sourced_work_request_plan_acceptance_receipt r
      join ops.work_request w on w.id = r.work_request_id
      join public.actor ha on ha.id = r.accepted_by_actor_id
      left join public.tool_call t on t.idempotency_key = r.idempotency_key::text
      left join public.actor a on a.id = t.actor_id
     where w.ref = p_ref
    union all
    select 'accept-outcome-feedback', ha.slug, r.accepted_at, a.slug, t.authorization_class, t.via
      from ops.sourced_work_request_outcome_feedback_acceptance_receipt r
      join ops.work_request w on w.id = r.work_request_id
      join public.actor ha on ha.id = r.accepted_by_actor_id
      left join public.tool_call t on t.idempotency_key = r.idempotency_key::text
      left join public.actor a on a.id = t.actor_id
     where w.ref = p_ref
  ) acts order by acted_at
$$;

comment on function ops.work_request_acting_identity(text) is
  'Reader-facing acting-identity projection for one Work Request card. SECURITY '
  'DEFINER with a fixed search_path so carr_reader can read who actually performed '
  'each authority act without receiving direct public.actor or public.tool_call '
  'table access. Same boundary shape as ops.standing_guidance (0382).';

revoke all on function ops.work_request_acting_identity(text) from public;
grant execute on function ops.work_request_acting_identity(text) to carr_reader, carr_writer;

do $$
begin
  if has_table_privilege('carr_reader','public.actor','select') then
    raise exception '0391 FAILED: carr_reader still holds direct public.actor select';
  end if;
  if has_table_privilege('carr_reader','public.tool_call','select') then
    raise exception '0391 FAILED: carr_reader still holds direct public.tool_call select';
  end if;
  if has_table_privilege('carr_reader','ops.work_request_triage_receipt','select')
     or has_table_privilege('carr_writer','ops.work_request_triage_receipt','select') then
    raise exception '0391 FAILED: a receipt-table grant from 0389 survived';
  end if;
  if not has_function_privilege('carr_reader','ops.work_request_acting_identity(text)','execute')
     or not has_function_privilege('carr_writer','ops.work_request_acting_identity(text)','execute') then
    raise exception '0391 FAILED: the app roles cannot execute the projection';
  end if;
  if has_function_privilege('public','ops.work_request_acting_identity(text)','execute') then
    raise exception '0391 FAILED: PUBLIC can execute the projection';
  end if;
end $$;
