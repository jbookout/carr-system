-- 0287_incident_fingerprint_and_success_clears.sql
-- THE INCIDENT LEDGER LEARNS TO COUNT, AND TO LET A GREEN RUN SPEAK.
--
-- Process-audit council, 2026-08-23, recommendation 3 — marked SAFE by both
-- chairs, payoff high, confidence high. The measurement it rests on, taken
-- against this database the same day:
--
--   26 incidents open. Five of them — partner-ping, rules-refresh,
--   run-spool-flush, room-bridge and doc-engine's liveness probe — had TWELVE
--   consecutive green runs behind them and were still open. Nothing was wrong
--   with any of them and nothing in the system could say so.
--
--   nightly.vault-drift-watch was open twice at once, as exit_2 and as
--   exit_69. nightly.portability-mirror had failed as exit_1, exit_2 and
--   exit_69 across four days. One job, one remedy, three fingerprints.
--
--   Every open row read alike. partner-ping's 89 failures and a verb that
--   threw once were the same single line in the nightly reprint.
--
-- WHY THE EXISTING DEDUPE WAS NOT THE PROBLEM. 0116 already made
-- service|environment|run_key|failure_class a column and already made two OPEN
-- incidents with one signature impossible, and it works: partner-ping's 89
-- failed runs did collapse into one incident. This migration does not replace
-- that design. It fixes the two things around it — the fourth field arriving in
-- two incompatible registers, and the total absence of a close path any
-- scheduled job could reach.
--
-- ── 1. THE FOURTH FIELD ──────────────────────────────────────────────────────
-- Some callers put a diagnosis there: pubkey_mismatch, keepalive_not_accepting,
-- performance_budget_exceeded. Two of those on one job are genuinely two
-- problems needing two different remedies, and they must stay two rows — that
-- is the council's explicit kill condition and it is preserved exactly.
--
-- bin/nightly.sh and bin/run-scheduled.sh instead pass the wrapper's exit code
-- through as `exit_<n>`, and an exit code is not a diagnosis. exit_1 and exit_2
-- from one step both mean "it returned nonzero", so splitting a row on that
-- number pages a human twice for one job failing one way.
--
-- The normalization is therefore DELIBERATELY NARROW: it rewrites only the
-- `exit_<n>` shape, and even there it keeps every code this codebase has given
-- its own meaning. 69 is "a dependency was unavailable" and 78 is "not
-- provisioned here"; those call for different work than a plain nonzero and are
-- never folded into it. A named class is never touched at all.
--
-- ── 2. THE CLOSE PATH NO JOB COULD REACH ─────────────────────────────────────
-- 0117 gave carr_jobs a column-scoped update on ops.incident and withheld
-- resolved_at and root_cause, so that closing an incident is a human's call
-- enforced in grants rather than in prose. That rule is right and this migration
-- does not weaken it: carr_jobs still holds no grant whatsoever on resolved_at.
--
-- What went wrong is the consequence nobody costed. The only close path in the
-- repo — ops-record.py sweep — needs the owner credential, and bin/nightly.sh
-- runs as carr_jobs. So every night since it shipped the chain has printed
-- `incident sweep (admin capability unavailable)` and swept nothing, while every
-- incident it opened carried the line "watch until 24h clear, then close with an
-- outcome". The watch was never performed by anyone. That is how five recovered
-- services stayed on a human's queue for four days.
--
-- The second half of the same problem: MONITORING_HOURS asks for 24 hours with
-- NO failure recorded. partner-ping runs every 120 seconds. One bad minute
-- anywhere in a day resets that window, so a ticker that flaps hourly and is
-- perfectly healthy in between can never clear on the clock. The council asked
-- for a success SEQUENCE instead — three consecutive healthy runs — which a
-- genuinely broken job never satisfies at all.
--
-- THREE ROWS, NOT THREE WAKES, and the difference is worth naming. bin/run-
-- scheduled.sh throttles a SUCCEEDED row to one per 1800s for partner-ping and
-- capture-poll (one per 900s for room-bridge), because recording ~720 healthy
-- fires a day is noise. So partner-ping clears roughly 90 minutes after it
-- recovers rather than in the six the raw cadence suggests. Failures are never
-- throttled, so this delays only the all-clear, never the page.
--
-- SO THE GATE MOVES INTO THE DATABASE INSTEAD OF THE GRANT. ops.clear_recovered_
-- incident is SECURITY DEFINER: carr_jobs may EXECUTE it and still cannot write
-- resolved_at by any other route. The function re-derives the success sequence
-- from ops.run itself rather than believing its caller, and refuses SEV-1,
-- refuses anything a human opened, and refuses anything whose evidence does not
-- hold up. A wrong number in tools/ops-record.py cannot close anything, and the
-- SEV-1 path the council called "not hygiene — the work" is untouched.
--
-- NO NEW SCHEDULED JOB, said out loud (the council rejected a 21st launchd entry
-- for a fleet that already fails daily, and rejected an agent sweep as spending
-- cognition on state). The function is called from tools/ops-record.py's
-- existing `run` writer on a success and from the nightly `assess` pass that
-- already runs. Both were already executing on every one of these rows.

