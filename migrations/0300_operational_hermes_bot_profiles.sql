-- 0300_operational_hermes_bot_profiles.sql
--
-- CARR-side staffing for the fixed eight-seat Hermes roster.  This migration
-- changes the named profile projection and records the human ruling; it does
-- not create Hermes credentials, grant authority, or make a profile an actor.
-- Hermes Desktop remains a separate transport/runtime configuration step.

begin;

do $$
declare
  joe_id uuid;
  expected integer := 8;
begin
  select id into joe_id
    from actor
   where slug = 'joe' and kind = 'human' and active;
  if joe_id is null then
    raise exception '0300 FAILED: active human actor joe is required to record staffing';
  end if;

  -- The schema snapshot used by migration rehearsal may contain the tables
  -- without 0284's data rows.  Seed the complete fixed roster here, while
  -- preserving any production rows already created by 0284.
  insert into agent_profile (profile_key, display_name, charter, status)
  values
    ('builder', 'Builder', '["implementation in the repo worktree lanes",
                             "migrations and Worker verbs",
                             "tests written before the thing",
                             "release mechanics through the sanctioned doors"]'::jsonb,
     'unstaffed'),
    ('designer', 'Designer', '["surface and interaction design under the CARR surface constraints",
                              "doctrine-governed visual work",
                              "concept documents to order level"]'::jsonb,
     'unstaffed'),
    ('reviewer', 'Reviewer', '["independent verification with fresh context",
                              "adversarial reading of finished work",
                              "attestation of builds it did not make"]'::jsonb,
     'unstaffed'),
    ('doc', 'Doc', '["the doctorcre app persona (Dr. CRE)",
                      "prospect-facing product surfaces under Doc''s own product rules",
                      "hermes-app runtime once the October machine arrives"]'::jsonb,
     'parked'),
    ('deal-steward', 'Deal Steward', '[]'::jsonb, 'unstaffed'),
    ('intake-clerk', 'Intake Clerk', '[]'::jsonb, 'unstaffed'),
    ('marketing-ops', 'Marketing Ops', '[]'::jsonb, 'unstaffed'),
    ('system-watch', 'System Watch', '[]'::jsonb, 'unstaffed')
  on conflict (profile_key) do nothing;

  -- Keep this update deliberately presentation/routing-only: no permission
  -- table or actor row is touched.  The role grouping is a routing/observatory
  -- distinction, not an authority distinction.
  update agent_profile p
     set charter = case p.profile_key
       when 'deal-steward' then
         '["deal watch and next-action hygiene", "critical-date and follow-up triage", "draft-only partner escalations"]'::jsonb
       when 'intake-clerk' then
         '["structured intake and normalization", "record completeness checks", "research queue preparation"]'::jsonb
       when 'marketing-ops' then
         '["marketing queue operations", "content repurposing and scheduling drafts", "campaign evidence capture"]'::jsonb
       when 'system-watch' then
         '["routine system health observation", "scheduled-run and freshness checks", "incident draft preparation"]'::jsonb
       else p.charter
     end,
     current_model = case p.profile_key
       when 'doc' then 'xai-oauth/grok-4.6'
       when 'deal-steward' then 'xai-oauth/grok-4.6'
       when 'intake-clerk' then 'nous/deepseek/deepseek-v4-pro'
       when 'marketing-ops' then 'xai-oauth/grok-4.6'
       when 'system-watch' then 'nous/deepseek/deepseek-v4-pro'
       when 'designer' then 'nous/moonshotai/kimi-k3'
       when 'builder' then 'openrouter/stealth/ox-alpha'
       when 'reviewer' then 'nous/deepseek/deepseek-v4-pro'
       else p.current_model
     end,
     current_desk = case p.profile_key
       when 'doc' then 'hermes-desktop'
       when 'deal-steward' then 'hermes-desktop'
       when 'intake-clerk' then 'hermes-desktop'
       when 'marketing-ops' then 'hermes-desktop'
       when 'system-watch' then 'hermes-desktop'
       when 'designer' then 'hermes-desktop'
       when 'builder' then 'hermes-desktop'
       when 'reviewer' then 'hermes-desktop'
       else p.current_desk
     end,
     status = case
       when p.profile_key in ('doc','deal-steward','intake-clerk','marketing-ops','system-watch',
                              'designer','builder','reviewer') then 'active'
       else p.status
     end,
     version = version + case
       when p.profile_key in ('doc','deal-steward','intake-clerk','marketing-ops','system-watch',
                              'designer','builder','reviewer') then 1
       else 0
       end,
     updated_at = now()
   where p.profile_key in ('doc','deal-steward','intake-clerk','marketing-ops','system-watch',
                           'designer','builder','reviewer');

  get diagnostics expected = row_count;
  if expected <> 8 then
    raise exception '0300 FAILED: expected the fixed eight-profile roster, updated %', expected;
  end if;

  insert into agent_profile_assignment
    (profile_id, model, desk, status, ruled_by, ruling_basis, note, idempotency_key)
  select p.id, p.current_model, p.current_desk, p.status, joe_id, 'human',
         'Joe-approved Hermes operational staffing; CARR profile only, no authority grant',
         x.idempotency_key::uuid
    from (values
      ('doc',          '03000000-0000-4000-8000-000000000001'),
      ('deal-steward', '03000000-0000-4000-8000-000000000002'),
      ('intake-clerk', '03000000-0000-4000-8000-000000000003'),
      ('marketing-ops','03000000-0000-4000-8000-000000000004'),
      ('system-watch', '03000000-0000-4000-8000-000000000005'),
      ('designer',    '03000000-0000-4000-8000-000000000006'),
      ('builder',     '03000000-0000-4000-8000-000000000007'),
      ('reviewer',    '03000000-0000-4000-8000-000000000008')
    ) as x(profile_key, idempotency_key)
    join agent_profile p on p.profile_key = x.profile_key;

  if (select count(*) from agent_profile
      where profile_key in ('doc','deal-steward','intake-clerk','marketing-ops','system-watch',
                            'designer','builder','reviewer')
        and status = 'active'
        and current_desk = 'hermes-desktop'
        and current_model is not null) <> 8 then
    raise exception '0300 FAILED: fixed eight-profile staffing projection is incomplete';
  end if;

  if (select count(*) from agent_profile
      where (profile_key, current_model) in (
        ('doc', 'xai-oauth/grok-4.6'),
        ('deal-steward', 'xai-oauth/grok-4.6'),
        ('intake-clerk', 'nous/deepseek/deepseek-v4-pro'),
        ('marketing-ops', 'xai-oauth/grok-4.6'),
        ('system-watch', 'nous/deepseek/deepseek-v4-pro'),
        ('designer', 'nous/moonshotai/kimi-k3'),
        ('builder', 'openrouter/stealth/ox-alpha'),
        ('reviewer', 'nous/deepseek/deepseek-v4-pro'))
    ) <> 8 then
    raise exception '0300 FAILED: fixed model assignment map is not exact';
  end if;

  if (select count(*) from agent_profile
      where profile_key in ('doc','deal-steward','intake-clerk','marketing-ops','system-watch')
        and status = 'active') <> 5
     or (select count(*) from agent_profile
      where profile_key in ('designer','builder','reviewer')
        and status = 'active') <> 3 then
    raise exception '0300 FAILED: operational and contingency profile groups are not both active';
  end if;
end $$;

commit;
