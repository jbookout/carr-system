-- 0276_work_in_progress_limit.sql
--
-- The tune-up council of 2026-08-21 ruled a work-in-progress limit of two
-- system-wide and one per executor, and was specific about where it lives:
-- "enforced in the claim path, not in prose". This is that enforcement.
--
-- WHY IN THE DATABASE. A limit written in a verb is a limit one new caller
-- forgets. The claim path is already more than one route — capability projects
-- claim through start-capability-project, sourced Work Requests through their
-- own lane, and a person can move a row directly — so a check in any single
-- handler is a check the other routes walk past. The row transition is the one
-- thing every route has in common, which is where the council's word "path"
-- actually points.
--
-- WHAT COUNTS AS IN FLIGHT: claimed and in_progress. Deliberately NOT
-- verification, needs_joe or blocked. Those are held, and they are current — the
-- current-work-item verb returns them for exactly that reason — but they are not
-- consuming the thing the limit protects, which is a person's or a session's
-- attention on active work. Counting a blocked row against the limit would mean
-- one credential nobody can supply freezes the whole queue, which is the
-- opposite of the intent.
--
-- WHAT THE LIMIT IS FOR, so a later reader can judge whether it still earns its
-- place: on 2026-08-21 four sessions were working the Drive-retirement phase at
-- once, across thirteen worktrees and four pull requests, three of them
-- independently proposing the same foundation. Nothing refused any of it. The
-- cost was not the parallelism, it was that three of those proposals were
-- discarded work nobody had agreed to fund.
--
-- HOW A LEGITIMATE EXCEPTION IS TAKEN. Not by editing this trigger. The row's
-- own state machine already has the answer: move something to blocked, verification
-- or confirmed_closed first. If a genuine need to run three-wide appears, that is
-- a decision to record and a limit to raise here in one deliberate act, not a
-- guard to route around — and the number lives in one place so raising it is a
-- one-line diff a reviewer can see.

begin;

create or replace function ops.enforce_work_in_progress_limit()
returns trigger language plpgsql as $$
declare
  system_wide   int;
  per_executor  int;
  who           text;
  limit_system  constant int := 2;
  limit_each    constant int := 1;
begin
  -- Only a transition INTO flight is checked. An update that leaves a row in
  -- flight (a note, a blocker detail, a version bump) must not be refused
  -- because the queue is already at its limit — that would make a full queue
  -- uneditable, and the guard would be discovered as a bug rather than a limit.
  if new.state not in ('claimed','in_progress') then
    return new;
  end if;
  if tg_op = 'UPDATE' and old.state in ('claimed','in_progress') then
    return new;
  end if;

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

  who := coalesce(new.executor_actor, new.owner_actor);
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
  'deliberately excluded so a blocked row cannot freeze the queue.';

drop trigger if exists work_in_progress_limit on ops.work_request;
create trigger work_in_progress_limit
  before insert or update on ops.work_request
  for each row execute function ops.enforce_work_in_progress_limit();

-- PROOF, inside the transaction. Fixtures enter at 'captured' and transition,
-- because ops.work_request_shape_gate refuses a new row landing straight in
-- implementation — a rule this proof must obey rather than work around. The
-- sentinel exception rolls the whole proof back, so no fixture row survives.
do $$
declare
  probe_a uuid;
  probe_c uuid;
  probe_d uuid;
  refused boolean;
  shaper uuid;
begin
  select id into shaper from actor where slug='joe' limit 1;
  if shaper is null then
    raise exception '0276 proof needs a joe actor row to attribute the shape decision to';
  end if;
  insert into ops.work_request (ref, state, title, requester_actor, executor_actor,
                              shape_disposition, shape_fixed_surface_ref, shape_rationale,
                              shape_decided_by_actor_id, shape_decided_at)
  values ('WR-999901','captured','wip probe a','joe','probe-executor-one',
          'not_required','probe:work-in-progress-limit-fixture',
          'rolled-back proof fixture for the work-in-progress limit',
          shaper, now())
  returning id into probe_a;
  update ops.work_request set state='claimed' where id=probe_a;

  -- Same executor, second item: refused by the per-executor limit.
  insert into ops.work_request (ref, state, title, requester_actor, executor_actor,
                              shape_disposition, shape_fixed_surface_ref, shape_rationale,
                              shape_decided_by_actor_id, shape_decided_at)
  values ('WR-999902','captured','wip probe b','joe','probe-executor-one',
          'not_required','probe:work-in-progress-limit-fixture',
          'rolled-back proof fixture for the work-in-progress limit',
          shaper, now())
  returning id into probe_c;
  begin
    update ops.work_request set state='claimed' where id=probe_c;
    refused := false;
  exception when check_violation then refused := true;
  end;
  if not refused then
    raise exception '0276 FAILED: a second in-flight item for one executor was not refused';
  end if;

  -- A different executor is fine while the system-wide limit has room.
  insert into ops.work_request (ref, state, title, requester_actor, executor_actor,
                              shape_disposition, shape_fixed_surface_ref, shape_rationale,
                              shape_decided_by_actor_id, shape_decided_at)
  values ('WR-999903','captured','wip probe c','joe','probe-executor-two',
          'not_required','probe:work-in-progress-limit-fixture',
          'rolled-back proof fixture for the work-in-progress limit',
          shaper, now())
  returning id into probe_d;
  update ops.work_request set state='claimed' where id=probe_d;

  -- Third distinct executor: refused by the system-wide limit.
  insert into ops.work_request (ref, state, title, requester_actor, executor_actor,
                              shape_disposition, shape_fixed_surface_ref, shape_rationale,
                              shape_decided_by_actor_id, shape_decided_at)
  values ('WR-999904','captured','wip probe d','joe','probe-executor-three',
          'not_required','probe:work-in-progress-limit-fixture',
          'rolled-back proof fixture for the work-in-progress limit',
          shaper, now());
  begin
    update ops.work_request set state='claimed' where ref='WR-999904';
    refused := false;
  exception when check_violation then refused := true;
  end;
  if not refused then
    raise exception '0276 FAILED: a third system-wide in-flight item was not refused';
  end if;

  -- AN EDIT TO A ROW ALREADY IN FLIGHT MUST STILL WORK. A guard that refuses
  -- this makes a full queue uneditable and reads as a bug rather than a limit.
  update ops.work_request set title = 'wip probe a, edited' where id = probe_a;

  -- AND A BLOCKED ROW MUST NOT CONSUME THE LIMIT. Park one, then a new claim
  -- must be accepted — otherwise one unavailable credential freezes everything.
  update ops.work_request
     set state='blocked', blocker_code='capability',
         blocker_detail='probe: a credential nobody here holds'
   where id = probe_a;
  update ops.work_request set state='claimed' where ref='WR-999904';

  raise exception 'CARR_0276_PROOF_OK';
exception when others then
  if sqlerrm <> 'CARR_0276_PROOF_OK' then raise; end if;
end $$;

commit;
