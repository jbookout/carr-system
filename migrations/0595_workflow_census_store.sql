-- 0595_workflow_census_store.sql
--
-- The durable, server-attested store for the V5-F09 workflow census: the owner
-- lib/control_plane_workflow_truth_reader.py has reported owed since PR #984
-- under the seam name durable_signed_census_store_seam.
--
-- THE CONTRACT (coordinator decision, 2026-09-24; Jev picked it at 1.0 over
-- waiting for SIEP-14 root trust or adding a database-role credential):
--
--   1. ONE WRITER. ops.record_workflow_census (SECURITY DEFINER, EXECUTE to
--      carr_writer only) is the only insert path, and the record-workflow-census
--      verb is the only caller of it. No app role holds any table privilege.
--      The writing principal is NOT a parameter: it is read from
--      carr.acting_actor_slug, which the deployed Worker sets from the
--      server-derived actor (mcp.js setWriterActorContext) before any handler
--      runs. The session login role is recorded beside it. The break-glass
--      door (local-verb.mjs) never sets that setting, so it cannot write here.
--   2. HASH CHAIN, COMPUTED BY THE DATABASE. Each row carries seq, prev_hash
--      (the previous row's row_hash, null only at seq 1), payload_sha256 (over
--      the canonical JSON of the census) and row_hash, the sha256 of the
--      canonical JSON of
--        {"db_session_principal","payload_sha256","prev_hash","principal",
--         "recorded_at","seq"}
--      with recorded_at rendered in UTC as YYYY-MM-DDTHH:MI:SS.ffffffZ.
--      Canonical JSON is ops.scac_canonical_json (0454): keys sorted by code
--      point, no whitespace -- byte-equal to Python's
--      json.dumps(sort_keys=True, separators=(",",":"), ensure_ascii=False)
--      for strings, integers, booleans, null, arrays and objects. Non-integer
--      numbers are refused at the door so the two renderings cannot disagree.
--      recorded_at is clock_timestamp(); no caller supplies it.
--   3. APPEND-ONLY. A trigger refuses UPDATE, DELETE and TRUNCATE for every
--      role including the owner, and a BEFORE INSERT trigger re-derives the
--      chain fields and refuses any row whose seq, prev_hash, payload_sha256,
--      row_hash or time order does not follow from the current head -- so even
--      an owner-level INSERT that skips the door cannot splice the chain. The
--      same guard refuses a row whose identity columns are not the session's
--      own: db_session_principal must equal session_user and principal must
--      equal carr.acting_actor_slug. An owner who skips the door can therefore
--      only append rows under the owner's own login role, which no reader
--      allowlist names. All three triggers are ENABLE ALWAYS, so they fire
--      under session_replication_role = replica too, and the read door reports
--      their enabled state so a reader refuses a chain whose guards were
--      switched off or re-enabled as ordinary triggers.
--   4. ONE READ DOOR. ops.read_workflow_census (SECURITY DEFINER, EXECUTE to
--      carr_reader and carr_writer) returns the whole chain's metadata, the
--      latest row's payload, the server clock, the three triggers' enabled
--      state and a sha256 of each trigger's function (name and source), so a
--      reader also refuses a guard whose body was replaced while its trigger
--      stayed ENABLE ALWAYS. It verifies NOTHING itself: the reader recomputes every hash on
--      its side and applies the writer allowlist and freshness window from
--      config-as-code.
--   5. AN ANCHOR OUTSIDE THE DATABASE, LINKED STRICTLY. After each committed
--      census write the Worker advances a Durable Object
--      (mcp-server/src/workflow-census-anchor.js) to the new head. The object
--      accepts only the next row of the chain it already holds (seq + 1 whose
--      prev_hash is the anchored row_hash), so a rewritten history can never be
--      carried forward by a later ordinary write. The write door is told the
--      anchored head (p_anchor_seq, p_anchor_row_hash, read by the Worker
--      before the call) and refuses to append unless the database head IS
--      that head: `workflow_census_anchor_gap` when the database is exactly one
--      linked row ahead (a write that committed but was never anchored; the
--      writer's retry of the same idempotency key replays that row and
--      re-advances the anchor), `workflow_census_tampered` for any other
--      disagreement. A replay alone never moves the anchor: the Worker
--      registers each row this door INSERTS (replayed = false) with the
--      anchor as the pending head for its key before the transaction commits,
--      and the anchor advances only to a row equal to that entry. A row the
--      owner wrote under some key and the door merely replays therefore stays
--      an anchor gap (mcp-server/src/workflow-census-anchor.js, R3-C1). The read verb returns the anchor beside the chain and the
--      reader refuses a chain whose head does not match it.
--   6. RE-ANCHOR ON THE RECORD. When the two legitimately disagree (the key of
--      an unanchored row was lost; a database restore), the only way forward
--      is ops.reanchor_workflow_census (EXECUTE to carr_authority only; the
--      record-workflow-census-reanchor verb, partner-authority only). It appends a
--      receipt to ops.workflow_census_reanchor_receipt naming the actor, the
--      verified partner, the reason, the old (anchored) head, the new
--      (database) head and how many rows the anchor never vouched for; the
--      Worker then applies it to the anchor as a compare-and-set on the old
--      head. No carr_writer path reaches it.
--
-- WHAT A VERIFIED CHAIN PROVES, AND WHAT IT DOES NOT. It proves the latest
-- census was recorded under the named principal at the named server time, that
-- the chain is intact as served, and that its head matches the external anchor
-- that only ever advanced one linked row at a time.
-- It does NOT prove the scheduler observations or acceptance rows inside the
-- census are true. It does NOT prove the writer was the scheduled writer job:
-- an allowlisted principal is a token, and a local token is readable by
-- anything running as that user on that machine. And it does NOT resist a
-- coordinated rewrite of both the database (as owner) and the anchor (by
-- deploying different Worker code), nor an owner who also replaces the read
-- door so that it serves a chain other than the stored one; and a partner who
-- re-anchors a forged chain is recorded, not prevented. Those limits are the reason the reader's
-- output is labelled as an attestation, never as a health verdict.
--
-- ATOMIC WITH 0596. The three new SECURITY DEFINER doors carry EXECUTE grants to
-- runtime roles, which moves the live SCAC mutation catalog; applied alone this
-- migration is refused at commit by the deferred epoch trigger. 0596 seals the
-- catalog as the next registry version, and tools/migrate.py declares
-- (0595, 0596) one atomic group.
--
-- No explicit transaction control: tools/migrate.py runs each migration inside
-- its own single transaction.

