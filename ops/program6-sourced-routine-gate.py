#!/usr/bin/env python3
# ci: db-gate
"""Rollback-only acceptance gate for Program 6 sourced Work Request intake."""

from __future__ import annotations

import hashlib
import os
import sys
import uuid

import psycopg
from psycopg.types.json import Jsonb
from gate_runtime_role import grant_settable_runtime_roles, rollback_only_connection, set_local_role


def fail(message: str) -> int:
    print(f"program6-sourced-routine-gate: FAIL — {message}", file=sys.stderr)
    return 1


def expect_refusal(cur, sql: str, params: tuple, label: str) -> None:
    cur.execute("savepoint program6_refusal")
    try:
        cur.execute(sql, params)
    except psycopg.Error:
        cur.execute("rollback to savepoint program6_refusal")
        return
    cur.execute("rollback to savepoint program6_refusal")
    raise RuntimeError(f"{label} was accepted")


def doctrine_fixture(cur, actor_id: uuid.UUID, *, visibility: str = "shared",
                    owner_actor_id: uuid.UUID | None = None, status: str = "active"):
    key = uuid.uuid4().hex
    doc_id = cur.execute(
        """insert into doctrine_document (slug,title,content_class,visibility,owner_actor_id,created_by)
             values (%s,%s,'reference',%s,%s,%s) returning id""",
        (f"program6-gate-{key}", "Program 6 gate evidence", visibility, owner_actor_id, actor_id),
    ).fetchone()[0]
    section_id = cur.execute(
        """insert into doctrine_section (document_id,section_key,title,ordinal,status,current_version)
             values (%s,'source','Program 6 source',10,%s,1) returning id""",
        (doc_id, status),
    ).fetchone()[0]
    text = "A sourced routine must preserve exact current doctrine evidence."
    revision_id = cur.execute(
        """insert into doctrine_revision (section_id,version,actor_id,body,plain_text,content_hash,commit_message)
             values (%s,1,%s,%s,%s,%s,'Program 6 gate fixture') returning id""",
        (section_id, actor_id, Jsonb({"text": text}), text, hashlib.sha256(text.encode()).hexdigest()),
    ).fetchone()[0]
    cur.execute("update doctrine_section set current_revision_id=%s where id=%s", (revision_id, section_id))
    return section_id, revision_id, f"doctrine:program6-gate-{key}#source"


def capture(cur, section_id, revision_id, origin_ref, key: uuid.UUID, *, title="Explain stale status"):
    return cur.execute(
        """select id,ref,state,version,organization_tenant_id,
                         doctrine_section_id,doctrine_revision_id,doctrine_source_label,replayed
                    from ops.capture_sourced_work_request(%s,%s,%s,%s,%s,%s,%s)""",
        (origin_ref, title, "Show an honest stale state",
         Jsonb([{"id": "STATE-SOURCE", "text": "State names source"}, {"id": "SAFE-ACTION", "text": "No unsafe action is offered"}]),
         section_id, revision_id, key),
    ).fetchone()


def runbook_fixture(cur, actor_id: uuid.UUID) -> tuple[str, uuid.UUID]:
    """Create the browser's exact current shared Program 6 runbook."""
    doc = cur.execute(
        "select id from doctrine_document where slug='runbook' and visibility='shared'"
    ).fetchone()
    if doc:
        doc_id = doc[0]
    else:
        doc_id = cur.execute(
            """insert into doctrine_document (slug,title,content_class,visibility,created_by)
               values ('runbook','Program 6 gate runbook','reference','shared',%s) returning id""",
            (actor_id,),
        ).fetchone()[0]
    key = "diagnosis-checklist-in-order-2-minutes"
    existing = cur.execute(
        """select s.id,
                  s.status='active' and s.current_revision_id=r.id
                  and r.content_hash ~ '^[0-9a-f]{64}$'
                  and encode(digest(r.plain_text,'sha256'),'hex')=r.content_hash
                  and r.body=jsonb_build_object('text',r.plain_text)
             from doctrine_section s
             left join doctrine_revision r
               on r.id=s.current_revision_id and r.section_id=s.id
            where s.document_id=%s and s.section_key=%s""",
        (doc_id, key),
    ).fetchone()
    if existing:
        if existing[1] is not True:
            raise RuntimeError("browser-fixed Program 6 runbook fixture is not exact and current")
        return f"doctrine:runbook#{key}", existing[0]
    section_id = cur.execute(
        """insert into doctrine_section (document_id,section_key,title,ordinal,status,current_version)
           values (%s,%s,'Current-list bounded runbook',999,'active',1) returning id""",
        (doc_id, key),
    ).fetchone()[0]
    text = "Inspect the named evidence, record one bounded result, and stop."
    revision_id = cur.execute(
        """insert into doctrine_revision (section_id,version,actor_id,body,plain_text,content_hash,commit_message)
           values (%s,1,%s,%s,%s,%s,'Program 6 current-list runbook fixture') returning id""",
        (section_id, actor_id, Jsonb({"text": text}), text, hashlib.sha256(text.encode()).hexdigest()),
    ).fetchone()[0]
    cur.execute("update doctrine_section set current_revision_id=%s where id=%s", (revision_id, section_id))
    return f"doctrine:runbook#{key}", section_id


