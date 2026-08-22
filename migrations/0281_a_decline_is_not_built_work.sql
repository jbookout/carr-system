-- 0281_a_decline_is_not_built_work.sql
--
-- I SHIPPED A CONTRACT THIS AFTERNOON THAT MADE DECLINES UNCLOSABLE. This is
-- that repair, and it was found the moment the owner ruled on twelve of them.
--
-- WHAT 0278 DID. It required, on the transition into confirmed_closed, an
-- evidence capsule carrying acceptance predicates, a change reference, an
-- explicit user-facing boolean, an exercised journey where that is true, and an
-- attestor who is not the implementer. That is the right price for work that was
-- BUILT, and it is the wrong price for work that was DECLINED, because a decline
-- has no change, no acceptance tests and no journey. There is nothing to point
-- at, and demanding a pointer to it refuses the close forever.
--
-- IT IS NOT A HYPOTHETICAL SHAPE. The capability program's own decline contract
-- says so in code: candidateEvidenceError() requires only a decision_ref for
-- kind 'declined', and explicitly does NOT require artifact_ref,
-- candidate_commit_sha or acceptance_test_refs — those three are demanded only
-- in the else branch, for work that produced something. So a legitimate,
-- fully-evidenced decline candidate carries exactly the fields 0278 refuses on.
--
-- HOW IT GOT PAST 0278'S OWN PROOF. Nine assertions, every one written about a
-- BUILT closure, and one that deliberately re-checked the capability lane's
-- shape — using a candidate carrying acceptance_test_refs and a commit sha. The
-- proof exercised the lane's build shape and never its decline shape, so it
-- passed while half the lane was broken. A test written from one branch of a
-- contract does not test the contract.
--
-- WHAT DONE MEANS FOR A DECLINE, which is the actual question. Not "what change
-- shipped" but "who decided, and who checked that they had". So the capsule for
-- a decline requires a DECISION REFERENCE and an attestor who is not the
-- implementer — the same independence requirement, unchanged, because a decline
-- somebody waved through on their own say-so is exactly as unaccountable as a
-- build they self-attested.
--
-- WHAT IS DELIBERATELY NOT RELAXED. The attestor test is identical for both
-- kinds, uuid-or-slug resolution included. A built closure still pays all five.
-- And the decline branch is keyed on completion_kind, which the work-request
-- table already constrains to a closed vocabulary, rather than on the capsule
-- being thin — otherwise "I have no change reference" would become the way to
-- buy a cheaper close.

begin;

-- The capsule reader gains the one field a decline actually turns on. Same
-- both-shapes reasoning as the rest: the capability lane writes decision_ref
-- inside its candidate object, a plain caller writes it at the top level, and
-- one definition reads either.
-- DROPPED AND RECREATED, not replaced. PostgreSQL refuses to change the OUT
-- parameter set of an existing function, and this adds one. The trigger below
-- calls it from a plpgsql body, which binds at run time rather than through a
-- recorded dependency, so both exist again before anything can call either.
drop function if exists ops.completion_capsule(jsonb);

create function ops.completion_capsule(evidence jsonb)
returns table (acceptance_predicates jsonb, change_ref text,
               user_facing boolean, user_journey_ref text, attestor text,
               decision_ref text)
