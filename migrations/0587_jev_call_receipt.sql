-- 0587_jev_call_receipt.sql
--
-- Server-side Jev call log: an append-only receipt of every Jev (TypeSafe)
-- call the Worker itself made, recorded in the same writer transaction that
-- returns the answers, stamped with the database clock.
--
-- WHY THIS EXISTS. Jev enforcement gates credited evidence the gated model
-- could write: the session transcript and out/jev-calls.jsonl. The model and
-- the hooks share one OS user, so a local log or a local credential is
-- forgeable. The `ask-jev` verb (mcp-server/src/jev-call-receipt.js) moves the
-- call into the Worker, which alone holds the TypeSafe key, and this table is
-- where the Worker writes down that the call happened. A gate that credits
-- only rows read back through `read-jev-call-receipts` credits something the
-- model could not have produced without actually asking Jev.
--
-- WHAT IS STORED. Ids, digests and probabilities only: the question ids, the
-- lowercase-hex sha256 of the canonical JSON of the state, the questions and
-- the answers, the answers themselves (Jev's probabilities), usage, the
-- requested and answered model, and -- for purpose 'build_advisory' only --
-- the sha256 of the canonical JSON of state.partner_request. Never the state
-- text. recorded_at is clock_timestamp() on the server; no caller supplies it.
--
-- APPEND-ONLY. No app role holds any direct privilege on the table. The one
-- write path is ops.record_jev_call_receipt (SECURITY DEFINER, EXECUTE to
-- carr_writer only), the one read path ops.read_jev_call_receipts (SECURITY
-- DEFINER, EXECUTE to carr_reader and carr_writer), and a trigger refuses
-- every UPDATE, DELETE and TRUNCATE, including the owner's.
--
-- ATOMIC WITH 0588. The write door is a new SECURITY DEFINER function with an
-- EXECUTE grant, which moves the live SCAC mutation catalog; applied alone
-- this migration is refused at commit by the deferred epoch trigger. 0588
-- seals the catalog as v69, and tools/migrate.py declares (0587, 0588) one
-- atomic group.
--
-- No explicit transaction control: from 0339 onward tools/migrate.py runs
-- each migration inside its own single transaction.

create table if not exists ops.jev_call_receipt (
  receipt_id uuid primary key default gen_random_uuid(),
  recorded_at timestamptz not null default clock_timestamp(),
  session_id text not null check (char_length(session_id) between 1 and 200),
  purpose text not null check (purpose in ('call','build_advisory')),
  question_ids text[] not null
    check (cardinality(question_ids) between 1 and 64 and array_position(question_ids, null) is null),
  facets text[] not null default '{}'
    check (facets <@ array['architecture_or_design','semantic_creation','diagnosis',
      'verification_selection','evidence_matching','next_action_priority']::text[]
      and array_position(facets, null) is null),
  model_requested text not null check (btrim(model_requested) <> ''),
  model_answered text not null check (btrim(model_answered) <> ''),
  state_sha256 text not null check (state_sha256 ~ '^[0-9a-f]{64}$'),
  questions_sha256 text not null check (questions_sha256 ~ '^[0-9a-f]{64}$'),
  answers_sha256 text not null check (answers_sha256 ~ '^[0-9a-f]{64}$'),
  prompt_sha256 text check (prompt_sha256 ~ '^[0-9a-f]{64}$'),
  answers jsonb not null check (jsonb_typeof(answers) = 'object'),
  usage jsonb check (usage is null or jsonb_typeof(usage) = 'object'),
  actor_slug text not null check (btrim(actor_slug) <> ''),
  idempotency_key text not null unique check (btrim(idempotency_key) <> ''),
  -- prompt_sha256 is required exactly when purpose = 'build_advisory'.
  constraint jev_call_receipt_prompt_iff_build_advisory
    check ((purpose = 'build_advisory') = (prompt_sha256 is not null))
);

create index if not exists jev_call_receipt_session_recorded_at_idx
  on ops.jev_call_receipt (session_id, recorded_at);

comment on table ops.jev_call_receipt is
  'Append-only receipt of each Jev (TypeSafe) call the Worker made through ask-jev. recorded_at is the server clock. Ids, digests and probabilities only; no state text. Written only through ops.record_jev_call_receipt, read only through ops.read_jev_call_receipts.';

revoke all on table ops.jev_call_receipt from public, carr_reader, carr_writer, carr_jobs, carr_authority;

create or replace function ops.jev_call_receipt_append_only()
returns trigger
language plpgsql
set search_path = pg_catalog, ops
as $$
begin
  raise exception 'ops.jev_call_receipt is append-only (% refused)', tg_op;
end;
$$;

drop trigger if exists jev_call_receipt_append_only on ops.jev_call_receipt;
create trigger jev_call_receipt_append_only
before update or delete on ops.jev_call_receipt
for each row execute function ops.jev_call_receipt_append_only();

drop trigger if exists jev_call_receipt_no_truncate on ops.jev_call_receipt;
create trigger jev_call_receipt_no_truncate
before truncate on ops.jev_call_receipt
for each statement execute function ops.jev_call_receipt_append_only();

revoke all on function ops.jev_call_receipt_append_only() from public;

create or replace function ops.record_jev_call_receipt(
  p_session_id text,
  p_purpose text,
  p_question_ids text[],
  p_facets text[],
  p_model_requested text,
  p_model_answered text,
  p_state_sha256 text,
  p_questions_sha256 text,
  p_answers_sha256 text,
  p_prompt_sha256 text,
  p_answers jsonb,
  p_usage jsonb,
  p_actor_slug text,
  p_idempotency_key text
)
returns table (
  receipt_id uuid,
  recorded_at timestamptz,
  replayed boolean
)
language plpgsql security definer
set search_path = pg_catalog, ops
as $$
declare
  v_existing ops.jev_call_receipt%rowtype;
  v_row ops.jev_call_receipt%rowtype;
begin
  if p_idempotency_key is null or btrim(p_idempotency_key) = '' then
    raise exception 'idempotency_key_required';
  end if;
  if p_actor_slug is null or btrim(p_actor_slug) = '' then
    raise exception 'jev_call_receipt_actor_required';
  end if;
  if p_session_id is null or char_length(p_session_id) not between 1 and 200 then
    raise exception 'jev_session_id_invalid';
  end if;
  if p_purpose is null or p_purpose not in ('call','build_advisory') then
    raise exception 'jev_purpose_invalid';
  end if;
  if (p_purpose = 'build_advisory') <> (p_prompt_sha256 is not null) then
    raise exception 'jev_call_receipt_prompt_sha256_iff_build_advisory';
  end if;

  -- The Worker holds an advisory lock on the key for the whole transaction
  -- (withEnvelope), so a same-key replay reaches here only when the envelope
  -- ledger and this table disagree. Identical content replays; anything else
  -- is key reuse.
  select * into v_existing from ops.jev_call_receipt r where r.idempotency_key = p_idempotency_key;
  if found then
    if v_existing.session_id is distinct from p_session_id
       or v_existing.purpose is distinct from p_purpose
       or v_existing.state_sha256 is distinct from p_state_sha256
       or v_existing.questions_sha256 is distinct from p_questions_sha256
       or v_existing.actor_slug is distinct from p_actor_slug then
      raise exception 'jev_call_receipt_key_reuse';
    end if;
    return query select v_existing.receipt_id, v_existing.recorded_at, true;
    return;
  end if;

  insert into ops.jev_call_receipt (
    session_id, purpose, question_ids, facets, model_requested, model_answered,
    state_sha256, questions_sha256, answers_sha256, prompt_sha256, answers, usage,
    actor_slug, idempotency_key
  ) values (
    p_session_id, p_purpose, p_question_ids, coalesce(p_facets, '{}'::text[]),
    p_model_requested, p_model_answered, p_state_sha256, p_questions_sha256,
    p_answers_sha256, p_prompt_sha256, p_answers, p_usage, p_actor_slug, p_idempotency_key
  ) returning * into v_row;

  return query select v_row.receipt_id, v_row.recorded_at, false;
end;
$$;

comment on function ops.record_jev_call_receipt(text,text,text[],text[],text,text,text,text,text,text,jsonb,jsonb,text,text) is
  'Write door for ops.jev_call_receipt: append one receipt of a Jev call the Worker made. recorded_at is the server clock. Idempotent on p_idempotency_key; no update or delete path exists.';

revoke all on function ops.record_jev_call_receipt(text,text,text[],text[],text,text,text,text,text,text,jsonb,jsonb,text,text) from public;
grant execute on function ops.record_jev_call_receipt(text,text,text[],text[],text,text,text,text,text,text,jsonb,jsonb,text,text) to carr_writer;

-- The most recent p_lim receipts for one session at or after p_since, returned
-- oldest first. answers are projected only for build_advisory receipts.
create or replace function ops.read_jev_call_receipts(p_session text, p_since timestamptz, p_lim int)
returns jsonb
language plpgsql
security definer
set search_path = pg_catalog, ops
as $$
begin
  if p_session is null or char_length(p_session) not between 1 and 200 then
    raise exception 'jev_session_id_invalid';
  end if;
  if p_lim is null or p_lim not between 1 and 500 then
    raise exception 'jev_limit_invalid';
  end if;
  return jsonb_build_object(
    'server_now', to_jsonb(clock_timestamp()),
    'receipts', coalesce((
      select jsonb_agg(jsonb_build_object(
          'receipt_id', recent.receipt_id,
          'recorded_at', recent.recorded_at,
          'purpose', recent.purpose,
          'question_ids', to_jsonb(recent.question_ids),
          'facets', to_jsonb(recent.facets),
          'model', recent.model_answered,
          'state_sha256', recent.state_sha256,
          'prompt_sha256', recent.prompt_sha256,
          'answers', case when recent.purpose = 'build_advisory' then recent.answers else null end
        ) order by recent.recorded_at, recent.receipt_id)
      from (
        select r.*
          from ops.jev_call_receipt r
         where r.session_id = p_session
           and (p_since is null or r.recorded_at >= p_since)
         order by r.recorded_at desc, r.receipt_id desc
         limit p_lim
      ) recent
    ), '[]'::jsonb)
  );
end;
$$;

comment on function ops.read_jev_call_receipts(text,timestamp with time zone,integer) is
  'Read door for ops.jev_call_receipt: the most recent p_lim receipts for one session at or after p_since, oldest first, with answers only for build_advisory receipts, plus the server clock.';

revoke all on function ops.read_jev_call_receipts(text,timestamp with time zone,integer) from public;
grant execute on function ops.read_jev_call_receipts(text,timestamp with time zone,integer) to carr_reader, carr_writer;
