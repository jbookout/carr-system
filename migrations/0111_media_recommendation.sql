-- 0111_media_recommendation.sql
-- THE CURRICULUM BOARD GETS A RECORD BEHIND IT (loop #122).
--
-- The board already claims this table in its own footer: "Source of truth:
-- media-recommendation records in the record layer." It has said so since it shipped
-- on 2026-08-01 and the records never existed, so the page is hand-maintained HTML
-- asserting a provenance it does not have. That is a render with two writers, which
-- is the one thing every other generated surface here is built to avoid.
--
-- WHY A TABLE FOR THREE ROWS, since the smallest-structure test would normally refuse
-- one. Because the ROW COUNT is not the point — the WRITE PATH is. A recommendation
-- has a lifecycle (proposed, then read, then finished) and the whole ask is that Joe
-- change a status by saying so in any session rather than by someone editing HTML.
-- A lifecycle needs a home, and three rows with a lifecycle beat three rows in a file
-- that no verb can reach.
--
-- NOT NULL WHERE IT MATTERS, on the lesson the defect log taught this week: an
-- optional field goes unfilled. A recommendation without a WHY is a reading list
-- entry, and this board's entire premise is that Doc adds items only when an observed
-- pattern earns one — so both the reason and the pattern that prompted it are
-- required, and a row that cannot say why it exists cannot be written.

begin;

create table media_recommendation (
  id               uuid primary key default gen_random_uuid(),
  title            text not null,
  author           text,
  kind             text not null default 'book'
                     check (kind in ('book','media','article','course')),
  why              text not null,
  observed_pattern text not null,
  tags             text[] not null default '{}',
  priority         text not null default 'normal'
                     check (priority in ('now','normal')),
  status           text not null default 'new'
                     check (status in ('new','reading','done','dropped')),
  recommended_on   date not null default current_date,
  finished_on      date,
  personal_to      text not null default 'joe',
  created_at       timestamptz not null default now(),
  created_by       uuid references actor(id),
  updated_at       timestamptz not null default now(),
  updated_by       uuid references actor(id),
  version          int not null default 1,
  constraint media_recommendation_why_is_real
    check (btrim(why) <> '' and btrim(observed_pattern) <> ''),
  -- A finished item must say WHEN, and an unfinished one must not pretend to.
  constraint media_recommendation_finish_shape
    check ((status = 'done') = (finished_on is not null))
);

create unique index media_recommendation_title_uniq
  on media_recommendation (lower(btrim(title)), personal_to);

comment on table media_recommendation is
  'What Doc recommends a partner read or watch, and why (0111, loop #122). The '
  'curriculum board renders FROM here — it claimed this table as its source of truth '
  'from the day it shipped and the table did not exist, so the page was hand-edited '
  'HTML asserting a provenance it did not have. why and observed_pattern are both NOT '
  'NULL because the board''s premise is that an item appears only when a pattern earns '
  'one, and a row that cannot say why it exists must not be writable.';

grant select on media_recommendation to carr_reader, carr_writer, carr_exporter;
grant insert, update on media_recommendation to carr_writer;

create or replace view v_media_recommendation as
select m.id, m.title, m.author, m.kind, m.why, m.observed_pattern, m.tags,
       m.priority, m.status, m.recommended_on, m.finished_on, m.personal_to,
       a.slug as recommended_by, m.version, m.updated_at
  from media_recommendation m
  left join actor a on a.id = m.created_by;

comment on view v_media_recommendation is
  'The curriculum board''s read surface (0111). carr_reader holds no grant on base '
  'tables, so the exporter and every read session come through here.';

grant select on v_media_recommendation to carr_reader, carr_writer, carr_exporter;

-- ── SEED: the three items already on the board ───────────────────────────────────────────
-- Taken verbatim from the hand-maintained page so the first generated render is
-- IDENTICAL in content to what Joe already has. A migration that silently changed
-- what the board says would make the cutover impossible to verify.
insert into media_recommendation
  (title, author, kind, why, observed_pattern, tags, priority, status, recommended_on, created_by)
select v.title, v.author, 'book', v.why, v.pattern, v.tags, v.priority, 'new',
       '2026-08-01'::date, a.id
  from actor a
  cross join (values
    ('Turn the Ship Around!', 'L. David Marquet',
     'Monday you start orchestrating a human, not a machine. A submarine captain replaced orders with intent and turned the fleet''s worst boat into its best. It is your orders-with-done-tests pattern applied to people, and it pairs directly with Dell''s ramp-up. Short, practical, this week''s skill.',
     'orchestration ladder opening; Dell onboarding imminent',
     array['Orchestration'], 'now'),
    ('Superforecasting', 'Philip Tetlock & Dan Gardner',
     'your calibration ledger opened today with its first logged prediction. This is the research behind Doctrine law 35, readable, and it trains the exact skill the ledger scores: honest percentages on your own calls, updated in small moves. Your business runs on forecasts; this is the manual.',
     'calibration ledger live; first prediction logged',
     array['Calibration'], 'normal'),
    ('The Pyramid Principle', 'Barbara Minto',
     'compression is your core talent running untrained. Minto codified how executives lead with the answer and structure everything beneath it. You do this instinctively; the formal structure makes it deliberate, in writing, at stakes: LOIs, board summaries, the one-sentence framing that wins a negotiation before it starts.',
     'load-bearing compressions logged repeatedly in design sessions',
     array['Compression'], 'normal')
  ) as v(title, author, why, pattern, tags, priority)
 where a.slug = 'system'
on conflict do nothing;

do $$
declare n int; bad int;
begin
  select count(*) into n from v_media_recommendation;
  if n <> 3 then
    raise exception '0111 done-test: expected the 3 seeded items, found %', n;
  end if;
  -- The NOT NULL reason is the whole contract; assert it bites rather than trusting it.
  begin
    insert into media_recommendation (title, why, observed_pattern)
      values ('probe', '', 'x');
    raise exception '0111 done-test: a recommendation with an empty why was accepted';
  exception when check_violation then null;
  end;
  -- And a done row with no finish date must be refused.
  begin
    insert into media_recommendation (title, why, observed_pattern, status)
      values ('probe2', 'x', 'y', 'done');
    raise exception '0111 done-test: a finished item with no finished_on was accepted';
  exception when check_violation then null;
  end;
  select count(*) into bad from media_recommendation where btrim(why) = '';
  if bad <> 0 then
    raise exception '0111 done-test: % row(s) carry an empty why', bad;
  end if;
  raise notice '0111 done-test ok — 3 seeded, empty-why refused, finish-shape enforced';
end $$;

commit;
