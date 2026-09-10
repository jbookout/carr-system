-- DoctorCRE v5 slice V5-F04: durable role descriptions (requirement Q106,
-- decision Q106.D1; catalog interface "agent role registry").
--
-- CANDIDATE SQL. This file is source, not a migration. It carries no ordinal,
-- no migration-ledger preflight and no SCAC registration, it is not listed in
-- public.schema_migrations, it creates no role, it inserts no row, and nothing
-- in this change applies it to any database. It has NOT been executed here.
-- Landing it as a numbered migration is a separate, reviewed, Joe-gated act.
--
-- WHAT THIS IS. mcp-server/src/model-routing.v5.js can DEFINE a role — validate
-- a durable job description and seal it with role_digest — and says of itself
-- that it adds "no table, no migration and no SQL integration". This file is the
-- missing durable half for the ROLE alone: four relations carrying versioned
-- immutable revisions, their append-only history, and an append-only current
-- pointer moved only by an explicit compare-and-swap under retained system
-- authority.
--
-- IT IS NOT A SECOND ROLE CONTRACT AND NOT A SECOND DIGEST SCHEME. There is
-- exactly one hash over a role in this system: sha256 over the canonical JSON of
-- the role's twelve content fields, as defineRole computes it. ops.model_role_digest
-- below recomputes THAT value from the stored rows. It wraps NO domain tag
-- around the object -- the benchmark rail hashes [domain_tag, payload] because
-- r7's receipt_payload_digest_rule says so, and role-description.v1 does not, so
-- adding one here would be a second scheme wearing the first one's name.
--
-- NOTHING HERE CONFERS AUTHORITY. A role DECLARES an authority class and the
-- capability references its jobs may draw on. Whether an actor may act is
-- global-boundaries.v5.js's evaluateActorAuthority question, asked of a live
-- actor. Consequently:
--   * ops.model_role_revision refuses authority_class 'system_authority' BY NAME
--     and by list, exactly as defineRole does: S01 retains that class for the
--     system-authority partner and makes it non-delegable, so a job description
--     whose occupant is a replaceable model could never be filled under it.
--   * No relation here has an occupant column, a qualification column, a grant
--     column or a numeric floor. A quality floor is a NAMED REFERENCE, because
--     this slice holds no authority to set a business number and the versioned
--     routing policy is where any such number is declared.
--   * ops.model_role_readback answers 'confers_authority', 'occupant_bound' and
--     'measured_qualification_bound' as literal false on every read.
--
-- THE ONE ACT THAT NEEDS AUTHORITY IS THE CURRENT POINTER, AND IT IS DERIVED,
-- NEVER SUPPLIED. ops.model_role_set_current_revision takes no actor, no
-- approval and no verified boolean: it calls ops.authority_actor_slug(), which
-- migration 0161 defines to read session_user on the per-partner authority
-- connection and to raise for anything else, and then requires that principal to
-- be the system-authority partner -- the same shape ops.disable_legacy_schedule
-- has held since 0161. Direct INSERT is granted to nobody, so the derivation
-- cannot be stepped around with raw SQL, and the pointer guard re-derives it
-- again inside the trigger where a writer bug cannot skip it.
--
-- THE WRITER RE-DERIVES THE WHOLE CONTRACT FROM THE STORED CONTENT. A caller may
-- name a digest; it is only ever COMPARED against one rebuilt from the rows.
-- Before a revision is returned and again before a current pointer is inserted,
-- ops.model_role_structure_error re-checks the shape (contiguous list ordinals,
-- at least one skill, rule, evidence requirement, quality floor and task class,
-- an occupiable authority class, the kernel's reference grammar) and
-- ops.model_role_digest re-hashes the rebuilt preimage. Neither reads the
-- caller's claim. NO ROLE IS SEEDED: this file ships zero rows, and a role
-- nobody recorded reads back as absent rather than as a default.
--
-- WHAT MAKES A REVISION IMMUTABLE, in two halves that are both needed:
--   1. APPEND-ONLY. ops.model_role_rows_immutable() refuses every UPDATE and
--      DELETE on all four relations, so a recorded revision cannot be rewritten
--      and a superseded current pointer cannot be erased.
--   2. CONTENT CLOSED AT COMMIT. Append-only does not stop somebody ADDING a
--      skill row to an old revision later, which would change what its digest
--      covers while the recorded digest still read as valid. Each revision
--      stores the transaction that created it (created_xid, from
--      pg_current_xact_id()), and ops.model_role_content_guard() refuses a
--      content insert from any other transaction. A revision's content is
--      therefore exactly what its own creating transaction wrote.
--      WHAT THAT DOES NOT DO, said plainly: it does not order two DIFFERENT
--      revisions of one role racing each other. ops.model_role_lock() and the
--      unique (tenant, role_key, revision_no) index do that.
--
-- ORDER IS PART OF THE HASH FOR THREE FIELDS AND NOT FOR THE OTHER THREE, and
-- both halves are properties of the emitters below rather than conventions.
-- skills, rules and evidence_requirements keep their supplied order and are
-- emitted `order by ordinal`: a reorder is a different role description.
-- capability_refs, quality_floor_refs and task_classes are SETS the kernel
-- sorts, so they are emitted `order by value collate "C"` -- the same order
-- JavaScript's default sort produces for the kernel's reference grammar, which
-- is ASCII-only, so code units and C collation coincide.
--
-- CANONICALIZATION IS NOT REDECIDED HERE. ops.portfolio_canonical_json() from
-- migration 0496 already matches artifact-trust.js's canonicalJson, including
-- JavaScript number rendering, and every digest below calls it. A role preimage
-- contains NO NUMBER AT ALL -- twelve fields of strings, arrays of strings and
-- one nested object -- so the number-rendering half of that reconciliation
-- cannot arise here. The STRING half remains, and it fails CLOSED: if the two
-- implementations ever escaped a string differently, ops.model_role_digest would
-- not equal the digest the module computed and the write would REFUSE rather
-- than store a role whose digest the two sides disagree about.
--
-- REQUIRES PostgreSQL 13 or later (pg_current_xact_id) and pgcrypto's
-- public.digest, which every comparable v5 rail already depends on.

-- ---------------------------------------------------------------------------
-- 0. Preconditions, or refuse. This file extends an existing schema. It will
--    not half-install against a database that lacks the derivations it reuses,
--    because a "durable" role store whose writer context or authority principal
--    is missing is one that cannot attribute a single row.
-- ---------------------------------------------------------------------------

\set ON_ERROR_STOP on

do $preconditions$
declare v_missing text[] := array[]::text[]; v_name text;
begin
  if to_regnamespace('ops') is null then
    raise exception 'model_role_store_blocked: schema ops does not exist'
      using errcode = '42704';
  end if;
  if current_setting('server_version_num')::integer < 130000 then
    raise exception 'model_role_store_blocked: pg_current_xact_id() needs PostgreSQL 13 or later; this server is %',
      current_setting('server_version') using errcode = '0A000';
  end if;
  foreach v_name in array array[
    -- Reused rather than restated. The writer context and the partner authority
    -- principal are the two derivations this rail refuses to write a second
    -- copy of; the canonicalizer is the one this system's digests already use.
    'ops.portfolio_writer_actor_id()',
    'ops.portfolio_canonical_json(jsonb)',
    'ops.authority_actor_slug()',
    'public.digest(bytea,text)'
  ] loop
    if to_regprocedure(v_name) is null then v_missing := v_missing || v_name; end if;
  end loop;
  if to_regclass('public.actor') is null then
    v_missing := v_missing || 'public.actor';
  end if;
  if cardinality(v_missing) > 0 then
    raise exception 'model_role_store_blocked: this database is missing %; apply migrations 0161 and 0496 and the pgcrypto extension first',
      array_to_string(v_missing, ', ') using errcode = '42704';
  end if;
end;
$preconditions$;

-- ---------------------------------------------------------------------------
-- 1. Constants and small predicates, each with exactly one copy.
--
-- These RESTATE server-held constants that live in JavaScript, in the same way
-- the benchmark rail restates r7's fixed thresholds: they are identity, not
-- configuration, and a CHECK constraint cannot import a module. Each names its
-- source so the pair can be checked by eye, and each is exercised from both
-- sides by mcp-server/test/model-role-store-postgres.sql.
-- ---------------------------------------------------------------------------

-- model-routing.v5.js V5_ROLE_DESCRIPTION_SCHEMA_VERSION.
create or replace function ops.model_role_schema_version()
returns text language sql immutable
set search_path = pg_catalog
as $$ select 'role-description.v1'::text $$;

-- model-role-store.v5.js MODEL_ROLE_READBACK_SCHEMA / MODEL_ROLE_REVISION_ENTRY_SCHEMA.
-- The module refuses a readback that does not name its contract, so a payload
-- from some other reader cannot satisfy the digest checks by accident.
create or replace function ops.model_role_readback_schema_version()
returns text language sql immutable
set search_path = pg_catalog
as $$ select 'doctorcre-v5-model-role-readback.v1'::text $$;

create or replace function ops.model_role_revision_entry_schema_version()
returns text language sql immutable
set search_path = pg_catalog
as $$ select 'doctorcre-v5-model-role-revision-entry.v1'::text $$;

-- identity.js ORGANIZATION_TENANT_ID. One internal tenant, a server constant,
-- never a claim accepted from a payload.
create or replace function ops.model_role_tenant()
returns text language sql immutable
set search_path = pg_catalog
as $$ select 'carr-internal'::text $$;

-- global-boundaries.v5.js V5_SYSTEM_AUTHORITY_PARTNER. The holder of the
-- retained, non-delegable system-authority class. Restated here because the
-- current-pointer guard has to compare against it inside the database, where a
-- JS caller's assertion is not evidence.
create or replace function ops.model_role_system_authority_partner()
returns text language sql immutable
set search_path = pg_catalog
as $$ select 'joe'::text $$;

-- model-routing.v5.js V5_ROLE_KEYS: the six settled roles, and no seventh.
create or replace function ops.model_role_keys()
returns text[] language sql immutable
set search_path = pg_catalog
as $$
  select array['architect', 'builder', 'operator', 'program_manager',
               'release_controller', 'reviewer']::text[]
$$;

-- model-routing.v5.js V5_OCCUPIABLE_AUTHORITY_CLASSES: every registered v5
-- authority class EXCEPT system_authority.
create or replace function ops.model_role_occupiable_authority_classes()
returns text[] language sql immutable
set search_path = pg_catalog
as $$ select array['developer', 'ordinary_business', 'release_admin']::text[] $$;

-- model-routing.v5.js's REF grammar, character for character. ASCII only, so
-- code points, UTF-16 code units and bytes are the same count and the 128-unit
-- bound needs no reconciliation with the module's String#length.
create or replace function ops.model_role_is_ref(p_text text)
returns boolean language sql immutable strict
set search_path = pg_catalog
as $$ select p_text ~ '^[a-z0-9][a-z0-9_.:/-]{0,127}$' $$;

-- The record layer's own instant, in the shape model-role-store.v5.js parses.
-- The authority evaluation in that module takes an instant and the module reads
-- no clock; this is where the instant comes from, so a caller cannot move it.
create or replace function ops.model_role_server_instant()
returns text language sql stable
set search_path = pg_catalog
as $$ select to_char(now() at time zone 'utc', 'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"') $$;

-- ORDERED LOCKING. Both writers take THIS lock, on the role, FIRST and before
-- anything else -- which is what makes the revision numbering and the
-- current-pointer compare-and-swap serial per role rather than merely usually
-- serial. A lock serializes nothing on its own; it serializes exactly the
-- transactions that request the same key, and there is one key per role here.
-- Nothing else in this file takes a second lock, so no lock-order cycle exists
-- to reason about.
create or replace function ops.model_role_lock(p_role_key text)
returns void language sql
set search_path = pg_catalog, ops
as $$
  select pg_advisory_xact_lock(
    hashtextextended('doctorcre:v5:model-role:' || ops.model_role_tenant() || ':' || p_role_key, 0))
$$;

comment on function ops.model_role_lock(text) is
  'The single per-role transaction lock both role-store writers take first, so revision numbering and the current-pointer compare-and-swap are serial per role.';

-- ---------------------------------------------------------------------------
-- 2. The relations.
--
-- Four, and each carries one fact: a revision's scalars, its ordered text, its
-- reference sets, and the ledger of which revision has been current.
-- ---------------------------------------------------------------------------

create table if not exists ops.model_role_revision (
  id                    uuid primary key default gen_random_uuid(),
  tenant                text not null,
  role_key              text not null,
  revision_no           integer not null check (revision_no > 0),
  idempotency_key       uuid not null unique,
  schema_version        text not null,
  role_digest           text not null check (role_digest ~ '^sha256:[0-9a-f]{64}$'),
  title                 text not null check (btrim(title) <> ''),
  mission               text not null check (btrim(mission) <> ''),
  minimum_strength_ref  text not null,
  -- THE CLASS A ROLE DECLARES, never a grant. system_authority is absent from
  -- the permitted list on purpose and is additionally refused by name below, so
  -- the prohibition is legible in two places rather than inferable from one.
  authority_class       text not null,
  -- DERIVED, never a parameter: ops.model_role_revision_guard() compares this
  -- against ops.portfolio_writer_actor_id() on every insert.
  recorded_by_actor_id  uuid not null references public.actor(id),
  -- THE DATABASE STAMPS CUSTODY TIME. No caller supplies an instant anywhere in
  -- this file, and no column accepts one.
  recorded_at           timestamptz not null default now(),
  -- The transaction that created this revision. Section 4's content guard
  -- compares against it so a later transaction cannot append to a sealed
  -- revision's content. Never read as a clock and never exposed on a readback.
  created_xid           xid8 not null default pg_current_xact_id(),
  unique (tenant, role_key, revision_no),
  -- A REVISION THAT REPEATS AN EXISTING REVISION'S CONTENT IS NOT HISTORY. It
  -- records no change, and it would make two versions compete to be "the" one
  -- with those bytes. Rolling BACK to an earlier description is done by pointing
  -- the current pointer at that earlier revision, which is exactly what an
  -- explicit current pointer over an append-only history is for.
  unique (tenant, role_key, role_digest),
  constraint model_role_revision_tenant
    check (tenant = ops.model_role_tenant()),
  constraint model_role_revision_schema_version
    check (schema_version = ops.model_role_schema_version()),
  constraint model_role_revision_role_key
    check (role_key = any (ops.model_role_keys())),
  constraint model_role_revision_authority_class_occupiable
    check (authority_class = any (ops.model_role_occupiable_authority_classes())),
  constraint model_role_revision_authority_class_not_system
    check (authority_class <> 'system_authority'),
  constraint model_role_revision_minimum_strength_ref
    check (ops.model_role_is_ref(minimum_strength_ref))
);

comment on table ops.model_role_revision is
  'One immutable revision of a durable DoctorCRE v5 role description. Its digest is the one model-routing.v5.js''s defineRole computes and is recomputed from these rows at every read, write and current-pointer change. A revision is inert: it selects nothing, grants nothing, binds no occupant and qualifies no model.';

-- The three ORDERED text lists. One relation rather than three: they share a
-- shape and a single closed field enum is easier to review than three
-- near-identical tables. Order participates in the digest, so the ordinal is
-- content rather than bookkeeping.
create table if not exists ops.model_role_revision_text (
  id           uuid primary key default gen_random_uuid(),
  revision_id  uuid not null references ops.model_role_revision(id),
  field        text not null check (field in ('evidence_requirements', 'rules', 'skills')),
  ordinal      integer not null check (ordinal >= 0),
  value        text not null check (btrim(value) <> ''),
  created_at   timestamptz not null default now(),
  unique (revision_id, field, ordinal)
);

comment on table ops.model_role_revision_text is
  'The ordered human-readable halves of one role description: its skills, the rules it must never break, and the evidence it requires. Order participates in the role digest.';

-- The three reference SETS. Unique on the VALUE as well as the ordinal, because
-- a set with a repeated member is not a set and defineRole refuses one.
create table if not exists ops.model_role_revision_ref (
  id           uuid primary key default gen_random_uuid(),
  revision_id  uuid not null references ops.model_role_revision(id),
  field        text not null check (field in ('capability_refs', 'quality_floor_refs', 'task_classes')),
  ordinal      integer not null check (ordinal >= 0),
  value        text not null,
  created_at   timestamptz not null default now(),
  unique (revision_id, field, ordinal),
  unique (revision_id, field, value),
  constraint model_role_ref_grammar check (ops.model_role_is_ref(value))
);

comment on table ops.model_role_revision_ref is
  'The reference sets of one role description: the capability references its jobs may draw on, the NAMED quality floors it requires, and its task classes. Named references, never numbers: this rail sets no business floor.';

-- THE CURRENT POINTER IS AN APPEND-ONLY LEDGER, NOT A MUTABLE ROW. A single row
-- per role updated in place would make "what was current on Tuesday" unanswerable
-- and would need an UPDATE, which section 4 refuses outright. The current
-- revision is the row with the highest pointer_no for that role; every earlier
-- one is preserved beside it, expected_prior_revision_no and all.
create table if not exists ops.model_role_current_pointer (
  id                          uuid primary key default gen_random_uuid(),
  tenant                      text not null,
  role_key                    text not null,
  pointer_no                  integer not null check (pointer_no > 0),
  revision_id                 uuid not null references ops.model_role_revision(id),
  revision_no                 integer not null check (revision_no > 0),
  role_digest                 text not null check (role_digest ~ '^sha256:[0-9a-f]{64}$'),
  -- THE COMPARE-AND-SWAP, RECORDED. Null exactly at creation, and the check
  -- below binds that to pointer_no rather than leaving null to mean two things.
  expected_prior_revision_no  integer,
  idempotency_key             uuid not null unique,
  -- The class the act was evaluated under, written as literal constants so a row
  -- asserting anything else cannot exist. These record WHICH authority was
  -- exercised; they are not a grant and nothing reads them to permit anything.
  authority_class             text not null check (authority_class = 'system_authority'),
  authority_grant_kind        text not null check (authority_grant_kind = 'retained_system_authority'),
  -- DERIVED from ops.authority_actor_slug(), never a parameter.
  set_by_partner_slug         text not null,
  set_by_actor_id             uuid not null references public.actor(id),
  set_at                      timestamptz not null default now(),
  unique (tenant, role_key, pointer_no),
  constraint model_role_pointer_tenant
    check (tenant = ops.model_role_tenant()),
  constraint model_role_pointer_role_key
    check (role_key = any (ops.model_role_keys())),
  constraint model_role_pointer_creation_shape
    check ((pointer_no = 1 and expected_prior_revision_no is null)
        or (pointer_no > 1 and expected_prior_revision_no is not null)),
  constraint model_role_pointer_not_self_expected
    check (expected_prior_revision_no is null or expected_prior_revision_no <> revision_no)
);

comment on table ops.model_role_current_pointer is
  'The append-only ledger of which revision of a role description has been current. The highest pointer_no per role is the current one; the rest are the history of what was. Each row records the compare-and-swap it won and the retained system authority it was made under, and confers nothing.';

-- ---------------------------------------------------------------------------
-- 3. Append-only.
-- ---------------------------------------------------------------------------

create or replace function ops.model_role_rows_immutable()
returns trigger language plpgsql
set search_path = pg_catalog, ops
as $$
begin
  raise exception 'DoctorCRE v5 model role rows are append-only; % on %.% is refused',
    tg_op, tg_table_schema, tg_table_name using errcode = '42501';
end;
$$;

comment on function ops.model_role_rows_immutable() is
  'Refuses every update and delete on the DoctorCRE v5 model role relations. A recorded revision is not edited, and a superseded current pointer is not erased.';

do $append_only$
declare t text;
begin
  foreach t in array array[
    'model_role_revision', 'model_role_revision_text',
    'model_role_revision_ref', 'model_role_current_pointer'
  ] loop
    execute format('drop trigger if exists %I on ops.%I', t || '_append_only', t);
    execute format(
      'create trigger %I before update or delete on ops.%I for each row execute function ops.model_role_rows_immutable()',
      t || '_append_only', t);
  end loop;
end;
$append_only$;

-- ---------------------------------------------------------------------------
-- 4. The content freeze.
--
-- Append-only stops a revision being rewritten. It does not stop a later
-- transaction ADDING a skill to one, which would change what the recorded digest
-- covers while the digest still read as valid. A revision's content therefore
-- belongs to the transaction that created it and to no other.
-- ---------------------------------------------------------------------------

create or replace function ops.model_role_content_guard()
returns trigger language plpgsql
set search_path = pg_catalog, ops
as $$
declare v_created xid8;
begin
  select created_xid into v_created from ops.model_role_revision where id = new.revision_id;
  if not found then
    raise exception 'model role content names an unknown revision' using errcode = '23503';
  end if;
  if v_created <> pg_current_xact_id() then
    raise exception 'model role revision % is sealed; its content was written by another transaction and cannot be added to',
      new.revision_id using errcode = '42501';
  end if;
  return new;
end;
$$;

comment on function ops.model_role_content_guard() is
  'Refuses a skill, rule, evidence, capability, quality-floor or task-class row for a revision created by a different transaction, so a revision''s content is exactly what its own creating transaction wrote. It does not order two different revisions of one role; ops.model_role_lock() does that.';

do $freeze$
declare t text;
begin
  foreach t in array array['model_role_revision_text', 'model_role_revision_ref'] loop
    execute format('drop trigger if exists %I on ops.%I', t || '_content_guard', t);
    execute format(
      'create trigger %I before insert on ops.%I for each row execute function ops.model_role_content_guard()',
      t || '_content_guard', t);
  end loop;
end;
$freeze$;

-- ---------------------------------------------------------------------------
-- 5. The canonical preimage, rebuilt FROM THE STORED ROWS.
--
-- This is what makes the digest a statement about the persisted role rather than
-- about an object a caller once supplied. It emits exactly the twelve fields
-- defineRole hashes, with the two constants emitted rather than stored.
-- ---------------------------------------------------------------------------

create or replace function ops.model_role_text_array(p_revision_id uuid, p_field text)
returns jsonb language sql stable security definer
set search_path = pg_catalog, ops, public
as $$
  select coalesce(jsonb_agg(to_jsonb(t.value) order by t.ordinal), '[]'::jsonb)
    from ops.model_role_revision_text t
   where t.revision_id = p_revision_id and t.field = p_field
$$;

comment on function ops.model_role_text_array(uuid, text) is
  'One ordered text list of a role revision, in its stored ordinal order. Order participates in the role digest.';

-- ORDER BY VALUE, NOT BY ORDINAL, and that is the point. defineRole SORTS these
-- three, so the canonical bytes are the sorted ones whatever order a row set
-- happens to carry. Emitting by ordinal would let a permuted insert produce
-- bytes the module would never compute. `collate "C"` is UTF-8 byte order, which
-- coincides with JavaScript's default code-unit sort for this ASCII-only grammar.
create or replace function ops.model_role_ref_array(p_revision_id uuid, p_field text)
returns jsonb language sql stable security definer
set search_path = pg_catalog, ops, public
as $$
  select coalesce(jsonb_agg(to_jsonb(r.value) order by r.value collate "C"), '[]'::jsonb)
    from ops.model_role_revision_ref r
   where r.revision_id = p_revision_id and r.field = p_field
$$;

comment on function ops.model_role_ref_array(uuid, text) is
  'One reference set of a role revision, sorted the way defineRole sorts it rather than in the order its rows were written.';

create or replace function ops.model_role_preimage(p_revision_id uuid)
returns jsonb language plpgsql stable security definer
set search_path = pg_catalog, ops, public
as $$
declare v ops.model_role_revision%rowtype;
begin
  select * into v from ops.model_role_revision where id = p_revision_id;
  if not found then
    raise exception 'model role revision % does not exist', p_revision_id using errcode = '23503';
  end if;
  -- Exactly the twelve content fields of role-description.v1. schema_version and
  -- tenant are EMITTED, never stored per revision: taking either from a row would
  -- make a fixed constant something a row could move.
  return jsonb_build_object(
    'schema_version', ops.model_role_schema_version(),
    'tenant', ops.model_role_tenant(),
    'role_key', v.role_key,
    'title', v.title,
    'mission', v.mission,
    'skills', ops.model_role_text_array(p_revision_id, 'skills'),
    'rules', ops.model_role_text_array(p_revision_id, 'rules'),
    'authority', jsonb_build_object(
      'authority_class', v.authority_class,
      'capability_refs', ops.model_role_ref_array(p_revision_id, 'capability_refs')),
    'evidence_requirements', ops.model_role_text_array(p_revision_id, 'evidence_requirements'),
    'quality_floor_refs', ops.model_role_ref_array(p_revision_id, 'quality_floor_refs'),
    'minimum_strength_ref', v.minimum_strength_ref,
    'task_classes', ops.model_role_ref_array(p_revision_id, 'task_classes'));
end;
$$;

comment on function ops.model_role_preimage(uuid) is
  'The twelve role-description.v1 content fields of one revision, rebuilt from its persisted rows with schema_version and tenant emitted rather than stored. These are the exact bytes defineRole hashes.';

-- THE ONE HASH, AND NO DOMAIN TAG. defineRole computes digest(content) over the
-- object itself. Wrapping a domain tag around it here would be a second digest
-- scheme, and a second scheme that agrees with the first today is one that can
-- disagree after one edit.
--
-- SPLIT IN TWO, AND THE SPLIT IS LOAD-BEARING. The hash is a pure function of a
-- preimage and knows nothing about rows; ops.model_role_preimage is the only
-- place a preimage is built FROM ROWS. A caller that must know a role's digest
-- before it has stored anything -- the proof fixture, and anyone reconciling the
-- two implementations -- assembles the preimage itself and hashes it here,
-- rather than a second row-reading builder existing for their benefit.
create or replace function ops.model_role_digest_of_preimage(p_preimage jsonb)
returns text language sql immutable strict
set search_path = pg_catalog, ops, public
as $$
  select 'sha256:' || encode(public.digest(convert_to(
    ops.portfolio_canonical_json(p_preimage), 'UTF8'), 'sha256'), 'hex')
$$;

comment on function ops.model_role_digest_of_preimage(jsonb) is
  'sha256 over the canonical JSON of one role-description.v1 preimage, with no domain tag. The only hashing step in this rail; ops.model_role_digest feeds it the preimage rebuilt from stored rows.';

create or replace function ops.model_role_digest(p_revision_id uuid)
returns text language sql stable security definer
set search_path = pg_catalog, ops, public
as $$
  select ops.model_role_digest_of_preimage(ops.model_role_preimage(p_revision_id))
$$;

comment on function ops.model_role_digest(uuid) is
  'The role_digest model-routing.v5.js''s defineRole computes, recomputed here from the persisted rows. No domain tag is wrapped around the preimage, because role-description.v1 declares none.';

-- ---------------------------------------------------------------------------
-- 6. Structural validation. Every clause is a recomputation from the persisted
--    rows, so a row cannot answer for itself.
--
-- DELIBERATELY NOT A DIGEST CHECK. Shape and content-binding are two different
-- findings with two different remedies, and the callers below check each in its
-- own statement so a reader learns which one failed.
-- ---------------------------------------------------------------------------

create or replace function ops.model_role_structure_error(p_revision_id uuid)
returns text language plpgsql stable security definer
set search_path = pg_catalog, ops, public
as $$
declare
  v ops.model_role_revision%rowtype;
  v_field text; v_count integer; v_max integer; v_bad text;
begin
  select * into v from ops.model_role_revision where id = p_revision_id;
  if not found then return 'no such role revision'; end if;
  if v.tenant <> ops.model_role_tenant() then return 'revision tenant is not the one server tenant'; end if;
  if v.schema_version <> ops.model_role_schema_version() then
    return 'revision schema_version is not ' || ops.model_role_schema_version();
  end if;
  if not (v.role_key = any (ops.model_role_keys())) then
    return 'role_key ' || v.role_key || ' is not one of the settled v5 roles';
  end if;
  if not (v.authority_class = any (ops.model_role_occupiable_authority_classes())) then
    return 'authority_class ' || v.authority_class ||
           ' is not occupiable; system authority is retained and non-delegable';
  end if;
  if not ops.model_role_is_ref(v.minimum_strength_ref) then
    return 'minimum_strength_ref is not a reference token';
  end if;

  -- The three ordered lists: at least one entry, and contiguously ordinaled from
  -- zero. A GAP is the shape a partial insert leaves behind, and rebuilding from
  -- gapped rows produces a shorter list hashing to something nobody computed.
  foreach v_field in array array['evidence_requirements', 'rules', 'skills'] loop
    select count(*), coalesce(max(ordinal), -1) into v_count, v_max
      from ops.model_role_revision_text
     where revision_id = p_revision_id and field = v_field;
    if v_count = 0 then return 'role ' || v_field || ' names no entry'; end if;
    if v_max <> v_count - 1 then
      return 'role ' || v_field || ' is not contiguously ordinaled from zero';
    end if;
  end loop;

  -- The reference sets. quality_floor_refs and task_classes must each name at
  -- least one member, exactly as defineRole requires; capability_refs may
  -- legitimately be empty, because a role may draw on no capability at all.
  foreach v_field in array array['capability_refs', 'quality_floor_refs', 'task_classes'] loop
    select count(*), coalesce(max(ordinal), -1) into v_count, v_max
      from ops.model_role_revision_ref
     where revision_id = p_revision_id and field = v_field;
    if v_field <> 'capability_refs' and v_count = 0 then
      return 'role ' || v_field || ' names no entry';
    end if;
    if v_count > 0 and v_max <> v_count - 1 then
      return 'role ' || v_field || ' is not contiguously ordinaled from zero';
    end if;
    select string_agg(value, ', ' order by value collate "C") into v_bad
      from ops.model_role_revision_ref
     where revision_id = p_revision_id and field = v_field
       and not ops.model_role_is_ref(value);
    if v_bad is not null then
      return 'role ' || v_field || ' names a value that is not a reference token: ' || v_bad;
    end if;
  end loop;

  -- No content row may belong to a field this revision does not declare, and no
  -- row may sit under a revision of another role: both foreign keys and the
  -- field enums already refuse those, and this clause states the property the
  -- rebuild depends on rather than assuming it.
  select count(*) into v_count from ops.model_role_revision_text
   where revision_id = p_revision_id
     and field not in ('evidence_requirements', 'rules', 'skills');
  if v_count > 0 then return 'role text rows name an unknown field'; end if;
  return null;
end;
$$;

comment on function ops.model_role_structure_error(uuid) is
  'The first structural reason one stored role revision is not a complete role-description.v1, or null. Shape only: the digest binding is checked separately so the two failures are told apart.';

-- ---------------------------------------------------------------------------
-- 7. Guards. Everything each act depends on is checked HERE, so a writer bug
--    cannot admit a misattributed revision or an unauthorized current pointer.
-- ---------------------------------------------------------------------------

create or replace function ops.model_role_revision_guard()
returns trigger language plpgsql
set search_path = pg_catalog, ops, public
as $$
declare v_prev integer;
begin
  -- THE AUTHOR IS THE SERVER-ESTABLISHED WRITER, never a payload field.
  if new.recorded_by_actor_id <> ops.portfolio_writer_actor_id() then
    raise exception 'model role revision actor does not match the authenticated writer context'
      using errcode = '42501';
  end if;
  if new.tenant <> ops.model_role_tenant() then
    raise exception 'model role revision tenant must be the one server tenant' using errcode = '42501';
  end if;
  if new.schema_version <> ops.model_role_schema_version() then
    raise exception 'model role revision must be a %', ops.model_role_schema_version()
      using errcode = '22023';
  end if;
  -- Refused BY NAME as well as by the column list, so the prohibition reads as a
  -- decision rather than as an omission from an enum.
  if new.authority_class = 'system_authority' then
    raise exception 'a durable role occupied by a replaceable model may not declare system authority: S01 retains that class and makes it non-delegable'
      using errcode = '42501';
  end if;
  -- VERSIONS ARE CONTIGUOUS. A hole in the history is a revision somebody wrote
  -- and lost, and it makes "the version before this one" unanswerable.
  select coalesce(max(revision_no), 0) into v_prev from ops.model_role_revision
   where tenant = new.tenant and role_key = new.role_key;
  if new.revision_no <> v_prev + 1 then
    raise exception 'model role % is at revision %; the next revision is %, not %',
      new.role_key, v_prev, v_prev + 1, new.revision_no using errcode = '23505';
  end if;
  return new;
end;
$$;

comment on function ops.model_role_revision_guard() is
  'Binds a role revision to the server-established writer and the one tenant, refuses a system-authority role class by name, and refuses a non-contiguous version.';

drop trigger if exists model_role_revision_guard on ops.model_role_revision;
create trigger model_role_revision_guard
  before insert on ops.model_role_revision
  for each row execute function ops.model_role_revision_guard();

-- THE CURRENT POINTER GUARD. Every authoritative fact is re-derived here, inside
-- the trigger, from the stored content and the live session -- not read back from
-- the row being inserted and not taken from the writer's parameters.
create or replace function ops.model_role_pointer_guard()
returns trigger language plpgsql
set search_path = pg_catalog, ops, public
as $$
declare
  v_rev ops.model_role_revision%rowtype;
  v_cur ops.model_role_current_pointer%rowtype;
  v_partner text; v_actor uuid; v_err text; v_live text;
begin
  -- 1. THE PRINCIPAL, DERIVED. ops.authority_actor_slug() reads session_user and
  --    raises for anything that is not an admitted partner authority principal.
  --    It is called rather than compared to a parameter, and the comparison
  --    below is against the S01 constant rather than against anything supplied.
  v_partner := ops.authority_actor_slug();
  if v_partner <> ops.model_role_system_authority_partner() then
    raise exception 'naming the current revision of a durable role is a retained system-authority act reserved to %; this authority session is %',
      ops.model_role_system_authority_partner(), v_partner using errcode = '42501';
  end if;
  if new.set_by_partner_slug <> v_partner then
    raise exception 'model role current pointer names a partner other than the authenticated authority session'
      using errcode = '42501';
  end if;
  select id into v_actor from public.actor
   where slug = v_partner and active and kind = 'human';
  if not found then
    raise exception 'partner authority session % has no active human actor', v_partner
      using errcode = '23503';
  end if;
  if new.set_by_actor_id <> v_actor then
    raise exception 'model role current pointer names an actor other than the authenticated partner'
      using errcode = '42501';
  end if;
  if new.tenant <> ops.model_role_tenant() then
    raise exception 'model role current pointer tenant must be the one server tenant'
      using errcode = '42501';
  end if;

  -- 2. THE REVISION, LOADED. A pointer at a revision nobody recorded is not a
  --    pointer, and naming a uuid never brings one into existence.
  select * into v_rev from ops.model_role_revision where id = new.revision_id;
  if not found then
    raise exception 'model role current pointer names an unknown revision' using errcode = '23503';
  end if;
  if v_rev.role_key <> new.role_key or v_rev.tenant <> new.tenant then
    raise exception 'model role current pointer names a revision of a different role' using errcode = '23503';
  end if;
  if v_rev.revision_no <> new.revision_no then
    raise exception 'model role current pointer names revision % of a row that is revision %',
      new.revision_no, v_rev.revision_no using errcode = '22023';
  end if;

  -- 3. THE WHOLE CONTRACT, RECHECKED FROM THE STORED CONTENT. Not the writer's
  --    word, and not the row's own claim: the shape is re-derived and the digest
  --    is re-hashed from the persisted rows.
  v_err := ops.model_role_structure_error(new.revision_id);
  if v_err is not null then
    raise exception 'a revision that is not a complete role description cannot become current: %', v_err
      using errcode = '22023';
  end if;
  v_live := ops.model_role_digest(new.revision_id);
  if new.role_digest <> v_live or v_rev.role_digest <> v_live then
    raise exception 'model role current pointer digest is stale: the stored rows produce %', v_live
      using errcode = '22000';
  end if;

  -- 4. THE COMPARE-AND-SWAP, RE-EVALUATED under the lock the writer already
  --    holds. Creation and replacement are two shapes and neither is inferred
  --    from a null: pointer_no fixes which one this row claims to be, and the
  --    ledger decides whether that claim is true.
  select * into v_cur from ops.model_role_current_pointer
   where tenant = new.tenant and role_key = new.role_key
   order by pointer_no desc limit 1;
  if not found then
    if new.pointer_no <> 1 or new.expected_prior_revision_no is not null then
      raise exception 'model role % has no current revision; the first pointer is number 1 with no prior expectation',
        new.role_key using errcode = '40001';
    end if;
  else
    if new.pointer_no <> v_cur.pointer_no + 1 then
      raise exception 'model role % current pointer is number %; the next is %, not %',
        new.role_key, v_cur.pointer_no, v_cur.pointer_no + 1, new.pointer_no using errcode = '40001';
    end if;
    if new.expected_prior_revision_no is null then
      raise exception 'model role % already has current revision %; a creation compare-and-swap cannot move it',
        new.role_key, v_cur.revision_no using errcode = '40001';
    end if;
    if new.expected_prior_revision_no <> v_cur.revision_no then
      raise exception 'model role % current revision is %, not the expected %',
        new.role_key, v_cur.revision_no, new.expected_prior_revision_no using errcode = '40001';
    end if;
    if v_cur.revision_no = new.revision_no then
      raise exception 'revision % of model role % is already current', new.revision_no, new.role_key
        using errcode = '42710';
    end if;
  end if;
  return new;
end;
$$;

comment on function ops.model_role_pointer_guard() is
  'Refuses a current-pointer change that is not made by the retained system-authority partner as derived from session_user, that names an unknown revision or a revision of another role, whose stored content no longer rebuilds its own digest or is not a complete role description, or whose compare-and-swap does not match the ledger. It grants nothing: it records which authority was exercised.';

drop trigger if exists model_role_pointer_guard on ops.model_role_current_pointer;
create trigger model_role_pointer_guard
  before insert on ops.model_role_current_pointer
  for each row execute function ops.model_role_pointer_guard();

-- ---------------------------------------------------------------------------
-- 8. Readers. Every one recomputes rather than reporting a stored column, and
--    every one states what it does not establish.
-- ---------------------------------------------------------------------------

create or replace function ops.model_role_revision_readback(p_revision_id uuid)
returns jsonb language plpgsql stable security definer
set search_path = pg_catalog, ops, public
as $$
declare v ops.model_role_revision%rowtype;
begin
  select * into v from ops.model_role_revision where id = p_revision_id;
  if not found then return null; end if;
  return jsonb_build_object(
    'schema_version', ops.model_role_revision_entry_schema_version(),
    'revision_id', v.id,
    'role_key', v.role_key,
    'revision_no', v.revision_no,
    'role_digest', v.role_digest,
    -- RECOMPUTED, beside the stored claim rather than instead of it. The module
    -- re-derives a third value from the preimage and refuses unless all three
    -- agree, which is what makes a read a statement about bytes.
    'recomputed_role_digest', ops.model_role_digest(v.id),
    'structure_error', ops.model_role_structure_error(v.id),
    'recorded_at', v.recorded_at,
    'preimage', ops.model_role_preimage(v.id),
    -- Said on every read, because the whole risk here is that somebody reads a
    -- durable job description as a grant, an occupancy or a measurement.
    'confers_authority', false,
    'occupant_bound', false,
    'measured_qualification_bound', false,
    'integrity', 'recomputed_from_committed_rows');
end;
$$;

comment on function ops.model_role_revision_readback(uuid) is
  'One stored role revision: its preimage rebuilt from the rows, the digest recorded at write and the digest recomputed now, its structural validity, and the database''s own custody time. Null when no such revision exists.';

create or replace function ops.model_role_current_revision(p_role_key text)
returns jsonb language plpgsql stable security definer
set search_path = pg_catalog, ops, public
as $$
declare v_cur ops.model_role_current_pointer%rowtype; v_entry jsonb;
begin
  select * into v_cur from ops.model_role_current_pointer
   where tenant = ops.model_role_tenant() and role_key = p_role_key
   order by pointer_no desc limit 1;
  -- NULL MEANS "NOBODY HAS SELECTED ONE", NEVER "THERE IS NO ROLE" AND NEVER A
  -- DEFAULT. No revision is current until a system-authority act makes one so,
  -- and nothing in this file seeds, assumes or hardcodes one.
  if not found then return null; end if;
  v_entry := ops.model_role_revision_readback(v_cur.revision_id);
  if v_entry is null then
    raise exception 'model role % current pointer names a revision that is not stored', p_role_key
      using errcode = '22000';
  end if;
  return v_entry || jsonb_build_object(
    'pointer_id', v_cur.id,
    'pointer_no', v_cur.pointer_no,
    'expected_prior_revision_no', v_cur.expected_prior_revision_no,
    'set_by_partner_slug', v_cur.set_by_partner_slug,
    'authority_class', v_cur.authority_class,
    'authority_grant_kind', v_cur.authority_grant_kind,
    'set_at', v_cur.set_at);
end;
$$;

comment on function ops.model_role_current_revision(text) is
  'The current revision of one role description, or null when no system-authority act has selected one. Null is an unanswered question, never a default role.';

create or replace function ops.model_role_readback(p_role_key text)
returns jsonb language plpgsql stable security definer
set search_path = pg_catalog, ops, public
as $$
declare
  v_history jsonb := '[]'::jsonb;
  v_pointers jsonb := '[]'::jsonb;
  r record;
begin
  if not (p_role_key = any (ops.model_role_keys())) then
    raise exception '% is not one of the settled v5 roles', p_role_key using errcode = '22023';
  end if;
  for r in select id from ops.model_role_revision
            where tenant = ops.model_role_tenant() and role_key = p_role_key
            order by revision_no
  loop
    v_history := v_history || jsonb_build_array(ops.model_role_revision_readback(r.id));
  end loop;
  for r in select * from ops.model_role_current_pointer
            where tenant = ops.model_role_tenant() and role_key = p_role_key
            order by pointer_no
  loop
    v_pointers := v_pointers || jsonb_build_array(jsonb_build_object(
      'pointer_no', r.pointer_no,
      'revision_no', r.revision_no,
      'role_digest', r.role_digest,
      'expected_prior_revision_no', r.expected_prior_revision_no,
      'set_by_partner_slug', r.set_by_partner_slug,
      'authority_class', r.authority_class,
      'authority_grant_kind', r.authority_grant_kind,
      'set_at', r.set_at));
  end loop;
  return jsonb_build_object(
    'schema_version', ops.model_role_readback_schema_version(),
    'role_schema_version', ops.model_role_schema_version(),
    'tenant', ops.model_role_tenant(),
    'role_key', p_role_key,
    'current', ops.model_role_current_revision(p_role_key),
    'history', v_history,
    'pointer_history', v_pointers,
    'integrity', 'recomputed_from_committed_rows',
    -- The exact scope of what a read here establishes. It is re-derivation from
    -- committed rows; it is not a signature, not a capability token, and not
    -- evidence that any model is qualified for this role or occupies it.
    'signed', false,
    'capability_token_issued', false,
    'confers_authority', false,
    'occupant_bound', false,
    'measured_qualification_bound', false);
end;
$$;

comment on function ops.model_role_readback(text) is
  'One role description''s whole durable state: every revision rebuilt from its rows with both digests, the current pointer, and the ledger of what has been current. It establishes re-derivation from committed rows and says so; it is not a signature and not a qualification.';

-- ---------------------------------------------------------------------------
-- 9. The only write paths. Each derives its own principal and accepts none.
--
-- IDEMPOTENCY IS A REPLAY, NOT A SECOND WRITE. Each writer looks its key up
-- first: an exact replay returns the row that already exists, and the same key
-- presented with different content, a different role, a different version, a
-- different compare-and-swap expectation or a different derived principal is
-- REFUSED rather than quietly writing a second row or silently returning the
-- first.
-- ---------------------------------------------------------------------------

create or replace function ops.model_role_record_revision(
  p_role_key text, p_revision_no integer, p_idempotency_key uuid,
  p_role_digest text, p_scalars jsonb, p_texts jsonb, p_refs jsonb)
returns uuid language plpgsql security definer
set search_path = pg_catalog, ops, public
as $$
declare
  v_id uuid; v_existing ops.model_role_revision%rowtype;
  v_actor uuid; v_err text; v_live text; t jsonb; r jsonb;
begin
  perform ops.model_role_lock(p_role_key);
  v_actor := ops.portfolio_writer_actor_id();

  select * into v_existing from ops.model_role_revision where idempotency_key = p_idempotency_key;
  if found then
    -- THE REPLAY IS BOUND TO THE EXACT CONTENT, ROLE, VERSION AND WRITER. A key
    -- reused for anything else is a different request wearing the same name, and
    -- the writer comparison is what stops one actor's key returning a row
    -- another actor authored.
    if v_existing.role_key is distinct from p_role_key
       or v_existing.revision_no is distinct from p_revision_no
       or v_existing.role_digest is distinct from p_role_digest
       or v_existing.recorded_by_actor_id is distinct from v_actor then
      raise exception 'model role idempotency key % was already used for a different revision, role, version or writer',
        p_idempotency_key using errcode = '23505';
    end if;
    return v_existing.id;
  end if;

  if exists (select 1 from ops.model_role_revision
              where tenant = ops.model_role_tenant() and role_key = p_role_key
                and role_digest = p_role_digest) then
    raise exception 'model role % already has a revision with these exact bytes; a revision that records no change is not history',
      p_role_key using errcode = '23505';
  end if;

  insert into ops.model_role_revision(
    tenant, role_key, revision_no, idempotency_key, schema_version, role_digest,
    title, mission, minimum_strength_ref, authority_class, recorded_by_actor_id)
  values (ops.model_role_tenant(), p_role_key, p_revision_no, p_idempotency_key,
    ops.model_role_schema_version(), p_role_digest,
    p_scalars ->> 'title', p_scalars ->> 'mission',
    p_scalars ->> 'minimum_strength_ref', p_scalars ->> 'authority_class', v_actor)
  returning id into v_id;

  for t in select value from jsonb_array_elements(p_texts) loop
    insert into ops.model_role_revision_text(revision_id, field, ordinal, value)
    values (v_id, t ->> 'field', (t ->> 'ordinal')::integer, t ->> 'value');
  end loop;

  for r in select value from jsonb_array_elements(p_refs) loop
    insert into ops.model_role_revision_ref(revision_id, field, ordinal, value)
    values (v_id, r ->> 'field', (r ->> 'ordinal')::integer, r ->> 'value');
  end loop;

  -- THE COMMIT CHECK, from the stored content rather than from the parameters.
  -- Shape first, then the digest binding, so a caller learns which one failed.
  v_err := ops.model_role_structure_error(v_id);
  if v_err is not null then
    raise exception 'model role revision is not a complete role description: %', v_err
      using errcode = '22023';
  end if;
  v_live := ops.model_role_digest(v_id);
  if v_live is distinct from p_role_digest then
    raise exception 'model role digest is stale: the stored rows produce %, not %', v_live, p_role_digest
      using errcode = '22000';
  end if;
  return v_id;
end;
$$;

comment on function ops.model_role_record_revision(text,integer,uuid,text,jsonb,jsonb,jsonb) is
  'The only way to record a revision of a durable role description. The author is derived from the server-established writer context and is not a parameter; the supplied digest is compared against one recomputed from the stored rows before the transaction may commit. The revision is inert and does not become current.';

-- THE CURRENT POINTER. Every authoritative fact is DERIVED inside this function
-- and re-derived again inside the guard: the partner from the authenticated
-- session, the revision and its whole contract from the stored rows, and the
-- compare-and-swap from the ledger under the role lock. The caller supplies only
-- the role, the key, the version it means, the hash it believes that version
-- has, and the expectation it is swapping against. There is no parameter through
-- which an actor, an approval, a verified boolean or a grant could arrive.
create or replace function ops.model_role_set_current_revision(
  p_role_key text, p_idempotency_key uuid, p_revision_no integer, p_role_digest text,
  p_expect_creation boolean, p_expected_current_revision_no integer)
returns uuid language plpgsql security definer
set search_path = pg_catalog, ops, public
as $$
declare
  v_id uuid; v_existing ops.model_role_current_pointer%rowtype;
  v_rev ops.model_role_revision%rowtype; v_cur ops.model_role_current_pointer%rowtype;
  v_partner text; v_actor uuid; v_pointer_no integer; v_expected_prior integer;
begin
  perform ops.model_role_lock(p_role_key);

  -- THE CREATION ASSERTION IS ITS OWN PARAMETER, and it is checked before
  -- anything else reads the null. A null that means "create" and a null that
  -- means "nobody filled this in" look identical in SQL, and telling them apart
  -- is the whole compare-and-swap. Both shapes must agree or this refuses.
  if p_expect_creation is null then
    raise exception 'state the compare-and-swap shape explicitly: p_expect_creation must be true or false'
      using errcode = '22023';
  end if;
  if p_expect_creation and p_expected_current_revision_no is not null then
    raise exception 'a creation compare-and-swap asserts there is no current revision, so it names no expected one'
      using errcode = '22023';
  end if;
  if not p_expect_creation and p_expected_current_revision_no is null then
    raise exception 'a replacing compare-and-swap must name the revision it expects to be current'
      using errcode = '22023';
  end if;

  -- Raises unless session_user is an admitted partner authority principal, then
  -- refuses unless that principal is the retained system authority.
  v_partner := ops.authority_actor_slug();
  if v_partner <> ops.model_role_system_authority_partner() then
    raise exception 'naming the current revision of a durable role is a retained system-authority act reserved to %; this authority session is %',
      ops.model_role_system_authority_partner(), v_partner using errcode = '42501';
  end if;
  select id into v_actor from public.actor where slug = v_partner and active and kind = 'human';
  if not found then
    raise exception 'partner authority session % has no active human actor', v_partner
      using errcode = '23503';
  end if;

  select * into v_existing from ops.model_role_current_pointer
   where idempotency_key = p_idempotency_key;
  if found then
    -- Every stored parameter is compared, the compare-and-swap expectation and
    -- the derived principal included: a replay that matched on the digest but
    -- swapped against a different expectation, or was made by a different
    -- partner, is a different act.
    if v_existing.role_key is distinct from p_role_key
       or v_existing.revision_no is distinct from p_revision_no
       or v_existing.role_digest is distinct from p_role_digest
       or v_existing.expected_prior_revision_no is distinct from p_expected_current_revision_no
       or v_existing.set_by_partner_slug is distinct from v_partner then
      raise exception 'model role idempotency key % was already used for a different current-pointer change',
        p_idempotency_key using errcode = '23505';
    end if;
    return v_existing.id;
  end if;

  select * into v_rev from ops.model_role_revision
   where tenant = ops.model_role_tenant() and role_key = p_role_key and revision_no = p_revision_no;
  if not found then
    raise exception 'model role % has no revision %', p_role_key, p_revision_no using errcode = '23503';
  end if;
  -- The caller's hash is compared against the STORED one here and against a
  -- freshly recomputed one in the guard. It is never used as the value.
  if v_rev.role_digest is distinct from p_role_digest then
    raise exception 'model role % revision % hashes to %, not to the digest named here',
      p_role_key, p_revision_no, v_rev.role_digest using errcode = '22000';
  end if;

  select * into v_cur from ops.model_role_current_pointer
   where tenant = ops.model_role_tenant() and role_key = p_role_key
   order by pointer_no desc limit 1;
  if p_expect_creation then
    if found then
      raise exception 'model role % already has current revision %; a creation compare-and-swap cannot move it',
        p_role_key, v_cur.revision_no using errcode = '40001';
    end if;
    v_pointer_no := 1;
    v_expected_prior := null;
  else
    if not found then
      raise exception 'model role % has no current revision to compare against; assert creation instead',
        p_role_key using errcode = '40001';
    end if;
    if v_cur.revision_no <> p_expected_current_revision_no then
      raise exception 'model role % current revision is %, not the expected %',
        p_role_key, v_cur.revision_no, p_expected_current_revision_no using errcode = '40001';
    end if;
    if v_cur.revision_no = p_revision_no then
      raise exception 'revision % of model role % is already current', p_revision_no, p_role_key
        using errcode = '42710';
    end if;
    v_pointer_no := v_cur.pointer_no + 1;
    v_expected_prior := p_expected_current_revision_no;
  end if;

  insert into ops.model_role_current_pointer(
    tenant, role_key, pointer_no, revision_id, revision_no, role_digest,
    expected_prior_revision_no, idempotency_key,
    authority_class, authority_grant_kind, set_by_partner_slug, set_by_actor_id)
  values (ops.model_role_tenant(), p_role_key, v_pointer_no, v_rev.id, v_rev.revision_no,
    v_rev.role_digest, v_expected_prior, p_idempotency_key,
    'system_authority', 'retained_system_authority', v_partner, v_actor)
  returning id into v_id;
  return v_id;
end;
$$;

comment on function ops.model_role_set_current_revision(text,uuid,integer,text,boolean,integer) is
  'The only way to name which revision of a durable role description is current. The partner is derived from the authenticated authority session and must be the retained system-authority holder; the compare-and-swap expectation is explicit and its creation shape is its own parameter rather than an interpreted null. It appends to a ledger, preserving every earlier pointer, and grants nothing.';

-- ---------------------------------------------------------------------------
-- 10. Grants. Reads reach the ordinary bundles. DIRECT INSERT IS GRANTED TO
--     NOBODY: every write goes through a definer function that derives its own
--     principal, so a writer holding a raw connection cannot attribute a row to
--     someone else or step around the guards.
--
--     No role is created by this file. Every role named below already exists.
-- ---------------------------------------------------------------------------

grant select on ops.model_role_revision, ops.model_role_revision_text,
  ops.model_role_revision_ref, ops.model_role_current_pointer
  to carr_reader, carr_writer, carr_authority;

revoke insert, update, delete, truncate on ops.model_role_revision,
  ops.model_role_revision_text, ops.model_role_revision_ref,
  ops.model_role_current_pointer
  from public, carr_reader, carr_writer, carr_jobs, carr_authority;

revoke all on function ops.model_role_schema_version(),
  ops.model_role_readback_schema_version(), ops.model_role_revision_entry_schema_version(),
  ops.model_role_tenant(), ops.model_role_system_authority_partner(),
  ops.model_role_keys(), ops.model_role_occupiable_authority_classes(),
  ops.model_role_is_ref(text), ops.model_role_server_instant(),
  ops.model_role_text_array(uuid,text), ops.model_role_ref_array(uuid,text),
  ops.model_role_preimage(uuid), ops.model_role_digest_of_preimage(jsonb),
  ops.model_role_digest(uuid),
  ops.model_role_structure_error(uuid), ops.model_role_revision_readback(uuid),
  ops.model_role_current_revision(text), ops.model_role_readback(text)
  from public, carr_reader, carr_writer, carr_jobs, carr_authority;

grant execute on function ops.model_role_schema_version(),
  ops.model_role_readback_schema_version(), ops.model_role_revision_entry_schema_version(),
  ops.model_role_tenant(), ops.model_role_system_authority_partner(),
  ops.model_role_keys(), ops.model_role_occupiable_authority_classes(),
  ops.model_role_is_ref(text), ops.model_role_server_instant(),
  ops.model_role_text_array(uuid,text), ops.model_role_ref_array(uuid,text),
  ops.model_role_preimage(uuid), ops.model_role_digest_of_preimage(jsonb),
  ops.model_role_digest(uuid),
  ops.model_role_structure_error(uuid), ops.model_role_revision_readback(uuid),
  ops.model_role_current_revision(text), ops.model_role_readback(text)
  to carr_reader, carr_writer, carr_jobs, carr_authority;

-- The lock helper is granted to nobody. It is reached from inside the two
-- definer writers as the function owner, which is the only access it needs; a
-- runtime grant would be a way to hold a role's write lock without writing.
revoke all on function ops.model_role_lock(text)
  from public, carr_reader, carr_writer, carr_jobs, carr_authority;

revoke all on function
  ops.model_role_record_revision(text,integer,uuid,text,jsonb,jsonb,jsonb),
  ops.model_role_set_current_revision(text,uuid,integer,text,boolean,integer)
  from public, carr_reader, carr_writer, carr_jobs, carr_authority;

-- Recording an inert revision reaches the ordinary writer. Naming which revision
-- is CURRENT reaches the authority bundle only -- and even there it refuses
-- unless session_user is the retained system-authority partner's own principal.
grant execute on function
  ops.model_role_record_revision(text,integer,uuid,text,jsonb,jsonb,jsonb)
  to carr_writer, carr_authority;
grant execute on function
  ops.model_role_set_current_revision(text,uuid,integer,text,boolean,integer)
  to carr_authority;
