-- 0046_party_identity.sql — the person gets an identity of their own.
--
-- Joe, 2026-08-02: "we are doig the party work today. im not waiting for a new user to
-- enter the system to do all this complicated stuff that will only be more complicated
-- then." And on the refs themselves: records should not be "boxed in by 'L-163'".
--
-- THE DEFECT THIS OPENS THE FIX FOR. Identity currently lives on the ROLE, not the person,
-- so every new role mints a new person:
--   * 42 name-identical lead/client pairs sit on DIFFERENT parties
--   * ZERO parties hold more than one role — not a coincidence, it is the mechanism
--   * Dr. Erik Petersen is party 909881e7 as lead L-201 and party 60346cc2 as client C-126
--   * Derrick Richardson is V-MSC-005 AND L-102, on two parties: one human in 665 who
--     genuinely wears two roles, and the model cannot say so
-- Downstream, this is why `find Zimmern` returns deals:[] while a deal exists, and part of
-- why capture coverage reads ~5%: touches land on parties that do not know about each other.
--
-- WHAT THIS MIGRATION DOES, AND DELIBERATELY DOES NOT DO. It gives every party a stable
-- ref of its own and a contact state. It merges NOTHING. The 42 pairs stay exactly as they
-- are until a later step runs them through confirm-merge one at a time, with a human
-- looking at both records — this system merged the wrong Beasley once (an import welded
-- Jenna Beasley to Jeff Beasley DMD; corrected within the hour, Jeff now L-158), and the
-- 0044 guard had to learn that even a name comparison needs parentheticals stripped before
-- it means anything. Identity first, merging second, deliberately.
--
-- THE EXISTING REFS ARE NOT REPLACED. L-, C- and V- refs keep working and keep meaning what
-- they mean. P- sits BENEATH them as the person, so every document, email and dossier line
-- citing L-163 still resolves. Nothing anyone has written stops being findable — which is
-- the whole reason to add rather than rename.
--
-- CONTACT STATE DEFAULTS TO 'active', and that default is a real assertion rather than a
-- shrug: absent an opt-out, a business contact is contactable. The dangerous default is the
-- other one — silently marking 1,082 people do-not-contact would be a fabricated fact with
-- consequences. Everything else about state (why, until when, at whose request) is stored
-- because nothing can derive it: "this doctor asked not to receive email" is a fact about a
-- person that must survive every stage change, and 0045 showed what happens when a fact
-- like that is inferred instead of recorded.

begin;

-- ── the vocabulary (ORDER 3 ref-table pattern, as with deal_phase and loop_domain) ──
create table if not exists contact_state (
  slug        text primary key,
  label       text not null,
  contactable boolean not null,
  sort        integer not null unique
);

insert into contact_state (slug, label, contactable, sort) values
  ('active',         'Active — normal contact',                       true,  10),
  ('nurture',        'Nurture — long-cycle cadence only',             true,  20),
  ('paused',         'Paused — do not contact until a date',          false, 30),
  ('do_not_contact', 'Do not contact — standing, with a reason',      false, 40)
on conflict (slug) do nothing;

-- ── party identity ──
create sequence if not exists ref_party_seq;

alter table party add column if not exists ref text;
alter table party add column if not exists contact_state text
  references contact_state(slug) default 'active';
alter table party add column if not exists contact_state_reason text;
alter table party add column if not exists contact_state_until date;
alter table party add column if not exists contact_state_cadence text;
alter table party add column if not exists contact_state_set_by uuid references actor(id);
alter table party add column if not exists contact_state_set_at timestamptz;

-- Backfill deterministically: oldest party gets P-0001. created_at then id as tiebreak, so
-- a re-run on a restored dump produces the same refs rather than reshuffling identity.
update party p
   set ref = 'P-' || lpad(n.rn::text, 4, '0')
  from (select id, row_number() over (order by created_at, id) as rn from party) n
 where n.id = p.id and p.ref is null;

select setval('ref_party_seq', (select count(*) from party));

update party set contact_state = 'active' where contact_state is null;

alter table party alter column contact_state set not null;
create unique index if not exists party_ref_uniq on party (ref);
create index if not exists party_contact_state_idx on party (contact_state);

comment on column party.ref is
  'P-#### — the PERSON''s stable identity, added 0046. Sits BENEATH the role refs rather '
  'than replacing them: L-/C-/V- keep working and keep meaning what they mean, so every '
  'document citing L-163 still resolves. Assigned by created_at order, so a re-run on a '
  'restored dump reproduces the same refs.';
comment on column party.contact_state is
  'active | nurture | paused | do_not_contact. STORED, never derived — "this person asked '
  'not to be contacted" is a fact only a human knows and it must survive every stage '
  'change. Eligibility for a given campaign IS derived, from this plus role state; storing '
  'that instead is what produced the 0045 fault.';
comment on column party.contact_state_set_by is
  'who recorded the state (who_initiated). Matters when the answer is do_not_contact: a '
  'client asking to be left alone and Joe deciding to pause outreach are different facts '
  'with different half-lives.';

commit;

-- guards INSIDE the transaction (0043 lesson: a guard that cannot roll back is a report)
do $$
declare total int; unreffed int; dupes int; unstated int; states int; unmerged_pairs int;
begin
  select count(*) into total from party;
  select count(*) into unreffed from party where ref is null;
  if unreffed > 0 then raise exception '% of % parties have no ref', unreffed, total; end if;

  select count(*) into dupes from (select ref from party group by ref having count(*) > 1) x;
  if dupes > 0 then raise exception '% duplicated party ref(s) — identity must be unique', dupes; end if;

  select count(*) into unstated from party where contact_state is null;
  if unstated > 0 then raise exception '% parties with no contact state', unstated; end if;

  select count(*) into states from contact_state;
  if states <> 4 then raise exception 'expected 4 contact states, found %', states; end if;

  -- Every duplicate pair must still be UNMERGED. Merging is a separate, human-gated step
  -- and this migration must not have quietly started it.
  select count(*) into unmerged_pairs
    from lead l
    join party lp on lp.id = l.party_id
    join party cp on lower(btrim(cp.name)) = lower(btrim(lp.name)) and cp.id <> lp.id
    join client c on c.party_id = cp.id and c.merged_into is null;
  if unmerged_pairs < 42 then
    raise exception 'name-identical lead/client pairs dropped from 42 to % — this migration '
                    'must not merge anything; that is confirm-merge''s job, one at a time',
                    unmerged_pairs;
  end if;

  raise notice 'party identity live: % parties, P-0001..P-%, all contactable by default. '
               '% duplicate pairs left UNMERGED by design.',
               total, lpad(total::text, 4, '0'), unmerged_pairs;
end $$;
