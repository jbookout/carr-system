-- 0061_national_account.sql — one brand, one account, thirteen sub-clients, thirteen deals.
--
-- FULL REASONING, THE MEASUREMENTS, AND THE JUDGMENT CALLS: 0061_national_account_spec.md,
-- beside this file. This header carries the part a reader needs in order to judge the SQL.
--
-- DEPENDS ON 0060 (participant sides) and 0059 (org identity). It touches no
-- deal_participant row, but it re-points deals, and 0060 is the file that establishes what a
-- deal's participant rows mean before anything starts moving deals around.
--
-- JOE'S RULING, 2026-08-02, which this file implements literally: "A franchise or
-- multi-location brand is ONE national account, and each franchisee is their OWN SUB-CLIENT
-- under that main client — not a line item on it. Each franchisee also carries their OWN
-- SALESFORCE DEAL, so the deal grain is per franchisee, never per brand. Structurally: the
-- parent is a single org party with one client record over it (client_type national_account);
-- each franchisee is a person party whose party.org_id points at that ONE parent org; each
-- deal attaches to the FRANCHISEE'S client, never to the parent and never to another
-- franchisee. Deals move DOWN to the franchisee, never up to the parent. Also: the brand is
-- NOT a segment. Segment holds the vertical; the national flag is a lane; the account is the
-- parent-client link."
--
-- THE DEFECT, measured read-only against production before a line of this was written.
-- Thirteen deals carry segment 'Musicologie'. TWELVE of them are attached to the wrong
-- client: all thirteen point at C-131, whose party is P-0301 Anjali Trambadia. That is
-- correct for exactly one deal, "Trambadia – Marietta/Smyrna GA". The other twelve belong to
-- twelve different franchisees who each ALREADY have their own person party and their own
-- client row — C-132, C-135, C-136, C-137, C-138, C-139, C-142, C-144, C-147, C-148, C-149,
-- C-150. Those twelve clients hold ZERO deals between them; C-131 holds all thirteen. So the
-- data already had the sub-client structure Joe describes, and the deal grain was the part
-- that collapsed: every franchisee's deal got filed under whichever franchisee happened to
-- be first.
--
-- 0059 did the layer underneath this and did it correctly: Musicologie existed as 13 org
-- party rows and is now 1 (P-0111) with 12 tombstoned into it, and every one of the 13
-- franchisee person parties already has org_id = P-0111. The parent link Joe describes is
-- therefore ALREADY TRUE at the party level and this file must not disturb it — it only has
-- to put a client record over the org and stop the deals pointing sideways.
--
-- ── THE MATCH IS EXACT AND NEEDS NO NAME INFERENCE, WHICH WAS NOT OBVIOUS ────────────────
--
-- I was warned that "Ryan Lehman – Clifton NJ" would not match party "Ryan Lehmann"
-- (double n) and told to leave anything inexact NULL and report it rather than guess. That
-- warning is correct about deal.name and irrelevant to this migration, because deal.name is
-- the wrong column to match on.
--
-- Every deal carries its Salesforce import verbatim in deal.source_row, and that jsonb has a
-- dedicated `contact` field. For "Ryan Lehman – Clifton NJ", source_row->>'contact' is
-- 'Ryan Lehmann' — spelled with two n's, exactly as the party is. Checked across all 13:
-- source_row->>'contact' matches a live kind='person' party on EXACT equality, one party each
-- (never zero, never two), and each of those parties carries exactly one live client. Thirteen
-- for thirteen, no normalisation, no similarity function, no fallback.
--
-- The plan is therefore built ONLY from exact equality, and the guard asserts all 13 resolved
-- before anything commits. If a single one fails to resolve the migration raises and applies
-- nothing — there is no partial mode and no inferred branch, because Joe's standing rule is
-- that an identity link is never asserted on inference and the correct behaviour for an
-- unresolvable row is to stop, not to pick the closest name.
--
-- ── THE THREE THINGS DEAL.SEGMENT WAS HOLDING AT ONCE ────────────────────────────────────
--
-- Joe's ruling names three separate facts that had all been crammed into one column. The
-- source data already separated two of them and the IMPORTER merged them, which is provable
-- rather than inferred: deal.source_row carries `seg` AND `lane` as distinct keys.
--
--     lane:  territory 25, national 15. Present and non-empty on all 40 deals.
--     seg:   equals the stored deal.segment on 38 of 40 deals.
--     The 2 that differ are Brett Moorman – St. Louis MO and Kumar Tadepalli – Cumming GA.
--     On both, source `seg` is the EMPTY STRING and source `lane` is 'national', and the
--     stored segment reads 'national'. The importer fell back to the lane when the segment
--     was blank, and the lane has been sitting in the segment column ever since.
--
-- So this file adds deal.lane, backfills it from source_row->>'lane' (pure transcription of a
-- field that already exists on every row — no inference anywhere), and takes the brand and
-- the lane back out of segment.
--
-- THE VERTICAL FOR MUSICOLOGIE IS SET TO NULL, AND THAT IS THE HONEST ANSWER RATHER THAN A
-- CONVENIENT ONE. I was asked to determine the correct vertical from the data or vault
-- instead of inventing one, and to say so plainly if there is none. There is none.
-- Musicologie is a music-lesson franchise. The vertical values in live use are Chiro, DPC,
-- Dental, Fitness, Healthcare, Legal, Ortho, Other and Vet; the vault's vertical references
-- cover dental, medical, vision and veterinary. Music education is not any of them, the
-- Salesforce `seg` field for these 13 rows says 'Musicologie' and nothing else, and there is
-- no segment vocabulary table anywhere in the schema to consult. Writing 'Other' would be
-- inventing a classification; writing 'Fitness' or 'Healthcare' would be worse. NULL means
-- "the vertical for these deals is not known", which is exactly true, and it REMOVES a value
-- that is known to be wrong rather than replacing it with one that is merely unverified. The
-- options for Joe to choose from are listed in the spec. Setting them later is a one-line
-- update; un-inventing a fabricated classification after the exporters have rendered it for a
-- month is not.
--
-- THE ONE JUDGMENT CALL, FLAGGED LOUDLY BECAUSE IT TOUCHES ROWS JOE HAS NOT RULED ON. Brett
-- Moorman (C-141) and Kumar Tadepalli (C-143) carry segment 'national'. Their business-model
-- question — whether they are national accounts in Joe's sense, each with a parent brand — is
-- OPEN and this file does not answer it. Their client rows, their party rows, their
-- self-named org parties (P-0872, P-0908) and their client_type are all left exactly as they
-- are. What this file does change is the single column value 'national' in deal.segment, to
-- NULL, because source_row proves it is the lane leaking into the segment column on a row
-- whose segment was blank. No information is lost: the lane is preserved in deal.lane, which
-- this migration creates and populates for all 40 deals. If Joe disagrees, the reversal is one
-- statement and it is in the list below. What is NOT done, deliberately, is giving them a
-- parent org, a national_account client, or any of the Musicologie treatment.
--
-- ── WHY NO parent_client_id COLUMN ───────────────────────────────────────────────────────
--
-- The obvious move is client.parent_client_id. It is not needed, and adding it would create a
-- second way to express a fact the database already holds. Joe's own wording is the argument:
-- "each franchisee is a person party whose party.org_id points at that ONE parent org". The
-- chain franchisee client -> party -> org_id -> P-0111 -> parent client already resolves, is
-- already populated for all 13 (0059 did it), and is already the mechanism every other
-- consumer uses to reach an org. A parent_client_id column would have to be kept in agreement
-- with party.org_id forever, and the day they disagree there would be no way to tell which
-- one is right. v_client_account, below, makes the existing chain a first-class read instead.
-- The reopen condition is genuine and in the spec: if an account ever needs to span two org
-- parties, or a client needs a parent that is not its employer, extract the column.
--
-- FIRST CLIENT IN THE SYSTEM OVER AN ORG PARTY, which is worth stating because it is a
-- structural first: all 168 existing client rows sit over kind='person' parties. Nothing
-- forbids an org — client.party_id is a plain FK to party(id) with no kind filter, and the
-- consumers read party.name, which an org row has. It is also the first populated client_type
-- in the table: all 168 existing clients have client_type NULL, and the vocabulary
-- (independent, group, dso, franchise, regional_system, national_account) has been seeded and
-- unused since 0002.
--
-- v_ref_index ABSORBS THIS WITHOUT DOUBLE-COUNTING, and it is worth checking rather than
-- assuming, because 0058 turned that exact claim into a test that will now run against a
-- shape 0056 never saw. The party branch is guarded by
-- `NOT EXISTS (select 1 from client c where c.party_id = p.id)`, so P-0111 leaves the party
-- branch at the instant it gains a client row and arrives in the client branch. The total is
-- unchanged and no party surfaces twice; the guard below asserts the branch counts moved by
-- exactly one in each direction rather than trusting the reasoning. One consequence to know
-- about: `find "Musicologie"` now resolves the brand to a CLIENT ref instead of a party ref.
-- It returned 13 rows before (the org party plus 12 tombstones) and returns 13 now (the
-- client plus the same 12 tombstones), so nothing became more ambiguous than it already was.
--
-- REVERSAL, no row deleted and no fact destroyed. The re-point is recorded in
-- deal_reattach_log before it happens, which is the only reason the client move is
-- reversible; the segment reverts from deal.source_row, which no migration ever writes and
-- which holds the Salesforce import verbatim on all 40 deals. The spec walks through why the
-- log is NOT the right source for the segment half (it has rows only for the 12 that moved,
-- and 13 had their segment cleared).
--
--     update deal d set client_id = r.from_client
--       from deal_reattach_log r where r.deal_id = d.id;
--     update deal set segment = nullif(source_row->>'seg', '')
--      where segment is null and source_row->>'seg' is not null;
--     update deal set segment = 'national'
--      where name in ('Brett Moorman – St. Louis MO','Kumar Tadepalli – Cumming GA');
--     update client set vertical = 'Musicologie', client_type = null
--      where id in (select c.id from client c join party p on p.id=c.party_id
--                    where p.org_id = (select id from party where ref='P-0111'));
--     delete from client where party_id = (select id from party where ref='P-0111');
--     drop view v_client_account;  alter table deal drop column lane;  drop table deal_lane;
--
-- NOTHING IS DELETED BY THIS MIGRATION and no party row is touched at all. 0059's org
-- consolidation is asserted intact, by name, in the guard.

