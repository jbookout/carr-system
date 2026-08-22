-- 0277_wip_limit_survives_reassignment.sql
--
-- 0276 put the council's work-in-progress limit in the row transition, which is
-- the right place. It has one hole, and the author named it as the branch they
-- had reasoned about least before handing the work over:
--
--   if tg_op = 'UPDATE' and old.state in ('claimed','in_progress') then
--     return new;
--   end if;
--
-- The reasoning behind that early return is sound for the SYSTEM-WIDE count — a
-- row already in flight was already counted, so an edit cannot push the total
-- over two. It does not hold for the PER-EXECUTOR count, because reassignment
-- moves a row between executors without changing the total at all.
--
-- THE HOLE, concretely. One executor holds a claimed row; a second executor
-- holds another. The queue is at its system-wide limit of two and each executor
-- is at their limit of one. Now change the first row's executor to the second
-- person. The row is already in flight, so the trigger returns before it counts
-- anything, and that one executor ends up holding two in-flight items — the
-- exact state the per-executor limit exists to refuse. Nothing objects, and the
-- reassignment is the ordinary way work changes hands.
--
-- WHY THIS IS WORTH A MIGRATION RATHER THAN A NOTE. The limit's whole purpose is
-- that it is enforced rather than recited (rule ab814a26: recitation is not
-- enforcement). A limit with a documented bypass is prose again, and the bypass
-- here is not an exotic path — it is `set executor_actor = ...`.
--
-- WHAT THIS CHANGES AND WHAT IT DELIBERATELY DOES NOT.
--   * The system-wide check still runs ONLY on entry into flight. Re-running it
--     on every edit would refuse a title change while the queue is full, which
--     makes a full queue uneditable — 0276 was right about that and its proof
--     asserts it. That assertion is repeated below so this change cannot break
--     the thing the previous one got right.
--   * The per-executor check now also runs when a row ALREADY in flight changes
--     who is responsible for it. If responsibility is unchanged, the trigger
--     still returns early and costs nothing.
--   * The limits themselves are untouched: two system-wide, one per executor,
--     still constants in one place so raising them stays a one-line diff.
--
-- IS DISTINCT FROM, not <>. Either side can be null — a row can be in flight
-- with no executor named — and `null <> null` is null, which is not true, so a
-- plain comparison would treat every null-to-named reassignment as "unchanged"
-- and skip exactly the check this migration exists to add.

begin;

create or replace function ops.enforce_work_in_progress_limit()
returns trigger language plpgsql as $$
declare
  system_wide     int;
  per_executor    int;
  who             text;
  who_before      text;
  already_in_flight boolean;
  limit_system    constant int := 2;
  limit_each      constant int := 1;
begin
  if new.state not in ('claimed','in_progress') then
    return new;
  end if;

  already_in_flight := (tg_op = 'UPDATE' and old.state in ('claimed','in_progress'));

  -- SYSTEM-WIDE: entry only. A row already in flight is already counted, and
  -- re-checking here would make a full queue uneditable.
  if not already_in_flight then
    select count(*) into system_wide
      from ops.work_request
     where state in ('claimed','in_progress')
       and id <> new.id;

    if system_wide + 1 > limit_system then
      raise exception
        'work-in-progress limit: % already in flight system-wide and the limit is %. '
        'Move something to blocked, verification or confirmed_closed before claiming %.',
        system_wide, limit_system, new.ref
        using errcode = 'check_violation';
    end if;
  end if;

  who := coalesce(new.executor_actor, new.owner_actor);

  -- PER-EXECUTOR: on entry, and on any reassignment of a row already in flight.
  -- Reassignment does not move the system-wide total but it does move this one.
  if already_in_flight then
    who_before := coalesce(old.executor_actor, old.owner_actor);
    if who_before is not distinct from who then
      return new;
    end if;
  end if;

  if who is not null then
    select count(*) into per_executor
      from ops.work_request
     where state in ('claimed','in_progress')
       and coalesce(executor_actor, owner_actor) = who
       and id <> new.id;

    if per_executor + 1 > limit_each then
      raise exception
        'work-in-progress limit: % already has % in flight and the limit per executor is %. '
        'One thing at a time is the point; finish or park it before claiming %.',
        who, per_executor, limit_each, new.ref
        using errcode = 'check_violation';
    end if;
  end if;

  return new;
end $$;

comment on function ops.enforce_work_in_progress_limit() is
  'Tune-up council 2026-08-21: work-in-progress limit of 2 system-wide and 1 per '
  'executor, enforced at the row transition because the claim path has more than '
  'one route. Counts claimed and in_progress only; held-but-not-active states are '
  'deliberately excluded so a blocked row cannot freeze the queue. The system-wide '
  'check runs on entry into flight only; the per-executor check ALSO runs when a '
  'row already in flight changes hands, because reassignment moves that count '
  'without moving the total (0277).';

