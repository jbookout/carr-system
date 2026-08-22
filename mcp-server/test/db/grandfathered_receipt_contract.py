#!/usr/bin/env python3
"""grandfathered_receipt_contract.py — what 0270's backfill did to receipts that
already existed, proven against a database where some actually did.

WHY THIS FILE EXISTS, AND WHY IT COULD NOT LIVE IN THE MIGRATION. 0270 adds
ops.write_receipt.material_digest and backfills it:

    alter table ops.write_receipt disable trigger write_receipt_immutable;
    update ops.write_receipt set material_digest = call_digest where material_digest is null;
    alter table ops.write_receipt enable always trigger write_receipt_immutable;

Three statements that stand the immutability guard down and rewrite every
pre-existing row, one way, with no reverse. 0270's own apply-time proof cannot
see any of it: that block runs inside a transaction it deliberately rolls back,
so every receipt it can observe is one IT created AFTER the backfill already
ran. The backfill's actual subject -- a row that existed BEFORE the migration --
is unreachable from inside the migration by construction.

ops/check-application-session.sh therefore builds a second database: a template
copy taken after 0269 and before 0270, seeded with receipts under the PRE-SPLIT
rules, and only then brought forward. That is the only place in this repository
where a grandfathered row exists, and this is what it asserts about one.

WHAT THE BACKFILL LEAVES BEHIND, stated plainly because it is a live exposure
and not a defect this file is asking anyone to fix:

  A GRANDFATHERED ROW CARRIES A VALUE THE CURRENT RULES WOULD REFUSE. After
  0270(F), an ordinary receipt must carry the material its call actually wrote,
  recomputed by ops.write_receipt_material_digest. A backfilled row carries its
  own CALL digest instead -- a digest of (verb, actor, tenant, session,
  request_hash, subject) -- which that recipe can never produce. Insert the same
  row today and the database refuses it.

  IT STILL COUNTS. ops.accept_phase4 counts ops.write_receipt globally, with no
  clause excluding backfilled rows, so a grandfathered receipt contributes to
  proven_receipts exactly like one written under the new rules. A grandfathered
  UNPROVEN one blocks the bar exactly like a new one.

  AND IT IS STILL LOAD-BEARING. ops.require_prior_state_existed accepts any
  proven, unretracted material on the subject as a prior, so a new receipt can
  legitimately build on a grandfathered state.

  THERE IS NO DOWN PATH. migrations/README.md is forward-only -- there are no
  down files in this series and tools/migrate.py refuses any applied file whose
  content changed -- so this is not a gap someone forgot to fill, it is the
  repository's model. What it means concretely: after 0270 there is no statement
  that restores the pre-split shape, and no record anywhere of which rows the
  UPDATE touched. Only its SIGNATURE survives, and that signature is exactly the
  one thing a new row can never have: material_digest = call_digest. Asserted
  below, because a fact that identifies grandfathered rows forever is worth
  more than a comment claiming they are identifiable.

Usage:
  grandfathered_receipt_contract.py seed   <dsn>   # BEFORE 0270 is applied
  grandfathered_receipt_contract.py verify <dsn>   # AFTER
"""
import sys
import uuid
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent))
# REUSED, NOT REIMPLEMENTED. This suite writes rows the substrate makes
# permanently undeletable; pointed anywhere real it would leave junk forever.
from application_session_contract import refuse_non_disposable  # noqa: E402

TENANT = "carr-internal"
PASSES, FAILURES = [], []


def check(name, fn):
    try:
        fn()
        PASSES.append(name)
        print(f"  pass  {name}")
    except AssertionError as exc:
        FAILURES.append((name, str(exc)))
        print(f"  FAIL  {name}\n          {exc}")
    except Exception as exc:  # noqa: BLE001
        FAILURES.append((name, f"{type(exc).__name__}: {exc}"))
        print(f"  ERROR {name}\n          {type(exc).__name__}: {exc}")


# ─────────────────────────────────────────────────────────────────── seed ────

