-- 0062_orphaned_edge.sql — the pre-merge orphan sweep stops being a thing you remember to do.
--
-- THE DEFECT, one row, verified read-only against production before this was written.
-- party_link 4aecf3b0-62ce-40d5-b23e-cf35e67e9514 is P-0462 Joe Ed Jackson -knows-> P-0365
-- "Dr. James Allen Tyrer", and P-0365 has merged_into = P-0384, the surviving Tyrer. So the
-- intro graph holds an edge whose far end is a tombstone.
--
-- IT IS A STALE DUPLICATE, AND THAT WAS CHECKED RATHER THAN ASSUMED, because the whole
-- treatment turns on it. party_link e1f2cbcd-6134-4bb8-8ca0-0591f2222ac0 is the same edge on
-- the survivor, and "same" here means every single column: from_party both 66e4e0bc
-- (P-0462), kind both 'knows', note both 'L-208 / C-155 Dr. James Allen Tyrer', source both
-- 'links_label_parse', created_by both the system actor, via_party both null, occurred_on both
-- null, and created_at identical to the microsecond
-- (2026-07-31 18:52:45.315171+00) because parse_party_links.py wrote them in one pass. The two
-- rows differ in exactly two values: their own id, and to_party. Removing the tombstoned one
-- destroys no information whatsoever — the live one already carries all of it.
--
-- WHY IT CANNOT SIMPLY BE RE-POINTED, which was the first thing I tried. There is a unique
-- index party_link_from_to_kind_uidx on (from_party, to_party, kind). Updating 4aecf3b0's
-- to_party to P-0384 collides head-on with e1f2cbcd. The edge that repointing would create
-- already exists; that is precisely what makes this row surplus rather than stranded.
--
-- WHY IT IS DELETED RATHER THAN TOMBSTONED, which is a real departure from 0059's "no row is
-- ever deleted" and needs a better reason than convenience. party_link has no merged_into, no
-- deleted_at and no retired_at, so tombstoning means adding a column — and a tombstone column
-- only works if every consumer filters on it. The consumers are who-do-we-know
-- (mcp-server/src/tools.js:659, which walks this table up to 3 hops and renders the chain),
-- link-parties (:1741), the reciprocity ledger and pipelines/build-graph-notes.py. Those files
-- belong to other seats this session. A retired_at column that nothing filters on would leave
-- who-do-we-know still walking the dead edge and still offering Joe a path to a person who no
-- longer exists, which is the actual symptom, while letting the migration claim it was fixed.
-- Deleting removes it from every consumer at once with no code change anywhere.
--
-- The information is preserved as a fact rather than as a row: the full row is written to the
-- event log as jsonb before the delete, so the reversal is one statement and it is exact.
-- 0059's principle was that a reversal is a new fact rather than an erasure; that principle is
-- kept here, only the carrier is the event log instead of a tombstone column. Reversal:
--
--     insert into party_link (id, from_party, to_party, kind, note, source, created_by,
--                             created_at, via_party, occurred_on)
--     select (old_value->>'id')::uuid, (old_value->>'from_party')::uuid,
--            (old_value->>'to_party')::uuid, old_value->>'kind', old_value->>'note',
--            old_value->>'source', (old_value->>'created_by')::uuid,
--            (old_value->>'created_at')::timestamptz, (old_value->>'via_party')::uuid,
--            (old_value->>'occurred_on')::date
--       from event where verb = 'retire-orphaned-edge'
--        and subject_id = '4aecf3b0-62ce-40d5-b23e-cf35e67e9514';
--
-- THE SWEEP IS THE POINT; THE ROW IS THE BACKLOG. Joe's survivorship rule requires an orphan
-- sweep before every merge, and 0055 exists because that step was skipped: confirm-merge set
-- merged_into on the loser and left its lead/client/vendor rows pointing at a party that no
-- longer resolved. 0055 repaired those and left behind v_orphaned_role, which turned the ROLE
-- half of the sweep into a query. Nothing did the same for the EDGE half, so this row survived
-- the same merge that v_orphaned_role would have caught the roles of, and sat unnoticed for two
-- days. A procedure that lives in a human's memory is a procedure that gets skipped on the
-- merge that matters.
--
-- WHAT v_orphaned_edge COVERS, derived from the catalog rather than from recall. There are 15
-- foreign keys to party(id) in the schema. Three are the role tables (lead, client, vendor) and
-- belong to v_orphaned_role. Three are org_merge_log, which points at merged rows BY DESIGN —
-- it is the map that makes 0059 reversible and flagging it would make the sweep permanently
-- noisy. One is party.merged_into, the tombstone pointer itself. The remaining eight are the
-- edges, and all eight are covered:
--
--     party_link.from_party / .to_party / .via_party
--     deal_participant.party_id
--     building_ownership.party_id
--     commission_allocation.party_id
--     registration.registered_with_party
--     party.org_id                (0059 guarded this once, in-migration, and then it was over)
--
-- Measured across all eight right now: party_link.to_party 1, every other one 0. After this
-- migration the whole sweep reads zero.
--
-- THE SEVEN CLIENTS THAT LOOK LIKE ORPHANS AND ARE NOT. Seven client rows point at a party
-- that has been merged: P-0546 Elizabeth Hughes, P-0255 Erik Petersen, P-0698 Kaydee Zimmern,
-- P-0776 Blair Stiles, P-0949 Jonathan Tubbs, P-0287 Nick Frigo, P-0552 Troy Bell. All seven
-- are CORRECT and must never be flagged. Checked, not taken on faith: every one has
-- merged_into set on the CLIENT row (it is a tombstoned client), a null roster_ref, zero
-- deals, and a survivor party that already carries exactly one live client row. They are the
-- losing halves of client merges, and pointing at the losing party is what a tombstone is for.
-- v_orphaned_role already excludes them with `c.merged_into is null` in its client branch,
-- which is why it reads 0 today. v_orphaned_edge cannot flag them for a stronger reason than a
-- filter: it does not look at the client table at all. The guard asserts all seven stay
-- invisible to both views anyway, because "cannot by construction" is the kind of claim that
-- stops being true when someone adds a branch.
--
-- REVERSAL of the rest: `drop function assert_no_orphaned_edges(); drop view v_orphaned_edge;`

