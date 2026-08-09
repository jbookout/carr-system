-- 0083_loop_gate_cutover.sql — draw v_loop_no_blocker's boundary at the instant
-- the gate actually went live, not at midnight of the day it shipped.
--
-- ─────────────────────────────────────────────────────────────────────────────
-- THE DEFECT, found within minutes of 0081 shipping. 0081 defined
-- predates_gate as `created_at < timestamptz '2026-08-09'`, which reads
-- "anything created before the day the gate landed." But the gate is enforced
-- by the WORKER, and the Worker carrying it (version c3370cfc) deployed at
-- 2026-08-09T22:20:28.647Z — twenty-two hours into that day. Fourteen open
-- loops had been opened by other sessions earlier the same day, the newest
-- (#284) at 22:19:28Z, roughly one minute before the deploy. The view called
-- all fourteen post-gate defects. They are not: no add-loop call reaching the
-- old Worker could have been asked for a blocker, because the code that asks
-- was not running yet.
--
-- WHY THIS MATTERS MORE THAN THE ARITHMETIC. The whole point of the
-- predates_gate flag is that a `false` row means the gate was BYPASSED, which
-- is a defect to investigate rather than a backlog item to work. A flag that
-- reports fourteen false positives on day one is a check that is chronically
-- red, and a chronically red check detects nothing — that is precisely the
-- failure that let the 2026-08-08 settings.json wipe hide behind an
-- already-failing config-as-code row for a full day (loop #266). Shipping the
-- detector and the thing it detects on the same day is exactly when this is
-- cheapest to get right.
--
-- WHY A HARDCODED INSTANT AND NOT A LOOKUP. The cutover is a historical fact
-- with one value that will never change: the moment a specific Worker version
-- reached production, read from `wrangler deployments list` rather than
-- guessed. There is nothing to derive it from at query time — the database has
-- no record of Worker deploys — and inferring it from the data (say, the first
-- row carrying a blocker) would make the boundary move every time the data
-- moves, which is the opposite of what a boundary is for.
-- ─────────────────────────────────────────────────────────────────────────────

begin;

create or replace view v_loop_no_blocker as
select li.id,
       li.number,
       li.kind,
       li.domain,
       li.owner,
       li.marker,
       li.since_text,
       lb.block_key                               as section,
       left(coalesce(li.body, li.title, ''), 160) as gist,
       -- The instant Worker version c3370cfc-8ebb-4caa-ad03-e9f803e68ef9 went
       -- live, per `wrangler deployments list`. Before this, no add-loop call
       -- was capable of being asked for a blocker.
       (li.created_at < timestamptz '2026-08-09 22:20:28.647+00') as predates_gate
  from loop_item li
  join loop_block lb on lb.id = li.block_id
 where li.kind = 'open_loop'
   and li.status = 'open'
   and li.blocker_class is null
 order by li.created_at;

comment on view v_loop_no_blocker is
  'Open open_loop rows carrying no named blocker. BOUND ACTION: each row is a '
  'candidate to DO or to CLOSE, never to re-file — nobody ever established that '
  'the work needed deferring. predates_gate separates rows opened before the '
  'gate reached production (2026-08-09 22:20:28Z, Worker c3370cfc) from '
  'anything opened after it; a FALSE row means add-loop was bypassed and is a '
  'defect to investigate, not a backlog item. Boundary corrected by 0083 — '
  '0081 used midnight and so reported 14 same-day rows as bypasses.';

commit;

-- ── proof, in the same run ───────────────────────────────────────────────────
do $$
declare total int; defects int; newest timestamptz;
begin
  select count(*), count(*) filter (where not predates_gate)
    into total, defects from v_loop_no_blocker;
  select max(created_at) into newest
    from loop_item where kind='open_loop' and status='open' and blocker_class is null;

  -- Every blocker-less open row must now read as inherited. This is a real
  -- assertion rather than a formality: it fails loudly if a row slipped in
  -- after the cutover without a blocker, which would mean the gate leaks.
  if defects <> 0 then
    raise exception '0083: % of % blocker-less rows still read as post-gate bypasses (newest created_at %)',
      defects, total, newest;
  end if;
  raise notice '0083 ok — % inherited rows, 0 bypasses; newest blocker-less row %', total, newest;
end $$;
