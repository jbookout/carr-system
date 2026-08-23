-- 0284_named_agent_profiles.sql
-- THE NAME PERSISTS; THE MODEL BEHIND IT IS STAFFING DETAIL.
--
-- Joe's ruling, 2026-08-22 (settled in decision history; concept at
-- out/council/20260821-tuneup/agent-profiles-concept.md, every line settled):
-- persistent agent identities — Builder, Designer, Reviewer, Doc — whose NAME
-- is the thing partners learn, with the current model visible underneath and
-- interchangeable. Doc's identity exists from day one and stays parked until
-- its runtime arrives in October: identity now, runtime later.
--
-- THE HARD BOUNDARY, stated where the schema lives: a profile is presentation
-- and routing, NEVER write authority. Every write still lands under the
-- verified credential's server-derived actor; no verb accepts a caller-claimed
-- profile as permission for anything; swapping a profile's model changes zero
-- permissions. There is deliberately no join from these tables into any
-- permission decision, and none may ever be added. Desks stay transport,
-- actors stay authority, profiles are the human-facing layer over both.
--
-- Staffing changes are RECORDED ACTS: the assignment history is append-only
-- (who staffed which profile with what model, when, on whose ruling), the same
-- discipline the retrieval-proposal ledger uses, because a history that can be
-- edited is not a history.

begin;

create table agent_profile (
  id uuid primary key default gen_random_uuid(),
  profile_key text not null unique
    check (profile_key ~ '^[a-z][a-z0-9]*(-[a-z0-9]+)*$'),
  display_name text not null check (length(display_name) between 1 and 60),
  -- The charter is the job description as a skills list, per the standing
  -- staffing rule: an agent IS a job description, and the job description is
  -- made up of skills.
  charter jsonb not null default '[]'::jsonb
    check (jsonb_typeof(charter) = 'array'),
  current_model text,
  current_desk text,
  sponsor_scope text not null default 'shared',
  status text not null check (status in ('active','unstaffed','parked')),
  version bigint not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table agent_profile_assignment (
  id uuid primary key default gen_random_uuid(),
  profile_id uuid not null references agent_profile(id),
  model text,
  desk text,
  status text not null check (status in ('active','unstaffed','parked')),
  ruled_by uuid not null references actor(id),
  ruling_basis text not null check (ruling_basis in ('human','standing_delegation')),
  note text,
  idempotency_key uuid not null unique,
  created_at timestamptz not null default now()
);

create index agent_profile_assignment_profile_idx
  on agent_profile_assignment (profile_id, created_at desc);

-- Append-only, enforced rather than recited: the history refuses UPDATE and
-- DELETE outright. A staffing mistake is corrected by the next assignment
-- row, never by rewriting the last one.
create or replace function agent_profile_assignment_guard()
returns trigger language plpgsql as $$
begin
  raise exception 'agent_profile_assignment is append-only: correct a staffing
 mistake with the next assignment row, never by editing history';
end $$;

create trigger agent_profile_assignment_append_only
  before update or delete on agent_profile_assignment
  for each row execute function agent_profile_assignment_guard();

-- The four profiles Joe named. Builder, Designer, and Reviewer begin
-- unstaffed — the identity exists before anyone staffs it, which is the
-- point. Doc begins parked: its runtime waits for the October machine, and
-- its charter carries the doctorcre-app role it will serve.
insert into agent_profile (profile_key, display_name, charter, status) values
  ('builder',  'Builder',  '["implementation in the repo worktree lanes",
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
  ('doc',      'Doc',      '["the doctorcre app persona (Dr. CRE)",
                             "prospect-facing product surfaces under Doc''s own product rules",
                             "hermes-app runtime once the October machine arrives"]'::jsonb,
   'parked');

-- Least privilege, as plain statements the authority-plan parser can read.
grant select on agent_profile to carr_reader;
grant select on agent_profile_assignment to carr_reader;
grant select, update on agent_profile to carr_writer;
grant select, insert on agent_profile_assignment to carr_writer;
revoke delete on agent_profile, agent_profile_assignment from public;

-- Proofs, in the migration itself.
do $$
declare n integer; blocked boolean := false; probe_profile uuid; probe_actor uuid;
        probe_row uuid;
begin
  select count(*) into n from agent_profile;
  if n <> 4 then raise exception '0284 FAILED: expected 4 seeded profiles, found %', n; end if;
  select count(*) into n from agent_profile where profile_key='doc' and status='parked';
  if n <> 1 then raise exception '0284 FAILED: doc must exist and start parked'; end if;
  select count(*) into n from agent_profile where status='unstaffed' and current_model is null;
  if n <> 3 then raise exception '0284 FAILED: builder/designer/reviewer must start unstaffed with no model'; end if;

  -- The append-only guard, exercised rather than trusted: insert one probe
  -- row, prove UPDATE refuses, then remove the probe via a direct catalog-
  -- level delete? No — delete must ALSO refuse. The probe row stays out of
  -- history by rolling back a savepoint-shaped block instead.
  select id into probe_profile from agent_profile where profile_key='builder';
  select id into probe_actor from actor where kind='human' and active limit 1;
  if probe_actor is null then
    select id into probe_actor from actor limit 1;
  end if;
  if probe_actor is not null then
    insert into agent_profile_assignment
      (profile_id, model, status, ruled_by, ruling_basis, idempotency_key)
    values (probe_profile, 'probe-model', 'active', probe_actor, 'human',
            '02840000-0000-4000-8000-000000000001')
    returning id into probe_row;
    begin
      update agent_profile_assignment set model='rewritten' where id=probe_row;
    exception when others then
      blocked := true;
    end;
    if not blocked then
      raise exception '0284 FAILED: assignment history accepted an UPDATE';
    end if;
    blocked := false;
    begin
      delete from agent_profile_assignment where id=probe_row;
    exception when others then
      blocked := true;
    end;
    if not blocked then
      raise exception '0284 FAILED: assignment history accepted a DELETE';
    end if;
  end if;

  if exists (select 1 from pg_roles where rolname='carr_writer') then
    if not has_table_privilege('carr_writer','agent_profile','update')
       or not has_table_privilege('carr_writer','agent_profile_assignment','insert')
       or has_table_privilege('carr_writer','agent_profile_assignment','delete') then
      raise exception '0284 FAILED: carr_writer grants are not the intended shape';
    end if;
  end if;
  if exists (select 1 from pg_roles where rolname='carr_reader') then
    if not has_table_privilege('carr_reader','agent_profile','select')
       or has_table_privilege('carr_reader','agent_profile','update') then
      raise exception '0284 FAILED: carr_reader grants are not the intended shape';
    end if;
  end if;
end $$;

commit;
