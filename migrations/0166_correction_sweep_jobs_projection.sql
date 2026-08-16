-- 0166_correction_sweep_jobs_projection.sql
--
-- The unattended monthly correction sweep needs a bounded read surface, not
-- the owner connection formerly supplied by db-tap.  Do not grant carr_jobs
-- either source view: v_decision_entry carries fields the sweep never reads,
-- and v_defect_class is a general reporting surface.  These projections expose
-- only the exact columns the deterministic report renders.

begin;

create or replace view public.v_correction_sweep_defects as
select defect_class, occurrences, caught_by_human, first_seen, last_seen,
       sources_unread, rules_violated
  from public.v_defect_class;

create or replace view public.v_correction_sweep_decisions as
select entry_date, title, human_quote
  from public.v_decision_entry
 where human_quote is not null
   and btrim(human_quote) <> '';

revoke all on public.v_correction_sweep_defects,
              public.v_correction_sweep_decisions from public;
grant select on public.v_correction_sweep_defects,
                public.v_correction_sweep_decisions to carr_jobs;

commit;

do $$
begin
  if not has_table_privilege('carr_jobs', 'public.v_correction_sweep_defects', 'select')
     or not has_table_privilege('carr_jobs', 'public.v_correction_sweep_decisions', 'select') then
    raise exception '0166 FAILED: carr_jobs cannot read correction sweep projections';
  end if;
  if has_table_privilege('carr_jobs', 'public.v_defect_class', 'select')
     or has_table_privilege('carr_jobs', 'public.v_decision_entry', 'select') then
    raise exception '0166 FAILED: carr_jobs received a broad correction-source view grant';
  end if;
end $$;
