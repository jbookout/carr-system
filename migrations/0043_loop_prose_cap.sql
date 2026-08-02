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
-- system now enforces one screen further down. That is the same defect this whole audit
-- kept turning up — doctrine written where nothing keeps it true — and it would have been
-- especially costly here, because that paragraph explicitly claims authority: "this header
-- wins over the task files".
--
-- Targeted string replacement, not a rewrite: the paragraph carries a lot of settled
-- doctrine (escalation rules, the 🗓 promotion mechanic, where unmarked rows live) that is
-- all still correct. Only the cap sentence is stale.

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

commit;

-- guard: the retired cap is gone from every stored prose block, the new one is present,
-- and no prose block was emptied or otherwise mangled by the replacement.
do $$
declare stale int; fresh int; emptied int;
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

  select count(*) into emptied from loop_block
   where prose_md is not null and length(btrim(prose_md)) = 0;
  if emptied > 0 then
    raise exception '% prose block(s) came out empty — replacement damaged stored prose', emptied;
  end if;

  raise notice 'cap doctrine corrected in % prose block(s)', fresh;
end $$;
