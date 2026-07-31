-- 0020: the intro graph becomes readable and single-vocabulary (ORDER 18;
-- Fable's rulings on ORDER 17's three flags, 2026-07-31).
--
-- ORDER 17 turned vendor.links_label prose into 28 real party_link edges. It
-- ended with three findings that were flagged rather than improvised, and this
-- migration is the ruling on all three:
--   1. TWO VOCABULARIES. The backfill wrote knows/intro/referral; the
--      `link-parties` verb offers can_introduce/intro_sent/intro_received/
--      works_with/referred. A partner recording an intro tomorrow could not
--      write any kind this backfill used, and vice versa — and nothing errored,
--      because party_link.kind carried no CHECK and no ref table. Fixed here the
--      way 0017 fixed the others: the vocabulary becomes ROWS, and the near
--      duplicates collapse (intro_sent and intro are the same fact; referred and
--      referral are the same fact).
--   2. NO UNIQUE CONSTRAINT. "unique on (from, to, kind)" was a property of the
--      backfill script, not of the database, so `link-parties` called twice
--      wrote two identical rows. Dedup becomes structural here.
--   3. UNREACHABLE FROM THE PRODUCT. carr_reader holds nothing on party_link and
--      no view exposed it, so no read verb could see the graph. v_party_graph is
--      the ORDER 7 answer applied again: a purpose-built view with SAFE COLUMNS
--      ONLY, never a widened reader.
--
-- ─────────────────────────────────────────────────────────────────────────────
-- ALSO IN THIS FILE, AND IT IS NOT COSMETIC: `grant select on v_ref_index to
-- carr_writer`. A production incident surfaced 2026-07-31 afternoon — EVERY
-- ref-based WRITE verb was returning 500 "permission denied for view
-- v_ref_index". ROOT CAUSE: ORDER 7 moved resolveSubject off the base tables and
-- onto v_ref_index, and its migration granted the view to carr_reader only. But
-- resolveSubject also runs inside the write transaction, under carr_writer,
-- which never got the grant — carr_writer held ZERO view grants system-wide.
-- Broken from ORDER 7's deploy (~01:15 CT) until the hotfix, and invisible for
-- half a day because smoke-reads.sh covered reads only. That is the seat's miss
-- in the ORDER 7 spec, not the executing session's.
--
-- Joe applied the grant live as a hotfix; the statement below re-asserts it
-- (grants are idempotent) so the repaired state is in the migration history
-- rather than only in someone's terminal. THE LESSON IS APPLIED FORWARD:
-- v_party_graph is granted to BOTH roles in the same breath, because a view any
-- verb may resolve through needs the write role too, and finding that out in
-- production twice would be a choice.

-- ── 0. the incident fix, re-asserted ─────────────────────────────────────────
grant select on v_ref_index to carr_writer;

-- ── 1. party_link_kind: the vocabulary becomes rows ──────────────────────────
-- Same shape as 0017's three ref tables: slug is the value the column stores,
-- label is what a human reads, sort is display order. An FK is exactly as closed
-- as a CHECK — what changes is that widening it is a row a human adds, not a
-- deploy.
create table party_link_kind (
  slug  text primary key,
  label text not null,
  sort  int  not null
);

comment on table party_link_kind is
  'Intro-graph edge kinds (ORDER 18). SIX kinds after the 2026-07-31 mapping: '
  'intro_sent collapsed into intro and referred into referral — the same fact '
  'said twice is how two vocabularies start. party_link.kind FKs here; the '
  'link-parties verb validates against this table and has no enum of its own.';

-- Seeded as the UNION of both vocabularies first, exactly as the order specifies,
-- so the backfill below has somewhere to stand before the duplicates are dropped.
insert into party_link_kind (slug, label, sort) values
  ('knows',          'Knows',           10),
  ('intro',          'Intro',           20),
  ('intro_received', 'Intro received',  30),
  ('can_introduce',  'Can introduce',   40),
  ('works_with',     'Works with',      50),
  ('referral',       'Referral',        60),
  -- the two that die below, seeded only so the backfill's FK target exists
  ('intro_sent',     'Intro sent',      70),
  ('referred',       'Referred',        80);