begin;

-- ── 1. prove the twin exists BEFORE removing anything ────────────────────────────────────
-- If the survivor edge is not there, this row is not surplus, it is the only copy, and
-- deleting it would lose a real edge in the intro graph. Fail rather than find out later.
do $$
declare tw int; diffs text;
begin
  select count(*) into tw
    from party_link doomed
    join party_link twin
      on twin.from_party = doomed.from_party
     and twin.kind       = doomed.kind
     and twin.id        <> doomed.id
    join party dead on dead.id = doomed.to_party
   where doomed.id = '4aecf3b0-62ce-40d5-b23e-cf35e67e9514'
     and dead.merged_into = twin.to_party;

  if tw <> 1 then
    raise exception 'the edge to be retired has % surviving twin(s), expected exactly 1. '
                    'Without a twin on the survivor this row is the ONLY copy of a real '
                    'relationship and must be re-pointed by hand, not deleted.', tw;
  end if;

  -- Every other column must agree, or "stale duplicate" is the wrong description and the
  -- doomed row is carrying something the twin is not.
  select string_agg(x, ', ') into diffs from (
    select 'note'        as x from party_link a, party_link b
      where a.id='4aecf3b0-62ce-40d5-b23e-cf35e67e9514'
        and b.id='e1f2cbcd-6134-4bb8-8ca0-0591f2222ac0'
        and a.note is distinct from b.note
    union all
    select 'source'      from party_link a, party_link b
      where a.id='4aecf3b0-62ce-40d5-b23e-cf35e67e9514'
        and b.id='e1f2cbcd-6134-4bb8-8ca0-0591f2222ac0' and a.source <> b.source
    union all
    select 'via_party'   from party_link a, party_link b
      where a.id='4aecf3b0-62ce-40d5-b23e-cf35e67e9514'
        and b.id='e1f2cbcd-6134-4bb8-8ca0-0591f2222ac0'
        and a.via_party is distinct from b.via_party
    union all
    select 'occurred_on' from party_link a, party_link b
      where a.id='4aecf3b0-62ce-40d5-b23e-cf35e67e9514'
        and b.id='e1f2cbcd-6134-4bb8-8ca0-0591f2222ac0'
        and a.occurred_on is distinct from b.occurred_on) d;

  if diffs is not null then
    raise exception 'the doomed edge differs from its twin on: %. It is not a stale duplicate '
                    'and deleting it would lose that value', diffs;
  end if;
end $$;

-- The BEFORE snapshot, measured rather than hardcoded so the guard is relative and stays
-- honest if another seat has written an edge since this was measured (31 links, 1 orphaned).
create temporary table _edge_before on commit drop as
select (select count(*) from party_link)                                    as links,
       (select count(*) from party_link pl join party p on p.id = pl.to_party
         where p.merged_into is not null or p.deleted_at is not null)       as orphaned_to,
       (select count(*) from party)                                         as parties,
       (select count(*) from client c join party p on p.id = c.party_id
         where p.merged_into is not null and c.merged_into is not null)     as tombstoned_clients,
       (select count(*) from v_orphaned_role)                               as orphaned_roles;