begin;

-- ── 1. the lane, which is a fact the source data already carried ─────────────────────────
-- A vocabulary table rather than free text, matching deal_phase / deal_type_ref /
-- client_type, and specifically NOT matching deal.segment — segment is free text with no FK,
-- which is a large part of how a brand ended up living in it.
create table if not exists deal_lane (
  slug  text primary key,
  label text not null,
  sort  int  not null,
  note  text
);

insert into deal_lane (slug, label, sort, note) values
  ('territory', 'Territory', 10,
   'Inside Joe and Dell''s own market: South Alabama through the Florida Panhandle. The '
   'default lane and the one the pipeline work is built around.'),
  ('national', 'National', 20,
   'A national-account deal, worked under a brand-level relationship and usually outside the '
   'home territory. A LANE, not a segment and not a vertical: a national deal still has a '
   'vertical, and a territory deal can belong to a national account. National accounts are a '
   'separate business model (DNA/Leads/pipeline-craft.md Part C).')
  on conflict (slug) do nothing;

alter table deal add column if not exists lane text references deal_lane(slug);

comment on column deal.lane is
  'Territory or national (0061). Transcribed verbatim from deal.source_row->>''lane'', which '
  'the Salesforce import has always carried and which the importer discarded. Distinct from '
  'segment ON PURPOSE (Joe, 2026-08-02: "the brand is NOT a segment. Segment holds the '
  'vertical; the national flag is a lane; the account is the parent-client link") — before '
  'this column existed, two deals whose source segment was blank had the string ''national'' '
  'written into deal.segment instead, which is how a lane came to be mistaken for a vertical.';

