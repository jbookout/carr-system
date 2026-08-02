-- 0049_deprecation_register.sql — deprecated things declare themselves, and get chased.
--
-- Joe, on the 0048 compatibility shim: "how long does the backwards compatibilty period
-- last until code catches up? i dont want bloat in the system but if it makes sense to
-- create a 'self-healing' component to this so that it slowly erases the old way that could
-- make sense and be safer than forcing it all now."
--
-- Right instinct, with one correction: SELF-HEALING MEANS DETECT-AND-PROMPT, NOT AUTO-DROP.
-- Dropping is irreversible and cannot be verified beforehand; auto-dropping something still
-- in use is the destructive automation with no undo. Detecting costs nothing and cannot
-- hurt. It is also the pattern every fix today followed — v_drip_conflict, v_loop_bell_cap,
-- the schedule-drift check: all report, none enforce.
--
-- WITHOUT THIS TABLE, a compatibility shim is a promise someone has to remember, which is
-- the same category of thing as "do not surface" written in prose (0032) or a cap of 5
-- taught by a file header after the cap became 3 (0043). The register turns "is it safe to
-- delete this yet?" from a memory into a query.
--
-- The half this table CANNOT answer is callers outside the repo — a Cowork session, a
-- script on Dell's Mac. pg_stat_statements would catch those and is available but not
-- installed; enabling it mid-build was not worth it. Stated here so the gap is known rather
-- than assumed away: `code_refs = 0` means nothing in THIS REPO references it.

begin;

create table if not exists deprecation (
  object_name    text primary key,
  object_kind    text not null,
  replaced_by    text,
  reason         text not null,
  deprecated_at  date not null default current_date,
  safe_to_drop_after date,
  dropped_at     date
);

comment on table deprecation is
  'Things kept alive only for compatibility. A row here is a debt with a payoff date. '
  '`run.sh health` reads it, greps the repo, and reports whether anything still references '
  'each object — so deleting is a decision made on evidence rather than nerve. Nothing here '
  'is dropped automatically: dropping is irreversible, detecting is free.';

insert into deprecation (object_name, object_kind, replaced_by, reason, safe_to_drop_after)
values (
  'prospect_pool',
  'view',
  'candidate_pool',
  'Auto-updatable shim left by 0048 so the four dependent views and six code files kept '
  'working between the rename and the Worker deploy. All six were updated the same day; '
  'this exists only until that deploy is verified live.',
  current_date + 7)
on conflict (object_name) do nothing;

-- guard, before commit
do $$
declare shim_rows int; live_rows int; registered int;
begin
  -- the shim must still WORK while it is registered — a broken shim is worse than none
  select count(*) into shim_rows from prospect_pool;
  select count(*) into live_rows from candidate_pool;
  if shim_rows <> live_rows then
    raise exception 'the prospect_pool shim shows % rows against candidate_pool''s %',
                    shim_rows, live_rows;
  end if;

  select count(*) into registered from deprecation where dropped_at is null;
  raise notice 'deprecation register live: % outstanding (shim mirrors % rows)',
               registered, shim_rows;
end $$;

commit;
