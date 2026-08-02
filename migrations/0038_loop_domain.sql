-- 0038_loop_domain.sql — loops get a DOMAIN, so business work stops being buried.
--
-- Joe, 2026-08-02: "another thing it seems like we need to have separate categories
-- for open loops. because important transactional and business things get buried by
-- system loops." Then: "nah do three categories. we need separation there."
--
-- HE IS RIGHT AND THE LIST PROVED IT. At the time he said it the hot list held 21
-- open items against its OWN DOCUMENTED HARD CAP OF 5 ("more than 5 means re-tier,
-- not stack"). Of those 21, roughly 14 were system/build work and 7 were business.
-- A deal with a confirmed GO (Gulf Coast Pelvic Floor, C-112) sat below repo chores.
-- Worse, the item that nearly went out unreviewed — 18 social drafts scheduled to
-- start firing the next morning at 8:23 — was in that same undifferentiated pile.
--
-- THREE DOMAINS, per Joe's ruling:
--   business  — deals, clients, prospects, vendor relationships. The work that earns.
--   marketing — social batches, newsletter, content, profile. Time-boxed and public,
--               so it cannot sit behind infrastructure; a queued batch fires whether
--               or not anyone read it.
--   system    — record layer, repo, radar lanes, automation. Real work, but it must
--               never outrank a live deal in the render.
--
-- A REF TABLE, not a CHECK: the ORDER 3 pattern this repo already applies to
-- deal_phase, activity_kind, lead_stage and party_link_kind. Widening a vocabulary
-- should be a row a human inserts, not a migration and a deploy.
--
-- NULLABLE ON PURPOSE, AND NOT BACKFILLED HERE. 79 existing loops need classifying
-- and a default would be a guess wearing the costume of a fact — the exact failure
-- this audit has been unpicking all day (prose read as state, empty read as healthy,
-- a narrow grep read as absence). Classification lands in its own reviewed pass, with
-- Joe seeing the proposed domain per loop. An unclassified loop renders in its own
-- "unsorted" section rather than silently defaulting into one of the three.

begin;

create table if not exists loop_domain (
  slug  text primary key,
  label text not null,
  sort  integer not null unique
);

insert into loop_domain (slug, label, sort) values
  ('business',  'Business — deals, clients, prospects, vendors', 10),
  ('marketing', 'Marketing — social, newsletter, content',       20),
  ('system',    'System — record layer, repo, automation',       30)
on conflict (slug) do nothing;

alter table loop_item add column if not exists domain text references loop_domain(slug);

comment on column loop_item.domain is
  'business | marketing | system (loop_domain). NULL = not yet classified, and that '
  'renders as its own unsorted section rather than defaulting into a domain — a '
  'guessed classification would bury exactly what this column exists to surface. '
  'Added 0038 after the hot list reached 21 items against a documented cap of 5, with '
  '14 of them system work sitting above a deal that had a confirmed GO.';

create index if not exists loop_item_domain_idx on loop_item (domain, status);

commit;

-- guard: vocabulary seeded, column present and NULLABLE, nothing classified yet.
do $$
declare doms int; nn text; classified int;
begin
  select count(*) into doms from loop_domain;
  if doms <> 3 then raise exception 'expected 3 loop domains, found %', doms; end if;

  select is_nullable into nn from information_schema.columns
   where table_name='loop_item' and column_name='domain';
  if nn is null then raise exception 'loop_item.domain was not created'; end if;
  if nn = 'NO' then
    raise exception 'loop_item.domain must stay NULLABLE — a NOT NULL forces a default, '
                    'and a defaulted domain is a guess recorded as a fact';
  end if;

  select count(*) into classified from loop_item where domain is not null;
  raise notice 'loop_domain seeded (3). loops already classified: % (expected 0 — '
               'classification is a separate reviewed pass)', classified;
end $$;
