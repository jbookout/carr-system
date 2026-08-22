-- 0278_completion_needs_a_capsule_and_a_second_pair_of_eyes.sql
--
-- The tune-up council named the binding dysfunction as work entering cheaply and
-- never formally leaving, and it was specific about what leaving costs: "done =
-- evidence capsule + independent non-implementing attestor + owner sign-off".
-- This is the first two thirds of that, in the database.
--
-- WHAT A CLOSE COSTS TODAY, which is the problem. The only thing standing
-- between an ordinary Work Request and confirmed_closed is
-- confirmed_close_needs_accepted_verification, which requires
-- verification_accepted_at to be non-null. A timestamp. Nothing requires an
-- evidence reference, nothing requires the acceptance predicates to have been
-- stated, and nothing whatsoever requires a second pair of eyes — the row's own
-- implementer can set that timestamp and the work has formally left.
--
-- THE CAPABILITY LANE ALREADY PAYS THIS PRICE, and that is why this migration
-- reads the shape it does rather than inventing a new one. A capability project
-- closes through ops.capability_verification, whose insert trigger already
-- rejects self-review and a stale candidate, and whose capsule carries
-- acceptance_test_refs, a candidate_commit_sha and the verifier's identity. The
-- one row closed so far carries exactly that. Forcing that lane to restate the
-- same three facts under new key names would be the second-transcription defect
-- this repository has now paid for twice (decision 6a38ac5d). So the contract is
-- defined ONCE, in ops.completion_capsule(), which normalises either shape and
-- is the single definition both the trigger and any future reader use.
--
-- THE FIVE THINGS A CAPSULE MUST CARRY:
--   acceptance predicates  what would make it done, stated before it was done
--   change reference       the commit or change this closure points at
--   user_facing            an EXPLICIT boolean, never absent
--   user journey           required when and only when user_facing is true
--   attestor               who checked it, and NOT the person who built it
--
-- WHY user_facing IS MANDATORY RATHER THAN DEFAULTED. The council's wording is
-- "an exercised user journey WHERE USER-FACING". A boolean column defaulting to
-- false would make that clause inert on day one: everything would quietly be
-- not-user-facing and the journey would never be asked for. Requiring the capsule
-- to say yes or no means nobody can be silent about it, which is the difference
-- between a rule and a recitation (rule ab814a26). This system has already been
-- caught once reporting a capability live before a human had used it; that is the
-- failure this clause exists to prevent, and a default would reinstate it.
--
-- ON THE TRANSITION, not on the row. The trigger fires only when a row ENTERS
-- confirmed_closed. A CHECK constraint would be validated against the row already
-- closed, which predates this contract and would have to be either backfilled
-- with judgements this migration is not entitled to make, or exempted with a NOT
-- VALID clause that quietly weakens the constraint for everyone. Firing on the
-- transition needs neither: the existing closure stands as it was recorded, and
-- every closure from here pays the price. Same reasoning, and the same shape, as
-- the work-in-progress limit in 0276 and 0277.
--
-- WHAT THIS DELIBERATELY DOES NOT DO. It does not implement the owner's
-- twice-weekly batch sign-off, and it does not auto-close an attestor-green item
-- after 48 hours. Both need a scheduled job rather than a constraint, they belong
-- with the job-ledger work rather than beside it, and half-building them here
-- would leave a timer nothing runs — which is the "detector with no caller" shape
-- this repository keeps finding (loop 498). They are named in loop 504 and stay
-- there until something can actually run them.

begin;

-- ONE DEFINITION OF WHAT A CAPSULE IS. Both shapes in, one answer out. A second
-- copy of this reasoning in a trigger body would be free to drift from whatever
-- a future reader consults, which is exactly how the control catalog reached a
-- 56-row disagreement with its own repository.
create or replace function ops.completion_capsule(evidence jsonb)
returns table (acceptance_predicates jsonb, change_ref text,
               user_facing boolean, user_journey_ref text, attestor text)
