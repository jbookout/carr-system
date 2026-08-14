-- 0123_trace_incident_recurrence.sql
-- PROGRAM 4, GAP A2 CONTINUED (defect cae5be2e-267c-40ba-9424-b4618845e905,
-- 2026-08-14). 0122 and mcp-server/src/trace.js made a failed Worker request
-- findable by its correlation id — but only the FIRST one of its kind.
--
-- THE HOLE, EXACTLY. trace.js records a failure in two shapes, and ops.v_trace
-- can see only one of them:
--
--   FIRST occurrence of a signature — recordWorkerFailure's openIncident()
--   writes a new ops.incident whose OWN correlation_id column is that request's
--   id. ops.v_trace's incident arm (0115) selects i.correlation_id, so
--   `tools/ops-record.py trace <id>` finds it. This half works.
--
--   RECURRENCE — a second, third, hundredth failure sharing the signature
--   service|environment|route|failure_class finds the already-open incident
--   (0116's partial unique index is what makes that one incident rather than a
--   hundred) and appendFactIfNew() writes an ops.incident_fact carrying
--   source_ref = 'correlation:<the NEW request's id>'. Nothing else records
--   that id anywhere. ops.v_trace has no arm over ops.incident_fact at all —
--   it unions ops.deployment, ops.run, ops.incident and ops.work_request — so
--   tracing a recurring failure by its own correlation id returns "no trace
--   for <id>" while the incident it belongs to is real, open, and one join
--   away.
--
-- That is the worse half of the two to lose. The first failure of a kind is
-- often the one nobody was watching for; the tenth is the one somebody is
-- holding a correlation id for and asking what happened. A ledger that can
-- explain the debut and not the recurrence answers the question nobody asked.
--
-- THE FIX IS A READ, NOT A WRITE. No new column, no new table, no change to
-- what trace.js records. The recurrence's id is ALREADY durably stored, in
-- ops.incident_fact.source_ref, in the exact shape appendFactIfNew() writes
-- ('correlation:' || the uuid — see incidentFactText/appendFactIfNew in
-- mcp-server/src/trace.js). This file teaches the view to read it. Adding a
-- correlation_id COLUMN to ops.incident_fact was the alternative and was
-- refused: it would leave the same id in two places on new rows, none of it on
-- the rows already written, and a backfill parsing the very string this arm
-- parses anyway. One source of truth, derived on read (rule d367188d).
--
-- WHAT THIS ARM DELIBERATELY DOES NOT RETURN. A fact whose parsed correlation
-- equals its parent incident's OWN correlation_id contributes nothing — the
-- incident arm already returns that request, and appendFactIfNew() always
-- writes a fact for the opening request too, so without this exclusion every
-- opening request would appear as two links in its own trace. It is also what
-- keeps ops/program3-trace-gate.py's exact-set assertion
-- (set(kinds) == {deployment, check, job, incident}) true for a fixture
-- incident that grows a fact for its own correlation later.
--
-- NO INDEX, SAID OUT LOUD (rule 590b11e1's cousin: name what you left out).
-- The only index that could serve this arm is an expression index over the
-- same substring(...)::uuid the view computes, and it is used ONLY while the
-- two expressions match textually — a silent, unannounced regression the day
-- somebody rewrites one of them. ops.incident_fact grows only when something
-- fails, dedupes to one row per distinct correlation id per incident, and is
-- read by a human typing one trace command. A sequential scan over that is the
-- honest trade, and it is reversible in a later migration if the table ever
-- stops being small.

begin;

-- ── the view, replaced whole ─────────────────────────────────────────────────
-- create or replace view cannot append an arm in place, so 0115's four arms are
-- reproduced VERBATIM below and the fifth is added after the incident arm it
-- belongs beside. Column names, types and order are unchanged; every arm still
-- answers 0115's six questions about itself (what kind of link, what it refers
-- to, what state it reached, when it happened, where the state came from, how
-- old that is), which is the condition 0115's own header sets for belonging in
-- this view at all.
create or replace view ops.v_trace as
  select
    d.correlation_id,
    'deployment'::text                                    as kind,
    coalesce(d.release_ref, left(d.git_sha, 12), d.id::text) as ref,
    d.state,
    coalesce(d.ended_at, d.started_at, d.observed_at)      as occurred_at,
    d.environment,
    s.key                                                  as service_key,
    d.failure_class,
    d.detail,
    d.source_kind,
    d.source_ref,
    d.observed_at,
    d.expires_at,
    ops.freshness(d.observed_at, d.expires_at)             as freshness_state,
    d.id                                                   as row_id
  from ops.deployment d
  join ops.service s on s.id = d.service_id

  union all

  select
    r.correlation_id,
    r.kind,
    r.run_key,
    r.state,
    coalesce(r.ended_at, r.started_at, r.observed_at),
    r.environment,
    s.key,
    r.failure_class,
    r.detail,
    r.source_kind,
    r.source_ref,
    r.observed_at,
    r.expires_at,
    ops.freshness(r.observed_at, r.expires_at),
    r.id
  from ops.run r
  join ops.service s on s.id = r.service_id

  union all

  select
    i.correlation_id,
    'incident'::text,
    i.ref,
    i.state,
    i.detected_at,
    i.environment,
    null::text,
    null::text,
    i.title,
    i.source_kind,
    i.source_ref,
    i.observed_at,
    i.expires_at,
    ops.freshness(i.observed_at, i.expires_at),
    i.id
  from ops.incident i

  union all

  -- ── THE RECURRENCE ARM (0123) ────────────────────────────────────────────
  -- One row per ops.incident_fact whose source_ref names a correlation id, so
  -- the second and later failures of an already-open incident are findable by
  -- their own ids rather than only by the id of the first one.
  --
  -- THE CAST CANNOT THROW, AND THAT IS THE POINT. substring() returns NULL
  -- when the regex does not match, and NULL::uuid is not an error — so a fact
  -- carrying any other source_ref (a runbook path, a log line, a ref written
  -- by tools/ops-record.py's own collector, or a malformed 'correlation:oops')
  -- yields a NULL correlation and is dropped by the where clause instead of
  -- failing the whole view for every reader. A bare `::uuid` over an
  -- unvalidated string would make one bad fact row break every trace.
  --
  -- The hex classes are case-insensitive because uuid parsing is: an uppercase
  -- id casts fine and normalises to lowercase, so accepting it here means the
  -- arm matches what the CAST would have accepted anyway.
  select
    f.correlation_id,
    'incident_fact'::text,
    i.ref,
    i.state,
    -- The fact's OWN time, not the incident's detected_at: this link happened
    -- when this request failed, and cmd_trace orders the chain by it.
    f.recorded_at,
    i.environment,
    null::text,
    null::text,
    f.text,
    -- The parent incident's provenance, matching the incident arm exactly. The
    -- fact's own source_ref is 'correlation:<the id the reader just typed>',
    -- which tells a human nothing they do not already have; the incident's
    -- source_kind/source_ref name the recorder that wrote both rows.
    i.source_kind,
    i.source_ref,
    f.recorded_at,
    -- Freshness is the INCIDENT's window (the only expiry anything stores) read
    -- against THIS fact's time. An incident whose observation has aged out
    -- reads stale on every one of its links, which is the honest answer.
    i.expires_at,
    ops.freshness(f.recorded_at, i.expires_at),
    f.id
  from (
    select
      incident_fact.id,
      incident_fact.incident_id,
      incident_fact.text,
      incident_fact.recorded_at,
      substring(incident_fact.source_ref from
        '^correlation:([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})$'
      )::uuid as correlation_id
    from ops.incident_fact
  ) f
  join ops.incident i on i.id = f.incident_id
  where f.correlation_id is not null
    and f.correlation_id is distinct from i.correlation_id

  union all

  select
    w.correlation_id,
    'work_request'::text,
    w.ref,
    w.state,
    w.captured_at,
    null::text,
    null::text,
    w.blocker_code,
    w.title,
    'registry'::text,
    'ops.work_request'::text,
    w.updated_at,
    null::timestamptz,
    ops.freshness(w.updated_at, null),
    w.id
  from ops.work_request w
  where w.correlation_id is not null;

comment on view ops.v_trace is
  'THE PROGRAM 3 GATE. One correlation id returns the whole journey — deploy, '
  'golden-workflow check, job run, incident, RECURRENCE of an already-open '
  'incident, work request — in time order, every link carrying its own source '
  'and freshness. Read-only by construction: a view over the ops tables with no '
  'insert rule. The recurrence arm (0123) reads ops.incident_fact.source_ref, '
  'where mcp-server/src/trace.js stores the correlation id of every failure '
  'after the first one that opened the incident.';

commit;

-- ── proof, in the same run ───────────────────────────────────────────────────
-- The defect this file closes was a MISSING READ, so the proof exercises reads
-- against rows shaped exactly the way mcp-server/src/trace.js writes them: an
-- incident opened by one request, a fact for that same opening request, a fact
-- for a later recurrence, and the fact shapes that must NOT become trace links.
-- Everything is rolled back by deleting the incident (ops.incident_fact
-- cascades), so this leaves no ops rows behind.
do $$
declare
  v_incident    uuid;
  v_open_corr   uuid := gen_random_uuid();   -- the request that OPENED the incident
  v_recur_corr  uuid := gen_random_uuid();   -- a later request, same signature
  v_upper_corr  uuid := gen_random_uuid();   -- a recurrence written in uppercase hex
  v_fact_open   uuid;
  v_fact_recur  uuid;
  v_fact_plain  uuid;
  v_fact_broken uuid;
  n             int;
  v_kind        text;
  v_ref         text;
  v_state       text;
  v_env         text;
  v_detail      text;
  v_skind       text;
  v_sref        text;
  v_fresh       text;
  v_rowid       uuid;
begin
  insert into ops.incident (ref, correlation_id, title, severity, state, environment,
                            detected_source, source_kind, source_ref, signature,
                            detected_at, observed_at, expires_at)
    values ('INC-0123-PROOF', v_open_corr, '/mcp failed on carr-mcp (local)', 'SEV-3',
            'detected', 'local', 'carr-mcp-worker', 'collector',
            'mcp-server/src/trace.js', 'carr-mcp|local|/mcp|http_5xx',
            now() - interval '10 minutes', now(), now() + interval '24 hours')
    returning id into v_incident;

  -- The four fact shapes that can land on an incident. Only two of them are a
  -- trace link, and only one of those is new in this migration.
  insert into ops.incident_fact (incident_id, text, source_ref)
    values (v_incident, '/mcp failed (http_5xx), correlation ' || v_open_corr,
            'correlation:' || v_open_corr)
    returning id into v_fact_open;
  insert into ops.incident_fact (incident_id, text, source_ref)
    values (v_incident, '/mcp failed (http_5xx), correlation ' || v_recur_corr,
            'correlation:' || v_recur_corr)
    returning id into v_fact_recur;
  insert into ops.incident_fact (incident_id, text, source_ref)
    values (v_incident, 'the writer credential was rotated at 14:02',
            'out/nightly.log')
    returning id into v_fact_plain;
  insert into ops.incident_fact (incident_id, text, source_ref)
    values (v_incident, 'a source_ref that only looks like a correlation',
            'correlation:not-a-uuid')
    returning id into v_fact_broken;
  insert into ops.incident_fact (incident_id, text, source_ref)
    values (v_incident, 'a recurrence recorded in uppercase hex',
            'correlation:' || upper(v_upper_corr::text));

  -- 1. THE DEFECT ITSELF: a recurrence is findable by its own correlation id.
  --    Before this migration this select returned zero rows.
  select count(*) into n from ops.v_trace where correlation_id = v_recur_corr;
  if n <> 1 then
    raise exception '0123 FAILED: a recurring failure traced to % link(s) by its own correlation id, expected exactly 1', n;
  end if;

  select kind, ref, state, environment, detail, source_kind, source_ref, freshness_state, row_id
    into v_kind, v_ref, v_state, v_env, v_detail, v_skind, v_sref, v_fresh, v_rowid
    from ops.v_trace where correlation_id = v_recur_corr;
  if v_kind <> 'incident_fact' then
    raise exception '0123 FAILED: the recurrence link named itself % rather than incident_fact', v_kind;
  end if;
  if v_rowid <> v_fact_recur then
    raise exception '0123 FAILED: the recurrence link did not carry its own fact row id';
  end if;
  -- Every link must answer 0115's six questions — the condition for being in
  -- this view — and ops/program3-trace-gate.py asserts exactly these four are
  -- non-null on every row of a chain.
  if v_ref <> 'INC-0123-PROOF' or v_state <> 'detected' or v_env <> 'local' then
    raise exception '0123 FAILED: the recurrence link did not inherit its parent incident''s ref/state/environment (got %, %, %)', v_ref, v_state, v_env;
  end if;
  if v_skind is null or v_sref is null or v_fresh is null or v_detail is null then
    raise exception '0123 FAILED: a recurrence link is missing source_kind, source_ref, freshness or detail — 0115''s own condition for belonging in this view';
  end if;
  if v_fresh <> 'fresh' then
    raise exception '0123 FAILED: a recurrence of an incident observed just now read % rather than fresh', v_fresh;
  end if;

  -- 2. NO DOUBLE LINK. appendFactIfNew() writes a fact for the OPENING request
  --    too; the incident arm already returns that request. Exactly one link.
  select count(*) into n from ops.v_trace where correlation_id = v_open_corr;
  if n <> 1 then
    raise exception '0123 FAILED: the opening request traced to % links, expected 1 — the incident arm and the recurrence arm are double-counting it', n;
  end if;
  select kind into v_kind from ops.v_trace where correlation_id = v_open_corr;
  if v_kind <> 'incident' then
    raise exception '0123 FAILED: the opening request should still trace as the incident itself, got %', v_kind;
  end if;

  -- 3. A NON-CORRELATION FACT IS NOT A TRACE LINK, and a malformed one neither
  --    appears NOR breaks the view for every other reader — the whole reason
  --    the cast sits behind a matching substring rather than in front of it.
  select count(*) into n from ops.v_trace where row_id in (v_fact_plain, v_fact_broken);
  if n <> 0 then
    raise exception '0123 FAILED: % fact(s) with no correlation id in source_ref became trace links', n;
  end if;

  -- 4. Uppercase hex is accepted and normalised, because ::uuid would have
  --    accepted it — the arm must not be stricter than the cast it guards.
  select count(*) into n from ops.v_trace where correlation_id = v_upper_corr;
  if n <> 1 then
    raise exception '0123 FAILED: an uppercase-hex correlation traced to % link(s), expected 1', n;
  end if;

  -- 5. FRESHNESS IS THE INCIDENT'S WINDOW. Age the incident out and every one
  --    of its links, recurrences included, must stop reading fresh.
  update ops.incident set expires_at = now() - interval '1 hour' where id = v_incident;
  select freshness_state into v_fresh from ops.v_trace where correlation_id = v_recur_corr;
  if v_fresh <> 'stale' then
    raise exception '0123 FAILED: a recurrence of an aged-out incident read % rather than stale', v_fresh;
  end if;

  -- 6. THE READERS KEEP THEIR ACCESS. create or replace view preserves ACLs,
  --    but "preserves" is a claim, and a claim about a grant is worth nothing
  --    until something has asked the database (0122's own posture).
  if not has_table_privilege('carr_reader', 'ops.v_trace', 'select') then
    raise exception '0123 FAILED: carr_reader lost select on ops.v_trace — the Control Room reader cannot trace anything';
  end if;
  if not has_table_privilege('carr_writer', 'ops.v_trace', 'select') then
    raise exception '0123 FAILED: carr_writer lost select on ops.v_trace';
  end if;

  delete from ops.incident where id = v_incident;   -- facts cascade

  raise notice '0123: a RECURRING Worker failure now traces by its own correlation id '
               '(kind incident_fact, carrying its parent incident''s ref/state/environment/'
               'source and the incident''s own freshness window); the opening request still '
               'traces as exactly one link, not two; facts whose source_ref is not a '
               'correlation id are neither returned nor able to break the view';
end $$;
