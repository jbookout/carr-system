-- 0018: amendment 2 — client_status rationalization.
--
-- JOE APPROVED THE MAPPING LIVE, 2026-07-31 ~10:15am CT (Fable seat interview,
-- three explicit yeses on record in decision-history). ORDER 10 of
-- opus-work-orders-2026-07-31.md. The mapping below is his ruling VERBATIM; it
-- is not widened or reinterpreted anywhere, and every rule is asserted to fire
-- on the row count it is supposed to fire on.
--
-- Four parts, one migration:
--   (a) the vocabulary becomes exactly six statuses
--   (b) the backfill, applied per-client, open-deal existence computed HERE
--   (c) one review event per orphan (status claimed a deal that has no record)
--   (d) the exported Status column becomes DERIVED display
--
-- The whole point of (d) is that the merge tombstones stop carrying their
-- pointer in a status string. If the derived render is byte-identical to the
-- pseudo-status it replaces, the pseudo-status was redundant — so this
-- migration PROVES that equality instead of asserting it in a comment.
--
-- ─────────────────────────────────────────────────────────────────────────────
-- THE ACTIVE BOOK SHRINKS BY 14, ON PURPOSE. Read this before touching step 8.
-- The first rehearsal (branch rehearse-0018, 2026-07-31 ~11:50am CT) stopped
-- itself: every guard passed except the membership one, because amendment 2
-- flags `cold` and `paused` as not-pipeline while migration 0014 — Joe's ruling
-- seven hours earlier the same day — had flagged both as ON the book, with the
-- reason written into the vocabulary row itself.
--
-- Joe settled it live at ~12:30pm CT, and cold/paused DROP: a cold client is
-- usually ghosting, you never pester, a pause can run longer than a year, and
-- the abundance mindset says work the live ones. So 0014's flags and 0014's
-- written reasons on those two rows are BOTH superseded here — step 2 rewrites
-- the notes so the old reason cannot outlive the ruling that replaced it.
--
-- The membership guard therefore does not expect an empty delta. It expects
-- EXACTLY those fourteen clients to leave and nobody to arrive, named one by
-- one. An expected change asserted by name is still a guard; "some rows moved"
-- is not.
-- ─────────────────────────────────────────────────────────────────────────────

-- ── 0. snapshots: what the exports said BEFORE anything moved ────────────────
-- Guards at the bottom compare against these. A migration that can silently
-- change the active book is the failure mode this whole order exists to avoid.
create temp table amd2_pre_status on commit drop as
  select "Client ID" as ref, "Status" as status from v_export_clients;

create temp table amd2_pre_book on commit drop as
  select "C-ID" as ref from v_export_clients_active;

-- ── 1. the merge tombstones lose their status; the column must allow it ──────
-- Joe's ruling: `Merged into C-###` pseudo-statuses → status NULL, because the
-- merge POINTER is the record. client.status has been NOT NULL since 0001.
alter table client alter column status drop not null;