create table if not exists ops.workflow_census_record (
  seq bigint primary key check (seq >= 1),
  recorded_at timestamptz not null,
  principal text not null check (principal ~ '^[a-z0-9][a-z0-9._-]{0,99}$'),
  db_session_principal text not null check (db_session_principal ~ '^[a-z_][a-z0-9_$]{0,62}$'),
  prev_hash text check (prev_hash ~ '^[0-9a-f]{64}$'),
  payload_sha256 text not null check (payload_sha256 ~ '^[0-9a-f]{64}$'),
  row_hash text not null unique check (row_hash ~ '^[0-9a-f]{64}$'),
  payload jsonb not null check (jsonb_typeof(payload) = 'object'),
  idempotency_key text not null unique check (btrim(idempotency_key) <> '' and char_length(idempotency_key) <= 200),
  constraint workflow_census_record_genesis_has_no_prev
    check ((seq = 1) = (prev_hash is null))
);

comment on table ops.workflow_census_record is
  'Append-only, database-hash-chained record of V5-F09 workflow census snapshots. Written only through ops.record_workflow_census (record-workflow-census verb), read only through ops.read_workflow_census (read-workflow-census verb). principal is the Worker-derived actor slug; recorded_at is the server clock.';

revoke all on table ops.workflow_census_record from public, carr_reader, carr_writer, carr_jobs, carr_authority;

