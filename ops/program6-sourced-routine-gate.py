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


def main() -> int:
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        return fail("DATABASE_URL is required")
    try:
        with psycopg.connect(dsn, autocommit=False) as conn, conn.cursor() as cur:
            joe = cur.execute("select id from actor where slug='joe' and active and kind='human'").fetchone()
            dell = cur.execute("select id from actor where slug='dell' and active and kind='human'").fetchone()
            if not joe or not dell:
                return fail("seeded active human actors joe and dell are required")
            joe_id, dell_id = joe[0], dell[0]
            section_id, revision_id, origin_ref = doctrine_fixture(cur, joe_id)
            cur.execute("set local role carr_writer")
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
            cur.execute("set local role carr_writer")
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
            cur.execute("set local role carr_writer")
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
            cur.execute("savepoint program6_later_state_card")
            cur.execute("alter table ops.work_request drop constraint work_request_sourced_capture_shape")
            cur.execute("alter table ops.work_request disable trigger sourced_work_request_is_immutable")
            cur.execute("update ops.work_request set state='needs_joe' where id=%s", (request_id,))
            if cur.execute("select * from ops.work_request_card(%s,%s)", (ref, "carr-internal")).fetchone():
                return fail("later-state Work Request returned a card")
            cur.execute("rollback to savepoint program6_later_state_card")
            conn.rollback()
        print("PASS: Program 6 sourced Work Request capture is current, scoped, idempotent, and captured-only")
        return 0
    except Exception as exc:
        return fail(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
