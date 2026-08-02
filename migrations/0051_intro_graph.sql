-- 0051_intro_graph.sql — the introduction graph gets a broker, and a vocabulary worth using.
--
-- Joe: "maybe theres a way to indicate which vendors have introduced us to other vendors or
-- clients/prospects and vice versa. which vendors we have introduced to one another... if we
-- do this the right way in the database it could be extremely powerful and fast/efficient."
--
-- THE MACHINERY ALREADY EXISTED AND WAS EMPTY: 31 edges across 1,082 parties, only two of
-- six kinds ever used (knows 24, intro 7), for a business where the vendor network produces
-- most of the lead flow. Same shape as log-decision and add-loop kind:'idea' — built, never
-- fed. Capture is the fix (emails and Doc dialogue, per Joe, ~2-3 weeks); this migration
-- makes sure that when capture arrives the shape is right.
--
-- ── WHY THE OLD VOCABULARY COULD NOT ANSWER JOE'S QUESTION ──
-- An introduction is TERNARY: A introduced B to C. party_link was BINARY. That works while
-- the third party is implicitly CARR ("a vendor introduced us to someone") and fails
-- completely for "we introduced these two to each other" — the reciprocity that EARNS
-- referrals back, and the half Joe most wants to see.
--
-- The old kinds also blurred three distinctions:
--   intro vs intro_received  direction, ambiguous — and the live data proves it: six edges
--                            read "Bruce Pardington -> Chris Kelly" while the note says
--                            CHRIS offered the intro. Stored backwards from how it reads.
--   intro vs referral        an introduction is a CONNECTION; a referral is BUSINESS SENT.
--                            Different value, same slot.
--   knows                    24 of 31 edges — a catch-all absorbing everything.
--
-- ── THE NEW VOCABULARY: two categories, six kinds ──
-- Standing relationships (symmetric, durable, describe the world):
--   knows        they know each other
--   works_with   an actual professional working relationship
-- Events (directional, dated, carry via_party):
--   can_introduce    an OFFER — potential, not yet done
--   intro_requested  WE ASKED — Joe: "sometimes we do want to be introduced to someone
--                    through a vendor. whether thats a prospect or another vendor"
--   introduced       COMPLETED
--   referred         business actually sent
--
-- The grammar carries the meaning: PRESENT tense = potential, PAST tense = happened.
-- can_introduce -> intro_requested -> introduced is a pipeline you can count at each stage.
-- Chris Kelly reads as six offers, none requested, none completed — a follow-up list that
-- was invisible before.
--
-- ── UNIFORM SHAPE ──
--   from_party / to_party = the two people connected
--   via_party             = WHO connected them (null = direct, no broker)
-- Every one of Joe's four questions is then one query:
--   vendors who introduced us to clients   via = vendor, other side = client
--   vendors who introduced us to vendors   via = vendor, other side = vendor
--   vendors WE introduced to each other    via = Joe/Dell, both sides vendors
--   vendors we introduced to clients       via = Joe/Dell, one side client
-- and the reciprocity ledger is "count where via = us" against "count where via = them".
--
-- JOE AND DELL BECOME PARTIES. They were not, so "introduced US to someone" had no
-- endpoint. Without them the uniform shape is impossible.

begin;

insert into party_link_kind (slug, label, sort) values
  ('intro_requested', 'Intro requested (we asked)', 45),
  ('introduced',      'Introduced (completed)',     50),
  ('referred',        'Referred (business sent)',   60)
on conflict (slug) do nothing;

alter table party_link add column if not exists via_party uuid references party(id);
alter table party_link add column if not exists occurred_on date;

create index if not exists party_link_via_idx on party_link (via_party, kind);

-- ── Joe and Dell as parties, so "us" is addressable ──
insert into party (kind, name, ref, contact_state, created_by, updated_by)
select 'person', a.display_name, 'P-' || lpad((1082 + row_number() over (order by a.slug))::text, 4, '0'),
       'active', a.id, a.id
  from actor a
 where a.slug in ('joe','dell')
   and not exists (select 1 from party p where p.name = a.display_name)
on conflict do nothing;

-- ── the six existing "intro" edges are OFFERS, and point the wrong way ──
-- Each note reads "Chris Kelly (V-CPA-006) — offered intro", so Chris is the BROKER and the
-- other party is who he can reach. Restated in the uniform shape: from = us, to = the
-- target, via = Chris. Only rows whose note says "offered" are touched; the Jon Shaw ->
-- Tyrer edge ("intro 2026-07-29") is a completed introduction and becomes `introduced`.
update party_link pl
   set kind = 'can_introduce',
       via_party = pl.to_party,
       to_party = pl.from_party,
       from_party = (select id from party where name = 'Joe Bookout' limit 1)
 where pl.kind = 'intro' and pl.note ilike '%offered intro%';

update party_link
   set kind = 'introduced'
 where kind = 'intro' and note not ilike '%offered intro%';

comment on column party_link.via_party is
  'WHO made the connection. Null = direct, no broker. Added 0051 because an introduction is '
  'ternary (A introduced B to C) and a binary edge cannot record "we introduced these two" — '
  'the reciprocity that earns referrals back, which was exactly the half Joe most wanted.';
comment on column party_link.occurred_on is
  'When it happened. An offer and a completed introduction are different events and the gap '
  'between them is the follow-up.';

do $$
declare kinds int; joe_id uuid; offers int; done int; legacy int;
begin
  select count(*) into kinds from party_link_kind;
  if kinds < 9 then raise exception 'expected at least 9 link kinds, found %', kinds; end if;

  select id into joe_id from party where name = 'Joe Bookout' limit 1;
  if joe_id is null then raise exception 'Joe was not created as a party — "us" has no endpoint'; end if;
  if not exists (select 1 from party where name = 'Wayne McCraney' or name ilike '%McCraney%') then
    raise notice 'NOTE: Dell was not created as a party (actor display_name may differ)';
  end if;

  select count(*) into offers from party_link where kind = 'can_introduce';
  select count(*) into done   from party_link where kind = 'introduced';
  select count(*) into legacy from party_link where kind = 'intro';
  if legacy > 0 then raise exception '% edges still carry the retired kind "intro"', legacy; end if;
  if offers <> 6 then raise exception 'expected 6 offers restated, got %', offers; end if;
  if done <> 1 then raise exception 'expected 1 completed introduction, got %', done; end if;

  -- every restated offer must now carry a broker; an offer with no broker is meaningless
  if exists (select 1 from party_link where kind = 'can_introduce' and via_party is null) then
    raise exception 'a can_introduce edge has no via_party — that is the whole point of it';
  end if;

  raise notice 'intro graph: % offers / % completed, via_party live, Joe is a party', offers, done;
end $$;

commit;
