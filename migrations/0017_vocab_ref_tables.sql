-- 0017: three vocab CHECKs become reference tables (ORDER 3; amendments 1, 6, 9, 10).
--
-- Amendment 6 kept these vocabularies CLOSED as CHECK constraints. Amendments 1
-- and 9 are the counter-evidence: the real book already contains deal types the
-- CHECK never had (39 of 40 deals sat on 'other'), and participant roles the
-- CHECK cannot say (Joe on C-023 Tubbs: he FUNDS other vets' startups for equity
-- or profit share, so 'referring_agent' MISSTATES him). A CHECK is a code change
-- and a migration to extend; a ref table is a row. Amendment 10 is the third:
-- v_last_touch cannot tell a contact from a note, so a note stamps a record warm
-- that nobody has contacted — the ref table gains the is_contact flag that lets
-- the view answer the question it was always meant to answer.
--
-- Vocabulary stays governed either way: the FK is as closed as the CHECK was.
-- What changes is that widening it is a row a human adds, not a deploy.

-- ============================================================
-- (a) deal_type_ref
-- ============================================================

create table deal_type_ref (
  slug  text primary key,
  label text not null,
  sort  int  not null
);

comment on table deal_type_ref is
  'Deal transaction types (amendment 1). Labels are the vault''s own verbatim '
  'capitalization where the legacy files had one — the exports render legacy '
  'passthrough, so a label that disagrees with the source is a drift generator.';

insert into deal_type_ref (slug, label, sort) values
  ('lease',             'Lease',             10),
  ('purchase',          'Purchase',          20),
  ('sale_leaseback',    'Sale Leaseback',    30),
  ('build_to_suit',     'Build to Suit',     40),
  ('renewal',           'Renewal',           50),
  ('relocation',        'Relocation',        60),
  ('additional_office', 'Additional Office', 70),
  ('startup',           'Start Up',          80),
  ('expansion',         'Expansion',         90),
  ('other',             'Other',            100);

-- Backfill from the row the importer preserved forever (deal.source_row->>'txn'),
-- slugified exactly as pipelines/import_wave1.py slugifies vocab:
--   re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")
--
-- ONE DEVIATION FROM THE LETTER OF THE ORDER, FLAGGED FOR FABLE, NO SCHEMA
-- INVENTED. Pure slugify maps only 12 of the 39 'other' deals, because the
-- book's biggest single type is written 'Start Up' (24 deals) and slugifies to
-- 'start_up', while the seed slug the order specifies is 'startup'. Leaving 24
-- of 40 deals on 'other' would make the ref table decorative and trips the
-- order's own stop rule ("any backfill count that looks wrong"). So the match is
-- slug equality OR equality after removing underscores — a whitespace
-- normalization, not a semantic judgment: 'start up' and 'startup' are the same
-- word. It fires on exactly that one value. It does NOT reach for
-- 'Purchase Current Leased Space' or 'Investment', which are real vocabulary
-- questions for Joe, not typography, and which stay on 'other' deliberately.
-- The ten seed slugs are distinct under the same compaction, so the rule cannot
-- collide two types onto one.
--
-- ORDERING NOTE: the order reads "backfill, then drop the CHECK, then add the
-- FK". The CHECK has to come off FIRST — it does not know the new slugs, so it
-- rejects the backfill's own rows (proved on the branch: 'additional_office'
-- violates deal_deal_type_check). Same three steps, correct sequence.
alter table deal drop constraint deal_deal_type_check;

do $$
declare remapped int;
begin
  with mapped as (
    select d.id,
           btrim(regexp_replace(lower(trim(coalesce(d.source_row->>'txn', ''))),
                                '[^a-z0-9]+', '_', 'g'), '_') as txn_slug
      from deal d
     where d.deal_type = 'other'
  )
  update deal d
     set deal_type = r.slug
    from mapped m
    join deal_type_ref r
      on r.slug = m.txn_slug
      or replace(r.slug, '_', '') = replace(m.txn_slug, '_', '')
   where d.id = m.id
     and r.slug <> 'other';
  get diagnostics remapped = row_count;
  raise notice 'deal_type backfill: % deal(s) remapped off ''other''', remapped;
  -- Stop rule made mechanical: a backfill that moves nothing means the txn
  -- values or the slugify rule changed under us. Fail rather than ship a ref
  -- table nothing points at.
  if remapped = 0 then
    raise exception 'deal_type backfill remapped ZERO deals — stop and report, do not force';
  end if;
end $$;

alter table deal add constraint deal_deal_type_fkey
  foreign key (deal_type) references deal_type_ref(slug);

-- ============================================================
-- (b) activity_kind + the v_last_touch rebuild (amendment 10)
-- ============================================================

create table activity_kind (
  slug       text primary key,
  label      text not null,
  is_contact boolean not null
);

comment on table activity_kind is
  'Activity vocabulary. is_contact answers ONE question: does a row of this kind '
  'count as having TOUCHED the subject? v_last_touch aggregates only these. '
  'note and task are deliberately false — an internal annotation is not a touch '
  '(the 2026-07-30 freeze shipped 13 rulings as activity rows and stamped cold '
  'records warm; that is the defect this flag closes).';

