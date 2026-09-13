-- 0505_gate_zero_tagged_digest_and_candidate_reads.sql
-- THREE FORWARD-ONLY CORRECTIONS TO THE GATE ZERO OUTCOME SURFACE, each one a
-- blocking finding of the outside review that REFUSED the third Worker release
-- candidate 79dc6e5374072dff8f92acb24d31e5c447d13b0c on 2026-09-13.
--
-- WHY A NEW FILE AND NOT AN EDIT. Migrations 0502, 0503 and 0504 are APPLIED to
-- Production -- highest applied 0504, applied count 346 -- and the runner records
-- and re-checks each applied file's sha256. An applied file is history; a
-- correction is a new number. Nothing below touches those three files.
--
-- ── (1) THE TAGGED DIGEST (review finding 2) ─────────────────────────────────
--
-- 0502 recorded the outcome digest as a plain canonical-JSON sha256 over the
-- receipt object, and said so in its own comment as a NAMED ASSUMPTION: r7's
-- `receipt_payload_digest_rule` named domain tags for four schemas and was
-- SILENT on `consumer-gate-receipt.v1`, so the plain reading was taken and put on
-- the surface where one line could overturn it.
--
-- IT WAS OVERTURNED. Act 5 of accepted plan PLAN-a5059eb52474-v3
-- (sha256:a5059eb5247418d150a261c5501dcfa38b69b57512c2d44f02356b92c07893bc)
-- amended r7 on 2026-09-13 to declare this schema's payload digest as the TAGGED
-- preimage -- sha256 over the RFC8785/JCS serialization of the two-element array
-- whose first element is the domain-tag string `consumer-gate-receipt.v1` and
-- whose second is the twenty-one-field receipt -- and to state that a plain
-- digest over the receipt alone does NOT satisfy it. All 62
-- normalized-r7-design chunks were resealed and the packet is pinned at
-- 4379c60e9a4fefbcf044f4bc5a34e5a95c90f77b9adf348b6da7475e17d5e6d7. The plan's
-- own planned check 18 names either reader computing digest(receipt) as a
-- FAILURE CONDITION.
--
-- NO BACKFILL IS OWED AND NONE IS ATTEMPTED. ops.gate_zero_read_only_outcome is
-- empty in Production: the live run that writes the first row is act 13 of the
-- same plan and has not happened. A proof below asserts that emptiness rather
-- than assuming it, because a CREATE OR REPLACE that silently left rows carrying
-- a superseded recipe beside rows carrying the declared one is exactly the
-- divergence the readback exists to catch.
--
-- WHAT IS DELIBERATELY *NOT* TAGGED. ops.gate_zero_outcome_candidate_digest is
-- computed over a PROJECTION of the receipt -- five per-run values removed -- and
-- that projection is not a consumer-gate-receipt.v1, so r7's rule does not speak
-- about it. Tagging a projection with the schema name of the thing it is not
-- would be the misstatement. It stays plain here and plain in the gateway as an
-- informational comparison aid; the candidate digest is the row arbiter.
--
-- ── (2) THE RETRY CONVERGES (review finding 3) ───────────────────────────────
--
-- The outcome row and its audit event are written by two different login roles
-- and therefore two different transactions; the seat's commits first. An outer
-- failure after it leaves an outcome with no event. A normal retry necessarily
-- carries a new session and instant, and its evidence may have moved, so exact
-- receipt matching strands that row. The fallback therefore returns the
-- immutable recorded row for the candidate and lets the outer transaction heal
-- the event. It reports convergence explicitly; nothing is replaced.
--
-- ── (3) THE READS A CANDIDATE FILING NEEDS (review finding 1) ────────────────
--
-- Standing-rule amendment 9(c) requires the release-candidate record to be filed
-- by the deploy wrapper under the AUTHORITY identity, because the Gate Zero seam
-- store reads a subject maker only out of a row whose GENERATED
-- `maker_authority_verified` column is true -- which 0504 makes true exactly when
-- the filing login was carr_authority_joe or carr_authority_dell. Filed on the
-- ledger writer, as the candidate was, 0504 marks the row unauthenticated and the
-- store correctly ignores it. That is the state the reviewer refused.
--
-- 0503 ALREADY ADMITTED THE WRITE HALF: `grant insert on table ops.release to
-- carr_authority`, inside the v26 mutation-registry successor that seals it,
-- plus the two column-scoped selects ops.release's INVOKER-RIGHTS completion
-- trigger reads. What was missing is the two reads the FILING COMMAND PATH
-- itself performs, and measured on Production on 2026-09-13 both were absent:
--
--     has_table_privilege('carr_authority','ops.service','select')  ->  false
--     has_table_privilege('carr_authority','ops.release','select')  ->  false
--
--   * tools/ops-record.py's service_id() runs `select id from ops.service where
--     key = %s` to resolve --service to its foreign key.
--   * its CANDIDATE_INSERT ends `returning id, release_key, maker_actor,
--     maker_session_user, maker_authority_verified`, and RETURNING requires
--     SELECT on the columns it returns.
--
-- Without both, moving the command onto the authority connection replaces a
-- silently-ignored row with a hard permission failure. They are granted below,
-- COLUMN-SCOPED to exactly those columns and nothing wider, and the width is
-- asserted rather than asserted-about.
--
-- NEITHER GRANT IS A NEW INGRESS, and that is measured rather than hoped: the
-- SIEP-11 mutation census counts only the ROW-CHANGING privileges, so a select
-- moves no category and this file is not a mutation-registry successor. 0503's
-- own header states the same rule for the two selects it carried. A proof below
-- re-asserts that this migration adds no update or delete anywhere.

-- NO EXPLICIT TRANSACTION CONTROL: from 0339 onward tools/migrate.py wraps the
-- whole file in ONE transaction, proof blocks included, so a failing proof below
-- rolls this DDL back with it.

-- ── (1) the digest r7 declares ───────────────────────────────────────────────
-- THE PREIMAGE IS THE TWO-ELEMENT ARRAY, built here with jsonb_build_array so
-- the SQL side and artifact-trust.js's canonicalJson serialize the same
-- structure: ops.portfolio_canonical_json preserves array order and renders the
-- tag through to_jsonb, which is byte-for-byte what
-- `canonicalJson(["consumer-gate-receipt.v1", receipt])` produces.
create or replace function ops.gate_zero_outcome_digest(p_receipt jsonb)
returns text language sql immutable
set search_path = pg_catalog, ops, public
as $$
  select 'sha256:' || encode(public.digest(convert_to(
    ops.portfolio_canonical_json(
      jsonb_build_array('consumer-gate-receipt.v1'::text, p_receipt)),
    'UTF8'), 'sha256'), 'hex')
$$;

comment on function ops.gate_zero_outcome_digest(jsonb) is
  'sha256 over the canonical JSON of ["consumer-gate-receipt.v1", <receipt>] -- the TAGGED preimage r7''s receipt_payload_digest_rule declares for this schema (amended 2026-09-13, packet 4379c60e9a4fefbcf044f4bc5a34e5a95c90f77b9adf348b6da7475e17d5e6d7). The same bytes artifact-trust.js digest([schema, receipt]) hashes. Replaces 0502''s plain digest over the receipt object, which that rule now states does not satisfy it.';

comment on column ops.gate_zero_read_only_outcome.outcome_digest is
  'Recomputed from `receipt` with ops.gate_zero_outcome_digest, which is sha256 over the canonical JSON of the TAGGED two-element array ["consumer-gate-receipt.v1", receipt] as r7''s receipt_payload_digest_rule declares (amended 2026-09-13). Never supplied by a caller. ops.portfolio_canonical_json matches artifact-trust.js canonicalJson byte for byte, over that same array.';

comment on column ops.gate_zero_read_only_outcome.candidate_scoped_digest is
  'Informational comparison value: ops.gate_zero_outcome_candidate_digest(receipt), the canonical-JSON sha256 over the receipt minus observed_at, ttl_expires_at and each identity''s per-call session_ref. It is not evidence and not an admission or idempotency key. The unique candidate_digest column arbitrates one immutable row per candidate; a later call returns that recorded row unchanged and reports whether its offered receipt differed.';

-- Forward-correct 0502's installed descriptions as well as its behavior. The
-- functions still compute the same values; only their stated authority changes.
comment on function ops.gate_zero_outcome_candidate_projection(jsonb) is
  'One consumer-gate-receipt.v1 reduced by removing observed_at, ttl_expires_at and each identity''s per-call session_ref. It is an informational view used to explain differences between offered and recorded observations. It is not evidence and does not admit or refuse a retry; candidate_digest arbitrates the immutable row.';

comment on function ops.gate_zero_outcome_candidate_digest(jsonb) is
  'Canonical-JSON sha256 over ops.gate_zero_outcome_candidate_projection(receipt). This informational digest can show whether non-time, non-session evidence differs between an offered and recorded observation. It is not evidence or an idempotency key and does not admit or refuse a retry; candidate_digest arbitrates the immutable row.';

-- THE SQL CONSUMER RECOMPUTES TOO. 0502's reader selected the stored digest and
-- returned it unchanged. The accepted plan requires both readers to derive the
-- tagged value from the stored receipt and compare before answering, so this
-- forward replacement keeps 0502's currentness rule and closed return shape but
-- refuses an internally inconsistent row.
create or replace function ops.benchmark_gate_zero_outcome()
returns jsonb language plpgsql stable security definer
set search_path = pg_catalog, ops, public
as $$
declare
  v_row ops.gate_zero_read_only_outcome%rowtype;
  v_any boolean;
  v_recomputed_digest text;
begin
  select * into v_row from ops.gate_zero_read_only_outcome
   where status = 'pass' and ttl_expires_at > now()
   order by observed_at desc, outcome_digest collate "C" desc
   limit 1;
  if not found then
    select exists (select 1 from ops.gate_zero_read_only_outcome) into v_any;
    if v_any then
      raise exception 'benchmark acceptance requires a current passing Gate Zero read-only outcome; every outcome recorded here is non-passing or past its expiry. No caller-supplied, configured or synthetic Gate Zero outcome is accepted.';
    end if;
    raise exception 'benchmark acceptance requires an authenticated Gate Zero read-only outcome binding, and none has been recorded here yet. The record exists (ops.gate_zero_read_only_outcome) and the independent oracle seat writes it; until it does, acceptance fails closed. No caller-supplied, configured or synthetic Gate Zero outcome is accepted.';
  end if;

  v_recomputed_digest := ops.gate_zero_outcome_digest(v_row.receipt);
  if v_row.outcome_digest <> v_recomputed_digest then
    raise exception 'Gate Zero outcome digest divergence: stored %, recomputed % from the tagged consumer-gate-receipt.v1 receipt',
      v_row.outcome_digest, v_recomputed_digest;
  end if;

  return jsonb_build_object(
    'step_ref', v_row.step_ref,
    'outcome_digest', v_row.outcome_digest,
    'observed_at', to_char(v_row.observed_at at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'));
end;
$$;

comment on function ops.benchmark_gate_zero_outcome() is
  'PRIVATE reader for the latest passing, unexpired Gate Zero outcome. Before returning the closed { step_ref, outcome_digest, observed_at } object it recomputes sha256 over canonical JSON ["consumer-gate-receipt.v1", receipt] and refuses if that tagged digest differs from the stored value.';

-- ── (2) the writer, with the fallback that converges ─────────────────────────
-- LIFTED FROM 0502 RATHER THAN RETYPED. Every line below except the fallback
-- branch is the text migration 0502 applied to Production; only the fallback
-- answer changes so a retry can heal the separately committed audit event.
-- The signature, the SECURITY DEFINER property, the owner and the EXECUTE grant
-- are unchanged, so this admits no capability: CREATE OR REPLACE keeps the
-- function's ACL, and the carr_gate_zero_producer-only closure 0502 established
-- stands untouched.

create or replace function ops.gate_zero_record_read_only_outcome(
  p_idempotency_key uuid,
  p_receipt jsonb)
returns uuid language plpgsql security definer
set search_path = pg_catalog, ops, public
as $$
declare
  v_actor uuid; v_slug text; v_seat text; v_id uuid;
  v_existing ops.gate_zero_read_only_outcome%rowtype;
  v_digest text; v_candidate_scoped_digest text;
  v_field text; v_keys integer; v_identity jsonb; v_identity_keys integer;
begin
  if p_idempotency_key is null then
    raise exception 'recording a Gate Zero read-only outcome requires an idempotency key';
  end if;
  if p_receipt is null or jsonb_typeof(p_receipt) <> 'object' then
    raise exception 'recording a Gate Zero read-only outcome requires one consumer-gate-receipt.v1 object';
  end if;

  -- AUTHORITY FIRST, so nothing below it can be reached by a transaction that
  -- may not write here at all.
  v_actor := ops.gate_zero_producer_actor_id();
  v_seat := ops.gate_zero_producer_seat_holder_ref();
  v_slug := split_part(v_seat, ':', 2);

  -- CLOSED SCHEMA. consumer-gate-receipt.v1 sets additional_properties false and
  -- names twenty-one required fields. An unknown field denies, which is r7's own
  -- rule ("Unknown or missing payload fields deny") and is what makes the digest
  -- a statement about a known shape.
  foreach v_field in array array[
    'gate_id', 'receipt_producer_step_ref', 'subject_digest', 'candidate_digest',
    'policy_digest', 'environment_manifest_digest', 'subject_environment', 'evidence_scope',
    'subject_maker_identity', 'producer_identity', 'evaluator_identity', 'producer_role',
    'independent_oracle_ref', 'oracle_version', 'evidence_ref', 'fixture_set_digest',
    'observed_at', 'ttl_expires_at', 'status', 'comparator', 'negative_admission_result'
  ] loop
    if not (p_receipt ? v_field) then
      raise exception 'the Gate Zero receipt is missing the required consumer-gate-receipt.v1 field %', v_field;
    end if;
  end loop;
  select count(*) into v_keys from jsonb_object_keys(p_receipt);
  if v_keys <> 21 then
    raise exception 'consumer-gate-receipt.v1 is a closed schema: the Gate Zero receipt carries % fields rather than 21', v_keys;
  end if;

  -- THE THREE IDENTITIES, AND r7's SHAPE FOR THEM IS CLOSED TOO.
  -- `authenticated-receipt-identity.v1` sets additional_properties false, names
  -- exactly actor_id, session_ref and authority_class, and gives session_ref a
  -- LOWERCASE pattern with a minimum length. All three clauses are checked here,
  -- and all three are checked again in the gateway: a fourth key inside an
  -- identity object would otherwise travel into the digest unread, which would
  -- make the digest a statement about an open shape.
  --
  -- WHAT THIS COPY CANNOT CHECK, said plainly rather than implied: the record
  -- layer does not know which correlation id the gateway derived for this call,
  -- so "these identities are THIS call's" is the gateway's clause alone
  -- (gate-zero-outcome-store.v5.js, against identity.js's authenticated call).
  -- What is checkable here is the SHAPE, the seat and the self-review rule, and
  -- those are checked here because a writer connection opened outside the
  -- gateway reaches this function and not that one.
  foreach v_field in array array['subject_maker_identity', 'producer_identity', 'evaluator_identity'] loop
    v_identity := p_receipt -> v_field;
    if jsonb_typeof(v_identity) <> 'object' then
      raise exception 'the Gate Zero receipt field % is not an authenticated-receipt-identity.v1 object', v_field;
    end if;
    select count(*) into v_identity_keys from jsonb_object_keys(v_identity);
    if v_identity_keys <> 3
       or not (v_identity ? 'actor_id')
       or not (v_identity ? 'session_ref')
       or not (v_identity ? 'authority_class') then
      raise exception 'authenticated-receipt-identity.v1 is a closed schema: the Gate Zero receipt field % carries % fields rather than exactly actor_id, session_ref and authority_class',
        v_field, v_identity_keys;
    end if;
    if coalesce(v_identity ->> 'actor_id', '') = ''
       or coalesce(v_identity ->> 'authority_class', '') = '' then
      raise exception 'the Gate Zero receipt field % has an empty actor_id or authority_class', v_field;
    end if;
    -- r7's own pattern, character for character. Lowercase only, and at least
    -- nine characters after `session:`.
    if coalesce(v_identity ->> 'session_ref', '') !~ '^session:[a-z0-9][a-z0-9:._/-]{8,199}$' then
      raise exception 'the Gate Zero receipt field % has a session_ref that is not r7''s authenticated-receipt-identity.v1 pattern: %',
        v_field, coalesce(v_identity ->> 'session_ref', '');
    end if;
  end loop;
  if p_receipt -> 'producer_identity' ->> 'actor_id' <> v_slug
     or p_receipt -> 'evaluator_identity' ->> 'actor_id' <> v_slug then
    raise exception 'the Gate Zero receipt names a producer or evaluator other than the staffed seat lane %', v_slug;
  end if;
  if p_receipt -> 'producer_identity' ->> 'authority_class' <> 'review_agent'
     or p_receipt -> 'evaluator_identity' ->> 'authority_class' <> 'review_agent' then
    raise exception 'the Gate Zero receipt names a producer or evaluator authority class other than review_agent';
  end if;
  -- SAME-ACTOR SELF-REVIEW DENIES, in both dimensions r7 names.
  if p_receipt -> 'subject_maker_identity' ->> 'actor_id' = v_slug then
    raise exception 'the Gate Zero receipt names the oracle seat as the maker of its own subject; r7 requires the subject maker to differ from the evaluator';
  end if;
  if p_receipt -> 'subject_maker_identity' ->> 'session_ref'
     = p_receipt -> 'evaluator_identity' ->> 'session_ref' then
    raise exception 'the Gate Zero receipt names one session as both subject maker and evaluator';
  end if;

  -- IDEMPOTENT ON THE CANDIDATE, ATOMICALLY, and the atomicity is the whole
  -- point of the shape (2026-09-13, PR 1014 correction).
  --
  -- THE DEFECT THIS REPLACES. The first draft looked the candidate up, found
  -- nothing, and then inserted. Two runs of the same candidate arriving at once
  -- both missed the lookup, and the loser of the race got a bare
  -- unique_violation on candidate_digest instead of the durable row -- a retry
  -- policy that says "every run kept, a retry collapses onto the row that
  -- exists" turning into an error whenever two writers actually retried at once.
  --
  -- THE SHAPE THAT CANNOT RACE. ONE statement does the insert with the candidate
  -- key as its arbiter, so the conflict is resolved by the index rather than by
  -- a window between two statements: a concurrent inserter BLOCKS on the
  -- speculative insertion, and when the first committer commits the second takes
  -- the DO NOTHING branch and reads the committed row in the fallback select --
  -- which sees it, because each statement in READ COMMITTED takes a fresh
  -- snapshot. If the first transaction rolls back instead, the second inserts.
  -- Either way both callers receive the same durable row and neither receives an
  -- error.
  --
  -- A later receipt cannot replace the recorded row. If its bytes differ, the
  -- fallback returns the first row and logs both full and projected digests so
  -- the caller can report the convergence rather than mistake it for a write.
  insert into ops.gate_zero_read_only_outcome (
    idempotency_key, step_ref, receipt_producer_step_ref, gate_id, receipt_schema,
    producer_role, independent_oracle_ref, oracle_version, evidence_scope,
    subject_environment, negative_admission_result, producing_seat_ref, producing_actor_id,
    candidate_digest, subject_digest, policy_digest, environment_manifest_digest,
    fixture_set_digest, evidence_ref, receipt, outcome_digest, candidate_scoped_digest,
    status, comparator, observed_at, ttl_expires_at)
  values (
    p_idempotency_key,
    'step:gate-zero-read-only-outcome',
    p_receipt ->> 'receipt_producer_step_ref',
    p_receipt ->> 'gate_id',
    'consumer-gate-receipt.v1',
    p_receipt ->> 'producer_role',
    p_receipt ->> 'independent_oracle_ref',
    p_receipt ->> 'oracle_version',
    p_receipt ->> 'evidence_scope',
    p_receipt ->> 'subject_environment',
    p_receipt ->> 'negative_admission_result',
    v_seat,
    v_actor,
    p_receipt ->> 'candidate_digest',
    p_receipt ->> 'subject_digest',
    p_receipt ->> 'policy_digest',
    p_receipt ->> 'environment_manifest_digest',
    p_receipt ->> 'fixture_set_digest',
    p_receipt ->> 'evidence_ref',
    p_receipt,
    ops.gate_zero_outcome_digest(p_receipt),
    ops.gate_zero_outcome_candidate_digest(p_receipt),
    p_receipt ->> 'status',
    p_receipt ->> 'comparator',
    (p_receipt ->> 'observed_at')::timestamptz,
    (p_receipt ->> 'ttl_expires_at')::timestamptz)
  on conflict (candidate_digest) do nothing
  returning id into v_id;
  if v_id is not null then
    return v_id;
  end if;

  -- THE FALLBACK, reached only when the arbiter index already held this
  -- candidate. It is a separate statement and therefore a fresh snapshot, which
  -- is what lets it see a row a concurrent transaction committed while this
  -- insert was blocked on it.
  select * into v_existing from ops.gate_zero_read_only_outcome
   where candidate_digest = p_receipt ->> 'candidate_digest';
  if not found then
    -- Neither inserted nor found: the only way here is the row having been
    -- removed between the two statements, which the append-only triggers refuse.
    -- It is reported rather than retried, because a writer that cannot explain
    -- its own outcome must not invent one.
    raise exception 'the Gate Zero outcome for candidate % was neither inserted nor found; the record layer is in a state this writer cannot account for',
      p_receipt ->> 'candidate_digest';
  end if;
  -- RETURN THE IMMUTABLE FIRST ROW UNCONDITIONALLY. The outcome and its audit
  -- event use different authenticated connections and transactions; the seat
  -- commits first, so a later outer failure can leave this row eventless. Every
  -- normal retry has new per-call identity and time bytes, and its evidence may
  -- also have moved. Refusing those bytes prevents the retry from reaching the
  -- event write. Returning the existing row changes no state here: the gateway
  -- recomputes and labels recorded versus offered digests before healing exactly
  -- one event under an advisory lock.
  v_digest := ops.gate_zero_outcome_digest(p_receipt);
  v_candidate_scoped_digest := ops.gate_zero_outcome_candidate_digest(p_receipt);
  if v_existing.outcome_digest <> v_digest then
    raise notice 'candidate % already has an immutable outcome; returning it unchanged (recorded full %, offered full %, recorded projection %, offered projection %)',
      v_existing.candidate_digest, v_existing.outcome_digest, v_digest,
      v_existing.candidate_scoped_digest, v_candidate_scoped_digest;
  end if;
  return v_existing.id;
end;
$$;

comment on function ops.gate_zero_record_read_only_outcome(uuid,jsonb) is
  'The only way to record a Gate Zero read-only outcome. The producing seat, actor and outcome digest are derived; the receipt and idempotency key are the only parameters. It admits only the staffed non-human oracle seat and validates the closed receipt and identity schemas. One insert is arbitrated by candidate_digest; its fallback always returns the immutable first row, including when later evidence, identity or time bytes differ, so the caller can report convergence and heal a missing outer audit event. No receipt is replaced. outcome_digest is the tagged digest r7 declares; candidate_scoped_digest is informational only.';

-- ── (3) the two column-scoped reads a candidate filing performs ──────────────
-- ops.service, read by tools/ops-record.py's service_id() to resolve --service.
-- Two columns: the one it filters on and the one it returns.
grant select (id, key) on table ops.service to carr_authority;

-- ops.release, read by the same command's RETURNING clause. Five columns, and
-- they are the five that clause names -- not the table, because the authority
-- bundle has never held a whole-table select here and this file is not the place
-- to give it one. maker_authority_verified is among them and is GENERATED, so
-- reading it back is the only thing any role can do with it.
grant select (id, release_key, maker_actor, maker_session_user,
              maker_authority_verified)
  on table ops.release to carr_authority;

do $gate_zero_candidate_filing_reads$
declare
  v_col        text;
  v_row_count  integer;
  v_attgenerated char;
  v_acl_probe  text;
  v_forbidden_execute boolean;
begin
  -- 0. THE ROW THIS MIGRATION EXISTS TO MAKE READABLE IS STILL UNWRITTEN, so
  --    the digest replaced above has no persisted value to contradict. Asserted,
  --    not assumed: a stored row under the superseded recipe beside one under the
  --    declared recipe is precisely the divergence the readback is for.
  select count(*) into v_row_count from ops.gate_zero_read_only_outcome;
  if v_row_count <> 0 then
    raise exception '0505 FAILED: % Gate Zero outcome row(s) already carry the superseded plain digest; replacing the recipe under them would leave two recipes in one table',
      v_row_count;
  end if;

  -- 1. THE TAGGED PREIMAGE IS WHAT THE FUNCTION NOW COMPUTES, proven against the
  --    array built independently here rather than against a pasted constant. A
  --    pinned hex string would pass if the function hashed the wrong bytes and
  --    somebody pasted the wrong bytes' hash.
  if ops.gate_zero_outcome_digest('{"a":1}'::jsonb) <>
     'sha256:' || encode(public.digest(convert_to(
       ops.portfolio_canonical_json(jsonb_build_array('consumer-gate-receipt.v1'::text, '{"a":1}'::jsonb)),
       'UTF8'), 'sha256'), 'hex') then
    raise exception '0505 FAILED: the outcome digest is not the tagged preimage';
  end if;
  -- AND IT IS NOT THE PLAIN ONE. The mutation this file exists to make, asserted
  -- as a refusal rather than described: if the tag were dropped the two would be
  -- equal, and this line would be the one that went red.
  if ops.gate_zero_outcome_digest('{"a":1}'::jsonb) =
     'sha256:' || encode(public.digest(convert_to(
       ops.portfolio_canonical_json('{"a":1}'::jsonb), 'UTF8'), 'sha256'), 'hex') then
    raise exception '0505 FAILED: the outcome digest still equals the untagged digest r7 refuses';
  end if;
  -- AND THE INFORMATIONAL CANDIDATE-SCOPED DIGEST IS STILL UNTAGGED, on purpose:
  -- it digests a projection that is not a consumer-gate-receipt.v1 and that
  -- r7's rule does not cover.
  if ops.gate_zero_outcome_candidate_digest('{"observed_at":"x","ttl_expires_at":"y","s":1}'::jsonb) <>
     'sha256:' || encode(public.digest(convert_to(
       ops.portfolio_canonical_json(ops.gate_zero_outcome_candidate_projection(
         '{"observed_at":"x","ttl_expires_at":"y","s":1}'::jsonb)), 'UTF8'), 'sha256'), 'hex') then
    raise exception '0505 FAILED: the informational candidate-scoped digest stopped being the plain digest over the projection';
  end if;

  -- 2. THE WRITER IS STILL THE SEAT'S AND NOTHING ELSE'S. CREATE OR REPLACE
  --    preserves an ACL, and "preserves" is the kind of claim worth measuring
  --    once rather than trusting forever.
  if not has_function_privilege('carr_gate_zero_producer',
        'ops.gate_zero_record_read_only_outcome(uuid,jsonb)', 'execute') then
    raise exception '0505 FAILED: the producer seat lost EXECUTE on its own writer';
  end if;
  -- Run the same census over its clean state, a real catalog mutation, and the
  -- restored state. PUBLIC is not a pg_roles row: aclexplode represents it as
  -- grantee OID 0, which is why the LEFT JOIN and explicit zero test below are
  -- load-bearing rather than stylistic.
  foreach v_acl_probe in array array['baseline', 'public-mutation', 'restored'] loop
    if v_acl_probe = 'public-mutation' then
      execute 'grant execute on function ops.gate_zero_record_read_only_outcome(uuid,jsonb) to public';
    elsif v_acl_probe = 'restored' then
      execute 'revoke execute on function ops.gate_zero_record_read_only_outcome(uuid,jsonb) from public';
    end if;
    select exists (
      select 1 from pg_proc p join pg_namespace n on n.oid = p.pronamespace
      cross join lateral aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) acl
      left join pg_roles g on g.oid = acl.grantee
      where n.nspname = 'ops' and p.proname = 'gate_zero_record_read_only_outcome'
        and acl.privilege_type = 'EXECUTE'
        and (
          acl.grantee = 0 -- PUBLIC has no pg_roles row; aclexplode uses OID 0.
          or g.oid is null -- Fail closed if any other ACL grantee is unresolved.
          or (g.rolname <> 'carr_gate_zero_producer' and acl.grantee <> p.proowner)
        )
    ) into v_forbidden_execute;
    if v_acl_probe in ('baseline', 'restored') and v_forbidden_execute then
      raise exception '0505 FAILED: a role other than the producer seat or owner holds EXECUTE on the writer';
    elsif v_acl_probe = 'public-mutation' and not v_forbidden_execute then
      raise exception '0505 FAILED: the writer EXECUTE census did not catch its GRANT EXECUTE TO PUBLIC mutation';
    end if;
  end loop;
  -- AND IT IS STILL SECURITY DEFINER, which is what makes session_user the
  -- connection's own login rather than the definer's.
  if not exists (
    select 1 from pg_proc p join pg_namespace n on n.oid = p.pronamespace
    where n.nspname = 'ops' and p.proname = 'gate_zero_record_read_only_outcome'
      and p.prosecdef) then
    raise exception '0505 FAILED: the writer is no longer SECURITY DEFINER';
  end if;

  -- 3. THE APPEND-ONLY TRIGGERS ARE UNTOUCHED. The fallback now returns a row
  --    instead of raising, and the one thing that must not have quietly bought
  --    is a weaker immutability guarantee.
  foreach v_col in array array['gate_zero_read_only_outcome_append_only',
                               'gate_zero_read_only_outcome_append_only_stmt'] loop
    if not exists (
      select 1 from pg_trigger t
      where t.tgrelid = 'ops.gate_zero_read_only_outcome'::regclass
        and t.tgname = v_col and not t.tgisinternal) then
      raise exception '0505 FAILED: the append-only trigger % is absent', v_col;
    end if;
  end loop;

  -- 4. THE TWO READS EXIST, AT EXACTLY THE WIDTH GRANTED. Column-scoped, proven
  --    per column, with a column OUTSIDE each list asserted absent -- the
  --    interlock 0117 established, because a bug that flattened column grants
  --    into table grants passes every positive test and is caught only here.
  foreach v_col in array array['id', 'key'] loop
    if not has_column_privilege('carr_authority', 'ops.service', v_col, 'select') then
      raise exception '0505 FAILED: carr_authority cannot read ops.service.%', v_col;
    end if;
  end loop;
  if has_table_privilege('carr_authority', 'ops.service', 'select') then
    raise exception '0505 FAILED: the ops.service read is a whole-table grant, not the two columns granted';
  end if;
  foreach v_col in array array['id', 'release_key', 'maker_actor',
                               'maker_session_user', 'maker_authority_verified'] loop
    if not has_column_privilege('carr_authority', 'ops.release', v_col, 'select') then
      raise exception '0505 FAILED: carr_authority cannot read ops.release.%', v_col;
    end if;
  end loop;
  if has_table_privilege('carr_authority', 'ops.release', 'select') then
    raise exception '0505 FAILED: the ops.release read is a whole-table grant, not the five columns granted';
  end if;
  if has_column_privilege('carr_authority', 'ops.release', 'plan_hash', 'select') then
    raise exception '0505 FAILED: the ops.release read reaches a column this migration did not grant';
  end if;

  -- 5. AND NO ROW-CHANGING PRIVILEGE MOVED, which is the measurement behind
  --    "this file is not a mutation-registry successor". The insert 0503
  --    admitted is expected and named; anything else on either relation is not.
  if exists (
    select 1 from pg_class c join pg_namespace n on n.oid = c.relnamespace
    cross join lateral aclexplode(coalesce(c.relacl, acldefault('r', c.relowner))) acl
    join pg_roles g on g.oid = acl.grantee
    where g.rolname = 'carr_authority' and n.nspname = 'ops'
      and c.relname in ('release', 'service')
      and acl.privilege_type in ('UPDATE', 'DELETE', 'TRUNCATE')
  ) then
    raise exception '0505 FAILED: a row-changing privilege beyond 0503''s insert reached carr_authority';
  end if;
  if has_table_privilege('carr_authority', 'ops.service', 'insert') then
    raise exception '0505 FAILED: ops.service gained a write grant this migration never asked for';
  end if;

  -- 6. THE COLUMN THE WHOLE FILING PATH TURNS ON IS STILL UNWRITABLE. The reads
  --    above exist so a filing session can READ maker_authority_verified back;
  --    if it had become writable, reading it back would prove nothing.
  select attgenerated into v_attgenerated from pg_attribute
   where attrelid = 'ops.release'::regclass and attname = 'maker_authority_verified';
  if v_attgenerated is distinct from 's' then
    raise exception '0505 FAILED: maker_authority_verified is no longer a stored generated column';
  end if;
end $gate_zero_candidate_filing_reads$;
