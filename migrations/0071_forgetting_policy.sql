-- 0071_forgetting_policy.sql — loop #212: the record layer learns to forget on
-- purpose. From the 2026-08-06 Memory Engineer capture (preflight-pass.md
-- §Memory engineering): growth SLOPE bankrupts a long-lived store, none of the
-- systems Stanford tested prunes by default, and this layer was no exception —
-- record_flag has carried expires_on since 0001 and NOTHING has ever consumed
-- it (the same day's enrichment run stamped zero of its 40 rows), ingest_inbox
-- holds 576 rows nobody counts, and event/tool_call growth is invisible to the
-- monthly health audit.
--
-- THREE OBJECTS, none of which deletes anything (the no-DELETE posture stands;
-- forgetting here means EXPIRING-AS-UNVERIFIED and SEEING the slope, never
-- erasing history):
--   1. v_expired_verification — the re-verify queue. Rows whose verification
--      has expired, plus volatile 'verified' rows that were never stamped with
--      expires_on (surfaced as unstamped, because an unstamped volatile fact
--      quietly becomes permanent "truth" — the exact decay the compiled rule
--      about expired-reads-as-unverified promises to prevent and had no
--      mechanism for). Consumed by contact-enrichment-weekly.
--   2. v_ingest_backlog — status counts + oldest-new age for ingest_inbox, so
--      "576 rows and nobody knows" (loop #182's neighbor) becomes a number a
--      health row can go amber on.
--   3. growth_snapshot — one row per accumulating table per day, written by the
--      health check via carr_jobs; slope = today vs the prior snapshot. A view
--      computes the deltas so the audit reads slope, not size.

begin;

create table growth_snapshot (
  taken_on   date not null,
  table_name text not null,
  row_count  bigint not null,
  primary key (taken_on, table_name)
);
comment on table growth_snapshot is
  'Daily row counts for the accumulating tables (0071). Written by the health '
  'check (carr_jobs); v_growth_slope reads consecutive snapshots. Append-only '
  'by convention; a day''s row is idempotent via ON CONFLICT DO NOTHING.';

create view v_growth_slope as
select cur.table_name,
       cur.taken_on,
       cur.row_count,
       prev.taken_on              as prev_taken_on,
       prev.row_count             as prev_row_count,
       cur.row_count - prev.row_count as delta,
       (cur.taken_on - prev.taken_on)  as days_between
  from growth_snapshot cur
  left join lateral (
    select p.taken_on, p.row_count
      from growth_snapshot p
     where p.table_name = cur.table_name and p.taken_on < cur.taken_on
     order by p.taken_on desc limit 1) prev on true;

create view v_expired_verification as
select f.id            as flag_id,
       f.subject_type,
       f.subject_id,
       f.kind,
       f.observed_at,
       f.expires_on,
       case when f.expires_on is not null and f.expires_on < current_date
            then 'expired'
            else 'unstamped_volatile' end as reason
  from record_flag f
 where (f.expires_on is not null and f.expires_on < current_date)
    or (f.kind in ('verified','title','email','cell','office_phone')
        and f.expires_on is null
        and f.observed_at < now() - interval '180 days');

comment on view v_expired_verification is
  'The re-verify queue (0071). expired = the row said when it stops being '
  'trustworthy and that day passed; unstamped_volatile = a volatile-kind '
  'verification (title/contact facts age with promotions and job moves) never '
  'given an expiry and now older than 180 days. Either way the fact reads as '
  'UNVERIFIED for decisions until re-checked — nothing is deleted.';

create view v_ingest_backlog as
select status,
       count(*)                                    as n,
       min(received_at)                            as oldest,
       extract(day from now() - min(received_at))::int as oldest_age_days
  from ingest_inbox
 group by status;

-- carr_jobs writes snapshots and reads all three views (views check their own
-- grants; underlying-table access rides the view owner). carr_reader gets the
-- views for renders and sessions; exporter inherits reader (0006).
grant insert, select on growth_snapshot to carr_jobs;
grant select on growth_snapshot        to carr_reader;
grant select on v_growth_slope         to carr_reader, carr_jobs;
grant select on v_expired_verification to carr_reader, carr_jobs, carr_writer;
grant select on v_ingest_backlog       to carr_reader, carr_jobs;

commit;

do $$
declare n int;
begin
  select count(*) into n from information_schema.tables where table_name='growth_snapshot';
  if n <> 1 then raise exception '0071: growth_snapshot missing'; end if;
  if not has_table_privilege('carr_jobs', 'growth_snapshot', 'insert') then
    raise exception '0071: carr_jobs cannot write snapshots'; end if;
  if has_table_privilege('carr_jobs', 'growth_snapshot', 'delete')
     or has_table_privilege('carr_writer', 'growth_snapshot', 'delete') then
    raise exception '0071: DELETE granted on growth_snapshot'; end if;
  perform 1 from v_ingest_backlog limit 1;   -- view executes under owner
  perform 1 from v_expired_verification limit 1;
end $$;
