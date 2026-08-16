#!/usr/bin/env python3
# ci: db-gate
"""Rollback-only acceptance gate for the typed Guidance Registry.

The gate deliberately uses a single transaction: its proposed revisions,
authority receipts, lifecycle events and situation bridge evidence must never
survive an acceptance run.
"""
from __future__ import annotations

import hashlib
import os
import sys
import uuid
from typing import Any, NoReturn

import psycopg
from psycopg.types.json import Jsonb


TABLES = (
    "ops.guidance_registry", "ops.guidance_item", "ops.guidance_revision",
    "ops.guidance_authority_binding", "ops.guidance_lifecycle_event",
    "ops.guidance_situation_mapping", "ops.guidance_registry_event",
)
FUNCTIONS = (
    "ops.guidance_revision_contract_hash(uuid)",
    "ops.record_guidance_decision(uuid,text,text,text)",
    "ops.propose_guidance_situation_mapping(uuid,uuid,uuid,text)",
    "ops.activate_guidance_situation_mapping(uuid,uuid,text)",
    "ops.assert_guidance_registry_coverage()",
    "ops.standing_guidance(text,text,text,text)",
    "ops.activate_guidance_registry(uuid,text,text,text)",
)


def fail(message: str) -> NoReturn:
    raise RuntimeError(message)


def one(cur: psycopg.Cursor[Any], query: str, params: tuple[Any, ...] = ()) -> Any:
    row = cur.execute(query, params).fetchone()
    if row is None:
        fail(f"expected one row: {query}")
    return row[0]


def refuses(cur: psycopg.Cursor[Any], query: str, params: tuple[Any, ...], label: str) -> None:
    cur.execute("savepoint guidance_gate_refusal")
    try:
        cur.execute(query, params)
    except psycopg.Error:
        cur.execute("rollback to savepoint guidance_gate_refusal")
        return
    cur.execute("rollback to savepoint guidance_gate_refusal")
    fail(f"{label} unexpectedly succeeded")


def authority_one(cur: psycopg.Cursor[Any], query: str,
                  params: tuple[Any, ...] = (), actor: str = "joe") -> Any:
    """Exercise an authority function with the session_user it authenticates.

    CI's PostgreSQL is disposable and the surrounding transaction rolls back
    the fixture login and membership with every other fixture.
    """
    cur.execute(f"set session authorization carr_authority_{actor}")
    try:
        return one(cur, query, params)
    finally:
        cur.execute("reset session authorization")


def authority_refuses(cur: psycopg.Cursor[Any], query: str,
                      params: tuple[Any, ...], label: str,
                      actor: str = "joe") -> None:
    cur.execute(f"set session authorization carr_authority_{actor}")
    try:
        refuses(cur, query, params, label)
    finally:
        cur.execute("reset session authorization")


def item(cur: psycopg.Cursor[Any], actor_id: Any, suffix: str) -> Any:
    intake_id = one(cur, """insert into ops.guidance_intake
        (lane,source_kind,source_ref,statement,state,normalized_contract,captured_by)
        values ('procedure','system',%s,%s,'admitted','{}'::jsonb,%s) returning id""",
                    (f"db-gate:{suffix}", f"DB gate {suffix}", actor_id))
    return one(cur, """insert into ops.guidance_item
        (guidance_intake_id,source_clause,created_by)
        values (%s,%s,%s) returning id""", (intake_id, f"clause {suffix}", actor_id))


def rule_item(cur: psycopg.Cursor[Any], actor_id: Any, suffix: str) -> Any:
    rule_id = one(cur, """insert into rule
        (statement,taught_by,scope,status,enforcement)
        values (%s,%s,'{}'::jsonb,'proposed','prose') returning id""",
                  (f"DB gate rule {suffix}", actor_id))
    intake_id = one(cur, """insert into ops.guidance_intake
        (lane,source_kind,source_ref,statement,state,normalized_contract,captured_by)
        values ('rule','system',%s,%s,'admitted','{}'::jsonb,%s) returning id""",
        (f"db-gate:rule:{suffix}", f"DB gate rule {suffix}", actor_id))
    cur.execute("""insert into ops.rule_admission
        (rule_id,guidance_intake_id,enforcement_class,binding_moment,
         applicability,projection,reachability,input_contract,fixture_refs,
         state,admitted_by,admitted_at,reason)
        values (%s,%s,'judgment_advisory','database acceptance','{}'::jsonb,
                '{}'::jsonb,'{}'::jsonb,'{}'::jsonb,'{}','admitted',%s,now(),
                'rollback-only rule-backed guidance fixture')""",
        (rule_id, intake_id, actor_id))
    cur.execute("""update rule set status='active',activated_by=%s,activated_at=now()
        where id=%s""", (actor_id, rule_id))
    return one(cur, """insert into ops.guidance_item
        (source_rule_id,source_clause,created_by)
        values (%s,%s,%s) returning id""", (rule_id, f"clause {suffix}", actor_id))


