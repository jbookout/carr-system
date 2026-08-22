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
                print("PASS: atomic Joe rule approval, replay, retirement and rollback")
                return 0
        finally:
            conn.rollback()


if __name__ == "__main__":
    raise SystemExit(main())