begin;

-- ── the counters ─────────────────────────────────────────────────────────────
-- WHY THESE THREE AND NOT A HISTORY TABLE. ops.incident_link already holds one
-- row per piece of evidence and ops.incident_fact already holds the story, so
-- the occurrence history exists and is queryable. What did not exist was the
-- summary a human reads in a list without joining anything: is this thing
-- happening constantly or did it happen once. Three columns answer that; a
-- fourth table would only restate ops.incident_link.
alter table ops.incident
  add column if not exists occurrence_count integer not null default 1,
  add column if not exists first_seen_at timestamptz,
  add column if not exists last_seen_at timestamptz;

comment on column ops.incident.occurrence_count is
  'How many distinct evidence rows have been attached to this fingerprint while '
  'it stayed open. Counted off ops.incident_link, whose primary key already '
  'refuses a second link to the same run — so a spool replay that lands the same '
  'row twice cannot inflate it.';
comment on column ops.incident.first_seen_at is
  'When this fingerprint first failed. Equals detected_at for everything the '
  'collector opens; kept separate because a hand-opened incident is detected '
  'when a human notices it, not when it started.';
comment on column ops.incident.last_seen_at is
  'The most recent failure of this fingerprint. With occurrence_count this is '
  'the difference between a fire and a blip, which no open row could show before.';

alter table ops.incident
  add constraint incident_occurrence_count_positive
  check (occurrence_count >= 1) not valid;
alter table ops.incident validate constraint incident_occurrence_count_positive;

-- Backfill from the evidence that was already there. An incident with three
-- linked runs has occurred three times whether or not anything was counting.
update ops.incident i
   set occurrence_count = greatest(1, (
         select count(*) from ops.incident_link l where l.incident_id = i.id)),
       first_seen_at = coalesce(i.first_seen_at, i.detected_at),
       last_seen_at  = coalesce(i.last_seen_at, greatest(i.detected_at, coalesce((
         select max(r.observed_at)
           from ops.incident_link l join ops.run r on r.id::text = l.ref
          where l.incident_id = i.id and l.kind = 'run'), i.detected_at)))
 where i.first_seen_at is null or i.last_seen_at is null;