update deal d
   set lane = d.source_row->>'lane',
       updated_by = (select id from actor where slug = 'system')
 where d.source_row->>'lane' in ('territory', 'national')
   and d.lane is distinct from d.source_row->>'lane';

-- ── 2. the plan, computed once from EXACT matches only ───────────────────────────────────
-- source_row->>'contact', not deal.name. The name would need fuzzy matching ("Ryan Lehman"
-- vs "Ryan Lehmann") and fuzzy matching is forbidden here; the contact field does not.
create temporary table _musico_plan on commit drop as
select d.id                        as deal_id,
       d.name                      as deal_name,
       d.client_id                 as from_client,
       d.segment                   as from_segment,
       c.id                        as to_client,
       c.roster_ref                as to_client_ref,
       p.id                        as franchisee_party,
       p.ref                       as franchisee_ref,
       d.source_row->>'contact'    as matched_on
  from deal d
  join party p on p.name = d.source_row->>'contact'
               and p.kind = 'person' and p.merged_into is null and p.deleted_at is null
  join client c on c.party_id = p.id and c.merged_into is null
 where d.segment = 'Musicologie';

-- The reversibility artifact, written BEFORE anything moves. merged_into records that org
-- rows collapsed (0059); nothing else records which deal used to hang off which client, and
-- the update destroys it.
create table if not exists deal_reattach_log (
  deal_id      uuid        not null references deal(id),
  from_client  uuid        not null references client(id),
  to_client    uuid        not null references client(id),
  from_segment text,
  reason       text        not null,
  moved_at     timestamptz not null default now(),
  primary key (deal_id, from_client)
);