-- ── 2. record the row as a fact, then remove it ──────────────────────────────────────────
insert into event (occurred_at, recorded_at, actor_id, verb, subject_type, subject_id,
                   field, old_value, new_value, cause, agent_rationale)
select now(), now(), (select id from actor where slug = 'system'),
       'retire-orphaned-edge', 'party_link', pl.id, null,
       to_jsonb(pl),
       jsonb_build_object('retired', true, 'survivor_edge',
                          'e1f2cbcd-6134-4bb8-8ca0-0591f2222ac0'),
       'import_migration',
       'Stale intro-graph edge whose to_party (P-0365) is merged into P-0384. The identical '
       'edge on the survivor already exists and carries every column of this one, so no '
       'relationship is lost. It could not be re-pointed: party_link_from_to_kind_uidx '
       'forbids the collision with the survivor edge. Deleted rather than tombstoned because '
       'party_link has no tombstone column and its consumers (who-do-we-know, the reciprocity '
       'ledger, build-graph-notes.py) do not filter one, so a tombstone would leave the dead '
       'edge being walked. old_value is the complete row; the reversal is one insert, spelled '
       'out in 0062''s header.'
  from party_link pl
 where pl.id = '4aecf3b0-62ce-40d5-b23e-cf35e67e9514';

delete from party_link where id = '4aecf3b0-62ce-40d5-b23e-cf35e67e9514';

-- ── 3. the sweep, as a query ─────────────────────────────────────────────────────────────
-- Deliberately mirrors v_orphaned_role's shape (0055) so the two read as one pair. It covers
-- the EDGE half of Joe's pre-merge sweep; v_orphaned_role covers the ROLE half.
--
-- deleted_at counts as orphaning as well as merged_into, matching v_orphaned_role: an edge to
-- a deleted party is just as dead as an edge to a redirect, and the difference is that the
-- redirect at least tells you where to go.
--
-- org_merge_log is EXCLUDED on purpose and is not an oversight — it holds three FKs to party
-- and two of them (from_org, and party_id where the person was later merged too) point at
-- merged rows by construction. It is the reversal map for 0059. Flagging it would put
-- permanent noise in a sweep whose only value is reading zero.
create or replace view v_orphaned_edge as
select 'party_link.to_party'::text as edge, pl.id as edge_id, pl.kind as detail,
       p.ref as party_ref, p.name, sv.ref as survivor_ref,
       p.merged_into is not null as party_merged, p.deleted_at is not null as party_deleted
  from party_link pl join party p on p.id = pl.to_party
  left join party sv on sv.id = p.merged_into
 where p.merged_into is not null or p.deleted_at is not null
union all
select 'party_link.from_party', pl.id, pl.kind, p.ref, p.name, sv.ref,
       p.merged_into is not null, p.deleted_at is not null
  from party_link pl join party p on p.id = pl.from_party
  left join party sv on sv.id = p.merged_into
 where p.merged_into is not null or p.deleted_at is not null
union all
select 'party_link.via_party', pl.id, pl.kind, p.ref, p.name, sv.ref,
       p.merged_into is not null, p.deleted_at is not null
  from party_link pl join party p on p.id = pl.via_party
  left join party sv on sv.id = p.merged_into
 where p.merged_into is not null or p.deleted_at is not null
union all
select 'deal_participant.party_id', dp.id, dp.role, p.ref, p.name, sv.ref,
       p.merged_into is not null, p.deleted_at is not null
  from deal_participant dp join party p on p.id = dp.party_id
  left join party sv on sv.id = p.merged_into
 where p.merged_into is not null or p.deleted_at is not null
union all
select 'building_ownership.party_id', bo.id, bo.kind, p.ref, p.name, sv.ref,
       p.merged_into is not null, p.deleted_at is not null
  from building_ownership bo join party p on p.id = bo.party_id
  left join party sv on sv.id = p.merged_into
 where p.merged_into is not null or p.deleted_at is not null
union all
select 'commission_allocation.party_id', ca.id, ca.kind::text, p.ref, p.name, sv.ref,
       p.merged_into is not null, p.deleted_at is not null
  from commission_allocation ca join party p on p.id = ca.party_id
  left join party sv on sv.id = p.merged_into
 where p.merged_into is not null or p.deleted_at is not null
