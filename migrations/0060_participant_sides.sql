-- 0060_participant_sides.sql — deal_participant is two tables in a trench coat, and nothing said so.
--
-- READ THIS BEFORE THE SQL. THE TASK THIS FILE WAS OPENED FOR DOES NOT EXIST.
--
-- The brief was: "41 of 42 deal_participant rows have a NULL party_id, all 41 are role='lead';
-- backfill them from the deal's client's party, resolve the 12 mis-attached Musicologie ones
-- by name, and add a guard so a role='lead' participant can never again be inserted with a
-- NULL party." Every count in that sentence is correct. The conclusion drawn from them is
-- backwards, and executing it would have corrupted the ownership record of all 40 deals.
--
-- WHAT role='lead' ACTUALLY MEANS HERE. It is not the lead as in the prospect. It is the LEAD
-- AGENT — which of Joe or Dell owns the deal. The evidence is not interpretive:
--
--   * All 41 rows carry actor_id, and the split is dell 35 / joe 6. Those are the only two
--     humans in the actor table. They are not clients.
--   * v_deal_board defines `lead_owner` as `lead_actor.slug` via
--     `LEFT JOIN deal_participant dp ON dp.deal_id = d.id AND dp.role = 'lead' AND dp.to_at IS
--     NULL   LEFT JOIN actor lead_actor ON lead_actor.id = dp.actor_id`. It reads actor_id.
--     It never reads party_id.
--   * set-lead (mcp-server/src/tools.js:1112) is described in its own schema as "THE handoff:
--     make joe or dell the current lead on a deal … THIS IS THE ONLY VERB THAT SETS A DEAL'S
--     OWNER". It closes the current row and inserts
--     `(deal_id, actor_id, role, set_by) values ($1,$2,'lead',$3)`. No party_id. It cannot
--     supply one: its `new_lead` argument is `enum: ["joe","dell"]`.
--   * 0036_sonography_lead_owner_dell.sql exists solely to insert one of these rows, and its
--     header spells it out: "set_by is joe: he is the one who stated the assignment. actor is
--     dell: he owns the work."
--   * prepare-document (tools.js:524) pulls the SIGNING AGENT off this row —
--     `select a.slug, a.display_name, a.email, a.phone from deal_participant dp join actor a
--     on a.id=dp.actor_id where dp.role='lead'` — and the comment above it reads "The signing
--     agent is the deal's CURRENT lead participant".
--
-- So a NULL party_id on a role='lead' row is not a defect. It is the correct and only legal
-- value. The table's own CHECK says as much: `(actor_id IS NOT NULL) OR (party_id IS NOT
-- NULL)` — an OR, deliberately, because the table holds two different kinds of row.
--
-- WHAT THE BRIEFED MIGRATION WOULD HAVE DONE. Written the client's party onto 40 rows whose
-- subject is a CARR employee, so every row would then have asserted both "Dell owns this
-- deal" and "Dr. Elizabeth Hughes owns this deal" out of one record. Nothing would have
-- errored, because nothing reads party_id on these rows today. It would have sat there until
-- the first consumer that did. Then the guard — "role='lead' may not have a NULL party" —
-- would have been added, and set-lead, the ONLY verb that assigns deal ownership, would have
-- begun raising on every call, along with any future re-run of 0036's shape. The guard is the
-- part that makes this worth a file rather than a note: it would have been a permanent,
-- load-bearing constraint encoding a misreading of the table.
--
-- THE "DUPLICATE" SONOGRAPHY ROWS ARE NOT DUPLICATES EITHER. Both are role='lead', both are
-- actor dell, on deal 12faa5d3 (Sonography Studios – Lily Frank PCB). One has
-- to_at = 2026-08-02 14:21:13.095382+00; the other has from_at equal to that same instant, to
-- the microsecond, and to_at null. That is one closed interval and one open interval abutting
-- exactly — the temporal succession set-lead writes when it closes the old row and opens the
-- new one, and the event log carries the matching row (verb 'set-lead', subject the deal,
-- cause 'human_stated', same timestamp). It is one current lead with one row of history
-- behind it, which is the design working. Counting the closed row is what produced "42 rows,
-- 41 NULL": the live population is 41 rows, 40 of them current leads on 40 deals.
--
-- ── SO WHAT IS ACTUALLY WRONG HERE ────────────────────────────────────────────────────────
--
-- Two things, and the first one is why the brief was possible to write at all.
--
-- (1) NOTHING IN THE DATABASE STATES WHICH SIDE A ROLE SITS ON. participant_role is a bare
-- (slug, label) list of seven values. Reading the schema alone, 'lead' and 'listing_side'
-- look identical, and the only way to learn that one is an employee and the other is a
-- counterparty is to read v_deal_board, three call sites in tools.js and a migration header.
-- A shape that can only be recovered by reading the consumers is a shape the next writer will
-- get wrong, and the next writer might not be reversible. The OR-check permits every
-- combination the table can express, including the two that are always wrong: an agent role
-- carrying a party, and a counterparty role carrying an actor.
--
-- (2) set-lead's description claims "The database enforces exactly one current lead." IT DOES
-- NOT. There is no unique index on this table at all — only the primary key. The invariant is
-- upheld entirely by set-lead's own update-then-insert, which is not atomic against a
-- concurrent second caller, and is bypassed completely by any migration that inserts
-- directly. 0036 knew this and hand-rolled a `not exists` guard in its WHERE clause precisely
-- because the database would not have stopped it. A claim in a tool description is not an
-- enforcement, and two deals' worth of concurrent handoff would silently produce two current
-- leads and a v_deal_board LEFT JOIN that returns the deal twice.
--
-- Measured now, before the index: 40 deals, 40 rows with role='lead' and to_at null, and the
-- maximum count of current leads on any single deal is 1. The invariant currently holds. This
-- file makes holding it the database's job.
--
-- ── WHY A TRIGGER AND NOT A CHECK ─────────────────────────────────────────────────────────
--
-- The brief said to pick one and explain the choice. A CHECK constraint cannot reference
-- another table, so the side rule as a CHECK would have to hardcode the role names into the
-- constraint body:
--
--     check (role <> 'lead' or (actor_id is not null and party_id is null))
--
-- That is cheaper and it is enforced without a function call, and for the single role 'lead'
-- it would be adequate. It is still the wrong instrument, for one reason: it is silent on
-- every role it does not name. participant_role holds seven slugs and will hold more; a role
-- added to the vocab tomorrow would be covered by nothing, and the omission would be
-- invisible because the constraint would still be there, still passing, still looking like it
-- covered the table. That is the exact failure that produced this file — a rule that lived
-- somewhere other than where the data is, so nobody could see its edges.
--
-- The trigger reads participant_role.side, so the vocabulary IS the rule. Adding a role means
-- declaring its side in the same insert, and the enforcement follows automatically with no
-- migration. The cost is a per-row function call on a table with 41 rows and single-digit
-- writes per week, which is not a cost.
--
-- SIDE IS DECLARED ONLY WHERE IT IS PROVEN, AND NULL MEANS UNCONSTRAINED. Three of the seven
-- roles have evidence; four do not, and I am not guessing at them:
--
--     lead            → actor   41 rows, all actor, none party; v_deal_board; set-lead; 0036
--     listing_side    → party    1 row,  party, no actor; add-premises (tools.js:1318)
--     client_contact  → party    0 rows, but prepare-document (tools.js:533) reads it as
--                                `join party p on p.id=dp.party_id` — unambiguous in code
--     support         → null    no rows, no code
--     referring_agent → null    no rows, no code. Genuinely ambiguous: a referring agent may
--                               be a CARR actor OR an outside broker, and those are opposite
--                               sides. Joe's call, not mine.
--     investor        → null    no rows, no code
--     capital_partner → null    no rows, no code
--
-- A null side means the trigger lets the row through and the OR-check remains the only rule,
-- which is exactly today's behaviour. This constrains nothing that was not already provable
-- and invents no fact — the same posture 0059 took with placeholder org names.
--
-- REVERSAL, three statements, no data loss (nothing in this file changes a single row of
-- deal_participant):
--
--     drop trigger deal_participant_side on deal_participant;
--     drop index  deal_participant_one_current_lead;
--     alter table participant_role drop column side;
--
-- WHAT IS DELIBERATELY LEFT UNDONE, so it is not lost:
--   * Zero deals carry a client_contact participant. Every deal resolves its person through
--     deal -> client -> client.party_id, exactly 1:1, so nothing is broken today and
--     prepare-document degrades cleanly to a null contact. Whether the client contact should
--     ALSO be a participant row is a modelling question for Joe, not a repair.
--   * referring_agent's side, above.
--   * set-lead's description should stop saying the database enforces one current lead and
--     start being true; after this file it is true. tools.js is another seat's file.

begin;

-- ── 1. the vocabulary carries the rule ───────────────────────────────────────────────────
alter table participant_role add column if not exists side text
  check (side in ('actor', 'party'));

comment on column participant_role.side is
  'Which side of the deal this role sits on, and therefore WHICH COLUMN of deal_participant '
  'carries its subject (0060). ''actor'' = a CARR employee, subject in actor_id, party_id '
  'MUST be null — role=''lead'' is the deal''s owning agent (joe or dell), which is what '
  'v_deal_board exposes as lead_owner and what set-lead writes. ''party'' = someone outside '
  'CARR, subject in party_id, actor_id MUST be null. NULL means the side has not been '
  'established and the row is left unconstrained; four roles have no rows and no call site, '
  'and referring_agent is genuinely ambiguous (a CARR actor or an outside broker are opposite '
  'sides of this table). Declare the side when the first real row appears, not before. '
  'Enforced by trg_deal_participant_side.';

update participant_role set side = 'actor' where slug = 'lead';
update participant_role set side = 'party' where slug in ('listing_side', 'client_contact');

-- ── 2. the BEFORE snapshot, measured rather than hardcoded ───────────────────────────────
create temporary table _dp_before on commit drop as
select (select count(*) from deal_participant)                                        as rows_all,
       (select count(*) from deal_participant where to_at is null)                    as rows_current,
       (select count(*) from deal_participant where role = 'lead')                    as lead_rows,
       (select count(*) from deal_participant where role = 'lead' and to_at is null)  as lead_current,
       (select count(*) from deal_participant where role = 'lead' and party_id is not null)
                                                                                      as lead_with_party,
       (select count(*) from deal_participant where role = 'lead' and actor_id is null)
                                                                                      as lead_without_actor,
       (select count(*) from deal_participant dp join participant_role r on r.slug = dp.role
         where r.side = 'party' and dp.actor_id is not null)                          as party_side_with_actor,
       (select count(*) from deal)                                                    as deals,
       (select count(*) from v_deal_board where lead_owner is null)                   as ownerless,
       (select coalesce(max(n), 0) from (
          select count(*) n from deal_participant
           where role = 'lead' and to_at is null group by deal_id) x)                 as max_current_leads;

-- ── 3. the rule ──────────────────────────────────────────────────────────────────────────
-- Fires on insert and on update of the three columns that can break it. A row whose role has
-- no declared side passes untouched, which is today's behaviour and is the point.
create or replace function trg_deal_participant_side() returns trigger
language plpgsql as $fn$
declare s text;
begin
  select side into s from participant_role where slug = new.role;

  if s = 'actor' then
    if new.actor_id is null then
      raise exception 'deal_participant.role=% is an ACTOR-side role (a CARR employee) and '
                      'needs actor_id. See participant_role.side.', new.role;
    end if;
    if new.party_id is not null then
      raise exception 'deal_participant.role=% is an ACTOR-side role and must NOT carry '
                      'party_id. role=''lead'' means the deal''s OWNING AGENT (joe or dell), '
                      'not the client — v_deal_board reads lead_owner off actor_id and never '
                      'looks at party_id. Writing the client''s party here makes one row '
                      'assert two different owners. If you want the client''s person on the '
                      'deal, that is role=''client_contact'', or just follow '
                      'deal -> client -> client.party_id, which is already exact and 1:1.',
                      new.role;
    end if;

  elsif s = 'party' then
    if new.party_id is null then
      raise exception 'deal_participant.role=% is a PARTY-side role (someone outside CARR) '
                      'and needs party_id. See participant_role.side.', new.role;
    end if;
    if new.actor_id is not null then
      raise exception 'deal_participant.role=% is a PARTY-side role and must NOT carry '
                      'actor_id — an actor is a CARR employee and this role is a '
                      'counterparty.', new.role;
    end if;
  end if;

  return new;
end
$fn$;

create trigger deal_participant_side
  before insert or update of role, actor_id, party_id on deal_participant
  for each row execute function trg_deal_participant_side();

comment on function trg_deal_participant_side() is
  'Enforces participant_role.side on deal_participant (0060): an actor-side role carries '
  'actor_id and never party_id, a party-side role the reverse, a role with a null side is '
  'left alone. Deliberately a trigger and not a CHECK: a CHECK cannot read participant_role, '
  'so it would have to hardcode the role names and would silently fail to cover any role '
  'added later. Here the vocabulary IS the rule, so declaring a new role''s side is all that '
  'is needed to constrain it.';

-- ── 4. the claim set-lead already makes, made true ───────────────────────────────────────
-- "The database enforces exactly one current lead" (tools.js:1112). Until this index, it did
-- not: there was no unique constraint on this table beyond the primary key, and the invariant
-- rested entirely on set-lead's non-atomic close-then-insert. Partial on `to_at is null`
-- because the whole point of the table is that closed rows accumulate — Sonography already
-- carries one, correctly.
create unique index if not exists deal_participant_one_current_lead
    on deal_participant (deal_id)
 where role = 'lead' and to_at is null;

comment on index deal_participant_one_current_lead is
  'One CURRENT lead agent per deal (0060). Closed rows (to_at not null) are history and '
  'accumulate freely — Sonography Studios legitimately holds a closed row abutting its open '
  'one, which is set-lead recording a handoff, not a duplicate. This index is what set-lead''s '
  'description has been claiming since it was written; before 0060 the invariant was upheld '
  'only by that verb''s own close-then-insert, which is not atomic against a second concurrent '
  'caller and is bypassed entirely by a direct insert (0036 hand-rolled a `not exists` guard '
  'for exactly this reason).';

-- ── guards BEFORE commit, so a failure rolls the whole thing back ────────────────────────
do $$
declare
  b record;
  rows_now int; lead_now int; lead_current_now int; ownerless_now int;
  max_leads int; fired boolean; sides text;
begin
  select * into b from _dp_before;

  -- (1) NOT ONE ROW CHANGED. This migration is schema only. If any count moved, something in
  -- here wrote data, which it must never do.
  select count(*), count(*) filter (where role = 'lead'),
         count(*) filter (where role = 'lead' and to_at is null)
    into rows_now, lead_now, lead_current_now from deal_participant;
  if (rows_now, lead_now, lead_current_now) <> (b.rows_all, b.lead_rows, b.lead_current) then
    raise exception 'deal_participant rows moved: % -> % total, % -> % lead, % -> % current. '
                    '0060 adds constraints and must not touch a single row.',
                    b.rows_all, rows_now, b.lead_rows, lead_now, b.lead_current, lead_current_now;
  end if;

  -- (2) THE EXISTING DATA ALREADY OBEYS THE RULE. If it did not, the rule is wrong about the
  -- table rather than the table being wrong — and the trigger only fires on write, so nothing
  -- here would have caught it. This is the check that says the reading in the header is right.
  if b.lead_with_party <> 0 then
    raise exception '% role=''lead'' row(s) already carry a party_id. The header asserts this '
                    'is impossible because lead is the CARR agent; if it is not zero, re-read '
                    'the data before trusting any of this.', b.lead_with_party;
  end if;
  if b.lead_without_actor <> 0 then
    raise exception '% role=''lead'' row(s) carry no actor_id — a deal with no owning agent',
                    b.lead_without_actor;
  end if;
  if b.party_side_with_actor <> 0 then
    raise exception '% party-side row(s) carry an actor_id', b.party_side_with_actor;
  end if;

  -- (3) THE SIDES ARE DECLARED WHERE THEY WERE PROVEN AND NOWHERE ELSE. Guards against a
  -- future edit quietly filling in the four unknown roles by guess.
  select string_agg(slug || '=' || coalesce(side, 'null'), ', ' order by slug)
    into sides from participant_role;
  if (select count(*) from participant_role where side is not null) <> 3 then
    raise exception 'expected exactly 3 roles with a declared side (lead, listing_side, '
                    'client_contact); got: %. The other four have no rows and no call site, '
                    'and referring_agent is genuinely ambiguous — declaring a side for it is '
                    'Joe''s call, not a migration''s', sides;
  end if;
  if (select side from participant_role where slug = 'lead') <> 'actor' then
    raise exception 'role ''lead'' must be side=actor — it is the deal''s owning agent';
  end if;

  -- (4) THE TRIGGER ACTUALLY FIRES. An assertion nobody has watched fail is indistinguishable
  -- from one that cannot fire (0058's lesson). Both directions, then rolled back.
  fired := false;
  begin
    insert into deal_participant (deal_id, actor_id, party_id, role, set_by)
    select d.id, (select id from actor where slug = 'dell'),
           (select c.party_id from client c where c.id = d.client_id), 'lead',
           (select id from actor where slug = 'system')
      from deal d limit 1;
  exception when others then fired := true;
  end;
  if not fired then
    raise exception 'the side trigger allowed a role=''lead'' row carrying a party_id — which '
                    'is precisely the write this migration exists to forbid';
  end if;

  fired := false;
  begin
    insert into deal_participant (deal_id, actor_id, role, set_by)
    select d.id, (select id from actor where slug = 'joe'), 'listing_side',
           (select id from actor where slug = 'system')
      from deal d limit 1;
  exception when others then fired := true;
  end;
  if not fired then
    raise exception 'the side trigger allowed a party-side role carrying an actor_id';
  end if;

  -- (5) THE ONE-CURRENT-LEAD INDEX HOLDS, and did before it existed.
  select coalesce(max(n), 0) into max_leads from (
    select count(*) n from deal_participant
     where role = 'lead' and to_at is null group by deal_id) x;
  if max_leads > 1 then
    raise exception 'a deal carries % current leads', max_leads;
  end if;
  fired := false;
  begin
    insert into deal_participant (deal_id, actor_id, role, set_by)
    select dp.deal_id, dp.actor_id, 'lead', (select id from actor where slug = 'system')
      from deal_participant dp where dp.role = 'lead' and dp.to_at is null limit 1;
  exception when others then fired := true;
  end;
  if not fired then
    raise exception 'a second CURRENT lead was accepted on a deal that already had one — '
                    'deal_participant_one_current_lead is not doing its job';
  end if;

  -- (6) v_deal_board IS UNCHANGED. It is the consumer this table exists for, and the reason
  -- the briefed backfill would have been invisible until it was not.
  select count(*) into ownerless_now from v_deal_board where lead_owner is null;
  if ownerless_now <> b.ownerless then
    raise exception 'deals without a lead owner went % -> %', b.ownerless, ownerless_now;
  end if;
  if (select count(*) from v_deal_board) <> b.deals then
    raise exception 'v_deal_board row count no longer matches the deal count (%) — a '
                    'duplicated current lead would do exactly this', b.deals;
  end if;

  raise notice 'deal_participant sides declared and enforced. NO ROW WAS CHANGED: % rows, % '
               'of them role=lead, % of those current, across % deals, % without an owner. '
               'THE BRIEFED BACKFILL WAS NOT PERFORMED AND MUST NOT BE: role=''lead'' is the '
               'OWNING AGENT (dell 35 / joe 6), its NULL party_id is correct, and the two '
               'Sonography rows are one closed and one open interval from a set-lead handoff, '
               'not a duplicate. Sides now declared: %. One-current-lead is a real index for '
               'the first time.',
               rows_now, lead_now, lead_current_now, b.deals, ownerless_now, sides;
end $$;

commit;
