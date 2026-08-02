-- 0043_loop_prose_cap.sql — the file's own header still taught the retired cap.
--
-- 0041 moved the bell cap to 3 PER DOMAIN and 0042 grouped the render by lane, but the
-- marker-convention paragraph that open-loops.md opens with is stored prose
-- (loop_block.prose_md) and still read:
--
--   "**🔔** = actionable THIS WEEK; the daily heartbeat lists these oldest first,
--    **hard cap 5** (more than 5 means re-tier, don't stack)."
--
-- So the first thing any session read at the top of the file contradicted the rule the
-- system now enforces one screen further down — and that paragraph explicitly claims
-- authority ("this header wins over the task files"), which is what made it worth a
-- migration rather than a shrug.
--
-- ── THIS FILE FAILED ONCE. Recorded because the failure is more instructive than the fix.
--
-- The first attempt raised "4 prose block(s) came out empty — replacement damaged stored
-- prose". The replacement had damaged nothing. Those four blocks (one each in
-- open-loops-backlog.md, action-required.md, team-loops.md and idea-bank.md) carry
-- block_key IS NULL and no header: they are STRUCTURAL SPACERS, and build_loop_file
-- documents exactly why they exist — "A prose-only block is emitted even when EMPTY: the
-- last block of open-loops-backlog.md is exactly that, and it is what carries the file's
-- trailing newline." A guard that treats empty-as-damage was simply wrong about the schema.
--
-- The real damage was structural, not textual: the guard sat AFTER `commit`, so when it
-- raised, the UPDATE was already durable and migrate.py had recorded nothing. The database
-- had the change and the ledger did not — a split brain, from a migration whose whole job
-- was to keep doctrine and reality in step. Every guard in this file now runs INSIDE the
-- transaction, so a failed check rolls the change back instead of stranding it.
--
-- Both fixes are the same lesson at different levels: a check is only as good as its model
-- of what "normal" looks like, and a check that cannot undo what it disapproves of is a
-- report, not a guard.
--
-- IDEMPOTENT BY CONSTRUCTION. The first attempt's UPDATE already landed, so the
-- like-'%hard cap 5%' filter now matches nothing and this re-run is a no-op on the data.
-- The guards still run and still have to pass.

begin;

update loop_block
   set prose_md = replace(
         prose_md,
         '**hard cap 5** (more than 5 means re-tier, don''t stack)',
         '**hard cap 3 per domain** (more than 3 in a lane means re-tier, don''t stack — '
         'the cap was 5 across the whole list before loops had domains, which left under '
         'one 🔔 per lane and drove everything to unmarked; `v_loop_bell_cap` reports '
         'breaches)')
 where prose_md like '%**hard cap 5**%';

-- guards INSIDE the transaction: a failure here rolls the update back.
do $$
declare stale int; fresh int; damaged int;
begin
  select count(*) into stale from loop_block where prose_md like '%hard cap 5%';
  if stale <> 0 then
    raise exception '% prose block(s) still teach the retired cap of 5', stale;
  end if;

  select count(*) into fresh from loop_block where prose_md like '%hard cap 3 per domain%';
  if fresh = 0 then
    raise exception 'no prose block carries the new cap — the replacement matched nothing, '
                    'so the wording in the file must have drifted from the pattern';
  end if;

  -- Empty prose is NORMAL for a structural spacer (block_key IS NULL, no header): those
  -- blocks exist to carry a file's trailing newline and have always been empty. Damage
  -- would be a block that renders CONTENT — one with a block_key or header_cols — ending
  -- up blank. That is what this checks, and what the first version got wrong.
  select count(*) into damaged from loop_block
   where prose_md is not null
     and length(btrim(prose_md)) = 0
     and (block_key is not null or header_cols is not null);
  if damaged > 0 then
    raise exception '% content block(s) came out empty — replacement damaged stored prose',
                    damaged;
  end if;

  raise notice 'cap doctrine corrected; % block(s) carry the per-domain rule', fresh;
end $$;

commit;