comment on table deal_reattach_log is
  'Deal -> old client -> new client, one row per re-point (0061). The record that makes a '
  'client re-attachment reversible: deal.client_id is a single column and overwriting it '
  'destroys the only copy of where the deal used to sit. Append-only; a record of what '
  'happened, not a work queue.';

create temporary table _na_before on commit drop as
select (select count(*) from deal)                                              as deals,
       (select count(*) from client)                                            as clients,
       (select count(*) from party)                                             as parties,
       (select count(*) from deal_participant)                                  as participants,
       (select count(*) from deal where segment = 'Musicologie')                as musico_deals,
       (select count(*) from _musico_plan)                                      as planned,
       (select count(*) from _musico_plan where from_client <> to_client)        as planned_moves,
       (select count(*) from deal d join client c on c.id = d.client_id
         where c.roster_ref = 'C-131')                                          as on_c131,
       (select count(*) from party where kind = 'org' and merged_into is null
          and lower(btrim(name)) = 'musicologie')                               as live_musico_orgs,
       (select count(*) from party p
         where p.org_id = (select id from party where ref = 'P-0111'))          as under_parent_org,
       (select count(*) from client where client_type is not null)              as typed_clients,
       (select count(*) from deal where segment = 'national')                   as lane_in_segment,
       (select count(*) from v_ref_index)                                       as refidx_rows,
       (select count(*) from v_ref_index where subject_type = 'party')          as refidx_party,
       (select count(*) from v_ref_index where subject_type = 'client')         as refidx_client;

-- ── 3. the parent account, over the ONE surviving org party 0059 left ────────────────────
-- status 'engaged' by the vocabulary's own definition: "Live relationship, no open deal. On
-- the active book." That is literally and permanently the parent's position, because Joe's
-- grain rule says the deals live on the franchisees and never on the brand.
insert into client (roster_ref, party_id, client_type, status, notes, created_by, updated_by)
select 'C-' || lpad(nextval('ref_client_seq')::text, 3, '0'),
       p.id,
       'national_account',
       'engaged',
       'Musicologie national account (0061). The PARENT client over the brand''s single org '
       'party. Holds no deals by design: the deal grain is per franchisee, so every '
       'Musicologie deal attaches to that franchisee''s own client. The children are found '
       'through party.org_id, not through a column on this row — see v_client_account.',
       (select id from actor where slug = 'system'),
       (select id from actor where slug = 'system')
  from party p
 where p.ref = 'P-0111'
   and p.kind = 'org' and p.merged_into is null
   and not exists (select 1 from client c where c.party_id = p.id and c.merged_into is null);