-- The one rendering of recorded_at that enters a hash, shared by the door, the
-- chain guard and the read door so the three can never disagree.
create or replace function ops.workflow_census_time_text(p_at timestamptz)
returns text
language sql immutable strict
set search_path = pg_catalog
as $$
  select to_char(p_at at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"');
$$;

revoke all on function ops.workflow_census_time_text(timestamptz) from public;

create or replace function ops.workflow_census_row_hash(
  p_seq bigint,
  p_recorded_at timestamptz,
  p_principal text,
  p_db_session_principal text,
  p_prev_hash text,
  p_payload_sha256 text
)
returns text
language sql immutable
set search_path = pg_catalog, public, ops
as $$
  select encode(public.digest(convert_to(ops.scac_canonical_json(jsonb_build_object(
    'seq', p_seq,
    'recorded_at', ops.workflow_census_time_text(p_recorded_at),
    'principal', p_principal,
    'db_session_principal', p_db_session_principal,
    'prev_hash', p_prev_hash,
    'payload_sha256', p_payload_sha256)), 'UTF8'), 'sha256'), 'hex');
$$;

revoke all on function ops.workflow_census_row_hash(bigint,timestamptz,text,text,text,text) from public;

create or replace function ops.workflow_census_payload_sha256(p_payload jsonb)
returns text
language sql immutable strict
set search_path = pg_catalog, public, ops
as $$
  select encode(public.digest(convert_to(ops.scac_canonical_json(p_payload), 'UTF8'), 'sha256'), 'hex');
$$;

revoke all on function ops.workflow_census_payload_sha256(jsonb) from public;

create or replace function ops.workflow_census_append_only()
returns trigger
language plpgsql
set search_path = pg_catalog, ops
as $$
begin
  raise exception 'ops.% is append-only (% refused)', tg_table_name, tg_op;
end;
$$;

revoke all on function ops.workflow_census_append_only() from public;

drop trigger if exists workflow_census_record_append_only on ops.workflow_census_record;
create trigger workflow_census_record_append_only
before update or delete on ops.workflow_census_record
for each row execute function ops.workflow_census_append_only();

drop trigger if exists workflow_census_record_no_truncate on ops.workflow_census_record;
create trigger workflow_census_record_no_truncate
before truncate on ops.workflow_census_record
for each statement execute function ops.workflow_census_append_only();

-- THE CHAIN GUARD. Every insert, from any role and any path, must extend the
-- current head exactly. It re-derives rather than trusts: a row whose stored
-- digests do not follow from its own fields and the head is refused.
create or replace function ops.workflow_census_chain_guard()
returns trigger
language plpgsql
set search_path = pg_catalog, public, ops
as $$
declare
  v_head ops.workflow_census_record%rowtype;
begin
  -- Identity first: a row may only carry the identity of the session that
  -- inserts it. The door stamps exactly these values, so it always passes;
  -- an owner-level INSERT that names another principal or login role is
  -- refused however well it is hashed.
  if new.db_session_principal is distinct from session_user::text then
    raise exception 'workflow_census_session_principal_forged';
  end if;
  if new.principal is distinct from nullif(current_setting('carr.acting_actor_slug', true), '') then
    raise exception 'workflow_census_principal_forged';
  end if;
  perform pg_advisory_xact_lock(hashtextextended('ops.workflow_census_record', 0));
  select * into v_head from ops.workflow_census_record r order by r.seq desc limit 1;
  if found then
    if new.seq is distinct from v_head.seq + 1 or new.prev_hash is distinct from v_head.row_hash then
      raise exception 'workflow_census_chain_splice_refused';
    end if;
    if new.recorded_at < v_head.recorded_at then
      raise exception 'workflow_census_time_regression_refused';
    end if;
  elsif new.seq is distinct from 1 or new.prev_hash is not null then
    raise exception 'workflow_census_chain_splice_refused';
  end if;
  if new.recorded_at > clock_timestamp() then
    raise exception 'workflow_census_future_time_refused';
  end if;
  if new.payload_sha256 is distinct from ops.workflow_census_payload_sha256(new.payload) then
    raise exception 'workflow_census_payload_digest_mismatch';
  end if;
  if new.row_hash is distinct from ops.workflow_census_row_hash(new.seq, new.recorded_at,
       new.principal, new.db_session_principal, new.prev_hash, new.payload_sha256) then
    raise exception 'workflow_census_row_hash_mismatch';
  end if;
  return new;
end;
$$;

revoke all on function ops.workflow_census_chain_guard() from public;

drop trigger if exists workflow_census_record_chain_guard on ops.workflow_census_record;
create trigger workflow_census_record_chain_guard
before insert on ops.workflow_census_record
for each row execute function ops.workflow_census_chain_guard();

-- ENABLE ALWAYS: the guards fire under session_replication_role = replica as
-- well. The read door reports each trigger's tgenabled; anything but 'A' (for
-- example after DISABLE TRIGGER and a later plain ENABLE TRIGGER, which leaves
-- 'O') makes the reader refuse the chain.
alter table ops.workflow_census_record enable always trigger workflow_census_record_append_only;
alter table ops.workflow_census_record enable always trigger workflow_census_record_no_truncate;
alter table ops.workflow_census_record enable always trigger workflow_census_record_chain_guard;

