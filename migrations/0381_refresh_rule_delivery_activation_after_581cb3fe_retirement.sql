-- 0381_refresh_rule_delivery_activation_after_581cb3fe_retirement.sql
--
-- WHY THIS EXISTS. `./run.sh local-db-ci --class migration` fails on
-- origin/main at the rule-delivery acceptance step with
--
--     rule-delivery cutover refused: activation map digest preimage differs
--
-- and behind that first refusal sits a second one. Both are fallout from the
-- WR-000019 rule-retirement batch, and neither is reachable from hosted CI:
-- no workflow in .github/workflows runs ops/rule-delivery-local-pg-acceptance.py
-- or ops/local-pg-ci.py, so this gate is exercised only by a developer running
-- the local database lane, and it has been red there for every one of them.
--
-- THE FIRST REFUSAL — the pinned preimage went stale. Migration 0348 pinned
-- ops.rule_delivery_activation_target.map_digest to the sha256 of
-- ops/config/rule-enforcement-map.json as it stood when 0348 was authored
-- (4038e097...). The map has moved several times since — most recently when
-- the WR-000019 batch retired eleven rules — and no migration refreshed the
-- pin. ops.set_rule_delivery_mode() compares the caller's expected digest
-- against every stored row before it will flip ops.rule_delivery_policy, and
-- the acceptance run takes its expected value from
-- ops/config/rule-delivery-activation-overlay.v1.json's base_map_sha256 via
-- lib/rule_delivery_activation.load_validated(), which validate_overlay()
-- proves equals the map file's actual bytes. Those two disagreed, so the
-- cutover refused. This migration re-pins to the current f7bf5726..., the
-- same way 0332 and 0348 each did before it.
--
-- THE SECOND REFUSAL — the reviewed set is eight now, not nine. 581cb3fe was
-- one of the nine reviewed activation ids until that same WR-000019 batch
-- retired it (superseded_by aa411351) and removed it from active_rule_ids,
-- rule_controls and rule_load_layers entirely. The overlay dropped its target
-- and lib/rule_delivery_activation.EXPECTED_IDS dropped it too, taking both to
-- eight -- but its row in ops.rule_delivery_activation_target stayed, and
-- ops.set_rule_delivery_mode() hard-codes nine in six separate places. So
-- refreshing the digest alone only moves the failure one line down, to
--
--     active target preimage count is 8, expected 9
--
-- because the function joins its nine target rows to active rules and the
-- retired one no longer has one. The row and the cardinality have to go
-- together with the digest, or the gate stays red.
--
-- WHAT THIS CHANGES AND WHAT IT DOES NOT. Delivery mode is untouched: the
-- policy stays in shadow, and a later, separately authorized cutover still
-- owns the atomic shadow/enforced transition. No control is added to or
-- removed from ops.enforcement_control_catalog. The eight surviving reviewed
-- ids (25fcddee, 3fa17fa0, 72e06bdf, 113b3833, 57d13061, c66dc739, 49533583,
-- 557838a5) keep every other column exactly as reviewed -- only map_digest
-- moves, and only the retired ninth row is removed.
--
-- WHY THE DIGEST IS SET UNCONDITIONALLY RATHER THAN FROM A NAMED OLD VALUE.
-- 0332 bound the exact prior digest in its WHERE clause. That is the stricter
-- shape, and it is the wrong one here: an unpushed branch already carries its
-- own in-flight refresh of these same rows to a third value, so the row this
-- migration meets on a given store may legitimately carry any of several
-- prior digests. Binding one of them would make this migration fail on the
-- merge order it is most likely to meet. The post-condition below is what
-- makes that safe -- it asserts the exact end state rather than trusting the
-- start state.

begin;

do $$
declare
  v_new_digest constant text :=
    'f7bf5726d329dd240434e51f7401fac9a977a3fb710636738f379f60f565f904';
  v_retired    constant text := '581cb3fe';
  v_expected   constant integer := 8;
  v_removed    bigint;
  v_updated    bigint;
