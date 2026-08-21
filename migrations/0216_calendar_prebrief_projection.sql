-- 0216: bounded, redacted, sponsor-fenced Calendar prebrief projection.
--
-- The EventKit collector supplies only opaque keys and already-resolved CARR
-- refs.  The database owns the calendar allowlist, derives both the sponsor and
-- snapshot time from a leased static job, and records no source identifier,
-- attendee address, description, join locator, recurrence blob, or credential.

begin;

insert into ops.job_definition
  (key,version,enabled,risk,owner_actor,execution_kind,execution_contract,
   inventory_contract,recurrence,state_contract,routing_contract,
   filtering_contract,validation_contract,retry_policy,deduplication,
   completion_contract,legacy_schedule)
values
  ('calendar-prebrief-projection-joe-daily',1,false,'green','joe','deterministic',
   '{"entrypoint":"ops.ingest_calendar_prebrief_projection","activation":"pending allowlist and device acceptance"}'::jsonb,
   '{"owner":"joe","inputs":["bounded calendar projection snapshot"],"canonical_writes":["ops.calendar_prebrief_projection_event","ops.calendar_prebrief_projection_receipt"]}'::jsonb,
   '{"cron":"30 6 * * 1-5","timezone":"America/Chicago","not_before":"06:30","brief_deadline":"06:45"}'::jsonb,
   '{"owner":"ops.job","initial":"queued"}'::jsonb,
   '{"key":"facts.all_true","destination":"live","spec":{"all_of":["calendar.snapshot_bounded"]}}'::jsonb,
   '{"key":"facts.all_true","spec":{"all_of":["calendar.keys_opaque","calendar.parties_resolved"]}}'::jsonb,
   '{"key":"facts.all_true","spec":{"all_of":["calendar.projection_receipt_persisted"]}}'::jsonb,
   '{"max_attempts":2,"backoff":"exponential","base_seconds":60,"cap_seconds":600,"timeout_seconds":300}'::jsonb,
   '{"key_template":"calendar-prebrief-projection-joe-daily:{scheduled_for}"}'::jsonb,
   '{"key":"facts.all_true","spec":{"all_of":["calendar.projection_receipt_persisted"]},"receipt_kind":"calendar_prebrief_projection"}'::jsonb,
   '{"provider":"ops.job dispatcher","status":"disabled","activation":"pending allowlist and device acceptance"}'::jsonb),
  ('calendar-prebrief-projection-dell-daily',1,false,'green','dell','deterministic',
   '{"entrypoint":"ops.ingest_calendar_prebrief_projection","activation":"pending allowlist and device acceptance"}'::jsonb,
   '{"owner":"dell","inputs":["bounded calendar projection snapshot"],"canonical_writes":["ops.calendar_prebrief_projection_event","ops.calendar_prebrief_projection_receipt"]}'::jsonb,
   '{"cron":"30 6 * * 1-5","timezone":"America/Chicago","not_before":"06:30","brief_deadline":"06:45"}'::jsonb,
   '{"owner":"ops.job","initial":"queued"}'::jsonb,
   '{"key":"facts.all_true","destination":"live","spec":{"all_of":["calendar.snapshot_bounded"]}}'::jsonb,
   '{"key":"facts.all_true","spec":{"all_of":["calendar.keys_opaque","calendar.parties_resolved"]}}'::jsonb,
   '{"key":"facts.all_true","spec":{"all_of":["calendar.projection_receipt_persisted"]}}'::jsonb,
   '{"max_attempts":2,"backoff":"exponential","base_seconds":60,"cap_seconds":600,"timeout_seconds":300}'::jsonb,
   '{"key_template":"calendar-prebrief-projection-dell-daily:{scheduled_for}"}'::jsonb,
   '{"key":"facts.all_true","spec":{"all_of":["calendar.projection_receipt_persisted"]},"receipt_kind":"calendar_prebrief_projection"}'::jsonb,
   '{"provider":"ops.job dispatcher","status":"disabled","activation":"pending allowlist and device acceptance"}'::jsonb),
  ('calendar-prebrief-canary-joe-daily',1,false,'green','joe','deterministic',
   '{"entrypoint":"ops.ingest_calendar_prebrief_canary_projection","activation":"disabled isolated canary"}'::jsonb,
   '{"owner":"joe","inputs":["bounded calendar prebrief canary snapshot"],"canonical_writes":["ops.calendar_prebrief_canary_event","ops.calendar_prebrief_canary_receipt"]}'::jsonb,
   '{"cron":"30 6 * * 1-5","timezone":"America/Chicago","not_before":"06:30","brief_deadline":"06:45"}'::jsonb,
   '{"owner":"ops.job","initial":"queued"}'::jsonb,
   '{"key":"facts.all_true","destination":"calendar-prebrief-canary-joe","spec":{"all_of":["calendar.canary_bounded"]}}'::jsonb,
   '{"key":"facts.all_true","spec":{"all_of":["calendar.keys_opaque"]}}'::jsonb,
   '{"key":"facts.all_true","spec":{"all_of":["calendar.canary_receipt_persisted"]}}'::jsonb,
   '{"max_attempts":2,"backoff":"exponential","base_seconds":60,"cap_seconds":600,"timeout_seconds":300}'::jsonb,
   '{"key_template":"calendar-prebrief-canary-joe-daily:{scheduled_for}"}'::jsonb,
   '{"key":"facts.all_true","spec":{"all_of":["calendar.canary_receipt_persisted"]},"receipt_kind":"calendar_prebrief_canary"}'::jsonb,
   '{"provider":"ops.job dispatcher","status":"disabled","activation":"isolated canary only"}'::jsonb),
  ('calendar-prebrief-canary-dell-daily',1,false,'green','dell','deterministic',
   '{"entrypoint":"ops.ingest_calendar_prebrief_canary_projection","activation":"disabled isolated canary"}'::jsonb,
   '{"owner":"dell","inputs":["bounded calendar prebrief canary snapshot"],"canonical_writes":["ops.calendar_prebrief_canary_event","ops.calendar_prebrief_canary_receipt"]}'::jsonb,
   '{"cron":"30 6 * * 1-5","timezone":"America/Chicago","not_before":"06:30","brief_deadline":"06:45"}'::jsonb,
   '{"owner":"ops.job","initial":"queued"}'::jsonb,
   '{"key":"facts.all_true","destination":"calendar-prebrief-canary-dell","spec":{"all_of":["calendar.canary_bounded"]}}'::jsonb,
   '{"key":"facts.all_true","spec":{"all_of":["calendar.keys_opaque"]}}'::jsonb,
   '{"key":"facts.all_true","spec":{"all_of":["calendar.canary_receipt_persisted"]}}'::jsonb,
   '{"max_attempts":2,"backoff":"exponential","base_seconds":60,"cap_seconds":600,"timeout_seconds":300}'::jsonb,
   '{"key_template":"calendar-prebrief-canary-dell-daily:{scheduled_for}"}'::jsonb,
   '{"key":"facts.all_true","spec":{"all_of":["calendar.canary_receipt_persisted"]},"receipt_kind":"calendar_prebrief_canary"}'::jsonb,
   '{"provider":"ops.job dispatcher","status":"disabled","activation":"isolated canary only"}'::jsonb);

-- This is a privilege bundle only.  The two LOGIN identities are provisioned
-- outside migrations; hard-coding their exact names below makes a substituted
-- general jobs credential fail closed.
do $$ begin
  if not exists (select 1 from pg_roles where rolname='carr_calendar_prebrief_jobs') then
    create role carr_calendar_prebrief_jobs nologin;
  end if;
  if not exists (select 1 from pg_roles where rolname='carr_calendar_prebrief_canary_jobs') then
    create role carr_calendar_prebrief_canary_jobs nologin;
  end if;
end $$;
grant usage on schema ops,public to carr_calendar_prebrief_jobs,carr_calendar_prebrief_canary_jobs;

-- Provisioned attestor and resolver identities receive these bundles outside
-- migrations.  A collector may present an envelope but may never attest it;
-- resolving one live email is narrower still, and neither bundle can write a
-- projection.
do $$ begin
  if not exists (select 1 from pg_roles where rolname='carr_calendar_prebrief_attestors') then
    create role carr_calendar_prebrief_attestors nologin;
  end if;
  if not exists (select 1 from pg_roles where rolname='carr_calendar_prebrief_email_resolver') then
    create role carr_calendar_prebrief_email_resolver nologin;
  end if;
end $$;
grant usage on schema ops,public to carr_calendar_prebrief_attestors,carr_calendar_prebrief_email_resolver;

create table ops.calendar_prebrief_allowlist_receipt(
  id uuid primary key default gen_random_uuid(),
  sponsor text not null check (sponsor in ('joe','dell')),
  calendar_keys text[] not null,
  configuration_digest text not null check (configuration_digest ~ '^[0-9a-f]{64}$'),
  configured_at timestamptz not null default now(),
  configured_by text not null check (configured_by in ('joe','dell')),
  check (cardinality(calendar_keys)>0)
);
create trigger calendar_prebrief_allowlist_receipt_append_only
  before update or delete on ops.calendar_prebrief_allowlist_receipt
  for each row execute function ops.refuse_job_evidence_rewrite();

create table ops.calendar_prebrief_allowed_calendar(
  sponsor text primary key check (sponsor in ('joe','dell')),
  calendar_keys text[] not null,
  configuration_digest text not null check (configuration_digest ~ '^[0-9a-f]{64}$'),
  active_revision_id uuid not null references ops.calendar_prebrief_allowlist_receipt(id),
  configured_at timestamptz not null default now(),
  configured_by text not null check (configured_by in ('joe','dell')),
  check (cardinality(calendar_keys)>0)
);