language sql immutable as $$
  select
    -- The council's "acceptance predicates". The capability lane states them as
    -- the acceptance tests it actually ran; an ordinary row states them directly.
    coalesce(
      case when jsonb_typeof(evidence #> '{candidate,acceptance_test_refs}') = 'array'
           then evidence #> '{candidate,acceptance_test_refs}' end,
      case when jsonb_typeof(evidence -> 'acceptance_predicates') = 'array'
           then evidence -> 'acceptance_predicates' end),
    coalesce(evidence #>> '{candidate,candidate_commit_sha}',
             evidence ->> 'change_ref'),
    -- Null when absent, and null is refused below. It is NOT coalesced to false:
    -- "nobody said" and "somebody said no" are different states and only one of
    -- them is an answer.
    coalesce((evidence #>> '{candidate,user_facing}')::boolean,
             (evidence ->> 'user_facing')::boolean),
    coalesce(evidence #>> '{candidate,user_journey_ref}',
             evidence ->> 'user_journey_ref'),
    coalesce(evidence #>> '{attestation,verifier_actor_id}',
             evidence ->> 'attested_by')
$$;

comment on function ops.completion_capsule(jsonb) is
  'The tune-up council 2026-08-21 completion capsule, read from either the '
  'capability lane shape (candidate/attestation) or a plain one. ONE definition '
  'so the closing trigger and any reader cannot disagree about what done means.';

create or replace function ops.enforce_completion_capsule()
returns trigger language plpgsql as $$
declare
  cap        record;
  implementer text;
  n_predicates int;
begin
  if new.state <> 'confirmed_closed' then
    return new;
  end if;
  if tg_op = 'UPDATE' and old.state = 'confirmed_closed' then
    return new;   -- already closed under whatever contract applied then
  end if;

  if new.completion_evidence is null
     or jsonb_typeof(new.completion_evidence) <> 'object' then
    raise exception
      'closing %: done needs an evidence capsule, and completion_evidence is not an object. '
      'The council''s price for leaving the queue is acceptance predicates, a change reference, '
      'whether it is user-facing, and who checked it.', new.ref
      using errcode = 'check_violation';
  end if;

  select * into cap from ops.completion_capsule(new.completion_evidence);

  select count(*) into n_predicates
    from jsonb_array_elements_text(coalesce(cap.acceptance_predicates,'[]'::jsonb)) p
   where btrim(p) <> '';
  if n_predicates = 0 then
    raise exception
      'closing %: the evidence capsule states no acceptance predicates. What would have '
      'made this done had to be sayable before it was done.', new.ref
      using errcode = 'check_violation';
  end if;

  if coalesce(btrim(cap.change_ref),'') = '' then
    raise exception
      'closing %: the evidence capsule names no change reference, so nothing ties this '
      'closure to what actually shipped.', new.ref
      using errcode = 'check_violation';
  end if;

  if cap.user_facing is null then
    raise exception
      'closing %: the evidence capsule does not say whether this is user-facing. Say so '
      'explicitly — silence here is how a capability gets reported live before a human '
      'has used it.', new.ref
      using errcode = 'check_violation';
  end if;

  if cap.user_facing and coalesce(btrim(cap.user_journey_ref),'') = '' then
    raise exception
      'closing %: this is declared user-facing, so the capsule must name an EXERCISED user '
      'journey. A user-facing thing nobody has used is not done.', new.ref
      using errcode = 'check_violation';
  end if;

  if coalesce(btrim(cap.attestor),'') = '' then
    raise exception
      'closing %: no attestor. Done requires somebody who did not build it saying it is done.',
      new.ref using errcode = 'check_violation';
  end if;

  -- THE ATTESTOR IS NOT THE IMPLEMENTER. Compared against the same
  -- executor-then-owner fallback the work-in-progress limit uses, so the two
  -- controls cannot disagree about who is responsible for a row. The attestor may
  -- be recorded as an actor uuid (the capability lane) or a slug, so both are
  -- resolved to a slug before comparing — otherwise a uuid would never equal a
  -- slug and this check would pass for every self-attestation ever made.
  implementer := coalesce(new.executor_actor, new.owner_actor);
  if implementer is not null then
    if lower(btrim(cap.attestor)) = lower(btrim(implementer))
       or exists (select 1 from actor a
                   where a.id::text = cap.attestor
                     and lower(a.slug) = lower(btrim(implementer))) then
      raise exception
        'closing %: % attested their own work. The whole point of an independent attestor '
        'is that it is somebody else.', new.ref, implementer
        using errcode = 'check_violation';
    end if;
  end if;

  return new;
end $$;

comment on function ops.enforce_completion_capsule() is
  'Tune-up council 2026-08-21: a Work Request cannot reach confirmed_closed on a '
  'timestamp alone. Fires on the TRANSITION into confirmed_closed, so the one row '
  'closed before this contract existed stands as recorded. The owner batch sign-off '
  'and the 48-hour attestor-green auto-close are NOT here: they need a scheduled '
  'job, not a constraint, and are tracked in loop 504.';

drop trigger if exists completion_capsule on ops.work_request;
create trigger completion_capsule
  before insert or update on ops.work_request
  for each row execute function ops.enforce_completion_capsule();

-- PROOF, inside the transaction, in the style 0276 established. Fixtures enter at
-- 'captured' and transition, because the shape gate refuses a row landing straight
-- in implementation. The sentinel exception rolls everything back.
--
-- Each refusal below is a way a close used to succeed. The last two cases are the
-- ones that make this a proof rather than a restatement: a complete capsule must
-- still CLOSE, and the row already closed must remain untouched.
do $$
declare
  wr      uuid;
  refused boolean;
  shaper  uuid;
  full_capsule jsonb;
begin
  select id into shaper from actor where slug='joe' limit 1;
  if shaper is null then
    raise exception '0278 proof needs a joe actor row to attribute the shape decision to';
  end if;

  full_capsule := jsonb_build_object(
    'acceptance_predicates', jsonb_build_array('the probe closes'),
    'change_ref', 'probe:0278',
    'user_facing', false,
    'attested_by', 'probe-attestor');

  insert into ops.work_request (ref, state, title, requester_actor, executor_actor,
                              verification_accepted_at, verification_evidence_ref,
                              shape_disposition, shape_fixed_surface_ref, shape_rationale,
                              shape_decided_by_actor_id, shape_decided_at)
  values ('WR-999921','captured','capsule probe','joe','probe-builder',
          now(),'probe:0278-evidence',
          'not_required','probe:completion-capsule-fixture',
          'rolled-back proof fixture for the completion capsule',
          shaper, now())
  returning id into wr;

  -- 1. A TIMESTAMP ALONE. This is exactly what used to be enough.
  begin
    update ops.work_request set state='confirmed_closed', closed_at=now() where id=wr;
    refused := false;
  exception when check_violation then refused := true;
  end;
  if not refused then
    raise exception '0278 FAILED: a close with no evidence capsule was accepted';
  end if;

  -- 2. NO ACCEPTANCE PREDICATES.
  begin
    update ops.work_request
       set state='confirmed_closed', closed_at=now(),
           completion_evidence = full_capsule - 'acceptance_predicates'
     where id=wr;
    refused := false;
  exception when check_violation then refused := true;
  end;
  if not refused then
    raise exception '0278 FAILED: a close stating no acceptance predicates was accepted';
  end if;

  -- 3. NO CHANGE REFERENCE.
  begin
    update ops.work_request
       set state='confirmed_closed', closed_at=now(),
           completion_evidence = full_capsule - 'change_ref'
     where id=wr;
    refused := false;
  exception when check_violation then refused := true;
  end;
  if not refused then
    raise exception '0278 FAILED: a close naming no change reference was accepted';
  end if;

  -- 4. SILENT ABOUT USER-FACING. Absent must not read as false.
  begin
    update ops.work_request
       set state='confirmed_closed', closed_at=now(),
           completion_evidence = full_capsule - 'user_facing'
     where id=wr;
    refused := false;
  exception when check_violation then refused := true;
  end;
  if not refused then
    raise exception '0278 FAILED: a close silent about whether it is user-facing was accepted';
  end if;

  -- 5. USER-FACING WITH NO EXERCISED JOURNEY.
  begin
    update ops.work_request
       set state='confirmed_closed', closed_at=now(),
           completion_evidence = jsonb_set(full_capsule,'{user_facing}','true'::jsonb)
     where id=wr;
    refused := false;
  exception when check_violation then refused := true;
  end;
  if not refused then
    raise exception '0278 FAILED: a user-facing close with no exercised journey was accepted';
  end if;

  -- 6. SELF-ATTESTATION, by slug.
  begin
    update ops.work_request
       set state='confirmed_closed', closed_at=now(),
           completion_evidence = jsonb_set(full_capsule,'{attested_by}','"probe-builder"'::jsonb)
     where id=wr;
    refused := false;
  exception when check_violation then refused := true;
  end;
  if not refused then
    raise exception '0278 FAILED: an implementer attested their own work and it was accepted';
  end if;

  -- 7. SELF-ATTESTATION, by actor uuid rather than slug. A comparison that only
  --    handled slugs would pass this, and every capability-lane attestation
  --    records a uuid — so this is the case that matters most.
  update ops.work_request set executor_actor='joe' where id=wr;
  begin
    update ops.work_request
       set state='confirmed_closed', closed_at=now(),
           completion_evidence = jsonb_set(full_capsule,'{attested_by}',
                                           to_jsonb(shaper::text))
     where id=wr;
    refused := false;
  exception when check_violation then refused := true;
  end;
  if not refused then
    raise exception '0278 FAILED: a self-attestation recorded as an actor uuid was accepted';
  end if;
  update ops.work_request set executor_actor='probe-builder' where id=wr;

  -- 8. A COMPLETE CAPSULE MUST STILL CLOSE. A gate that refuses everything is
  --    not a gate, and this is the assertion a careless tightening breaks.
  update ops.work_request
     set state='confirmed_closed', closed_at=now(), completion_evidence = full_capsule
   where id=wr;
  if (select state from ops.work_request where id=wr) <> 'confirmed_closed' then
    raise exception '0278 FAILED: a complete capsule did not close the row';
  end if;

  -- 9. THE CAPABILITY LANE'S OWN SHAPE MUST PASS, read through the same
  --    normaliser. If this fails, closing a capability project just broke.
  if (select count(*) from ops.completion_capsule(jsonb_build_object(
        'candidate', jsonb_build_object(
          'acceptance_test_refs', jsonb_build_array('ops/x-selftest.py'),
          'candidate_commit_sha', '66efcc450d524eb264a6b4676365f96aa4424044',
          'user_facing', false),
        'attestation', jsonb_build_object('verifier_actor_id', shaper::text)))
       where acceptance_predicates is not null and change_ref is not null
         and user_facing is not null and attestor is not null) <> 1 then
    raise exception '0278 FAILED: the capability lane capsule shape does not read through the normaliser';
  end if;

  raise exception 'CARR_0278_PROOF_OK';
exception when others then
  if sqlerrm <> 'CARR_0278_PROOF_OK' then raise; end if;
end $$;

commit;