-- ── the normalization, applied once to what is already open ──────────────────
-- THIS FUNCTION IS A ONE-TIME BACKFILL, NOT A SECOND IMPLEMENTATION, and it is
-- dropped at the bottom of this file so it cannot become one.
-- tools/ops-record.py:normalize_failure_class is where the rule lives; this
-- exists only to reach rows the writer will never touch again. The exit-code
-- pairs below are the same data as its NAMED_EXIT_CLASSES, and
-- ops/incident-fingerprint-selftest.py parses THIS FILE and fails if the two
-- ever drift — which is the guard that makes writing them twice safe.
create function pg_temp.normalize_signature_0287(sig text) returns text
language sql immutable as $norm$
  select split_part(sig, '|', 1) || '|'
      || split_part(sig, '|', 2) || '|'
      || split_part(sig, '|', 3) || '|'
      || case
           when cls = '' then 'unclassified'
           when cls !~* '^exit[_-]?[0-9]{1,3}$' then cls
           else coalesce(
                  (select m.class
                     from (values (64,  'usage'),
                                  (69,  'dependency_unavailable'),
                                  (77,  'permission_denied'),
                                  (78,  'configuration'),
                                  (124, 'timed_out'),
                                  (137, 'killed'),
                                  (143, 'terminated')) as m(code, class)
                    where m.code = substring(cls from '([0-9]{1,3})$')::int),
                  'exit_status')
         end
    from (select substring(sig from '^[^|]*\|[^|]*\|[^|]*\|(.*)$') as cls) _
$norm$;

-- A COLLISION TO PROVE THE MERGE ON, seeded here and asserted below. The three
-- statements that follow are the riskiest thing in this file — they resolve
-- rows a human is looking at — and on the live ledger of 2026-08-23 they have
-- NOTHING to do: vault-drift-watch's exit_2 and exit_69 stay two incidents by
-- design, so nothing currently open actually collides. Untested code that edits
-- production incidents is not something to ship on the argument that it will
-- probably never fire. These two rows make it fire, through the real statements,
-- against a real index, and the block after the backfill checks what happened
-- and removes them.
insert into ops.incident (ref, title, severity, state, environment,
                          detected_source, source_kind, source_ref, signature,
                          detected_at)
values ('INC-0287-COLLIDE-1', 'collision proof (older)', 'SEV-3', 'detected',
        'local', 'proof', 'collector', 'proof',
        'migration-0287-collide|local|proof.job|exit_1', now() - interval '2 hours'),
       ('INC-0287-COLLIDE-2', 'collision proof (newer)', 'SEV-3', 'detected',
        'local', 'proof', 'collector', 'proof',
        'migration-0287-collide|local|proof.job|exit_2', now() - interval '1 hour');

-- COLLISIONS CLOSE AS DUPLICATES AND SAY SO IN THE FIRST WORDS (rule 7105955b).
-- Two open rows that normalize to one fingerprint were always one problem; the
-- older one is the incident and the younger one is a page nobody should have
-- received. It is resolved rather than deleted, because deleting evidence to
-- tidy a queue is the failure mode this whole ledger exists to prevent, and the
-- survivor gains a fact pointing at it so the trace still reaches both.
insert into ops.incident_fact (incident_id, text, source_ref)
select keep.id,
       'duplicate ' || dup.ref || ' folded in here by migration 0287: the same '
         || 'job and the same failure, split only by a raw exit code',
       'migration:0287'
  from (select n.id, n.ref, n.detected_at,
               first_value(n.id) over w as keep_id,
               row_number() over w as rn
          from (select x.id, x.ref, x.detected_at,
                       pg_temp.normalize_signature_0287(x.signature) as new_signature
                  from ops.incident x
                 where x.signature is not null
                   and x.state not in ('resolved', 'reviewed')) n
        window w as (partition by n.new_signature order by n.detected_at, n.ref)) dup
  join ops.incident keep on keep.id = dup.keep_id
 where dup.rn > 1;