language sql immutable as $$
  select
    coalesce(
      case when jsonb_typeof(evidence #> '{candidate,acceptance_test_refs}') = 'array'
           then evidence #> '{candidate,acceptance_test_refs}' end,
      case when jsonb_typeof(evidence -> 'acceptance_predicates') = 'array'
           then evidence -> 'acceptance_predicates' end),
    coalesce(evidence #>> '{candidate,candidate_commit_sha}',
             evidence ->> 'change_ref'),
    coalesce((evidence #>> '{candidate,user_facing}')::boolean,
             (evidence ->> 'user_facing')::boolean),
    coalesce(evidence #>> '{candidate,user_journey_ref}',
             evidence ->> 'user_journey_ref'),
    coalesce(evidence #>> '{attestation,verifier_actor_id}',
             evidence ->> 'attested_by'),
    coalesce(evidence #>> '{candidate,decision_ref}',
             evidence ->> 'decision_ref')
$$;

comment on function ops.completion_capsule(jsonb) is
  'The tune-up council 2026-08-21 completion capsule, read from either the '
  'capability lane shape (candidate/attestation) or a plain one. ONE definition '
  'so the closing trigger and any reader cannot disagree about what done means. '
  '0281 added decision_ref, which is what a DECLINED closure turns on instead of '
  'a change reference.';

create or replace function ops.enforce_completion_capsule()
returns trigger language plpgsql as $$
declare
  cap         record;
  implementer text;
  n_predicates int;
  declining   boolean;
begin
  if new.state <> 'confirmed_closed' then
    return new;
  end if;
  if tg_op = 'UPDATE' and old.state = 'confirmed_closed' then
    return new;
  end if;

  if new.completion_evidence is null
     or jsonb_typeof(new.completion_evidence) <> 'object' then
    raise exception
      'closing %: done needs an evidence capsule, and completion_evidence is not an object.',
      new.ref using errcode = 'check_violation';
  end if;

  select * into cap from ops.completion_capsule(new.completion_evidence);

  -- KEYED ON THE DECLARED KIND, never on the capsule looking thin. If a missing
  -- change reference were itself the signal, "I have no change reference" would
  -- become the way to buy the cheaper close.
  declining := (new.completion_kind = 'declined');

  if declining then
    -- A DECLINE HAS NO CHANGE, NO ACCEPTANCE TESTS AND NO JOURNEY. What it has
    -- is a decision, and someone other than the implementer who checked it.
    if coalesce(btrim(cap.decision_ref),'') = '' then
      raise exception
        'closing %: a decline needs the DECISION that made it. Nothing else here is '
        'evidence — there is no change to point at and no test that passed.',
        new.ref using errcode = 'check_violation';
    end if;
  else
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
  end if;

  -- IDENTICAL FOR BOTH KINDS, and that is the point. A decline waved through on
  -- the implementer's own say-so is exactly as unaccountable as a self-attested
  -- build. The uuid-or-slug resolution stays too: every capability-lane
  -- attestation records a uuid, so a slug-only comparison would pass every
  -- self-attestation ever made.
  if coalesce(btrim(cap.attestor),'') = '' then
    raise exception
      'closing %: no attestor. Done requires somebody who did not build it saying it is done.',
      new.ref using errcode = 'check_violation';
  end if;

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
  'timestamp alone. Fires on the TRANSITION into confirmed_closed. A BUILT closure '
  'pays acceptance predicates, a change reference, an explicit user-facing boolean '
  'and a journey where that is true; a DECLINED closure pays the decision that made '
  'it, because there is no change and no test (0281). The independent-attestor test '
  'is identical for both.';

-- PROOF. The first assertion FAILS against 0278's function and passes against
-- this one, which is what makes this a repair rather than a restatement. The
-- rest re-assert what 0278 got right, so fixing the decline branch cannot
-- quietly buy a cheaper close for built work.
do $$
declare
  wr      uuid;
  refused boolean;
  shaper  uuid;
  decline_capsule jsonb;
  built_capsule   jsonb;
begin
  select id into shaper from actor where slug='joe' limit 1;
  if shaper is null then
    raise exception '0281 proof needs a joe actor row to attribute the shape decision to';
  end if;

  -- Exactly what the capability lane's decline contract produces: a decision
  -- reference and an attestation, and nothing else.
  decline_capsule := jsonb_build_object(
    'candidate',   jsonb_build_object('decision_ref','8cfeff4b-a4bb-410b-b0be-87495b2726ac'),
    'attestation', jsonb_build_object('verifier_actor_id', shaper::text));

  built_capsule := jsonb_build_object(
    'acceptance_predicates', jsonb_build_array('the probe closes'),
    'change_ref', 'probe:0281', 'user_facing', false, 'attested_by', 'probe-attestor');

  insert into ops.work_request (ref, state, title, requester_actor, executor_actor,
                              verification_accepted_at, verification_evidence_ref,
                              shape_disposition, shape_fixed_surface_ref, shape_rationale,
                              shape_decided_by_actor_id, shape_decided_at)
  values ('WR-999941','captured','decline capsule probe','joe','probe-builder',
          now(),'probe:0281-evidence','not_required','probe:decline-capsule-fixture',
          'rolled-back proof fixture for the decline capsule', shaper, now())
  returning id into wr;

  -- 1. THE REGRESSION. A properly evidenced decline must CLOSE. Under 0278 this
  --    raised "the evidence capsule states no acceptance predicates".
  update ops.work_request
     set state='confirmed_closed', closed_at=now(), completion_kind='declined',
         completion_evidence = decline_capsule
   where id=wr;
  if (select state from ops.work_request where id=wr) <> 'confirmed_closed' then
    raise exception '0281 FAILED: a fully evidenced decline did not close';
  end if;

  -- 2. A DECLINE WITH NO DECISION IS STILL REFUSED. The cheaper price is not no
  --    price.
  insert into ops.work_request (ref, state, title, requester_actor, executor_actor,
                              verification_accepted_at, verification_evidence_ref,
                              shape_disposition, shape_fixed_surface_ref, shape_rationale,
                              shape_decided_by_actor_id, shape_decided_at)
  values ('WR-999942','captured','decline probe b','joe','probe-builder',
          now(),'probe:0281-evidence','not_required','probe:decline-capsule-fixture',
          'rolled-back proof fixture', shaper, now())
  returning id into wr;
  begin
    update ops.work_request
       set state='confirmed_closed', closed_at=now(), completion_kind='declined',
           completion_evidence = decline_capsule #- '{candidate,decision_ref}'
     where id=wr;
    refused := false;
  exception when check_violation then refused := true;
  end;
  if not refused then
    raise exception '0281 FAILED: a decline naming no decision was accepted';
  end if;

  -- 3. A DECLINE SELF-ATTESTED IS STILL REFUSED. The independence test does not
  --    get cheaper along with the evidence.
  update ops.work_request set executor_actor='joe' where id=wr;
  begin
    update ops.work_request
       set state='confirmed_closed', closed_at=now(), completion_kind='declined',
           completion_evidence = decline_capsule
     where id=wr;
    refused := false;
  exception when check_violation then refused := true;
  end;
  if not refused then
    raise exception '0281 FAILED: a self-attested decline was accepted';
  end if;
  update ops.work_request set executor_actor='probe-builder' where id=wr;

  -- 4. A BUILT CLOSURE STILL PAYS THE FULL PRICE, and this assertion is written
  --    to tell REFUSED FOR THE RIGHT REASON apart from merely refused. The
  --    careless fix is branching on the capsule looking thin rather than on the
  --    declared kind. A built capsule stripped of its change reference and ALSO
  --    carrying a decision reference is the case that separates them: under the
  --    correct rule it is still a built closure and is refused for the missing
  --    change reference; under the thin-capsule rule it would be mistaken for a
  --    decline, find the decision it carries, and be ACCEPTED. The first version
  --    of this assertion only removed the change reference, which is refused
  --    either way — and it survived its own mutation, which is how this was found.
  begin
    update ops.work_request
       set state='confirmed_closed', closed_at=now(), completion_kind='built',
           completion_evidence = (built_capsule - 'change_ref')
                                 || jsonb_build_object('decision_ref','8cfeff4b-a4bb-410b-b0be-87495b2726ac')
     where id=wr;
    refused := false;
  exception when check_violation then refused := true;
  end;
  if not refused then
    raise exception '0281 FAILED: a built closure bought the decline price by omitting its change reference and naming a decision instead';
  end if;

  -- 5. AND A COMPLETE BUILT CAPSULE STILL CLOSES.
  update ops.work_request
     set state='confirmed_closed', closed_at=now(), completion_kind='built',
         completion_evidence = built_capsule
   where id=wr;
  if (select state from ops.work_request where id=wr) <> 'confirmed_closed' then
    raise exception '0281 FAILED: a complete built capsule did not close the row';
  end if;

  raise exception 'CARR_0281_PROOF_OK';
exception when others then
  if sqlerrm <> 'CARR_0281_PROOF_OK' then raise; end if;
end $$;

commit;
