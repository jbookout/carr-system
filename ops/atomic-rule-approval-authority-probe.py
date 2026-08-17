#!/usr/bin/env python3
"""Disposable-local rollback proof for enforced-only human rule approval."""
from __future__ import annotations

import os
import uuid
from typing import Any

import psycopg
from psycopg import sql


def one(cur: psycopg.Cursor[Any], query: str, params: tuple[object, ...] = ()) -> Any:
    cur.execute(query, params)
    row = cur.fetchone()
    if row is None:
        raise RuntimeError("authority probe query returned no row")
    return row[0]


def main() -> int:
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        raise SystemExit("DATABASE_URL is required for the rollback-only authority probe")
    if os.environ.get("CARR_ALLOW_DISPOSABLE_SESSION_AUTH") != "1":
        raise SystemExit("CARR_ALLOW_DISPOSABLE_SESSION_AUTH=1 is required; never use this on a retained database")
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            if one(cur, "select rolsuper from pg_roles where rolname=current_user") is not True:
                raise RuntimeError("disposable fixture setup requires the local cluster superuser")
            # These principals are external provisioning in retained databases
            # and migrations deliberately do not mint them.  This probe is the
            # narrow exception: it is superuser-only, explicitly disposable,
            # and the enclosing transaction rolls the fixture roles back.
            for role in ("carr_authority_joe", "carr_authority_dell"):
                if one(cur, "select count(*) from pg_roles where rolname=%s", (role,)) == 0:
                    cur.execute(sql.SQL("create role {} nologin").format(sql.Identifier(role)))
                cur.execute(sql.SQL("grant carr_authority to {}").format(sql.Identifier(role)))
            actor = one(cur, "select id from actor where slug='joe' and active")
            missing_rule = one(cur, """insert into rule
                (statement,human_quote,taught_by,status)
                values (%s,'fixture',%s,'proposed') returning id""",
                (f"missing control fixture {uuid.uuid4()}", actor))
            direct_rule = one(cur, """insert into rule
                (statement,human_quote,taught_by,status)
                values (%s,'fixture',%s,'proposed') returning id""",
                (f"direct activation fixture {uuid.uuid4()}", actor))
            cost_rule = one(cur, """insert into rule
                (statement,human_quote,taught_by,status)
                values (%s,'fixture',%s,'proposed') returning id""",
                (f"metered execution fixture {uuid.uuid4()}", actor))
            dell_rule = one(cur, """insert into rule
                (statement,human_quote,taught_by,status)
                values (%s,'fixture',%s,'proposed') returning id""",
                (f"Dell nonblocking authority fixture {uuid.uuid4()}", actor))
            cur.execute("""insert into ops.rule_control_binding
                (rule_id,control_key,statement_hash,binding_contract)
                select id,'platform_metering_pre_dispatch',
                       encode(digest(statement,'sha256'),'hex'),
                       '{"fixture":"atomic-rule-approval-authority-probe"}'::jsonb
                  from rule where id=any(%s)""", ([cost_rule, dell_rule],))
            cur.execute("set session authorization carr_authority_dell")
            cur.execute("savepoint dell_authority_refusal")
            try:
                cur.execute("select ops.approve_rule(%s,%s,%s,%s,%s)",
                            (dell_rule, "machine_enforceable",
                             ["platform_metering_pre_dispatch"],
                             f"dell-{uuid.uuid4()}", "Dell participation is nonblocking"))
            except psycopg.Error as exc:
                if "system rule approval requires Joe authority" not in str(exc):
                    raise
                cur.execute("rollback to savepoint dell_authority_refusal")
            else:
                raise RuntimeError("Dell authority unexpectedly replaced Joe approval")
            if one(cur, "select status from rule where id=%s", (dell_rule,)) != "proposed":
                raise RuntimeError("Dell refusal changed the proposed rule")
            cur.execute("reset session authorization")
            cur.execute("set session authorization carr_authority_joe")
            identity = one(cur, "select session_user||'/'||current_user")
            if identity != "carr_authority_joe/carr_authority_joe":
                raise RuntimeError("probe requires exact carr_authority_joe session identity")

            cur.execute("savepoint missing_control_refusal")
            try:
                cur.execute("select ops.approve_rule(%s,%s,%s,%s,%s)",
                            (missing_rule, "machine_enforceable",
                             ["platform_metering_pre_dispatch"],
                             f"missing-{uuid.uuid4()}", "must refuse"))
            except psycopg.Error as exc:
                if "exact enforcement is not installed" not in str(exc):
                    raise
                cur.execute("rollback to savepoint missing_control_refusal")
            else:
                raise RuntimeError("missing control unexpectedly produced an approval")
            if one(cur, "select status from rule where id=%s", (missing_rule,)) != "proposed":
                raise RuntimeError("missing-control rule did not remain proposed")
            if one(cur, "select count(*) from ops.rule_approval_receipt where rule_id=%s",
                   (missing_rule,)) != 0:
                raise RuntimeError("missing control minted an approval receipt")
            cur.execute("reset session authorization")
            cur.execute("set role carr_writer")
            cur.execute("savepoint direct_activation_refusal")
            try:
                cur.execute("update rule set status='active',activated_by=%s where id=%s",
                            (actor, direct_rule))
            except psycopg.Error as exc:
                if "admitted rule contract is missing" not in str(exc):
                    raise
                cur.execute("rollback to savepoint direct_activation_refusal")
            else:
                raise RuntimeError("direct activation bypass unexpectedly succeeded")
            cur.execute("reset role")
            cur.execute("set session authorization carr_authority_joe")
            result = one(cur, "select ops.approve_rule(%s,%s,%s,%s,%s)",
                         (cost_rule, "machine_enforceable",
                          ["platform_metering_pre_dispatch"],
                          f"metered-{uuid.uuid4()}", "approved enforced cost boundary"))
            if not isinstance(result, dict) or result.get("policy_status") != "active":
                raise RuntimeError("enforced approval did not return active")
            if result.get("enforcement_status") != "hard_enforced":
                raise RuntimeError("enforced approval did not return hard_enforced")
            if one(cur, "select status from rule where id=%s", (cost_rule,)) != "active":
                raise RuntimeError("enforced approval did not activate the rule")
            if one(cur, "select count(*) from ops.rule_approval_receipt where rule_id=%s",
                   (cost_rule,)) != 1:
                raise RuntimeError("enforced approval receipt is missing or duplicated")
            if one(cur, """select count(*) from ops.v_rule_enforcement_status
                where rule_id=%s
                  and installed_controls @> array['platform_metering_pre_dispatch']""",
                   (cost_rule,)) != 1:
                raise RuntimeError("approved rule lacks its exact installed control")
            cur.execute("reset session authorization")
        conn.rollback()
    print("PASS: Joe approval is atomic with enforcement; Dell, missing, and direct paths refuse")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