def seed(dsn):
    """Write receipts under the PRE-SPLIT rules, as 0267 left them.

    Deliberately NOT written through any helper that knows about 0270: the
    whole point is a row shaped the way rows were shaped before it. At this
    point ops.write_receipt still has claimed_digest (not call_digest), has no
    material_digest, and carries neither the says-what-its-call-wrote guard nor
    the prior-state guard, both of which 0270 adds. A row like this is
    unreproducible five statements later, which is why it is made here.
    """
    refuse_non_disposable(dsn)
    conn = psycopg.connect(dsn)
    with conn.cursor() as cur:
        cur.execute("select id from actor where kind='human' order by slug limit 1")
        actor = cur.fetchone()[0]

        sid = uuid.uuid4()
        cur.execute("""insert into ops.application_session
                         (id, actor_id, organization_tenant_id, sponsoring_human_slug,
                          via, auth_issuer, authorization_class, verified_subject, expires_at)
                       values (%s,%s,%s,'joe','grandfathered','probe-issuer',
                               'verified_partner','probe', clock_timestamp() + interval '2 hours')""",
                    (sid, actor, TENANT))

        subject = uuid.uuid4()
        made = []
        prior = "origin"
        for n in (1, 2):
            key = f"grandfathered-{n}-{uuid.uuid4()}"
            cur.execute("""insert into tool_call
                             (idempotency_key, verb, actor_id, request_hash, response,
                              organization_tenant_id, application_session_id)
                           values (%s,'log-activity',%s,%s,'{}'::jsonb,%s,%s)""",
                        (key, actor, f"rh-{n}", TENANT, sid))
            cur.execute("""insert into event
                             (occurred_at, actor_id, verb, subject_type, subject_id, field,
                              new_value, cause, idempotency_key, organization_tenant_id,
                              application_session_id)
                           values (clock_timestamp(),%s,'log-activity','deal',%s,'stage',
                                   %s::jsonb,'system',%s,%s,%s)""",
                        (actor, subject, f'"stage-{n}"', key, TENANT, sid))
            cur.execute("select ops.write_receipt_digest(%s,%s,%s,%s,%s)",
                        ("log-activity", actor, TENANT, sid, f"rh-{n}"))
            claimed = cur.fetchone()[0]
            rid = uuid.uuid4()
            cur.execute("""insert into ops.write_receipt
                             (id, application_session_id, actor_id, organization_tenant_id,
                              verb, subject_type, subject_id, tool_call_idempotency_key,
                              claimed_digest, prior_digest)
                           values (%s,%s,%s,%s,'log-activity','deal',%s,%s,%s,%s)""",
                        (rid, sid, actor, TENANT, subject, key, claimed, prior))
            cur.execute("select ops.prove_write_receipt(%s)", (rid,))
            assert cur.fetchone()[0] is True, (
                f"seed receipt {n} failed to prove under the PRE-split readback; the "
                f"grandfathered fixture would be testing an unproven row instead")
            made.append(str(rid))
            prior = claimed
    conn.commit()
    conn.close()
    print(f"seeded {len(made)} pre-0270 receipts on subject {subject}: {', '.join(made)}")
    return 0


# ─────────────────────────────────────────────────────────────── verify ────