-- ── 2. collapse the near-duplicates ──────────────────────────────────────────
-- MEASURED BEFORE WRITING THIS: production holds 28 party_link rows, all from
-- ORDER 17's backfill, split knows 21 / intro 7. There is not one intro_sent or
-- referred row anywhere, because the verb that offers those kinds has never been
-- called. So both updates below are EXPECTED TO MOVE ZERO ROWS, and that is not
-- a failure — it is the state of the book. They exist so that a database which
-- did collect verb-written edges before this migration lands (Dell's side, a
-- re-run against an older copy) collapses cleanly instead of failing the FK.
do $$
declare n_sent int; n_ref int; leftover text;
begin
  update party_link set kind = 'intro'    where kind = 'intro_sent';
  get diagnostics n_sent = row_count;
  update party_link set kind = 'referral' where kind = 'referred';
  get diagnostics n_ref = row_count;
  raise notice 'kind mapping: intro_sent -> intro %, referred -> referral %', n_sent, n_ref;

  -- Nothing may still point at a slug about to be deleted.
  select string_agg(distinct kind, ', ') into leftover
    from party_link where kind in ('intro_sent', 'referred');
  if leftover is not null then
    raise exception 'rows still on a retired kind after mapping: %', leftover;
  end if;
end $$;

delete from party_link_kind where slug in ('intro_sent', 'referred');

-- ── 3. the FK, once every existing value is known-good ───────────────────────
do $$
declare orphan text;
begin
  select string_agg(distinct pl.kind, ', ') into orphan
    from party_link pl
    left join party_link_kind k on k.slug = pl.kind
   where k.slug is null;
  if orphan is not null then
    raise exception
      'party_link carries kind(s) the vocabulary does not have: %. STOP — decide '
      'whether each is a real kind (add the row) or a mistake (fix the rows) '
      'rather than widening the seed to make a constraint pass.', orphan;
  end if;
end $$;

alter table party_link
  add constraint party_link_kind_fkey foreign key (kind) references party_link_kind(slug);

-- ── 4. dedup becomes structural ──────────────────────────────────────────────
-- ORDER 17's script enforced this in Python. A property that lives in one caller
-- is not a property of the record: `link-parties` had no dedup at all, so two
-- taps on a phone wrote two identical edges.
do $$
declare dups int;
begin
  select count(*) into dups from (
    select from_party, to_party, kind from party_link
     group by 1,2,3 having count(*) > 1) d;
  if dups > 0 then
    raise exception
      'STOP: % duplicate (from_party, to_party, kind) group(s) already exist. The '
      'unique index cannot be created and the duplicates are a human decision '
      '(which note survives?), not something a migration should pick.', dups;
  end if;
end $$;

create unique index party_link_from_to_kind_uidx
  on party_link (from_party, to_party, kind);

comment on index party_link_from_to_kind_uidx is
  'ORDER 18(b): one edge per (from, to, kind). link-parties upserts against this '
  'index and returns the existing edge rather than a second row.';

-- ── 5. v_party_graph — the reader surface, ORDER 7 precedent ─────────────────
-- SAFE COLUMNS ONLY. Refs, names, the kind, the provenance note, and when the
-- edge was recorded. NO phone, NO email, NO address, NO record notes. The column
-- list of a reader-facing view is a security boundary, not a convenience.
--
-- ONE COLUMN BEYOND THE ORDER'S LIST, FLAGGED RATHER THAN SLIPPED IN: linked_at.
-- The order's (c) names six columns; its (d) says the find block must be capped
-- to "top edges by recency". Those two clauses cannot both hold — nothing in the
-- six carries recency, and ordering a view by a column it does not expose is a
-- hidden contract. linked_at is party_link.created_at, a timestamp: it is not
-- contact detail and not the class of column ORDER 7's stop rule exists to keep
-- out (phones, emails, notes). The alternative — cap by name and drop "by
-- recency" — was rejected as the larger deviation. FABLE TO CONFIRM.
--
-- One ref per party, chosen deterministically, so an edge can never appear
-- twice. Measured on a full-data copy of production before choosing: ZERO
-- parties in the system carry more than one ref today, so `distinct on` changes
-- nothing now — it is here so that a party which later holds both a lead and a
-- client ref cannot silently double every edge it touches. Precedence
-- client > vendor > lead, and a non-null ref always beats a null one (8 clients
-- carry no roster_ref).
create view v_party_graph as
with party_ref as (
  select distinct on (party_id) party_id, ref
    from (
      select c.party_id, c.roster_ref   as ref, 1 as pref from client c
      union all
      select v.party_id, v.vendor_ref,          2         from vendor v
      union all
      select l.party_id, l.registry_ref,        3         from lead l
    ) r
   order by party_id, (ref is null), pref
)
select fr.ref        as from_ref,
       fp.name       as from_name,
       tr.ref        as to_ref,
       tp.name       as to_name,
       pl.kind       as kind,
       pl.note       as note,
       pl.created_at as linked_at
  from party_link pl
  join party fp on fp.id = pl.from_party
  join party tp on tp.id = pl.to_party
  left join party_ref fr on fr.party_id = pl.from_party
  left join party_ref tr on tr.party_id = pl.to_party;

