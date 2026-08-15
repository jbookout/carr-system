-- 0128_capability_session_terminal_immutability.sql
--
-- Closed Work Requests and verification rows became immutable in 0127. Freeze
-- the remaining audit surface too: candidate_kind travels with the already
-- immutable candidate payload, and a completed/cancelled build session accepts
-- no later edits, including timestamp or version rewrites.

begin;

create or replace function ops.capability_agent_session_guard()
returns trigger language plpgsql as $$
begin
  if tg_op = 'INSERT' then
    if new.state <> 'claimed' or new.candidate_evidence is not null
       or new.candidate_kind is not null or new.candidate_fingerprint is not null then
      raise exception 'capability session must be created claimed with no candidate';
    end if;
  else
    if old.state in ('completed','cancelled') then
      if new is distinct from old then
        raise exception 'terminal capability session is immutable';
      end if;
      return old;
    end if;
    if new.work_request_id is distinct from old.work_request_id
       or new.executor_actor_id is distinct from old.executor_actor_id
       or new.created_by_actor_id is distinct from old.created_by_actor_id
       or new.source_commit_sha is distinct from old.source_commit_sha
       or new.worktree_ref is distinct from old.worktree_ref
       or new.scope_ref is distinct from old.scope_ref then
      raise exception 'capability session identity is immutable';
    end if;
    if old.candidate_kind is not null
       and new.candidate_kind is distinct from old.candidate_kind then
      raise exception 'prepared capability candidate kind is immutable';
    end if;
    if old.candidate_evidence is not null
       and new.candidate_evidence is distinct from old.candidate_evidence then
      raise exception 'prepared capability candidate is immutable';
    end if;
    if old.candidate_fingerprint is not null
       and new.candidate_fingerprint is distinct from old.candidate_fingerprint then
      raise exception 'prepared capability fingerprint is immutable';
    end if;
    if old.state = 'claimed' and new.state not in ('in_progress','cancelled') then
      raise exception 'capability session claimed may only move to in_progress or cancelled';
    elsif old.state = 'in_progress' and new.state not in ('verification','cancelled') then
      raise exception 'capability session in_progress may only move to verification or cancelled';
    elsif old.state = 'verification' and new.state not in ('completed','cancelled') then
      raise exception 'capability session verification may only move to completed or cancelled';
    end if;
  end if;

  if new.candidate_evidence is not null then
    new.candidate_fingerprint := md5(new.candidate_evidence::text);
  elsif new.candidate_fingerprint is not null then
    raise exception 'candidate fingerprint requires candidate evidence';
  end if;
  new.updated_at := now();
  return new;
end;
$$;

-- Rollback-only proof against one real session. No queue state is changed.
do $$
declare wr uuid; executor uuid; verifier uuid; sess uuid;
begin
  begin
    select id into wr from ops.work_request
     where program_key='carr-ai-engineering-suite-v1' and program_ordinal=1;
    select id into executor from actor where slug='system' and active;
    select id into verifier from actor where slug='joe' and active;
    if wr is null or executor is null or verifier is null then
      raise exception '0128 FAILED: queue row and active actors are required';
    end if;
    insert into ops.capability_agent_session
      (work_request_id, executor_actor_id, created_by_actor_id, source_commit_sha, worktree_ref)
      values (wr, executor, verifier, repeat('e',40), '0128-proof-worktree')
      returning id into sess;
    update ops.capability_agent_session
       set state='in_progress', started_at=now(), version=version+1 where id=sess;
    update ops.capability_agent_session
       set state='verification', candidate_kind='extended',
           candidate_evidence=jsonb_build_object(
             'artifact_ref','0128-proof',
             'candidate_commit_sha',repeat('f',40),
             'acceptance_test_refs',jsonb_build_array('0128-proof-test')),
           prepared_at=now(), version=version+1 where id=sess;
    begin
      update ops.capability_agent_session set candidate_kind='built' where id=sess;
      raise exception '0128 FAILED: prepared candidate kind was rewritten';
    exception when raise_exception then
      if sqlerrm like '0128 FAILED:%' then raise; end if;
    end;
    update ops.capability_agent_session
       set state='completed', completed_at=now(), version=version+1 where id=sess;
    begin
      update ops.capability_agent_session set completed_at=now() + interval '1 second' where id=sess;
      raise exception '0128 FAILED: terminal capability session was rewritten';
    exception when raise_exception then
      if sqlerrm like '0128 FAILED:%' then raise; end if;
    end;
    raise exception '0128 PROOF ROLLBACK';
  exception when raise_exception then
    if sqlerrm <> '0128 PROOF ROLLBACK' then raise; end if;
  end;
end;
$$;

commit;
