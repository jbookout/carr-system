-- 0126_capability_program_evidence.sql
--
-- 0125 deliberately made the ordered programme visible, but it did not make a
-- completion claim strong enough: free-text session and review references could
-- be supplied by the same caller who built the work.  This forward-only repair
-- persists a real build session, freezes its prepared candidate, and records a
-- separately-authenticated attestation before the Work Request may close.
--
-- 0125 has already run in staging.  Do not edit it: every repair belongs here.
-- This file keeps its proof INSIDE its transaction.  A failed proof rolls back
-- schema, grants and seed-proof rows together rather than leaving a half-applied
-- migration reported as failed.

begin;

create table ops.capability_agent_session (
  id                    uuid primary key default gen_random_uuid(),
  work_request_id       uuid not null references ops.work_request(id) on delete restrict,
  executor_actor_id     uuid not null references actor(id) on delete restrict,
  created_by_actor_id   uuid not null references actor(id) on delete restrict,
  state                 text not null default 'claimed'
    check (state in ('claimed','in_progress','verification','completed','cancelled')),
  source_commit_sha     text not null check (source_commit_sha ~ '^[0-9a-f]{40}$'),
  worktree_ref          text not null check (btrim(worktree_ref) <> ''),
  scope_ref             text,
  candidate_kind        text check (candidate_kind in ('built','extended','adopted','declined')),
  candidate_evidence    jsonb,
  candidate_fingerprint text,
  claimed_at            timestamptz not null default now(),
  started_at            timestamptz,
  prepared_at           timestamptz,
  completed_at          timestamptz,
  cancelled_at          timestamptz,
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now(),
  version               integer not null default 1 check (version > 0),
  constraint capability_agent_session_candidate_travels_together check (
    (candidate_kind is null) = (candidate_evidence is null)
    and (candidate_kind is null) = (candidate_fingerprint is null)
  ),
  constraint capability_agent_session_verification_has_candidate check (
    state not in ('verification','completed') or candidate_evidence is not null
  )
);

create unique index capability_one_open_session_per_request
  on ops.capability_agent_session (work_request_id)
  where state not in ('completed','cancelled');

create index capability_agent_session_request_idx
  on ops.capability_agent_session (work_request_id, state, created_at desc);

comment on table ops.capability_agent_session is
  'Server-created, actor-bound build-session evidence for the fixed capability programme. '
  'A session starts claimed, is separately started, then freezes one candidate for independent verification. '
  'It never stores code or secrets: only worktree, commit and evidence references.';

-- A pass/fail is a first-class, universal record.  It works for code, adopted
-- products and a declined project alike; a code-finding is useful supporting
-- evidence, but cannot be the only verification home because it cannot express
-- a decision-only candidate.
create table ops.capability_verification (
  id                      uuid primary key default gen_random_uuid(),
  build_session_id        uuid not null references ops.capability_agent_session(id) on delete restrict,
  work_request_id         uuid not null references ops.work_request(id) on delete restrict,
  verifier_actor_id       uuid not null references actor(id) on delete restrict,
  outcome                 text not null check (outcome in ('pass','fail')),
  verification_evidence_ref text not null check (btrim(verification_evidence_ref) <> ''),
  source_ref              text not null check (btrim(source_ref) <> ''),
  candidate_fingerprint   text not null check (candidate_fingerprint ~ '^[0-9a-f]{32}$'),
  note                    text,
  attested_at             timestamptz not null default now(),
  constraint capability_attestation_note_present check (note is null or btrim(note) <> '')
);

create unique index capability_one_attestation_per_reviewer_candidate
  on ops.capability_verification (build_session_id, verifier_actor_id, candidate_fingerprint);

comment on table ops.capability_verification is
  'Independent pass/fail attestation bound to one immutable capability-session candidate. '
  'The insert trigger rejects self-review or a stale candidate. Evidence and source references '
  'are persisted with the attestation rather than supplied later during completion.';

