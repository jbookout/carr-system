-- 0117_collector_incident_grants.sql
-- THE COLLECTOR MAY RAISE AN INCIDENT AND MAY NEVER CLOSE ONE, AND THAT IS A
-- DATABASE GUARANTEE RATHER THAN A PROPERTY OF THE WRITER'S CODE.
--
-- FOUND BY RUNNING IT, NOT BY READING IT. 0116 shipped the deduplication index
-- and tools/ops-record.py gained `assess`, and the first run against production
-- died with `permission denied for table incident`. carr_jobs — the role every
-- unattended collector holds — was granted ops.run and ops.deployment in 0115
-- and nothing on the incident tables, because at that point nothing wrote to
-- them. Rule 5409731b names this exact miss: a new writer changes the permission
-- surface of every table it touches, and every one of them has to be
-- grant-checked. This migration is that check, arriving one run late.
--
-- WHY UPDATE IS SCOPED TO SIX COLUMNS. The obvious fix is `grant insert, update
-- on ops.incident to carr_jobs`, and it would be wrong in a way that matters.
-- The collector's whole job is to observe: open an incident when a job fails,
-- and move it to monitoring when the job starts passing again. It has no
-- business rewriting a title, a severity, a signature, or a root cause — and it
-- must never mark anything RESOLVED, because one green run says the symptom
-- stopped, not that the cause is understood.
--
-- Postgres grants UPDATE per column, so that boundary is expressible instead of
-- merely documented. The collector gets exactly the six columns its recovery
-- path writes and not one more.
--
-- THE INTERLOCK, and it is the point of the whole file. `state` IS granted, so
-- nothing stops the collector setting state = 'resolved'. What stops it is
-- 0115's constraint: resolved requires recovery_evidence_ref, monitoring_until
-- AND resolved_at, and resolved_at is NOT in the column grant. The constraint
-- demands a column the role cannot write, so the transition fails on any path.
-- Two mechanisms that were designed separately meet here and produce a rule
-- neither states alone: a machine can report that something recovered, and only
-- a human can declare it resolved. The proof at the bottom exercises exactly
-- that, because an interlock nobody has watched engage is a coincidence.

begin;

grant select on ops.incident, ops.incident_fact,
                ops.incident_hypothesis, ops.incident_link, ops.incident_service
  to carr_jobs;

-- Opening an incident and recording what was observed. Facts carry a source by
-- 0115's NOT NULL; links are how a fact is attached to the run that produced it.
grant insert on ops.incident, ops.incident_fact, ops.incident_link
  to carr_jobs;

-- The recovery path, and nothing else. Deliberately ABSENT: resolved_at,
-- root_cause, reviewed_at, followup_disposition (a human's conclusions),
-- title, severity, signature (the incident's identity), and detected_* (its
-- history).
grant update (state, recovery_evidence_ref, monitoring_until,
              next_action, observed_at, expires_at)
  on ops.incident to carr_jobs;

-- No grant on ops.incident_hypothesis beyond select, on purpose. A machine
-- reading an exit code has no theory about the cause, and the facts/hypotheses
-- split exists so a human looking for evidence never finds a guess sitting in
-- its place. The collector cannot write one even by mistake.

commit;

-- ── proof, in the same run ───────────────────────────────────────────────────
-- EACH HALF OF THE INTERLOCK IS CHECKED BY THE MECHANISM THAT ACTUALLY ENFORCES
-- IT, which is not the same mechanism for both halves.
--
-- The CONSTRAINT half is executed: an update to resolved without resolved_at is
-- attempted for real and must be refused. A constraint nobody has watched refuse
-- anything is a comment with punctuation.
--
-- The GRANT half is read from the catalog with has_column_privilege rather than
-- executed, and that is a deliberate downgrade with a reason. Executing it needs
-- `set role carr_jobs`, and on this Postgres SET ROLE requires more than
-- membership — the first two attempts at this file died on `permission denied to
-- set role`, on a role the migration runner had just granted itself. Granting
-- membership WITH SET to make a test pass would widen the role graph for the
-- length of the migration to prove a narrowing, which is the wrong trade. The
-- catalog is authoritative about what a grant says; the constraint proof below
-- covers what the database does.
do $$
declare
  v_service   uuid;
  v_incident  uuid;
  v_can_state boolean;
  v_can_close boolean;
  v_can_sev   boolean;
  v_can_hypo  boolean;
begin
  insert into ops.service (key, name, family, criticality, owner_actor)
    values ('migration-0117-proof', 'proof', 'Data', 'low', 'system')
    returning id into v_service;

  insert into ops.incident (ref, title, severity, state, environment,
                            detected_source, source_kind, source_ref, signature)
    values ('INC-0117-PROOF', 'proof', 'SEV-3', 'detected', 'local',
            'proof', 'collector', 'proof', 'sig-0117|local|job|boom')
    returning id into v_incident;

  -- 1. THE GRANT BOUNDARY, from the catalog.
  select has_column_privilege('carr_jobs', 'ops.incident', 'state', 'update'),
         has_column_privilege('carr_jobs', 'ops.incident', 'resolved_at', 'update'),
         has_column_privilege('carr_jobs', 'ops.incident', 'severity', 'update'),
         has_table_privilege ('carr_jobs', 'ops.incident_hypothesis', 'insert')
    into v_can_state, v_can_close, v_can_sev, v_can_hypo;

  if not v_can_state then
    raise exception '0117 FAILED: carr_jobs cannot write state, so it cannot report a recovery';
  end if;
  if v_can_close then
    raise exception '0117 FAILED: carr_jobs can write resolved_at — a machine could close an incident';
  end if;
  if v_can_sev then
    raise exception '0117 FAILED: carr_jobs can rewrite severity';
  end if;
  if v_can_hypo then
    raise exception '0117 FAILED: carr_jobs can write a hypothesis';
  end if;

  -- 2. THE CONSTRAINT HALF, executed. Even as the OWNER, with every privilege,
  --    resolved is unreachable without evidence — so the collector losing the
  --    resolved_at grant is belt and braces rather than the only thing standing
  --    between a machine and a closed incident.
  begin
    update ops.incident set state = 'resolved' where id = v_incident;
    raise exception '0117 FAILED: an incident reached resolved with no evidence';
  exception when check_violation then null;
  end;

  -- 3. AND THE RECOVERY PATH THE COLLECTOR ACTUALLY TAKES still works.
  update ops.incident
     set state = 'monitoring',
         recovery_evidence_ref = 'ops.run:proof',
         monitoring_until = now() + interval '24 hours'
   where id = v_incident;

  if not exists (select 1 from ops.incident
                  where id = v_incident and state = 'monitoring') then
    raise exception '0117 FAILED: the monitoring transition did not take';
  end if;

  delete from ops.incident where id = v_incident;
  delete from ops.service where id = v_service;

  raise notice '0117: carr_jobs may report a recovery and may not close, reclassify, '
               'or theorise about an incident; resolved is unreachable without evidence';
end $$;
