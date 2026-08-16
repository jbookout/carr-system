-- 0170_guidance_import_lifecycle.sql
-- Exact, staged import and human batch-decision boundary for the typed
-- Guidance Registry.  This migration deliberately does not activate the
-- registry: registry activation remains a separate human authority action.

begin;

-- The digest preimage is the reviewed compiler artifact's exact UTF-8 bytes.
-- Its top-level `canonicalization` value is `utf8-json-sort-keys-compact-newline/v1`,
-- defined as `json.dumps(sort_keys=True, ensure_ascii=False,
-- separators=(',', ':')) + '\n'`.  The database parses the same bytes for structural validation, but
-- never reserializes them before hashing (which would create an ambiguous,
-- PostgreSQL-specific external preimage).
create or replace function ops.guidance_import_manifest_digest(p_manifest_text text)
returns text language sql immutable as $$
  select encode(digest(convert_to(p_manifest_text,'UTF8'),'sha256'),'hex')
$$;

-- Render the declared portable v1 preimage from parsed JSON without using
-- PostgreSQL's jsonb text output as the hash input.  Objects are sorted by
-- UTF-8/code-point key order, punctuation is compact, and JSON strings are
-- emitted with the database's UTF-8 JSON quoting primitive.  Staging compares
-- this result to the caller's original bytes before hashing those original
-- bytes, so pretty spacing, a different key order, and ASCII-only Unicode
-- escapes cannot masquerade as the named canonicalization.
create or replace function ops.guidance_import_canonical_json(p_value jsonb)
returns text language plpgsql immutable set search_path=ops,public,pg_temp as $$
declare
  v_kind text := jsonb_typeof(p_value);
  v_result text;