begin
  if (select mode from ops.rule_delivery_policy where singleton)
       is distinct from 'shadow' then
    raise exception '0381 REFUSED: activation preimage refresh requires shadow mode';
  end if;

  -- If 581cb3fe is somehow still a live rule, the retirement this migration is
  -- cleaning up after did not actually happen, and dropping its delivery
  -- target would silently un-deliver an active rule. Refuse instead.
  if exists (select 1 from public.rule r
              where left(r.id::text,8) = v_retired and r.status = 'active') then
    raise exception
      '0381 REFUSED: % is still an active rule; its activation target must not be removed',
      v_retired;
  end if;

  delete from ops.rule_delivery_activation_target where short_id = v_retired;
  get diagnostics v_removed = row_count;
  if v_removed = 0 then
    raise notice '0381: % already has no activation target row', v_retired;
  end if;

  update ops.rule_delivery_activation_target
     set map_digest = v_new_digest
   where map_digest <> v_new_digest;
  get diagnostics v_updated = row_count;
  if v_updated = 0 then
    raise notice '0381: activation targets already carry the current map digest';
  end if;

  if (select count(*) from ops.rule_delivery_activation_target) <> v_expected then
    raise exception
      '0381 FAILED: activation target set is %, expected the reviewed %',
      (select count(*) from ops.rule_delivery_activation_target), v_expected;
  end if;
end $$;

-- ── the receipt ledger's cardinality check counts eight too ───────────────
-- ops.rule_delivery_activation_receipt.target_short_ids is written by the
-- cutover function from the target table, so a table of eight and a constraint
-- demanding nine make every cutover fail at the very last statement -- which
-- is exactly where the acceptance run failed once the digest was refreshed.
--
-- The new constraint is NOT VALID on purpose. This is an append-only audit
-- ledger: a receipt written while the reviewed set genuinely was nine is a
-- true record of that era, and retroactively declaring it malformed would be
-- rewriting history to suit today's cardinality. NOT VALID enforces the new
-- size on every future write while leaving existing receipts undisturbed.
alter table ops.rule_delivery_activation_receipt
  drop constraint if exists rule_delivery_activation_receipt_target_short_ids_check;
alter table ops.rule_delivery_activation_receipt
  add constraint rule_delivery_activation_receipt_target_short_ids_check
  check (cardinality(target_short_ids) = 8) not valid;

-- ── the cutover function counts eight, not nine ───────────────────────────
-- Identical to migration 0317's function in every other respect. The reviewed
-- cardinality moves to a single declared constant so the next retirement
-- changes one line instead of six, and cannot half-change them.
create or replace function ops.set_rule_delivery_mode(
  p_mode text,
  p_changed_by text,
  p_reason text,
  p_expected_map_digest text)
returns table(mode text, changed_controls bigint, receipt_id uuid)
language plpgsql security definer
set search_path = pg_catalog, public, ops
as $$
declare
  v_targets constant integer := 8;
  v_from_mode text;
  v_expected_control text;
  v_expected_class text;
  v_next_control text;
  v_next_class text;
  v_count bigint;
  v_receipt uuid;
  v_health record;