def main() -> int:
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        return fail("DATABASE_URL is required")
    try:
        with rollback_only_connection(dsn) as conn, conn.cursor() as cur:
            joe = cur.execute("select id from actor where slug='joe' and active and kind='human'").fetchone()
            dell = cur.execute("select id from actor where slug='dell' and active and kind='human'").fetchone()
            if not joe or not dell:
                return fail("seeded active human actors joe and dell are required")
            joe_id, dell_id = joe[0], dell[0]
            cur.execute("""do $$ begin
              if not exists (select 1 from pg_roles where rolname='carr_authority_joe') then create role carr_authority_joe login; end if;
            end $$""")
            cur.execute("grant carr_authority to carr_authority_joe")
            grant_settable_runtime_roles(cur, "carr_writer")
            grant_settable_runtime_roles(cur, "carr_authority_joe")
            section_id, revision_id, origin_ref = doctrine_fixture(cur, joe_id)
            set_local_role(cur, "carr_writer")
            created = capture(cur, section_id, revision_id, origin_ref, uuid.uuid4())
            cur.execute("reset role")
            if not created:
                return fail("capture returned no row")
            request_id, ref, state, version, tenant, source_section, source_revision, label, replayed = created
            if not (len(ref) == 9 and ref.startswith("WR-") and ref[3:].isdigit() and state == "captured" and version >= 1
                    and tenant == "carr-internal"
                    and source_section == section_id and source_revision == revision_id
                    and label == "Program 6 source" and replayed is False):
                return fail(f"capture returned the wrong durable shape: {created}")
            stored = cur.execute(
                "select program_key,program_ordinal,state,origin_ref from ops.work_request where id=%s", (request_id,)
            ).fetchone()
            if stored != (None, None, "captured", origin_ref):
                return fail(f"capture changed program semantics or source reference: {stored}")
            set_local_role(cur, "carr_writer")
            expect_refusal(cur,
                """insert into ops.work_request
                   (ref,state,title,requester_actor,capture_idempotency_key,organization_tenant_id,
                    doctrine_section_id,doctrine_revision_id,sourced_capture_sequence,origin_ref)
                   values (%s,'captured','forged','joe',%s,'carr-internal',%s,%s,999999,%s)""",
                (f"WR-FORGED-{uuid.uuid4()}", uuid.uuid4(), section_id, revision_id, origin_ref),
                "direct fully shaped sourced capture")
            expect_refusal(cur,
                "update ops.work_request set title='forged mutation' where id=%s",
                (request_id,), "direct sourced-row mutation")
            cur.execute("reset role")
            privileges = cur.execute(
                "select has_table_privilege('carr_writer','ops.work_request','INSERT'), "
                "has_table_privilege('carr_writer','ops.work_request','UPDATE')"
            ).fetchone()
            if privileges != (False, True):
                return fail(f"writer privileges do not preserve legacy update while closing raw insert: {privileges}")

            replay_key = uuid.uuid4()
            set_local_role(cur, "carr_writer")
            first = capture(cur, section_id, revision_id, origin_ref, replay_key)
            second = capture(cur, section_id, revision_id, origin_ref, replay_key)
            cur.execute("reset role")
            if first[:8] != second[:8] or first[8] is not False or second[8] is not True:
                return fail("identical idempotency replay did not return the original durable request")
            expect_refusal(cur,
                "select * from ops.capture_sourced_work_request(%s,%s,%s,%s,%s,%s,%s)",
                (origin_ref, "Altered title", "Show an honest stale state",
                 Jsonb([{"id": "STATE-SOURCE", "text": "State names source"}, {"id": "SAFE-ACTION", "text": "No unsafe action is offered"}]), section_id, revision_id, replay_key),
                "altered idempotency replay")

            retired_section, retired_revision, retired_origin = doctrine_fixture(cur, joe_id, status="retired")
            expect_refusal(cur, "select * from ops.capture_sourced_work_request(%s,%s,%s,%s,%s,%s,%s)",
                (retired_origin, "Retired", "No", Jsonb([{"id":"RETIRED", "text":"No"}]), retired_section, retired_revision, uuid.uuid4()),
                "retired evidence")
            stale_section, stale_revision, stale_origin = doctrine_fixture(cur, joe_id)
            cur.execute("update doctrine_section set current_revision_id=null,current_version=2 where id=%s", (stale_section,))
            expect_refusal(cur, "select * from ops.capture_sourced_work_request(%s,%s,%s,%s,%s,%s,%s)",
                (stale_origin, "Stale", "No", Jsonb([{"id":"STALE", "text":"No"}]), stale_section, stale_revision, uuid.uuid4()),
                "superseded evidence")
            personal_section, personal_revision, personal_origin = doctrine_fixture(cur, joe_id, visibility="personal", owner_actor_id=joe_id)
            expect_refusal(cur, "select * from ops.capture_sourced_work_request(%s,%s,%s,%s,%s,%s,%s)",
                (personal_origin, "Personal", "No", Jsonb([{"id":"PERSONAL", "text":"No"}]), personal_section, personal_revision, uuid.uuid4()),
                "personal evidence")
            expect_refusal(cur, "select * from ops.capture_sourced_work_request(%s,%s,%s,%s,%s,%s,%s)",
                ("doctrine:invented#evidence", "Invented", "No", Jsonb([{"id":"INVENTED", "text":"No"}]), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()),
                "invented evidence")
            card = cur.execute("select * from ops.work_request_card(%s,%s)", (ref, "carr-internal")).fetchone()
            if not card or card[0] != ref or card[10] is not True:
                return fail("same-tenant requester card did not return current source provenance")
            if cur.execute("select * from ops.work_request_card(%s,%s)", (ref, "other")).fetchone():
                return fail("wrong tenant received a Work Request card")

            # The first-use collection has no client-selected filter and only
            # exposes current shared Program 6 captures that still admit the
            # next bounded action.  The triaged and ready fixtures below use
            # the real typed transitions; no trigger or constraint is bypassed.
            collection_rows = [created]
            set_local_role(cur, "carr_writer")
            for number in range(20):
                collection_rows.append(capture(
                    cur, section_id, revision_id, origin_ref, uuid.uuid4(),
                    title=f"Current collection fixture {number:02d}",
                ))
            cur.execute("reset role")
            if any(not row for row in collection_rows):
                return fail("current collection fixture capture returned no row")
            cur.execute("savepoint program6_current_collection")
            triaged_ref, triaged_version = collection_rows[1][1], collection_rows[1][3]
            cur.execute("set session authorization carr_authority_joe")
            triaged = cur.execute(
                "select * from ops.triage_sourced_work_request(%s,%s,'operational',%s)",
                (triaged_ref, triaged_version, uuid.uuid4()),
            ).fetchone()
            cur.execute("reset session authorization")
            if not triaged or triaged[2] != 'triaged':
                return fail(f"typed triage did not reach triaged: {triaged}")
            # A ready row must arrive through the real proposal + Joe acceptance
            # path, never by disabling the sourced-row trigger or constraints.
            ready_ref, ready_captured_version = collection_rows[2][1], collection_rows[2][3]
            cur.execute("set session authorization carr_authority_joe")
            ready_triaged = cur.execute(
                "select * from ops.triage_sourced_work_request(%s,%s,'operational',%s)",
                (ready_ref, ready_captured_version, uuid.uuid4()),
            ).fetchone()
            cur.execute("reset session authorization")
            if not ready_triaged or ready_triaged[2] != 'triaged':
                return fail(f"typed ready fixture did not first triage: {ready_triaged}")
            runbook_ref, runbook_section_id = runbook_fixture(cur, joe_id)
            set_local_role(cur, "carr_writer")
            ready_plan = cur.execute(
                """select * from ops.propose_sourced_work_request_plan(
                     %s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (ready_ref, ready_triaged[3], "Inspect current evidence and record a bounded result",
                 runbook_ref, Jsonb(["safe:dependency:record-layer"]),
                 "safe:recovery:stop-no-change", "safe:observability:ops-run",
                 Jsonb({"max_steps": 3, "max_duration_minutes": 15}), uuid.uuid4()),
            ).fetchone()
            cur.execute("reset role")
            if not ready_plan:
                return fail("typed ready fixture did not produce a proposal")
            cur.execute("set session authorization carr_authority_joe")
            ready_accepted = cur.execute(
                "select * from ops.accept_sourced_work_request_plan(%s,%s,%s,%s)",
                (ready_ref, ready_triaged[3], ready_plan[2], uuid.uuid4()),
            ).fetchone()
            cur.execute("reset session authorization")
            if not ready_accepted or ready_accepted[2] != 'ready':
                return fail(f"typed ready fixture did not accept its exact plan: {ready_accepted}")
            # A triaged row with a pending plan follows a different card path
            # from an unplanned triaged row.  Both must remain discoverable
            # only while their exact next-step runbook is current.
            planned_ref, planned_captured_version = collection_rows[3][1], collection_rows[3][3]
            cur.execute("set session authorization carr_authority_joe")
            planned_triaged = cur.execute(
                "select * from ops.triage_sourced_work_request(%s,%s,'operational',%s)",
                (planned_ref, planned_captured_version, uuid.uuid4()),
            ).fetchone()
            cur.execute("reset session authorization")
            if not planned_triaged or planned_triaged[2] != 'triaged':
                return fail(f"typed pending-plan fixture did not first triage: {planned_triaged}")
            set_local_role(cur, "carr_writer")
            pending_plan = cur.execute(
                """select * from ops.propose_sourced_work_request_plan(
                     %s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (planned_ref, planned_triaged[3], "Inspect current evidence and record a bounded result",
                 runbook_ref, Jsonb(["safe:dependency:record-layer"]),
                 "safe:recovery:stop-no-change", "safe:observability:ops-run",
                 Jsonb({"max_steps": 3, "max_duration_minutes": 15}), uuid.uuid4()),
            ).fetchone()
            cur.execute("reset role")
            if not pending_plan:
                return fail("typed pending-plan fixture did not produce a proposal")
            before_collection_count = cur.execute("select count(*) from ops.work_request").fetchone()[0]
            current_rows = cur.execute(
                "select * from ops.current_sourced_work_requests(%s)", ("carr-internal",)
            ).fetchall()
            after_collection_count = cur.execute("select count(*) from ops.work_request").fetchone()[0]
            repeated_current_rows = cur.execute(
                "select * from ops.current_sourced_work_requests(%s)", ("carr-internal",)
            ).fetchall()
            if after_collection_count != before_collection_count:
                return fail("current collection had a database side effect")
            if repeated_current_rows != current_rows:
                return fail("current collection ordering was not deterministic")
            if len(current_rows) != 20:
                return fail(f"current collection did not enforce its 20-row cap: {len(current_rows)}")
            if any(len(row) != 6 for row in current_rows):
                return fail(f"current collection leaked a non-six-column projection: {current_rows}")
            if [row[0] for row in current_rows] != [row[0] for row in repeated_current_rows]:
                return fail("current collection did not retain an exact deterministic prefix order")
            returned_refs = {row[0] for row in current_rows}
            required_states = {
                created[1]: "captured",
                triaged_ref: "triaged",
                ready_ref: "ready",
                planned_ref: "triaged",
            }
            returned_states = {row[0]: row[2] for row in current_rows}
            if (any(returned_states.get(ref_value) != state
                    for ref_value, state in required_states.items()) or
                    any(row[4] != "current" or not row[5] for row in current_rows)):
                return fail(f"current collection omitted an eligible state or included an unrelated row: {current_rows}")
            if cur.execute("select * from ops.current_sourced_work_requests(%s)", ("other",)).fetchone():
                return fail("current collection returned another tenant's Work Request")
            cur.execute("savepoint program6_current_runbook_refusal")
            cur.execute(
                "update doctrine_section set current_revision_id=null where id=%s",
                (runbook_section_id,),
            )
            runbook_stale_refs = {row[0] for row in cur.execute(
                "select * from ops.current_sourced_work_requests(%s)", ("carr-internal",)
            ).fetchall()}
            if triaged_ref in runbook_stale_refs or planned_ref in runbook_stale_refs:
                return fail("current collection returned triaged work with a stale next-step runbook")
            if created[1] not in runbook_stale_refs or ready_ref not in runbook_stale_refs:
                return fail("runbook staleness incorrectly hid captured or ready work")
            cur.execute("rollback to savepoint program6_current_runbook_refusal")
            cur.execute("savepoint program6_current_later_state_refusal")
            cur.execute("alter table ops.work_request drop constraint work_request_sourced_capture_shape")
            cur.execute("alter table ops.work_request disable trigger sourced_work_request_is_immutable")
            cur.execute("update ops.work_request set state='needs_joe' where id=%s", (request_id,))
            if ref in {row[0] for row in cur.execute(
                "select * from ops.current_sourced_work_requests(%s)", ("carr-internal",)
            ).fetchall()}:
                return fail("current collection returned a non-actionable later-state Work Request")
            cur.execute("rollback to savepoint program6_current_later_state_refusal")
            cur.execute("rollback to savepoint program6_current_collection")

            # A current source is not merely a title.  A stale revision or a
            # nonshared document must disappear from the collection even when
            # its otherwise valid Program 6 capture remains in the table.
            cur.execute("savepoint program6_current_source_refusals")
            cur.execute("update doctrine_section set current_revision_id=null where id=%s", (section_id,))
            if ref in {row[0] for row in cur.execute(
                "select * from ops.current_sourced_work_requests(%s)", ("carr-internal",)
            ).fetchall()}:
                return fail("current collection returned a stale source revision")
            cur.execute("rollback to savepoint program6_current_source_refusals")
            cur.execute("savepoint program6_current_shared_refusal")
            cur.execute(
                "update doctrine_document set visibility='personal' where id=(select document_id from doctrine_section where id=%s)",
                (section_id,),
            )
            if ref in {row[0] for row in cur.execute(
                "select * from ops.current_sourced_work_requests(%s)", ("carr-internal",)
            ).fetchall()}:
                return fail("current collection returned a nonshared source")
            cur.execute("rollback to savepoint program6_current_shared_refusal")
            cur.execute("savepoint program6_current_legacy_refusal")
            legacy_ref = f"WR-{uuid.uuid4().int % 10**9:09d}"
            cur.execute(
                """insert into ops.work_request
                   (ref,state,title,requester_actor,organization_tenant_id,captured_at)
                   values (%s,'captured','legacy uncaptured row','joe',null,'2000-01-01')""",
                (legacy_ref,),
            )
            if legacy_ref in {row[0] for row in cur.execute(
                "select * from ops.current_sourced_work_requests(%s)", ("carr-internal",)
            ).fetchall()}:
                return fail("current collection returned an unsourced legacy Work Request")
            cur.execute("rollback to savepoint program6_current_legacy_refusal")
            privileges = cur.execute(
                "select has_function_privilege('carr_reader','ops.current_sourced_work_requests(text)','EXECUTE'), "
                "has_function_privilege('carr_writer','ops.current_sourced_work_requests(text)','EXECUTE'), "
                "has_function_privilege('public','ops.current_sourced_work_requests(text)','EXECUTE'), "
                "has_function_privilege('carr_jobs','ops.current_sourced_work_requests(text)','EXECUTE'), "
                "has_function_privilege('carr_authority','ops.current_sourced_work_requests(text)','EXECUTE')"
            ).fetchone()
            if privileges != (True, True, False, False, False):
                return fail(f"current collection function grants are not least privilege: {privileges}")
            grant_settable_runtime_roles(cur, "carr_reader")
            set_local_role(cur, "carr_reader")
            reader_rows = cur.execute(
                "select * from ops.current_sourced_work_requests(%s)", ("carr-internal",)
            ).fetchall()
            cur.execute("reset role")
            if not reader_rows:
                return fail("carr_reader could not execute the current collection function")
            cur.execute("savepoint program6_later_state_card")
            cur.execute("alter table ops.work_request drop constraint work_request_sourced_capture_shape")
            cur.execute("alter table ops.work_request disable trigger sourced_work_request_is_immutable")
            cur.execute("update ops.work_request set state='needs_joe' where id=%s", (request_id,))
            if cur.execute("select * from ops.work_request_card(%s,%s)", (ref, "carr-internal")).fetchone():
                return fail("later-state Work Request returned a card")
            cur.execute("rollback to savepoint program6_later_state_card")
        print("PASS: Program 6 sourced Work Request capture is current, scoped, idempotent, and captured-only")
        return 0
    except Exception as exc:
        return fail(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