begin
  if v_kind='object' then
    select '{' || coalesce(string_agg(
             to_jsonb(entry.key)::text || ':' || ops.guidance_import_canonical_json(entry.value),
             ',' order by entry.key collate "C"), '') || '}'
      into v_result
      from jsonb_each(p_value) as entry(key,value);
    return v_result;
  elsif v_kind='array' then
    select '[' || coalesce(string_agg(
             ops.guidance_import_canonical_json(entry.value), ',' order by entry.ordinality), '') || ']'
      into v_result
      from jsonb_array_elements(p_value) with ordinality as entry(value,ordinality);
    return v_result;
  elsif v_kind='string' then
    return to_jsonb(p_value #>> '{}')::text;
  end if;
  -- The compiler emits only integral structural numbers; jsonb's scalar
  -- representation is identical for that admitted subset.  Booleans and null
  -- are likewise already the portable JSON spellings.
  return p_value::text;
end $$;

create table ops.guidance_import_batch (
  id                  uuid primary key default gen_random_uuid(),
  manifest_digest     text not null unique check (manifest_digest ~ '^[0-9a-f]{64}$'),
  canonical_manifest_text text not null,
  manifest_json       jsonb not null,
  source_manifest_digest text not null check (source_manifest_digest ~ '^[0-9a-f]{64}$'),
  classifier_actor_id uuid not null references actor(id),
  staging_key         text not null unique check (btrim(staging_key) <> ''),
  reason              text not null check (btrim(reason) <> ''),
  created_at          timestamptz not null default now()
);

create table ops.guidance_import_entry (
  id                    uuid primary key default gen_random_uuid(),
  batch_id              uuid not null references ops.guidance_import_batch(id) on delete restrict,
  ordinal               integer not null check (ordinal > 0),
  guidance_id           text not null check (btrim(guidance_id) <> ''),
  source_rule_id        uuid not null references rule(id) on delete restrict,
  source_clause         text not null check (btrim(source_clause) <> ''),
  is_primary            boolean not null,
  split_group_key       text,
  guidance_type         text not null check (guidance_type in
    ('constraint','procedure','doctrine','rubric','preference','precedent','example')),
  scope                 jsonb not null,
  activation            jsonb not null,
  consumer              text not null check (btrim(consumer) <> ''),
  verification          jsonb not null,
  provenance            jsonb not null,
  delivery              jsonb not null,
  is_constitution       boolean not null,
  revision_reason       text not null check (btrim(revision_reason) <> ''),
  situation_mappings    jsonb not null default '[]'::jsonb,
  unique (batch_id, ordinal),
  unique (batch_id, guidance_id),
  unique (batch_id, source_rule_id, source_clause)
);

create table ops.guidance_import_apply_event (
  id                uuid primary key default gen_random_uuid(),
  batch_id          uuid not null unique references ops.guidance_import_batch(id) on delete restrict,
  manifest_digest   text not null check (manifest_digest ~ '^[0-9a-f]{64}$'),
  idempotency_key   text not null unique check (btrim(idempotency_key) <> ''),
  applied_by        uuid not null references actor(id),
  reason            text not null check (btrim(reason) <> ''),
  created_at        timestamptz not null default now()
);

create table ops.guidance_import_mapping_execution (
  id                    uuid primary key default gen_random_uuid(),
  batch_id              uuid not null references ops.guidance_import_batch(id) on delete restrict,
  entry_id              uuid not null references ops.guidance_import_entry(id) on delete restrict,
  ordinal               integer not null check (ordinal > 0),
  concept_id            uuid not null references retrieval_concept(id) on delete restrict,
  doctrine_section_id   uuid not null references doctrine_section(id) on delete restrict,
  proposed_mapping_id   uuid not null references ops.guidance_situation_mapping(id) on delete restrict,
  active_mapping_id     uuid unique references ops.guidance_situation_mapping(id) on delete restrict,
  unique (batch_id, entry_id, ordinal)
);

create table ops.guidance_import_decision_event (
  id                uuid primary key default gen_random_uuid(),
  batch_id          uuid not null references ops.guidance_import_batch(id) on delete restrict,
  manifest_digest   text not null check (manifest_digest ~ '^[0-9a-f]{64}$'),
  state             text not null check (state = 'active'),
  idempotency_key   text not null unique check (btrim(idempotency_key) <> ''),
  authority_actor_id uuid not null references actor(id),
  reason            text not null check (btrim(reason) <> ''),
  created_at        timestamptz not null default now(),
  unique (batch_id, state, manifest_digest, idempotency_key)
);

create trigger guidance_import_batch_append_only before update or delete
  on ops.guidance_import_batch for each row execute function ops.refuse_guidance_history_rewrite();
create trigger guidance_import_entry_append_only before update or delete
  on ops.guidance_import_entry for each row execute function ops.refuse_guidance_history_rewrite();
create trigger guidance_import_apply_event_append_only before update or delete
  on ops.guidance_import_apply_event for each row execute function ops.refuse_guidance_history_rewrite();
create trigger guidance_import_mapping_execution_append_only before update or delete
  on ops.guidance_import_mapping_execution for each row execute function ops.refuse_guidance_history_rewrite();
create trigger guidance_import_decision_event_append_only before update or delete
  on ops.guidance_import_decision_event for each row execute function ops.refuse_guidance_history_rewrite();

create or replace function ops.guidance_import_split_group_id(p_key text)
returns uuid language sql immutable strict as $$
  select (substr(md5('guidance-import-split:' || p_key),1,8) || '-' ||
          substr(md5('guidance-import-split:' || p_key),9,4) || '-' ||
          substr(md5('guidance-import-split:' || p_key),13,4) || '-' ||
          substr(md5('guidance-import-split:' || p_key),17,4) || '-' ||
          substr(md5('guidance-import-split:' || p_key),21,12))::uuid
$$;

create or replace function ops.assert_guidance_import_inventory(p_batch_id uuid)
returns void language plpgsql stable security definer set search_path=ops,public,pg_temp as $$
begin
  if not exists (select 1 from ops.guidance_import_batch where id=p_batch_id) then
    raise exception 'unknown guidance import batch %',p_batch_id;
  end if;
  if exists (
    (select id from rule where status='active')
    except
    (select distinct source_rule_id from ops.guidance_import_entry where batch_id=p_batch_id)
  ) or exists (
    (select distinct source_rule_id from ops.guidance_import_entry where batch_id=p_batch_id)
    except
    (select id from rule where status='active')
  ) then
    raise exception 'guidance import batch source inventory no longer exactly matches active rules';
  end if;
end $$;

create or replace function ops.validate_guidance_import_manifest(p_manifest jsonb)
returns void language plpgsql stable security definer set search_path=ops,public,pg_temp as $$
declare
  v_entry jsonb;
  v_position integer := 0;
  v_previous_key text := null;
  v_constitution_ids text[];
  v_entry_constitution_ids text[];
  v_constitution_source_ids text[];
  v_entry_constitution_source_ids text[];
  v_short text;
  v_uuid text;
begin
  if jsonb_typeof(p_manifest) <> 'object'
     or p_manifest->>'schema' <> 'guidance-activation-manifest/v1'
     or jsonb_typeof(p_manifest->'source_manifest') <> 'object'
     or coalesce(p_manifest->'source_manifest'->>'sha256','') !~ '^[0-9a-f]{64}$'
     or jsonb_typeof(p_manifest->'base_inventory') <> 'object'
     or jsonb_typeof(p_manifest->'base_inventory'->'active_source_ids') <> 'array'
     or jsonb_typeof(p_manifest->'base_inventory'->'source_rule_ids') <> 'object'
     or jsonb_typeof(p_manifest->'constitution_guidance_ids') <> 'array'
     or jsonb_typeof(p_manifest->'constitution_source_rule_ids') <> 'array'
     or jsonb_typeof(p_manifest->'entries') <> 'array'
     or jsonb_array_length(p_manifest->'entries') = 0 then
    raise exception 'guidance import manifest has an invalid top-level contract';
  end if;
  if coalesce(p_manifest->'source_manifest'->>'path','') = ''
     or p_manifest->'source_manifest'->>'manifest' <> 'carr-guidance-migration'
     or p_manifest->'source_manifest'->>'schema_version' <> '1.0.0'
     or p_manifest->'source_manifest'->>'source_classification' <> 'judgment_ambient'
     or coalesce((p_manifest->'source_manifest'->>'entry_count')::integer,0) < 1
     or coalesce(p_manifest->'base_inventory'->>'path','') = ''
     or coalesce(p_manifest->'base_inventory'->>'sha256','') !~ '^[0-9a-f]{64}$'
     or coalesce((p_manifest->'base_inventory'->>'active_source_count')::integer,0) < 1 then
    raise exception 'guidance import manifest provenance or inventory contract is incomplete';
  end if;
  if (p_manifest->'base_inventory'->>'active_source_count')::integer
       <> (select count(*) from jsonb_array_elements_text(p_manifest->'base_inventory'->'active_source_ids')) then
    raise exception 'guidance import manifest active source count does not match its inventory';
  end if;
  if exists (
    select 1 from jsonb_array_elements_text(p_manifest->'base_inventory'->'active_source_ids') id
     where id.value !~ '^[0-9a-f]{8}$'
  ) then
    raise exception 'guidance import manifest base inventory must contain short source ids';
  end if;
  if (select count(*) from jsonb_array_elements_text(p_manifest->'base_inventory'->'active_source_ids'))
     <> (select count(distinct value) from jsonb_array_elements_text(p_manifest->'base_inventory'->'active_source_ids')) then
    raise exception 'guidance import manifest base inventory has duplicate source ids';
  end if;
  for v_short,v_uuid in select key,value from jsonb_each_text(p_manifest->'base_inventory'->'source_rule_ids') loop
    if v_short !~ '^[0-9a-f]{8}$'
       or v_uuid !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
       or v_short <> left(v_uuid,8) then
      raise exception 'guidance import manifest source_rule_ids must map each short id to its matching full UUID';
    end if;
  end loop;
  if (select count(*) from jsonb_object_keys(p_manifest->'base_inventory'->'source_rule_ids'))
       <> (select count(distinct value) from jsonb_each_text(p_manifest->'base_inventory'->'source_rule_ids')) then
    raise exception 'guidance import manifest source_rule_ids has duplicate UUID values';
  end if;
  if exists (
    (select value from jsonb_array_elements_text(p_manifest->'base_inventory'->'active_source_ids'))
    except
    (select source_key.key from jsonb_object_keys(p_manifest->'base_inventory'->'source_rule_ids') as source_key(key))
  ) or exists (
    (select source_key.key from jsonb_object_keys(p_manifest->'base_inventory'->'source_rule_ids') as source_key(key))
    except
    (select value from jsonb_array_elements_text(p_manifest->'base_inventory'->'active_source_ids'))
  ) then
    raise exception 'guidance import manifest short source inventory and UUID map differ';
  end if;
  for v_entry in select value from jsonb_array_elements(p_manifest->'entries') loop
    v_position := v_position + 1;
    if jsonb_typeof(v_entry) <> 'object'
       or coalesce((v_entry->>'ordinal')::integer,0) <> v_position
       or coalesce(v_entry->>'guidance_id','') = ''
       or coalesce(v_entry->>'source_rule_id','') !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
       or coalesce(v_entry->>'source_clause','') = ''
       or jsonb_typeof(v_entry->'is_primary') <> 'boolean'
       or coalesce(v_entry->>'guidance_type','') not in
          ('constraint','procedure','doctrine','rubric','preference','precedent','example')
       or jsonb_typeof(v_entry->'scope') <> 'object'
       or jsonb_typeof(v_entry->'activation') <> 'object'
       or coalesce(v_entry->>'consumer','') = ''
       or jsonb_typeof(v_entry->'verification') <> 'object'
       or jsonb_typeof(v_entry->'provenance') <> 'object'
       or jsonb_typeof(v_entry->'delivery') <> 'object'
       or jsonb_typeof(v_entry->'is_constitution') <> 'boolean'
       or coalesce((v_entry->'lifecycle'->>'version')::integer,0) <> 1
       or coalesce(v_entry->>'reason','') = ''
       or jsonb_typeof(coalesce(v_entry->'activation'->'situation_mappings','[]'::jsonb)) <> 'array' then
      raise exception 'guidance import entry % has an invalid contract',v_position;
    end if;
    if v_previous_key is not null and v_entry->>'guidance_id' <= v_previous_key then
      raise exception 'guidance import entries must be strictly sorted by guidance_id';
    end if;
    v_previous_key := v_entry->>'guidance_id';
    if not exists (
      select 1 from jsonb_each_text(p_manifest->'base_inventory'->'source_rule_ids') source_map
       where source_map.value=(v_entry->>'source_rule_id')) then
      raise exception 'guidance import entry % names a source outside base inventory',v_position;
    end if;
    if exists (
      select 1 from jsonb_array_elements(coalesce(v_entry->'activation'->'situation_mappings','[]'::jsonb)) mapping
       where jsonb_typeof(mapping.value) <> 'object'
          or coalesce(mapping.value->>'concept_id','') !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
          or coalesce(mapping.value->>'doctrine_section_id','') !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
          or coalesce(mapping.value->>'reason','') = '') then
      raise exception 'guidance import entry % has an unresolved doctrine mapping contract',v_position;
    end if;
    if v_entry->>'guidance_type' = 'doctrine'
       and jsonb_array_length(coalesce(v_entry->'activation'->'situation_mappings','[]'::jsonb)) = 0 then
      raise exception 'doctrine guidance requires exact situation mappings';
    end if;
    if (v_entry->>'is_constitution')::boolean
       and not (v_entry->>'is_primary')::boolean then
      raise exception 'constitution guidance must be a primary source clause';
    end if;
    if v_entry->>'guidance_type' <> 'doctrine'
       and jsonb_array_length(coalesce(v_entry->'activation'->'situation_mappings','[]'::jsonb)) <> 0 then
      raise exception 'only doctrine guidance may name situation mappings';
    end if;
  end loop;
  if (select count(distinct entry.value->>'guidance_id')
        from jsonb_array_elements(p_manifest->'entries') entry)
       <> jsonb_array_length(p_manifest->'entries') then
    raise exception 'guidance import manifest has duplicate entry keys';
  end if;
  if exists (
    (select value from jsonb_each_text(p_manifest->'base_inventory'->'source_rule_ids'))
    except
    (select distinct entry.value->>'source_rule_id' from jsonb_array_elements(p_manifest->'entries') entry)
  ) or exists (
    (select distinct entry.value->>'source_rule_id' from jsonb_array_elements(p_manifest->'entries') entry)
    except
    (select value from jsonb_each_text(p_manifest->'base_inventory'->'source_rule_ids'))
  ) then
    raise exception 'guidance import manifest entries do not exactly cover base inventory';
  end if;
  select array_agg(value order by value) into v_constitution_ids
    from jsonb_array_elements_text(p_manifest->'constitution_guidance_ids');
  select array_agg(entry.value->>'guidance_id' order by entry.value->>'guidance_id')
    into v_entry_constitution_ids
    from jsonb_array_elements(p_manifest->'entries') entry
   where (entry.value->>'is_constitution')::boolean;
  select array_agg(value order by value) into v_constitution_source_ids
    from jsonb_array_elements_text(p_manifest->'constitution_source_rule_ids');
  select array_agg(distinct entry.value->>'source_rule_id' order by entry.value->>'source_rule_id')
    into v_entry_constitution_source_ids
    from jsonb_array_elements(p_manifest->'entries') entry
   where (entry.value->>'is_constitution')::boolean and (entry.value->>'is_primary')::boolean;
  if v_constitution_ids is null or cardinality(v_constitution_ids) not between 5 and 10
     or v_constitution_ids is distinct from v_entry_constitution_ids then
    raise exception 'guidance import manifest must bind exactly five to ten selected constitution guidance ids';
  end if;
  if v_constitution_source_ids is null or cardinality(v_constitution_source_ids) not between 5 and 10
     or v_constitution_source_ids is distinct from v_entry_constitution_source_ids then
    raise exception 'guidance import manifest constitution source ids do not exactly match flagged primary entries';
  end if;
end $$;

-- Stage one immutable import contract.  The caller must be a non-human
-- classifier actor (normally codex); this operation cannot record a human
-- approval, lifecycle event, active mapping, or registry activation.
create or replace function ops.stage_guidance_import_batch(
  p_manifest_digest text,
  p_canonical_manifest_text text,
  p_classifier_actor_id uuid,
  p_idempotency_key text,
  p_reason text
) returns uuid
language plpgsql security definer set search_path=ops,public,pg_temp as $$
declare
  v_batch_id uuid;
  v_source_digest text;
  v_manifest jsonb;
  v_existing record;
  v_entry jsonb;
  v_ordinal integer := 0;
begin
  if p_manifest_digest !~ '^[0-9a-f]{64}$'
     or coalesce(btrim(p_idempotency_key),'')=''
     or coalesce(btrim(p_reason),'')='' then
    raise exception 'guidance import staging requires digest, idempotency key and reason';
  end if;
  if coalesce(p_canonical_manifest_text,'')='' or right(p_canonical_manifest_text,1) <> E'\n'
     or position(E'\r' in p_canonical_manifest_text) <> 0 then
    raise exception 'guidance import manifest must be the declared newline-terminated UTF-8 canonical artifact';
  end if;
  begin
    v_manifest := p_canonical_manifest_text::jsonb;
  exception when others then
    raise exception 'guidance import manifest is not valid JSON';
  end;
  perform ops.validate_guidance_import_manifest(v_manifest);
  if coalesce(v_manifest->>'canonicalization','') <> 'utf8-json-sort-keys-compact-newline/v1' then
    raise exception 'guidance import manifest has an unsupported canonicalization identifier';
  end if;
  if current_setting('server_encoding') <> 'UTF8'
     or p_canonical_manifest_text <> ops.guidance_import_canonical_json(v_manifest) || E'\n' then
    raise exception 'guidance import manifest bytes do not match utf8-json-sort-keys-compact-newline/v1';
  end if;
  if p_manifest_digest is distinct from ops.guidance_import_manifest_digest(p_canonical_manifest_text) then
    raise exception 'guidance import digest does not match the canonical manifest';
  end if;
  select id,manifest_digest,canonical_manifest_text,manifest_json,source_manifest_digest,classifier_actor_id,reason
    into v_existing from ops.guidance_import_batch where staging_key=p_idempotency_key;
  if v_existing.id is not null then
    if v_existing.manifest_digest<>p_manifest_digest
       or v_existing.canonical_manifest_text is distinct from p_canonical_manifest_text
       or v_existing.manifest_json is distinct from v_manifest
       or v_existing.classifier_actor_id<>p_classifier_actor_id
       or v_existing.reason<>p_reason then
      raise exception 'idempotency key already names a different guidance import stage';
    end if;
    return v_existing.id;
  end if;
  if exists (select 1 from ops.guidance_import_batch where manifest_digest=p_manifest_digest) then
    raise exception 'guidance import digest is already staged under another idempotency key';
  end if;
  if not exists (select 1 from actor where id=p_classifier_actor_id
                 and kind in ('automation','system') and active) then
    raise exception 'guidance import staging requires an active non-human classifier actor';
  end if;
  v_source_digest := v_manifest->'source_manifest'->>'sha256';
  insert into ops.guidance_import_batch
    (manifest_digest,canonical_manifest_text,manifest_json,source_manifest_digest,classifier_actor_id,staging_key,reason)
  values (p_manifest_digest,p_canonical_manifest_text,v_manifest,v_source_digest,p_classifier_actor_id,p_idempotency_key,p_reason)
  returning id into v_batch_id;
  for v_entry in select value from jsonb_array_elements(v_manifest->'entries') loop
    v_ordinal := v_ordinal + 1;
    insert into ops.guidance_import_entry
      (batch_id,ordinal,guidance_id,source_rule_id,source_clause,is_primary,split_group_key,
       guidance_type,scope,activation,consumer,verification,provenance,delivery,
       is_constitution,revision_reason,situation_mappings)
    values
      (v_batch_id,v_ordinal,v_entry->>'guidance_id',(v_entry->>'source_rule_id')::uuid,
       v_entry->>'source_clause',(v_entry->>'is_primary')::boolean,
       nullif(v_entry->>'split_group_key',''),v_entry->>'guidance_type',v_entry->'scope',
       v_entry->'activation',v_entry->>'consumer',v_entry->'verification',v_entry->'provenance',
       v_entry->'delivery',(v_entry->>'is_constitution')::boolean,v_entry->>'reason',
       coalesce(v_entry->'activation'->'situation_mappings','[]'::jsonb));
  end loop;
  perform ops.assert_guidance_import_inventory(v_batch_id);
  return v_batch_id;
end $$;

-- Apply a staged contract into proposed items/revisions and proposed doctrine
-- mappings.  This routine is deliberately writer-only and never makes a
-- revision or mapping active.  Existing data is accepted only when byte-for-
-- byte-equivalent at the stored JSONB contract level; otherwise it refuses.
create or replace function ops.apply_guidance_import_batch(
  p_batch_id uuid,p_manifest_digest text,p_idempotency_key text,p_reason text
) returns uuid
language plpgsql security definer set search_path=ops,public,pg_temp as $$
declare
  v_batch ops.guidance_import_batch%rowtype;
  v_existing record;
  v_entry ops.guidance_import_entry%rowtype;
  v_item_id uuid;
  v_revision_id uuid;
  v_mapping jsonb;
  v_mapping_ordinal integer;
  v_proposed_mapping_id uuid;
  v_apply_event_id uuid;
begin
  if p_manifest_digest !~ '^[0-9a-f]{64}$'
     or coalesce(btrim(p_idempotency_key),'')=''
     or coalesce(btrim(p_reason),'')='' then
    raise exception 'guidance import apply requires digest, idempotency key and reason';
  end if;
  select * into v_batch from ops.guidance_import_batch where id=p_batch_id;
  if v_batch.id is null or v_batch.manifest_digest<>p_manifest_digest then
    raise exception 'unknown guidance import batch or mismatched manifest digest';
  end if;
  select * into v_existing from ops.guidance_import_apply_event where idempotency_key=p_idempotency_key;
  if v_existing.id is not null then
    if v_existing.batch_id<>p_batch_id or v_existing.manifest_digest<>p_manifest_digest
       or v_existing.reason<>p_reason then
      raise exception 'idempotency key already names a different guidance import apply';
    end if;
    return v_existing.id;
  end if;
  if exists (select 1 from ops.guidance_import_apply_event where batch_id=p_batch_id) then
    raise exception 'guidance import batch is already applied under another idempotency key';
  end if;
  perform ops.assert_guidance_import_inventory(p_batch_id);
  for v_entry in select * from ops.guidance_import_entry where batch_id=p_batch_id order by ordinal loop
    select id into v_item_id from ops.guidance_item
      where source_rule_id=v_entry.source_rule_id and source_clause=v_entry.source_clause;
    if v_item_id is null then
      insert into ops.guidance_item
        (source_rule_id,source_clause,is_primary,split_group_id,created_by)
      values (v_entry.source_rule_id,v_entry.source_clause,v_entry.is_primary,
              ops.guidance_import_split_group_id(v_entry.split_group_key),v_batch.classifier_actor_id)
      returning id into v_item_id;
    elsif not exists (
      select 1 from ops.guidance_item where id=v_item_id
        and is_primary=v_entry.is_primary
        and split_group_id is not distinct from ops.guidance_import_split_group_id(v_entry.split_group_key)) then
      raise exception 'existing guidance item conflicts with import entry %',v_entry.guidance_id;
    end if;
    select id into v_revision_id from ops.guidance_revision
      where guidance_item_id=v_item_id and version=1;
    if v_revision_id is null then
      if exists (select 1 from ops.guidance_revision where guidance_item_id=v_item_id) then
        raise exception 'existing guidance item has a non-import revision history for entry %',v_entry.guidance_id;
      end if;
      insert into ops.guidance_revision
        (guidance_item_id,version,guidance_type,scope,activation,consumer,verification,
         provenance,delivery,is_constitution,classified_by,reason)
      values (v_item_id,1,v_entry.guidance_type,v_entry.scope,v_entry.activation,v_entry.consumer,
              v_entry.verification,v_entry.provenance,v_entry.delivery,v_entry.is_constitution,
              v_batch.classifier_actor_id,v_entry.revision_reason)
      returning id into v_revision_id;
    elsif not exists (
      select 1 from ops.guidance_revision where id=v_revision_id
        and guidance_type=v_entry.guidance_type and scope=v_entry.scope
        and activation=v_entry.activation and consumer=v_entry.consumer
        and verification=v_entry.verification and provenance=v_entry.provenance
        and delivery=v_entry.delivery and is_constitution=v_entry.is_constitution
        and reason=v_entry.revision_reason and classified_by=v_batch.classifier_actor_id) then
      raise exception 'existing guidance revision conflicts with import entry %',v_entry.guidance_id;
    end if;
    v_mapping_ordinal := 0;
    for v_mapping in select value from jsonb_array_elements(v_entry.situation_mappings) loop
      v_mapping_ordinal := v_mapping_ordinal + 1;
      select proposed_mapping_id into v_proposed_mapping_id
        from ops.guidance_import_mapping_execution
       where batch_id=p_batch_id and entry_id=v_entry.id and ordinal=v_mapping_ordinal;
      if v_proposed_mapping_id is null then
        v_proposed_mapping_id := ops.propose_guidance_situation_mapping(
          v_revision_id,(v_mapping->>'concept_id')::uuid,
          (v_mapping->>'doctrine_section_id')::uuid,v_mapping->>'reason');
        insert into ops.guidance_import_mapping_execution
          (batch_id,entry_id,ordinal,concept_id,doctrine_section_id,proposed_mapping_id)
        values (p_batch_id,v_entry.id,v_mapping_ordinal,(v_mapping->>'concept_id')::uuid,
                (v_mapping->>'doctrine_section_id')::uuid,v_proposed_mapping_id);
      end if;
    end loop;
  end loop;
  insert into ops.guidance_import_apply_event
    (batch_id,manifest_digest,idempotency_key,applied_by,reason)
  values (p_batch_id,p_manifest_digest,p_idempotency_key,v_batch.classifier_actor_id,p_reason)
  returning id into v_apply_event_id;
  return v_apply_event_id;
end $$;

-- A human authority approves every staged revision from
-- one exact manifest.  Per-revision receipts remain immutable and bind each
-- stored revision contract; this batch event is only the atomic envelope.
create or replace function ops.decide_guidance_import_batch(
  p_batch_id uuid,p_manifest_digest text,p_state text,p_idempotency_key text,p_reason text
) returns uuid
language plpgsql security definer set search_path=ops,public,pg_temp as $$
declare
  v_batch ops.guidance_import_batch%rowtype;
  v_authority_slug text;
  v_authority_actor uuid;
  v_registry_owner uuid;
  v_existing record;
  v_decision_id uuid;
  v_entry ops.guidance_import_entry%rowtype;
  v_item_id uuid;
  v_revision_id uuid;
  v_lifecycle_id uuid;
  v_binding_id uuid;
  v_mapping ops.guidance_import_mapping_execution%rowtype;
  v_active_mapping_id uuid;
begin
  v_authority_slug := ops.authority_actor_slug();
  select id into v_authority_actor from actor where slug=v_authority_slug and kind='human' and active;
  select created_by into v_registry_owner from ops.guidance_registry where singleton;
  if v_authority_actor is null or v_authority_actor<>v_registry_owner then
    raise exception 'guidance import batch decision requires the accountable registry human authority';
  end if;
  if p_state <> 'active' or p_manifest_digest !~ '^[0-9a-f]{64}$'
     or coalesce(btrim(p_idempotency_key),'')='' or coalesce(btrim(p_reason),'')='' then
    raise exception 'guidance import decision requires state, digest, idempotency key and reason';
  end if;
  select * into v_batch from ops.guidance_import_batch where id=p_batch_id;
  if v_batch.id is null or v_batch.manifest_digest<>p_manifest_digest
     or not exists (select 1 from ops.guidance_import_apply_event where batch_id=p_batch_id) then
    raise exception 'guidance import batch must be staged, exact-digest matched and applied before a decision';
  end if;
  perform ops.assert_guidance_import_inventory(p_batch_id);
  select * into v_existing from ops.guidance_import_decision_event where idempotency_key=p_idempotency_key;
  if v_existing.id is not null then
    if v_existing.batch_id<>p_batch_id or v_existing.manifest_digest<>p_manifest_digest
       or v_existing.state<>p_state or v_existing.authority_actor_id<>v_authority_actor
       or v_existing.reason<>p_reason then
      raise exception 'idempotency key already names a different guidance import decision';
    end if;
    return v_existing.id;
  end if;
  insert into ops.guidance_import_decision_event
    (batch_id,manifest_digest,state,idempotency_key,authority_actor_id,reason)
  values (p_batch_id,p_manifest_digest,p_state,p_idempotency_key,v_authority_actor,p_reason)
  returning id into v_decision_id;
  for v_entry in select * from ops.guidance_import_entry where batch_id=p_batch_id order by ordinal loop
    select i.id,r.id into v_item_id,v_revision_id from ops.guidance_item i
      join ops.guidance_revision r on r.guidance_item_id=i.id and r.version=1
     where i.source_rule_id=v_entry.source_rule_id and i.source_clause=v_entry.source_clause;
    if v_revision_id is null then
      raise exception 'applied import entry % has no exact revision',v_entry.guidance_id;
    end if;
    v_lifecycle_id := ops.record_guidance_decision(
      v_revision_id,p_state,
      encode(digest(convert_to(p_idempotency_key || ':revision:' || v_revision_id::text,'UTF8'),'sha256'),'hex'),
      p_reason);
    if p_state='active' then
      select authority_binding_id into v_binding_id from ops.guidance_lifecycle_event where id=v_lifecycle_id;
      for v_mapping in select * from ops.guidance_import_mapping_execution
          where batch_id=p_batch_id and entry_id=v_entry.id
            and active_mapping_id is null order by ordinal loop
        if exists (select 1 from ops.guidance_import_mapping_execution prior
                    where prior.proposed_mapping_id=v_mapping.proposed_mapping_id
                      and prior.active_mapping_id is not null) then
          continue;
        end if;
        v_active_mapping_id := ops.activate_guidance_situation_mapping(
          v_mapping.proposed_mapping_id,v_binding_id,p_reason);
        -- Mapping execution is append-only; record the activated counterpart
        -- in a second immutable row rather than rewriting the proposal row.
        insert into ops.guidance_import_mapping_execution
          (batch_id,entry_id,ordinal,concept_id,doctrine_section_id,proposed_mapping_id,active_mapping_id)
        values (p_batch_id,v_entry.id,v_mapping.ordinal + 1000000,
                v_mapping.concept_id,v_mapping.doctrine_section_id,
                v_mapping.proposed_mapping_id,v_active_mapping_id);
      end loop;
    end if;
  end loop;
  return v_decision_id;
end $$;

-- Lifecycle evidence remains readable after deactivation, but no reader-facing
-- delivery projection may expose its materialized guidance until the singleton
-- registry is active.  Keep the ungated materialization private to in-database
-- activation validation: activation must validate its candidate before it
-- appends the active registry event that opens the public delivery fence.
create or replace view ops.v_guidance_materialized_current as
select distinct on (i.id)
       i.id as guidance_item_id,
       i.source_rule_id,
       i.guidance_intake_id,
       i.source_clause,
       i.is_primary,
       i.split_group_id,
       r.id as guidance_revision_id,
       r.version,
       r.guidance_type,
       r.scope,
       r.activation,
       r.consumer,
       r.verification,
       r.provenance,
       r.delivery,
       r.is_constitution,
       r.classified_by,
       r.reason,
       r.lifecycle_at
  from ops.guidance_item i
  join ops.v_guidance_revision_state r on r.guidance_item_id=i.id
 where r.lifecycle_status='active'
 order by i.id,r.version desc,r.lifecycle_at desc,r.id desc;

create or replace view ops.v_guidance_materialized_situation_mapping_current as
select distinct on (m.guidance_revision_id,m.concept_id,m.doctrine_section_id)
       m.*
  from ops.guidance_situation_mapping m
 order by m.guidance_revision_id,m.concept_id,m.doctrine_section_id,
          m.mapping_seq desc;

create or replace view ops.v_guidance_current as
select g.*
  from ops.v_guidance_materialized_current g
 where exists (
   select 1
     from ops.v_guidance_registry_state s
     join ops.guidance_registry registry on registry.id=s.registry_id and registry.singleton
    where s.state='active'
 );

create or replace view ops.v_guidance_situation_mapping_current as
select m.*
  from ops.v_guidance_materialized_situation_mapping_current m
 where exists (
   select 1
     from ops.v_guidance_registry_state s
     join ops.guidance_registry registry on registry.id=s.registry_id and registry.singleton
    where s.state='active'
 );

-- standing_guidance is a direct reader-facing function, so retain an explicit
-- singleton-state fence in addition to its dependency on v_guidance_current.
create or replace function ops.standing_guidance(
  p_actor text,
  p_workflow text default null,
  p_surface text default null,
  p_tier text default null
) returns table(
  source_rule_id uuid,
  statement text,
  human_quote text,
  taught_by text,
  personal_to text,
  scope jsonb,
  guidance_type text,
  is_constitution boolean
)
language sql stable as $$
  select r.id,r.statement,r.human_quote,teacher.display_name,owner.slug,g.scope,
         g.guidance_type,g.is_constitution
    from ops.v_guidance_current g
    join rule r on r.id=g.source_rule_id and r.status='active'
    join actor teacher on teacher.id=r.taught_by
    left join actor owner on owner.id=r.personal_to
   where exists (
           select 1
             from ops.v_guidance_registry_state s
             join ops.guidance_registry registry
               on registry.id=s.registry_id and registry.singleton
            where s.state='active'
         )
     and (r.personal_to is null or owner.slug=p_actor)
     and (
       g.is_constitution
       or (g.guidance_type='constraint' and exists (
         select 1 from ops.applicable_rules(p_workflow,p_surface,p_tier) ar
          where ar.rule_id=r.id))
     )
   order by g.is_constitution desc,r.personal_to nulls first,r.created_at,r.id
$$;

-- Prove that the exact approved batch, not merely its primary-rule coverage,
-- is what the registry would expose.  This catches stale extras, omitted split
-- clauses, and doctrine mappings that did not become retrievable.
create or replace function ops.assert_guidance_import_materialization(p_batch_id uuid)
returns void language plpgsql stable security definer set search_path=ops,public,pg_temp as $$
begin
  if not exists (select 1 from ops.guidance_import_apply_event where batch_id=p_batch_id)
     or not exists (select 1 from ops.guidance_import_decision_event
                     where batch_id=p_batch_id and state='active') then
    raise exception 'guidance import batch is not applied and human-approved';
  end if;
  if (select count(*) from ops.guidance_import_entry where batch_id=p_batch_id)
     <> (select count(*) from ops.guidance_import_entry e
          join ops.guidance_item i on i.source_rule_id=e.source_rule_id and i.source_clause=e.source_clause
          join ops.guidance_revision r on r.guidance_item_id=i.id and r.version=1
          where e.batch_id=p_batch_id) then
    raise exception 'guidance import batch lacks an exact materialized revision for one or more entries';
  end if;
  if exists (
    (select g.guidance_revision_id from ops.v_guidance_materialized_current g)
    except
    (select r.id from ops.guidance_import_entry e
      join ops.guidance_item i on i.source_rule_id=e.source_rule_id and i.source_clause=e.source_clause
      join ops.guidance_revision r on r.guidance_item_id=i.id and r.version=1
     where e.batch_id=p_batch_id)
  ) or exists (
    (select r.id from ops.guidance_import_entry e
      join ops.guidance_item i on i.source_rule_id=e.source_rule_id and i.source_clause=e.source_clause
      join ops.guidance_revision r on r.guidance_item_id=i.id and r.version=1
     where e.batch_id=p_batch_id)
    except
    (select g.guidance_revision_id from ops.v_guidance_materialized_current g)
  ) then
    raise exception 'active guidance revisions do not exactly match the approved import batch';
  end if;
  if exists (
    (select m.guidance_revision_id,m.concept_id,m.doctrine_section_id
       from ops.v_guidance_materialized_situation_mapping_current m
       join ops.v_guidance_materialized_current g on g.guidance_revision_id=m.guidance_revision_id
      where m.state='active' and g.guidance_type='doctrine')
    except
    (select r.id,x.concept_id,x.doctrine_section_id
       from ops.guidance_import_mapping_execution x
       join ops.guidance_import_entry e on e.id=x.entry_id
       join ops.guidance_item i on i.source_rule_id=e.source_rule_id and i.source_clause=e.source_clause
       join ops.guidance_revision r on r.guidance_item_id=i.id and r.version=1
      where x.batch_id=p_batch_id)
  ) or exists (
    (select r.id,x.concept_id,x.doctrine_section_id
       from ops.guidance_import_mapping_execution x
       join ops.guidance_import_entry e on e.id=x.entry_id
       join ops.guidance_item i on i.source_rule_id=e.source_rule_id and i.source_clause=e.source_clause
       join ops.guidance_revision r on r.guidance_item_id=i.id and r.version=1
      where x.batch_id=p_batch_id)
    except
    (select m.guidance_revision_id,m.concept_id,m.doctrine_section_id
       from ops.v_guidance_materialized_situation_mapping_current m
       join ops.v_guidance_materialized_current g on g.guidance_revision_id=m.guidance_revision_id
      where m.state='active' and g.guidance_type='doctrine')
  ) then
    raise exception 'active doctrine mappings do not exactly match the approved import batch';
  end if;
end $$;

-- This authority preflight intentionally reads the private materialization
-- layer.  It runs before activation appends the singleton active event, so the
-- reader-facing delivery fence must not turn a valid candidate into a false
-- coverage failure.
create or replace function ops.assert_guidance_registry_coverage()
returns table(source_rule_id uuid, issue text)
language sql stable security definer set search_path=ops,public,pg_temp as $$
  with active_rules as (
    select id from rule where status='active'
  ), primary_counts as (
    select ar.id,
           count(g.*) filter (where g.is_primary) as primary_count
      from active_rules ar
      left join ops.v_guidance_materialized_current g on g.source_rule_id=ar.id
     group by ar.id
  )
  select id,
         case when primary_count=0 then 'missing active primary guidance'
              else 'multiple active primary guidance records' end
    from primary_counts where primary_count <> 1
  union all
  select g.source_rule_id,'constraint lacks admitted installed enforcement projection'
    from ops.v_guidance_materialized_current g
   where g.is_primary and g.guidance_type='constraint'
     and not exists (
       select 1
         from ops.rule_admission a
         join ops.rule_enforcement_point ep
           on ep.rule_id=a.rule_id and ep.installed
        where a.rule_id=g.source_rule_id and a.state='admitted')
  union all
  select g.source_rule_id,'doctrine lacks active WR-AI-006 situation bridge'
    from ops.v_guidance_materialized_current g
   where g.is_primary and g.guidance_type='doctrine'
     and not exists (
       select 1
         from ops.v_guidance_materialized_situation_mapping_current m
         join retrieval_concept c on c.id=m.concept_id and c.status='approved'
         join doctrine_section s on s.id=m.doctrine_section_id and s.status='active'
         join doctrine_concept_mapping dcm
           on dcm.concept_id=m.concept_id
          and dcm.section_id=m.doctrine_section_id
          and dcm.status='approved'
        where m.guidance_revision_id=g.guidance_revision_id and m.state='active')
$$;

-- Bind registry activation to an actually staged, applied, human-approved
-- manifest.  It preserves the original function signature for the MCP
-- boundary, but removes the former free-form digest route.
create or replace function ops.activate_guidance_registry(
  p_registry_id uuid,p_manifest_digest text,p_idempotency_key text,p_reason text
) returns uuid
language plpgsql security definer set search_path=ops,public,pg_temp as $$
declare
  v_batch_id uuid;
  authority_slug text;
  authority_actor uuid;
  registry_owner uuid;
  receipt_id uuid;
  event_id uuid;
  constitution_count integer;
  coverage_count integer;
  existing record;
begin
  authority_slug := ops.authority_actor_slug();
  select id into authority_actor from actor
   where slug=authority_slug and kind='human' and active;
  if authority_actor is null then
    raise exception 'guidance registry activation requires an admitted human authority actor';
  end if;
  if p_manifest_digest !~ '^[0-9a-f]{64}$' or coalesce(btrim(p_idempotency_key),'')=''
     or coalesce(btrim(p_reason),'')='' then
    raise exception 'activation requires a sha256 manifest digest, idempotency key and reason';
  end if;
  select created_by into registry_owner from ops.guidance_registry where id=p_registry_id;
  if registry_owner is null then
    raise exception 'unknown guidance registry %',p_registry_id;
  end if;
  if registry_owner <> authority_actor then
    raise exception 'guidance registry activation requires its accountable human authority actor';
  end if;
  select b.id into v_batch_id from ops.guidance_import_batch b
   where b.manifest_digest=p_manifest_digest
     and exists (select 1 from ops.guidance_import_apply_event a where a.batch_id=b.id)
     and exists (select 1 from ops.guidance_import_decision_event d
                  where d.batch_id=b.id and d.manifest_digest=p_manifest_digest and d.state='active');
  if v_batch_id is null then
    raise exception 'registry activation requires an applied, human-approved exact guidance import manifest';
  end if;
  perform ops.assert_guidance_import_inventory(v_batch_id);
  perform ops.assert_guidance_import_materialization(v_batch_id);
  perform pg_advisory_xact_lock(
    hashtextextended('guidance-registry-activation:' || p_idempotency_key,0));
  select ar.id,ar.kind,ar.subject_type,ar.subject_id,ar.actor_id,ar.decision,
         ar.contract_hash,ge.id as event_id,ge.manifest_digest,ge.reason
    into existing
    from ops.authority_receipt ar
    left join ops.guidance_registry_event ge on ge.authority_receipt_id=ar.id
   where ar.idempotency_key=p_idempotency_key;
  if existing.id is not null then
    if existing.kind<>'activation' or existing.subject_type<>'guidance'
       or existing.subject_id<>p_registry_id or existing.actor_id<>authority_actor
       or existing.decision<>'approved' or existing.contract_hash<>p_manifest_digest
       or existing.event_id is null or existing.manifest_digest<>p_manifest_digest
       or existing.reason<>p_reason then
      raise exception 'idempotency key already names a different or incomplete guidance registry activation';
    end if;
    return existing.event_id;
  end if;
  select count(*) into constitution_count
    from ops.v_guidance_materialized_current where is_constitution;
  if constitution_count not between 5 and 10 then
    raise exception 'guidance constitution must contain between 5 and 10 active items';
  end if;
  select count(*) into coverage_count from ops.assert_guidance_registry_coverage();
  if coverage_count <> 0 then
    raise exception 'guidance registry has % coverage failure(s)',coverage_count;
  end if;
  insert into ops.authority_receipt
    (idempotency_key,kind,subject_type,subject_id,actor_id,decision,
     contract_hash,evidence_refs)
  values
    (p_idempotency_key,'activation','guidance',p_registry_id,authority_actor,
     'approved',p_manifest_digest,array[p_registry_id::text])
  returning id into receipt_id;
  insert into ops.guidance_registry_event
    (registry_id,state,authority_receipt_id,manifest_digest,reason)
  values (p_registry_id,'active',receipt_id,p_manifest_digest,p_reason)
  returning id into event_id;
  return event_id;
end $$;

-- Registry deactivation is the reversible complement to activation.  It is a
-- separate human authority receipt bound to the digest of the active registry
-- state; no writer/session can silently turn standing guidance off.
create or replace function ops.deactivate_guidance_registry(
  p_registry_id uuid,p_manifest_digest text,p_idempotency_key text,p_reason text
) returns uuid
language plpgsql security definer set search_path=ops,public,pg_temp as $$
declare
  v_authority_slug text;
  v_authority_actor uuid;
  v_registry_owner uuid;
  v_active_digest text;
  v_existing record;
  v_receipt_id uuid;
  v_event_id uuid;
begin
  v_authority_slug := ops.authority_actor_slug();
  select id into v_authority_actor from actor where slug=v_authority_slug and kind='human' and active;
  select created_by into v_registry_owner from ops.guidance_registry where id=p_registry_id;
  if v_authority_actor is null or v_registry_owner is null or v_authority_actor<>v_registry_owner then
    raise exception 'guidance registry deactivation requires its accountable human authority actor';
  end if;
  if p_manifest_digest !~ '^[0-9a-f]{64}$' or coalesce(btrim(p_idempotency_key),'')=''
     or coalesce(btrim(p_reason),'')='' then
    raise exception 'deactivation requires a sha256 manifest digest, idempotency key and reason';
  end if;
  perform pg_advisory_xact_lock(hashtextextended('guidance-registry-deactivation:' || p_idempotency_key,0));
  select ar.id,ar.kind,ar.subject_type,ar.subject_id,ar.actor_id,ar.decision,
         ar.contract_hash,ge.id as event_id,ge.manifest_digest,ge.reason
    into v_existing from ops.authority_receipt ar
    left join ops.guidance_registry_event ge on ge.authority_receipt_id=ar.id
   where ar.idempotency_key=p_idempotency_key;
  if v_existing.id is not null then
    if v_existing.kind<>'rejection' or v_existing.subject_type<>'guidance'
       or v_existing.subject_id<>p_registry_id or v_existing.actor_id<>v_authority_actor
       or v_existing.decision<>'retired' or v_existing.contract_hash<>p_manifest_digest
       or v_existing.event_id is null or v_existing.manifest_digest<>p_manifest_digest
       or v_existing.reason<>p_reason then
      raise exception 'idempotency key already names a different or incomplete guidance registry deactivation';
    end if;
    return v_existing.event_id;
  end if;
  select manifest_digest into v_active_digest from ops.v_guidance_registry_state
   where registry_id=p_registry_id and state='active';
  if v_active_digest is null or v_active_digest<>p_manifest_digest then
    raise exception 'guidance registry deactivation requires the exact currently active manifest digest';
  end if;
  insert into ops.authority_receipt
    (idempotency_key,kind,subject_type,subject_id,actor_id,decision,contract_hash,evidence_refs)
  values (p_idempotency_key,'rejection','guidance',p_registry_id,v_authority_actor,
          'retired',p_manifest_digest,array[p_registry_id::text])
  returning id into v_receipt_id;
  insert into ops.guidance_registry_event
    (registry_id,state,authority_receipt_id,manifest_digest,reason)
  values (p_registry_id,'inactive',v_receipt_id,p_manifest_digest,p_reason)
  returning id into v_event_id;
  return v_event_id;
end $$;

grant select on ops.guidance_import_batch,ops.guidance_import_entry,
  ops.guidance_import_apply_event,ops.guidance_import_mapping_execution,
  ops.guidance_import_decision_event to carr_reader,carr_writer,carr_authority;

-- The raw materialization views exist only so activation can validate before
-- it opens the public registry fence.  They are not a reader escape hatch.
revoke all on ops.v_guidance_materialized_current,
  ops.v_guidance_materialized_situation_mapping_current
  from public,carr_reader,carr_writer,carr_authority;

revoke all on function ops.guidance_import_manifest_digest(text) from public;
revoke all on function ops.guidance_import_canonical_json(jsonb) from public;
revoke all on function ops.guidance_import_split_group_id(text) from public;
revoke all on function ops.assert_guidance_import_inventory(uuid) from public;
revoke all on function ops.assert_guidance_import_materialization(uuid) from public;
revoke all on function ops.validate_guidance_import_manifest(jsonb) from public;
revoke all on function ops.assert_guidance_registry_coverage()
  from public,carr_reader,carr_writer,carr_authority;
revoke all on function ops.stage_guidance_import_batch(text,text,uuid,text,text) from public;
revoke all on function ops.apply_guidance_import_batch(uuid,text,text,text) from public;
revoke all on function ops.decide_guidance_import_batch(uuid,text,text,text,text) from public,carr_writer;
revoke all on function ops.deactivate_guidance_registry(uuid,text,text,text) from public,carr_writer;

grant execute on function ops.guidance_import_manifest_digest(text) to carr_reader,carr_writer,carr_authority;
grant execute on function ops.stage_guidance_import_batch(text,text,uuid,text,text) to carr_writer;
grant execute on function ops.apply_guidance_import_batch(uuid,text,text,text) to carr_writer;
grant execute on function ops.decide_guidance_import_batch(uuid,text,text,text,text) to carr_authority;
grant execute on function ops.deactivate_guidance_registry(uuid,text,text,text) to carr_authority;

commit;

do $$
begin
  if to_regclass('ops.guidance_import_batch') is null
     or to_regclass('ops.guidance_import_entry') is null then
    raise exception '0170 FAILED: guidance import staging tables missing';
  end if;
  if to_regprocedure('ops.stage_guidance_import_batch(text,text,uuid,text,text)') is null
     or to_regprocedure('ops.decide_guidance_import_batch(uuid,text,text,text,text)') is null
     or to_regprocedure('ops.deactivate_guidance_registry(uuid,text,text,text)') is null then
    raise exception '0170 FAILED: guidance import authority functions missing';
  end if;
end $$;