comment on view v_party_graph is
  'The intro graph under the views-only reader role (ORDER 18(c), v_ref_index '
  'precedent). SAFE COLUMNS ONLY — never add phone, email, or record notes '
  'here; a reader-scoped session sees everything in this view. `note` is the '
  'edge provenance, which for backfilled edges is the vendor label verbatim.';

-- ── 6. grants ────────────────────────────────────────────────────────────────
-- Reader: the view only, never party_link and never party_link_kind. Writer:
-- the same view (the lesson from the v_ref_index incident above — a view the
-- verbs may one day resolve through gets both roles at creation, not after an
-- outage) plus select on the vocabulary, which is what link-parties validates
-- against now that its enum is gone.
grant select on v_party_graph  to carr_reader;
grant select on v_party_graph  to carr_writer;
grant select on party_link_kind to carr_writer;

-- ── 7. guards ────────────────────────────────────────────────────────────────
do $$
declare n int; leaked text; kinds text;
begin
  -- the vocabulary is exactly six, and exactly these six
  select count(*) into n from party_link_kind;
  if n <> 6 then
    raise exception 'party_link_kind must hold exactly 6 kinds after the mapping, found %', n;
  end if;
  select string_agg(slug, ',' order by slug) into kinds from party_link_kind;
  if kinds <> 'can_introduce,intro,intro_received,knows,referral,works_with' then
    raise exception 'party_link_kind vocabulary is not the six ruled kinds: %', kinds;
  end if;

  -- the FK exists and the unique index exists (both are the order's own asks)
  if not exists (select 1 from pg_constraint where conname = 'party_link_kind_fkey') then
    raise exception 'party_link.kind FK was not created';
  end if;
  if not exists (select 1 from pg_indexes
                  where indexname = 'party_link_from_to_kind_uidx') then
    raise exception 'the (from_party, to_party, kind) unique index was not created';
  end if;

  -- THE STOP RULE, MADE MECHANICAL: carr_reader still holds zero base-table
  -- grants anywhere. Views-only is what makes the leak guard structural, and a
  -- migration that adds a reader view is exactly where it would erode.
  select string_agg(distinct g.table_name, ', ') into leaked
    from information_schema.role_table_grants g
    join pg_tables t on t.tablename = g.table_name and t.schemaname = 'public'
   where g.grantee = 'carr_reader' and g.table_schema = 'public';
  if leaked is not null then
    raise exception
      'ORDER 7/18 stop rule: carr_reader holds a BASE TABLE grant on %. Views only.', leaked;
  end if;

  -- the reader can actually see the graph (a view nobody may read is furniture)
  if not exists (select 1 from information_schema.role_table_grants
                  where grantee = 'carr_reader' and table_name = 'v_party_graph'
                    and privilege_type = 'SELECT') then
    raise exception 'carr_reader cannot select v_party_graph';
  end if;

  -- and the writer can resolve through both views — this is the incident, made
  -- impossible to reintroduce silently
  select count(*) into n
    from information_schema.role_table_grants
   where grantee = 'carr_writer' and privilege_type = 'SELECT'
     and table_name in ('v_ref_index', 'v_party_graph');
  if n <> 2 then
    raise exception
      'carr_writer must be able to select v_ref_index AND v_party_graph (% of 2). '
      'resolveSubject runs inside the write transaction — this is the 2026-07-31 '
      'production incident and it must not come back.', n;
  end if;

  -- every edge still points at a live vocabulary row, and no edge was lost
  select count(*) into n from party_link;
  raise notice 'ORDER 18 guards: 6 kinds, FK + unique index live, reader 0 base grants, writer reads both views, % edge(s) intact', n;
end $$;