begin
  if p_mode not in ('shadow','enforced') then
    raise exception 'unknown rule-delivery mode %',p_mode;
  end if;
  if coalesce(btrim(p_changed_by),'')='' or coalesce(btrim(p_reason),'')='' then
    raise exception 'changed_by and reason are required';
  end if;
  if (select count(*) from ops.rule_delivery_activation_target) <> v_targets then
    raise exception 'activation target set is not exactly %',v_targets;
  end if;
  if exists (select 1 from ops.rule_delivery_activation_target
              where map_digest<>p_expected_map_digest) then
    raise exception 'activation map digest preimage differs';
  end if;

  select p.mode into v_from_mode
    from ops.rule_delivery_policy p where p.singleton for update;
  if v_from_mode is null then raise exception 'delivery policy singleton is absent'; end if;
  if v_from_mode=p_mode then
    raise exception 'delivery policy already %; refusing a receipt-free no-op',p_mode;
  end if;
  if v_from_mode='shadow' and p_mode='enforced' then
    v_expected_control:='session_boot'; v_expected_class:='surfacing';
    v_next_control:='pack_delivery'; v_next_class:='stop_gate';
  elsif v_from_mode='enforced' and p_mode='shadow' then
    v_expected_control:='pack_delivery'; v_expected_class:='stop_gate';
    v_next_control:='session_boot'; v_next_class:='surfacing';
  else
    raise exception 'unsupported delivery transition % -> %',v_from_mode,p_mode;
  end if;

  select * into v_health from ops.rule_delivery_audit_counts(35);
  if v_health.total=0 or v_health.untagged<>0 or v_health.orphaned<>0
     or v_health.wildcarded<>0 or v_health.packless<>0 or v_health.emptypack<>0
     or v_health.scope_mismatch<>0 then
    raise exception 'delivery coverage is not activation-safe: %',row_to_json(v_health);
  end if;

  perform r.id from public.rule r
    join ops.rule_delivery_activation_target t on left(r.id::text,8)=t.short_id
   where r.status='active' for update of r;
  get diagnostics v_count=row_count;
  if v_count<>v_targets then
    raise exception 'active target preimage count is %, expected %',v_count,v_targets;
  end if;

  perform l.rule_id from ops.rule_load_layer l
    join ops.rule_delivery_activation_target t on t.short_id=l.short_id
   where l.scope=t.expected_scope and l.packs=array[t.expected_pack]
     and l.load_layer='pack' and l.map_digest=t.map_digest
   for update of l;
  get diagnostics v_count=row_count;
  if v_count<>v_targets then
    raise exception 'delivery target tag preimage count is %, expected %',v_count,v_targets;
  end if;

  perform a.rule_id from ops.rule_admission a
    join public.rule r on r.id=a.rule_id
    join ops.rule_delivery_activation_target t on left(r.id::text,8)=t.short_id
   where a.state='admitted'
     and a.reason='Backfilled from the reviewed active rule enforcement map'
     and a.enforcement_status='blocked'
     and not exists (select 1 from ops.rule_approval_receipt ar where ar.rule_id=r.id)
   for update of a;
  get diagnostics v_count=row_count;
  if v_count<>v_targets then
    raise exception 'admission target preimage count is %, expected %',v_count,v_targets;
  end if;

  select count(*) into v_count
    from ops.rule_enforcement_point ep
    join public.rule r on r.id=ep.rule_id
    join ops.rule_delivery_activation_target t on left(r.id::text,8)=t.short_id
   where ep.control_key=v_expected_control
     and ep.enforcement_class=v_expected_class and ep.installed;
  if v_count<>v_targets or (select count(*) from ops.rule_enforcement_point ep
      join public.rule r on r.id=ep.rule_id
      join ops.rule_delivery_activation_target t on left(r.id::text,8)=t.short_id)<>v_targets then
    raise exception 'enforcement-point preimage is not the exact % %/% rows',
      v_targets,v_expected_control,v_expected_class;
  end if;
  if p_mode='enforced' and not exists (
      select 1 from ops.enforcement_control_catalog c
       where c.control_key='pack_delivery' and c.enforcement_class='stop_gate'
         and c.installed and c.verified_at is not null) then
    raise exception 'pack_delivery is not an installed, verified stop gate';
  end if;

  delete from ops.rule_enforcement_point ep using public.rule r,
      ops.rule_delivery_activation_target t
   where ep.rule_id=r.id and left(r.id::text,8)=t.short_id;
  insert into ops.rule_enforcement_point
    (rule_id,control_key,implementation_ref,test_ref,enforcement_class,installed,verified_at)
  select r.id,
         case when p_mode='enforced' then t.to_control else t.from_control end,
         case when p_mode='enforced' then t.to_implementation_ref else t.from_implementation_ref end,
         case when p_mode='enforced' then t.to_test_ref else t.from_test_ref end,
         case when p_mode='enforced' then t.to_enforcement_class else t.from_enforcement_class end,
         true,now()
    from public.rule r join ops.rule_delivery_activation_target t
      on left(r.id::text,8)=t.short_id where r.status='active';

  update ops.rule_admission a
     set coverage_detail=jsonb_set(a.coverage_detail,'{rule_delivery}',
           jsonb_build_object('mode',p_mode,'control',v_next_control,
                              'changed_by',p_changed_by,'changed_at',now()),true),
         version=a.version+1,updated_at=now()
    from public.rule r join ops.rule_delivery_activation_target t
      on left(r.id::text,8)=t.short_id
   where a.rule_id=r.id;

  perform set_config('carr.rule_delivery_cutover','on',true);
  update ops.rule_delivery_policy
     set mode=p_mode,changed_by=p_changed_by,reason=p_reason,changed_at=now()
   where singleton;

  insert into ops.rule_delivery_activation_receipt
    (from_mode,to_mode,changed_by,reason,map_digest,target_short_ids)
  select v_from_mode,p_mode,p_changed_by,p_reason,p_expected_map_digest,
         array_agg(t.short_id order by t.short_id)
    from ops.rule_delivery_activation_target t returning id into v_receipt;
  return query select p_mode,v_targets::bigint,v_receipt;