union all
select 'registration.registered_with_party', r.id, r.method::text, p.ref, p.name, sv.ref,
       p.merged_into is not null, p.deleted_at is not null
  from registration r join party p on p.id = r.registered_with_party
  left join party sv on sv.id = p.merged_into
 where p.merged_into is not null or p.deleted_at is not null
union all
select 'party.org_id', holder.id, holder.ref::text, p.ref, p.name, sv.ref,
       p.merged_into is not null, p.deleted_at is not null
  from party holder join party p on p.id = holder.org_id
  left join party sv on sv.id = p.merged_into
 where p.merged_into is not null or p.deleted_at is not null;

comment on view v_orphaned_edge is
  'The EDGE half of the pre-merge orphan sweep (0062); v_orphaned_role (0055) is the ROLE '
  'half. Every non-role foreign key to party(id) that points at a merged or deleted party. '
  'RUN IT BEFORE AND AFTER EVERY MERGE — it must read zero both times, and confirm-merge must '
  'leave it at zero. It exists because the sweep was a remembered procedure and got skipped: '
  'one stale party_link edge to a merged Tyrer sat in the intro graph for two days while '
  'who-do-we-know went on offering Joe a path to a party that no longer resolves. '
  'DELIBERATELY EXCLUDES org_merge_log, whose whole job is to point at merged org rows so '
  '0059 stays reversible. DOES NOT FLAG the seven tombstoned client rows whose party is '
  'merged — those are correct (a tombstoned client pointing at the losing party is what a '
  'tombstone IS) and this view does not read the client table at all.';

create or replace function assert_no_orphaned_edges() returns void
language plpgsql as $fn$
declare n int; worst text;
begin
  select count(*), min(edge || ' -> ' || coalesce(party_ref, '?') || ' ' || coalesce(name, ''))
    into n, worst from v_orphaned_edge;
  if n > 0 then
    raise exception 'v_orphaned_edge is not empty: % edge(s) point at a merged or deleted '
                    'party (e.g. %). Repoint or retire them before merging anything else — a '
                    'merge on top of a stale edge produces a graph that walks to a party which '
                    'no longer resolves.', n, worst;
  end if;
end
$fn$;

comment on function assert_no_orphaned_edges() is
  'Raises unless v_orphaned_edge is empty (0062). Call it from a migration guard alongside '
  'the v_orphaned_role check, and from any future merge path, so the survivorship rule''s '
  'mandatory sweep is enforced rather than remembered. Companion to assert_view_disjoint '
  '(0058): the same trade — a paragraph claiming an invariant becomes a call that fails.';

-- ── guards BEFORE commit, so a failure rolls the whole thing back ────────────────────────
do $$
declare
  b record;
  links_now int; twin_left int; orphans int; roles int; hidden_clients int; fired boolean;
