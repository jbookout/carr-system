-- 0106_precedent_and_point_in_time.sql
-- TWO READS THE RECORD ALREADY HAD THE MATERIAL FOR (loops #279 and #281).
--
-- Neither of these adds data. Both expose something the record has been carrying since
-- the beginning and that nothing could reach.
--
-- ── #279, PRECEDENT SEARCH ───────────────────────────────────────────────────────────────
-- 481 rulings are recorded (the loop said 363; measured 2026-08-13 it is 481) and the only
-- way to retrieve one is a human remembering it exists. `find` matches party.name /
-- deal.name / client.roster_ref and nothing else, so the decision history is write-only in
-- practice. That is the missing evidence behind the two counterparty chairs: council-landlord
-- and council-listing-agent are both specified to announce "runs at very low n" in EVERY run,
-- and a searchable ruling history is exactly what raises it.
--
-- TRIGRAM, NOT EMBEDDINGS, and the loop is explicit about why: "no embedding store, no second
-- database". At 481 rows a similarity scan is free, so this ships as a view plus one index
-- and buys nothing by being cleverer.
--
-- ── #281, POINT-IN-TIME RECONSTRUCTION ───────────────────────────────────────────────────
-- `event` has carried field-level old/new on every write since 0001, so the record is
-- replayable and NOTHING EXPOSES IT. carr_reader holds no grant on any base table, and
-- v_subject_timeline — the one event-shaped view a read session can reach — deliberately
-- drops old_value and new_value. So the replay material exists and is unreachable by the
-- role that would use it.
--
-- WHAT IT IS FOR, and it is written into doctrine as a human chore today. Rule 3fa17fa0 makes
-- a session STOP and ask Joe "is this still true?" before drafting off notes older than ~60
-- days — a question the event log can answer mechanically. Rule 23ea0607 (a Salesforce phase
-- is not authoritative for a deal already closed in the Deal Room) is the same shape
-- underneath: knowing what we believed WHEN is how you settle which surface went stale.

begin;

-- ── 1. PRECEDENT ─────────────────────────────────────────────────────────────────────────
-- A decision is an EVENT, not a table (there is no `decision` relation — checked before
-- building this, because the loop's phrasing implies one). v_decision_entry is the existing
-- read surface; this view adds only the searchable text, so there is one definition of what
-- a ruling is and this is not a second one.
create or replace view v_precedent as
select d.decision_id,
       d.entry_date,
       d.title,
       d.human_quote,
       d.agent_rationale,
       d.author,
       d.session_key,
       d.provenance,
       d.cost_delta,
       d.quality_delta,
       -- ONE haystack, weighted by construction rather than by a scoring formula: the title
       -- is repeated so a title hit outranks a body hit without anyone tuning a coefficient.
       -- The human's own words are included because a fork is often findable only by how the
       -- partner phrased it, which is never how the summary phrases it.
       coalesce(d.title,'') || ' ' || coalesce(d.title,'') || ' '
         || coalesce(d.human_quote,'') || ' ' || coalesce(d.agent_rationale,'') as haystack
  from v_decision_entry d;

comment on view v_precedent is
  'Every recorded ruling with one searchable haystack (0106, loop #279). The title is '
  'deliberately doubled so a title match outranks a body match with no scoring coefficient '
  'to tune, and the human_quote is included because a fork is often findable only by how the '
  'partner phrased it. Read by the find-precedent verb; not a second definition of what a '
  'decision is — it reads v_decision_entry.';

grant select on v_precedent to carr_reader, carr_writer, carr_exporter;

-- The index is on the BASE table the decision view reads, because you cannot index a view.
-- At 481 rows this buys little today and costs almost nothing; it exists so the verb does not
-- become the reason nobody logs decisions once there are ten thousand.
create index if not exists event_decision_text_trgm
  on event using gin ((coalesce(new_value->>'title','') || ' ' ||
                       coalesce(human_quote,'') || ' ' ||
                       coalesce(agent_rationale,'')) gin_trgm_ops)
  where verb = 'log-decision';

-- ── 2. POINT IN TIME ─────────────────────────────────────────────────────────────────────
-- The field-level change log, which is what makes a replay possible. Every row is one field
-- of one subject changing once.
--
-- SENSITIVE VALUES ARE ALREADY HANDLED UPSTREAM and this view does not re-handle them: 0001's
-- addendum A9 stores {"__sensitive_ref": "<uuid>"} in place of a designated sensitive value,
-- so what lands here is the reference, never the secret. A `redacted` flag is lifted out so a
-- reader can tell a withheld value from an absent one — the same rail as unmeasured vs zero.
create or replace view v_field_history as
select e.subject_type,
       e.subject_id,
       e.field,
       e.old_value,
       e.new_value,
       e.occurred_at,
       e.recorded_at,
       e.verb,
       a.slug            as actor,
       e.cause,
       e.human_quote,
       (e.new_value ? '__sensitive_ref') as redacted
  from event e
  left join actor a on a.id = e.actor_id
 where e.field is not null;

comment on view v_field_history is
  'One row per field of one subject changing once (0106, loop #281). event has carried '
  'old/new since 0001 and nothing exposed it — v_subject_timeline drops both columns and '
  'carr_reader cannot see a base table, so the replay material existed and was unreachable. '
  '`redacted` is lifted out of the jsonb because a value withheld under 0001 addendum A9 must '
  'not read like a value that was never set.';

grant select on v_field_history to carr_reader, carr_writer, carr_exporter;

-- The read the state-as-of verb actually performs: the newest value of each field at or
-- before a given instant. Expressed as a function rather than a view because the cutoff is
-- the caller's, and a view would force every caller to re-derive the distinct-on.
create or replace function state_as_of(p_type text, p_id uuid, p_at timestamptz)
returns table (field text, value_at jsonb, changed_at timestamptz, changed_by text,
               verb text, later_changes int, current_value jsonb)
language sql stable as $$
  with hist as (
    select h.field, h.new_value, h.occurred_at, h.actor, h.verb
      from v_field_history h
     where h.subject_type = p_type and h.subject_id = p_id
  ), at_time as (
    select distinct on (field) field, new_value, occurred_at, actor, verb
      from hist where occurred_at <= p_at
     order by field, occurred_at desc
  ), now_val as (
    select distinct on (field) field, new_value
      from hist order by field, occurred_at desc
  )
  select a.field, a.new_value, a.occurred_at, a.actor, a.verb,
         -- THE COLUMN THAT MAKES THE ANSWER SAFE TO ACT ON. A value with later changes is a
         -- historical value; one with none is still current. Without this a caller cannot
         -- tell "this was true then and still is" from "this was true then and has moved",
         -- which is the entire question rule 3fa17fa0 makes a session stop and ask about.
         (select count(*)::int from hist h
           where h.field = a.field and h.occurred_at > p_at) as later_changes,
         n.new_value
    from at_time a left join now_val n on n.field = a.field
   order by a.field;
$$;

comment on function state_as_of(text, uuid, timestamptz) is
  'What the record said about one subject at one instant (0106, loop #281). Returns the '
  'newest value of each field at or before the cutoff, plus later_changes and the current '
  'value beside it — because "this was true then and still is" and "this was true then and '
  'has since moved" are different answers and a caller must never have to guess which it '
  'holds.';

grant execute on function state_as_of(text, uuid, timestamptz)
  to carr_reader, carr_writer, carr_exporter;

-- ── 3. DONE-TEST ─────────────────────────────────────────────────────────────────────────
do $$
declare n int; hits int; fields int;
begin
  select count(*) into n from v_precedent;
  if n < 400 then
    raise exception '0106 done-test: v_precedent shows % rulings, expected at least the 481 measured', n;
  end if;
  -- The search has to actually match something a reader would search for. 'loop' appears in
  -- many rulings; a zero here means the haystack is not being built.
  select count(*) into hits from v_precedent where haystack ilike '%migration%';
  if hits = 0 then
    raise exception '0106 done-test: precedent search matched nothing for a common term';
  end if;
  select count(*) into fields from v_field_history;
  if fields = 0 then
    raise exception '0106 done-test: v_field_history is empty — event.field is never set?';
  end if;
  -- And the replay must return something for a subject that demonstrably has history.
  perform 1 from v_field_history limit 1;
  if not exists (
    select 1 from (
      select subject_type, subject_id from v_field_history
       group by 1,2 order by count(*) desc limit 1) t,
      lateral state_as_of(t.subject_type, t.subject_id, now()) s) then
    raise exception '0106 done-test: state_as_of returned nothing for the busiest subject';
  end if;
  raise notice '0106 done-test ok — % rulings searchable, % field-change rows replayable', n, fields;
end $$;

commit;
