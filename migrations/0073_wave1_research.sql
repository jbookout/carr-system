-- 0073_wave1_research.sql — wave 1 of the research-council ruling (decision
-- a317439f, loop #222). Three pieces, no data moved:
--
-- 1. export_run.file_sha — the render-tamper detector (C1). export_run has
--    carried a CANONICAL-DATA checksum since 0006, which proves what the DB
--    said at export; it deliberately never hashed FILE BYTES (openpyxl
--    restamps metadata). But the failure class that cost V-BNK-050 (the
--    Carissa Adams clobber) is a file edited AFTER export — Drive web, another
--    device, a partial sync — which no data checksum can see. file_sha stores
--    the written file's bytes at export; ops/renders-verify.py re-hashes the
--    live file and a mismatch means the render was touched by something other
--    than the exporter. Bound action (rule 590b11e1): re-export the target and
--    investigate the edit — printed in the health row itself.
--
-- 2. The re-verify queue learns relevance ordering with an age floor (E8).
--    v_expired_verification ordered by touch-weight x age starves nothing:
--    any row expired beyond the configured floor surfaces FIRST regardless of
--    how untouched its subject is (Codex's anti-starvation requirement).
--    Relevance = activity rows on the subject, the same countable-events
--    standard rule faf1b643 applies to vendor levels.
--
-- 3. forgetting.age_floor_days config row — the floor as data, not a literal.

begin;

alter table export_run add column if not exists file_sha text;
comment on column export_run.file_sha is
  'sha256 of the WRITTEN FILE''s bytes at export (0073). The data checksum '
  'proves what the DB said; this proves what the file was. A live file whose '
  'bytes no longer match was edited by something other than the exporter — '
  'the V-BNK-050 clobber class, now machine-detectable.';

insert into system_config (key, value, note) values
  ('forgetting.age_floor_days', '30',
   '0073 (E8 anti-starvation): a re-verify row expired more than this many days '
   'surfaces at the head of v_expired_verification regardless of subject touch '
   'frequency. Relevance ordering below the floor, hard seniority above it.')
on conflict (key) do nothing;

drop view if exists v_expired_verification;
create view v_expired_verification as
with floor_cfg as (
  select coalesce((select (value #>> '{}')::int from system_config
                    where key = 'forgetting.age_floor_days'), 30) as days
),
touches as (
  select coalesce(a.vendor_id, a.client_id, a.lead_id, a.deal_id) as subject_id,
         count(*) as touch_count
    from activity a
   group by 1
)
select f.id            as flag_id,
       f.subject_type,
       f.subject_id,
       f.kind,
       f.observed_at,
       f.expires_on,
       case when f.expires_on is not null and f.expires_on < current_date
            then 'expired'
            else 'unstamped_volatile' end as reason,
       coalesce(t.touch_count, 0)         as subject_touches,
       (f.expires_on is not null
        and f.expires_on < current_date - (select days from floor_cfg))
                                          as past_age_floor
  from record_flag f
  left join touches t on t.subject_id = f.subject_id
 where (f.expires_on is not null and f.expires_on < current_date)
    or (f.kind in ('verified','title','email','cell','office_phone')
        and f.expires_on is null
        and f.observed_at < now() - interval '180 days')
 order by
   (f.expires_on is not null
    and f.expires_on < current_date - (select days from floor_cfg)) desc,
   coalesce(t.touch_count, 0) desc,
   f.observed_at asc;

grant select on v_expired_verification to carr_reader, carr_jobs, carr_writer;

commit;

do $$
declare n int;
begin
  select count(*) into n from information_schema.columns
   where table_name='export_run' and column_name='file_sha';
  if n <> 1 then raise exception '0073: file_sha missing'; end if;
  select count(*) into n from system_config where key='forgetting.age_floor_days';
  if n <> 1 then raise exception '0073: age floor config missing'; end if;
  perform 1 from v_expired_verification limit 1;
end $$;
