-- 0085_decision_price.sql — surface the two fields that let a build decision
-- state what it cost and what it bought, so "did that work?" stops being
-- unanswerable a month later.
--
-- ─────────────────────────────────────────────────────────────────────────────
-- WHY. Idea 68, from the loop-vs-graph study 2026-08-09 (capture d79ff9ae).
-- The source records both deltas together and insists on writing them down:
-- +60% inference cost bought an approval rate move from 45% to 82.5%, and his
-- reason for the discipline is that "the honest number is easy to forget once
-- the pipeline is running well." We had nowhere to put either number. The
-- doctrine store, the Deal Council and report-card v2 all shipped inside one
-- week carrying no before-and-after measure, so none of them can ever be shown
-- to have worked — only asserted to have.
--
-- WHY NO TABLE CHANGE. log-decision already writes new_value as jsonb holding
-- title, quote_absent and provenance. Two more optional keys cost nothing and
-- break nothing: every existing row simply returns null for them. This is the
-- cheapest honest version idea 68 asked for, and the alternative — a decision
-- price table — would put a second home under a fact that belongs on the
-- decision itself, which loses to consolidation bias d367188d.
--
-- WHY BOTH FIELDS OR NEITHER, ENFORCED IN THE VERB AND NOT HERE. Half a price
-- is not a price. A cost with no quality number is a complaint, and a quality
-- number with no cost is a boast; either alone is exactly the selective
-- reporting the discipline exists to prevent. The verb refuses one without the
-- other. That check lives in the Worker rather than in a CHECK constraint
-- because the pairing is a doctrine rule about honest reporting, and doctrine
-- belongs where Joe can change it without a migration (same reasoning R-40a
-- already applies to decision grouping).
--
-- WHY FREE TEXT AND NOT NUMERIC. The unit that matters changes per build:
-- model calls, dollars, minutes of Joe's attention, drafts approved out of
-- forty. Forcing a float would force a fake one. What is enforced is that a
-- baseline appears in the same string as the after-value, because a delta with
-- no baseline is the thing the skeptic chair already refuses on client work.
-- ─────────────────────────────────────────────────────────────────────────────

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
    -- 0085: what it cost and what it bought. Null on every row written before
    -- this migration, which is honest — those decisions genuinely were not
    -- priced, and backfilling a guess would be worse than the gap.
    --
    -- APPENDED AT THE END DELIBERATELY. `create or replace view` may only add
    -- columns after the existing ones; inserting these next to `provenance`,
    -- where they read better, fails with "cannot change name of view column".
    -- Caught on the first apply of this file, 2026-08-09.
    e.new_value ->> 'cost_delta'::text AS cost_delta,
    e.new_value ->> 'quality_delta'::text AS quality_delta,
    (e.new_value ? 'cost_delta') AS priced
   FROM record_source rs
     JOIN event e ON e.id = rs.entity_id AND rs.entity_type = 'event'::text
     JOIN actor act ON act.id = e.actor_id
  WHERE rs.source_system = 'decision-history'::text;

comment on view v_decision_entry is
  'One row per logged decision, as decision-history.md renders it. 0085 added '
  'cost_delta / quality_delta / priced: what a build cost and what it bought, '
  'recorded at the moment it shipped. BOUND ACTION: when a decision changes how '
  'the system works, price it in the same call — a build with no before-and-after '
  'number can never be shown to have worked, only asserted to have. Null on '
  'everything predating 2026-08-09 because those really were unpriced; nothing '
  'is backfilled.';

commit;

-- ── proof, in the same run ───────────────────────────────────────────────────
do $$
declare
  v_rows int; v_priced int; v_broken int;
begin
  -- The view must still return every historical decision. A rewrite that
  -- silently narrowed the row set would take decision-history.md with it.
  select count(*), count(*) filter (where priced) into v_rows, v_priced
    from v_decision_entry;
  if v_rows = 0 then
    raise exception '0085: v_decision_entry returned 0 rows after rewrite — the render would go empty';
  end if;

  -- Nothing predating this migration may claim to be priced.
  select count(*) into v_broken
    from v_decision_entry
   where priced and (cost_delta is null or quality_delta is null);
  if v_broken <> 0 then
    raise exception '0085: % row(s) flagged priced while missing a half — the verb pairing leaked', v_broken;
  end if;

  raise notice '0085 ok — % decisions visible, % priced (expected 0 on first apply)',
    v_rows, v_priced;
end $$;