def revision(cur: psycopg.Cursor[Any], item_id: Any, actor_id: Any, kind: str = "procedure",
             is_constitution: bool = False) -> Any:
    activation: dict[str, Any] = {
        "trigger": "when the acceptance fixture is invoked",
        "entry_condition": "fixture transaction is open",
    }
    verification: dict[str, Any] = {
        "mechanism": "rollback database gate",
        "completion_condition": "fixture assertion passed",
    }
    delivery: dict[str, Any] = {
        "projection": {
            "constraint": "constraint_enforcement",
            "procedure": "procedure_workflow",
            "doctrine": "doctrine_retrieval",
            "rubric": "verification_rubric",
            "preference": "scoped_preference",
            "precedent": "precedent_search",
            "example": "example_retrieval",
        }[kind]
    }
    if kind == "doctrine":
        activation["situation_mappings"] = ["db-gate"]
        delivery["projection"] = "doctrine_retrieval"
    return one(cur, """insert into ops.guidance_revision
        (guidance_item_id,version,guidance_type,scope,activation,consumer,
         verification,provenance,delivery,is_constitution,classified_by,reason)
        values (%s,1,%s,%s,%s,'database acceptance gate',%s,
                %s,%s,%s,%s,%s) returning id""", (
            item_id, kind, Jsonb({"tenant": "carr", "actor": "joe"}), Jsonb(activation),
            Jsonb(verification), Jsonb({"preserve_source_record": True}), Jsonb(delivery),
            is_constitution, actor_id, "rollback-only fixture"))


def refuses_revision(cur: psycopg.Cursor[Any], item_id: Any, actor_id: Any,
                     kind: str, label: str, *, is_constitution: bool = False) -> None:
    cur.execute("savepoint guidance_gate_revision_refusal")
    try:
        revision(cur, item_id, actor_id, kind, is_constitution)
    except psycopg.Error:
        cur.execute("rollback to savepoint guidance_gate_revision_refusal")
        return
    cur.execute("rollback to savepoint guidance_gate_revision_refusal")
    fail(f"{label} unexpectedly succeeded")