-- ── 4. the deals move DOWN to the franchisee, never up to the parent ─────────────────────
insert into deal_reattach_log (deal_id, from_client, to_client, from_segment, reason)
select pl.deal_id, pl.from_client, pl.to_client, pl.from_segment,
       'Musicologie franchisee deal filed under the wrong franchisee''s client. Re-pointed to '
       'the client of the person named in deal.source_row->>''contact'' (' || pl.matched_on ||
       '), matched on exact equality with a live person party. 0061.'
  from _musico_plan pl
 where pl.from_client <> pl.to_client
    on conflict (deal_id, from_client) do nothing;

update deal d
   set client_id  = pl.to_client,
       updated_by = (select id from actor where slug = 'system')
  from _musico_plan pl
 where d.id = pl.deal_id and pl.from_client <> pl.to_client;

insert into event (occurred_at, recorded_at, actor_id, verb, subject_type, subject_id,
                   field, old_value, new_value, cause, agent_rationale)
select now(), now(), (select id from actor where slug = 'system'),
       'reattach-deal', 'deal', pl.deal_id, 'client_id',
       jsonb_build_object('client_ref', 'C-131', 'client_name', 'Anjali Trambadia'),
       jsonb_build_object('client_ref', pl.to_client_ref, 'party_ref', pl.franchisee_ref,
                          'matched_on', pl.matched_on),
       'import_migration',
       'All 13 Musicologie deals were attached to C-131 (Anjali Trambadia), correct for one '
       'of them. Under Joe''s 2026-08-02 national-account ruling the deal grain is per '
       'franchisee, so this deal moved to its own franchisee''s existing client. Matched on '
       'exact equality between deal.source_row->>''contact'' and a live person party name; no '
       'name inference was used, because deal.name disagrees with the party spelling on one '
       'row ("Ryan Lehman" vs "Ryan Lehmann") and the source contact field does not.'
  from _musico_plan pl
 where pl.from_client <> pl.to_client;

-- ── 5. the brand comes out of the columns that hold the vertical ─────────────────────────
-- Both levels, because leaving 'Musicologie' in client.vertical while taking it out of
-- deal.segment would just move the same defect one table over.
update deal d
   set segment    = null,
       updated_by = (select id from actor where slug = 'system')
  from _musico_plan pl
 where d.id = pl.deal_id;

update client c
   set client_type = 'franchise',
       vertical    = null,
       updated_by  = (select id from actor where slug = 'system')
  from party p
 where p.id = c.party_id
   and p.org_id = (select id from party where ref = 'P-0111')
   and c.merged_into is null;

-- The two rows Joe has NOT ruled on. ONLY the segment string moves, and only because
-- source_row proves it is the lane: seg is '' and lane is 'national' on both. Their clients,
-- parties, org parties and client_type are untouched.
update deal d
   set segment    = null,
       updated_by = (select id from actor where slug = 'system')
 where d.segment = 'national'
   and coalesce(d.source_row->>'seg', '') = ''
   and d.source_row->>'lane' = 'national';

-- ── 6. the parent-client link, as a read rather than a column ────────────────────────────
create or replace view v_client_account as
select c.id                as client_id,
       c.roster_ref        as client_ref,
       p.name              as client_name,
       c.client_type,
       c.status,
       org.id              as account_party_id,
       org.ref             as account_party_ref,
       org.name            as account_name,
       pc.id               as account_client_id,
       pc.roster_ref       as account_client_ref,
       pc.client_type      as account_client_type,
       pc.id is not null and pc.id <> c.id as is_sub_client,
       (select count(*) from deal d where d.client_id = c.id) as deals
  from client c
  join party p        on p.id  = c.party_id
  left join party org on org.id = p.org_id and org.merged_into is null
  left join client pc on pc.party_id = org.id and pc.merged_into is null
                     and pc.client_type = 'national_account'
 where c.merged_into is null;