-- THE WRITE DOOR.
create or replace function ops.record_workflow_census(
  p_payload jsonb,
  p_idempotency_key text,
  p_anchor_seq bigint,
  p_anchor_row_hash text
)
returns table (
  seq bigint,
  recorded_at text,
  principal text,
  row_hash text,
  prev_hash text,
  payload_sha256 text,
  replayed boolean
)
language plpgsql security definer
set search_path = pg_catalog, public, ops
as $$
declare
  v_principal text := nullif(current_setting('carr.acting_actor_slug', true), '');
  v_session text := session_user::text;
  v_existing ops.workflow_census_record%rowtype;
  v_head ops.workflow_census_record%rowtype;
  v_row ops.workflow_census_record%rowtype;
  v_payload_sha text;
  v_now timestamptz;
  v_found boolean;
begin
  if p_idempotency_key is null or btrim(p_idempotency_key) = '' or char_length(p_idempotency_key) > 200 then
    raise exception 'idempotency_key_required';
  end if;
  if v_principal is null or v_principal !~ '^[a-z0-9][a-z0-9._-]{0,99}$' then
    raise exception 'workflow_census_principal_unavailable';
  end if;
  if (p_anchor_seq is null) <> (p_anchor_row_hash is null)
     or (p_anchor_seq is not null and (p_anchor_seq < 1 or p_anchor_row_hash !~ '^[0-9a-f]{64}$')) then
    raise exception 'workflow_census_anchor_invalid';
  end if;
  if p_payload is null or jsonb_typeof(p_payload) <> 'object' then
    raise exception 'workflow_census_payload_invalid';
  end if;
  if p_payload->>'schema_version' is distinct from 'control-plane-workflow-truth.v1'
     or jsonb_typeof(p_payload->'rows') is distinct from 'array'
     or jsonb_typeof(p_payload->'summary') is distinct from 'object' then
    raise exception 'workflow_census_payload_shape_refused';
  end if;
  if octet_length(p_payload::text) > 4194304 then
    raise exception 'workflow_census_payload_too_large';
  end if;
  if jsonb_path_exists(p_payload, 'strict $.** ? (@.type() == "number" && @ != @.floor())') then
    raise exception 'workflow_census_payload_fraction_refused';
  end if;

  v_payload_sha := ops.workflow_census_payload_sha256(p_payload);

  perform pg_advisory_xact_lock(hashtextextended('ops.workflow_census_record', 0));

  select * into v_existing from ops.workflow_census_record r where r.idempotency_key = p_idempotency_key;
  if found then
    if v_existing.payload_sha256 is distinct from v_payload_sha
       or v_existing.principal is distinct from v_principal then
      raise exception 'workflow_census_key_reuse';
    end if;
    return query select v_existing.seq, ops.workflow_census_time_text(v_existing.recorded_at),
      v_existing.principal, v_existing.row_hash, v_existing.prev_hash, v_existing.payload_sha256, true;
    return;
  end if;

  select * into v_head from ops.workflow_census_record r order by r.seq desc limit 1;
  v_found := found;

  -- THE DATABASE HEAD MUST BE THE ANCHORED HEAD. Checked after the replay
  -- branch above, so the writer's retry of a committed-but-unanchored row
  -- still replays it (and the Worker then re-advances the anchor, which
  -- takes it only because the Worker registered that row when it inserted it).
  if p_anchor_seq is null then
    if v_found then
      raise exception 'workflow_census_tampered' using detail = 'anchor_absent_with_rows';
    end if;
  elsif not v_found then
    raise exception 'workflow_census_tampered' using detail = 'chain_missing_behind_anchor';
  elsif v_head.seq = p_anchor_seq and v_head.row_hash = p_anchor_row_hash then
    null;
  elsif v_head.seq = p_anchor_seq + 1 and v_head.prev_hash = p_anchor_row_hash
        and exists (select 1 from ops.workflow_census_record r
                     where r.seq = p_anchor_seq and r.row_hash = p_anchor_row_hash) then
    raise exception 'workflow_census_anchor_gap' using detail = 'database_one_linked_row_ahead_of_anchor';
  else
    raise exception 'workflow_census_tampered' using detail = 'database_head_is_not_anchored_head';
  end if;

  v_now := clock_timestamp();
  if v_found and v_now < v_head.recorded_at then
    raise exception 'workflow_census_time_regression_refused';
  end if;

  v_row.seq := coalesce(v_head.seq, 0) + 1;
  v_row.recorded_at := v_now;
  v_row.principal := v_principal;
  v_row.db_session_principal := v_session;
  v_row.prev_hash := v_head.row_hash;
  v_row.payload_sha256 := v_payload_sha;
  v_row.payload := p_payload;
  v_row.idempotency_key := p_idempotency_key;
  v_row.row_hash := ops.workflow_census_row_hash(v_row.seq, v_row.recorded_at, v_row.principal,
    v_row.db_session_principal, v_row.prev_hash, v_row.payload_sha256);

  insert into ops.workflow_census_record values (v_row.*);

  return query select v_row.seq, ops.workflow_census_time_text(v_row.recorded_at), v_row.principal,
    v_row.row_hash, v_row.prev_hash, v_row.payload_sha256, false;
