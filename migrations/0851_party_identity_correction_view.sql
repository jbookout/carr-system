-- rollback: drop view public.v_party_identity_correction; nothing else depends on it and no row is written.
-- correct-party-identity (rule 578fdd91) writes a party's name, org and state
-- from a verified finding, and each changed field records its prior value on an
-- event. This view is the read surface for those corrections: what changed,
-- from what, to what, on whose authority and from which source, so a wrong
-- correction can be found and undone from the record. It reads the event table
-- the verb already writes; it adds no table and no write path.
create view public.v_party_identity_correction as
select e.id as event_id,
       e.mutation_order,
       e.occurred_at,
       e.subject_id as party_id,
       p.ref as party_ref,
       p.kind as party_kind,
       p.name as party_name_now,
       e.field,
       e.old_value -> e.field as prior_value,
       e.new_value -> e.field as corrected_value,
       e.new_value ->> 'mode' as org_mode,
       nullif(regexp_replace(coalesce(e.agent_rationale, ''), '^source: ', ''), '') as source,
       a.slug as corrected_by,
       e.idempotency_key
  from public.event e
  join public.party p on p.id = e.subject_id
  join public.actor a on a.id = e.actor_id
 where e.verb = 'correct-party-identity'
   and e.subject_type = 'party';

comment on view public.v_party_identity_correction is
  'Every correct-party-identity change with its prior value and source, newest by mutation_order; the undo surface for identity corrections.';

revoke all on public.v_party_identity_correction from public, carr_reader, carr_writer, carr_jobs, carr_authority;
grant select on public.v_party_identity_correction to carr_reader, carr_writer;