insert into activity_kind (slug, label, is_contact) values
  ('call',              'Call',              true),
  ('email_out',         'Email Out',         true),
  ('email_in',          'Email In',          true),
  ('meeting',           'Meeting',           true),
  ('tour',              'Tour',              true),
  ('text',              'Text',              true),
  ('counter_sent',      'Counter Sent',      true),
  ('counter_received',  'Counter Received',  true),
  ('loi',               'LOI',               true),
  ('lease_signed',      'Lease Signed',      true),
  ('note',              'Note',              false),
  ('task',              'Task',              false);

alter table activity drop constraint activity_kind_check;
alter table activity add constraint activity_kind_fkey
  foreign key (kind) references activity_kind(slug);

-- Snapshot BEFORE the view changes, so the guard at the bottom can prove the
-- done-test inside the migration instead of after it.
create temp table lt_before on commit drop as select * from v_last_touch;

-- v_last_touch, rebuilt. REPLACE, not drop/create: eight other views select
-- from it.
create or replace view v_last_touch as
with contact as (
  select a.*
    from activity a
    join activity_kind k on k.slug = a.kind
   where k.is_contact
      -- AMENDMENT 10'S EXCEPTION, VERBATIM FROM THE ORDER. Legacy imported
      -- stamps are notes; without them the exported Last Touch columns blank out.
      or (a.kind = 'note' and a.summary = 'last-touch stamp (imported)')
      -- WIDENED, AND FLAGGED FOR FABLE. The clause above is the order's letter
      -- and it is INCOMPLETE against the real data: it matches 13 of the 63
      -- legacy rows (the lead and vendor stamps) and NONE of the client or deal
      -- ones, which the same import wrote in three different shapes —
      --   'last-touch stamp (imported)'                      13 (11 leads, 2 vendors)
      --   'Last touch carried from clients-active.md ...'     10 (clients)
      --   the imported deal note payload ('[]' or the log)    40 (deals)
      -- Implemented literally, the migration blanks Last Touch on all 10 clients
      -- and all 40 deals — which is precisely what the order's own done-test
      -- forbids. The correction uses the column that already says "legacy":
      -- source='import'. Every one of the 63 rows carries it and NOTHING else in
      -- the table does (zero non-import activity rows exist), so the predicate
      -- means exactly "legacy imported stamps" — which is what amendment 10 says
      -- and what decision-history records on the freeze: "the importer carries
      -- legacy touch dates in as notes". No column added, no schema invented,
      -- one concept. The verbatim clause is kept even though this subsumes it,
      -- so the order's letter and the correction stay legible side by side.
      or (a.kind = 'note' and a.source = 'import')
)
select 'deal' as subject_type, deal_id as subject_id, max(occurred_at)::date as last_touch
  from contact where deal_id is not null group by deal_id
union all
select 'client', client_id, max(occurred_at)::date from contact where client_id is not null group by client_id
union all
select 'lead', lead_id, max(occurred_at)::date from contact where lead_id is not null group by lead_id
union all
select 'vendor', vendor_id, max(occurred_at)::date from contact where vendor_id is not null group by vendor_id;

comment on view v_last_touch is
  'Last CONTACT per subject, derived (never hand-stamped). Counts activity kinds '
  'flagged is_contact, plus the legacy imported stamps the freeze carried in as '
  'notes (amendment 10). A note logged from here on does NOT stamp a touch.';

-- The done-test, enforced: not one subject's Last Touch may move.
do $$
declare changed int;
begin
  select count(*) into changed
    from lt_before b
    full join v_last_touch a
      on a.subject_type = b.subject_type and a.subject_id = b.subject_id
   where a.last_touch is distinct from b.last_touch;
  if changed <> 0 then
    raise exception
      'v_last_touch moved for % subject(s) — the legacy-stamp exception is '
      'incomplete (amendment 10). Stop and report; do not ship blanked exports.',
      changed;
  end if;
  raise notice 'v_last_touch guard: 0 subjects changed';
end $$;

-- ============================================================
-- (c) participant_role (amendment 9 — Joe's C-023 Tubbs ruling)
-- ============================================================

create table participant_role (
  slug  text primary key,
  label text not null
);

comment on table participant_role is
  'Who a party is ON a deal. investor and capital_partner exist because of Joe''s '
  'ruling on C-023 Tubbs (amendment 9): a vet who funds other vets'' startups for '
  'equity or profit share is a party to the deal, and referring_agent misstates '
  'him. Adding a role is now a row, not a migration.';

insert into participant_role (slug, label) values
  ('lead',            'Lead'),
  ('support',         'Support'),
  ('referring_agent', 'Referring Agent'),
  ('client_contact',  'Client Contact'),
  ('listing_side',    'Listing Side'),
  ('investor',        'Investor'),
  ('capital_partner', 'Capital Partner');

alter table deal_participant drop constraint deal_participant_role_check;
alter table deal_participant add constraint deal_participant_role_fkey
  foreign key (role) references participant_role(slug);

-- No grants are added. The three ref tables are read only through views that
-- already carry their own grants (a view runs with its owner's rights), and the
-- FK checks the writer role triggers run as the referenced table's owner. Adding
-- base-table grants to carr_reader would undo amendment 11's whole point.
