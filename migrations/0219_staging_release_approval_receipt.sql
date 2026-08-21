-- A staging candidate has no Production recovery bundle and must never borrow
-- the Production-only approval function.  This is a separate, append-only Joe
-- approval fact for the exact staging plan.

begin;

create table ops.staging_release_approval_receipt (
  id uuid primary key default gen_random_uuid(),
  idempotency_key uuid not null unique,
  release_id uuid not null references ops.release(id) on delete restrict,
  plan_hash text not null,
  approved_by_actor text not null check (approved_by_actor='joe'),
  approved_at timestamptz not null,
  approval_expires_at timestamptz not null check (approval_expires_at > approved_at),
  verifier_actor text not null check (verifier_actor=lower(btrim(verifier_actor)) and btrim(verifier_actor)<>''),
  verifier_evidence_ref text not null check (verifier_evidence_ref=btrim(verifier_evidence_ref) and btrim(verifier_evidence_ref)<>''),
  approval_sha256 text not null unique check (approval_sha256 ~ '^sha256:[0-9a-f]{64}$'),
  evidence_ref text not null unique check (evidence_ref ~ '^ops\.staging-release-approval:sha256:[0-9a-f]{64}$')
);

alter table ops.release add column staging_approval_receipt_id uuid
  references ops.staging_release_approval_receipt(id) on delete restrict;
create unique index release_staging_approval_receipt_once
  on ops.release(staging_approval_receipt_id) where staging_approval_receipt_id is not null;

create function ops.refuse_staging_release_approval_mutation()
returns trigger language plpgsql as $$
begin raise exception 'staging release approval evidence is append-only'; end $$;

create trigger staging_release_approval_receipt_append_only
before update or delete on ops.staging_release_approval_receipt
for each row execute function ops.refuse_staging_release_approval_mutation();

create function ops.staging_release_plan_revision_invalidates_approval()
returns trigger language plpgsql as $$
begin
  if old.environment='staging' and old.plan_hash is distinct from new.plan_hash
     and old.state='complete' then raise exception 'complete staging release plan is immutable';
  end if;
  if old.environment='staging' and old.plan_hash is distinct from new.plan_hash
     and old.state in ('approved','deploying','verifying') then
    new.state:='candidate'; new.approved_by_actor:=null; new.approved_at:=null;
    new.approval_expires_at:=null; new.staging_approval_receipt_id:=null;
  end if;
  return new;
end $$;
create trigger a_staging_release_plan_revision_invalidates_approval
before update of plan_hash on ops.release for each row
execute function ops.staging_release_plan_revision_invalidates_approval();

create function ops.staging_release_approval_is_typed()
returns trigger language plpgsql as $$
declare receipt ops.staging_release_approval_receipt%rowtype;
begin
  if tg_op='UPDATE' and old.environment='staging'
     and old.state in ('approved','deploying','verifying','complete')
     and new.environment is distinct from old.environment then
    raise exception 'promoted staging release environment is immutable';
  end if;
  if new.environment<>'staging' or new.state not in ('approved','deploying','verifying','complete') then return new; end if;
  if new.staging_approval_receipt_id is null then raise exception 'staging promoted release requires its typed approval receipt'; end if;
  select * into receipt from ops.staging_release_approval_receipt where id=new.staging_approval_receipt_id;
  if not found or receipt.release_id<>new.id or receipt.plan_hash<>new.plan_hash
     or receipt.approved_by_actor<>'joe' or new.approved_by_actor<>receipt.approved_by_actor
     or new.approved_at<>receipt.approved_at or new.approval_expires_at<>receipt.approval_expires_at
     or new.verifier_actor<>receipt.verifier_actor or new.verifier_evidence_ref<>receipt.verifier_evidence_ref then
    raise exception 'staging promoted release does not match its typed approval receipt';
  end if;
  if tg_op='UPDATE' and old.state in ('approved','deploying','verifying','complete')
     and (new.staging_approval_receipt_id,new.approved_by_actor,new.approved_at,new.approval_expires_at,
          new.verifier_actor,new.verifier_evidence_ref) is distinct from
         (old.staging_approval_receipt_id,old.approved_by_actor,old.approved_at,old.approval_expires_at,
          old.verifier_actor,old.verifier_evidence_ref) then
    raise exception 'staging approval projection is immutable across promoted states';
  end if;
  if (tg_op='INSERT' or old.state not in ('approved','deploying','verifying','complete'))
     and session_user<>'carr_authority_joe' then raise exception 'staging approval requires Joe authority'; end if;
  return new;
end $$;
create trigger staging_release_approval_is_typed
before insert or update of environment,state,approved_by_actor,approved_at,approval_expires_at,
  verifier_actor,verifier_evidence_ref,staging_approval_receipt_id,plan_hash on ops.release
for each row execute function ops.staging_release_approval_is_typed();

-- Retargeting a promoted staging release must run the existing Production Joe
-- trigger too; otherwise an environment rewrite could bypass its scope.
drop trigger if exists program5_release_approval_is_joe_owned on ops.release;
create trigger program5_release_approval_is_joe_owned
before insert or update of environment,state,approved_by_actor,approved_at,approval_expires_at,
  approval_receipt_id on ops.release
