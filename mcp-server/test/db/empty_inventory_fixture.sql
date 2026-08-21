-- empty_inventory_fixture.sql — the one state that isolates the empty-inventory
-- clause in tools/drive-retirement-verifier.py.
--
-- WHY A WHOLE FIXTURE FOR ONE CLAUSE. The verifier reports NOT READY for seven
-- separate reasons, and on an ordinary fresh database SIX of them are true at
-- once. Deleting the empty-inventory clause therefore changed nothing anybody
-- could observe: the run still exited 1, just for a different reason, and the
-- mutation survived a gate that checked only the exit code. That is precisely
-- the "clauses mask each other" shape the review found in the acceptance bar.
--
-- The state this builds is the one 0243 calls "the dangerous empty case":
-- everything else satisfied -- qualifying evidence, a proven receipt, an
-- authority acceptance, a bound inventory manifest -- and NOT ONE Drive
-- dependency ever recorded. A verifier without the empty-inventory clause reads
-- that as fully retired: a system that never looked, reporting that it finished.
--
-- WHAT IT DELIBERATELY DOES NOT DO: record a drive_dependency row. That absence
-- is the entire subject of the fixture.
--
-- Run as the database owner against a DISPOSABLE database only. It writes rows
-- the substrate makes permanently undeletable.
\set ON_ERROR_STOP on

do $$
declare
  probe_actor uuid;
  -- FIXED, NOT RANDOM. The acceptance below runs after `set role
  -- carr_authority`, and carr_authority holds no SELECT on
  -- ops.application_session -- so it cannot look this id up and must be handed
  -- one. (0242 grants that identity EXECUTE on accept_phase4 and nothing else,
  -- which is a tight grant and correct; it just means the caller resolves ids.)
  sid         uuid := '4e3d0000-0000-4000-8000-000000000f17';
  subj        uuid := gen_random_uuid();
  key1        text := 'empty-inventory-fixture-' || gen_random_uuid()::text;
  call_d      text;
  material_d  text;
  rid         uuid := gen_random_uuid();
begin
  if exists (select 1 from ops.drive_dependency) then
    raise exception 'empty_inventory_fixture: this database already has Drive '
                    'dependencies; the fixture''s whole subject is their absence';
  end if;

  select id into probe_actor from public.actor where kind = 'human' order by slug limit 1;
  if probe_actor is null then
    raise exception 'empty_inventory_fixture: need a human actor';
  end if;

  insert into ops.application_session
    (id, actor_id, organization_tenant_id, sponsoring_human_slug, via, auth_issuer,
     authorization_class, verified_subject, expires_at)
  values (sid, probe_actor, 'carr-internal', 'joe', 'fixture', 'fixture-issuer',
          'verified_partner', 'fixture', clock_timestamp() + interval '2 hours');

  insert into public.tool_call
    (idempotency_key, verb, actor_id, request_hash, response,
     organization_tenant_id, application_session_id)
  values (key1, 'log-activity', probe_actor, 'rh-empty', '{}'::jsonb, 'carr-internal', sid);

  -- The call must actually have written something about the subject: 0244(F)
  -- refuses a receipt whose call touched nothing.
  insert into public.event
    (occurred_at, actor_id, verb, subject_type, subject_id, field, new_value,
     cause, idempotency_key, organization_tenant_id, application_session_id)
  values (clock_timestamp(), probe_actor, 'log-activity', 'deal', subj, 'stage',
          '"fixture"'::jsonb, 'system', key1, 'carr-internal', sid);

  call_d     := ops.write_receipt_digest('log-activity', probe_actor, 'carr-internal',
                                         sid, 'rh-empty', 'deal', subj);
  material_d := ops.write_receipt_material_digest(key1, sid, 'deal', subj);

  insert into ops.write_receipt
    (id, application_session_id, actor_id, organization_tenant_id, verb,
     subject_type, subject_id, tool_call_idempotency_key,
     call_digest, material_digest, prior_digest)
  values (rid, sid, probe_actor, 'carr-internal', 'log-activity', 'deal', subj,
          key1, call_d, material_d, 'origin');
  if not ops.prove_write_receipt(rid) then
    raise exception 'empty_inventory_fixture: the receipt did not prove, so the '
                    'acceptance below would refuse for the wrong reason';
  end if;

  -- A MANIFEST FOR AN EMPTY INVENTORY. 0246 hashes an empty inventory as the
  -- sha256 of the empty string, so a manifest CAN bind here -- which is what
  -- makes this fixture sharp: inventory_bound is true, and the only thing wrong
  -- is that there is nothing in the inventory to retire.
  insert into ops.drive_inventory_manifest
    (id, inventory_digest, application_session_id, declared_by_actor_id,
     organization_tenant_id, note)
  values (gen_random_uuid(), ops.drive_dependency_digest(), sid, probe_actor,
          'carr-internal', 'empty-inventory fixture: nothing was found, and that is the claim');

  raise notice 'empty_inventory_fixture: session % ready for acceptance', sid;
end $$;

-- ACCEPTANCE RUNS IN ITS OWN TRANSACTION, in a separate statement, because 0242
-- refuses an acceptance that is not the first write in its transaction -- it may
-- not count evidence it authored. Everything above is a separate transaction by
-- the time this runs.
set role carr_authority;
select ops.accept_phase4(
  gen_random_uuid(),
  '4e3d0000-0000-4000-8000-000000000f17'::uuid,
  'empty-inventory fixture: everything satisfied except an inventory');
reset role;

-- AND THE FIXTURE MUST BE THE STATE IT CLAIMS TO BE. A fixture that quietly
-- failed to build would make the verifier check below pass for the wrong
-- reason, which is the defect this whole block exists to catch.
do $$
declare rdy record;
begin
  select * into rdy from ops.drive_retirement_readiness();
  if rdy.operational_total <> 0 then
    raise exception 'empty_inventory_fixture: expected NO operational dependencies, found %',
      rdy.operational_total;
  end if;
  if not rdy.has_authority then
    raise exception 'empty_inventory_fixture: the authority acceptance did not land';
  end if;
  if not rdy.inventory_bound then
    raise exception 'empty_inventory_fixture: the empty-inventory manifest did not bind, '
                    'so the empty-inventory clause is not the only thing left to refuse';
  end if;
  if rdy.remaining <> 0 then
    raise exception 'empty_inventory_fixture: remaining is %, not 0', rdy.remaining;
  end if;
  if rdy.ready then
    raise exception 'empty_inventory_fixture: readiness said YES over an empty inventory';
  end if;
  raise notice 'empty_inventory_fixture: built — everything satisfied except an inventory';
end $$;