create or replace function ops.capability_agent_session_guard()
returns trigger language plpgsql as $$
begin
  if tg_op = 'INSERT' then
    if new.state <> 'claimed' or new.candidate_evidence is not null
       or new.candidate_kind is not null or new.candidate_fingerprint is not null then
      raise exception 'capability session must be created claimed with no candidate';
    end if;
  else
    if new.work_request_id is distinct from old.work_request_id
       or new.executor_actor_id is distinct from old.executor_actor_id
       or new.created_by_actor_id is distinct from old.created_by_actor_id
       or new.source_commit_sha is distinct from old.source_commit_sha
       or new.worktree_ref is distinct from old.worktree_ref
       or new.scope_ref is distinct from old.scope_ref then
      raise exception 'capability session identity is immutable';
    end if;
    if old.candidate_evidence is not null and new.candidate_evidence is distinct from old.candidate_evidence then
      raise exception 'prepared capability candidate is immutable';
    end if;
    if old.candidate_fingerprint is not null and new.candidate_fingerprint is distinct from old.candidate_fingerprint then
      raise exception 'prepared capability fingerprint is immutable';
    end if;
    if old.state = 'claimed' and new.state not in ('in_progress','cancelled') then
      raise exception 'capability session claimed may only move to in_progress or cancelled';
    elsif old.state = 'in_progress' and new.state not in ('verification','cancelled') then
      raise exception 'capability session in_progress may only move to verification or cancelled';
    elsif old.state = 'verification' and new.state not in ('completed','cancelled') then
      raise exception 'capability session verification may only move to completed or cancelled';
    elsif old.state in ('completed','cancelled') and new.state is distinct from old.state then
      raise exception 'terminal capability session cannot transition';
    end if;
  end if;

  if new.candidate_evidence is not null then
    -- md5 is PostgreSQL core and is used here only as a
    -- deterministic content fingerprint, not as a secret or security hash. The
    -- immutable jsonb payload remains the authoritative evidence.
    new.candidate_fingerprint := md5(new.candidate_evidence::text);
  elsif new.candidate_fingerprint is not null then
    raise exception 'candidate fingerprint requires candidate evidence';
  end if;
  new.updated_at := now();
  return new;
end;
$$;

create trigger capability_agent_session_guard_before_write
before insert or update on ops.capability_agent_session
for each row execute function ops.capability_agent_session_guard();

create or replace function ops.capability_attestation_guard()
returns trigger language plpgsql as $$
declare s ops.capability_agent_session%rowtype;
begin
  select * into s from ops.capability_agent_session where id = new.build_session_id for key share;
  if not found then raise exception 'capability build session does not exist'; end if;
  if s.work_request_id <> new.work_request_id then
    raise exception 'attestation work request does not match build session';
  end if;
  if s.state <> 'verification' then
    raise exception 'attestation requires a session in verification';
  end if;
  if new.verifier_actor_id = s.executor_actor_id then
    raise exception 'capability executor may not attest its own candidate';
  end if;
  if new.candidate_fingerprint <> s.candidate_fingerprint then
    raise exception 'attestation candidate does not match the prepared candidate';
  end if;
  return new;
end;
$$;

create trigger capability_attestation_guard_before_insert
before insert on ops.capability_verification
for each row execute function ops.capability_attestation_guard();

-- The programme key and ordinal identify the governed queue itself. A direct
-- writer must not evade the close guard by first turning a programme row into
-- an ordinary Work Request or moving it behind another row.
create or replace function ops.capability_program_identity_guard()
returns trigger language plpgsql as $$
begin
  if old.program_key = 'carr-ai-engineering-suite-v1'
     and (new.program_key is distinct from old.program_key
          or new.program_ordinal is distinct from old.program_ordinal) then
    raise exception 'capability programme identity is immutable';
  end if;
  return new;
end;
$$;

create trigger capability_program_identity_guard_before_update
before update of program_key, program_ordinal on ops.work_request
for each row execute function ops.capability_program_identity_guard();

-- Defense in depth: the Worker is the normal lifecycle gate, but a direct SQL
-- update cannot close a programme Work Request without the same immutable
-- candidate and independently-recorded pass. Non-programme Work Requests keep
-- their existing transition behavior untouched.
create or replace function ops.capability_program_close_guard()
returns trigger language plpgsql as $$
declare s ops.capability_agent_session%rowtype; pass_id uuid;
begin
  if new.program_key is distinct from 'carr-ai-engineering-suite-v1'
     or new.state <> 'confirmed_closed' or old.state = 'confirmed_closed' then
    return new;
  end if;
  select * into s from ops.capability_agent_session
   where work_request_id=new.id and state='verification'
   order by prepared_at desc limit 1 for key share;
  if not found or s.candidate_kind <> new.completion_kind
     or (new.completion_evidence -> 'candidate') is distinct from s.candidate_evidence then
    raise exception 'capability programme close requires its frozen prepared candidate';
  end if;
  begin
    pass_id := new.verification_evidence_ref::uuid;
  exception when invalid_text_representation then
    raise exception 'capability programme close requires a stored pass attestation id';
  end;
  if not exists (
    select 1 from ops.capability_verification a
     where a.id=pass_id and a.build_session_id=s.id and a.work_request_id=new.id
       and a.outcome='pass' and a.candidate_fingerprint=s.candidate_fingerprint
       and a.verifier_actor_id <> s.executor_actor_id
  ) then
    raise exception 'capability programme close requires an independent stored pass';
  end if;
  return new;
