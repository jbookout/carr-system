-- 0205_program5_approval_verifier.sql
-- Program 5: approval may receive verification that completed after candidacy.
--
-- 0202 moved approval into a typed Joe-authority function but accidentally
-- removed the late-verifier path that 0169 requires before a release can become
-- approved.  This forward migration restores that path inside the authority
-- function, preserving the typed receipt and idempotent replay contract.

begin;

alter table ops.release_approval_receipt
  add column verifier_actor text,
  add column verifier_evidence_ref text;

do $$
begin
  if exists (select 1 from ops.release_approval_receipt) then
    raise exception '0205 refuses to rewrite existing approval receipts: populated 0202 evidence requires a separate audited versioned conversion';
  end if;
end $$;

alter table ops.release_approval_receipt
  alter column verifier_actor set not null,
  alter column verifier_evidence_ref set not null,
  add constraint release_approval_receipt_verifier_actor_nonblank
    check (btrim(verifier_actor)<>''),
  add constraint release_approval_receipt_verifier_evidence_nonblank
    check (btrim(verifier_evidence_ref)<>''),
  add constraint release_approval_receipt_verifier_actor_canonical
    check (verifier_actor=lower(btrim(verifier_actor))),
  add constraint release_approval_receipt_verifier_evidence_canonical
    check (verifier_evidence_ref=btrim(verifier_evidence_ref));

create or replace function ops.program5_release_verifier_is_immutable()
returns trigger language plpgsql as $$
begin
  if (new.verifier_actor,new.verifier_evidence_ref)
       is distinct from (old.verifier_actor,old.verifier_evidence_ref)
     and (old.approval_receipt_id is not null
          or old.state in ('approved','deploying','verifying','complete'))
     and new.plan_hash is not distinct from old.plan_hash then
    raise exception 'Program 5 verifier evidence is immutable after approval; revise the plan to invalidate approval first';
  end if;
  return new;
end $$;

drop trigger if exists program5_release_verifier_is_immutable on ops.release;
create trigger program5_release_verifier_is_immutable
before update of verifier_actor,verifier_evidence_ref,state,approval_receipt_id,plan_hash on ops.release
for each row execute function ops.program5_release_verifier_is_immutable();

create or replace function ops.approve_program5_release(
  p_release_key text,p_plan_hash text,p_idempotency_key uuid,p_expires_hours integer,
  p_verifier_actor text,p_verifier_evidence_ref text
) returns jsonb
language plpgsql security definer set search_path=ops,public,pg_temp
as $$
declare actor_slug text; rel ops.release%rowtype; existing ops.release_approval_receipt%rowtype;
  rehearsal_run ops.run%rowtype; approval_uuid uuid; approved_time timestamptz;
  expiry_time timestamptz; projection jsonb; approval_hash text; approval_ref text;
  supplied_verifier_actor text; supplied_verifier_evidence text;
  candidate_verifier_actor text; candidate_verifier_evidence text;
  verifier_actor_value text; verifier_evidence_value text;
