#!/usr/bin/env python3
# ci: db-gate
"""Rollback-only acceptance gate for Program 6 sourced outcome feedback."""

from __future__ import annotations

import hashlib
import os
import sys
import uuid

import psycopg
from psycopg.types.json import Jsonb


def fail(message: str) -> int:
    print(f"program6-outcome-feedback-gate: FAIL — {message}", file=sys.stderr)
    return 1


def one(cur, sql: str, params: tuple = ()):
    row = cur.execute(sql, params).fetchone()
    if row is None:
        raise RuntimeError("required gate fixture row was not returned")
    return row


def refusal(cur, sql: str, params: tuple = (), label: str = "unsafe operation") -> None:
    cur.execute("savepoint program6_outcome_refusal")
    try:
        cur.execute(sql, params)
    except psycopg.Error:
        cur.execute("rollback to savepoint program6_outcome_refusal")
        return
    cur.execute("rollback to savepoint program6_outcome_refusal")
    raise RuntimeError(f"{label} was accepted")


def authority_roles(cur) -> None:
    cur.execute("""do $$ begin
      if not exists (select 1 from pg_roles where rolname='carr_authority_joe') then
        create role carr_authority_joe login;
      end if;
      if not exists (select 1 from pg_roles where rolname='carr_authority_dell') then
        create role carr_authority_dell login;
      end if;
    end $$""")
    cur.execute("grant carr_authority to carr_authority_joe,carr_authority_dell")
    cur.execute("""do $$ begin
      execute format('grant carr_authority_joe,carr_authority_dell,carr_writer,carr_reader,carr_jobs to %I', current_user);
    end $$""")


def fixture(cur, actor_id: uuid.UUID):
    token = uuid.uuid4().hex
    source_doc = one(cur, """insert into doctrine_document
      (slug,title,content_class,visibility,created_by)
      values (%s,'Outcome feedback source','reference','shared',%s) returning id""",
      (f"program6-outcome-{token}", actor_id))[0]
    source_section = one(cur, """insert into doctrine_section
      (document_id,section_key,title,ordinal,status,current_version)
      values (%s,'source','Outcome feedback source',1,'active',1) returning id""", (source_doc,))[0]
    source_text = "An observed outcome is evidence, not authority to execute another step."
    source_rev = one(cur, """insert into doctrine_revision
      (section_id,version,actor_id,body,plain_text,content_hash,commit_message)
      values (%s,1,%s,%s,%s,%s,'Program 6 feedback source fixture') returning id""",
      (source_section, actor_id, Jsonb({"text": source_text}), source_text,
       hashlib.sha256(source_text.encode()).hexdigest()))[0]
    cur.execute("update doctrine_section set current_revision_id=%s where id=%s", (source_rev, source_section))
    runbook_doc = cur.execute("select id from doctrine_document where slug='runbook' and visibility='shared'").fetchone()
    if not runbook_doc:
        runbook_doc = one(cur, """insert into doctrine_document
          (slug,title,content_class,visibility,created_by)
          values ('runbook','Runbook','reference','shared',%s) returning id""", (actor_id,))
    runbook_key = f"program6-outcome-{token}"
    runbook_section = one(cur, """insert into doctrine_section
      (document_id,section_key,title,ordinal,status,current_version)
      values (%s,%s,'Outcome feedback runbook',999,'active',1) returning id""",
      (runbook_doc[0], runbook_key))[0]
    runbook_text = "Observe the named criterion, preserve evidence references, and stop."
    runbook_rev = one(cur, """insert into doctrine_revision
      (section_id,version,actor_id,body,plain_text,content_hash,commit_message)
      values (%s,1,%s,%s,%s,%s,'Program 6 feedback runbook fixture') returning id""",
      (runbook_section, actor_id, Jsonb({"text": runbook_text}), runbook_text,
       hashlib.sha256(runbook_text.encode()).hexdigest()))[0]
    cur.execute("update doctrine_section set current_revision_id=%s where id=%s", (runbook_rev, runbook_section))
    return source_section, source_rev, f"doctrine:program6-outcome-{token}#source", f"doctrine:runbook#{runbook_key}"


