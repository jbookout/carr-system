#!/usr/bin/env python3
"""Superuser-only, rollback-only positive acceptance for the Joe rule lifecycle.

This is deliberately not a marked portable DB gate: managed database owners
cannot impersonate externally provisioned authority LOGIN principals. The
local PostgreSQL runner supplies a fresh loopback superuser DSN; every role,
rule and receipt created here is rolled back.
"""
from __future__ import annotations

import os
import uuid
from typing import NoReturn
from urllib.parse import urlparse

import psycopg


def refuse(message: str) -> NoReturn:
    raise RuntimeError(message)


def main() -> int:
    dsn = os.environ.get("CARR_LOCAL_PG_DSN", "").strip()
    parsed = urlparse(dsn)
    if parsed.scheme not in {"postgres", "postgresql"} or parsed.hostname not in {
        "127.0.0.1", "localhost", "::1",
    }:
        refuse("atomic rule acceptance requires an explicit loopback CARR_LOCAL_PG_DSN")

    with psycopg.connect(dsn) as conn:
        try:
            with conn.cursor() as cur:
                cur.execute("begin")
                cur.execute("select rolsuper from pg_roles where rolname=current_user")
                row = cur.fetchone()
                if row is None or row[0] is not True:
                    refuse("atomic rule acceptance requires a disposable local superuser")
                cur.execute(
                    """do $$
                       begin
                         if not exists (
                           select 1 from pg_roles where rolname='carr_authority_joe'
                         ) then
                           create role carr_authority_joe login;
                         elsif not (
                           select rolcanlogin from pg_roles where rolname='carr_authority_joe'
                         ) then
                           raise exception 'carr_authority_joe must be an exact LOGIN identity';
                         end if;
                       end $$"""
                )
                cur.execute("grant carr_authority to carr_authority_joe")
                # carr_authority_dell: a role that DOES carry EXECUTE on the
                # amendment function (member of carr_authority) but must still
                # be refused by ops.amend_rule_statement's OWN internal check
                # -- proving the Joe-only guard is not merely the GRANT.
                cur.execute(
                    """do $$
                       begin
                         if not exists (
                           select 1 from pg_roles where rolname='carr_authority_dell'
                         ) then
                           create role carr_authority_dell login;
                         elsif not (
                           select rolcanlogin from pg_roles where rolname='carr_authority_dell'
                         ) then
                           raise exception 'carr_authority_dell must be an exact LOGIN identity';
                         end if;
                       end $$"""
                )
                cur.execute("grant carr_authority to carr_authority_dell")
                cur.execute("grant carr_writer to current_user")
                cur.execute("select id from actor where slug='joe' and kind='human' and active")
                actor = cur.fetchone()
                if actor is None:
                    refuse("Joe actor fixture is missing")
                rule_id = uuid.uuid4()
                control_key = f"local-rule-acceptance-{uuid.uuid4()}"
                approve_key = f"local-approve-{uuid.uuid4()}"
                retire_key = f"local-retire-{uuid.uuid4()}"
                cur.execute(
                    """insert into rule(id,statement,human_quote,taught_by,status)
                       values (%s,'local exact approval fixture','Joe approved the fixture',%s,'proposed')""",
                    (rule_id, actor[0]),
                )
                cur.execute(
                    """insert into ops.enforcement_control_catalog
                         (control_key,implementation_ref,test_ref,enforcement_class,installed,verified_at)
                       values (%s,'local-pg-acceptance','ops/atomic-rule-approval-local-pg-acceptance.py',
                               'transactional_schema',true,now())""",
                    (control_key,),
                )
                cur.execute(
                    """insert into ops.rule_control_binding
                         (rule_id,control_key,statement_hash,binding_contract)
                       select id,%s,encode(digest(statement,'sha256'),'hex'),
                              '{"fixture":"local atomic approval"}'::jsonb
                         from rule where id=%s""",
                    (control_key, rule_id),
                )

                cur.execute("set session authorization carr_authority_joe")
                cur.execute(
                    "select ops.approve_rule(%s,'machine_enforceable',array[%s],%s,%s)",
                    (rule_id, control_key, approve_key, "Joe local authority acceptance"),
                )
                approved = cur.fetchone()
                if approved is None or approved[0].get("policy_status") != "active" \
                        or approved[0].get("replayed") is not False:
                    refuse("atomic approve did not activate exact enforcement")
                cur.execute(
                    "select ops.approve_rule(%s,'machine_enforceable',array[%s],%s,%s)",
                    (rule_id, control_key, approve_key, "Joe local authority acceptance"),
                )
                replay = cur.fetchone()
                if replay is None or replay[0].get("replayed") is not True:
                    refuse("atomic approval exact replay failed")
                cur.execute(
                    "select count(*) from ops.applicable_rules(null,null,null) where rule_id=%s",
                    (rule_id,),
                )
                applicable = cur.fetchone()
                if applicable is None or applicable[0] != 1:
                    refuse("receipt-bound policy compiler omitted the approved rule")
                cur.execute(
                    """select count(*) from ops.applicable_rules(%s,%s,%s)
                        where rule_id=%s""",
                    ("finite-workflow", "finite-surface", "finite-tier", rule_id),
                )
                finite_applicable = cur.fetchone()
                if finite_applicable is None or finite_applicable[0] != 1:
                    refuse("global approved rule did not apply to a finite context")

                # ---- versioned amendment (WR-000019 slice S10) ----
                # A SEPARATE rule/control fixture, deliberately never retired in
                # this script: ops.retire_rule's own approval-receipt lookup
                # still requires an EXACT statement-hash match (slice S10 only
                # taught ops.applicable_rules() to follow an amendment chain,
                # by design -- ops.approve_rule and ops.retire_rule are
                # untouched), so amending then retiring the rule_id fixture
                # above would conflate two different questions in one fixture.
                amend_rule_id = uuid.uuid4()
                amend_control_key = f"local-amend-acceptance-{uuid.uuid4()}"
                amend_approve_key = f"local-amend-approve-{uuid.uuid4()}"
                amend_key = f"local-amend-{uuid.uuid4()}"
                original_statement = "local exact amendment fixture, before wording fix"
                # Back to the disposable superuser for fixture setup, same as
                # the rule_id fixture above did BEFORE its own "set session
                # authorization carr_authority_joe" -- carr_authority_joe has
                # no direct grant on `rule`/`ops.enforcement_control_catalog`/
                # `ops.rule_control_binding`; it only ever writes through the
                # SECURITY DEFINER authority functions.
                cur.execute("reset session authorization")
                cur.execute(
                    """insert into rule(id,statement,human_quote,taught_by,status)
                       values (%s,%s,'Joe approved the amendment fixture',%s,'proposed')""",
                    (amend_rule_id, original_statement, actor[0]),
                )
                cur.execute(
                    """insert into ops.enforcement_control_catalog
                         (control_key,implementation_ref,test_ref,enforcement_class,installed,verified_at)
                       values (%s,'local-pg-acceptance','ops/atomic-rule-approval-local-pg-acceptance.py',
                               'transactional_schema',true,now())""",
                    (amend_control_key,),
                )
                cur.execute(
                    """insert into ops.rule_control_binding
                         (rule_id,control_key,statement_hash,binding_contract)
                       select id,%s,encode(digest(statement,'sha256'),'hex'),
                              '{"fixture":"local amendment"}'::jsonb
                         from rule where id=%s""",
                    (amend_control_key, amend_rule_id),
                )
                cur.execute("set session authorization carr_authority_joe")
                cur.execute(
                    "select ops.approve_rule(%s,'machine_enforceable',array[%s],%s,%s)",
                    (amend_rule_id, amend_control_key, amend_approve_key, "Joe local authority acceptance"),
                )
                amend_approved = cur.fetchone()
                if amend_approved is None or amend_approved[0].get("policy_status") != "active":
                    refuse("amendment fixture did not activate")
                cur.execute(
                    "select count(*) from ops.applicable_rules(null,null,null) where rule_id=%s",
                    (amend_rule_id,),
                )
                before_amend = cur.fetchone()
                if before_amend is None or before_amend[0] != 1:
                    refuse("amendment fixture was not applicable before amendment")

                amended_statement = "local exact amendment fixture, AFTER wording fix"
                cur.execute(
                    "select ops.amend_rule_statement(%s,%s,%s,%s)",
                    (amend_rule_id, amended_statement, amend_key, "fixing the wording, same meaning"),
                )
                amended = cur.fetchone()
                if amended is None or amended[0].get("ok") is not True or amended[0].get("replayed") is not False:
                    refuse("amend_rule_statement did not report a fresh success")
                amendment_receipt_id = amended[0].get("amendment_receipt_id")
                if not amendment_receipt_id:
                    refuse("amend_rule_statement did not return a receipt id")

                # RECEIPT EXISTS, STATEMENT UPDATED, PRIOR HASH CHAINS -- the
                # exact round trip WR-000019 slice S10 names.
                cur.execute(
                    """select r.statement=%s and r.version=ar.rule_version_after
                         and ar.prior_statement_hash=encode(digest(%s,'sha256'),'hex')
                         and ar.new_statement=%s
                         and ar.new_statement_hash=encode(digest(r.statement,'sha256'),'hex')
                         and ar.rationale='fixing the wording, same meaning'
                         and ar.id=%s
                         from rule r join ops.rule_amendment_receipt ar on ar.rule_id=r.id
                        where r.id=%s""",
                    (amended_statement, original_statement, amended_statement,
                     amendment_receipt_id, amend_rule_id),
                )
                amendment_binding = cur.fetchone()
                if amendment_binding is None or amendment_binding[0] is not True:
                    refuse("amendment receipt is not exactly bound to the amended statement")

                cur.execute(
                    "select ops.amend_rule_statement(%s,%s,%s,%s)",
                    (amend_rule_id, amended_statement, amend_key, "fixing the wording, same meaning"),
                )
                amend_replay = cur.fetchone()
                if amend_replay is None or amend_replay[0].get("replayed") is not True:
                    refuse("amendment exact replay failed")

                # THE PART THAT IS EASY TO MISS: an amended ACTIVE rule must
                # keep reciting under its old approval, not silently vanish
                # from ops.applicable_rules() the moment its wording changed.
                cur.execute(
                    "select count(*) from ops.applicable_rules(null,null,null) where rule_id=%s",
                    (amend_rule_id,),
                )
                after_amend = cur.fetchone()
                if after_amend is None or after_amend[0] != 1:
                    refuse("amended active rule dropped out of applicable_rules -- "
                           "it must keep reciting under its old approval")

                # Dell HAS execute (carr_authority_dell is a carr_authority
                # member) but ops.amend_rule_statement's own body must still
                # refuse -- this is the check the GRANT alone cannot prove.
                cur.execute("reset session authorization")
                cur.execute("savepoint amend_requires_joe_not_just_authority")
                cur.execute("set session authorization carr_authority_dell")
                try:
                    cur.execute(
                        "select ops.amend_rule_statement(%s,%s,%s,%s)",
                        (amend_rule_id, "a Dell-authored rewrite",
                         f"local-amend-dell-{uuid.uuid4()}", "Dell tries to amend"),
                    )
                    refuse("amend_rule_statement succeeded for Dell, not Joe")
                except psycopg.Error as exc:
                    cur.execute("rollback to savepoint amend_requires_joe_not_just_authority")
                    if "joe authority" not in str(exc).lower():
                        refuse(f"Dell amendment refusal was for the wrong reason: {exc}")
                finally:
                    cur.execute("reset session authorization")
                cur.execute("release savepoint amend_requires_joe_not_just_authority")

                # A no-op "amendment" (new text hashes identically to the
                # current text) is refused, not silently accepted with a
                # receipt that would claim a correction never made.
                cur.execute("set session authorization carr_authority_joe")
                cur.execute("savepoint amend_refuses_noop")
                try:
                    cur.execute(
                        "select ops.amend_rule_statement(%s,%s,%s,%s)",
                        (amend_rule_id, amended_statement,
                         f"local-amend-noop-{uuid.uuid4()}", "no actual change"),
                    )
                    refuse("amend_rule_statement accepted a no-op amendment")
                except psycopg.Error as exc:
                    cur.execute("rollback to savepoint amend_refuses_noop")
                    if "no-op" not in str(exc).lower():
                        refuse(f"no-op amendment refusal was for the wrong reason: {exc}")
                cur.execute("release savepoint amend_refuses_noop")

                # A RETIRED rule's words are history; amending a tombstone is
                # exactly what the retirement receipt already refuses to be
                # reopened from -- ops.amend_rule_statement must refuse it
                # directly, at the database layer, not merely because the MCP
                # handler happens to check status first.
                retired_rule_id = uuid.uuid4()
                retire_for_amend_key = f"local-retire-for-amend-{uuid.uuid4()}"
                cur.execute("reset session authorization")
                cur.execute(
                    """insert into rule(id,statement,human_quote,taught_by,status)
                       values (%s,'local exact retired-amendment fixture','Joe said retire it',%s,'proposed')""",
                    (retired_rule_id, actor[0]),
                )
                cur.execute("set session authorization carr_authority_joe")
                cur.execute(
                    "select ops.retire_rule(%s,%s,null,%s)",
                    (retired_rule_id, "never needed", retire_for_amend_key),
                )
                retired_for_amend = cur.fetchone()
                if retired_for_amend is None or retired_for_amend[0].get("status") != "retired":
                    refuse("retired-amendment fixture did not actually retire")
                cur.execute("savepoint amend_refuses_retired")
                try:
                    cur.execute(
                        "select ops.amend_rule_statement(%s,%s,%s,%s)",
                        (retired_rule_id, "reopening a tombstone",
                         f"local-amend-retired-{uuid.uuid4()}", "try to reopen"),
                    )
                    refuse("amend_rule_statement succeeded on a retired rule")
                except psycopg.Error as exc:
                    cur.execute("rollback to savepoint amend_refuses_retired")
                    if "retired" not in str(exc).lower():
                        refuse(f"retired-rule amendment refusal was for the wrong reason: {exc}")
                cur.execute("release savepoint amend_refuses_retired")
                cur.execute("reset session authorization")

                # THE TRIGGER'S OWN GUARD, independent of the function: even a
                # local superuser writing `update rule set statement=...`
                # directly (never touching ops.amend_rule_statement, so no
                # matching ops.rule_amendment_receipt row exists) must be
                # refused by ops.require_rule_admission -- the receipted
                # function is not a courtesy path, it is the ONLY path.
                cur.execute("savepoint amend_trigger_refuses_bypass")
                try:
                    cur.execute(
                        "update rule set statement=%s where id=%s",
                        ("a direct bypass with no amendment receipt", amend_rule_id),
                    )
                    refuse("a direct UPDATE changed an active rule's statement with no amendment receipt")
                except psycopg.Error as exc:
                    cur.execute("rollback to savepoint amend_trigger_refuses_bypass")
                    if "immutable" not in str(exc).lower():
                        refuse(f"direct-bypass refusal was for the wrong reason: {exc}")
                cur.execute("release savepoint amend_trigger_refuses_bypass")

                cur.execute("savepoint amend_requires_authority")
                cur.execute("set local role carr_writer")
                try:
                    cur.execute(
                        "select ops.amend_rule_statement(%s,%s,%s,%s)",
                        (amend_rule_id, "an unauthorized rewrite",
                         f"local-amend-unauth-{uuid.uuid4()}", "no authority"),
                    )
                    refuse("amend_rule_statement succeeded for a non-authority role")
                except psycopg.Error as exc:
                    cur.execute("rollback to savepoint amend_requires_authority")
                    # Two valid refusal shapes, both proving the same boundary:
                    # carr_writer has no EXECUTE grant on the function at all
                    # (revoke all from public + grant to carr_authority only),
                    # so Postgres refuses before the function body ever runs;
                    # a role that DID have execute (e.g. carr_authority_dell)
                    # would instead reach ops.authority_actor_slug()'s own
                    # "is not an admitted human authority principal" check.
                    msg = str(exc).lower()
                    if "authority" not in msg and "permission denied" not in msg:
                        refuse(f"amendment authority refusal was for the wrong reason: {exc}")
                finally:
                    cur.execute("reset role")
                cur.execute("release savepoint amend_requires_authority")
                # Restore the session authorization the ORIGINAL rule_id
                # fixture's own retire_rule call below still expects.
                cur.execute("set session authorization carr_authority_joe")

                cur.execute(
                    "select ops.retire_rule(%s,%s,null,%s)",
                    (rule_id, "Joe local authority retirement", retire_key),
                )
                retired = cur.fetchone()
                if retired is None or retired[0].get("status") != "retired" \
                        or retired[0].get("replayed") is not False:
                    refuse("Joe authority retirement did not complete")
                cur.execute(
                    "select ops.retire_rule(%s,%s,null,%s)",
                    (rule_id, "Joe local authority retirement", retire_key),
                )
                retire_replay = cur.fetchone()
                if retire_replay is None or retire_replay[0].get("replayed") is not True:
                    refuse("Joe authority retirement exact replay failed")
                cur.execute("reset session authorization")
                for label, mutation in (
                    ("retired statement", "update rule set statement=statement||' drift' where id=%s"),
                    ("retired scope", "update rule set scope='{\"workflows\":[\"drift\"]}'::jsonb where id=%s"),
                    ("retired actor", "update rule set retired_by=null where id=%s"),
                    ("retired timestamp", "update rule set retired_at=now() where id=%s"),
                    ("retired revival", "update rule set status='proposed' where id=%s"),
                ):
                    cur.execute("savepoint retired_tombstone_mutation")
                    try:
                        cur.execute("set local role carr_writer")
                        cur.execute(mutation, (rule_id,))
                        refuse(f"retired tombstone mutation refused: {label} unexpectedly succeeded")
                    except psycopg.Error:
                        cur.execute("rollback to savepoint retired_tombstone_mutation")
                    finally:
                        cur.execute("reset role")
                # A local superuser can bypass row triggers; deliberately do so
                # only inside this rollback-only acceptance to prove replay is
                # not fooled by a corrupted tombstone it did not create.
                cur.execute("savepoint altered_retirement_replay")
                cur.execute("alter table rule disable trigger rule_activation_requires_admission")
                try:
                    cur.execute("update rule set statement=statement||' altered' where id=%s", (rule_id,))
                finally:
                    cur.execute("alter table rule enable trigger rule_activation_requires_admission")
                cur.execute("set session authorization carr_authority_joe")
                try:
                    cur.execute(
                        "select ops.retire_rule(%s,%s,null,%s)",
                        (rule_id, "Joe local authority retirement", retire_key),
                    )
                    refuse("altered retirement replay was accepted")
                except psycopg.Error as exc:
                    cur.execute("rollback to savepoint altered_retirement_replay")
                    if "current retired rule no longer matches the immutable retirement" not in str(exc).lower():
                        refuse(f"altered retirement replay refused for the wrong reason: {exc}")
                finally:
                    cur.execute("reset session authorization")
                cur.execute("release savepoint altered_retirement_replay")
                cur.execute(
                    """select r.status='retired' and r.retired_by=rr.actor_id
                         and r.retired_at=rr.retired_at
                         and r.version=rr.rule_version_after
                         and encode(digest(r.statement,'sha256'),'hex')=rr.statement_hash
                         from rule r join ops.rule_retirement_receipt rr on rr.rule_id=r.id
                        where r.id=%s""",
                    (rule_id,),
                )
                retirement_binding = cur.fetchone()
                if retirement_binding is None or retirement_binding[0] is not True:
                    refuse("retirement receipt is not exactly bound to the retired tombstone")
                conn.rollback()
                print("PASS: atomic Joe rule approval, amendment, replay, retirement and rollback")
                return 0
        finally:
            conn.rollback()


if __name__ == "__main__":
    raise SystemExit(main())