for each row execute function ops.program5_release_approval_is_joe_owned();

create function ops.approve_staging_release(
  p_release_key text, p_plan_hash text, p_idempotency_key uuid,
  p_expires_hours integer, p_verifier_actor text, p_verifier_evidence_ref text
) returns jsonb
language plpgsql security definer set search_path=ops,public,pg_temp
as $$
declare
  actor_slug text; rel ops.release%rowtype; existing ops.staging_release_approval_receipt%rowtype;
  approved_time timestamptz; expiry_time timestamptz; projection jsonb; approval_hash text;
  approval_ref text; approval_uuid uuid; verifier_actor_value text; verifier_evidence_value text;
begin
  actor_slug:=ops.authority_actor_slug();
  if session_user<>'carr_authority_joe' or actor_slug<>'joe' then
    raise exception 'staging approval requires Joe authority';
  end if;
  if p_idempotency_key is null or coalesce(p_release_key,'')='' or coalesce(p_plan_hash,'')=''
     or coalesce(p_expires_hours,0) not between 1 and 24
     or coalesce(btrim(p_verifier_actor),'')='' or coalesce(btrim(p_verifier_evidence_ref),'')='' then
    raise exception 'invalid typed staging approval input';
  end if;
  verifier_actor_value:=lower(btrim(p_verifier_actor));
  verifier_evidence_value:=btrim(p_verifier_evidence_ref);
  perform pg_advisory_xact_lock(hashtextextended(p_idempotency_key::text,219));
  select * into existing from ops.staging_release_approval_receipt where idempotency_key=p_idempotency_key;
  if found then
    if existing.plan_hash<>p_plan_hash
       or existing.approval_expires_at-existing.approved_at<>make_interval(hours=>p_expires_hours)
       or existing.verifier_actor<>verifier_actor_value or existing.verifier_evidence_ref<>verifier_evidence_value
       or not exists (select 1 from ops.release where id=existing.release_id and release_key=p_release_key
                        and environment='staging' and plan_hash=existing.plan_hash
                        and state in ('approved','deploying','verifying','complete')
                        and approved_by_actor='joe' and approved_at=existing.approved_at
                        and approval_expires_at=existing.approval_expires_at
                        and staging_approval_receipt_id=existing.id
                        and verifier_actor=existing.verifier_actor
                        and verifier_evidence_ref=existing.verifier_evidence_ref) then
      raise exception 'staging approval idempotency key was reused with changed input';
    end if;
    return jsonb_build_object('approval_receipt_id',existing.id,'approval_ref',existing.evidence_ref,
      'approval_expires_at',existing.approval_expires_at,'replayed',true);
  end if;
  select * into rel from ops.release where release_key=p_release_key for update;
  if not found or rel.environment<>'staging' or rel.state<>'candidate'
     or rel.plan_hash is distinct from p_plan_hash or rel.rollback_ready is not true
     or coalesce(btrim(rel.rollback_plan_ref),'')='' then
    raise exception 'release is not the exact rollback-ready staging candidate plan requested';
  end if;
  if rel.maker_actor is not null and verifier_actor_value=lower(btrim(rel.maker_actor)) then
    raise exception 'maker cannot independently verify their own staging release';
  end if;
  approved_time:=clock_timestamp(); expiry_time:=approved_time+make_interval(hours=>p_expires_hours);
  projection:=jsonb_build_object('release_id',rel.id,'environment','staging','plan_hash',rel.plan_hash,'approved_by_actor',actor_slug,
    'approved_at',approved_time,'approval_expires_at',expiry_time,'verifier_actor',verifier_actor_value,
    'verifier_evidence_ref',verifier_evidence_value);
  approval_hash:='sha256:'||encode(public.digest(projection::text,'sha256'),'hex');
  approval_ref:='ops.staging-release-approval:'||approval_hash;
  insert into ops.staging_release_approval_receipt(idempotency_key,release_id,plan_hash,approved_by_actor,
    approved_at,approval_expires_at,verifier_actor,verifier_evidence_ref,approval_sha256,evidence_ref)
  values(p_idempotency_key,rel.id,rel.plan_hash,actor_slug,approved_time,expiry_time,verifier_actor_value,
    verifier_evidence_value,approval_hash,approval_ref) returning id into approval_uuid;
  update ops.release set state='approved',approved_by_actor=actor_slug,approved_at=approved_time,
    approval_expires_at=expiry_time,verifier_actor=verifier_actor_value,
    verifier_evidence_ref=verifier_evidence_value,staging_approval_receipt_id=approval_uuid where id=rel.id;
  return jsonb_build_object('approval_receipt_id',approval_uuid,'approval_ref',approval_ref,
    'approval_expires_at',expiry_time,'replayed',false);
end $$;

revoke all on table ops.staging_release_approval_receipt from public,carr_reader,carr_writer,carr_jobs,carr_authority;
grant select on table ops.staging_release_approval_receipt to carr_reader,carr_writer,carr_jobs,carr_authority;
revoke all on function ops.approve_staging_release(text,text,uuid,integer,text,text) from public,carr_reader,carr_writer,carr_jobs;
grant execute on function ops.approve_staging_release(text,text,uuid,integer,text,text) to carr_authority;

commit;