end;
$$;

comment on function ops.record_workflow_census(jsonb,text,bigint,text) is
  'Write door for ops.workflow_census_record: append one V5-F09 census snapshot to the hash chain. The principal is the Worker-set carr.acting_actor_slug, never a parameter; recorded_at is the server clock; the chain fields are computed here and re-checked by the chain guard. Idempotent on p_idempotency_key. Refuses to append unless the database head is the anchored head the Worker passes (workflow_census_anchor_gap / workflow_census_tampered).';

revoke all on function ops.record_workflow_census(jsonb,text,bigint,text) from public;
grant execute on function ops.record_workflow_census(jsonb,text,bigint,text) to carr_writer;

-- THE READ DOOR. Chain metadata for every row (oldest first), the latest row's
-- payload, the server clock, and the guards' enabled state. p_max_rows bounds the answer; when the chain is
-- longer the answer says so and a reader must refuse to call it verified.
create or replace function ops.read_workflow_census(p_max_rows int)
returns jsonb
language plpgsql security definer
set search_path = pg_catalog, ops
as $$
declare
  v_count bigint;
begin
  if p_max_rows is null or p_max_rows not between 1 and 100000 then
    raise exception 'workflow_census_max_rows_invalid';
  end if;
  select count(*) into v_count from ops.workflow_census_record;
  return jsonb_build_object(
    'schema_version', 'workflow-census-chain.v1',
    'server_now', ops.workflow_census_time_text(clock_timestamp()),
    'row_count', v_count,
    'truncated', v_count > p_max_rows,
    'chain', coalesce((
      select jsonb_agg(jsonb_build_object(
          'seq', c.seq,
          'recorded_at', ops.workflow_census_time_text(c.recorded_at),
          'principal', c.principal,
          'db_session_principal', c.db_session_principal,
          'prev_hash', c.prev_hash,
          'payload_sha256', c.payload_sha256,
          'row_hash', c.row_hash) order by c.seq)
        from (select * from ops.workflow_census_record r order by r.seq desc limit p_max_rows) c
    ), '[]'::jsonb),
    'latest_payload', (select r.payload from ops.workflow_census_record r order by r.seq desc limit 1),
    'guards', coalesce((
      select jsonb_object_agg(t.tgname, t.tgenabled::text)
        from pg_catalog.pg_trigger t
       where t.tgrelid = 'ops.workflow_census_record'::regclass and not t.tgisinternal
    ), '{}'::jsonb),
    -- sha256 of "<schema>.<function name>\n<source>" for the function each
    -- trigger calls: a guard whose body was replaced (or a trigger re-pointed
    -- to another function) no longer matches the digest pinned in
    -- ops/config/workflow-census-attestation.v1.json.
    'guard_functions', coalesce((
      select jsonb_object_agg(t.tgname, encode(public.digest(convert_to(
               n.nspname || '.' || p.proname || chr(10) || p.prosrc, 'UTF8'), 'sha256'), 'hex'))
        from pg_catalog.pg_trigger t
        join pg_catalog.pg_proc p on p.oid = t.tgfoid
        join pg_catalog.pg_namespace n on n.oid = p.pronamespace
       where t.tgrelid = 'ops.workflow_census_record'::regclass and not t.tgisinternal
    ), '{}'::jsonb)
  );
end;
$$;

comment on function ops.read_workflow_census(integer) is
  'Read door for ops.workflow_census_record: every chain row''s metadata oldest first (bounded by p_max_rows, with truncated set when the chain is longer), the latest census payload, the server clock and each trigger''s tgenabled state. Verifies nothing: the reader recomputes the chain.';