-- PROOF, inside the transaction, in 0276's own style: fixtures enter at
-- 'captured' and transition, because the shape gate refuses a row landing
-- straight in implementation. The sentinel exception rolls everything back, so
-- no fixture row survives.
--
-- The first assertion below FAILS against 0276's function and passes against
-- this one, which is what makes it a proof of the repair rather than a
-- restatement of it. The last two re-assert what 0276 got right, so this change
-- cannot quietly trade one behaviour for the other.
do $$
declare
  probe_a uuid;
  probe_b uuid;
  probe_c uuid;
  refused boolean;
  shaper  uuid;
begin
  select id into shaper from actor where slug='joe' limit 1;
  if shaper is null then
    raise exception '0277 proof needs a joe actor row to attribute the shape decision to';
  end if;

  insert into ops.work_request (ref, state, title, requester_actor, executor_actor,
                              shape_disposition, shape_fixed_surface_ref, shape_rationale,
                              shape_decided_by_actor_id, shape_decided_at)
  values ('WR-999911','captured','reassign probe a','joe','probe-exec-alpha',
          'not_required','probe:wip-reassignment-fixture',
          'rolled-back proof fixture for the work-in-progress reassignment hole',
          shaper, now())
  returning id into probe_a;
  update ops.work_request set state='claimed' where id=probe_a;

  insert into ops.work_request (ref, state, title, requester_actor, executor_actor,
                              shape_disposition, shape_fixed_surface_ref, shape_rationale,
                              shape_decided_by_actor_id, shape_decided_at)
  values ('WR-999912','captured','reassign probe b','joe','probe-exec-beta',
          'not_required','probe:wip-reassignment-fixture',
          'rolled-back proof fixture for the work-in-progress reassignment hole',
          shaper, now())
  returning id into probe_b;
  update ops.work_request set state='claimed' where id=probe_b;

  -- THE HOLE. Both rows are in flight, one each. Handing the first row to the
  -- executor who already holds the second must be REFUSED. Under 0276 this
  -- update succeeded and left one executor holding two.
  begin
    update ops.work_request set executor_actor='probe-exec-beta' where id=probe_a;
    refused := false;
  exception when check_violation then refused := true;
  end;
  if not refused then
    raise exception '0277 FAILED: reassigning an in-flight row onto an executor who already holds one was not refused';
  end if;

  -- A REASSIGNMENT TO SOMEBODY FREE IS STILL FINE. The fix must narrow the
  -- bypass, not forbid handing work over.
  update ops.work_request set executor_actor='probe-exec-gamma' where id=probe_a;

  -- AN ORDINARY EDIT TO AN IN-FLIGHT ROW MUST STILL WORK, with the queue at its
  -- system-wide limit. This is what 0276 got right and a careless fix breaks by
  -- re-running the system-wide count on every update.
  update ops.work_request set title='reassign probe a, edited' where id=probe_a;

  -- AND RESPONSIBILITY UNCHANGED MUST NOT BE RE-CHECKED. Writing the SAME
  -- executor back is a no-op for the limit; refusing it would mean an idempotent
  -- write starts failing once a row is in flight.
  update ops.work_request set executor_actor='probe-exec-gamma' where id=probe_a;

  -- A ROW IN FLIGHT WITH NOBODY NAMED still reassigns into a free executor, and
  -- the null-to-named transition is exactly the case a `<>` comparison would
  -- have skipped.
  insert into ops.work_request (ref, state, title, requester_actor,
                              shape_disposition, shape_fixed_surface_ref, shape_rationale,
                              shape_decided_by_actor_id, shape_decided_at)
  values ('WR-999913','captured','reassign probe c','joe',
          'not_required','probe:wip-reassignment-fixture',
          'rolled-back proof fixture for the work-in-progress reassignment hole',
          shaper, now())
  returning id into probe_c;
  update ops.work_request set state='blocked', blocker_code='other_lane',
         blocker_detail='probe: parked so the system-wide limit has room'
   where id=probe_b;
  update ops.work_request set state='claimed' where id=probe_c;
  begin
    update ops.work_request set executor_actor='probe-exec-gamma' where id=probe_c;
    refused := false;
  exception when check_violation then refused := true;
  end;
  if not refused then
    raise exception '0277 FAILED: an unassigned in-flight row was handed to an executor who already holds one, and was not refused';
  end if;

  raise exception 'CARR_0277_PROOF_OK';
exception when others then
  if sqlerrm <> 'CARR_0277_PROOF_OK' then raise; end if;
end $$;

commit;