create or replace function ops.replace_calendar_prebrief_allowlist(p_calendar_keys text[])
returns ops.calendar_prebrief_allowlist_receipt
language plpgsql security definer set search_path=ops,public,pg_temp
as $$
declare
  v_sponsor text;
  v_keys text[];
  v_configuration_digest text;
  v_receipt ops.calendar_prebrief_allowlist_receipt%rowtype;
begin
  v_sponsor := ops.authority_actor_slug();
  select array_agg(k order by k) into v_keys from unnest(coalesce(p_calendar_keys,'{}'::text[])) keys(k);
  if coalesce(cardinality(v_keys),0)=0
     or array_position(v_keys,null) is not null
     or exists (select 1 from unnest(v_keys) keys(k) where k !~ '^[0-9a-f]{64}$')
     or cardinality(v_keys) <> (select count(distinct k) from unnest(v_keys) keys(k)) then
    raise exception using errcode='22023',message='calendar prebrief allowlist requires nonempty distinct opaque 64-hex calendar keys';
  end if;
  perform pg_advisory_xact_lock(hashtextextended('calendar-prebrief-allowlist:' || v_sponsor,0));
  v_configuration_digest:=encode(digest(convert_to(jsonb_build_object(
      'sponsor',v_sponsor,'calendar_keys',to_jsonb(v_keys))::text,'UTF8'),'sha256'),'hex');
  insert into ops.calendar_prebrief_allowlist_receipt
    (sponsor,calendar_keys,configuration_digest,configured_by)
  values(v_sponsor,v_keys,v_configuration_digest,v_sponsor)
  returning * into v_receipt;
  insert into ops.calendar_prebrief_allowed_calendar(sponsor,calendar_keys,configuration_digest,active_revision_id,configured_at,configured_by)
  values(v_sponsor,v_keys,v_configuration_digest,v_receipt.id,now(),v_sponsor)
  on conflict(sponsor) do update set calendar_keys=excluded.calendar_keys,
    configuration_digest=excluded.configuration_digest,active_revision_id=excluded.active_revision_id,
    configured_at=excluded.configured_at,configured_by=excluded.configured_by;
  return v_receipt;
end $$;

create table ops.calendar_prebrief_projection_event(
  id uuid primary key default gen_random_uuid(),
  sponsor text not null check (sponsor in ('joe','dell')),
  calendar_key text not null check (calendar_key ~ '^[0-9a-f]{64}$'),
  event_key text not null check (event_key ~ '^[0-9a-f]{64}$'),
  occurrence_key text not null check (occurrence_key ~ '^[0-9a-f]{64}$'),
  starts_at timestamptz not null,
  ends_at timestamptz not null check (ends_at > starts_at),
  title varchar(240) not null check (btrim(title) <> ''),
  location varchar(240),
  snapshot_at timestamptz not null,
  allowlist_revision_id uuid not null references ops.calendar_prebrief_allowlist_receipt(id),
  captured_at timestamptz not null default now(),
  unique(sponsor,occurrence_key)
);

