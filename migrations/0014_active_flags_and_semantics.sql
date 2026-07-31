-- 0014: set the active-book flags (Joe's ruling, 2026-07-31 ~03:00 UTC) and
-- record the semantic line that makes the two look-alike statuses distinguishable.
--
-- Joe's rider 1: "Cold - not started" and "Roster - unworked" read as synonyms to
-- a fresh session, and picking the wrong one silently moves a client on or off the
-- working book. The distinction is now written down next to the values themselves,
-- not left in a chat transcript, so Dell's brain reads the same rule.

alter table client_status add column note text;
comment on column client_status.note is
  'Why this status exists and when to use it. Read before assigning a status.';

update client_status set note =
  'ON THE WORKING BOOK, not yet started. Appears in clients-active.md. Use for a '
  'client Joe or Dell intends to work but has not yet touched.'
 where slug = 'cold_not_started';

update client_status set note =
  'BULK UNIVERSE, not the working book. Excluded from clients-active.md. Use for a '
  'roster record nobody has committed to working. Promote to "Cold - not started" '
  'the moment it belongs on the book.'
 where slug = 'roster_unworked';

-- The book = these statuses, plus anyone with an open deal regardless of status.
update client_status set is_active_pipeline = true
 where slug in ('cold_not_started','research','due_diligence','legal','negotiation',
                'closing','pending','warm_re_engagement','active_awaiting_reply',
                'active_relationship_building','active_client','negotiating',
                'paused','active');

-- Guard: if a slug above were misspelled it would flag silently fewer rows and the
-- book would quietly shrink. Fail the migration instead.
do $$
declare n int;
begin
  select count(*) into n from client_status where is_active_pipeline;
  if n <> 14 then
    raise exception 'expected 14 active-pipeline statuses, got %', n;
  end if;
end $$;