update ops.incident i
   set state = 'resolved',
       resolved_at = now(),
       monitoring_until = coalesce(i.monitoring_until, now()),
       recovery_evidence_ref = coalesce(i.recovery_evidence_ref,
                                        'ops.incident:' || keep.ref),
       root_cause = 'duplicate of ' || keep.ref || ' — the same job failing the '
                    || 'same way, split into two open rows only by a raw exit '
                    || 'code. Folded by migration 0287; the evidence stays here '
                    || 'and the surviving incident carries a fact pointing at it.',
       next_action = 'nothing — read ' || keep.ref
  from (select n.id, n.ref, n.detected_at,
               first_value(n.id) over w as keep_id,
               row_number() over w as rn
          from (select x.id, x.ref, x.detected_at,
                       pg_temp.normalize_signature_0287(x.signature) as new_signature
                  from ops.incident x
                 where x.signature is not null
                   and x.state not in ('resolved', 'reviewed')) n
        window w as (partition by n.new_signature order by n.detected_at, n.ref)) dup
  join ops.incident keep on keep.id = dup.keep_id
 where i.id = dup.id and dup.rn > 1;

-- Now the survivors can take the normalized fingerprint without tripping
-- 0116's partial unique index, because every row that would have collided is
-- no longer open.
update ops.incident i
   set signature = pg_temp.normalize_signature_0287(i.signature)
 where i.signature is not null
   and i.state not in ('resolved', 'reviewed')
   and i.signature is distinct from pg_temp.normalize_signature_0287(i.signature);

drop function pg_temp.normalize_signature_0287(text);

-- ── what the merge just did to the seeded collision ──────────────────────────
do $$
declare
  v_keep_state text;
  v_keep_sig   text;
  v_dup_state  text;
  v_dup_cause  text;
  v_facts      int;
begin
  select state, signature into v_keep_state, v_keep_sig
    from ops.incident where ref = 'INC-0287-COLLIDE-1';
  select state, root_cause into v_dup_state, v_dup_cause
    from ops.incident where ref = 'INC-0287-COLLIDE-2';

  if v_keep_state is distinct from 'detected' then
    raise exception '0287 FAILED: the merge closed the OLDER row (%), which is '
                    'the incident, not the duplicate', v_keep_state;
  end if;
  if v_keep_sig is distinct from 'migration-0287-collide|local|proof.job|exit_status' then
    raise exception '0287 FAILED: the surviving row did not take the normalized '
                    'fingerprint, reads %', v_keep_sig;
  end if;
  if v_dup_state is distinct from 'resolved' then
    raise exception '0287 FAILED: the duplicate row is still open (%)', v_dup_state;
  end if;
  -- Close-as-duplicate must say so in the FIRST words, so a reader scanning a
  -- list of closed incidents is never left guessing why this one shut.
  if v_dup_cause is null or left(v_dup_cause, 12) <> 'duplicate of' then
    raise exception '0287 FAILED: a close-as-duplicate did not say so first: %',
                    coalesce(v_dup_cause, '(null)');
  end if;
  select count(*) into v_facts from ops.incident_fact f
    join ops.incident i on i.id = f.incident_id
   where i.ref = 'INC-0287-COLLIDE-1' and f.source_ref = 'migration:0287';
  if v_facts <> 1 then
    raise exception '0287 FAILED: the survivor should carry exactly one fact '
                    'pointing at the folded duplicate, has %', v_facts;
  end if;

  delete from ops.incident where ref like 'INC-0287-COLLIDE-%';
  raise notice '0287: a collision folds into the older row, which keeps the '
               'normalized fingerprint and a fact naming the duplicate; the '
               'younger row closes as a duplicate and says so first';
end $$;

comment on column ops.incident.signature is
  'service|environment|operation|failure-class — the deterministic fingerprint of '
  'one problem. Two OPEN incidents cannot share one (0116''s partial unique '
  'index). Since 0287 the failure class is normalized before it lands here: a '
  'named diagnosis is never rewritten, and only the bare exit_<n> shape collapses '
  '— and even then 69, 78 and the other codes this codebase gives a meaning keep '
  'their own class. tools/ops-record.py:normalize_failure_class is the rule.';