revoke all on function ops.read_workflow_census(integer) from public;
grant execute on function ops.read_workflow_census(integer) to carr_reader, carr_writer;

-- THE RE-ANCHOR RECEIPT (contract point 6). Append-only like the chain, under
-- the same trigger function, ENABLE ALWAYS.
create table if not exists ops.workflow_census_reanchor_receipt (
  receipt_id uuid primary key default gen_random_uuid(),
  recorded_at timestamptz not null,
  actor text not null check (actor ~ '^[a-z0-9][a-z0-9._-]{0,99}$'),
  verified_partner text not null check (verified_partner ~ '^[a-z0-9][a-z0-9._-]{0,99}$'),
  db_session_principal text not null,
  reason text not null check (btrim(reason) <> '' and char_length(reason) <= 1000),
  old_seq bigint check (old_seq >= 1),
  old_row_hash text check (old_row_hash ~ '^[0-9a-f]{64}$'),
  new_seq bigint check (new_seq >= 1),
  new_row_hash text check (new_row_hash ~ '^[0-9a-f]{64}$'),
  rows_reattested bigint not null check (rows_reattested >= 0),
  idempotency_key text not null unique check (btrim(idempotency_key) <> '' and char_length(idempotency_key) <= 200),
  constraint workflow_census_reanchor_old_pair check ((old_seq is null) = (old_row_hash is null)),
  constraint workflow_census_reanchor_new_pair check ((new_seq is null) = (new_row_hash is null)),
  constraint workflow_census_reanchor_moves
    check (old_seq is distinct from new_seq or old_row_hash is distinct from new_row_hash)
);

comment on table ops.workflow_census_reanchor_receipt is
  'Append-only receipts for re-anchoring the V5-F09 census anchor to the database head: who (actor and verified partner), why, the old anchored head, the new database head, and how many rows the anchor never vouched for. Written only through ops.reanchor_workflow_census (carr_authority).';

revoke all on table ops.workflow_census_reanchor_receipt from public, carr_reader, carr_writer, carr_jobs, carr_authority;

drop trigger if exists workflow_census_reanchor_receipt_append_only on ops.workflow_census_reanchor_receipt;
create trigger workflow_census_reanchor_receipt_append_only
before update or delete on ops.workflow_census_reanchor_receipt
for each row execute function ops.workflow_census_append_only();

drop trigger if exists workflow_census_reanchor_receipt_no_truncate on ops.workflow_census_reanchor_receipt;
create trigger workflow_census_reanchor_receipt_no_truncate
before truncate on ops.workflow_census_reanchor_receipt
for each statement execute function ops.workflow_census_append_only();

alter table ops.workflow_census_reanchor_receipt enable always trigger workflow_census_reanchor_receipt_append_only;
alter table ops.workflow_census_reanchor_receipt enable always trigger workflow_census_reanchor_receipt_no_truncate;

-- THE RE-ANCHOR DOOR. The Worker passes the head it read from the anchor
-- (p_anchor_*); the partner passes the database head they reviewed and accept
-- (p_accept_*), which must still be the database head. rows_reattested counts
-- the rows the anchor never vouched for: the rows after the anchored head when
-- that head is still in the chain unchanged, otherwise every row.
create or replace function ops.reanchor_workflow_census(
  p_anchor_seq bigint,
  p_anchor_row_hash text,
  p_accept_seq bigint,
  p_accept_row_hash text,
  p_reason text,
  p_idempotency_key text
)
returns table (
  receipt_id uuid,
  recorded_at text,
  actor text,
  verified_partner text,
  reason text,
  old_seq bigint,
  old_row_hash text,
  new_seq bigint,
  new_row_hash text,
  rows_reattested bigint,
  replayed boolean
)
language plpgsql security definer
set search_path = pg_catalog, ops
as $$
declare
  v_actor text := nullif(current_setting('carr.acting_actor_slug', true), '');
  v_partner text := nullif(current_setting('carr.verified_human_actor_slug', true), '');
  v_existing ops.workflow_census_reanchor_receipt%rowtype;
  v_head ops.workflow_census_record%rowtype;
  v_found boolean;
  v_row ops.workflow_census_reanchor_receipt%rowtype;