end $$;

-- 0317 granted execute to carr_authority and revoked it from public. A
-- `create or replace` preserves the existing ACL, but restating it costs
-- nothing and makes a rebuilt store's privileges independent of that.
revoke all on function ops.set_rule_delivery_mode(text,text,text,text) from public;
grant execute on function ops.set_rule_delivery_mode(text,text,text,text) to carr_authority;

-- ── prove the end state rather than trusting the start state ──────────────
do $$
declare
  v_new_digest constant text :=
    'f7bf5726d329dd240434e51f7401fac9a977a3fb710636738f379f60f565f904';
  v_expected constant integer := 8;
  v_ids constant text[] := array[
    '25fcddee','3fa17fa0','72e06bdf','113b3833',
    '57d13061','c66dc739','49533583','557838a5'
  ];
begin
  if exists (select 1 from ops.rule_delivery_activation_target
              where map_digest <> v_new_digest) then
    raise exception '0381 FAILED: an activation target still carries a stale map digest';
  end if;

  if exists (select 1 from ops.rule_delivery_activation_target
              where short_id = '581cb3fe') then
    raise exception '0381 FAILED: the retired rule 581cb3fe still has an activation target';
  end if;

  if (select count(*) from ops.rule_delivery_activation_target) <> v_expected then
    raise exception '0381 FAILED: activation target set is not exactly %',v_expected;
  end if;

  -- The surviving set must be exactly the eight reviewed ids: a count alone
  -- would pass if a row had been swapped for an unreviewed one.
  if exists (select 1 from ops.rule_delivery_activation_target
              where short_id <> all(v_ids)) then
    raise exception '0381 FAILED: an unreviewed id is in the activation target set';
  end if;
  if (select count(*) from ops.rule_delivery_activation_target
       where short_id = any(v_ids)) <> v_expected then
    raise exception '0381 FAILED: a reviewed activation target id is missing';
  end if;

  -- The receipt ledger must accept a write of the new size, or the cutover
  -- fails at its final statement with the target table already correct.
  if not exists (
    select 1 from pg_constraint
     where conname = 'rule_delivery_activation_receipt_target_short_ids_check'
       and conrelid = 'ops.rule_delivery_activation_receipt'::regclass
       and pg_get_constraintdef(oid) like '%= 8)%'
  ) then
    raise exception
      '0381 FAILED: the activation receipt still demands a cardinality other than %',
      v_expected;
  end if;

  -- Nothing here may move delivery mode.
  if (select mode from ops.rule_delivery_policy where singleton)
       is distinct from 'shadow' then
    raise exception '0381 FAILED: delivery mode left shadow';
  end if;
end $$;

commit;