-- ── the one automatic close, gated by the database ───────────────────────────
create or replace function ops.clear_recovered_incident(p_ref text, p_required int)
returns boolean
language plpgsql
security definer
set search_path = pg_catalog, public
as $fn$
declare
  v_id           uuid;
  v_state        text;
  v_severity     text;
  v_source_kind  text;
  v_signature    text;
  v_environment  text;
  v_service      text;
  v_env          text;
  v_run_key      text;
  v_required     int;
  v_total        int;
  v_green        int;
  v_latest       uuid;
begin
  -- THE FLOOR IS THE FUNCTION'S, NOT THE CALLER'S. A caller that asks for a
  -- shorter sequence than the system's definition of recovered gets the
  -- system's definition. Asking for a longer one is allowed: that is a caller
  -- being more careful, which never needs preventing.
  v_required := greatest(coalesce(p_required, 3), 3);

  select i.id, i.state, i.severity, i.source_kind, i.signature, i.environment
    into v_id, v_state, v_severity, v_source_kind, v_signature, v_environment
    from ops.incident i where i.ref = p_ref for update;
  if not found then return false; end if;

  -- Everything below is a REFUSAL, and each one is the reason a job role may
  -- hold EXECUTE on a function that writes resolved_at.
  if v_state in ('resolved', 'reviewed') then return false; end if;

  -- SEV-1 (and any SEV-0) NEVER CLOSES HERE. The council was explicit that the
  -- critical incidents are the work, not hygiene, and 0117 made human closure a
  -- grant rather than a convention. This keeps that true through the one door
  -- the grant no longer covers.
  if v_severity !~ '^SEV-[2-4]$' then return false; end if;

  -- Only what the machine opened. A human who writes an incident by hand is
  -- describing something the run ledger cannot see, so the run ledger has no
  -- standing to declare it over.
  if v_source_kind <> 'collector' then return false; end if;
  if v_signature is null then return false; end if;

  v_service := split_part(v_signature, '|', 1);
  v_env     := split_part(v_signature, '|', 2);
  v_run_key := split_part(v_signature, '|', 3);
  if v_service = '' or v_env = '' or v_run_key = '' then return false; end if;
  if v_env is distinct from v_environment then return false; end if;

  -- RE-DERIVED, NOT BELIEVED. The caller passes no evidence and no count; the
  -- sequence is read here, from the same ledger a human would read. skipped and
  -- cancelled runs are excluded rather than counted either way — a step that was
  -- gated out is not proof of health and not proof of breakage.
  with recent as (
    select r.id, r.state, r.observed_at
      from ops.run r join ops.service s on s.id = r.service_id
     where s.key = v_service
       and r.environment = v_env
       and r.run_key = v_run_key
       and r.state in ('succeeded', 'failed', 'timed_out')
     order by r.observed_at desc, r.id desc
     limit v_required)
  select count(*), count(*) filter (where recent.state = 'succeeded'),
         (select r2.id from recent r2 order by r2.observed_at desc, r2.id desc limit 1)
    into v_total, v_green, v_latest
    from recent;

  -- A job with fewer than v_required terminal runs on record has not yet
  -- demonstrated the sequence, however green the ones it has are.
  if v_total < v_required or v_green < v_required then return false; end if;

  update ops.incident i
     set state = 'resolved',
         resolved_at = now(),
         monitoring_until = coalesce(i.monitoring_until, now()),
         recovery_evidence_ref = 'ops.run:' || v_latest::text,
         root_cause = format(
           'recovered: %s has run green %s consecutive times in %s, which is the '
           'success sequence this system calls recovered. Closed automatically by '
           'ops.clear_recovered_incident with those runs as the evidence.',
           v_run_key, v_required, v_env),
         next_action = 'review and record a followup disposition',
         last_seen_at = i.last_seen_at,
         observed_at = now()
   where i.id = v_id;

  insert into ops.incident_fact (incident_id, text, source_ref)
  values (v_id,
          format('%s consecutive healthy runs of %s (%s); latest green run %s',
                 v_required, v_run_key, v_env, v_latest),
          'ops.clear_recovered_incident');

  return true;
