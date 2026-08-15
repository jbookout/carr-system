-- 0129_reprioritize_rag_benchmark.sql
--
-- Joe reprioritized the lexical RAG benchmark on 2026-08-15 because retrieval
-- quality affects every downstream use of CARR. Move the existing WR-AI-006 to
-- the queue head without renaming any Work Request or changing the relative
-- order of the other fifty projects. This is a one-time owner decision, not a
-- general queue-reordering verb.

begin;

lock table ops.work_request in share row exclusive mode;
lock table ops.capability_agent_session in share row exclusive mode;

do $$
declare total integer; non_ready integer; sessions integer;
begin
  select count(*), count(*) filter (where state <> 'ready')
    into total, non_ready
    from ops.work_request
   where program_key='carr-ai-engineering-suite-v1';
  select count(*) into sessions
    from ops.capability_agent_session s
    join ops.work_request w on w.id=s.work_request_id
   where w.program_key='carr-ai-engineering-suite-v1';
  if total <> 51 or non_ready <> 0 or sessions <> 0 then
    raise exception '0129 REFUSED: reprioritization requires 51 untouched ready projects and zero capability sessions (total %, non_ready %, sessions %)', total, non_ready, sessions;
  end if;
  if not exists (
    select 1 from ops.work_request
     where program_key='carr-ai-engineering-suite-v1'
       and ref='WR-AI-006' and program_ordinal=6 and title='RAG pipeline'
  ) then
    raise exception '0129 REFUSED: the approved RAG benchmark is not at its expected original identity';
  end if;
end;
$$;

-- 0126 deliberately made ordinals immutable to ordinary application writers.
-- A versioned migration owned by the schema owner is the only allowed escape;
-- disabling the trigger inside this transaction rolls back automatically if
-- any proof below fails.
alter table ops.work_request disable trigger capability_program_identity_guard_before_update;

update ops.work_request
   set program_ordinal=1006
 where program_key='carr-ai-engineering-suite-v1' and ref='WR-AI-006';

update ops.work_request
   set program_ordinal=program_ordinal+1000
 where program_key='carr-ai-engineering-suite-v1' and program_ordinal between 1 and 5;

update ops.work_request
   set program_ordinal=1
 where program_key='carr-ai-engineering-suite-v1' and ref='WR-AI-006';

update ops.work_request
   set program_ordinal=program_ordinal-999
 where program_key='carr-ai-engineering-suite-v1' and program_ordinal between 1001 and 1005;

alter table ops.work_request enable trigger capability_program_identity_guard_before_update;

do $$
declare actual text[]; expected text[] := array[
  'WR-AI-006','WR-AI-001','WR-AI-002','WR-AI-003','WR-AI-004','WR-AI-005',
  'WR-AI-007','WR-AI-008','WR-AI-009','WR-AI-010','WR-AI-011','WR-AI-012',
  'WR-AI-013','WR-AI-014','WR-AI-015','WR-AI-016','WR-AI-017','WR-AI-018',
  'WR-AI-019','WR-AI-020','WR-AI-021','WR-AI-022','WR-AI-023','WR-AI-024',
  'WR-AI-025','WR-AI-026','WR-AI-027','WR-AI-028','WR-AI-029','WR-AI-030',
  'WR-AI-031','WR-AI-032','WR-AI-033','WR-AI-034','WR-AI-035','WR-AI-036',
  'WR-AI-037','WR-AI-038','WR-AI-039','WR-AI-040','WR-AI-041','WR-AI-042',
  'WR-AI-043','WR-AI-044','WR-AI-045','WR-AI-046','WR-AI-047','WR-AI-048',
  'WR-AI-049','WR-AI-050','WR-AI-051'
];
begin
  select array_agg(ref order by program_ordinal) into actual
    from ops.work_request where program_key='carr-ai-engineering-suite-v1';
  if actual is distinct from expected then
    raise exception '0129 FAILED: effective queue order does not match Joe-approved RAG-first order';
  end if;
  if (select count(*) from ops.v_capability_program_next where program_key='carr-ai-engineering-suite-v1') <> 1
     or (select ref from ops.v_capability_program_next where program_key='carr-ai-engineering-suite-v1') <> 'WR-AI-006' then
    raise exception '0129 FAILED: RAG pipeline is not the sole queue head';
  end if;
end;
$$;

commit;