comment on view v_client_account is
  'Every live client with the national account it sits under, if any (0061). The parent link '
  'is NOT a column: it resolves client -> party -> party.org_id -> the org''s '
  'client_type=''national_account'' client row, which is the chain 0059 already populated and '
  'every other consumer already uses to reach an org. Deliberately no client.parent_client_id '
  '— a second representation of the same fact has to be kept in agreement forever, and on the '
  'day the two disagree there is no way to tell which is right. is_sub_client is the test '
  'Joe''s ruling turns on: a franchisee is its OWN client under the account, never a line '
  'item on it, and deals hang off the sub-client. Reopen the column question if an account '
  'ever spans two org parties, or a client needs a parent that is not its employer.';

-- ── guards BEFORE commit, so a failure rolls the whole thing back ────────────────────────
do $$
declare
  b record;
  deals_now int; clients_now int; parties_now int; participants_now int;
  planned int; unresolved int; on_c131 int; parent_ref text; parent_deals int;
  franchisee_clients int; franchisee_deals int; brand_segments int; lane_segments int;
  laneless int; under_org int; live_orgs int; moved int; logged int; flagged int;
begin
  select * into b from _na_before;

  -- (1) ALL THIRTEEN RESOLVED EXACTLY, OR NOTHING APPLIES. This is Joe's no-inference rule
  -- expressed as a precondition: a deal whose person cannot be matched exactly is not guessed
  -- at and is not skipped either — it stops the migration.
  select count(*) into planned from _musico_plan;
  if planned <> b.musico_deals then
    select count(*) into unresolved from deal d
      where d.segment = 'Musicologie'
        and not exists (select 1 from _musico_plan pl where pl.deal_id = d.id);
    raise exception 'only % of % Musicologie deals resolved to a franchisee by EXACT match on '
                    'source_row->>''contact''; % did not. Nothing is guessed here — fix the '
                    'unmatched rows and re-run.', planned, b.musico_deals, unresolved;
  end if;
  if planned <> 13 then
    raise exception 'expected 13 Musicologie deals, planned %', planned;
  end if;
  -- No two deals may resolve to the same franchisee: 13 deals, 13 distinct people.
  if (select count(distinct to_client) from _musico_plan) <> 13 then
    raise exception 'the 13 Musicologie deals resolved to only % distinct franchisee clients '
                    '— the whole point is one deal per franchisee',
                    (select count(distinct to_client) from _musico_plan);
  end if;

  -- (2) NOTHING WAS CREATED OR DESTROYED except the one parent client.
  select (select count(*) from deal), (select count(*) from client),
         (select count(*) from party), (select count(*) from deal_participant)
    into deals_now, clients_now, parties_now, participants_now;
  if deals_now <> b.deals then
    raise exception 'deal count changed % -> % — this migration re-points deals, never '
                    'creates or removes one', b.deals, deals_now;
  end if;
  if clients_now <> b.clients + 1 then
    raise exception 'client count is %, expected % (% before + exactly 1 parent account)',
                    clients_now, b.clients + 1, b.clients;
  end if;
  if parties_now <> b.parties then
    raise exception 'party count changed % -> % — 0061 touches no party row at all',
                    b.parties, parties_now;
  end if;
  if participants_now <> b.participants then
    raise exception 'deal_participant changed % -> % — 0061 must not touch it; role=''lead'' '
                    'is the owning AGENT and re-pointing a deal''s client does not change who '
                    'works it', b.participants, participants_now;
  end if;

  -- (3) THE ACCOUNT EXISTS, IS THE ONLY ONE, AND HOLDS NO DEALS. "Deals move DOWN to the
  -- franchisee, never up to the parent" is the ruling's sharpest line, so it gets its own
  -- assertion rather than being implied by the arithmetic.
  select c.roster_ref, (select count(*) from deal d where d.client_id = c.id)
    into parent_ref, parent_deals
    from client c join party p on p.id = c.party_id
   where p.ref = 'P-0111' and c.merged_into is null;
  if parent_ref is null then
    raise exception 'no parent client was created over P-0111';
  end if;
  if parent_deals <> 0 then
    raise exception 'the Musicologie parent account % holds % deal(s). Deals move DOWN to the '
                    'franchisee, never up to the parent', parent_ref, parent_deals;
  end if;
  if (select count(*) from client where client_type = 'national_account') <> 1 then
    raise exception 'expected exactly 1 national_account client, found %',
                    (select count(*) from client where client_type = 'national_account');
  end if;

  -- (4) ONE DEAL PER FRANCHISEE, AND C-131 IS BACK DOWN TO ITS OWN.
  select count(*) into on_c131 from deal d join client c on c.id = d.client_id
   where c.roster_ref = 'C-131';
  if b.on_c131 <> 13 then
    raise exception 'C-131 held % deals before, expected 13 — the data has drifted from what '
                    'this migration was measured against', b.on_c131;
  end if;
  if on_c131 <> 1 then
    raise exception 'C-131 holds % deals after, expected exactly 1 (Trambadia – '
                    'Marietta/Smyrna GA)', on_c131;
  end if;
  select count(*), sum(deals) into franchisee_clients, franchisee_deals
    from v_client_account where account_party_ref = 'P-0111' and is_sub_client;
  if franchisee_clients <> 13 or franchisee_deals <> 13 then
    raise exception '% franchisee sub-clients holding % deals; expected 13 and 13',
                    franchisee_clients, franchisee_deals;
  end if;
  if exists (select 1 from v_client_account
              where account_party_ref = 'P-0111' and is_sub_client and deals <> 1) then
    raise exception 'a Musicologie franchisee holds a number of deals other than 1 — the '
                    'grain is one Salesforce deal per franchisee';
  end if;

  -- (5) THE REVERSAL IS POSSIBLE. One log row per move, or the header is lying.
  select count(*) into moved from _musico_plan where from_client <> to_client;
  select count(*) into logged from deal_reattach_log;
  if moved <> 12 then
    raise exception '% deals needed moving, expected 12 (13 minus Trambadia, who was already '
                    'correct)', moved;
  end if;
  if logged <> moved then
    raise exception 'deal_reattach_log holds % row(s) for % move(s) — without a complete '
                    'mapping this change cannot be reversed', logged, moved;
  end if;

  -- (6) THE BRAND IS NO LONGER A SEGMENT, tested structurally rather than by name. Before
  -- this migration exactly one segment value collided with a live organisation's name:
  -- 'Musicologie'. Afterwards none may.
  select count(*) into brand_segments from (
    select distinct d.segment from deal d
     where d.segment is not null
       and exists (select 1 from party p where p.kind = 'org' and p.merged_into is null
                    and org_identity_key(p.name) = org_identity_key(d.segment))) x;
  if brand_segments <> 0 then
    raise exception '% deal.segment value(s) are the name of a live organisation. The brand '
                    'is not a segment (Joe, 2026-08-02); segment holds the vertical',
                    brand_segments;
  end if;

  -- (7) THE LANE IS NO LONGER A SEGMENT EITHER, and it is recorded where it belongs.
  select count(*) into lane_segments from deal
   where segment is not null and segment in (select slug from deal_lane);
  if lane_segments <> 0 then
    raise exception '% deal(s) still carry a LANE value in the segment column', lane_segments;
  end if;
  select count(*) into laneless from deal where lane is null;
  if laneless <> 0 then
    raise exception '% deal(s) have no lane; source_row->>''lane'' is populated on all 40 so '
                    'every one should have transcribed', laneless;
  end if;
  if (select count(*) from deal where lane = 'national') <> 15
     or (select count(*) from deal where lane = 'territory') <> 25 then
    raise exception 'lane split is national %, territory %; measured 15 and 25 in source_row',
                    (select count(*) from deal where lane = 'national'),
                    (select count(*) from deal where lane = 'territory');
  end if;
  -- Every lane matches its own source row. Transcription, not inference.
  if exists (select 1 from deal where lane is distinct from source_row->>'lane') then
    raise exception 'a deal.lane disagrees with its own source_row->>''lane''';
  end if;

  -- (8) 0059 IS INTACT. This file must not disturb the org consolidation underneath it.
  select count(*) into live_orgs from party
   where kind = 'org' and merged_into is null and lower(btrim(name)) = 'musicologie';
  if live_orgs <> 1 then
    raise exception 'Musicologie has % live org rows, expected the 1 that 0059 left', live_orgs;
  end if;
  select count(*) into under_org from party
   where org_id = (select id from party where ref = 'P-0111');
  if under_org <> b.under_parent_org or under_org <> 13 then
    raise exception 'parties under the Musicologie org went % -> % (expected 13, unchanged) — '
                    '0059''s parent link must survive this migration', b.under_parent_org, under_org;
  end if;
  perform assert_view_disjoint('v_client_account', 'client_id');
  perform assert_view_disjoint('v_ref_index', '(subject_type, subject_id)');
  -- 0056's claim, re-tested against a shape it never saw: a party holding a role must not
  -- also surface under the party branch. P-0111 just gained a client row, so it has to have
  -- LEFT the party branch in the same breath. Counts, not reasoning.
  perform assert_view_disjoint(
    $src$ (select party_id from v_ref_index where subject_type = 'party'
           union all
           select distinct party_id from v_ref_index
            where subject_type <> 'party' and party_id is not null) z $src$,
    'party_id');
  if (select count(*) from v_ref_index) <> b.refidx_rows then
    raise exception 'v_ref_index went % -> % rows; a client over an org party must MOVE P-0111 '
                    'from the party branch to the client branch, not add a row',
                    b.refidx_rows, (select count(*) from v_ref_index);
  end if;
  if (select count(*) from v_ref_index where subject_type = 'party') <> b.refidx_party - 1
     or (select count(*) from v_ref_index where subject_type = 'client') <> b.refidx_client + 1 then
    raise exception 'v_ref_index branches moved wrong: party % -> % (expected %), client % -> '
                    '% (expected %)',
                    b.refidx_party, (select count(*) from v_ref_index where subject_type='party'),
                    b.refidx_party - 1,
                    b.refidx_client, (select count(*) from v_ref_index where subject_type='client'),
                    b.refidx_client + 1;
  end if;
  if (select count(*) from v_orphaned_role) <> 0 then
    raise exception 'v_orphaned_role is no longer zero';
  end if;

  -- (9) THE FLAGGED ROWS, reported rather than asserted. Joe has not ruled on Moorman and
  -- Tadepalli; all this migration did to them is move a lane out of the segment column.
  select count(*) into flagged from deal
   where name in ('Brett Moorman – St. Louis MO', 'Kumar Tadepalli – Cumming GA')
     and segment is null and lane = 'national';
  if b.lane_in_segment <> 2 then
    raise exception 'expected 2 deals with segment=''national'' before, found %',
                    b.lane_in_segment;
  end if;
  if flagged <> 2 then
    raise exception 'the 2 lane-in-segment rows did not land as expected';
  end if;

  raise notice 'Musicologie is one national account. Parent client % created over org party '
               'P-0111 (client_type national_account, 0 deals, by design). % deals re-pointed '
               'to their own franchisee''s client and logged for reversal; % franchisee '
               'sub-clients now hold % deals, exactly one each; C-131 is back to 1. All 13 '
               'matched on EXACT source_row->>''contact'' equality, including Ryan Lehmann, '
               'whose deal NAME is misspelled — no inference was used anywhere. The brand is '
               'out of deal.segment and client.vertical; deal.lane now carries the lane for '
               'all 40 deals (% national / % territory). 0059 intact: 1 live Musicologie org, '
               '13 parties under it.',
               parent_ref, moved, franchisee_clients, franchisee_deals,
               (select count(*) from deal where lane = 'national'),
               (select count(*) from deal where lane = 'territory');

  raise notice 'FLAGGED FOR JOE, NOT RESOLVED HERE: Brett Moorman (C-141) and Kumar Tadepalli '
               '(C-143) had the LANE ''national'' sitting in deal.segment because their '
               'Salesforce seg field is blank. Their segment is now null and their lane is '
               '''national''. NOTHING ELSE about them changed — no parent org, no '
               'national_account client, no client_type. Whether they are national accounts '
               'in your sense is still open. Also open: the correct VERTICAL for the 13 '
               'Musicologie deals, which is null because music education is not any vertical '
               'this system has; options are in 0061_national_account_spec.md.';
end $$;

commit;