def main() -> int:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("guidance-registry-db-gate: FAIL — DATABASE_URL is required", file=sys.stderr)
        return 1
    try:
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                for relation in TABLES:
                    if one(cur, "select to_regclass(%s)", (relation,)) is None:
                        fail(f"missing relation {relation}")
                for function in FUNCTIONS:
                    if one(cur, "select to_regprocedure(%s)", (function,)) is None:
                        fail(f"missing function {function}")

                # A proposal writer can construct drafts, but it may not mint
                # authority evidence, lifecycle state, active mappings, or a
                # registry activation event by writing tables directly.
                for relation in ("ops.guidance_authority_binding", "ops.guidance_lifecycle_event",
                                 "ops.guidance_situation_mapping", "ops.guidance_registry_event"):
                    if one(cur, "select has_table_privilege('carr_writer',%s,'insert')", (relation,)):
                        fail(f"carr_writer can insert {relation} directly")
                for function in ("ops.record_guidance_decision(uuid,text,text,text)",
                                 "ops.activate_guidance_situation_mapping(uuid,uuid,text)",
                                 "ops.activate_guidance_registry(uuid,text,text,text)"):
                    if one(cur, "select has_function_privilege('carr_writer',%s::regprocedure,'execute')", (function,)):
                        fail(f"carr_writer can execute authority function {function}")
                    if one(cur, "select has_function_privilege('public',%s::regprocedure,'execute')", (function,)):
                        fail(f"PUBLIC can execute authority function {function}")

                # Security-definer authority functions must pin lookup order;
                # otherwise a caller can shadow objects through search_path.
                for function in ("ops.record_guidance_decision(uuid,text,text,text)",
                                 "ops.activate_guidance_situation_mapping(uuid,uuid,text)",
                                 "ops.activate_guidance_registry(uuid,text,text,text)"):
                    definition = str(one(cur, "select pg_get_functiondef(%s::regprocedure)", (function,))).lower()
                    if ("security definer" not in definition
                            or "search_path" not in definition
                            or "pg_temp" not in definition):
                        fail(f"{function} is not a pinned security-definer authority boundary")

                # The real deployment provisions this login externally.  CI's
                # fresh cluster does not, so create it transactionally and use
                # it to prove the session_user-derived human boundary.
                cur.execute("""do $$ begin
                  if not exists (select 1 from pg_roles where rolname='carr_authority_joe') then
                    create role carr_authority_joe login;
                  end if;
                  if not exists (select 1 from pg_roles where rolname='carr_authority_dell') then
                    create role carr_authority_dell login;
                  end if;
                end $$""")
                cur.execute("grant carr_authority to carr_authority_joe")
                cur.execute("grant carr_authority to carr_authority_dell")

                actor_id = one(cur, "select id from actor where slug='joe' and kind='human'")
                procedure_item = item(cur, actor_id, uuid.uuid4().hex)
                procedure_revision = revision(cur, procedure_item, actor_id)
                contract_hash = one(cur, "select ops.guidance_revision_contract_hash(%s)", (procedure_revision,))
                if not isinstance(contract_hash, str) or len(contract_hash) != 64:
                    fail("revision contract hash is not a sha256 digest")

                # A receipt with a mismatching hash must never bind a revision.
                bad_receipt = one(cur, """insert into ops.authority_receipt
                    (idempotency_key,kind,subject_type,subject_id,actor_id,decision,contract_hash)
                    values (%s,'activation','guidance',%s,%s,'approved',%s) returning id""",
                    (f"db-gate-bad-{uuid.uuid4()}", procedure_item, actor_id, "0" * 64))
                refuses(cur, """insert into ops.guidance_authority_binding
                    (guidance_revision_id,authority_receipt_id,contract_hash) values (%s,%s,%s)""",
                         (procedure_revision, bad_receipt, contract_hash), "mismatched receipt hash binding")

                lifecycle_id = authority_one(
                    cur, "select ops.record_guidance_decision(%s,'active',%s,%s)",
                    (procedure_revision, f"db-gate-live-{uuid.uuid4()}", "accept fixture"))
                if lifecycle_id is None:
                    fail("human authority path did not return a lifecycle event")
                binding_id = one(cur, """select authority_binding_id from ops.guidance_lifecycle_event
                    where id=%s""", (lifecycle_id,))
                refuses(cur, "update ops.guidance_authority_binding set contract_hash=%s where id=%s",
                         ("0" * 64, binding_id), "append-only authority binding")
                refuses(cur, "update ops.guidance_revision set reason='rewrite' where id=%s",
                         (procedure_revision,), "append-only revision")

                # Constitution and precedent are rule-backed read paths:
                # standing_guidance and v_guidance_precedent resolve rule rows.
                # Intake-only items may not become either kind, so activation
                # cannot count a constitution row that retrieval drops.
                intake_precedent = item(cur, actor_id, uuid.uuid4().hex)
                refuses_revision(cur, intake_precedent, actor_id, "precedent",
                                 "intake-only precedent revision")
                intake_constitution = item(cur, actor_id, uuid.uuid4().hex)
                refuses_revision(cur, intake_constitution, actor_id, "procedure",
                                 "intake-only constitution revision", is_constitution=True)

                precedent_item = rule_item(cur, actor_id, uuid.uuid4().hex)
                precedent_revision = revision(cur, precedent_item, actor_id, "precedent")
                authority_one(
                    cur, "select ops.record_guidance_decision(%s,'active',%s,%s)",
                    (precedent_revision, f"db-gate-precedent-{uuid.uuid4()}", "precedent fixture"))
                if not one(cur, "select exists(select 1 from ops.v_guidance_precedent "
                                "where decision_id=%s)", (precedent_item,)):
                    fail("rule-backed precedent did not reach v_guidance_precedent")

                constitution_item = rule_item(cur, actor_id, uuid.uuid4().hex)
                constitution_revision = revision(
                    cur, constitution_item, actor_id, is_constitution=True)
                authority_one(
                    cur, "select ops.record_guidance_decision(%s,'active',%s,%s)",
                    (constitution_revision, f"db-gate-constitution-{uuid.uuid4()}",
                     "constitution fixture"))
                if not one(cur, "select exists(select 1 from ops.v_guidance_current "
                                "where guidance_revision_id=%s and is_constitution)",
                           (constitution_revision,)):
                    fail("rule-backed constitution did not reach active guidance")

                # Every validator gets a negative fixture; this keeps an
                # accidental weakening visible even when no live classification exists.
                invalid_item = item(cur, actor_id, uuid.uuid4().hex)
                refuses(cur, """insert into ops.guidance_revision
                    (guidance_item_id,version,guidance_type,scope,activation,consumer,verification,
                     provenance,delivery,classified_by,reason)
                    values (%s,1,'procedure',%s,%s,'x',%s,%s,%s,%s,%s,'bad')""", (
                    invalid_item, Jsonb({"tenant":"carr","actor":"joe"}), Jsonb({"trigger":"x"}),
                    Jsonb({"mechanism":"x"}), Jsonb({"preserve_source_record":True}),
                    Jsonb({"projection":"procedure_workflow"}), actor_id), "procedure without entry/completion")
                for kind, projection in (("doctrine", "doctrine_retrieval"), ("rubric", "verification_rubric"),
                                         ("preference", "scoped_preference"), ("constraint", "constraint_enforcement")):
                    bad = item(cur, actor_id, uuid.uuid4().hex)
                    refuses(cur, """insert into ops.guidance_revision
                      (guidance_item_id,version,guidance_type,scope,activation,consumer,verification,
                       provenance,delivery,classified_by,reason)
                      values (%s,1,%s,%s,%s,'x',%s,%s,%s,%s,%s,'bad')""", (
                        bad, kind, Jsonb({"tenant":"carr","actor":"all"}), Jsonb({"trigger":"x"}),
                        Jsonb({"mechanism":"x"}), Jsonb({"preserve_source_record":True}),
                        Jsonb({"projection":projection}), actor_id), f"invalid {kind} revision")

                # Bridge an approved doctrine revision through a real WR-AI-006
                # concept/section mapping and require it to reach the view.
                bridge_suffix = uuid.uuid4().hex
                document_id = one(cur, """insert into doctrine_document
                    (slug,title,content_class,visibility,created_by)
                    values (%s,%s,'sop','shared',%s) returning id""",
                    (f"guidance-db-gate-{bridge_suffix}", "Guidance DB gate", actor_id))
                section_id = one(cur, """insert into doctrine_section
                    (document_id,section_key,title,ordinal,status,current_version)
                    values (%s,'typed-guidance','Typed guidance',1,'active',1) returning id""",
                    (document_id,))
                body = "typed guidance situation bridge acceptance fixture"
                body_hash = hashlib.sha256(body.encode()).hexdigest()
                doctrine_source_revision = one(cur, """insert into doctrine_revision
                    (section_id,version,actor_id,body,plain_text,content_hash,commit_message)
                    values (%s,1,%s,%s,%s,%s,'guidance db gate') returning id""",
                    (section_id, actor_id, Jsonb({"text": body}), body, body_hash))
                cur.execute("""update doctrine_section
                    set current_revision_id=%s,body_hash=%s where id=%s""",
                    (doctrine_source_revision, body_hash, section_id))
                concept_id = one(cur, """insert into retrieval_concept
                    (concept_key,label,definition,status,proposer_id,approver_id,approved_at)
                    values (%s,'Typed guidance gate','Rollback-only bridge fixture','approved',%s,%s,now())
                    returning id""", (f"guidance-gate-{bridge_suffix}", actor_id, actor_id))
                cur.execute("""insert into doctrine_concept_mapping
                    (concept_id,section_id,role,rationale,status,proposer_id,approver_id,approved_at)
                    values (%s,%s,'governs','rollback-only bridge fixture','approved',%s,%s,now())""",
                    (concept_id, section_id, actor_id, actor_id))
                concept_section = (concept_id, section_id)
                doctrine_item = item(cur, actor_id, uuid.uuid4().hex)
                doctrine_revision = revision(cur, doctrine_item, actor_id, "doctrine")
                doctrine_lifecycle = authority_one(
                    cur, "select ops.record_guidance_decision(%s,'active',%s,%s)",
                    (doctrine_revision, f"db-gate-doctrine-{uuid.uuid4()}", "bridge fixture"))
                doctrine_binding = one(cur, """select authority_binding_id
                    from ops.guidance_lifecycle_event where id=%s""", (doctrine_lifecycle,))
                mapping_id = one(cur, "select ops.propose_guidance_situation_mapping(%s,%s,%s,%s)",
                    (doctrine_revision, concept_section[0], concept_section[1], "bridge fixture"))
                authority_one(cur, "select ops.activate_guidance_situation_mapping(%s,%s,%s)",
                    (mapping_id, doctrine_binding, "bridge approval"))
                if not one(cur, "select exists(select 1 from ops.v_guidance_doctrine_retrieval where guidance_revision_id=%s)",
                           (doctrine_revision,)):
                    diagnostics = cur.execute("""select
                      (select lifecycle_status from ops.v_guidance_revision_state where id=%s),
                      (select state from ops.v_guidance_situation_mapping_current
                        where guidance_revision_id=%s and concept_id=%s and doctrine_section_id=%s),
                      (select status from retrieval_concept where id=%s),
                      (select status from doctrine_concept_mapping
                        where concept_id=%s and section_id=%s)""", (
                        doctrine_revision, doctrine_revision, concept_section[0], concept_section[1],
                        concept_section[0], concept_section[0], concept_section[1])).fetchone()
                    fail(f"approved doctrine revision did not reach the situation bridge: {diagnostics}")
                cur.execute("select * from ops.standing_guidance('joe',null,null,null)").fetchall()

                registry_id = one(cur, "select id from ops.guidance_registry where singleton")
                constitution_count = one(cur, "select count(*) from ops.v_guidance_current where is_constitution")
                if constitution_count > 5:
                    fail("activation fixture inherited more than five constitution rows")
                for number in range(5 - constitution_count):
                    approved_revision = revision(
                        cur, rule_item(cur, actor_id, f"activation-constitution-{uuid.uuid4().hex}"),
                        actor_id, is_constitution=True)
                    authority_one(
                        cur, "select ops.record_guidance_decision(%s,'active',%s,%s)",
                        (approved_revision, f"db-gate-activation-constitution-{number}-{uuid.uuid4()}",
                         "activation constitution fixture"))
                if one(cur, "select count(*) from ops.v_guidance_current where is_constitution") != 5:
                    fail("registry activation fixture did not create five active constitution rows")

                # Build a complete rollback-only target state through the real
                # lifecycle path.  The positive activation case must pass the
                # production coverage function; replacing or stubbing that gate
                # would prove only that activation works when its guard is gone.
                missing_rules = cur.execute("""select r.id from rule r
                    where r.status='active'
                      and not exists (
                        select 1 from ops.v_guidance_current g
                         where g.source_rule_id=r.id and g.is_primary)
                    order by r.id""").fetchall()
                missing_revisions = []
                for number, (rule_id,) in enumerate(missing_rules):
                    covered_item = one(cur, """insert into ops.guidance_item
                        (source_rule_id,source_clause,created_by)
                        values (%s,%s,%s) returning id""",
                        (rule_id, f"rollback coverage fixture {number}", actor_id))
                    missing_revisions.append(revision(cur, covered_item, actor_id))
                for number, covered_revision in enumerate(missing_revisions):
                    authority_one(
                        cur, "select ops.record_guidance_decision(%s,'active',%s,%s)",
                        (covered_revision, f"db-gate-coverage-{number}-{uuid.uuid4()}",
                         "rollback-only complete coverage fixture"))
                coverage_failures = cur.execute(
                    "select source_rule_id,issue from ops.assert_guidance_registry_coverage()"
                ).fetchall()
                if coverage_failures:
                    fail(f"complete activation fixture still has coverage failures: {coverage_failures[:5]}")

                activation_key = f"db-gate-registry-active-{uuid.uuid4()}"
                manifest_digest = "b" * 64
                activation_reason = "full reviewed activation fixture"
                activation_event = authority_one(
                    cur, "select ops.activate_guidance_registry(%s,%s,%s,%s)",
                    (registry_id, manifest_digest, activation_key, activation_reason))
                receipt = cur.execute("""select ar.subject_type,ar.subject_id,ar.actor_id,
                           ar.contract_hash,ar.decision,ge.manifest_digest,ge.reason
                      from ops.authority_receipt ar
                      join ops.guidance_registry_event ge on ge.authority_receipt_id=ar.id
                     where ge.id=%s""", (activation_event,)).fetchone()
                if receipt != ("guidance", registry_id, actor_id, manifest_digest, "approved",
                               manifest_digest, activation_reason):
                    fail(f"registry activation receipt is not exact/session-bound: {receipt}")
                replay_event = authority_one(
                    cur, "select ops.activate_guidance_registry(%s,%s,%s,%s)",
                    (registry_id, manifest_digest, activation_key, activation_reason))
                if replay_event != activation_event:
                    fail("identical registry activation replay returned another event")
                if one(cur, "select count(*) from ops.guidance_registry_event where authority_receipt_id in "
                           "(select id from ops.authority_receipt where idempotency_key=%s)",
                       (activation_key,)) != 1:
                    fail("registry activation replay wrote another event")
                if not one(cur, "select exists(select 1 from ops.standing_guidance("
                                "'joe',null,null,null) where source_rule_id="
                                "(select source_rule_id from ops.guidance_item where id=%s))",
                           (constitution_item,)):
                    fail("rule-backed constitution did not reach standing_guidance after activation")
                authority_refuses(cur, "select ops.activate_guidance_registry(%s,%s,%s,%s)",
                                  (registry_id, manifest_digest, f"db-gate-dell-{uuid.uuid4()}",
                                   activation_reason),
                                  "Dell authority activated Joe-owned registry", actor="dell")
                authority_refuses(cur, "select ops.activate_guidance_registry(%s,%s,%s,%s)",
                                  (registry_id, "c" * 64, activation_key, activation_reason),
                                  "different manifest digest reused registry activation key")

                uncovered_rule = one(cur, """insert into rule
                    (statement,taught_by,scope,status,enforcement)
                    values (%s,%s,'{}'::jsonb,'proposed','prose') returning id""",
                    (f"DB gate uncovered active rule {uuid.uuid4()}", actor_id))
                uncovered_intake = one(cur, """insert into ops.guidance_intake
                    (lane,source_kind,source_ref,statement,state,normalized_contract,captured_by)
                    values ('rule','system',%s,'coverage fixture','admitted','{}'::jsonb,%s)
                    returning id""", (f"db-gate:coverage:{uuid.uuid4()}", actor_id))
                cur.execute("""insert into ops.rule_admission
                    (rule_id,guidance_intake_id,enforcement_class,binding_moment,
                     applicability,projection,reachability,input_contract,fixture_refs,
                     state,admitted_by,admitted_at,reason)
                    values (%s,%s,'judgment_advisory','session boot','{}'::jsonb,
                            '{}'::jsonb,'{}'::jsonb,'{}'::jsonb,'{}','admitted',%s,now(),
                            'rollback-only coverage fixture')""",
                    (uncovered_rule, uncovered_intake, actor_id))
                cur.execute("""update rule set status='active',activated_by=%s,activated_at=now()
                    where id=%s""", (actor_id, uncovered_rule))
                if one(cur, "select count(*) from ops.assert_guidance_registry_coverage()") == 0:
                    fail("coverage unexpectedly passes with an uncovered active rule")

                # Coverage refusal is atomic: it cannot leave behind a receipt
                # that becomes replayable after coverage is repaired.
                coverage_key = f"db-gate-registry-coverage-{uuid.uuid4()}"
                receipts_before = one(cur, "select count(*) from ops.authority_receipt")
                authority_refuses(cur, "select ops.activate_guidance_registry(%s,%s,%s,%s)",
                                  (registry_id, "a" * 64, coverage_key,
                                   "must refuse incomplete registry"),
                                  "registry activation without valid coverage")
                if one(cur, "select count(*) from ops.authority_receipt") != receipts_before:
                    fail("coverage refusal minted a registry authority receipt")
            conn.rollback()
        print("PASS: guidance registry database gate (catalog, privilege, authority, validators, bridge, rollback)")
        return 0
    except Exception as exc:
        print(f"guidance-registry-db-gate: FAIL — {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
