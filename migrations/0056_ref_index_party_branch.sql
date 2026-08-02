-- 0056_ref_index_party_branch.sql — the resolver can finally see a person who holds no role.
--
-- THE DEFECT, found by the 2026-08-02 audit and verified live. Ask `find` for "Henry Schein"
-- and it answers with "Henry Pruett" — a trigram hit on the first word — while the 17 real
-- Henry Schein party rows sit in the table untouched. `who-do-we-know "Henry Schein"` goes
-- further and says "No record and no graph node matches that name", which is not a miss, it
-- is a false statement about the book. A verb that invents an absence is worse than a verb
-- that errors: an error gets investigated, "we don't know them" gets believed and acted on.
--
-- ROOT CAUSE, in 0016 and inherited unchanged by 0027. Every branch of v_ref_index reaches
-- party THROUGH a role — `from lead l join party p`, `from client c join party p`,
-- `from vendor v join party p`. The view is therefore an index of ROLES that happens to
-- display party names, not an index of subjects. A party with no lead, client or vendor row
-- has no path into it and is unreachable by the system's primary lookup verb. On this data
-- that is 432 of 1,084 parties invisible, including all 415 org parties that were imported
-- as counterparties, employers and suppliers rather than as anybody's lead.
--
-- This is the same class of fault as 0046: identity lived on the ROLE instead of the person.
-- 0046 gave the person a ref (P-####); this makes that ref resolvable. Without it, P-0301 is
-- an identifier the system mints and then cannot look up, which is a promise half kept.
--
-- WHY A FOURTH BRANCH RATHER THAN LOOSENING THE JOINS. Turning the three role joins into
-- left joins from party would collapse the role rows into one row per party and silently
-- change what every existing consumer reads — 0030's md ledger, resolveSubject's ref lookups,
-- counterparty-history. Additive is auditable: the four existing branches emit byte-identical
-- rows after this migration, and the new branch can only ADD subjects that were previously
-- unreachable. The before/after count per subject_type is the proof, and it is asserted below.
--
-- NOTHING DOUBLE-COUNTS, BY CONSTRUCTION. The branch takes only parties with NO lead, client
-- or vendor row at all. The existence tests deliberately do NOT filter on client.merged_into:
-- the client branch of this view emits merged tombstones too (that is what the `merged` flag
-- is for, and `find` shows them on purpose so a search for a merged name learns where it
-- went), so a party carrying only a tombstone client row IS already represented here and must
-- not be emitted twice under a second subject_type. The test matches what the view actually
-- emits, not what a live-records view would emit.
--
-- MERGED IN, DELETED OUT, and they are different facts. A merged party is a real record that
-- redirects, so it stays and carries merged=true — the 0016 posture, and the reason `find`
-- keeps tombstones. A deleted party is a record someone purged; surfacing it would resurrect
-- data that was removed on purpose. deleted_at is null is a hard filter, not a preference.
--
-- SAFE COLUMNS ONLY — the boundary 0016 set and 0027 tested when it added party_id. This
-- branch reads party directly rather than through a role, which is exactly the position from
-- which it would be easy to reach for phone, email, notes or notes_path. It does not. Column
-- list and order are byte-identical to the other four branches; a reader-scoped session sees
-- everything in this view, so the column list is a security boundary, not a convenience.
--
-- CREATE OR REPLACE, NOT DROP AND CREATE, and the difference is not stylistic. Postgres
-- preserves ownership and ACLs across a replace; a drop takes the grants with it. Three roles
-- hold select here, added across three separate migrations — carr_reader (0016), carr_writer
-- (0020, after resolveSubject broke in production for exactly this reason) and carr_jobs
-- (0021) — plus v_md_ledger in 0030 depends on the view and a drop would have to cascade.
-- A replace is legal here because the column prefix is unchanged; only union branches are
-- added. The carr_reader grant is re-issued anyway (it is a no-op on a replace) and the guard
-- asserts all three roles still hold select, so the 0020 incident cannot repeat unnoticed.
--
-- THIS MIGRATION ALONE DOES NOT FIX `find`. Both read verbs hardcode
-- `subject_type in ('lead','client','vendor')` (mcp-server/src/tools.js, the `find` handler
-- and resolveSubject's name fallback), so the new rows are queryable but not yet queried.
-- The filter widening is a code change and lands separately; this is the data half.

begin;

create or replace view v_ref_index as
select 'lead'::text          as subject_type,
       l.id                  as subject_id,
       l.registry_ref        as ref,
       p.name                as display_name,
       org.name              as org_name,
       p.city                as city,
       p.specialty           as specialty,
       l.stage               as status,
       (p.merged_into is not null) as merged,
       null::text            as client_ref,
       p.id                  as party_id
  from lead l
  join party p on p.id = l.party_id
  left join party org on org.id = p.org_id
union all
select 'client', c.id, c.roster_ref, p.name, org.name, p.city, p.specialty, c.status,
       (coalesce(c.merged_into, p.merged_into) is not null),
       null::text,
       p.id
  from client c
  join party p on p.id = c.party_id
  left join party org on org.id = p.org_id
union all
select 'vendor', v.id, v.vendor_ref, p.name, org.name, p.city, p.specialty, v.stage,
       (p.merged_into is not null), null::text, p.id
  from vendor v
  join party p on p.id = v.party_id
  left join party org on org.id = p.org_id
union all
-- Deals have no party, so the party-shaped columns are null by construction and
-- `merged` is false: a deal is never a merge tombstone.
select 'deal', d.id, null::text, d.name, null::text, null::text, null::text, d.phase,
       false, c.roster_ref, null::uuid
  from deal d
  left join client c on c.id = d.client_id
union all
-- The party branch (0056). subject_id and party_id are the same uuid here and that is
-- correct rather than redundant: for a role row subject_id keys the ROLE and party_id keys
-- the person, and for a subject that IS the person the two coincide. Consumers that resolve
-- a ref to a party (counterparty-history, the graph verbs) keep working without a special
-- case. status carries contact_state — the only status a party has of its own, and the one
-- that decides whether they may be contacted at all.
select 'party', p.id, p.ref, p.name, org.name, p.city, p.specialty, p.contact_state,
       (p.merged_into is not null), null::text, p.id
  from party p
  left join party org on org.id = p.org_id
 where p.deleted_at is null
   and not exists (select 1 from lead   l where l.party_id = p.id)
   and not exists (select 1 from client c where c.party_id = p.id)
   and not exists (select 1 from vendor v where v.party_id = p.id);

grant select on v_ref_index to carr_reader;

comment on view v_ref_index is
  'Resolver surface for find/resolveSubject under the views-only reader role '
  '(amendment 11). SAFE COLUMNS ONLY — never add phone, email, notes, or any '
  'contact detail here; a reader-scoped session sees everything in this view. '
  'Five branches: lead/client/vendor/deal key a ROLE, and party (0056) keys a '
  'PERSON OR ORG holding no role at all — without it the view indexed roles, not '
  'subjects, and 432 of 1,084 parties including every org were unreachable by the '
  'primary lookup verb, which answered "no record matches that name" for 17 live '
  'Henry Schein rows. The party branch is disjoint from the other three by '
  'construction (no lead/client/vendor row exists for it), so nothing double-counts.';

commit;

-- guards INSIDE their own transaction (0043 lesson: a guard that cannot roll back is a
-- report). Numbers are asserted against the pre-migration baseline measured on the
-- rehearse-verbs-20260802 branch, so a drift in the role branches fails loudly here rather
-- than being discovered later by a verb returning the wrong answer.
do $$
declare
  leads int; clients int; vendors int; deals int; parties int; total int;
  schein int; dup_subjects int; dup_refs int; roles int; grantees int; leaked text;
begin
  select count(*) filter (where subject_type = 'lead'),
         count(*) filter (where subject_type = 'client'),
         count(*) filter (where subject_type = 'vendor'),
         count(*) filter (where subject_type = 'deal'),
         count(*) filter (where subject_type = 'party'),
         count(*)
    into leads, clients, vendors, deals, parties, total
    from v_ref_index;

  -- The four pre-existing branches must be untouched. These are the live counts measured
  -- immediately before this migration ran; any change means the replace altered an existing
  -- branch, which is the one thing an additive migration must not do.
  if (leads, clients, vendors, deals) <> (207, 168, 290, 40) then
    raise exception 'existing branches changed: lead=% client=% vendor=% deal=% '
                    '(expected 207/168/290/40) — this migration must only ADD rows',
                    leads, clients, vendors, deals;
  end if;

  if parties = 0 then
    raise exception 'party branch emitted no rows — the defect is unfixed';
  end if;
  if total <> leads + clients + vendors + deals + parties then
    raise exception 'row counts do not reconcile: % <> sum of branches', total;
  end if;

  -- The reported symptom, asserted as the done-test. 17 org parties named Henry Schein
  -- exist; before this migration v_ref_index returned zero of them and `find` answered with
  -- an unrelated human whose first name matched.
  select count(*) into schein
    from v_ref_index where subject_type = 'party' and display_name ilike 'Henry Schein';
  if schein <> 17 then
    raise exception 'expected 17 resolvable Henry Schein party rows, got %', schein;
  end if;

  -- No subject appears twice. A party emitted under both 'party' and a role type would make
  -- every such name permanently ambiguous to resolveSubject, which refuses to guess past one
  -- match — the fix would have broken lookups that work today.
  select count(*) into dup_subjects from (
    select subject_type, subject_id from v_ref_index group by 1, 2 having count(*) > 1) x;
  if dup_subjects <> 0 then
    raise exception '% duplicated (subject_type, subject_id) group(s)', dup_subjects;
  end if;

  select count(*) into roles from (
    select party_id from v_ref_index
     where subject_type in ('lead', 'client', 'vendor') and party_id is not null
    intersect
    select party_id from v_ref_index where subject_type = 'party') x;
  if roles <> 0 then
    raise exception '% party_id(s) appear under BOTH a role branch and the party branch', roles;
  end if;

  select count(*) into dup_refs from (
    select ref from v_ref_index where ref is not null group by ref having count(*) > 1) x;
  if dup_refs <> 0 then
    raise exception '% ref(s) now resolve to more than one row — refs must stay unique', dup_refs;
  end if;

  -- The 0020 incident: a view whose grants a later migration silently dropped. CREATE OR
  -- REPLACE preserves ACLs, but asserting it is cheaper than rediscovering it in production.
  select count(*) into grantees from information_schema.role_table_grants
   where table_name = 'v_ref_index' and privilege_type = 'SELECT'
     and grantee in ('carr_reader', 'carr_writer', 'carr_jobs');
  if grantees <> 3 then
    raise exception 'only % of 3 roles hold select on v_ref_index (reader/writer/jobs)', grantees;
  end if;

  -- The safe-columns boundary, checked mechanically rather than by reading the SQL.
  select string_agg(column_name, ', ') into leaked
    from information_schema.columns
   where table_name = 'v_ref_index'
     and column_name in ('phone', 'email', 'notes', 'notes_path', 'contact_state_reason');
  if leaked is not null then
    raise exception 'contact detail leaked into the reader surface: %', leaked;
  end if;

  raise notice 'v_ref_index now indexes SUBJECTS, not roles: % rows (% lead, % client, '
               '% vendor, % deal, + % party). Henry Schein resolves to % rows. '
               'Zero double-counts. `find` and resolveSubject still filter to '
               'lead/client/vendor in tools.js — widen that filter to make this reachable.',
               total, leads, clients, vendors, deals, parties, schein;
end $$;
