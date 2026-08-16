-- 0127_capability_program_immutability.sql
--
-- 0126 made completion conditional on a frozen candidate and a separate stored
-- pass, but its Work Request trigger ran only while the row entered
-- confirmed_closed. A shared writer could subsequently rewrite the evidence or
-- reopen the row without invoking that trigger. Closed capability evidence is
-- an audit result, not mutable application state, so this forward-only repair
-- makes the complete row and every verification record immutable.
--
-- AUTHORITY BOUNDARY, STATED HONESTLY. PostgreSQL sees the Worker as the shared
-- carr_writer role; it does not see the OAuth/MCP actor. These triggers enforce
-- consistency and immutability, not caller identity. Actor authentication,
-- humanOnly dispatch, and executor/verifier attribution remain enforced by the
-- MCP application before this role receives SQL. Possession of the writer DSN
-- is privileged infrastructure access to the whole record layer and is not
-- represented as an independent human identity.

begin;

create or replace function ops.capability_program_closed_immutable()
returns trigger language plpgsql as $$
begin
  if old.program_key = 'carr-ai-engineering-suite-v1'
     and old.state = 'confirmed_closed'
     and new is distinct from old then
    raise exception 'closed capability programme evidence is immutable';
  end if;
  return new;
end;
$$;

create trigger capability_program_closed_immutable_before_update
before update on ops.work_request
for each row execute function ops.capability_program_closed_immutable();

create or replace function ops.capability_verification_immutable()
returns trigger language plpgsql as $$
begin
  raise exception 'capability verification records are immutable';
end;
$$;

create trigger capability_verification_immutable_before_change
before update or delete on ops.capability_verification
for each row execute function ops.capability_verification_immutable();

-- Prove both immutable surfaces against a real frozen candidate and pass. The
-- sentinel exception rolls the entire inner proof back, so project 1 remains
-- the queue head and no proof rows survive the migration.
do $$
declare wr uuid; executor uuid; verifier uuid; sess uuid; fingerprint text; pass uuid;
begin
  begin
    select id into wr from ops.work_request
     where program_key='carr-ai-engineering-suite-v1' and program_ordinal=1;
    select id into executor from actor where slug='system' and active;
    select id into verifier from actor where slug='joe' and active;
    if wr is null or executor is null or verifier is null or executor=verifier then
      raise exception '0127 FAILED: queue row and two distinct active actors are required';
    end if;

    insert into ops.capability_agent_session
      (work_request_id, executor_actor_id, created_by_actor_id, source_commit_sha, worktree_ref)
      values (wr, executor, verifier, repeat('c',40), '0127-proof-worktree')
      returning id into sess;
    update ops.capability_agent_session
       set state='in_progress', started_at=now(), version=version+1 where id=sess;
    update ops.capability_agent_session
       set state='verification', candidate_kind='extended',
           candidate_evidence=jsonb_build_object(
             'artifact_ref','0127-proof',
             'candidate_commit_sha',repeat('d',40),
             'acceptance_test_refs',jsonb_build_array('0127-proof-test')),
           prepared_at=now(), version=version+1
     where id=sess returning candidate_fingerprint into fingerprint;
    update ops.work_request set state='verification' where id=wr;
    insert into ops.capability_verification
      (build_session_id, work_request_id, verifier_actor_id, outcome,
       verification_evidence_ref, source_ref, candidate_fingerprint)
      values (sess, wr, verifier, 'pass', '0127-proof-test',
              '0127 transaction proof', fingerprint)
      returning id into pass;
    update ops.work_request
       set state='confirmed_closed', completion_kind='extended',
           completion_evidence=jsonb_build_object(
             'candidate',(select candidate_evidence from ops.capability_agent_session where id=sess)),
           verification_accepted_at=now(), verification_evidence_ref=pass::text,
           closed_at=now()
     where id=wr;

    begin
      update ops.work_request
         set completion_evidence='{"forged":true}'::jsonb where id=wr;
      raise exception '0127 FAILED: closed evidence was rewritten';
    exception when raise_exception then
      if sqlerrm like '0127 FAILED:%' then raise; end if;
    end;
    begin
      update ops.work_request set state='verification', closed_at=null where id=wr;
      raise exception '0127 FAILED: closed project was reopened';
    exception when raise_exception then
      if sqlerrm like '0127 FAILED:%' then raise; end if;
    end;
    begin
      update ops.capability_verification set note='forged' where id=pass;
      raise exception '0127 FAILED: verification record was rewritten';
    exception when raise_exception then
      if sqlerrm like '0127 FAILED:%' then raise; end if;
    end;

    raise exception '0127 PROOF ROLLBACK';
  exception when raise_exception then
    if sqlerrm <> '0127 PROOF ROLLBACK' then raise; end if;
  end;
end;
$$;

commit;