create table ops.calendar_prebrief_projection_participant(
  event_id uuid not null references ops.calendar_prebrief_projection_event(id) on delete cascade,
  party_id uuid not null references party(id),
  subject_type text not null check (subject_type in ('lead','client','vendor','party')),
  subject_id uuid not null,
  participant_ref text not null check (participant_ref ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'),
  captured_at timestamptz not null default now(),
  primary key(event_id,participant_ref)
);

create table ops.calendar_prebrief_projection_receipt(
  id uuid primary key default gen_random_uuid(),
  job_id uuid not null references ops.job(id),
  attempt integer not null check (attempt > 0),
  sponsor text not null check (sponsor in ('joe','dell')),
  snapshot_at timestamptz not null,
  allowlist_revision_id uuid not null references ops.calendar_prebrief_allowlist_receipt(id),
  allowlist_digest text not null check (allowlist_digest ~ '^[0-9a-f]{64}$'),
  snapshot_digest text not null check (snapshot_digest ~ '^[0-9a-f]{64}$'),
  event_count integer not null check (event_count >= 0),
  participant_count integer not null check (participant_count >= 0),
  captured_at timestamptz not null default now(),
  unique(sponsor,snapshot_at),
  unique(job_id,attempt)
);
create trigger calendar_prebrief_projection_receipt_append_only
  before update or delete on ops.calendar_prebrief_projection_receipt
  for each row execute function ops.refuse_job_evidence_rewrite();

-- A device attests exactly the bounded source it observed.  This receipt is
-- append-only and carries only opaque keys, counts, and digests; notably it
-- cannot carry an EventKit identifier or an attendee address.
create table ops.calendar_prebrief_source_attestation_receipt(
  id uuid primary key default gen_random_uuid(),
  job_id uuid not null references ops.job(id),
  attempt integer not null check (attempt > 0),
  lease_token uuid not null,
  sponsor text not null check (sponsor in ('joe','dell')),
  attestor_identity text not null check (attestor_identity in ('carr_calendar_prebrief_attestor_joe','carr_calendar_prebrief_attestor_dell')),
  mode text not null check (mode in ('live','canary')),
  destination text not null check (destination in ('live','calendar-prebrief-canary-joe','calendar-prebrief-canary-dell')),
  snapshot_at timestamptz not null,
  allowlist_revision_id uuid not null references ops.calendar_prebrief_allowlist_receipt(id),
  capture_challenge_id uuid not null unique,
  allowlist_digest text not null check (allowlist_digest ~ '^[0-9a-f]{64}$'),
  observed_calendar_keys text[] not null,
  event_count integer not null check (event_count >= 0 and event_count <= 128),
  canonical_event_digest text not null check (canonical_event_digest ~ '^[0-9a-f]{64}$'),
  collector_key_fingerprint text not null check (collector_key_fingerprint ~ '^[0-9a-f]{64}$'),
  signature_sha256 text not null check (signature_sha256 ~ '^[0-9a-f]{64}$'),
  collector_version text not null check (collector_version ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$'),
  attested_at timestamptz not null default now(),
  unique(job_id,attempt),
  unique(signature_sha256)
);
create trigger calendar_prebrief_source_attestation_receipt_append_only
  before update or delete on ops.calendar_prebrief_source_attestation_receipt
  for each row execute function ops.refuse_job_evidence_rewrite();

-- The resolver-facing capture contract is DB-issued, job- and attempt-bound,
-- and contains only opaque calendar material.  The collector signs these
-- values verbatim; a challenge is consumed only by that exact attestation.
create table ops.calendar_prebrief_capture_challenge(
  id uuid primary key default gen_random_uuid(),
  job_id uuid not null references ops.job(id),
  attempt integer not null check (attempt > 0),
  lease_token uuid not null,
  sponsor text not null check (sponsor in ('joe','dell')),
  resolver_identity text not null check (resolver_identity in ('carr_calendar_prebrief_resolver_joe','carr_calendar_prebrief_resolver_dell')),
  mode text not null check (mode in ('live','canary')),
  destination text not null check (destination in ('live','calendar-prebrief-canary-joe','calendar-prebrief-canary-dell')),
  scheduled_for timestamptz not null,
  window_starts_at timestamptz not null,
  window_ends_at timestamptz not null,
  allowlist_revision_id uuid not null references ops.calendar_prebrief_allowlist_receipt(id),
  allowlist_digest text not null check (allowlist_digest ~ '^[0-9a-f]{64}$'),
  calendar_keys text[] not null,
  issued_at timestamptz not null default now(),
  check (window_starts_at=scheduled_for-interval '7 days' and window_ends_at=scheduled_for+interval '45 days'),
  check (cardinality(calendar_keys)>0)
);
create trigger calendar_prebrief_capture_challenge_append_only
  before update or delete on ops.calendar_prebrief_capture_challenge
  for each row execute function ops.refuse_job_evidence_rewrite();

alter table ops.calendar_prebrief_source_attestation_receipt
  add constraint calendar_prebrief_source_attestation_receipt_challenge_fkey
  foreign key (capture_challenge_id) references ops.calendar_prebrief_capture_challenge(id);

alter table ops.calendar_prebrief_projection_receipt
  add column source_attestation_id uuid not null references ops.calendar_prebrief_source_attestation_receipt(id);

create table ops.calendar_prebrief_canary_event(
  id uuid primary key default gen_random_uuid(),
  sponsor text not null check (sponsor in ('joe','dell')),
  destination text not null check (destination in ('calendar-prebrief-canary-joe','calendar-prebrief-canary-dell')),
  occurrence_key text not null check (occurrence_key ~ '^[0-9a-f]{64}$'),
  snapshot_at timestamptz not null,
  allowlist_revision_id uuid not null references ops.calendar_prebrief_allowlist_receipt(id),
  source_attestation_id uuid not null references ops.calendar_prebrief_source_attestation_receipt(id),
  captured_at timestamptz not null default now(),
  unique(sponsor,destination,occurrence_key)
);
create table ops.calendar_prebrief_canary_receipt(
  id uuid primary key default gen_random_uuid(),
  job_id uuid not null references ops.job(id),
  attempt integer not null check (attempt > 0),
  sponsor text not null check (sponsor in ('joe','dell')),
  destination text not null check (destination in ('calendar-prebrief-canary-joe','calendar-prebrief-canary-dell')),
  snapshot_at timestamptz not null,
  allowlist_revision_id uuid not null references ops.calendar_prebrief_allowlist_receipt(id),
  source_attestation_id uuid not null references ops.calendar_prebrief_source_attestation_receipt(id),
  canonical_event_digest text not null check (canonical_event_digest ~ '^[0-9a-f]{64}$'),
  event_count integer not null check (event_count >= 0),
  captured_at timestamptz not null default now(),
  unique(job_id,attempt)
);
create trigger calendar_prebrief_canary_receipt_append_only
  before update or delete on ops.calendar_prebrief_canary_receipt
  for each row execute function ops.refuse_job_evidence_rewrite();

create or replace function ops.calendar_prebrief_attestor_sponsor()
returns text language plpgsql security definer set search_path=ops,public,pg_temp as $$
begin
  if not pg_has_role(session_user,'carr_calendar_prebrief_attestors','member') then
    raise exception using errcode='42501',message='calendar prebrief attestor identity lacks its capability bundle';
  end if;
  case session_user
    when 'carr_calendar_prebrief_attestor_joe' then return 'joe';
    when 'carr_calendar_prebrief_attestor_dell' then return 'dell';
    else raise exception using errcode='42501',message='calendar prebrief requires its exact sponsor-bound verifier identity';
  end case;
end $$;

create or replace function ops.calendar_prebrief_canonical_event_digest(p_events jsonb)
returns table(event_count integer,canonical_event_digest text)
language plpgsql security definer set search_path=ops,public,pg_temp as $$
declare v_events jsonb;
begin
  if p_events is null or pg_column_size(p_events)>262144 or jsonb_typeof(p_events)<>'array' then
    raise exception using errcode='22023',message='calendar prebrief verified envelope requires a bounded event array';
  end if;
  if jsonb_array_length(p_events)>128 or exists(
      select 1 from jsonb_array_elements(p_events) e(value)
       where pg_column_size(e.value)>4096 or jsonb_typeof(e.value)<>'object'
          or jsonb_typeof(e.value->'participant_refs')<>'array') then
    raise exception using errcode='22023',message='calendar prebrief verified envelope has invalid bounded events';
  end if;
  select coalesce(jsonb_agg(event order by event->>'occurrence_key'),'[]'::jsonb) into v_events
    from (select jsonb_set(e.value,'{participant_refs}',coalesce((select jsonb_agg(ref order by ref)
             from jsonb_array_elements_text(e.value->'participant_refs') refs(ref)),'[]'::jsonb)) event
            from jsonb_array_elements(p_events) e(value)) normalized;
  event_count:=jsonb_array_length(p_events);
  canonical_event_digest:=encode(digest(convert_to(v_events::text,'UTF8'),'sha256'),'hex');
  return next;
end $$;

create or replace function ops.record_calendar_prebrief_verified_envelope(
  p_job_id uuid,p_lease uuid,p_observed_calendar_keys text[],p_events jsonb,p_destination text,
  p_collector_key_fingerprint text,p_signature_sha256 text,p_collector_version text
) returns ops.calendar_prebrief_source_attestation_receipt
language plpgsql security definer set search_path=ops,public,pg_temp
as $$
declare
  v_job ops.job%rowtype; v_sponsor text; v_attestor_sponsor text; v_owner text;
  v_allowed text[]; v_observed text[]; v_allowlist_digest text; v_allowlist_revision_id uuid;
  v_expected_destination text; v_event_count integer; v_event_digest text;
  v_existing ops.calendar_prebrief_source_attestation_receipt%rowtype;
begin
  v_attestor_sponsor:=ops.calendar_prebrief_attestor_sponsor();
  select * into v_job from ops.job where id=p_job_id for update;
  if not found or v_job.state<>'running' or v_job.lease_token is distinct from p_lease
     or v_job.leased_until is null or v_job.leased_until<now() then
    raise exception using errcode='55000',message='calendar prebrief verified envelope requires current live job lease';
  end if;
  if v_job.scheduled_for < now()-interval '30 minutes' or v_job.scheduled_for > now()+interval '5 minutes' then
    raise exception using errcode='22023',message='calendar prebrief verified envelope refuses job scheduled outside its DB-clock window';
  end if;
  select owner_actor into v_owner from ops.job_definition
    where key=v_job.definition_key and version=v_job.definition_version for update;
  v_expected_destination:=case v_job.definition_key
    when 'calendar-prebrief-projection-joe-daily' then 'live'
    when 'calendar-prebrief-projection-dell-daily' then 'live'
    when 'calendar-prebrief-canary-joe-daily' then 'calendar-prebrief-canary-joe'
    when 'calendar-prebrief-canary-dell-daily' then 'calendar-prebrief-canary-dell'
  end;
  if not found or v_owner not in ('joe','dell') or v_owner<>v_attestor_sponsor
     or p_destination is distinct from v_expected_destination
     or ((v_job.definition_key in ('calendar-prebrief-projection-joe-daily','calendar-prebrief-projection-dell-daily')) <> (v_job.mode='live'))
     or ((v_job.definition_key in ('calendar-prebrief-canary-joe-daily','calendar-prebrief-canary-dell-daily')) <> (v_job.mode='canary'))
     or v_job.definition_key not in ('calendar-prebrief-projection-joe-daily','calendar-prebrief-projection-dell-daily','calendar-prebrief-canary-joe-daily','calendar-prebrief-canary-dell-daily') then
    raise exception using errcode='42501',message='calendar prebrief verified envelope identity, mode, or destination does not match the static job owner',
      detail=format('owner=%s attestor=%s key=%s mode=%s expected_destination=%s destination=%s',v_owner,v_attestor_sponsor,v_job.definition_key,v_job.mode,v_expected_destination,p_destination);
  end if;
  v_sponsor:=v_owner;
  if p_collector_key_fingerprint is null or p_collector_key_fingerprint !~ '^[0-9a-f]{64}$'
     or p_signature_sha256 is null or p_signature_sha256 !~ '^[0-9a-f]{64}$'
     or p_collector_version is null or p_collector_version !~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$' then
    raise exception using errcode='22023',message='calendar prebrief verified envelope requires bounded collector fingerprint, signature digest, and version';
  end if;
  select calendar_keys,configuration_digest,active_revision_id into v_allowed,v_allowlist_digest,v_allowlist_revision_id
    from ops.calendar_prebrief_allowed_calendar where sponsor=v_sponsor for update;
  select array_agg(k order by k) into v_observed from unnest(coalesce(p_observed_calendar_keys,'{}'::text[])) keys(k);
  if not found or coalesce(cardinality(v_allowed),0)=0 or coalesce(cardinality(v_observed),0)=0
     or array_position(v_observed,null) is not null
     or exists(select 1 from unnest(v_observed) keys(k) where k !~ '^[0-9a-f]{64}$')
     or cardinality(v_observed)<>(select count(distinct k) from unnest(v_observed) keys(k))
     or v_observed is distinct from v_allowed then
    raise exception using errcode='22023',message='calendar prebrief verified envelope requires exact DB allowlist coverage';
  end if;
  select event_count,canonical_event_digest into v_event_count,v_event_digest
    from ops.calendar_prebrief_canonical_event_digest(p_events);
  perform pg_advisory_xact_lock(hashtextextended('calendar-prebrief-source:' || v_job.id::text,0));
  select * into v_existing from ops.calendar_prebrief_source_attestation_receipt where job_id=v_job.id and attempt=v_job.attempt;
  if found then
    if v_existing.lease_token<>p_lease or v_existing.sponsor<>v_sponsor or v_existing.attestor_identity<>session_user
       or v_existing.destination<>p_destination or v_existing.snapshot_at<>v_job.scheduled_for
       or v_existing.allowlist_revision_id<>v_allowlist_revision_id or v_existing.allowlist_digest<>v_allowlist_digest
       or v_existing.observed_calendar_keys is distinct from v_observed or v_existing.event_count<>v_event_count
       or v_existing.canonical_event_digest<>v_event_digest or v_existing.collector_key_fingerprint<>p_collector_key_fingerprint
       or v_existing.signature_sha256<>p_signature_sha256 or v_existing.collector_version<>p_collector_version then
      raise exception using errcode='23505',message='calendar prebrief verified envelope replay conflicts with immutable attempt';
    end if;
    return v_existing;
  end if;
  insert into ops.calendar_prebrief_source_attestation_receipt
    (job_id,attempt,lease_token,sponsor,attestor_identity,mode,destination,snapshot_at,allowlist_revision_id,allowlist_digest,observed_calendar_keys,event_count,canonical_event_digest,collector_key_fingerprint,signature_sha256,collector_version)
  values(v_job.id,v_job.attempt,p_lease,v_sponsor,session_user,v_job.mode,p_destination,v_job.scheduled_for,v_allowlist_revision_id,v_allowlist_digest,v_observed,v_event_count,v_event_digest,p_collector_key_fingerprint,p_signature_sha256,p_collector_version)
  returning * into v_existing;
  return v_existing;
end $$;

create or replace function ops.calendar_prebrief_resolver_sponsor()
returns text language plpgsql security definer set search_path=ops,public,pg_temp as $$
begin
  if not pg_has_role(session_user,'carr_calendar_prebrief_email_resolver','member') then
    raise exception using errcode='42501',message='calendar prebrief resolver identity lacks its capability bundle';
  end if;
  case session_user
    when 'carr_calendar_prebrief_resolver_joe' then return 'joe';
    when 'carr_calendar_prebrief_resolver_dell' then return 'dell';
    else raise exception using errcode='42501',message='calendar prebrief requires its exact sponsor-bound resolver identity';
  end case;
end $$;

create or replace function ops.resolve_calendar_prebrief_email_ref(p_email text)
returns text language plpgsql security definer set search_path=ops,public,pg_temp as $$
declare v_count integer; v_ref text;
begin
  perform ops.calendar_prebrief_resolver_sponsor();
  if p_email is null or length(p_email)>320
     or lower(btrim(p_email)) !~ '^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]@]+$' then
    raise exception using errcode='42501',message='calendar prebrief email resolver requires one bounded exact email';
  end if;
  select count(distinct r.ref),min(r.ref) into v_count,v_ref
    from party p join v_ref_index r on r.party_id=p.id and not r.merged
   where lower(btrim(p.email))=lower(btrim(p_email));
  if v_count<>1 then
    raise exception using errcode='22023',message='calendar prebrief email resolver requires exactly one live unmerged canonical ref';
  end if;
  return v_ref;
end $$;

create or replace function ops.issue_calendar_prebrief_capture_contract(p_job_id uuid,p_lease uuid)
returns table(challenge_id uuid,sponsor text,job_id uuid,attempt integer,lease_token uuid,
              scheduled_for timestamptz,window_starts_at timestamptz,window_ends_at timestamptz,
              mode text,destination text,allowlist_revision_id uuid,allowlist_digest text,calendar_keys text[])
language plpgsql security definer set search_path=ops,public,pg_temp as $$
declare v_job ops.job%rowtype; v_sponsor text; v_resolver_sponsor text; v_destination text;
  v_keys text[]; v_digest text; v_revision uuid; v_challenge ops.calendar_prebrief_capture_challenge%rowtype;
begin
  v_resolver_sponsor:=ops.calendar_prebrief_resolver_sponsor();
  select * into v_job from ops.job where id=p_job_id for update;
  if not found or v_job.state<>'running' or v_job.lease_token is distinct from p_lease
     or v_job.leased_until is null or v_job.leased_until<now() then
    raise exception using errcode='55000',message='calendar prebrief capture contract requires current live job lease';
  end if;
  if v_job.scheduled_for < now()-interval '30 minutes' or v_job.scheduled_for > now()+interval '5 minutes' then
    raise exception using errcode='22023',message='calendar prebrief capture contract refuses job scheduled outside its DB-clock window';
  end if;
  select owner_actor into v_sponsor from ops.job_definition where key=v_job.definition_key and version=v_job.definition_version for update;
  v_destination:=case v_job.definition_key
    when 'calendar-prebrief-projection-joe-daily' then 'live'
    when 'calendar-prebrief-projection-dell-daily' then 'live'
    when 'calendar-prebrief-canary-joe-daily' then 'calendar-prebrief-canary-joe'
    when 'calendar-prebrief-canary-dell-daily' then 'calendar-prebrief-canary-dell'
  end;
  if not found or v_sponsor<>v_resolver_sponsor or v_destination is null
     or (v_destination='live' and v_job.mode<>'live') or (v_destination<>'live' and v_job.mode<>'canary') then
    raise exception using errcode='42501',message='calendar prebrief capture contract identity, mode, or static job owner mismatch';
  end if;
  select a.calendar_keys,a.configuration_digest,a.active_revision_id into v_keys,v_digest,v_revision
    from ops.calendar_prebrief_allowed_calendar a where a.sponsor=v_sponsor for update;
  if not found or coalesce(cardinality(v_keys),0)=0 or v_revision is null then
    raise exception using errcode='22023',message='calendar prebrief capture contract requires current DB allowlist';
  end if;
  insert into ops.calendar_prebrief_capture_challenge
    (job_id,attempt,lease_token,sponsor,resolver_identity,mode,destination,scheduled_for,window_starts_at,window_ends_at,allowlist_revision_id,allowlist_digest,calendar_keys)
  values(v_job.id,v_job.attempt,p_lease,v_sponsor,session_user,v_job.mode,v_destination,v_job.scheduled_for,
         v_job.scheduled_for-interval '7 days',v_job.scheduled_for+interval '45 days',v_revision,v_digest,v_keys)
  returning * into v_challenge;
  return query select v_challenge.id,v_challenge.sponsor,v_challenge.job_id,v_challenge.attempt,v_challenge.lease_token,
    v_challenge.scheduled_for,v_challenge.window_starts_at,v_challenge.window_ends_at,v_challenge.mode,v_challenge.destination,
    v_challenge.allowlist_revision_id,v_challenge.allowlist_digest,v_challenge.calendar_keys;
end $$;

drop function ops.record_calendar_prebrief_verified_envelope(uuid,uuid,text[],jsonb,text,text,text,text);
create function ops.record_calendar_prebrief_verified_envelope(
  p_job_id uuid,p_lease uuid,p_challenge_id uuid,p_scheduled_for timestamptz,p_window_starts_at timestamptz,p_window_ends_at timestamptz,
  p_allowlist_revision_id uuid,p_allowlist_digest text,p_calendar_keys text[],p_observed_calendar_keys text[],p_events jsonb,p_destination text,
  p_collector_key_fingerprint text,p_signature_sha256 text,p_collector_version text
) returns ops.calendar_prebrief_source_attestation_receipt
language plpgsql security definer set search_path=ops,public,pg_temp as $$
declare v_job ops.job%rowtype; v_challenge ops.calendar_prebrief_capture_challenge%rowtype;
  v_existing ops.calendar_prebrief_source_attestation_receipt%rowtype; v_attestor_sponsor text;
  v_current_keys text[]; v_current_digest text; v_current_revision uuid; v_observed text[]; v_event_count integer; v_event_digest text;
begin
  v_attestor_sponsor:=ops.calendar_prebrief_attestor_sponsor();
  if p_challenge_id is null or p_signature_sha256 is null or p_signature_sha256 !~ '^[0-9a-f]{64}$'
     or p_collector_key_fingerprint is null or p_collector_key_fingerprint !~ '^[0-9a-f]{64}$'
     or p_collector_version is null or p_collector_version !~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$' then
    raise exception using errcode='22023',message='calendar prebrief verified envelope requires bounded signed claim and collector evidence';
  end if;
  select * into v_job from ops.job where id=p_job_id for update;
  if not found or v_job.state<>'running' or v_job.lease_token is distinct from p_lease or v_job.leased_until is null or v_job.leased_until<now() then
    raise exception using errcode='55000',message='calendar prebrief verified envelope requires current live job lease';
  end if;
  select * into v_challenge from ops.calendar_prebrief_capture_challenge where id=p_challenge_id for update;
  if not found or v_challenge.job_id<>v_job.id or v_challenge.attempt<>v_job.attempt or v_challenge.lease_token<>p_lease
     or v_challenge.sponsor<>v_attestor_sponsor or v_challenge.scheduled_for<>p_scheduled_for
     or v_challenge.window_starts_at<>p_window_starts_at or v_challenge.window_ends_at<>p_window_ends_at
     or v_challenge.allowlist_revision_id<>p_allowlist_revision_id or v_challenge.allowlist_digest<>p_allowlist_digest
     or v_challenge.calendar_keys is distinct from p_calendar_keys or v_challenge.destination<>p_destination or v_challenge.mode<>v_job.mode then
    raise exception using errcode='42501',message='calendar prebrief verified envelope signed claim does not match its DB-issued capture contract';
  end if;
  select calendar_keys,configuration_digest,active_revision_id into v_current_keys,v_current_digest,v_current_revision
    from ops.calendar_prebrief_allowed_calendar where sponsor=v_challenge.sponsor for update;
  if not found or v_current_revision<>v_challenge.allowlist_revision_id or v_current_digest<>v_challenge.allowlist_digest
     or v_current_keys is distinct from v_challenge.calendar_keys then
    raise exception using errcode='22023',message='calendar prebrief verified envelope capture contract is stale against DB allowlist';
  end if;
  select array_agg(k order by k) into v_observed from unnest(coalesce(p_observed_calendar_keys,'{}'::text[])) keys(k);
  if v_observed is distinct from v_challenge.calendar_keys then
    raise exception using errcode='22023',message='calendar prebrief verified envelope requires exact signed allowlist coverage';
  end if;
  select event_count,canonical_event_digest into v_event_count,v_event_digest from ops.calendar_prebrief_canonical_event_digest(p_events);
  perform pg_advisory_xact_lock(hashtextextended('calendar-prebrief-source:' || v_job.id::text,0));
  select * into v_existing from ops.calendar_prebrief_source_attestation_receipt where job_id=v_job.id and attempt=v_job.attempt;
  if found then
    if v_existing.capture_challenge_id<>p_challenge_id or v_existing.lease_token<>p_lease or v_existing.sponsor<>v_challenge.sponsor
       or v_existing.attestor_identity<>session_user or v_existing.mode<>v_challenge.mode or v_existing.destination<>p_destination
       or v_existing.snapshot_at<>p_scheduled_for or v_existing.allowlist_revision_id<>p_allowlist_revision_id
       or v_existing.allowlist_digest<>p_allowlist_digest or v_existing.observed_calendar_keys is distinct from v_observed
       or v_existing.event_count<>v_event_count or v_existing.canonical_event_digest<>v_event_digest
       or v_existing.collector_key_fingerprint<>p_collector_key_fingerprint or v_existing.signature_sha256<>p_signature_sha256
       or v_existing.collector_version<>p_collector_version then
      raise exception using errcode='23505',message='calendar prebrief verified envelope replay conflicts with immutable attempt';
    end if;
    return v_existing;
  end if;
  if exists(select 1 from ops.calendar_prebrief_source_attestation_receipt where capture_challenge_id=p_challenge_id or signature_sha256=p_signature_sha256) then
    raise exception using errcode='23505',message='calendar prebrief verified envelope challenge or signature was already consumed';
  end if;
  insert into ops.calendar_prebrief_source_attestation_receipt
    (job_id,attempt,lease_token,sponsor,attestor_identity,mode,destination,snapshot_at,allowlist_revision_id,capture_challenge_id,allowlist_digest,observed_calendar_keys,event_count,canonical_event_digest,collector_key_fingerprint,signature_sha256,collector_version)
  values(v_job.id,v_job.attempt,p_lease,v_challenge.sponsor,session_user,v_challenge.mode,p_destination,p_scheduled_for,p_allowlist_revision_id,p_challenge_id,p_allowlist_digest,v_observed,v_event_count,v_event_digest,p_collector_key_fingerprint,p_signature_sha256,p_collector_version)
  returning * into v_existing;
  return v_existing;
end $$;

create or replace function ops.ingest_calendar_prebrief_projection(
  p_job_id uuid,
  p_lease uuid,
  p_observed_calendar_keys text[],
  p_events jsonb
) returns ops.calendar_prebrief_projection_receipt
language plpgsql security definer set search_path=ops,public,pg_temp
as $$
declare
  v_job ops.job%rowtype;
  v_sponsor text;
  v_session_sponsor text;
  v_execution_kind text;
  v_allowed text[];
  v_allowlist_digest text;
  v_allowlist_revision_id uuid;
  v_observed text[];
  v_event jsonb;
  v_event_id uuid;
  v_calendar_key text;
  v_event_key text;
  v_occurrence_key text;
  v_starts_at timestamptz;
  v_ends_at timestamptz;
  v_title text;
  v_location text;
  v_participant_ref text;
  v_party_id uuid;
  v_subject_type text;
  v_subject_id uuid;
  v_match_count integer;
  v_event_participant_count integer;
  v_event_count integer := 0;
  v_participant_count integer := 0;
  v_canonical_events jsonb;
  v_event_digest text;
  v_snapshot_digest text;
  v_receipt ops.calendar_prebrief_projection_receipt%rowtype;
  v_source ops.calendar_prebrief_source_attestation_receipt%rowtype;
begin
  case session_user
    when 'carr_calendar_prebrief_joe' then v_session_sponsor := 'joe';
    when 'carr_calendar_prebrief_dell' then v_session_sponsor := 'dell';
    else raise exception using errcode='42501',message='calendar prebrief projection requires its named externally provisioned execution identity';
  end case;
  if not pg_has_role(session_user,'carr_calendar_prebrief_jobs','member') then
    raise exception using errcode='42501',message='calendar prebrief projection execution identity lacks its capability bundle';
  end if;
  if p_events is null or pg_column_size(p_events)>262144 or jsonb_typeof(p_events)<>'array' then
    raise exception using errcode='22023',message='calendar prebrief projection requires a bounded event array';
  end if;
  if jsonb_array_length(p_events)>128 then
    raise exception using errcode='22023',message='calendar prebrief projection event count exceeds its bound';
  end if;

  select * into v_job from ops.job where id=p_job_id for update;
  if not found or v_job.state<>'running' or v_job.lease_token is distinct from p_lease
     or v_job.leased_until is null or v_job.leased_until<now() then
    raise exception using errcode='55000',message='calendar prebrief projection requires current live job lease';
  end if;
  if v_job.scheduled_for < now()-interval '30 minutes' or v_job.scheduled_for > now()+interval '5 minutes' then
    raise exception using errcode='22023',message='calendar prebrief projection refuses job scheduled outside its DB-clock window';
  end if;
  select owner_actor,execution_kind into v_sponsor,v_execution_kind
    from ops.job_definition where key=v_job.definition_key and version=v_job.definition_version for update;
  if not found or v_job.definition_key not in ('calendar-prebrief-projection-joe-daily','calendar-prebrief-projection-dell-daily')
     or v_job.definition_version<>1 or v_job.mode<>'live' or v_execution_kind<>'deterministic'
     or (v_job.definition_key='calendar-prebrief-projection-joe-daily' and v_sponsor<>'joe')
     or (v_job.definition_key='calendar-prebrief-projection-dell-daily' and v_sponsor<>'dell')
     or v_sponsor<>v_session_sponsor then
    raise exception using errcode='42501',message='calendar prebrief projection execution identity does not match the static job owner';
  end if;
  perform pg_advisory_xact_lock(hashtextextended('calendar-prebrief-projection:' || v_sponsor,0));
  select calendar_keys,configuration_digest,active_revision_id into v_allowed,v_allowlist_digest,v_allowlist_revision_id from ops.calendar_prebrief_allowed_calendar
   where sponsor=v_sponsor for update;
  if not found or coalesce(cardinality(v_allowed),0)=0
     or array_position(v_allowed,null) is not null
     or v_allowlist_digest !~ '^[0-9a-f]{64}$' or v_allowlist_revision_id is null
     or exists(select 1 from unnest(v_allowed) keys(k) where k !~ '^[0-9a-f]{64}$')
     or cardinality(v_allowed)<>(select count(distinct k) from unnest(v_allowed) keys(k))
     or not exists(select 1 from ops.calendar_prebrief_allowlist_receipt ar
                   where ar.id=v_allowlist_revision_id and ar.sponsor=v_sponsor
                     and ar.configuration_digest=v_allowlist_digest and ar.calendar_keys=v_allowed) then
    raise exception using errcode='22023',message='calendar prebrief projection requires a valid DB-owned sponsor allowlist';
  end if;
  select array_agg(k order by k) into v_observed from unnest(coalesce(p_observed_calendar_keys,'{}'::text[])) keys(k);
  if coalesce(cardinality(v_observed),0)=0 or array_position(v_observed,null) is not null
     or exists(select 1 from unnest(v_observed) keys(k) where k !~ '^[0-9a-f]{64}$')
     or cardinality(v_observed)<>(select count(distinct k) from unnest(v_observed) keys(k))
     or v_observed is distinct from v_allowed then
    raise exception using errcode='22023',message='calendar prebrief observed calendars must have exact DB allowlist coverage';
  end if;

  -- Prevalidate every event and every participant before deleting a current
  -- projection.  A bad event cannot partially replace a good one.
  for v_event in select value from jsonb_array_elements(p_events) loop
    if pg_column_size(v_event)>4096 or jsonb_typeof(v_event)<>'object'
       or exists(select 1 from jsonb_object_keys(v_event) keys(key)
                 where key not in ('calendar_key','event_key','occurrence_key','starts_at','ends_at','title','location','participant_refs'))
       or exists(select key from unnest(array['calendar_key','event_key','occurrence_key','starts_at','ends_at','title','location','participant_refs']) required(key)
                 except select key from jsonb_object_keys(v_event) actual(key)) then
      raise exception using errcode='22023',message='calendar prebrief event has fields outside its bounded contract';
    end if;
    if jsonb_typeof(v_event->'calendar_key')<>'string' or jsonb_typeof(v_event->'event_key')<>'string'
       or jsonb_typeof(v_event->'occurrence_key')<>'string' or jsonb_typeof(v_event->'starts_at')<>'string'
       or jsonb_typeof(v_event->'ends_at')<>'string' or jsonb_typeof(v_event->'title')<>'string'
       or jsonb_typeof(v_event->'participant_refs')<>'array'
       or (v_event->'location' is not null and jsonb_typeof(v_event->'location') not in ('string','null')) then
      raise exception using errcode='22023',message='calendar prebrief event has invalid bounded fields';
    end if;
    v_event_participant_count:=jsonb_array_length(v_event->'participant_refs');
    if v_event_participant_count>16 or exists(select 1 from jsonb_array_elements(v_event->'participant_refs') refs(value) where jsonb_typeof(refs.value)<>'string')
       or v_event_participant_count<>(select count(distinct value#>>'{}') from jsonb_array_elements(v_event->'participant_refs') refs(value)) then
      raise exception using errcode='22023',message='calendar prebrief event has invalid or duplicate participant refs';
    end if;
    v_participant_count:=v_participant_count+v_event_participant_count;
    if v_participant_count>256 then
      raise exception using errcode='22023',message='calendar prebrief projection participant count exceeds its bound';
    end if;
    v_calendar_key:=v_event->>'calendar_key'; v_event_key:=v_event->>'event_key';
    v_occurrence_key:=v_event->>'occurrence_key'; v_title:=v_event->>'title'; v_location:=v_event->>'location';
    if v_calendar_key!~'^[0-9a-f]{64}$' or v_event_key!~'^[0-9a-f]{64}$' or v_occurrence_key!~'^[0-9a-f]{64}$'
       or length(v_title)>240 or coalesce(length(v_location),0)>240 or btrim(v_title)=''
       or v_title~'[[:alnum:]._%+-]+@[[:alnum:].-]+\.[[:alpha:]]{2,}'
       or coalesce(v_location,'')~'[[:alnum:]._%+-]+@[[:alnum:].-]+\.[[:alpha:]]{2,}'
       or v_title~*'(^|[^[:alnum:]])[a-z][a-z0-9+.-]*:[^[:space:]]' or coalesce(v_location,'')~*'(^|[^[:alnum:]])[a-z][a-z0-9+.-]*:[^[:space:]]'
       or v_title~*'www\.' or coalesce(v_location,'')~*'www\.'
       or v_title~*'\m(join|meeting|conference)[[:space:]]*(id|code|link)?[[:space:]]*[:#=]?[[:space:]]*[a-z0-9_-]{6,}\M'
       or coalesce(v_location,'')~*'\m(join|meeting|conference)[[:space:]]*(id|code|link)?[[:space:]]*[:#=]?[[:space:]]*[a-z0-9_-]{6,}\M' then
      raise exception using errcode='22023',message='calendar prebrief bounded fields must not contain email, URI, www, or join locator';
    end if;
    v_starts_at:=(v_event->>'starts_at')::timestamptz; v_ends_at:=(v_event->>'ends_at')::timestamptz;
    if v_ends_at<=v_starts_at or v_starts_at<v_job.scheduled_for-interval '7 days'
       or v_starts_at>v_job.scheduled_for+interval '45 days' or v_ends_at<v_job.scheduled_for-interval '7 days'
       or v_ends_at>v_job.scheduled_for+interval '45 days' then
      raise exception using errcode='22023',message='calendar prebrief event is outside its bounded snapshot window';
    end if;
    if not (v_calendar_key=any(v_allowed)) then
      raise exception using errcode='22023',message='calendar prebrief event calendar is outside the DB allowlist';
    end if;
    for v_participant_ref in select value from jsonb_array_elements_text(v_event->'participant_refs') loop
      if length(v_participant_ref)>128 or v_participant_ref!~'^[A-Za-z0-9][A-Za-z0-9._:-]*$' then
        raise exception using errcode='22023',message='calendar prebrief participant ref must be a bounded canonical ref';
      end if;
      select count(*) into v_match_count from v_ref_index r where r.ref=v_participant_ref and not r.merged and r.party_id is not null;
      if v_match_count<>1 then
        raise exception using errcode='22023',message='calendar prebrief participant ref does not resolve uniquely to one live unmerged party';
      end if;
    end loop;
  end loop;
  if (select count(*) from jsonb_array_elements(p_events) element(value))
     <> (select count(distinct value->>'occurrence_key') from jsonb_array_elements(p_events) element(value)) then
    raise exception using errcode='22023',message='calendar prebrief snapshot has duplicate occurrence keys';
  end if;

  select coalesce(jsonb_agg(event order by event->>'occurrence_key'),'[]'::jsonb) into v_canonical_events
  from (select jsonb_set(element.value,'{participant_refs}',coalesce((select jsonb_agg(ref order by ref)
          from jsonb_array_elements_text(element.value->'participant_refs') refs(ref)),'[]'::jsonb)) event
        from jsonb_array_elements(p_events) element(value)) normalized;
  v_event_digest:=encode(digest(convert_to(v_canonical_events::text,'UTF8'),'sha256'),'hex');
  v_snapshot_digest:=encode(digest(convert_to(jsonb_build_object(
    'allowlist_revision_id',v_allowlist_revision_id,'allowlist_digest',v_allowlist_digest,'observed_calendar_keys',to_jsonb(v_observed),
    'events',v_canonical_events,'snapshot_at',v_job.scheduled_for)::text,'UTF8'),'sha256'),'hex');
  select * into v_source from ops.calendar_prebrief_source_attestation_receipt
   where job_id=v_job.id and attempt=v_job.attempt;
  if not found or v_source.lease_token<>p_lease or v_source.sponsor<>v_sponsor
     or v_source.mode<>'live' or v_source.destination<>'live'
     or v_source.snapshot_at<>v_job.scheduled_for or v_source.allowlist_revision_id<>v_allowlist_revision_id
     or v_source.allowlist_digest<>v_allowlist_digest or v_source.observed_calendar_keys is distinct from v_observed
     or v_source.event_count<>jsonb_array_length(p_events) or v_source.canonical_event_digest<>v_event_digest then
    raise exception using errcode='55000',message='calendar prebrief projection requires an exact immutable verified source envelope';
  end if;
  select * into v_receipt from ops.calendar_prebrief_projection_receipt where job_id=v_job.id and attempt=v_job.attempt;
  if found then
    if v_receipt.snapshot_digest<>v_snapshot_digest then raise exception using errcode='23505',message='calendar prebrief job attempt conflicts with immutable snapshot'; end if;
    return v_receipt;
  end if;
  select * into v_receipt from ops.calendar_prebrief_projection_receipt where sponsor=v_sponsor and snapshot_at=v_job.scheduled_for;
  if found then
    if v_receipt.snapshot_digest<>v_snapshot_digest then raise exception using errcode='23505',message='calendar prebrief equal snapshot timestamp conflicts with immutable digest'; end if;
    return v_receipt;
  end if;
  if exists(select 1 from ops.calendar_prebrief_projection_receipt where sponsor=v_sponsor and snapshot_at>v_job.scheduled_for) then
    raise exception using errcode='22023',message='calendar prebrief projection refuses stale snapshot';
  end if;

  -- The second check is deliberately adjacent to the destructive replacement.
  if not exists(select 1 from ops.job where id=v_job.id and state='running' and lease_token=p_lease
                and leased_until is not null and leased_until>=now()) then
    raise exception using errcode='55000',message='calendar prebrief projection lease expired before current projection replacement';
  end if;
  delete from ops.calendar_prebrief_projection_event where sponsor=v_sponsor;
  v_participant_count:=0;
  for v_event in select value from jsonb_array_elements(p_events) loop
    insert into ops.calendar_prebrief_projection_event
      (sponsor,calendar_key,event_key,occurrence_key,starts_at,ends_at,title,location,snapshot_at,allowlist_revision_id)
    values(v_sponsor,v_event->>'calendar_key',v_event->>'event_key',v_event->>'occurrence_key',
      (v_event->>'starts_at')::timestamptz,(v_event->>'ends_at')::timestamptz,v_event->>'title',v_event->>'location',v_job.scheduled_for,v_allowlist_revision_id)
    returning id into v_event_id;
    v_event_count:=v_event_count+1;
    for v_participant_ref in select value from jsonb_array_elements_text(v_event->'participant_refs') loop
      select r.party_id,r.subject_type,r.subject_id into v_party_id,v_subject_type,v_subject_id from v_ref_index r
       where r.ref=v_participant_ref and not r.merged and r.party_id is not null;
      insert into ops.calendar_prebrief_projection_participant(event_id,party_id,subject_type,subject_id,participant_ref)
      values(v_event_id,v_party_id,v_subject_type,v_subject_id,v_participant_ref);
      v_participant_count:=v_participant_count+1;
    end loop;
  end loop;
  insert into ops.calendar_prebrief_projection_receipt(job_id,attempt,sponsor,snapshot_at,allowlist_revision_id,allowlist_digest,source_attestation_id,snapshot_digest,event_count,participant_count)
  values(v_job.id,v_job.attempt,v_sponsor,v_job.scheduled_for,v_allowlist_revision_id,v_allowlist_digest,v_source.id,v_snapshot_digest,v_event_count,v_participant_count)
  returning * into v_receipt;
  return v_receipt;
end $$;

create or replace function ops.ingest_calendar_prebrief_canary_projection(
  p_job_id uuid,p_lease uuid,p_destination text,p_observed_calendar_keys text[],p_events jsonb
) returns ops.calendar_prebrief_canary_receipt
language plpgsql security definer set search_path=ops,public,pg_temp
as $$
declare
  v_job ops.job%rowtype; v_sponsor text; v_session_sponsor text; v_destination text;
  v_observed text[]; v_event_count integer; v_event_digest text; v_source ops.calendar_prebrief_source_attestation_receipt%rowtype;
  v_receipt ops.calendar_prebrief_canary_receipt%rowtype; v_event jsonb;
  v_current_revision uuid; v_current_digest text;
begin
  case session_user
    when 'carr_calendar_prebrief_canary_joe' then v_session_sponsor := 'joe';
    when 'carr_calendar_prebrief_canary_dell' then v_session_sponsor := 'dell';
    else raise exception using errcode='42501',message='calendar prebrief canary requires its named externally provisioned execution identity';
  end case;
  if not pg_has_role(session_user,'carr_calendar_prebrief_canary_jobs','member') then
    raise exception using errcode='42501',message='calendar prebrief canary execution identity lacks its capability bundle';
  end if;
  select * into v_job from ops.job where id=p_job_id for update;
  if not found or v_job.state<>'running' or v_job.lease_token is distinct from p_lease
     or v_job.leased_until is null or v_job.leased_until<now() then
    raise exception using errcode='55000',message='calendar prebrief canary requires current live job lease';
  end if;
  select owner_actor into v_sponsor from ops.job_definition
    where key=v_job.definition_key and version=v_job.definition_version for update;
  v_destination:=case v_job.definition_key
    when 'calendar-prebrief-canary-joe-daily' then 'calendar-prebrief-canary-joe'
    when 'calendar-prebrief-canary-dell-daily' then 'calendar-prebrief-canary-dell'
  end;
  if not found or v_job.mode<>'canary' or v_sponsor<>v_session_sponsor or p_destination is distinct from v_destination
     or (v_job.definition_key='calendar-prebrief-canary-joe-daily' and v_sponsor<>'joe')
     or (v_job.definition_key='calendar-prebrief-canary-dell-daily' and v_sponsor<>'dell')
     or v_job.definition_key not in ('calendar-prebrief-canary-joe-daily','calendar-prebrief-canary-dell-daily') then
    raise exception using errcode='42501',message='calendar prebrief canary mode or destination does not match the static job owner';
  end if;
  select array_agg(k order by k) into v_observed from unnest(coalesce(p_observed_calendar_keys,'{}'::text[])) keys(k);
  select event_count,canonical_event_digest into v_event_count,v_event_digest
    from ops.calendar_prebrief_canonical_event_digest(p_events);
  select * into v_source from ops.calendar_prebrief_source_attestation_receipt
   where job_id=v_job.id and attempt=v_job.attempt;
  if not found then
    raise exception using errcode='55000',message='calendar prebrief canary requires an exact immutable verified source envelope';
  end if;
  select active_revision_id,configuration_digest into v_current_revision,v_current_digest
    from ops.calendar_prebrief_allowed_calendar where sponsor=v_sponsor for update;
  if v_current_revision is null or v_current_digest is null or v_source.lease_token<>p_lease or v_source.sponsor<>v_sponsor or v_source.mode<>'canary'
     or v_source.destination<>v_destination or v_source.snapshot_at<>v_job.scheduled_for
     or v_source.allowlist_revision_id<>v_current_revision or v_source.allowlist_digest<>v_current_digest
     or v_source.observed_calendar_keys is distinct from v_observed or v_source.event_count<>v_event_count
     or v_source.canonical_event_digest<>v_event_digest then
    raise exception using errcode='55000',message='calendar prebrief canary requires an exact immutable verified source envelope';
  end if;
  select * into v_receipt from ops.calendar_prebrief_canary_receipt where job_id=v_job.id and attempt=v_job.attempt;
  if found then
    if v_receipt.source_attestation_id<>v_source.id or v_receipt.destination<>v_destination
       or v_receipt.canonical_event_digest<>v_event_digest then
      raise exception using errcode='23505',message='calendar prebrief canary replay conflicts with immutable attempt';
    end if;
    return v_receipt;
  end if;
  if exists(select 1 from ops.calendar_prebrief_canary_receipt where sponsor=v_sponsor and destination=v_destination and snapshot_at>v_job.scheduled_for) then
    raise exception using errcode='22023',message='calendar prebrief canary refuses stale snapshot';
  end if;
  if not exists(select 1 from ops.job where id=v_job.id and state='running' and lease_token=p_lease and leased_until>=now()) then
    raise exception using errcode='55000',message='calendar prebrief canary lease expired before isolated replacement';
  end if;
  if exists(select 1 from jsonb_array_elements(p_events) e(value)
      where coalesce(e.value->>'occurrence_key','') !~ '^[0-9a-f]{64}$')
     or v_event_count<>(select count(distinct e.value->>'occurrence_key') from jsonb_array_elements(p_events) e(value)) then
    raise exception using errcode='22023',message='calendar prebrief canary requires distinct opaque occurrence keys';
  end if;
  delete from ops.calendar_prebrief_canary_event where sponsor=v_sponsor and destination=v_destination;
  for v_event in select value from jsonb_array_elements(p_events) loop
    insert into ops.calendar_prebrief_canary_event(sponsor,destination,occurrence_key,snapshot_at,allowlist_revision_id,source_attestation_id)
    values(v_sponsor,v_destination,v_event->>'occurrence_key',v_job.scheduled_for,v_source.allowlist_revision_id,v_source.id);
  end loop;
  insert into ops.calendar_prebrief_canary_receipt(job_id,attempt,sponsor,destination,snapshot_at,allowlist_revision_id,source_attestation_id,canonical_event_digest,event_count)
  values(v_job.id,v_job.attempt,v_sponsor,v_destination,v_job.scheduled_for,v_source.allowlist_revision_id,v_source.id,v_event_digest,v_event_count)
  returning * into v_receipt;
  return v_receipt;
end $$;

create view v_calendar_prebrief_events as
select e.sponsor,e.occurrence_key,e.starts_at,e.ends_at,e.title,e.location,
       r.ref as participant_ref,r.display_name as participant_display_name,
       r.org_name as participant_org_name,r.status as participant_status,
       lt.last_touch as participant_last_touch,owner.slug as open_owner,action.description as open_action
  from ops.calendar_prebrief_projection_event e
  join ops.calendar_prebrief_allowed_calendar a on a.sponsor=e.sponsor and a.active_revision_id=e.allowlist_revision_id
  left join ops.calendar_prebrief_projection_participant ep on ep.event_id=e.id
  left join v_ref_index r on r.ref=ep.participant_ref and r.subject_type=ep.subject_type and r.subject_id=ep.subject_id and r.party_id=ep.party_id and not r.merged
  left join v_last_touch lt on lt.subject_type=ep.subject_type and lt.subject_id=ep.subject_id
  left join next_action action on action.subject_type=ep.subject_type and action.subject_id=ep.subject_id and action.status='open' and (action.hold_until is null or action.hold_until<=current_date)
  left join actor owner on owner.id=action.owner_id;

create view v_calendar_prebrief_snapshot_status as
select distinct on (r.sponsor) r.sponsor,r.snapshot_at,r.captured_at,r.event_count,r.participant_count
  from ops.calendar_prebrief_projection_receipt r
  join ops.calendar_prebrief_allowed_calendar a on a.sponsor=r.sponsor and a.active_revision_id=r.allowlist_revision_id
 order by r.sponsor,r.snapshot_at desc,r.captured_at desc;

create view v_calendar_prebrief_canary_snapshot_status as
select r.sponsor,r.destination,r.snapshot_at,r.captured_at,r.event_count
  from ops.calendar_prebrief_canary_receipt r
  join ops.calendar_prebrief_allowed_calendar a on a.sponsor=r.sponsor and a.active_revision_id=r.allowlist_revision_id;

revoke all on ops.calendar_prebrief_allowed_calendar,ops.calendar_prebrief_allowlist_receipt,
  ops.calendar_prebrief_projection_event,ops.calendar_prebrief_projection_participant,ops.calendar_prebrief_projection_receipt,
  ops.calendar_prebrief_source_attestation_receipt,ops.calendar_prebrief_capture_challenge,ops.calendar_prebrief_canary_event,ops.calendar_prebrief_canary_receipt
  from public,carr_reader,carr_writer,carr_jobs,carr_authority,carr_calendar_prebrief_jobs,carr_calendar_prebrief_canary_jobs,carr_calendar_prebrief_attestors,carr_calendar_prebrief_email_resolver;
revoke all on function ops.replace_calendar_prebrief_allowlist(text[]) from public,carr_reader,carr_writer,carr_jobs,carr_calendar_prebrief_jobs;
revoke all on function ops.ingest_calendar_prebrief_projection(uuid,uuid,text[],jsonb) from public,carr_reader,carr_writer,carr_jobs,carr_authority;
revoke all on function ops.calendar_prebrief_attestor_sponsor(),ops.calendar_prebrief_resolver_sponsor(),ops.calendar_prebrief_canonical_event_digest(jsonb),
  ops.issue_calendar_prebrief_capture_contract(uuid,uuid),ops.record_calendar_prebrief_verified_envelope(uuid,uuid,uuid,timestamptz,timestamptz,timestamptz,uuid,text,text[],text[],jsonb,text,text,text,text),ops.resolve_calendar_prebrief_email_ref(text),
  ops.ingest_calendar_prebrief_canary_projection(uuid,uuid,text,text[],jsonb)
  from public,carr_reader,carr_writer,carr_jobs,carr_authority,carr_calendar_prebrief_jobs,carr_calendar_prebrief_canary_jobs,carr_calendar_prebrief_attestors,carr_calendar_prebrief_email_resolver;
grant execute on function ops.replace_calendar_prebrief_allowlist(text[]) to carr_authority;
grant execute on function ops.ingest_calendar_prebrief_projection(uuid,uuid,text[],jsonb) to carr_calendar_prebrief_jobs;
grant execute on function ops.record_calendar_prebrief_verified_envelope(uuid,uuid,uuid,timestamptz,timestamptz,timestamptz,uuid,text,text[],text[],jsonb,text,text,text,text) to carr_calendar_prebrief_attestors;
grant execute on function ops.issue_calendar_prebrief_capture_contract(uuid,uuid),ops.resolve_calendar_prebrief_email_ref(text) to carr_calendar_prebrief_email_resolver;
grant execute on function ops.ingest_calendar_prebrief_canary_projection(uuid,uuid,text,text[],jsonb) to carr_calendar_prebrief_canary_jobs;
revoke all on v_calendar_prebrief_events,v_calendar_prebrief_snapshot_status,v_calendar_prebrief_canary_snapshot_status from public,carr_writer,carr_jobs,carr_authority,carr_calendar_prebrief_jobs,carr_calendar_prebrief_canary_jobs,carr_calendar_prebrief_attestors,carr_calendar_prebrief_email_resolver;
grant select on v_calendar_prebrief_events,v_calendar_prebrief_snapshot_status,v_calendar_prebrief_canary_snapshot_status to carr_reader;

commit;

do $$
declare v_columns text[]; v_role record;
begin
  if not exists(select 1 from pg_roles where rolname='carr_calendar_prebrief_jobs' and not rolcanlogin) then
    raise exception '0216 FAILED: calendar prebrief capability bundle is not NOLOGIN';
  end if;
  if not exists(select 1 from pg_roles where rolname='carr_calendar_prebrief_canary_jobs' and not rolcanlogin) then
    raise exception '0216 FAILED: calendar prebrief canary capability bundle is not NOLOGIN';
  end if;
  if not exists(select 1 from pg_roles where rolname='carr_calendar_prebrief_attestors' and not rolcanlogin)
     or not exists(select 1 from pg_roles where rolname='carr_calendar_prebrief_email_resolver' and not rolcanlogin) then
    raise exception '0216 FAILED: calendar prebrief device capability bundles are not NOLOGIN';
  end if;
  if (select count(*) from ops.job_definition where version=1 and enabled=false and execution_kind='deterministic'
      and inventory_contract->>'owner'=owner_actor
      and legacy_schedule->>'status'='disabled'
      and recurrence->>'cron'='30 6 * * 1-5'
      and ((key='calendar-prebrief-projection-joe-daily' and owner_actor='joe') or (key='calendar-prebrief-projection-dell-daily' and owner_actor='dell')))<>2 then
    raise exception '0216 FAILED: separate disabled pre-06:45 Joe/Dell job definitions are missing';
  end if;
  if (select count(*) from ops.job_definition where version=1 and enabled=false and execution_kind='deterministic'
      and legacy_schedule->>'status'='disabled' and recurrence->>'cron'='30 6 * * 1-5'
      and ((key='calendar-prebrief-canary-joe-daily' and owner_actor='joe') or (key='calendar-prebrief-canary-dell-daily' and owner_actor='dell')))<>2 then
    raise exception '0216 FAILED: separate disabled isolated Joe/Dell canary definitions are missing';
  end if;
  if exists(select 1 from information_schema.columns where table_schema='ops' and table_name in
      ('calendar_prebrief_allowed_calendar','calendar_prebrief_allowlist_receipt','calendar_prebrief_projection_event','calendar_prebrief_projection_participant','calendar_prebrief_projection_receipt','calendar_prebrief_source_attestation_receipt','calendar_prebrief_canary_event','calendar_prebrief_canary_receipt')
      and column_name~'(email|description|url|recurrence|credential|eventkit)') then
    raise exception '0216 FAILED: calendar prebrief base tables retain prohibited source data columns';
  end if;
  for v_role in select unnest(array['carr_reader','carr_writer','carr_jobs','carr_authority','carr_calendar_prebrief_jobs','carr_calendar_prebrief_canary_jobs','carr_calendar_prebrief_attestors','carr_calendar_prebrief_email_resolver']) role_name loop
    if exists(select 1 from unnest(array['ops.calendar_prebrief_allowed_calendar','ops.calendar_prebrief_allowlist_receipt','ops.calendar_prebrief_projection_event','ops.calendar_prebrief_projection_participant','ops.calendar_prebrief_projection_receipt','ops.calendar_prebrief_source_attestation_receipt','ops.calendar_prebrief_capture_challenge','ops.calendar_prebrief_canary_event','ops.calendar_prebrief_canary_receipt']) tbl(name)
              where has_table_privilege(v_role.role_name,tbl.name,'select')
                 or has_table_privilege(v_role.role_name,tbl.name,'insert')
                 or has_table_privilege(v_role.role_name,tbl.name,'update')
                 or has_table_privilege(v_role.role_name,tbl.name,'delete')) then
      raise exception '0216 FAILED: calendar prebrief table privilege leaked to %',v_role.role_name;
    end if;
  end loop;
  if has_function_privilege('carr_reader','ops.ingest_calendar_prebrief_projection(uuid,uuid,text[],jsonb)'::regprocedure,'execute')
     or has_function_privilege('carr_jobs','ops.ingest_calendar_prebrief_projection(uuid,uuid,text[],jsonb)'::regprocedure,'execute')
     or has_function_privilege('carr_writer','ops.ingest_calendar_prebrief_projection(uuid,uuid,text[],jsonb)'::regprocedure,'execute')
     or has_function_privilege('carr_authority','ops.ingest_calendar_prebrief_projection(uuid,uuid,text[],jsonb)'::regprocedure,'execute')
     or not has_function_privilege('carr_calendar_prebrief_jobs','ops.ingest_calendar_prebrief_projection(uuid,uuid,text[],jsonb)'::regprocedure,'execute')
     or has_function_privilege('carr_calendar_prebrief_canary_jobs','ops.ingest_calendar_prebrief_projection(uuid,uuid,text[],jsonb)'::regprocedure,'execute')
     or has_function_privilege('carr_reader','ops.replace_calendar_prebrief_allowlist(text[])'::regprocedure,'execute')
     or has_function_privilege('carr_jobs','ops.replace_calendar_prebrief_allowlist(text[])'::regprocedure,'execute')
     or has_function_privilege('carr_writer','ops.replace_calendar_prebrief_allowlist(text[])'::regprocedure,'execute')
     or has_function_privilege('carr_calendar_prebrief_jobs','ops.replace_calendar_prebrief_allowlist(text[])'::regprocedure,'execute')
     or not has_function_privilege('carr_authority','ops.replace_calendar_prebrief_allowlist(text[])'::regprocedure,'execute')
     or not has_function_privilege('carr_calendar_prebrief_attestors','ops.record_calendar_prebrief_verified_envelope(uuid,uuid,uuid,timestamptz,timestamptz,timestamptz,uuid,text,text[],text[],jsonb,text,text,text,text)'::regprocedure,'execute')
     or has_function_privilege('carr_calendar_prebrief_jobs','ops.record_calendar_prebrief_verified_envelope(uuid,uuid,uuid,timestamptz,timestamptz,timestamptz,uuid,text,text[],text[],jsonb,text,text,text,text)'::regprocedure,'execute')
     or has_function_privilege('carr_calendar_prebrief_canary_jobs','ops.record_calendar_prebrief_verified_envelope(uuid,uuid,uuid,timestamptz,timestamptz,timestamptz,uuid,text,text[],text[],jsonb,text,text,text,text)'::regprocedure,'execute')
     or not has_function_privilege('carr_calendar_prebrief_email_resolver','ops.resolve_calendar_prebrief_email_ref(text)'::regprocedure,'execute')
     or not has_function_privilege('carr_calendar_prebrief_email_resolver','ops.issue_calendar_prebrief_capture_contract(uuid,uuid)'::regprocedure,'execute')
     or has_function_privilege('carr_calendar_prebrief_attestors','ops.resolve_calendar_prebrief_email_ref(text)'::regprocedure,'execute')
     or has_function_privilege('carr_calendar_prebrief_jobs','ops.ingest_calendar_prebrief_canary_projection(uuid,uuid,text,text[],jsonb)'::regprocedure,'execute')
     or has_function_privilege('carr_calendar_prebrief_canary_jobs','ops.issue_calendar_prebrief_capture_contract(uuid,uuid)'::regprocedure,'execute')
     or not has_function_privilege('carr_calendar_prebrief_canary_jobs','ops.ingest_calendar_prebrief_canary_projection(uuid,uuid,text,text[],jsonb)'::regprocedure,'execute') then
    raise exception '0216 FAILED: calendar prebrief function privilege boundary is wrong';
  end if;
  if not exists(select 1 from information_schema.columns where table_schema='ops' and table_name='calendar_prebrief_allowed_calendar' and column_name='configuration_digest')
     or not exists(select 1 from information_schema.columns where table_schema='ops' and table_name='calendar_prebrief_allowed_calendar' and column_name='active_revision_id')
     or not exists(select 1 from information_schema.columns where table_schema='ops' and table_name='calendar_prebrief_projection_event' and column_name='allowlist_revision_id')
     or not exists(select 1 from information_schema.columns where table_schema='ops' and table_name='calendar_prebrief_projection_receipt' and column_name='allowlist_digest')
     or not exists(select 1 from information_schema.columns where table_schema='ops' and table_name='calendar_prebrief_projection_receipt' and column_name='allowlist_revision_id') then
    raise exception '0216 FAILED: allowlist freshness revision columns are missing';
  end if;
  if not exists(select 1 from information_schema.columns where table_schema='ops' and table_name='calendar_prebrief_projection_receipt' and column_name='source_attestation_id')
     or not exists(select 1 from information_schema.columns where table_schema='ops' and table_name='calendar_prebrief_source_attestation_receipt' and column_name='canonical_event_digest')
     or not exists(select 1 from information_schema.columns where table_schema='ops' and table_name='calendar_prebrief_source_attestation_receipt' and column_name='attestor_identity')
     or not exists(select 1 from information_schema.columns where table_schema='ops' and table_name='calendar_prebrief_source_attestation_receipt' and column_name='signature_sha256')
     or not exists(select 1 from information_schema.columns where table_schema='ops' and table_name='calendar_prebrief_source_attestation_receipt' and column_name='capture_challenge_id')
     or not exists(select 1 from information_schema.columns where table_schema='ops' and table_name='calendar_prebrief_capture_challenge' and column_name='scheduled_for')
     or not exists(select 1 from information_schema.columns where table_schema='ops' and table_name='calendar_prebrief_canary_receipt' and column_name='allowlist_revision_id')
     or not exists(select 1 from information_schema.columns where table_schema='ops' and table_name='calendar_prebrief_canary_event' and column_name='allowlist_revision_id') then
    raise exception '0216 FAILED: immutable verified source envelope columns are missing';
  end if;
  if not exists(select 1 from pg_trigger where tgrelid='ops.calendar_prebrief_allowlist_receipt'::regclass and tgname='calendar_prebrief_allowlist_receipt_append_only' and not tgisinternal)
     or not exists(select 1 from pg_trigger where tgrelid='ops.calendar_prebrief_projection_receipt'::regclass and tgname='calendar_prebrief_projection_receipt_append_only' and not tgisinternal)
     or not exists(select 1 from pg_trigger where tgrelid='ops.calendar_prebrief_source_attestation_receipt'::regclass and tgname='calendar_prebrief_source_attestation_receipt_append_only' and not tgisinternal)
     or not exists(select 1 from pg_trigger where tgrelid='ops.calendar_prebrief_capture_challenge'::regclass and tgname='calendar_prebrief_capture_challenge_append_only' and not tgisinternal)
     or not exists(select 1 from pg_trigger where tgrelid='ops.calendar_prebrief_canary_receipt'::regclass and tgname='calendar_prebrief_canary_receipt_append_only' and not tgisinternal) then
    raise exception '0216 FAILED: append-only calendar prebrief receipts are not guarded';
  end if;
  if (select count(*) from pg_constraint where contype='f' and confrelid='ops.calendar_prebrief_allowlist_receipt'::regclass
        and conrelid in ('ops.calendar_prebrief_allowed_calendar'::regclass,'ops.calendar_prebrief_projection_event'::regclass,'ops.calendar_prebrief_projection_receipt'::regclass,'ops.calendar_prebrief_source_attestation_receipt'::regclass,'ops.calendar_prebrief_capture_challenge'::regclass,'ops.calendar_prebrief_canary_event'::regclass,'ops.calendar_prebrief_canary_receipt'::regclass))<>7
     or not exists(select 1 from pg_constraint where contype='f'
                   and conrelid='ops.calendar_prebrief_source_attestation_receipt'::regclass
                   and confrelid='ops.calendar_prebrief_capture_challenge'::regclass) then
    raise exception '0216 FAILED: current allowlist, source receipts, and projections do not all bind immutable revisions and contracts';
  end if;
  select array_agg(column_name order by ordinal_position) into v_columns from information_schema.columns where table_schema='public' and table_name='v_calendar_prebrief_events';
  if v_columns is distinct from array['sponsor','occurrence_key','starts_at','ends_at','title','location','participant_ref','participant_display_name','participant_org_name','participant_status','participant_last_touch','open_owner','open_action'] then
    raise exception '0216 FAILED: reader event view column boundary drifted';
  end if;
  select array_agg(column_name order by ordinal_position) into v_columns from information_schema.columns where table_schema='public' and table_name='v_calendar_prebrief_snapshot_status';
  if v_columns is distinct from array['sponsor','snapshot_at','captured_at','event_count','participant_count'] then
    raise exception '0216 FAILED: reader snapshot-status view column boundary drifted';
  end if;
end $$;