end;
$fn$;

comment on function ops.clear_recovered_incident(text, int) is
  'The only path by which a scheduled job may close an incident. SECURITY '
  'DEFINER because 0117 withholds resolved_at from carr_jobs and that grant '
  'stands: the role gains one function that re-derives the success sequence from '
  'ops.run and refuses SEV-1, refuses hand-opened incidents, and refuses '
  'anything whose evidence does not hold. Returns false rather than raising, so '
  'a refusal never costs the caller its run row.';

-- ── grants ───────────────────────────────────────────────────────────────────
-- The counters join the column-scoped grant 0117 established. resolved_at and
-- root_cause DELIBERATELY DO NOT — the whole point of the function above is that
-- they never need to.
grant insert (occurrence_count, first_seen_at, last_seen_at)
  on ops.incident to carr_jobs;
grant update (occurrence_count, first_seen_at, last_seen_at)
  on ops.incident to carr_jobs;

revoke all on function ops.clear_recovered_incident(text, int) from public;
grant execute on function ops.clear_recovered_incident(text, int) to carr_jobs;

commit;

-- ── proof, in the same run ───────────────────────────────────────────────────
do $$
declare
  v_service uuid;
  v_inc     uuid;
  v_run     uuid;
  v_ok      boolean;
  v_t       timestamptz;
  i         int;