begin
  if p_idempotency_key is null or btrim(p_idempotency_key) = '' or char_length(p_idempotency_key) > 200 then
    raise exception 'idempotency_key_required';
  end if;
  if v_actor is null or v_actor !~ '^[a-z0-9][a-z0-9._-]{0,99}$' then
    raise exception 'workflow_census_principal_unavailable';
  end if;
  if v_partner is null or v_partner !~ '^[a-z0-9][a-z0-9._-]{0,99}$' then
    raise exception 'workflow_census_reanchor_requires_partner';
  end if;
  if p_reason is null or btrim(p_reason) = '' or char_length(p_reason) > 1000 then
    raise exception 'workflow_census_reanchor_reason_required';
  end if;
  if (p_anchor_seq is null) <> (p_anchor_row_hash is null)
     or (p_anchor_seq is not null and (p_anchor_seq < 1 or p_anchor_row_hash !~ '^[0-9a-f]{64}$'))
     or (p_accept_seq is null) <> (p_accept_row_hash is null)
     or (p_accept_seq is not null and (p_accept_seq < 1 or p_accept_row_hash !~ '^[0-9a-f]{64}$')) then
    raise exception 'workflow_census_anchor_invalid';
  end if;

  perform pg_advisory_xact_lock(hashtextextended('ops.workflow_census_record', 0));

  select * into v_existing from ops.workflow_census_reanchor_receipt r where r.idempotency_key = p_idempotency_key;
  if found then
    if v_existing.old_seq is distinct from p_anchor_seq or v_existing.old_row_hash is distinct from p_anchor_row_hash
       or v_existing.new_seq is distinct from p_accept_seq or v_existing.new_row_hash is distinct from p_accept_row_hash
       or v_existing.reason is distinct from p_reason or v_existing.actor is distinct from v_actor then
      raise exception 'workflow_census_key_reuse';
    end if;
    return query select v_existing.receipt_id, ops.workflow_census_time_text(v_existing.recorded_at),
      v_existing.actor, v_existing.verified_partner, v_existing.reason, v_existing.old_seq,
      v_existing.old_row_hash, v_existing.new_seq, v_existing.new_row_hash, v_existing.rows_reattested, true;
    return;
  end if;

  select * into v_head from ops.workflow_census_record r order by r.seq desc limit 1;
  v_found := found;
  if (v_found and (p_accept_seq is distinct from v_head.seq or p_accept_row_hash is distinct from v_head.row_hash))
     or (not v_found and p_accept_seq is not null) then
    raise exception 'workflow_census_reanchor_head_moved';
  end if;
  if p_anchor_seq is not distinct from p_accept_seq and p_anchor_row_hash is not distinct from p_accept_row_hash then
    raise exception 'workflow_census_reanchor_not_needed';
  end if;

  v_row.receipt_id := gen_random_uuid();
  v_row.recorded_at := clock_timestamp();
  v_row.actor := v_actor;
  v_row.verified_partner := v_partner;
  v_row.db_session_principal := session_user::text;
  v_row.reason := p_reason;
  v_row.old_seq := p_anchor_seq;
  v_row.old_row_hash := p_anchor_row_hash;
  v_row.new_seq := p_accept_seq;
  v_row.new_row_hash := p_accept_row_hash;
  v_row.idempotency_key := p_idempotency_key;
  if p_anchor_seq is not null and exists (select 1 from ops.workflow_census_record r
       where r.seq = p_anchor_seq and r.row_hash = p_anchor_row_hash) then
    v_row.rows_reattested := coalesce(p_accept_seq, 0) - p_anchor_seq;
  else
    select count(*) into v_row.rows_reattested from ops.workflow_census_record;
  end if;

  insert into ops.workflow_census_reanchor_receipt values (v_row.*);

  return query select v_row.receipt_id, ops.workflow_census_time_text(v_row.recorded_at), v_row.actor,
    v_row.verified_partner, v_row.reason, v_row.old_seq, v_row.old_row_hash, v_row.new_seq,
    v_row.new_row_hash, v_row.rows_reattested, false;
end;
$$;

comment on function ops.reanchor_workflow_census(bigint,text,bigint,text,text,text) is
  'Re-anchor door for the V5-F09 census anchor (contract point 6): appends one receipt naming the actor, the verified partner, the reason, the old anchored head, the accepted database head and the rows re-attested; the Worker then applies it to the anchor as a compare-and-set. EXECUTE to carr_authority only.';

revoke all on function ops.reanchor_workflow_census(bigint,text,bigint,text,text,text) from public;
grant execute on function ops.reanchor_workflow_census(bigint,text,bigint,text,text,text) to carr_authority;