def verify(dsn):
    refuse_non_disposable(dsn)
    print("grandfathered receipts — what 0270's backfill did to rows that already existed")
    print(f"target: {dsn}\n")
    conn = psycopg.connect(dsn)

    def grandfathered_ids():
        with conn.cursor() as cur:
            cur.execute("""select id from ops.write_receipt
                            where material_digest = call_digest order by seq""")
            return [r[0] for r in cur.fetchall()]

    def the_backfill_ran():
        """The literal effect. If 0270's UPDATE were deleted, material_digest
        would be NULL for these rows and the `set not null` two statements later
        would fail the migration -- but only on a database that HAS pre-existing
        rows, which is exactly the database nothing else builds."""
        with conn.cursor() as cur:
            cur.execute("""select count(*), count(material_digest),
                                  count(*) filter (where material_digest = call_digest)
                             from ops.write_receipt
                            where verb='log-activity' and tool_call_idempotency_key
                                  like 'grandfathered-%'""")
            total, filled, matching = cur.fetchone()
        assert total == 2, f"expected the 2 seeded pre-0270 receipts, found {total}"
        assert filled == 2, f"{total - filled} grandfathered rows have a NULL material digest"
        assert matching == 2, (
            f"only {matching} of {total} grandfathered rows carry material_digest = "
            f"call_digest; the backfill did not set the value it says it sets")
    check("the backfill ran: every pre-existing receipt carries its call digest as "
          "its material digest", the_backfill_ran)

    def immutability_was_restored_to_enable_always():
        """THE SHARPEST MUTANT IN THE WHOLE BACKFILL, and the one 0270's own
        comment warns about: restoring the trigger with a plain ENABLE instead
        of ENABLE ALWAYS silently downgrades it to origin-only, so it stops
        firing for replication and for any session_replication_role=replica
        path. tgenabled is 'A' for ALWAYS and 'O' for origin-only, and the two
        are indistinguishable from every functional test in this repository --
        both fire for an ordinary local UPDATE."""
        with conn.cursor() as cur:
            cur.execute("""select tgname, tgenabled from pg_trigger
                            where tgrelid = 'ops.write_receipt'::regclass
                              and not tgisinternal order by tgname""")
            triggers = dict(cur.fetchall())
        assert "write_receipt_immutable" in triggers, (
            "the immutability trigger is missing after the backfill stood it down")
        assert triggers["write_receipt_immutable"] == "A", (
            f"write_receipt_immutable came back as {triggers['write_receipt_immutable']!r}, "
            f"not 'A' (ENABLE ALWAYS) — the backfill left it downgraded to origin-only")
        downgraded = {n: e for n, e in triggers.items() if e != "A"}
        assert not downgraded, (
            f"triggers on ops.write_receipt are not ENABLE ALWAYS: {downgraded}")
    check("the backfill restored write_receipt_immutable to ENABLE ALWAYS, not a "
          "plain ENABLE", immutability_was_restored_to_enable_always)

    def a_grandfathered_value_would_be_refused_today():
        """THE CONSEQUENCE, made concrete. 0270(F) requires an ordinary receipt
        to carry the material its call actually wrote. A backfilled row carries
        its CALL digest, which that recipe cannot produce. So these rows hold a
        value the database would now refuse -- proven here by trying it."""
        with conn.cursor() as cur:
            cur.execute("select id from actor where kind='human' order by slug limit 1")
            actor = cur.fetchone()[0]
            sid = uuid.uuid4()
            cur.execute("""insert into ops.application_session
                             (id, actor_id, organization_tenant_id, sponsoring_human_slug,
                              via, auth_issuer, authorization_class, verified_subject, expires_at)
                           values (%s,%s,%s,'joe','probe','probe-issuer','verified_partner',
                                   'probe', clock_timestamp() + interval '1 hour')""",
                        (sid, actor, TENANT))
            key = f"today-{uuid.uuid4()}"
            subject = uuid.uuid4()
            cur.execute("""insert into tool_call
                             (idempotency_key, verb, actor_id, request_hash, response,
                              organization_tenant_id, application_session_id)
                           values (%s,'log-activity',%s,'rh-today','{}'::jsonb,%s,%s)""",
                        (key, actor, TENANT, sid))
            cur.execute("""insert into event
                             (occurred_at, actor_id, verb, subject_type, subject_id, field,
                              new_value, cause, idempotency_key, organization_tenant_id,
                              application_session_id)
                           values (clock_timestamp(),%s,'log-activity','deal',%s,'stage',
                                   '"x"'::jsonb,'system',%s,%s,%s)""",
                        (actor, subject, key, TENANT, sid))
            cur.execute("select ops.write_receipt_digest(%s,%s,%s,%s,%s,'deal',%s)",
                        ("log-activity", actor, TENANT, sid, "rh-today", subject))
            call_digest = cur.fetchone()[0]
            conn.commit()

        # material_digest = call_digest: precisely the grandfathered shape.
        try:
            with conn.cursor() as cur:
                cur.execute("""insert into ops.write_receipt
                                 (id, application_session_id, actor_id, organization_tenant_id,
                                  verb, subject_type, subject_id, tool_call_idempotency_key,
                                  call_digest, material_digest, prior_digest)
                               values (%s,%s,%s,%s,'log-activity','deal',%s,%s,%s,%s,'origin')""",
                            (uuid.uuid4(), sid, actor, TENANT, subject, key,
                             call_digest, call_digest))
            conn.rollback()
            raise AssertionError(
                "a receipt carrying the GRANDFATHERED shape (material_digest = "
                "call_digest) was accepted today; the backfilled rows are "
                "reproducible under current rules and the signature below means "
                "nothing")
        except psycopg.Error as exc:
            conn.rollback()
            text = str(exc)
            assert "receipt material does not match what its call wrote" in text, (
                f"refused, but by a DIFFERENT guard than 0270(F): {text.strip().splitlines()[0]}")
    check("a grandfathered value is one the current rules REFUSE — the same row "
          "cannot be written today", a_grandfathered_value_would_be_refused_today)

    def grandfathered_rows_still_count_toward_the_bar():
        """ops.accept_phase4 counts ops.write_receipt globally, with no clause
        excluding backfilled rows. That is the exposure, and asserting it is how
        it stops being a surprise: a mutation that quietly filtered them out
        would change the bar's meaning and nothing else would notice."""
        ids = grandfathered_ids()
        assert len(ids) >= 2, (
            f"expected at least the 2 seeded grandfathered rows, found {len(ids)}")
        with conn.cursor() as cur:
            cur.execute("""select count(*) filter (where is_proven),
                                  count(*) filter (where not is_proven)
                             from ops.write_receipt""")
            proven_all, unproven_all = cur.fetchone()
            cur.execute("""select count(*) filter (where is_proven)
                             from ops.write_receipt where material_digest = call_digest""")
            proven_grandfathered = cur.fetchone()[0]
        assert proven_grandfathered >= 2, (
            f"the seeded grandfathered receipts are not proven "
            f"({proven_grandfathered} proven)")
        conn.rollback()
        # ACCEPTANCE MUST BE THE FIRST WRITE IN ITS TRANSACTION (0268), so this
        # runs alone on a connection that has written nothing.
        acc = psycopg.connect(dsn)
        try:
            with acc.cursor() as cur:
                cur.execute("select id from actor where kind='human' order by slug limit 1")
                actor = cur.fetchone()[0]
                cur.execute("""select id from ops.application_session
                                where revoked_at is null and expires_at > now()
                                  and actor_id = %s order by authenticated_at desc limit 1""",
                            (actor,))
                row = cur.fetchone()
                assert row, "no live session to accept with"
                cur.execute("set role carr_authority")
                cur.execute("select ops.accept_phase4(%s,%s,%s)",
                            (uuid.uuid4(), row[0], "grandfathered-row accounting probe"))
                acc.commit()
            with acc.cursor() as cur:
                # 0268 grants SELECT on ops.phase4_acceptance to carr_reader and
                # carr_writer but NOT to carr_authority -- the identity that can
                # create one cannot read one back. Noted, not changed here: it is
                # a grant asymmetry in another slice's migration, not this
                # contract's subject.
                cur.execute("reset role")
                acc.commit()
            with acc.cursor() as cur:
                cur.execute("""select proven_receipts, unproven_receipts
                                 from ops.phase4_acceptance order by accepted_at desc limit 1""")
                counted_proven, counted_unproven = cur.fetchone()
        finally:
            acc.close()
        assert counted_proven == proven_all, (
            f"the acceptance bar counted {counted_proven} proven receipts but the "
            f"table holds {proven_all}; something is filtering rows out of the bar")
        assert counted_unproven == unproven_all, (
            f"the acceptance bar counted {counted_unproven} unproven receipts but "
            f"the table holds {unproven_all}")
        assert counted_proven >= proven_grandfathered, (
            "the bar counted fewer proven receipts than there are proven "
            "grandfathered ones")
    check("grandfathered receipts count toward the acceptance bar exactly like "
          "rows written under the new rules", grandfathered_rows_still_count_toward_the_bar)

    def a_new_receipt_can_build_on_a_grandfathered_state():
        """AND THEY ARE LOAD-BEARING, not merely present.
        ops.require_prior_state_existed accepts any proven, unretracted material
        on the subject, so a grandfathered material -- a value the guard in the
        previous contract refuses to let anyone WRITE -- is a prior anyone may
        BUILD ON. Both facts are true at once, and that is worth stating out
        loud rather than leaving for whoever hits it."""
        with conn.cursor() as cur:
            cur.execute("""select subject_type, subject_id, material_digest,
                                  organization_tenant_id
                             from ops.write_receipt
                            where material_digest = call_digest and is_proven
                            order by seq desc limit 1""")
            subject_type, subject_id, grandfathered_material, tenant = cur.fetchone()
            cur.execute("select id from actor where kind='human' order by slug limit 1")
            actor = cur.fetchone()[0]
            sid = uuid.uuid4()
            cur.execute("""insert into ops.application_session
                             (id, actor_id, organization_tenant_id, sponsoring_human_slug,
                              via, auth_issuer, authorization_class, verified_subject, expires_at)
                           values (%s,%s,%s,'joe','probe','probe-issuer','verified_partner',
                                   'probe', clock_timestamp() + interval '1 hour')""",
                        (sid, actor, tenant))
            key = f"successor-{uuid.uuid4()}"
            cur.execute("""insert into tool_call
                             (idempotency_key, verb, actor_id, request_hash, response,
                              organization_tenant_id, application_session_id)
                           values (%s,'log-activity',%s,'rh-succ','{}'::jsonb,%s,%s)""",
                        (key, actor, tenant, sid))
            cur.execute("""insert into event
                             (occurred_at, actor_id, verb, subject_type, subject_id, field,
                              new_value, cause, idempotency_key, organization_tenant_id,
                              application_session_id)
                           values (clock_timestamp(),%s,'log-activity',%s,%s,'stage',
                                   '"successor"'::jsonb,'system',%s,%s,%s)""",
                        (actor, subject_type, subject_id, key, tenant, sid))
            cur.execute("select ops.write_receipt_digest(%s,%s,%s,%s,%s,%s,%s)",
                        ("log-activity", actor, tenant, sid, "rh-succ",
                         subject_type, subject_id))
            call_digest = cur.fetchone()[0]
            cur.execute("select ops.write_receipt_material_digest(%s,%s,%s,%s)",
                        (key, sid, subject_type, subject_id))
            material = cur.fetchone()[0]
            rid = uuid.uuid4()
            cur.execute("""insert into ops.write_receipt
                             (id, application_session_id, actor_id, organization_tenant_id,
                              verb, subject_type, subject_id, tool_call_idempotency_key,
                              call_digest, material_digest, prior_digest)
                           values (%s,%s,%s,%s,'log-activity',%s,%s,%s,%s,%s,%s)""",
                        (rid, sid, actor, tenant, subject_type, subject_id, key,
                         call_digest, material, grandfathered_material))
            assert cur.rowcount == 1
            conn.commit()
        with conn.cursor() as cur:
            cur.execute("select prior_digest from ops.write_receipt where id=%s", (rid,))
            assert cur.fetchone()[0] == grandfathered_material, (
                "the successor receipt did not record the grandfathered material "
                "as its prior")
    check("a receipt written today CAN build on a grandfathered state — the "
          "backfilled value is load-bearing, not inert",
          a_new_receipt_can_build_on_a_grandfathered_state)

    def there_is_no_down_path_only_a_signature():
        """THE IRREVERSIBILITY, asserted rather than asserted-in-a-comment.

        migrations/ is forward-only: no down files exist in the series, and
        tools/migrate.py refuses any applied file whose content changed. So
        nothing restores the pre-split shape and nothing records which rows the
        UPDATE touched. What DOES survive is the signature -- material_digest =
        call_digest -- and the previous contracts prove no new row can wear it.
        This one proves the signature is a real discriminator here: some rows
        have it, some do not, and it is not simply true of everything.
        """
        with conn.cursor() as cur:
            cur.execute("""select count(*) filter (where material_digest = call_digest),
                                  count(*) filter (where material_digest <> call_digest)
                             from ops.write_receipt""")
            wearing, not_wearing = cur.fetchone()
        assert wearing >= 2, (
            f"only {wearing} rows carry the grandfathered signature; the fixture "
            f"seeded 2")
        assert not_wearing >= 1, (
            "EVERY receipt in this database carries material_digest = "
            "call_digest, so the signature separates nothing and cannot identify "
            "a backfilled row — check that the post-0270 receipts were written")
        # And the pre-split column name is gone for good.
        with conn.cursor() as cur:
            cur.execute("""select count(*) from information_schema.columns
                            where table_schema='ops' and table_name='write_receipt'
                              and column_name='claimed_digest'""")
            assert cur.fetchone()[0] == 0, (
                "ops.write_receipt.claimed_digest still exists; 0270 renamed it, so "
                "its presence means the split did not actually happen here")
    check("there is no down path — only a signature, and it discriminates",
          there_is_no_down_path_only_a_signature)

    conn.close()
    print(f"\n{len(PASSES)} passed, {len(FAILURES)} failed")
    if FAILURES:
        print("\nFAILED CONTRACTS:")
        for name, why in FAILURES:
            print(f"  - {name}: {why}")
        return 1
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] not in ("seed", "verify"):
        print("usage: grandfathered_receipt_contract.py {seed|verify} <dsn>", file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(seed(sys.argv[2]) if sys.argv[1] == "seed" else verify(sys.argv[2]))