begin
  insert into ops.service (key, name, family, criticality, owner_actor)
    values ('migration-0287-proof', 'proof', 'Data', 'medium', 'system')
    returning id into v_service;

  -- 1. AN INCIDENT WITH NO SUCCESS SEQUENCE DOES NOT CLOSE.
  insert into ops.incident (ref, title, severity, state, environment,
                            detected_source, source_kind, source_ref, signature)
    values ('INC-0287-PROOF-1', 'proof', 'SEV-3', 'detected', 'local',
            'proof', 'collector', 'proof',
            'migration-0287-proof|local|proof.job|exit_status')
    returning id into v_inc;
  select ops.clear_recovered_incident('INC-0287-PROOF-1', 3) into v_ok;
  if v_ok then
    raise exception '0287 FAILED: closed an incident with no green runs at all';
  end if;

  -- EVERY PROOF RUN CARRIES AN EXPLICIT observed_at, ONE MINUTE APART. now() is
  -- transaction start time, so a DO block that inserts four runs gives all four
  -- the same instant, and "the latest run" then falls through to the uuid
  -- tiebreak — which made cases 3 and 4 below pass or fail by luck. A sequence
  -- test has to be able to state the sequence.
  v_t := now() - interval '1 hour';

  -- 2. TWO GREEN RUNS ARE NOT THREE.
  for i in 1..2 loop
    v_t := v_t + interval '1 minute';
    insert into ops.run (kind, service_id, environment, run_key, state,
                         started_at, ended_at, observed_at, source_kind, source_ref)
      values ('job', v_service, 'local', 'proof.job', 'succeeded',
              v_t, v_t, v_t, 'operator', 'migration-0287-proof');
  end loop;
  select ops.clear_recovered_incident('INC-0287-PROOF-1', 3) into v_ok;
  if v_ok then
    raise exception '0287 FAILED: two healthy runs satisfied a three-run sequence';
  end if;

  -- 3. THE THIRD GREEN RUN CLOSES IT, WITH THAT RUN AS THE EVIDENCE.
  v_t := v_t + interval '1 minute';
  insert into ops.run (kind, service_id, environment, run_key, state,
                       started_at, ended_at, observed_at, source_kind, source_ref)
    values ('job', v_service, 'local', 'proof.job', 'succeeded',
            v_t, v_t, v_t, 'operator', 'migration-0287-proof')
    returning id into v_run;
  select ops.clear_recovered_incident('INC-0287-PROOF-1', 3) into v_ok;
  if not v_ok then
    raise exception '0287 FAILED: three consecutive healthy runs did not close it';
  end if;
  if not exists (select 1 from ops.incident
                  where ref = 'INC-0287-PROOF-1' and state = 'resolved'
                    and resolved_at is not null
                    and recovery_evidence_ref = 'ops.run:' || v_run::text) then
    raise exception '0287 FAILED: the close did not carry the green run as evidence';
  end if;

  -- 4. A FAILURE INSIDE THE SEQUENCE BREAKS IT, however green the runs before
  --    and however green the newest one. This is the flapping ticker: two good
  --    runs, a failure, then a recovery. One green run is not a sequence.
  insert into ops.incident (ref, title, severity, state, environment,
                            detected_source, source_kind, source_ref, signature)
    values ('INC-0287-PROOF-2', 'proof', 'SEV-3', 'detected', 'local',
            'proof', 'collector', 'proof',
            'migration-0287-proof|local|proof.flaky|exit_status');
  v_t := now() - interval '1 hour';
  for i in 1..2 loop
    v_t := v_t + interval '1 minute';
    insert into ops.run (kind, service_id, environment, run_key, state,
                         started_at, ended_at, observed_at, source_kind, source_ref)
      values ('job', v_service, 'local', 'proof.flaky', 'succeeded',
              v_t, v_t, v_t, 'operator', 'migration-0287-proof');
  end loop;
  v_t := v_t + interval '1 minute';
  insert into ops.run (kind, service_id, environment, run_key, state, failure_class,
                       started_at, ended_at, observed_at, source_kind, source_ref)
    values ('job', v_service, 'local', 'proof.flaky', 'failed', 'exit_1',
            v_t, v_t, v_t, 'operator', 'migration-0287-proof');
  v_t := v_t + interval '1 minute';
  insert into ops.run (kind, service_id, environment, run_key, state,
                       started_at, ended_at, observed_at, source_kind, source_ref)
    values ('job', v_service, 'local', 'proof.flaky', 'succeeded',
            v_t, v_t, v_t, 'operator', 'migration-0287-proof');
  select ops.clear_recovered_incident('INC-0287-PROOF-2', 3) into v_ok;
  if v_ok then
    raise exception '0287 FAILED: a failure inside the window did not break the sequence';
  end if;

  -- 4b. AND A SKIPPED RUN NEITHER BREAKS NOR COMPLETES A SEQUENCE. A nightly
  --     step gated out by a missing capability is not evidence of health and is
  --     not evidence of breakage; counting it either way would let a gate close
  --     an incident, or keep one open forever.
  v_t := v_t + interval '1 minute';
  insert into ops.run (kind, service_id, environment, run_key, state,
                       started_at, ended_at, observed_at, source_kind, source_ref)
    values ('job', v_service, 'local', 'proof.flaky', 'skipped',
            v_t, v_t, v_t, 'operator', 'migration-0287-proof');
  select ops.clear_recovered_incident('INC-0287-PROOF-2', 3) into v_ok;
  if v_ok then
    raise exception '0287 FAILED: a skipped run completed a success sequence';
  end if;
  v_t := v_t + interval '1 minute';
  insert into ops.run (kind, service_id, environment, run_key, state,
                       started_at, ended_at, observed_at, source_kind, source_ref)
    values ('job', v_service, 'local', 'proof.flaky', 'succeeded',
            v_t, v_t, v_t, 'operator', 'migration-0287-proof');
  v_t := v_t + interval '1 minute';
  insert into ops.run (kind, service_id, environment, run_key, state,
                       started_at, ended_at, observed_at, source_kind, source_ref)
    values ('job', v_service, 'local', 'proof.flaky', 'succeeded',
            v_t, v_t, v_t, 'operator', 'migration-0287-proof');
  select ops.clear_recovered_incident('INC-0287-PROOF-2', 3) into v_ok;
  if not v_ok then
    raise exception '0287 FAILED: three green runs either side of a skip did not close it';
  end if;

  -- 5. SEV-1 NEVER CLOSES HERE, however perfect the evidence.
  insert into ops.incident (ref, title, severity, state, environment,
                            detected_source, source_kind, source_ref, signature)
    values ('INC-0287-PROOF-3', 'proof', 'SEV-1', 'detected', 'local',
            'proof', 'collector', 'proof',
            'migration-0287-proof|local|proof.job|dependency_unavailable');
  select ops.clear_recovered_incident('INC-0287-PROOF-3', 3) into v_ok;
  if v_ok then
    raise exception '0287 FAILED: a SEV-1 closed without a human';
  end if;

  -- 6. A HAND-OPENED INCIDENT NEVER CLOSES HERE EITHER.
  insert into ops.incident (ref, title, severity, state, environment,
                            detected_source, source_kind, source_ref, signature)
    values ('INC-0287-PROOF-4', 'proof', 'SEV-3', 'detected', 'local',
            'proof', 'operator', 'proof',
            'migration-0287-proof|local|proof.job|hand_written');
  select ops.clear_recovered_incident('INC-0287-PROOF-4', 3) into v_ok;
  if v_ok then
    raise exception '0287 FAILED: the machine closed an incident a human opened';
  end if;

  -- 7. THE CALLER CANNOT SHORTEN THE SEQUENCE. One green run on a job that has
  --    only ever run once, and a caller asking for a sequence of one.
  insert into ops.incident (ref, title, severity, state, environment,
                            detected_source, source_kind, source_ref, signature)
    values ('INC-0287-PROOF-5', 'proof', 'SEV-3', 'detected', 'local',
            'proof', 'collector', 'proof',
            'migration-0287-proof|local|proof.once|exit_status');
  insert into ops.run (kind, service_id, environment, run_key, state,
                       started_at, ended_at, observed_at, source_kind, source_ref)
    values ('job', v_service, 'local', 'proof.once', 'succeeded',
            now(), now(), now(), 'operator', 'migration-0287-proof');
  select ops.clear_recovered_incident('INC-0287-PROOF-5', 1) into v_ok;
  if v_ok then
    raise exception '0287 FAILED: a caller asking for one run got one run';
  end if;
  select ops.clear_recovered_incident('INC-0287-PROOF-5', 0) into v_ok;
  if v_ok then
    raise exception '0287 FAILED: a caller asking for zero runs got zero runs';
  end if;

  -- 8. DISTINCT NAMED FAILURE CLASSES ON ONE JOB REMAIN TWO OPEN INCIDENTS.
  --    This is the council's kill condition, held as data rather than intent.
  insert into ops.incident (ref, title, severity, state, environment,
                            detected_source, source_kind, source_ref, signature)
    values ('INC-0287-PROOF-6', 'proof', 'SEV-3', 'detected', 'local',
            'proof', 'collector', 'proof',
            'migration-0287-proof|local|restore.key-recovery|pubkey_mismatch'),
           ('INC-0287-PROOF-7', 'proof', 'SEV-3', 'detected', 'local',
            'proof', 'collector', 'proof',
            'migration-0287-proof|local|restore.key-recovery|restore_failed');

  delete from ops.incident where ref like 'INC-0287-PROOF-%';
  delete from ops.run where service_id = v_service;
  delete from ops.service where id = v_service;

  raise notice '0287: three consecutive healthy runs close a SEV-3 job incident with '
               'the green run as evidence; two do not, a failure inside the sequence '
               'does not, a SEV-1 never does, a hand-opened incident never does, the '
               'caller cannot shorten the sequence, and two named failure classes on '
               'one job stay two incidents';
end $$;