begin
  actor_slug:=ops.authority_actor_slug();
  if actor_slug<>'joe' then raise exception 'Program 5 Production approval requires Joe authority'; end if;
  if p_idempotency_key is null or coalesce(p_release_key,'')='' or coalesce(p_plan_hash,'')=''
     or coalesce(p_expires_hours,0) not between 1 and 24 then
    raise exception 'invalid Program 5 approval input';
  end if;
  if (p_verifier_actor is null) <> (p_verifier_evidence_ref is null)
     or (p_verifier_actor is not null
         and (btrim(p_verifier_actor)='' or btrim(p_verifier_evidence_ref)='')) then
    raise exception 'supplied verifier actor and evidence must be an atomic nonblank pair';
  end if;
  supplied_verifier_actor:=case when p_verifier_actor is null then null else lower(btrim(p_verifier_actor)) end;
  supplied_verifier_evidence:=case when p_verifier_evidence_ref is null then null else btrim(p_verifier_evidence_ref) end;
  perform pg_advisory_xact_lock(hashtextextended(p_idempotency_key::text,202));
  select * into existing from ops.release_approval_receipt where idempotency_key=p_idempotency_key;
  if found then
    if existing.plan_hash<>p_plan_hash
       or existing.approval_expires_at-existing.approved_at
          <> make_interval(hours=>p_expires_hours)
       or not exists (
      select 1 from ops.release where id=existing.release_id and release_key=p_release_key
        and plan_hash=existing.plan_hash and approval_receipt_id=existing.id
        and state in ('approved','deploying','verifying','complete')
        and approved_at=existing.approved_at
        and approval_expires_at=existing.approval_expires_at
        and (supplied_verifier_actor is null or existing.verifier_actor=supplied_verifier_actor)
        and (supplied_verifier_evidence is null or existing.verifier_evidence_ref=supplied_verifier_evidence)) then
      raise exception 'Program 5 approval idempotency key was reused with changed input';
    end if;
    return jsonb_build_object('approval_receipt_id',existing.id,
      'approval_ref',existing.evidence_ref,'approval_expires_at',existing.approval_expires_at,
      'replayed',true);
  end if;
  select * into rel from ops.release where release_key=p_release_key for update;
  if not found or rel.environment<>'production' or rel.state<>'candidate'
     or rel.plan_hash is distinct from p_plan_hash then
    raise exception 'release is not the exact Production candidate plan requested';
  end if;
  if (rel.verifier_actor is null) <> (rel.verifier_evidence_ref is null)
     or (rel.verifier_actor is not null
         and (btrim(rel.verifier_actor)='' or btrim(rel.verifier_evidence_ref)='')) then
    raise exception 'candidate verifier actor and evidence must be an atomic nonblank pair';
  end if;
  candidate_verifier_actor:=case when rel.verifier_actor is null then null else lower(btrim(rel.verifier_actor)) end;
  candidate_verifier_evidence:=case when rel.verifier_evidence_ref is null then null else btrim(rel.verifier_evidence_ref) end;
  verifier_actor_value:=coalesce(supplied_verifier_actor,candidate_verifier_actor);
  verifier_evidence_value:=coalesce(supplied_verifier_evidence,candidate_verifier_evidence);
  if verifier_actor_value is null or verifier_evidence_value is null then
    raise exception 'release cannot be approved without an INDEPENDENT VERIFIER and verification evidence';
  end if;
  if rel.maker_actor is not null
     and verifier_actor_value=lower(btrim(rel.maker_actor)) then
    raise exception 'maker cannot independently verify their own release';
  end if;
  select r.* into rehearsal_run from ops.run r
   join ops.staging_recovery_rehearsal_bundle b on b.id=r.recovery_rehearsal_bundle_id
   where r.release_id=rel.id and r.service_id=rel.service_id
     and r.environment='staging' and r.run_key='recovery.rehearsal.worker'
     and r.state='succeeded' and r.evidence_ref=b.evidence_ref
     and b.current_release_id=rel.id and b.service_id=rel.service_id
     and b.recovery_strategy=rel.recovery_strategy
     and b.recovery_plan_ref=rel.rollback_plan_ref and b.plan_hash=rel.plan_hash
     and b.declared_migration_set_sha256=ops.program5_migration_set_sha256(rel.migration_set)
     and b.declared_migration_count=cardinality(rel.migration_set)
     and b.declared_schema_highest_migration=rel.schema_highest_migration
     and b.declared_schema_applied_count=rel.schema_applied_count
     and b.declared_schema_ledger_sha256=rel.schema_ledger_sha256
     and b.completed_at between clock_timestamp()-interval '24 hours' and clock_timestamp()
   order by r.ended_at desc limit 1;
  if not found then raise exception 'release has no exact typed recovery rehearsal'; end if;
  approved_time:=clock_timestamp(); expiry_time:=approved_time+make_interval(hours=>p_expires_hours);
  projection:=jsonb_build_object('release_id',rel.id,'plan_hash',rel.plan_hash,
    'recovery_run_id',rehearsal_run.id,'recovery_bundle_id',rehearsal_run.recovery_rehearsal_bundle_id,
    'approved_by_actor',actor_slug,'approved_at',approved_time,'approval_expires_at',expiry_time,
    'verifier_actor',verifier_actor_value,'verifier_evidence_ref',verifier_evidence_value);
  approval_hash:='sha256:'||encode(public.digest(projection::text,'sha256'),'hex');
  approval_ref:='ops.program5-release-approval:'||approval_hash;
  insert into ops.release_approval_receipt(idempotency_key,release_id,recovery_run_id,
    recovery_bundle_id,plan_hash,approved_by_actor,approved_at,approval_expires_at,
    verifier_actor,verifier_evidence_ref,
    approval_sha256,evidence_ref)
  values(p_idempotency_key,rel.id,rehearsal_run.id,rehearsal_run.recovery_rehearsal_bundle_id,
    rel.plan_hash,actor_slug,approved_time,expiry_time,verifier_actor_value,verifier_evidence_value,
    approval_hash,approval_ref)
  returning id into approval_uuid;
  update ops.release set verifier_actor=verifier_actor_value,
    verifier_evidence_ref=verifier_evidence_value,state='approved',approved_by_actor=actor_slug,
    approved_at=approved_time,approval_expires_at=expiry_time,
    approval_receipt_id=approval_uuid where id=rel.id;
  return jsonb_build_object('approval_receipt_id',approval_uuid,'approval_ref',approval_ref,
    'approval_expires_at',expiry_time,'replayed',false);
end $$;

create or replace function ops.approve_program5_release(
  p_release_key text,p_plan_hash text,p_idempotency_key uuid,p_expires_hours integer
) returns jsonb
language sql security definer set search_path=ops,public,pg_temp
as $$
  select ops.approve_program5_release($1,$2,$3,$4,null,null)
$$;

revoke all on function ops.approve_program5_release(text,text,uuid,integer) from public,carr_reader,carr_writer,carr_jobs;
revoke all on function ops.approve_program5_release(text,text,uuid,integer,text,text) from public,carr_reader,carr_writer,carr_jobs;
grant execute on function ops.approve_program5_release(text,text,uuid,integer) to carr_authority;
grant execute on function ops.approve_program5_release(text,text,uuid,integer,text,text) to carr_authority;

commit;