begin
  select * into b from _edge_before;

  -- (1) EXACTLY ONE ROW WENT, AND THE SURVIVOR STAYED.
  if b.orphaned_to <> 1 then
    raise exception '% orphaned party_link.to_party edge(s) before this ran, expected exactly '
                    '1. This migration retires ONE row by id; if the population has grown, '
                    'sweep the rest with v_orphaned_edge and decide each one deliberately',
                    b.orphaned_to;
  end if;
  select count(*) into links_now from party_link;
  if links_now <> b.links - 1 then
    raise exception 'party_link went % -> % rows; exactly one row may go', b.links, links_now;
  end if;
  if (select count(*) from party) <> b.parties then
    raise exception 'party count changed % -> % — 0062 touches no party row',
                    b.parties, (select count(*) from party);
  end if;
  select count(*) into twin_left from party_link
   where id = 'e1f2cbcd-6134-4bb8-8ca0-0591f2222ac0';
  if twin_left <> 1 then
    raise exception 'the SURVIVOR edge e1f2cbcd is gone — the wrong row was deleted';
  end if;
  if exists (select 1 from party_link where id = '4aecf3b0-62ce-40d5-b23e-cf35e67e9514') then
    raise exception 'the stale edge is still present';
  end if;
  -- The relationship itself must still be reachable, which is the thing that actually matters:
  -- Joe Ed Jackson still knows Tyrer, now the live one.
  if not exists (
    select 1 from party_link pl
      join party f on f.id = pl.from_party join party t on t.id = pl.to_party
     where f.ref = 'P-0462' and t.ref = 'P-0384' and pl.kind = 'knows'
       and t.merged_into is null) then
    raise exception 'P-0462 -knows-> P-0384 no longer exists. The edge was supposed to be a '
                    'surplus copy; if this fires, a real relationship was just destroyed';
  end if;
  -- And the reversal is on the record.
  if not exists (select 1 from event where verb = 'retire-orphaned-edge'
                   and subject_id = '4aecf3b0-62ce-40d5-b23e-cf35e67e9514'
                   and old_value ? 'from_party' and old_value ? 'to_party') then
    raise exception 'the deleted row was not recorded in the event log — without the full row '
                    'this change is not reversible and the header is lying';
  end if;

  -- (2) THE WHOLE SWEEP READS ZERO, both halves.
  select count(*) into orphans from v_orphaned_edge;
  if orphans <> 0 then
    raise exception 'v_orphaned_edge still holds % row(s) after the repair', orphans;
  end if;
  perform assert_no_orphaned_edges();
  select count(*) into roles from v_orphaned_role;
  if roles <> b.orphaned_roles or roles <> 0 then
    raise exception 'v_orphaned_role reads % (was %) — the role half of the sweep is not clean',
                    roles, b.orphaned_roles;
  end if;

  -- (3) THE SEVEN CORRECT CLIENTS ARE STILL INVISIBLE TO BOTH VIEWS. They point at merged
  -- parties on purpose and flagging them would make the sweep cry wolf on every read.
  select count(*) into hidden_clients
    from client c join party p on p.id = c.party_id
   where p.merged_into is not null and c.merged_into is not null;
  if hidden_clients <> 7 or hidden_clients <> b.tombstoned_clients then
    raise exception 'expected 7 tombstoned client rows over merged parties, found % (was %) — '
                    'the population this view was checked against has changed',
                    hidden_clients, b.tombstoned_clients;
  end if;
  -- None of those seven parties may appear in the sweep. Direct: take their party refs and
  -- look for them in the view.
  if exists (
    select 1 from v_orphaned_edge
     where party_ref in (select p.ref from client c join party p on p.id = c.party_id
                          where c.merged_into is not null and p.merged_into is not null)) then
    raise exception 'v_orphaned_edge is flagging a tombstoned client''s merged party. Those '
                    'seven rows are CORRECT: a tombstoned client pointing at the losing party '
                    'is what a tombstone is for, and this view must not read the client table';
  end if;
  -- Every one of the seven has a live survivor carrying the real client row, which is what
  -- makes them harmless. If that ever stops being true they are orphans after all.
  if exists (
    select 1 from client c join party p on p.id = c.party_id
     where c.merged_into is not null and p.merged_into is not null
       and not exists (select 1 from client c2
                        where c2.party_id = p.merged_into and c2.merged_into is null)) then
    raise exception 'a tombstoned client points at a merged party whose SURVIVOR has no live '
                    'client row — that one is a genuine orphan, not a correct tombstone';
  end if;

  -- (4) THE ASSERTION CAN ACTUALLY FAIL. 0058's rule: an assertion nobody has watched fail is
  -- indistinguishable from one that cannot fire. Insert a deliberate orphan, watch it raise,
  -- roll it back.
  fired := false;
  begin
    insert into party_link (from_party, to_party, kind, source, created_by)
    select (select id from party where ref = 'P-0462'),
           (select id from party where ref = 'P-0365'),
           'works_with', 'selftest', (select id from actor where slug = 'system');
    perform assert_no_orphaned_edges();
  exception when others then fired := true;
  end;
  if not fired then
    raise exception 'assert_no_orphaned_edges stayed silent on a deliberately orphaned edge — '
                    'every call to it would be theatre';
  end if;
  if exists (select 1 from party_link where source = 'selftest') then
    raise exception 'the self-test edge survived its own rollback';
  end if;
  if (select count(*) from party_link) <> links_now then
    raise exception 'party_link is % rows after the self-test, expected %',
                    (select count(*) from party_link), links_now;
  end if;

  raise notice 'orphan sweep is now a query. One stale intro-graph edge retired (P-0462 '
               '-knows-> P-0365, whose survivor edge to P-0384 carries every column of it and '
               'remains); % party_link rows left. v_orphaned_edge covers all 8 non-role party '
               'FKs and reads 0, v_orphaned_role reads 0, and assert_no_orphaned_edges() is '
               'proven to fire. The 7 tombstoned clients over merged parties are untouched and '
               'unflagged, as they should be. RUN assert_no_orphaned_edges() BEFORE AND AFTER '
               'EVERY MERGE — that sweep was a remembered step, which is why this row existed.',
               links_now;
end $$;

commit;
