-- 0110_decision_entry_occurred_at.sql
-- ORDER THE DECISION LOG WITHIN A DAY, NOT JUST BETWEEN DAYS.
--
-- WHAT WENT WRONG, 2026-08-13. v_decision_entry exposes occurred_at ONLY as
-- entry_date, a cast to date that throws the time away. decision-history.md is a
-- byte-budgeted window that renders the newest entries in full, so it orders by
-- `entry_date desc, session_key desc` — which is no ordering at all inside a
-- single day, because every entry a partner logs in one session shares both keys.
-- Which decisions get full text on a busy day is therefore luck.
--
-- It cost something real the same day. Joe settled the recovery time objective in
-- the afternoon; the entry recording his answer lost its place to older entries
-- from that morning and rendered only as an index line, while an earlier entry
-- stating "RTO alone remains open" rendered in FULL. For a few minutes the file a
-- human reads asserted the opposite of the position he had just taken. That was
-- corrected by amending the stale entry, but the cause was the ordering, and the
-- cause is here.
--
-- WHY THIS IS A ONE-COLUMN CHANGE. Nothing new is recorded and nothing is
-- backfilled: event.occurred_at has always held the timestamp, and this view has
-- always read it — it just narrowed it to a date on the way out. This widens the
-- exposure so a renderer can sort by the moment a decision was actually taken.
--
-- APPENDED AT THE END, like 0085's price columns and for the same reason:
-- `create or replace view` may only ADD columns after the existing ones. Putting
-- occurred_at next to entry_date, where it reads better, fails with "cannot change
-- name of view column". 0085 learned this on its first apply; this file does not
-- relearn it.

begin;

create or replace view v_decision_entry as
 SELECT rs.external_key,
    split_part(rs.external_key, '#'::text, 1) AS source_file,
    split_part(rs.external_key, '#'::text, 2) AS session_key,
    e.occurred_at::date AS entry_date,
    act.slug AS author,
    e.new_value ->> 'title'::text AS title,
    e.human_quote,
    e.agent_rationale,
    e.cause,
    (e.new_value ->> 'quote_absent'::text)::boolean AS quote_absent,
    e.new_value ->> 'provenance'::text AS provenance,
    e.subject_id AS decision_id,
    e.id AS event_id,
    e.new_value ->> 'cost_delta'::text AS cost_delta,
    e.new_value ->> 'quality_delta'::text AS quality_delta,
    (e.new_value ? 'cost_delta') AS priced,
    -- 0110: the full timestamp, so a renderer can order within a day. entry_date
    -- stays exactly as it was — it is what the file prints as a heading, and
    -- changing its type would break every consumer for no gain.
    e.occurred_at AS occurred_at
   FROM record_source rs
     JOIN event e ON e.id = rs.entity_id AND rs.entity_type = 'event'::text
     JOIN actor act ON act.id = e.actor_id
  WHERE rs.source_system = 'decision-history'::text;

comment on view v_decision_entry is
  'One row per logged decision, as decision-history.md renders it. 0085 added '
  'cost_delta / quality_delta / priced: what a build cost and what it bought, '
  'recorded at the moment it shipped. 0110 added occurred_at, the full timestamp '
  'behind entry_date: ORDER BY occurred_at DESC, never by entry_date alone, or '
  'entries logged on the same day sort arbitrarily and a byte-budgeted render '
  'drops whichever ones luck puts last. entry_date remains a date and remains '
  'what the render prints as a heading.';

commit;

-- ── proof, in the same run ───────────────────────────────────────────────────
do $$
declare
  v_rows       int;
  v_null_ts    int;
  v_same_day   int;
  v_ordering   int;
begin
  select count(*) into v_rows from v_decision_entry;
  select count(*) into v_null_ts from v_decision_entry where occurred_at is null;

  -- The condition that made this necessary: entries sharing a date AND a session
  -- key, which the old ORDER BY could not separate.
  select count(*) into v_same_day
    from (select entry_date, session_key
            from v_decision_entry
           group by entry_date, session_key
          having count(*) > 1) t;

  -- And the proof it is now separable: distinct timestamps inside those groups.
  select count(*) into v_ordering
    from (select entry_date, session_key
            from v_decision_entry
           group by entry_date, session_key
          having count(*) > 1 and count(distinct occurred_at) > 1) t;

  raise notice '0110: % decision rows, % with a null timestamp', v_rows, v_null_ts;
  raise notice '0110: % date+session groups hold more than one decision; % of those are now orderable',
    v_same_day, v_ordering;

  if v_null_ts > 0 then
    raise exception '0110 FAILED: % rows have a null occurred_at — the column is '
                    'not usable as a sort key', v_null_ts;
  end if;

  if v_same_day > 0 and v_ordering = 0 then
    raise exception '0110 FAILED: % multi-entry groups exist but none has distinct '
                    'timestamps, so exposing occurred_at fixes nothing', v_same_day;
  end if;
end $$;
