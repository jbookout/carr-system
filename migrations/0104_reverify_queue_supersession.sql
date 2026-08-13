-- 0104_reverify_queue_supersession.sql — the re-verify queue can now DRAIN.
--
-- THE DEFECT, proven in production on 2026-08-13 by the weekly contact-enrichment
-- run (loop #346). v_expired_verification (0071, reshaped by 0073) selects flag
-- rows that are expired or unstamped-volatile. `record-finding` only ever ADDS
-- rows; it never retires or supersedes one. So re-verifying a subject writes a
-- NEW, correctly-stamped flag and leaves the OLD row exactly where it was — still
-- in the queue, forever, no matter how many times the subject is re-checked.
--
-- Measured case: vendor V-SUP-037 (Tatum Cannon). Flag 71c33899, kind 'title',
-- observed 2026-01-30, expires_on NULL — surfaced as unstamped_volatile. The
-- 2026-08-13 run re-verified her against LinkedIn and Patterson's own pages and
-- wrote kind 'verified', observed 2026-08-13, expires_on 2027-02-09. The queue
-- still returned the 2026-01-30 row.
--
-- WHY IT MATTERS MORE THAN ONE ROW: this queue is the FIRST queue
-- contact-enrichment-weekly is instructed to drain, ahead of never-verified
-- records, on the reasoning that a stale "verified" stamp misleads where a blank
-- at least looks unknown. A queue that cannot drain sends every future run back
-- to re-research the same already-fresh contact and never reach the records that
-- actually need it. The task's own top priority was a treadmill.
--
-- THE FIX: derive supersession from data already present rather than adding a
-- pointer column somebody has to maintain (rule d367188d, consolidation bias —
-- one source of truth plus derive-on-demand beats maintaining two versions).
-- A flag is SUPERSEDED when a newer flag on the SAME subject re-verifies it and
-- is ITSELF still trustworthy. All four conditions bind:
--   1. same subject (subject_type AND subject_id — id alone could collide across
--      types), and not the row itself;
--   2. strictly newer observed_at;
--   3. covering the same ground: identical kind, OR kind 'verified', which is the
--      umbrella pass. This asymmetry is deliberate and matches the stamping rule
--      contact-enrichment-weekly already follows ("title, company, email, cell,
--      office_phone, and any `verified` pass covering them"). A newer 'title' does
--      NOT supersede an older 'verified', because re-checking one field is not a
--      re-verification of all of them. Narrow beats convenient here.
--   4. the superseder is FRESH: expires_on present AND not yet past. An unstamped
--      or already-expired newer row supersedes NOTHING — it would itself belong in
--      this queue, which is the correct outcome, not a bug.
-- Plus: a "searched, not found" row (value->>'found' = 'false') can never
-- supersede. Failing to find a fact is not verifying it. record_flag has no
-- `found` column; it lives inside the value JSON, so the test reads the JSON.
--
-- WHAT THIS DOES NOT CHANGE: the nine output columns, their names, order and
-- types are byte-identical to 0073 — ops/forgetting-check.py groups on `reason`
-- and tools/health-check.py names the view in its breach message. The ordering
-- clause (0073's E8 anti-starvation: past-age-floor first, then touch weight,
-- then oldest) is preserved verbatim. Nothing is deleted; a superseded row stays
-- in record_flag as history and stays resolvable. Only the QUEUE stops nagging.

begin;

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
 where ((f.expires_on is not null and f.expires_on < current_date)
     or (f.kind in ('verified','title','email','cell','office_phone')
         and f.expires_on is null
         and f.observed_at < now() - interval '180 days'))
   -- 0104: a later, still-valid re-verification retires this row from the queue.
   and not exists (
     select 1
       from record_flag g
      where g.subject_type = f.subject_type
        and g.subject_id   = f.subject_id
        and g.id          <> f.id
        and g.observed_at  > f.observed_at
        and (g.kind = f.kind or g.kind = 'verified')
        and g.expires_on is not null
        and g.expires_on >= current_date
        and (g.value ->> 'found') is distinct from 'false')
 order by
   (f.expires_on is not null
    and f.expires_on < current_date - (select days from floor_cfg)) desc,
   coalesce(t.touch_count, 0) desc,
   f.observed_at asc;

comment on view v_expired_verification is
  'The re-verify queue (0071, ordering 0073, supersession 0104). expired = the '
  'row said when it stops being trustworthy and that day passed; '
  'unstamped_volatile = a volatile-kind verification (title/contact facts age '
  'with promotions and job moves) never given an expiry and now older than 180 '
  'days. Either way the fact reads as UNVERIFIED for decisions until re-checked '
  '— nothing is deleted. 0104: a row leaves this queue once a NEWER flag on the '
  'same subject re-verifies it (same kind, or an umbrella ''verified'' pass) AND '
  'that newer flag is itself stamped and unexpired and is not a not-found row. '
  'Before 0104 the queue could never drain, because record-finding only adds '
  'rows — re-verifying a subject left the stale row in place forever.';

grant select on v_expired_verification to carr_reader, carr_jobs, carr_writer;

commit;

-- Guards in their own transaction (the 0043 lesson) ---------------------------
do $$
declare
  n int; grantees int; cols text;
begin
  -- the view survived and still exposes the EXACT nine-column contract 0073 set,
  -- in order. ops/forgetting-check.py groups on `reason`; a column rename or a
  -- reorder breaks a caller silently, which is the whole reason this is asserted.
  select string_agg(column_name, ',' order by ordinal_position) into cols
    from information_schema.columns where table_name = 'v_expired_verification';
  if cols is distinct from
     'flag_id,subject_type,subject_id,kind,observed_at,expires_on,reason,subject_touches,past_age_floor'
  then
    raise exception '0104: column contract changed, got %', cols;
  end if;

  -- the 0073 grant posture survives the replace
  select count(distinct grantee) into grantees
    from information_schema.role_table_grants
   where table_name = 'v_expired_verification' and privilege_type = 'SELECT'
     and grantee in ('carr_reader','carr_jobs','carr_writer');
  if grantees < 3 then
    raise exception '0104: select grants incomplete (% of 3)', grantees;
  end if;

  -- the view is queryable (0073's own smoke check, kept)
  perform 1 from v_expired_verification limit 1;

  -- SUPERSESSION ACTUALLY BITES: no row may remain in the queue while a newer,
  -- stamped, unexpired, found re-verification of the same ground exists. If this
  -- is ever non-zero the predicate has regressed.
  select count(*) into n
    from v_expired_verification q
   where exists (
     select 1 from record_flag g
      join record_flag f2 on f2.id = q.flag_id
      where g.subject_type = f2.subject_type
        and g.subject_id   = f2.subject_id
        and g.id          <> f2.id
        and g.observed_at  > f2.observed_at
        and (g.kind = f2.kind or g.kind = 'verified')
        and g.expires_on is not null
        and g.expires_on >= current_date
        and (g.value ->> 'found') is distinct from 'false');
  if n <> 0 then
    raise exception '0104: % superseded row(s) still in the queue', n;
  end if;
end $$;
