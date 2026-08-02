-- 0059_org_identity.sql — the company stops being minted once per employee.
--
-- FULL REASONING, THE MEASUREMENTS, AND THE SHAPE COMPARISON: 0059_org_identity_spec.md,
-- beside this file. This header carries the part a reader needs in order to judge the SQL.
--
-- THE DEFECT, verified read-only on rehearse-verbs-20260802 before a line of this was
-- written. "Henry Schein" exists as 17 separate kind='org' party rows. Patterson Dental as
-- 10, Musicologie as 13. Across all 415 org rows the inbound-count distribution is a single
-- bucket — 1 → 415. Not one org has two people pointing at it; not one has zero. That is not
-- a table with duplicates in it. It is a table where party.org_id is functionally a
-- per-person employer NAME, stored as a foreign key. Seventeen Henry Schein rows are
-- seventeen reps, and the company got minted seventeen times because the row that carried
-- the identity was the CONTACT.
--
-- This is 0046 one level up, and the same sentence applies: identity lived on the thing that
-- arrived rather than on the thing that persists. 0046 moved it from role to person. This
-- moves it from person to organisation.
--
-- IT IS A RUNNING TOTAL, AND THAT IS A CODE FACT RATHER THAN AN INFERENCE. add-party
-- (mcp-server/src/tools.js:1156) and add-premises (:1281) both run an unconditional
-- `insert into party (kind,name,...) values ('org', $1, ...)` whenever an org_name is
-- supplied. Neither looks first. candidate_pool holds 9,773 unpromoted rows, and 57 distinct
-- org_name values in it already appear on more than one candidate row. Promoting the pool as
-- it stands would mint at least 57 new duplicate groups on top of today's 46. Cleaning the
-- backlog without closing the mint would be housework.
--
-- WHY THIS CONSOLIDATES PARTIES INSTEAD OF CREATING AN `organization` TABLE. Short version;
-- the spec argues it properly. party.org_id is a self-FK and every consumer already reaches
-- an org through party (v_ref_index's five branches, tools.js:507, the exporters), so a new
-- table rewrites code owned by three other seats this session. A separate table would also
-- buy nothing today: of the 415 org rows, 57 carry a city and 54 a county, and EVERY other
-- column is null on every one of them. And party.merged_into is an existing, exercised
-- tombstone (A3, 0055) which makes this reversible without deleting anything. The reopen
-- condition is in the spec, and it is genuine: when an org needs org-only attributes or a
-- role of its own, extract the table — this migration makes that extraction easier, because
-- by then org_id points at one row per real organisation instead of 415 near-copies.
--
-- REVERSIBLE IN EFFECT, AND THE MECHANISM IS THE POINT. No row is deleted. The 109 losers
-- keep every column and every P-#### ref and merely gain merged_into, so `find` still
-- returns them with merged=true, which is the posture 0056 chose on purpose. The one fact
-- the repoint would otherwise destroy is WHICH person pointed at WHICH loser, so
-- org_merge_log records it first. The reversal is three statements, and the index has to go
-- first because it is precisely what forbids the pre-merge state:
--
--     drop index party_org_identity_uniq;
--     update party p set org_id = m.from_org from org_merge_log m where m.party_id = p.id;
--     update party set merged_into = null where id in (select from_org from org_merge_log);
--
-- RUN AND VERIFIED on the rehearsal branch before this file was committed, not asserted:
-- it restores 415 org rows with 0 tombstones, 46 live duplicate groups, 17 live Henry Schein
-- rows, and every one of the 415 orgs back to exactly one inbound person. Two honest
-- footnotes. The survivor KEEPS any city or county salvaged from a loser, because that value
-- came from a row in its own identity group and the loser still holds its own copy — that is
-- additive and correct, not a fact lost or invented. And org_merge_log plus the
-- consolidate-org events remain as the record that this happened, which is the intent: a
-- reversal is a new fact, not an erasure of the old one.
--
-- NOTHING HERE MERGES A PERSON, structurally rather than by intention. Every statement
-- filters kind = 'org'. The guard asserts that the person row count, the person merged_into
-- count and the person duplicate-name group count are IDENTICAL before and after, so a
-- future edit that widened a where clause fails the migration instead of applying. Person
-- merges stay the exclusive business of the humanOnly confirm-merge verb under the
-- survivorship rule — this system welded Jenna Beasley to Jeff Beasley DMD once already.
--
-- ORG SURVIVORSHIP IS TRIVIAL, AND THAT WAS MEASURED, NOT ASSUMED. Within every one of the
-- 46 groups there is exactly one distinct exact spelling, one email, one phone, one
-- notes_path, one specialty. ZERO groups hold two different non-null cities. Eleven groups
-- have a city on one row and null on another, and the survivor picks up the non-null value,
-- so the collapse loses nothing whatsoever.
--
-- THE PLACEHOLDER TRAP, which is the one way this migration could fabricate a fact. Six rows
-- are literally named `(TBD — enrich)`; there is also `(TBD)`, `Startup dental practice
-- (entity name TBD)` twice, and two spellings of `(new practice, relocating VA → FL)`.
-- Eleven rows. Collapsing six `(TBD — enrich)` rows would assert that six unrelated people
-- work at the same company, and inventing one fact is worse than leaving eleven duplicates.
-- org_identity_key returns NULL for those, which excludes them from the merge AND from the
-- unique index, so placeholders stay separate and new ones stay legal. The rejection list
-- was checked against all 415 names: it catches those five spellings and nothing else.
--
-- WHAT THIS BREAKS, AND IT IS NOT OPTIONAL. Once the unique index exists, add-party and
-- add-premises raise unique_violation on an org_name that already exists, because both do a
-- blind insert. Loud failure is the right side of that trade (silent duplication is what
-- produced 115 rows nobody noticed), but it must not be met in production. The companion fix
-- is ONE LINE at each of the two call sites: `select org_party_id($1, $2)`, the atomic
-- find-or-create defined at the bottom of this file. tools.js is owned by another seat this
-- session and is not touched here. APPLY 0059 AND THAT CHANGE IN THE SAME DEPLOY.
--
-- The BEFORE numbers are captured in-migration rather than hardcoded, so the assertions are
-- relative and stay true if production has drifted from the branch this rehearsed on. The
-- one symptom asserted by name is Henry Schein, because it is the reported symptom and a
-- migration that collapses everything except the case that prompted it is not a fix.

begin;

-- ── 1. what an organisation's identity IS ────────────────────────────────────────────────
-- Trim, collapse internal whitespace, lowercase. Nothing more. It does NOT strip legal
-- suffixes or parentheticals, and that restraint is load-bearing: the data holds
-- `Carr Riggs Ingram` and `Carr Riggs Ingram (advisory)` as separate rows on purpose, and an
-- aggressive normaliser would weld an advisory arm onto a CPA firm. Case and whitespace are
-- the only differences that are never meaningful. Immutable, because a partial unique index
-- is built on it.
create or replace function org_identity_key(p_name text) returns text
language sql immutable as $$
  select case
    when p_name is null                then null
    when btrim(p_name) = ''            then null
    when btrim(p_name) like '(%'       then null   -- '(TBD)', '(new practice, relocating…)'
    when p_name ~* '\mtbd\M'
      or p_name ~* '\munknown\M'
      or p_name ~* '\mn/a\M'           then null   -- 'Startup dental practice (entity name TBD)'
    else lower(regexp_replace(btrim(p_name), '\s+', ' ', 'g'))
  end
$$;

comment on function org_identity_key(text) is
  'The identity of an ORGANISATION, normalised (0059): trim, collapse whitespace, lowercase, '
  'and NULL for a placeholder. Null means "this string names no organisation" — six rows are '
  'literally called "(TBD — enrich)" and collapsing them would assert that six unrelated '
  'people share an employer, which is a fabricated fact. Null keys are excluded from the '
  'merge and from party_org_identity_uniq, so placeholders stay separate and new ones stay '
  'legal. Deliberately does NOT strip legal suffixes or parentheticals: "Carr Riggs Ingram" '
  'and "Carr Riggs Ingram (advisory)" are different rows on purpose. The enrichment step '
  'that wants domain as an exact match key should EXTEND this function rather than invent a '
  'second normalisation rule.';

-- ── 2. the reversibility artifact, written BEFORE anything moves ─────────────────────────
create table if not exists org_merge_log (
  party_id     uuid        not null references party(id),  -- the person repointed
  from_org     uuid        not null references party(id),  -- the org row they used to hold
  to_org       uuid        not null references party(id),  -- the survivor they hold now
  identity_key text        not null,
  merged_at    timestamptz not null default now(),
  primary key (party_id, from_org)
);

comment on table org_merge_log is
  'Person → old org → surviving org, one row per repoint (0059). This is what makes the org '
  'consolidation reversible in effect: merged_into records that the org rows collapsed, but '
  'only this records WHICH person came from WHICH row, and the repoint destroys that fact '
  'otherwise. Reversal is two statements, spelled out in 0059''s header. Append-only; it is '
  'a record of what happened, not a work queue.';

-- ── 3. the plan, computed once and reused by every step below ────────────────────────────
create temporary table _org_plan on commit drop as
with live as (
  select id, org_identity_key(name) as k, created_at, city, county
    from party
   where kind = 'org'
     and merged_into is null
     and deleted_at is null
     and org_identity_key(name) is not null),
ranked as (
  select l.*,
         first_value(l.id) over (partition by l.k order by l.created_at, l.id) as survivor
    from live l)
select id as loser_id, survivor, k as identity_key, city, county
  from ranked
 where id <> survivor;

-- The BEFORE snapshot, measured rather than hardcoded so the guard is relative and stays
-- honest if production has drifted from the branch this rehearsed on.
create temporary table _org_before on commit drop as
select (select count(*) from party)                                                as parties,
       (select count(*) from party where kind = 'org')                             as orgs,
       (select count(*) from party where kind = 'person')                          as persons,
       (select count(*) from party where kind = 'org'    and merged_into is not null) as org_merged,
       (select count(*) from party where kind = 'person' and merged_into is not null) as person_merged,
       (select count(*) from party where org_id is not null)                       as with_org,
       (select count(*) from lead)                                                 as leads,
       (select count(*) from client)                                               as clients,
       (select count(*) from vendor)                                               as vendors,
       (select count(*) from _org_plan)                                            as planned_losers,
       (select count(*) from party p where p.org_id in (select loser_id from _org_plan))
                                                                                   as planned_repoints,
       (select count(*) from party
         where kind = 'org' and merged_into is null and deleted_at is null
           and lower(btrim(name)) = 'henry schein')                                as schein_before,
       (select count(*) from (
          select 1 from party where kind = 'person' and deleted_at is null
           group by lower(btrim(name)) having count(*) > 1) x)                     as person_dup_groups;

-- ── 4. salvage, then repoint, then tombstone (order matters) ─────────────────────────────
-- Eleven groups carry a city on one row and null on another; no group carries two different
-- non-null cities. Take the non-null value deterministically so the survivor is at least as
-- complete as any row it replaces.
update party s
   set city   = coalesce(s.city,   pick.city),
       county = coalesce(s.county, pick.county),
       updated_by = (select id from actor where slug = 'system')
  from (select survivor,
               (array_remove(array_agg(city   order by loser_id), null))[1] as city,
               (array_remove(array_agg(county order by loser_id), null))[1] as county
          from _org_plan group by survivor) pick
 where s.id = pick.survivor
   and (s.city is null or s.county is null);

-- Record the mapping BEFORE the update destroys it.
insert into org_merge_log (party_id, from_org, to_org, identity_key)
select p.id, pl.loser_id, pl.survivor, pl.identity_key
  from party p join _org_plan pl on pl.loser_id = p.org_id
    on conflict (party_id, from_org) do nothing;

update party p
   set org_id     = pl.survivor,
       updated_by = (select id from actor where slug = 'system')
  from _org_plan pl
 where p.org_id = pl.loser_id;

update party o
   set merged_into = pl.survivor,
       updated_by  = (select id from actor where slug = 'system')
  from _org_plan pl
 where o.id = pl.loser_id;

-- One event per collapsed row. cause='import_migration' is the existing precedent for a
-- migration-authored event (0001's imports, 0032's repairs); actor is 'system' because no
-- human stated this, a measurement did.
insert into event (occurred_at, recorded_at, actor_id, verb, subject_type, subject_id,
                   field, old_value, new_value, cause, agent_rationale)
select now(), now(), (select id from actor where slug = 'system'),
       'consolidate-org', 'party', pl.loser_id,
       'merged_into',
       jsonb_build_object('merged_into', null, 'org_ref', o.ref, 'name', o.name),
       jsonb_build_object('merged_into', pl.survivor, 'identity_key', pl.identity_key),
       'import_migration',
       'Duplicate organisation row collapsed by 0059. Identity was minted by the contact, '
       'so one company existed once per employee; this row''s single inbound person was '
       'repointed at the surviving org row and the mapping recorded in org_merge_log.'
  from _org_plan pl join party o on o.id = pl.loser_id;

-- ── 5. the part that stops it coming back ────────────────────────────────────────────────
-- Without this, the migration is housework: add-party mints a fresh org row on every call
-- and 46 groups become 103 the moment candidate_pool is promoted. The predicate is narrow on
-- purpose — merged rows are history and must be allowed to sit alongside their survivor, and
-- a null key is a placeholder that has no identity to collide on.
create unique index if not exists party_org_identity_uniq
    on party (org_identity_key(name))
 where kind = 'org'
   and merged_into is null
   and deleted_at is null
   and org_identity_key(name) is not null;

comment on index party_org_identity_uniq is
  'One live row per organisation (0059). This is the fix; the consolidation is only the '
  'backlog. A genuine same-name collision (two unrelated "Lighthouse Dental") is resolved by '
  'disambiguating the NAME — the data already does this with "Carr Riggs Ingram (advisory)" '
  '— never by weakening the key. WARNING: add-party and add-premises do a blind insert and '
  'will raise unique_violation until both call sites use org_party_id().';

-- ── 6. the one-line fix for the call sites ───────────────────────────────────────────────
create or replace function org_party_id(p_name text, p_actor uuid) returns uuid
language plpgsql as $fn$
declare k text; found uuid;
begin
  k := org_identity_key(p_name);

  -- A placeholder has no identity, so it gets a fresh row exactly as today. Six unknowns are
  -- six unknowns, not one company.
  if k is null then
    insert into party (kind, name, created_by, updated_by)
         values ('org', p_name, p_actor, p_actor) returning id into found;
    return found;
  end if;

  select id into found from party
   where kind = 'org' and merged_into is null and deleted_at is null
     and org_identity_key(name) = k
   limit 1;
  if found is not null then return found; end if;

  begin
    insert into party (kind, name, created_by, updated_by)
         values ('org', btrim(p_name), p_actor, p_actor) returning id into found;
  exception when unique_violation then
    -- Two sessions created the same organisation at once. The index decided; re-read.
    select id into found from party
     where kind = 'org' and merged_into is null and deleted_at is null
       and org_identity_key(name) = k
     limit 1;
  end;
  return found;
end
$fn$;

comment on function org_party_id(text, uuid) is
  'Find-or-create an organisation party by normalised name, atomically (0059). Replaces the '
  'blind `insert into party ... values (''org'', $1, ...)` at mcp-server/src/tools.js:1156 '
  '(add-party) and :1281 (add-premises), which is what minted 17 Henry Schein rows. Race-safe '
  'against party_org_identity_uniq: the loser of a concurrent insert re-reads rather than '
  'failing. A placeholder name still mints a fresh row, on purpose.';

-- ── guards BEFORE commit, so a failure rolls the whole thing back ────────────────────────
-- (0043's lesson is that a guard which cannot roll back is a report. 0046 and 0056 state it
-- and then place the block after `commit;`, where migrate.py has already ended the
-- transaction — 0052 and 0055 do it correctly and this follows them.)
do $$
declare
  b record;
  orgs_now int; org_merged_now int; persons_now int; person_merged_now int;
  with_org_now int; parties_now int; live_keyed int; distinct_keys int;
  dup_groups int; logged int; dangling int; schein_now int;
  person_dups_now int; leads_now int; clients_now int; vendors_now int;
begin
  select * into b from _org_before;

  select count(*) filter (where kind = 'org'),
         count(*) filter (where kind = 'org'    and merged_into is not null),
         count(*) filter (where kind = 'person'),
         count(*) filter (where kind = 'person' and merged_into is not null),
         count(*) filter (where org_id is not null),
         count(*)
    into orgs_now, org_merged_now, persons_now, person_merged_now, with_org_now, parties_now
    from party;

  -- (1) NOTHING WAS DELETED. The whole reversibility claim rests on this one line.
  if parties_now <> b.parties or orgs_now <> b.orgs then
    raise exception 'party rows changed: % -> % total, % -> % org. This migration must '
                    'delete nothing; it only tombstones', b.parties, parties_now, b.orgs, orgs_now;
  end if;

  -- (2) NO PERSON WAS TOUCHED. Three independent ways of saying it, because this is the one
  -- thing the migration is forbidden to do and a single check can be defeated by an accident.
  if persons_now <> b.persons then
    raise exception 'person count changed % -> %', b.persons, persons_now;
  end if;
  if person_merged_now <> b.person_merged then
    raise exception 'person merged_into count changed % -> % — THIS MIGRATION MUST NOT MERGE '
                    'A PERSON. That is confirm-merge''s job, one at a time, with a human '
                    'looking at both records', b.person_merged, person_merged_now;
  end if;
  select count(*) into person_dups_now from (
    select 1 from party where kind = 'person' and deleted_at is null
     group by lower(btrim(name)) having count(*) > 1) x;
  if person_dups_now <> b.person_dup_groups then
    raise exception 'person duplicate-name groups changed % -> % — no person merge may '
                    'happen here', b.person_dup_groups, person_dups_now;
  end if;

  -- (3) THE BACKLOG IS GONE. Zero live org identity keys repeat.
  select count(*), count(distinct org_identity_key(name))
    into live_keyed, distinct_keys
    from party
   where kind = 'org' and merged_into is null and deleted_at is null
     and org_identity_key(name) is not null;
  if live_keyed <> distinct_keys then
    raise exception '% live org rows for % distinct identities — consolidation incomplete',
                    live_keyed, distinct_keys;
  end if;
  select count(*) into dup_groups from (
    select 1 from party
     where kind = 'org' and merged_into is null and deleted_at is null
       and org_identity_key(name) is not null
     group by org_identity_key(name) having count(*) > 1) x;
  if dup_groups <> 0 then
    raise exception '% duplicate org group(s) survived', dup_groups;
  end if;

  -- (4) THE ARITHMETIC RECONCILES. Losers tombstoned == losers planned, and every person who
  -- had an employer still has one.
  if org_merged_now <> b.org_merged + b.planned_losers then
    raise exception 'org merged_into is % , expected % (% before + % planned)',
                    org_merged_now, b.org_merged + b.planned_losers, b.org_merged, b.planned_losers;
  end if;
  if with_org_now <> b.with_org then
    raise exception 'people carrying an org went % -> % — the repoint dropped somebody''s '
                    'employer', b.with_org, with_org_now;
  end if;

  -- (5) THE REVERSAL IS ACTUALLY POSSIBLE. One log row per repoint, or the change is not
  -- reversible and the header is lying.
  select count(*) into logged from org_merge_log;
  if logged <> b.planned_repoints then
    raise exception 'org_merge_log holds % row(s) for % repoint(s) — without a complete '
                    'mapping this change cannot be reversed', logged, b.planned_repoints;
  end if;

  -- (6) NO PERSON POINTS AT A TOMBSTONE. A dangling org_id is exactly the fault 0055 had to
  -- repair one level up, and it would be silent here.
  select count(*) into dangling
    from party p join party o on o.id = p.org_id
   where o.merged_into is not null;
  if dangling <> 0 then
    raise exception '% person(s) still point at a merged org row', dangling;
  end if;

  -- (7) THE REPORTED SYMPTOM, by name. A migration that collapses everything except the case
  -- that prompted it is not a fix.
  select count(*) into schein_now from party
   where kind = 'org' and merged_into is null and deleted_at is null
     and lower(btrim(name)) = 'henry schein';
  if b.schein_before > 1 and schein_now <> 1 then
    raise exception 'Henry Schein: % live org row(s) before, % after, expected exactly 1',
                    b.schein_before, schein_now;
  end if;

  -- (8) PLACEHOLDERS SURVIVED AS SEPARATE ROWS. The inverse assertion: this migration is
  -- also forbidden from asserting that six unknowns are one company.
  if (select count(*) from party
       where kind = 'org' and merged_into is null and org_identity_key(name) is null) < 11 then
    raise exception 'placeholder org rows were collapsed — "(TBD — enrich)" is not a company '
                    'and merging those rows fabricates an employment fact';
  end if;

  -- (9) THE ROLE LAYER IS UNTOUCHED, and the resolver still double-counts nothing. Reuses
  -- 0058's helper rather than restating the argument, which is what it exists for.
  select (select count(*) from lead), (select count(*) from client), (select count(*) from vendor)
    into leads_now, clients_now, vendors_now;
  if (leads_now, clients_now, vendors_now) <> (b.leads, b.clients, b.vendors) then
    raise exception 'role rows changed: lead %->%, client %->%, vendor %->% — this migration '
                    'touches parties only', b.leads, leads_now, b.clients, clients_now,
                    b.vendors, vendors_now;
  end if;
  perform assert_view_disjoint('v_ref_index', '(subject_type, subject_id)');

  raise notice 'org identity live. % org rows kept (0 deleted), % collapsed into % surviving '
               'organisations, % people repointed and logged for reversal. Henry Schein: % '
               'rows -> %. % placeholder rows left separate on purpose. Persons untouched at '
               '% (% merged, unchanged). party_org_identity_uniq now makes a duplicate '
               'IMPOSSIBLE — WHICH MEANS add-party AND add-premises RAISE unique_violation '
               'UNTIL BOTH CALL SITES USE org_party_id($1,$2). Ship that line with this.',
               orgs_now, b.planned_losers, distinct_keys, logged,
               b.schein_before, schein_now,
               (select count(*) from party where kind = 'org' and merged_into is null
                 and org_identity_key(name) is null),
               persons_now, person_merged_now;
end $$;

commit;