def as_authority(cur, actor: str, sql: str, params: tuple):
    cur.execute(f"set session authorization carr_authority_{actor}")
    try:
        return cur.execute(sql, params).fetchone()
    finally:
        cur.execute("reset session authorization")


def propose(cur, ref: str, version: int, plan_hash: str, key: uuid.UUID, outcome: str = "met", summary: str = "Observed the bounded result"):
    result = Jsonb([{"id": "OBSERVED", "result": outcome}])
    return cur.execute("""select * from ops.propose_sourced_work_request_outcome_feedback(
      %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
      (ref, version, plan_hash, result, Jsonb(["safe:evidence:observed-result"]),
       "none" if outcome == "met" else "criterion_not_met", summary,
       12, "mcp", False, 0, key)).fetchone()


def accept(cur, ref: str, version: int, feedback_hash: str, key: uuid.UUID, actor: str):
    return as_authority(cur, actor,
      "select * from ops.accept_sourced_work_request_outcome_feedback(%s,%s,%s,%s)",
      (ref, version, feedback_hash, key))


def main() -> int:
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        return fail("DATABASE_URL is required")
    try:
        with psycopg.connect(dsn, autocommit=False) as conn, conn.cursor() as cur:
            authority_roles(cur)
            joe_id = one(cur, "select id from actor where slug='joe' and active and kind='human'")[0]
            source_section, source_rev, origin_ref, runbook_ref = fixture(cur, joe_id)
            cur.execute("set local role carr_writer")
            request_id, ref, _, captured_version, *_ = one(cur, """select * from ops.capture_sourced_work_request(
              %s,%s,%s,%s,%s,%s,%s)""", (origin_ref, "Outcome feedback", "Observe one result",
              Jsonb([{"id": "OBSERVED", "text": "The stated outcome has an evidence reference"}]),
              source_section, source_rev, uuid.uuid4()))
            cur.execute("reset role")
            triaged = as_authority(cur, "dell", "select * from ops.triage_sourced_work_request(%s,%s,'operational',%s)", (ref, captured_version, uuid.uuid4()))
            triaged_version = triaged[3]
            cur.execute("set local role carr_writer")
            plan = one(cur, """select * from ops.propose_sourced_work_request_plan(
              %s,%s,%s,%s,%s,%s,%s,%s,%s)""", (ref, triaged_version, "Observe one bounded result",
              runbook_ref, Jsonb([]), "safe:recovery:stop", "safe:observability:record",
              Jsonb({"max_steps": 2, "max_duration_minutes": 15}), uuid.uuid4()))
            cur.execute("reset role")
            ready = as_authority(cur, "dell", "select * from ops.accept_sourced_work_request_plan(%s,%s,%s,%s)",
              (ref, triaged_version, plan[2], uuid.uuid4()))
            ready_version, plan_hash = ready[3], plan[2]
            if ready[2] != "ready":
                raise RuntimeError(f"ready plan fixture failed: {ready}")

            # Routine writer may append a proposal but cannot write private rows or move work.
            proposal_a_key = uuid.uuid4()
            cur.execute("set local role carr_writer")
            proposal_a = propose(cur, ref, ready_version, plan_hash, proposal_a_key)
            replay_a = propose(cur, ref, ready_version, plan_hash, proposal_a_key)
            refusal(cur, "insert into ops.sourced_work_request_outcome_feedback default values", label="raw writer feedback proposal insert")
            refusal(cur, "insert into ops.sourced_work_request_outcome_feedback_acceptance_receipt default values", label="raw writer feedback receipt insert")
            refusal(cur, "update ops.work_request set state='claimed' where id=%s", (request_id,), "writer lifecycle transition")
            refusal(cur, """select * from ops.propose_sourced_work_request_outcome_feedback(
              %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
              (ref, ready_version, plan_hash, Jsonb([]), Jsonb(["safe:evidence:observed-result"]), "none",
               "bad criterion set", 12, "mcp", False, 0, uuid.uuid4()), "partial criterion result set")
            refusal(cur, """select * from ops.propose_sourced_work_request_outcome_feedback(
              %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
              (ref, ready_version, plan_hash, Jsonb([{"id":"OBSERVED","result":"met"}]),
               Jsonb(["safe:evidence:observed-result"]), "system_error", "inconsistent blocker",
               12, "mcp", False, 0, uuid.uuid4()), "inconsistent blocker")
            cur.execute("reset role")
            if proposal_a[-1] is not False or replay_a[:-1] != proposal_a[:-1] or replay_a[-1] is not True:
                raise RuntimeError("proposal did not preserve exact idempotent readback")
            if proposal_a[5:7] != ("ready", ready_version):
                raise RuntimeError(f"proposal changed or misreported Work Request state: {proposal_a}")

            card = one(cur, "select * from ops.work_request_card(%s,'carr-internal')", (ref,))
            if card[28] is not None or card[29] != [] or card[30] != 0:
                raise RuntimeError(f"pending outcome proposal leaked into card: {card[28:31]}")
            # The ready-plan accepter may also accept outcome feedback: no invented two-human gate.
            accepted_a_key = uuid.uuid4()
            accepted_a = accept(cur, ref, ready_version, proposal_a[2], accepted_a_key, "dell")
            replay_accepted_a = accept(cur, ref, ready_version, proposal_a[2], accepted_a_key, "dell")
            if accepted_a[2:4] != ("ready", ready_version) or accepted_a[-1] is not False:
                raise RuntimeError(f"acceptance advanced a ready Work Request: {accepted_a}")
            if replay_accepted_a[:-1] != accepted_a[:-1] or replay_accepted_a[-1] is not True:
                raise RuntimeError("acceptance did not preserve exact idempotent readback")
            refusal(cur, "select * from ops.accept_sourced_work_request_outcome_feedback(%s,%s,%s,%s)",
              (ref, ready_version, proposal_a[2], accepted_a_key), "cross-human acceptance replay")

            cur.execute("set local role carr_writer")
            proposal_b = propose(cur, ref, ready_version, plan_hash, uuid.uuid4(), summary="A later observed trial")
            cur.execute("reset role")
            card_pending_b = one(cur, "select * from ops.work_request_card(%s,'carr-internal')", (ref,))
            if card_pending_b[28]["feedback_ref"] != proposal_a[1] or card_pending_b[29] != [card_pending_b[28]] or card_pending_b[30] != 1:
                raise RuntimeError(f"pending B leaked or accepted history is wrong: {card_pending_b[28:31]}")
            accepted_b = accept(cur, ref, ready_version, proposal_b[2], uuid.uuid4(), "dell")
            card_b = one(cur, "select * from ops.work_request_card(%s,'carr-internal')", (ref,))
            history = card_b[29]
            if card_b[28]["feedback_ref"] != proposal_b[1] or card_b[30] != 2 or [x["feedback_ref"] for x in history] != [proposal_a[1], proposal_b[1]]:
                raise RuntimeError(f"accepted history is not deterministic A then B: {card_b[28:31]}")

            # Replays are facts about the original accepted proposal/receipt, not a claim that a later lifecycle remains ready.
            cur.execute("savepoint historical_replay")
            cur.execute("alter table ops.work_request drop constraint work_request_sourced_capture_shape")
            cur.execute("alter table ops.work_request disable trigger sourced_work_request_is_immutable")
            cur.execute("update ops.work_request set state='claimed',version=version+1 where id=%s", (request_id,))
            cur.execute("alter table ops.work_request enable trigger sourced_work_request_is_immutable")
            historical_proposal = propose(cur, ref, ready_version, plan_hash, proposal_a_key)
            historical_acceptance = accept(cur, ref, ready_version, proposal_a[2], accepted_a_key, "dell")
            refusal(cur, """select * from ops.propose_sourced_work_request_outcome_feedback(
              %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
              (ref, ready_version, plan_hash, Jsonb([{"id":"OBSERVED","result":"met"}]),
               Jsonb(["safe:evidence:observed-result"]), "none", "changed replay", 12, "mcp", False, 0, proposal_a_key),
              "changed historical proposal replay")
            if historical_proposal[5:7] != ("ready", ready_version) or historical_acceptance[2:4] != ("ready", ready_version):
                raise RuntimeError("historical replay did not preserve the original ready version")
            cur.execute("rollback to savepoint historical_replay")
            conn.rollback()
    except Exception as exc:
        return fail(str(exc))
    print("program6-outcome-feedback-gate: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
