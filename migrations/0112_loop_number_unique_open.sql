-- 0112_loop_number_unique_open.sql — one open row per number, per kind (loop #306).
--
-- THE DEFECT. Two OPEN rows of the same kind can carry the same number, and two
-- pairs did: #95 (the Awareness Play proposal and the CARR corporate newsletter
-- proposal) and #88 (a bio-header reminder and the card visual system). Every
-- loop verb that resolves by number — update-loop, close-loop, read-loop —
-- refuses on an ambiguous number. That refusal is CORRECT and it is still a
-- failure, because a human saying "close 95" is saying something the system
-- cannot act on, and the failure surfaces as a refusal rather than as an
-- obviously wrong record, so it reads like the verb is broken rather than like
-- the data is. The workaround (pass loop_id) silently picks whichever row the
-- caller happened to look up, which is how the wrong loop gets closed.
--
-- WHY THIS WAS NOT CAUGHT EARLIER. #306 was opened on 2026-08-10 naming #103 and
-- #95. By the time it was worked, #103 had resolved itself (one side closed) and
-- #88 had appeared — a THIRD collision, arriving exactly the way the row
-- predicted it would. A defect that keeps regenerating is a missing constraint,
-- not a data cleanup.
--
-- PARTIAL, ON PURPOSE — `where status = 'open'`. Numbers are deliberately
-- REUSED across closed history: the renders are per-file and the series restarts,
-- so a full unique index would refuse to close nothing and would instead reject
-- perfectly ordinary historical rows. What must be unambiguous is the set a verb
-- can still resolve against, which is exactly the open set. A row closing frees
-- its number, which is the existing behaviour and stays.
--
-- SCOPED BY KIND for the same reason. open_loop #4 and idea #4 are different
-- series, not a collision — the idea bank numbers its own rows from 1 — and every
-- loop verb already takes `kind` to narrow them. Constraining on number alone
-- would declare four legitimate pairs illegal.
--
-- ORDER MATTERS: the two live collisions are renumbered through update-loop's new
-- `number` field (which requires a renumber_reason, rule 7105955b) BEFORE this
-- runs. Creating the index first would simply fail. This file is therefore the
-- second half of that pass, and it is what stops a fourth collision appearing.

begin;

-- Fail loudly and specifically if a collision is still present, rather than
-- letting Postgres report a generic duplicate-key error against an index name
-- that says nothing about which rows are at fault.
do $$
declare
  offending text;
begin
  select string_agg(kind || ' #' || number || ' (' || n || ' rows)', ', ')
    into offending
    from (select kind, number, count(*) as n
            from loop_item where status = 'open'
           group by kind, number having count(*) > 1) s;
  if offending is not null then
    raise exception 'open loop numbers still collide: %. Renumber through update-loop (number + renumber_reason) before applying 0112.', offending;
  end if;
end $$;

create unique index if not exists loop_item_open_number_unique
  on loop_item (kind, number)
  where status = 'open';

commit;
