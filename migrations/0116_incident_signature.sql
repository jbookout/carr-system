-- 0116_incident_signature.sql
-- DEDUPLICATION BECOMES A DATABASE GUARANTEE, NOT APPLICATION POLITENESS.
--
-- THE DOCTRINE LINE THIS ENFORCES, verbatim from the observability contract:
-- "Repeated identical failures deduplicate into one incident or work item."
--
-- WHY IT CANNOT LIVE IN THE WRITER. ops.incident shipped in 0115 with a
-- lifecycle and honest close constraints, and nothing has ever written to it.
-- The writer being built now (tools/ops-record.py assess) has to answer "is
-- there already an incident for this failure?" on every pass, and the only
-- material it had was the title — so dedupe would have been string matching
-- against prose. Prose changes. The first reworded title silently produces a
-- second incident for the same failure, and nobody notices until the incident
-- list is the thing people have stopped reading.
--
-- So the signature is a COLUMN, and the uniqueness is a PARTIAL UNIQUE INDEX
-- over open incidents only. Two open incidents with the same signature become
-- impossible rather than merely unlikely, and the writer's dedupe logic can be
-- wrong without the data being wrong.
--
-- WHY PARTIAL, AND WHY THAT EXACT PREDICATE. Closed incidents accumulate
-- forever, and the same failure absolutely may recur next month — that is a NEW
-- incident, not a violation. Uniqueness therefore covers only the states where
-- an incident is still live. `monitoring` counts as live on purpose: a service
-- that fails again while we are watching it recover is the same incident
-- continuing, not a second one starting.
--
-- WHAT A SIGNATURE IS: service, environment, run key, failure class. Four
-- things that together mean "the same thing broke the same way in the same
-- place". Not the correlation id — that is per-run and would defeat dedupe
-- entirely. Not the title — see above.

begin;

alter table ops.incident
  add column if not exists signature text;

comment on column ops.incident.signature is
  'service|environment|run_key|failure_class — the identity of a recurring '
  'failure. Two OPEN incidents cannot share one (see the partial unique index). '
  'Null is allowed for incidents a human opens by hand, which have no automatic '
  'recurrence to collapse.';

-- The guarantee. NULLs are excluded by construction: a unique index treats each
-- NULL as distinct, so hand-opened incidents never collide with each other.
create unique index if not exists incident_one_open_per_signature
  on ops.incident (signature)
  where state not in ('resolved', 'reviewed');

comment on index ops.incident_one_open_per_signature is
  'The deduplication rule as a constraint. monitoring counts as OPEN on purpose: '
  'a service that fails again while we are watching it recover is the same '
  'incident continuing, not a second one starting.';

-- Recurrences are FACTS on the existing incident, and a fact carries its source
-- (0115 already requires source_ref NOT NULL). Nothing further is needed for the
-- append path — this index is what forces the writer down it.

commit;

-- ── proof, in the same run ───────────────────────────────────────────────────
do $$
declare
  v_service uuid;
  v_first   uuid;
begin
  insert into ops.service (key, name, family, criticality, owner_actor)
    values ('migration-0116-proof', 'proof', 'Data', 'low', 'system')
    returning id into v_service;

  insert into ops.incident (ref, title, severity, state, environment,
                            detected_source, source_kind, source_ref, signature)
    values ('INC-0116-PROOF-1', 'proof', 'SEV-3', 'detected', 'local',
            'proof', 'collector', 'proof', 'sig|local|job|boom')
    returning id into v_first;

  -- 1. A SECOND OPEN INCIDENT WITH THE SAME SIGNATURE IS REFUSED.
  begin
    insert into ops.incident (ref, title, severity, state, environment,
                              detected_source, source_kind, source_ref, signature)
      values ('INC-0116-PROOF-2', 'proof', 'SEV-3', 'detected', 'local',
              'proof', 'collector', 'proof', 'sig|local|job|boom');
    raise exception '0116 FAILED: a second OPEN incident shared a signature';
  exception when unique_violation then null;
  end;

  -- 2. monitoring STILL COUNTS AS OPEN — the same failure recurring while we
  --    watch a recovery is the same incident, not a new one.
  update ops.incident set state = 'monitoring',
         recovery_evidence_ref = 'proof', monitoring_until = now() + interval '1 hour'
   where id = v_first;
  begin
    insert into ops.incident (ref, title, severity, state, environment,
                              detected_source, source_kind, source_ref, signature)
      values ('INC-0116-PROOF-3', 'proof', 'SEV-3', 'detected', 'local',
              'proof', 'collector', 'proof', 'sig|local|job|boom');
    raise exception '0116 FAILED: monitoring did not count as open';
  exception when unique_violation then null;
  end;

  -- 3. ONCE RESOLVED, THE SAME FAILURE MAY OPEN A GENUINELY NEW INCIDENT.
  update ops.incident set state = 'resolved', resolved_at = now()
   where id = v_first;
  insert into ops.incident (ref, title, severity, state, environment,
                            detected_source, source_kind, source_ref, signature)
    values ('INC-0116-PROOF-4', 'proof', 'SEV-3', 'detected', 'local',
            'proof', 'collector', 'proof', 'sig|local|job|boom');

  -- 4. TWO HAND-OPENED INCIDENTS WITH NO SIGNATURE DO NOT COLLIDE.
  insert into ops.incident (ref, title, severity, state, environment,
                            detected_source, source_kind, source_ref)
    values ('INC-0116-PROOF-5', 'proof', 'SEV-3', 'detected', 'local',
            'proof', 'operator', 'proof'),
           ('INC-0116-PROOF-6', 'proof', 'SEV-3', 'detected', 'local',
            'proof', 'operator', 'proof');

  delete from ops.incident where ref like 'INC-0116-PROOF-%';
  delete from ops.service where id = v_service;

  raise notice '0116: a duplicate open incident is refused, monitoring counts as open, '
               'a resolved signature may recur, and unsignatured incidents never collide';
end $$;