-- ── 2. (a) the six statuses ──────────────────────────────────────────────────
-- Inserted alongside the old vocabulary; the old rows are deleted in step 5,
-- which is itself a guard: the FK refuses the delete if any client still
-- points at an old slug, so an incomplete backfill cannot commit.
-- The `note` column is the reason a status exists, read before assigning one
-- (0014's own words). Two of these notes carry Joe's 12:30pm ruling, because
-- 0014 wrote the OPPOSITE reason into cold_not_started and left paused blank,
-- and a superseded reason left standing is how the next session gets it wrong.
insert into client_status (slug, label, sort, is_active_pipeline, note) values
  ('roster',      'Roster',      10, false,
     'In the book, unworked. Bulk universe: on the roster, not on the daily '
     'active book. Nobody has committed to working this record yet.'),
  ('cold',        'Cold',        20, false,
     'Approached, no traction. NOT on the active book, and that is deliberate '
     '(Joe, 2026-07-31 ~12:30pm CT — this SUPERSEDES 0014''s note, which said '
     '"ON THE WORKING BOOK ... Appears in clients-active.md"). His reasoning: a '
     'cold client is usually ghosting, you never pester, and the abundance '
     'mindset says work the live ones. Cold is a separate not-top-of-mind '
     'category, not a queue to grind. It comes back the moment there is a deal.'),
  ('engaged',     'Engaged',     30, true,
     'Live relationship, no open deal. On the active book.'),
  ('active_deal', 'Active deal', 40, true,
     'Open deal in progress. On the active book.'),
  ('paused',      'Paused',      50, false,
     'Deliberately on hold. NOT on the active book (Joe, 2026-07-31 ~12:30pm CT '
     '— SUPERSEDES 0014, which flagged paused pipeline-active). A pause can run '
     'longer than a year; a paused client sitting in the daily book is noise. An '
     'open deal still pulls the client back on regardless of this status.'),
  ('past_client', 'Past client', 60, false,
     'Deals concluded, relationship kept. Off the active book until a new deal '
     'opens; a past client is a referral source, not a task.')
on conflict (slug) do update
  set label = excluded.label, sort = excluded.sort,
      is_active_pipeline = excluded.is_active_pipeline, note = excluded.note;
-- ('paused' already exists as a slug — the upsert re-labels it in place rather
--  than fighting the FK. Its label is unchanged; only its flag and note move.)

-- ── 3. (b) the approved mapping, one row per client, computed once ───────────
-- open deal = deal row, outcome null, client not merged (the order's definition,
-- verbatim). Note this is NOT the active-book's open-deal test, which also
-- requires phase <> 'closed'; the two are kept separate on purpose.
create temp table amd2_map on commit drop as
with od as (
  select c.id,
         c.merged_into is not null as merged,
         (c.merged_into is null
          and exists (select 1 from deal d
                       where d.client_id = c.id and d.outcome is null)) as open_deal
    from client c
)
select c.id,
       c.roster_ref,
       c.status                                     as old_status,
       (select cs.label from client_status cs where cs.slug = c.status) as old_label,
       r.rule,
       r.new_status
  from client c
  join od on od.id = c.id
  join lateral (
    select v.rule, v.new_status
      from (values
        -- R8 first and exclusive: every other rule requires merged = false.
        ('R8 merged → NULL',            od.merged,                                   null::text),
        ('R1 roster_unworked → roster', not od.merged and c.status = 'roster_unworked',  'roster'),
        ('R2 cold_not_started → cold',  not od.merged and c.status = 'cold_not_started', 'cold'),
        ('R3 paused → paused',          not od.merged and c.status = 'paused',           'paused'),
        ('R4 with open deal → active_deal',
           not od.merged and od.open_deal and c.status in
             ('research','negotiation','negotiating','due_diligence','legal',
              'closing','pending','active','warm_re_engagement'),               'active_deal'),
        ('R5 research/active, no open deal → engaged',
           not od.merged and not od.open_deal and c.status in ('research','active'), 'engaged'),
        ('R6 active-awaiting/relationship → engaged',
           not od.merged and c.status in
             ('active_awaiting_reply','active_relationship_building'),           'engaged'),
        ('R7 ORPHAN: negotiation/dd/legal, no open deal → active_deal',
           not od.merged and not od.open_deal and c.status in
             ('negotiation','due_diligence','legal'),                            'active_deal')
      ) v(rule, hit, new_status)
     where v.hit
  ) r on true;

-- Guard A: exactly one rule per client, and every roster row covered.
-- "Every one of the 160 non-null statuses must be covered by exactly one rule;
--  any row matching none → STOP."
do $$
declare dup int; uncovered int; covered_refs int;
begin
  select count(*) into dup from (
    select id from amd2_map group by id having count(*) > 1) x;
  if dup <> 0 then
    raise exception 'amendment 2: % client(s) matched more than one mapping rule', dup;
  end if;
  select count(*) into uncovered
    from client c where not exists (select 1 from amd2_map m where m.id = c.id);
  if uncovered <> 0 then
    raise exception 'amendment 2: % client(s) matched NO mapping rule — STOP', uncovered;
  end if;
  select count(*) into covered_refs from amd2_map where roster_ref is not null;
  if covered_refs <> 160 then
    raise exception 'amendment 2: expected 160 roster-bearing clients, got %', covered_refs;
  end if;
end $$;

-- Guard B: the merge rule keys on the POINTER, the order's text names the
-- pseudo-status STRING. Prove they are the same set on every roster-bearing
-- client, so the pointer form is a restatement and not a widening. (It reaches
-- further only on ref-less rows, which no export or view can see.)
do $$
declare mismatch int;
begin
  select count(*) into mismatch
    from client c
   where c.roster_ref is not null
     and (c.merged_into is not null) is distinct from (c.status like 'merged\_into\_c\_%');
  if mismatch <> 0 then
    raise exception 'amendment 2: merge pointer and merge pseudo-status disagree on % roster row(s)', mismatch;
  end if;
end $$;

-- Guard C: the orphan count is Joe's stated expectation. Different → STOP.
do $$
declare n int;
begin
  select count(*) into n from amd2_map where rule like 'R7 ORPHAN%';
  if n <> 9 then
    raise exception 'amendment 2: expected 9 orphans (status claims a deal with no deal row), got %', n;
  end if;
end $$;

-- Apply. updated_at / updated_by are deliberately NOT stamped: this is a
-- vocabulary rewrite, not a change of fact about any client, and stamping would
-- erase who last actually touched each record.
update client c set status = m.new_status from amd2_map m where m.id = c.id;

-- ── 4. (c) the review list, one event per orphan ─────────────────────────────
-- NOTE: the `event` table has no `summary` column (the order's word). The
-- order's sentence lands in agent_rationale, which is the field for a
-- system-written explanation of a record state. human_quote stays NULL — no
-- human said this sentence, and that field is for verbatim human words only.
insert into event (occurred_at, actor_id, verb, subject_type, subject_id,
                   field, old_value, new_value, cause, agent_rationale)
select now(),
       (select id from actor where slug = 'system'),
       'amendment-2-review', 'client', m.id,
       'status', to_jsonb(m.old_status), to_jsonb('active_deal'::text),
       'import_migration',
       'amendment-2 review: status claimed ''' || m.old_label ||
       ''' with no open deal record; create the missing deal or downgrade to engaged'
  from amd2_map m
 where m.rule like 'R7 ORPHAN%';

-- ── 5. retire the old vocabulary ─────────────────────────────────────────────
delete from client_status
 where slug not in ('roster','cold','engaged','active_deal','paused','past_client');

do $$
declare n int;
begin
  select count(*) into n from client_status;
  if n <> 6 then
    raise exception 'amendment 2: expected exactly 6 statuses after the sweep, got %', n;
  end if;
end $$;

-- ── 6. (d) the exported Status column becomes DERIVED display ────────────────
-- merged  → 'Merged into <ref>' straight off the merge pointer
-- active_deal WITH an open deal → 'Active deal – <Phase title>' (newest deal)
-- active_deal WITHOUT one (the nine orphans) → 'Active deal – no deal on file'
--   (self-flagging by design: the roster shows the gap until someone fixes it)
-- everything else → its label
drop view v_export_clients;
create view v_export_clients as
select c.roster_ref              as "Client ID",
       p.name                    as "Name",
       org.name                  as "Practice / Entity",
       coalesce(c.owner_label, owner.display_name) as "Owner",
       case
         when c.merged_into is not null then 'Merged into ' || mt.roster_ref
         when c.status = 'active_deal' then
           coalesce('Active deal – ' || (select ph.label
                                           from deal d
                                           join deal_phase ph on ph.slug = d.phase
                                          where d.client_id = c.id and d.outcome is null
                                          order by d.created_at desc, d.id desc
                                          limit 1),
                    'Active deal – no deal on file')
         else cs.label
       end                       as "Status",
       c.specialty_type_label    as "Specialty / Type",
       coalesce(p.city,'') || coalesce(', ' || p.state,'')        as "Market / Location",
       c.deal_type_label         as "Deal Type",
       c.acquisition_source      as "Referral Source",
       c.contact_label           as "Contact",
       p.phone                   as "Phone",
       p.email                   as "Email",
       c.possible_duplicate_label as "Possible Duplicate Of",
       c.notes_path              as "Detail File",
       c.notes                   as "Notes"
from client c
join party p on p.id = c.party_id
-- LEFT, not inner: a merge tombstone's status is NULL now, and an inner join
-- would drop those nine rows out of the roster entirely.
left join client_status cs on cs.slug = c.status
left join client mt on mt.id = c.merged_into
left join party org on org.id = p.org_id
left join actor owner on owner.id = c.owner_id
where c.roster_ref is not null;
grant select on v_export_clients to carr_reader;

drop view v_export_clients_active;
create view v_export_clients_active as
select coalesce(c.owner_label, owner.display_name)          as "Owner",
       pc.name                                              as "Name",
       c.roster_ref                                         as "C-ID",
       case
         when c.status = 'active_deal' then
           coalesce('Active deal – ' || (select ph.label
                                           from deal d
                                           join deal_phase ph on ph.slug = d.phase
                                          where d.client_id = c.id and d.outcome is null
                                          order by d.created_at desc, d.id desc
                                          limit 1),
                    'Active deal – no deal on file')
         else cs.label
       end                                                  as "Status",
       c.deal_type_label                                    as "Deal Type",
       coalesce(c.specialty_type_label, c.vertical, '')     as "Specialty",
       coalesce(pc.city,'') || coalesce(', ' || pc.state,'') as "Location",
       lt.last_touch                                        as "Last Touch",
       na.description                                       as "Next Step",
       c.notes_path                                         as "Detail"
from client c
join party pc on pc.id = c.party_id
left join client_status cs on cs.slug = c.status
left join actor owner on owner.id = c.owner_id
left join v_last_touch lt on lt.subject_type = 'client' and lt.subject_id = c.id
left join lateral (
    select n.description
    from next_action n
    where n.subject_type = 'client' and n.subject_id = c.id and n.status = 'open'
    order by (n.owner_id = c.owner_id) desc nulls last, n.due_on nulls last, n.created_at
    limit 1
) na on true
where c.merged_into is null
  and c.roster_ref is not null
  -- The derivation is UNCHANGED: flagged status, or an open deal regardless of
  -- status. Only the set of flagged statuses moved, and step 8 measures that.
  and (coalesce(cs.is_active_pipeline, false)
       or exists (select 1 from deal d
                   where d.client_id = c.id
                     and d.outcome is null and d.phase <> 'closed'));
grant select on v_export_clients_active to carr_reader;

-- ── 7. the merge-render proof ────────────────────────────────────────────────
-- "byte-identical to today's strings — this is the proof the pseudo-status was
--  redundant". Measured, not claimed.
do $$
declare bad int;
begin
  select count(*) into bad
    from amd2_pre_status pre
    join v_export_clients post on post."Client ID" = pre.ref
   where pre.status like 'Merged into %' and post."Status" is distinct from pre.status;
  if bad <> 0 then
    raise exception 'amendment 2: % merge row(s) no longer render their original string', bad;
  end if;
end $$;

-- ── 8. THE MEMBERSHIP GUARD — expect EXACTLY these fourteen, and nobody else ──
-- Joe's 12:30pm ruling replaced ORDER 10(e)'s empty-delta clause with a named
-- one: "delta must be EXACTLY 14 dropped [C-110 ... C-124] / 0 added — anything
-- else → STOP." The set below is that list, hard-coded on purpose. A count check
-- would pass if fourteen of the WRONG clients left; only the names catch that.
do $$
declare expected text[] := array[
    'C-110','C-111','C-113','C-114','C-115','C-116','C-117',
    'C-118','C-119','C-120','C-121','C-122','C-123','C-124'];
  gone text[]; added text[]; unexpected text[]; missing text[];
begin
  select coalesce(array_agg(ref order by ref), '{}') into gone
    from (select ref from amd2_pre_book
          except select "C-ID" from v_export_clients_active) x;
  select coalesce(array_agg(ref order by ref), '{}') into added
    from (select "C-ID" as ref from v_export_clients_active
          except select ref from amd2_pre_book) y;

  select coalesce(array_agg(r order by r), '{}') into unexpected
    from unnest(gone) r where not r = any (expected);
  select coalesce(array_agg(r order by r), '{}') into missing
    from unnest(expected) r where not r = any (gone);

  if array_length(added, 1) is not null then
    raise exception 'amendment 2 STOP: % client(s) ADDED to the active book, none was expected: %',
      array_length(added, 1), added;
  end if;
  if array_length(unexpected, 1) is not null then
    raise exception 'amendment 2 STOP: client(s) left the active book that the ruling did not name: %',
      unexpected;
  end if;
  if array_length(missing, 1) is not null then
    raise exception 'amendment 2 STOP: the ruling named client(s) that did NOT leave the active book: %',
      missing;
  end if;
  raise notice 'amendment 2: active book % -> % rows; exactly the 14 named clients left, 0 added.',
    (select count(*) from amd2_pre_book),
    (select count(*) from v_export_clients_active);
end $$;