end;
$$;

create trigger capability_program_close_guard_before_update
before update of state on ops.work_request
for each row execute function ops.capability_program_close_guard();

grant select, insert, update on ops.capability_agent_session to carr_writer;
grant select, insert on ops.capability_verification to carr_writer;
grant select on ops.capability_agent_session, ops.capability_verification to carr_reader;
-- carr_jobs gets neither write path: it may observe the program head (0125) but
-- cannot create a session, attest a result, or close work by proxy.

-- ── proof, still inside the transaction ────────────────────────────────────
do $$
declare wr uuid; executor uuid; verifier uuid; sess uuid; fingerprint text; pass uuid;
        original ops.work_request%rowtype;
begin
  select * into original from ops.work_request where program_key = 'carr-ai-engineering-suite-v1' order by program_ordinal limit 1;
  wr := original.id;
  if wr is null then raise exception '0126 FAILED: 0125 capability work requests are absent'; end if;
  select id into executor from actor where slug='system' and active;
  select id into verifier from actor where slug='joe' and active;
  if executor is null or verifier is null then
    raise exception '0126 FAILED: two active actors are required for independent attestation proof';
  end if;
  insert into ops.capability_agent_session (work_request_id, executor_actor_id, created_by_actor_id, source_commit_sha, worktree_ref)
    values (wr, executor, verifier, repeat('a',40), 'proof-worktree') returning id into sess;
  update ops.capability_agent_session
     set state='in_progress', started_at=now(), version=version+1 where id=sess;
  update ops.capability_agent_session
     set state='verification', candidate_kind='built',
         candidate_evidence=jsonb_build_object('artifact_ref','proof','candidate_commit_sha',repeat('b',40),'acceptance_test_refs',jsonb_build_array('proof-test')),
         prepared_at=now(), version=version+1 where id=sess;
  select candidate_fingerprint into fingerprint from ops.capability_agent_session where id=sess;
  if fingerprint !~ '^[0-9a-f]{32}$' then raise exception '0126 FAILED: prepared candidate did not receive a fingerprint'; end if;
  begin
    update ops.capability_agent_session
       set candidate_evidence='{"artifact_ref":"tampered"}'::jsonb where id=sess;
    raise exception '0126 FAILED: immutable prepared candidate was changed';
  exception when raise_exception then
    if sqlerrm like '0126 FAILED:%' then raise; end if;
  end;
  -- The close trigger must refuse a direct SQL completion before the independent
  -- pass exists, even though the frozen candidate is otherwise supplied.
  begin
    update ops.work_request set state='confirmed_closed', completion_kind='built',
      completion_evidence=jsonb_build_object('candidate', (select candidate_evidence from ops.capability_agent_session where id=sess)),
      verification_accepted_at=now(), verification_evidence_ref=gen_random_uuid()::text, closed_at=now()
      where id=wr;
    raise exception '0126 FAILED: programme Work Request closed without a pass';
  exception when raise_exception then
    if sqlerrm like '0126 FAILED:%' then raise; end if;
  end;
  insert into ops.capability_verification
    (build_session_id, work_request_id, verifier_actor_id, outcome, verification_evidence_ref, source_ref, candidate_fingerprint)
    values (sess, wr, verifier, 'pass', 'proof-test', '0126 transaction proof', fingerprint) returning id into pass;
  begin
    insert into ops.capability_verification
      (build_session_id, work_request_id, verifier_actor_id, outcome, verification_evidence_ref, source_ref, candidate_fingerprint)
      values (sess, wr, executor, 'pass', 'proof-test', '0126 transaction proof', fingerprint);
    raise exception '0126 FAILED: executor self-attestation was accepted';
  exception when raise_exception then
    if sqlerrm like '0126 FAILED:%' then raise; end if;
  end;
  -- The same direct path becomes legal only when its evidence points to the
  -- immutable candidate and the stored independent pass, then the row is
  -- restored exactly so migration proof leaves no operational history behind.
  update ops.work_request set state='confirmed_closed', completion_kind='built',
    completion_evidence=jsonb_build_object('candidate', (select candidate_evidence from ops.capability_agent_session where id=sess)),
    verification_accepted_at=now(), verification_evidence_ref=pass::text, closed_at=now()
    where id=wr;
  update ops.work_request set state=original.state, completion_kind=original.completion_kind,
    completion_evidence=original.completion_evidence, verification_accepted_at=original.verification_accepted_at,
    verification_evidence_ref=original.verification_evidence_ref, closed_at=original.closed_at,
    updated_at=original.updated_at, version=original.version where id=wr;
  delete from ops.capability_verification where build_session_id=sess;
  delete from ops.capability_agent_session where id=sess;
end;
$$;

commit;
