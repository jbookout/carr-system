#!/usr/bin/env python3
# ci: db-gate
"""Rollback-only adversarial proof for calendar-prebrief projection 0216."""
import os
import uuid
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlparse

import psycopg
from psycopg.types.json import Jsonb


def required(cur: psycopg.Cursor[Any], label: str) -> tuple[Any, ...]:
    row = cur.fetchone()
    if row is None:
        raise RuntimeError(f"{label} returned no row")
    return tuple(row)


def refused(cur: psycopg.Cursor[Any], sql: str, args: tuple[Any, ...], state: str, text: str) -> None:
    cur.execute("savepoint expected_refusal")
    try:
        cur.execute(sql, args)
        raise RuntimeError(f"expected refusal was accepted: {text}")
    except psycopg.Error as exc:
        if exc.sqlstate != state or text not in str(exc):
            raise
        cur.execute("rollback to savepoint expected_refusal")


def job(cur: psycopg.Cursor[Any], definition_key: str, lease: uuid.UUID, scheduled_offset: str = "0 seconds") -> tuple[uuid.UUID, datetime]:
    identifier = uuid.uuid4()
    cur.execute(
        """insert into ops.job
             (id,definition_key,definition_version,idempotency_key,scheduled_for,mode,state,
              attempt,max_attempts,next_attempt_at,lease_owner,lease_token,leased_until,timeout_seconds)
           values (%s,%s,1,%s,now()+(%s)::interval,'live','running',1,1,now(),
                   'calendar-prebrief-local',%s,now()+interval '5 minutes',60)
           returning id,scheduled_for""",
        (identifier, definition_key, str(identifier), scheduled_offset, lease),
    )
    row = required(cur, "calendar prebrief job")
    return row[0], row[1]


def payload_data(calendar_key: str, occurrence_key: str, participant_ref: str, scheduled_for: datetime,
                 *, title: str = "Prebrief acceptance", location: str = "CARR") -> list[dict[str, Any]]:
    starts = scheduled_for + timedelta(hours=1)
    ends = starts + timedelta(hours=1)
    return [{
        "calendar_key": calendar_key,
        "event_key": "e" * 64,
        "occurrence_key": occurrence_key,
        "starts_at": starts.isoformat(),
        "ends_at": ends.isoformat(),
        "title": title,
        "location": location,
        "participant_refs": [participant_ref],
    }]


def ingest_sql() -> str:
    return "select (ops.ingest_calendar_prebrief_projection(%s,%s,%s,%s)).id"


dsn = os.environ.get("CARR_LOCAL_PG_DSN") or os.environ.get("DATABASE_URL", "")
parsed = urlparse(dsn)
if parsed.scheme not in {"postgres", "postgresql"} or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
    raise RuntimeError("calendar prebrief projection acceptance requires loopback CARR_LOCAL_PG_DSN or DATABASE_URL")

with psycopg.connect(dsn) as conn, conn.cursor() as cur:
    cur.execute("begin")
    cur.execute(
        """do $$ begin
             if not exists(select 1 from pg_roles where rolname='carr_authority_joe') then create role carr_authority_joe login; end if;
             if not exists(select 1 from pg_roles where rolname='carr_authority_dell') then create role carr_authority_dell login; end if;
             if not exists(select 1 from pg_roles where rolname='carr_calendar_prebrief_joe') then create role carr_calendar_prebrief_joe login; end if;
             if not exists(select 1 from pg_roles where rolname='carr_calendar_prebrief_dell') then create role carr_calendar_prebrief_dell login; end if;
             if not exists(select 1 from pg_roles where rolname='carr_calendar_prebrief_other') then create role carr_calendar_prebrief_other login; end if;
           end $$"""
    )
    cur.execute("grant carr_authority to carr_authority_joe,carr_authority_dell")
    cur.execute("grant carr_calendar_prebrief_jobs to carr_calendar_prebrief_joe,carr_calendar_prebrief_dell,carr_calendar_prebrief_other")
    cur.execute(
        """select key,owner_actor,inventory_contract->>'owner',enabled,recurrence->>'cron',legacy_schedule->>'status'
             from ops.job_definition
             where key in ('calendar-prebrief-projection-joe-daily','calendar-prebrief-projection-dell-daily') and version=1 order by key"""
    )
    expected_defs = [
        ("calendar-prebrief-projection-dell-daily", "dell", "dell", False, "30 6 * * 1-5", "disabled"),
        ("calendar-prebrief-projection-joe-daily", "joe", "joe", False, "30 6 * * 1-5", "disabled"),
    ]
    actual_defs = cur.fetchall()
    if actual_defs != expected_defs:
        raise RuntimeError(f"calendar prebrief definitions must remain separate, disabled, owner-matched, and before 06:45: {actual_defs!r}")

    # Authority may replace only its own opaque set.  Neither source IDs nor a
    # caller-selected sponsor make the function boundary.
    joe_allowed, dell_allowed, alien = "a" * 64, "b" * 64, "c" * 64
    cur.execute("set session authorization carr_authority_joe")
    cur.execute("select (ops.replace_calendar_prebrief_allowlist(%s)).sponsor", ([joe_allowed],))
    if required(cur, "Joe allowlist receipt") != ("joe",):
        raise RuntimeError("allowlist did not derive Joe from authority identity")
    refused(cur, "select ops.replace_calendar_prebrief_allowlist(%s)", ([],), "22023", "nonempty distinct opaque")
    refused(cur, "select ops.replace_calendar_prebrief_allowlist(%s)", ([joe_allowed, joe_allowed],), "22023", "nonempty distinct opaque")
    cur.execute("reset session authorization")
    cur.execute("set session authorization carr_authority_dell")
    cur.execute("select (ops.replace_calendar_prebrief_allowlist(%s)).sponsor", ([dell_allowed],))
    if required(cur, "Dell allowlist receipt") != ("dell",):
        raise RuntimeError("allowlist did not derive Dell from authority identity")
    cur.execute("reset session authorization")
    cur.execute("select active_revision_id from ops.calendar_prebrief_allowed_calendar where sponsor='dell'")
    dell_revision_a = required(cur, "initial Dell allowlist revision")[0]

    cur.execute("select id from actor where active order by slug limit 1")
    actor = required(cur, "active acceptance actor")[0]
    cur.execute("select slug from client_status order by sort limit 1")
    status = required(cur, "acceptance client status")[0]
    refs: list[str] = []
    for suffix in ("one", "two"):
        ref = f"C-CALENDAR-PREBREF-{suffix.upper()}-{uuid.uuid4().hex[:12]}"
        cur.execute("insert into party(kind,name,created_by,updated_by) values('person',%s,%s,%s) returning id",
                    (f"Calendar Prebrief {suffix}", actor, actor))
        party = required(cur, "acceptance party")[0]
        cur.execute("insert into client(roster_ref,party_id,status,created_by,updated_by) values(%s,%s,%s,%s,%s)",
                    (ref, party, status, actor, actor))
        refs.append(ref)
    ref, second_ref = refs
    joe_key, dell_key = "calendar-prebrief-projection-joe-daily", "calendar-prebrief-projection-dell-daily"
    first_lease = uuid.uuid4()
    first_job, first_snapshot = job(cur, joe_key, first_lease)
    first_events = Jsonb(payload_data(joe_allowed, "1" * 64, ref, first_snapshot))
    first_args = (first_job, first_lease, [joe_allowed], first_events)

    cur.execute("set session authorization carr_calendar_prebrief_joe")
    cur.execute(ingest_sql(), first_args)
    first_receipt = required(cur, "first projection receipt")[0]
    cur.execute(ingest_sql(), first_args)
    if required(cur, "idempotent replay")[0] != first_receipt:
        raise RuntimeError("exact job-attempt replay did not return its immutable receipt")
    cur.execute("reset session authorization")
    cur.execute("set session authorization carr_calendar_prebrief_other")
    refused(cur, ingest_sql(), first_args, "42501", "named externally provisioned execution identity")
    cur.execute("reset session authorization")
    with psycopg.connect(dsn) as race_conn, race_conn.cursor() as race_cur:
        race_cur.execute("select pg_try_advisory_xact_lock(hashtextextended('calendar-prebrief-projection:joe',0))")
        if required(race_cur, "advisory race fence") != (False,):
            raise RuntimeError("sponsor replacement is not serialized by its advisory race fence")
        race_conn.rollback()
    cur.execute("select job_id,attempt,snapshot_at,event_count,participant_count from ops.calendar_prebrief_projection_receipt where id=%s", (first_receipt,))
    receipt_row = required(cur, "first immutable receipt")
    if receipt_row[:2] != (first_job, 1) or receipt_row[2] != first_snapshot or receipt_row[3:] != (1, 1):
        raise RuntimeError("receipt is not bound to job attempt, DB-derived snapshot, and counts")

    # P0 failures: wrong identity/lease/clock/allowlist cannot reach replacement.
    dell_lease = uuid.uuid4(); dell_job, dell_snapshot = job(cur, dell_key, dell_lease, "1 second")
    cur.execute("set session authorization carr_calendar_prebrief_joe")
    refused(cur, ingest_sql(), (dell_job, dell_lease, [dell_allowed], Jsonb(payload_data(dell_allowed, "2" * 64, second_ref, dell_snapshot))), "42501", "does not match the static job owner")
    refused(cur, ingest_sql(), (first_job, uuid.uuid4(), [joe_allowed], first_events), "55000", "current live job lease")
    cur.execute("reset session authorization")
    cur.execute("set session authorization carr_calendar_prebrief_dell")
    refused(cur, ingest_sql(), first_args, "42501", "does not match the static job owner")
    cur.execute("reset session authorization")
    far_lease = uuid.uuid4(); far_job, far_snapshot = job(cur, joe_key, far_lease, "31 minutes")
    cur.execute("set session authorization carr_calendar_prebrief_joe")
    refused(cur, ingest_sql(), (far_job, far_lease, [joe_allowed], Jsonb(payload_data(joe_allowed, "3" * 64, ref, far_snapshot))), "22023", "DB-clock window")
    refused(cur, ingest_sql(), (first_job, first_lease, [alien], first_events), "22023", "exact DB allowlist coverage")

    # P1 content-shape failures: all are evaluated before any current rows move.
    duplicate_refs = payload_data(joe_allowed, "4" * 64, ref, first_snapshot)
    duplicate_refs[0]["participant_refs"] = [ref, ref]
    refused(cur, ingest_sql(), (first_job, first_lease, [joe_allowed], Jsonb(duplicate_refs)), "22023", "duplicate participant refs")
    for index, unsafe in enumerate(("zoommtg://private", "teams:meeting", "sip:private@example.org", "www.private.example", "join meeting id 123456"), start=5):
        refused(cur, ingest_sql(), (first_job, first_lease, [joe_allowed], Jsonb(payload_data(joe_allowed, f"{index:x}".zfill(64), ref, first_snapshot, title=unsafe))), "22023", "email, URI, www, or join locator")
    unexpected = payload_data(joe_allowed, "a" * 64, ref, first_snapshot)
    unexpected[0]["description"] = "not permitted"
    refused(cur, ingest_sql(), (first_job, first_lease, [joe_allowed], Jsonb(unexpected)), "22023", "outside its bounded contract")
    unresolved = payload_data(joe_allowed, "b" * 64, "P-NOT-RESOLVED", first_snapshot)
    refused(cur, ingest_sql(), (first_job, first_lease, [joe_allowed], Jsonb(unresolved)), "22023", "does not resolve uniquely")
    too_many = [payload_data(joe_allowed, f"{index:x}".zfill(64), ref, first_snapshot)[0] for index in range(129)]
    refused(cur, ingest_sql(), (first_job, first_lease, [joe_allowed], Jsonb(too_many)), "22023", "event count exceeds")
    cur.execute("reset session authorization")
    cur.execute("select count(*) from ops.calendar_prebrief_projection_event where sponsor='joe'")
    if required(cur, "projection retained after all prevalidation failures") != (1,):
        raise RuntimeError("failed prevalidation changed Joe's current projection")

    # A later current projection makes a near-past scheduled job stale; empty
    # snapshots are valid and atomically prune only that sponsor's current rows.
    cur.execute("set session authorization carr_calendar_prebrief_dell")
    dell_events = payload_data(dell_allowed, "d" * 64, second_ref, dell_snapshot)
    no_participant = payload_data(dell_allowed, "e" * 64, second_ref, dell_snapshot)[0]
    no_participant["participant_refs"] = []
    cur.execute(ingest_sql(), (dell_job, dell_lease, [dell_allowed], Jsonb(dell_events + [no_participant])))
    cur.execute("reset session authorization")
    stale_lease = uuid.uuid4(); stale_job, stale_snapshot = job(cur, joe_key, stale_lease, "-10 minutes")
    cur.execute("set session authorization carr_calendar_prebrief_joe")
    refused(cur, ingest_sql(), (stale_job, stale_lease, [joe_allowed], Jsonb(payload_data(joe_allowed, "f" * 64, ref, stale_snapshot))), "22023", "refuses stale snapshot")
    cur.execute("reset session authorization")
    prune_lease = uuid.uuid4(); prune_job, prune_snapshot = job(cur, joe_key, prune_lease, "2 seconds")
    cur.execute("set session authorization carr_calendar_prebrief_joe")
    cur.execute(ingest_sql(), (prune_job, prune_lease, [joe_allowed], Jsonb([])))
    cur.execute("reset session authorization")
    cur.execute("select sponsor,count(*) from ops.calendar_prebrief_projection_event group by sponsor order by sponsor")
    if cur.fetchall() != [("dell", 2)]:
        raise RuntimeError("empty Joe snapshot did not prune only Joe's current projection")
    colon_lease = uuid.uuid4(); colon_job, colon_snapshot = job(cur, joe_key, colon_lease, "3 seconds")
    cur.execute("set session authorization carr_calendar_prebrief_joe")
    cur.execute(ingest_sql(), (colon_job, colon_lease, [joe_allowed], Jsonb(payload_data(
        joe_allowed, "9" * 64, ref, colon_snapshot, title="Meeting: Dr Smith"))))
    cur.execute("reset session authorization")

    # Tables remain invisible; only the exact capabilities and redacted views
    # are reachable to application identities.
    cur.execute(
        """select has_table_privilege(role_name,table_name,'select') or has_table_privilege(role_name,table_name,'insert')
                    or has_table_privilege(role_name,table_name,'update') or has_table_privilege(role_name,table_name,'delete')
              from (values ('carr_reader'),('carr_writer'),('carr_jobs'),('carr_authority'),('carr_calendar_prebrief_jobs')) roles(role_name)
              cross join (values ('ops.calendar_prebrief_allowed_calendar'),('ops.calendar_prebrief_allowlist_receipt'),
                                 ('ops.calendar_prebrief_projection_event'),('ops.calendar_prebrief_projection_participant'),
                                 ('ops.calendar_prebrief_projection_receipt')) tables(table_name)"""
    )
    if any(row[0] for row in cur.fetchall()):
        raise RuntimeError("calendar prebrief base/config table leaked to an application capability")
    cur.execute(
        """select has_function_privilege('carr_reader','ops.ingest_calendar_prebrief_projection(uuid,uuid,text[],jsonb)','execute'),
                  has_function_privilege('carr_jobs','ops.ingest_calendar_prebrief_projection(uuid,uuid,text[],jsonb)','execute'),
                  has_function_privilege('carr_writer','ops.ingest_calendar_prebrief_projection(uuid,uuid,text[],jsonb)','execute'),
                  has_function_privilege('carr_calendar_prebrief_jobs','ops.ingest_calendar_prebrief_projection(uuid,uuid,text[],jsonb)','execute'),
                  has_function_privilege('carr_authority','ops.replace_calendar_prebrief_allowlist(text[])','execute'),
                  has_function_privilege('carr_reader','ops.replace_calendar_prebrief_allowlist(text[])','execute'),
                  has_function_privilege('carr_jobs','ops.replace_calendar_prebrief_allowlist(text[])','execute'),
                  has_function_privilege('carr_calendar_prebrief_jobs','ops.replace_calendar_prebrief_allowlist(text[])','execute')"""
    )
    if required(cur, "function capability split") != (False, False, False, True, True, False, False, False):
        raise RuntimeError("calendar prebrief function capability grants are wrong")
    cur.execute("set session authorization carr_jobs")
    refused(cur, ingest_sql(), first_args, "42501", "permission denied")
    cur.execute("reset session authorization")
    cur.execute("set session authorization carr_writer")
    refused(cur, ingest_sql(), first_args, "42501", "permission denied")
    cur.execute("reset session authorization")
    cur.execute("select column_name from information_schema.columns where table_schema='public' and table_name='v_calendar_prebrief_events' order by ordinal_position")
    expected_events = [(name,) for name in ("sponsor", "occurrence_key", "starts_at", "ends_at", "title", "location", "participant_ref", "participant_display_name", "participant_org_name", "participant_status", "participant_last_touch", "open_owner", "open_action")]
    if cur.fetchall() != expected_events:
        raise RuntimeError("event reader view drifted from brief-pack contract")
    cur.execute("set session authorization carr_reader")
    cur.execute("select sponsor,snapshot_at,event_count,participant_count from v_calendar_prebrief_snapshot_status order by sponsor")
    if cur.fetchall() != [("dell", dell_snapshot, 2, 1), ("joe", colon_snapshot, 1, 1)]:
        raise RuntimeError("redacted snapshot-status view did not expose the latest per-sponsor counts")
    cur.execute("select sponsor,occurrence_key,title,location,participant_ref from v_calendar_prebrief_events order by sponsor,occurrence_key")
    rows = cur.fetchall()
    if len(rows) != 3 or not any(row[-1] is None for row in rows) or any("@" in str(value) or "://" in str(value) for row in rows for value in row if value is not None):
        raise RuntimeError("reader event view exposed prohibited source data")
    refused(cur, "select * from ops.calendar_prebrief_projection_event", (), "42501", "permission denied")
    refused(cur, "select * from ops.calendar_prebrief_allowed_calendar", (), "42501", "permission denied")
    cur.execute("reset session authorization")
    cur.execute("set session authorization carr_authority_dell")
    cur.execute("select (ops.replace_calendar_prebrief_allowlist(%s)).configuration_digest", ([dell_allowed, alien],))
    required(cur, "fresh Dell allowlist receipt")
    cur.execute("reset session authorization")
    cur.execute("select active_revision_id from ops.calendar_prebrief_allowed_calendar where sponsor='dell'")
    dell_revision_b = required(cur, "changed Dell allowlist revision")[0]
    if dell_revision_b == dell_revision_a:
        raise RuntimeError("allowlist change did not mint a new active revision")
    cur.execute("set session authorization carr_reader")
    cur.execute("select sponsor,snapshot_at,event_count,participant_count from v_calendar_prebrief_snapshot_status order by sponsor")
    if cur.fetchall() != [("joe", colon_snapshot, 1, 1)]:
        raise RuntimeError("allowlist change did not immediately invalidate Dell's stale snapshot status")
    cur.execute("select sponsor,count(*) from v_calendar_prebrief_events group by sponsor order by sponsor")
    if cur.fetchall() != [("joe", 1)]:
        raise RuntimeError("allowlist change did not immediately hide Dell's stale projection events")
    cur.execute("reset session authorization")
    cur.execute("set session authorization carr_authority_dell")
    cur.execute("select (ops.replace_calendar_prebrief_allowlist(%s)).id", ([dell_allowed],))
    dell_revision_a_again = required(cur, "restored Dell allowlist revision")[0]
    cur.execute("reset session authorization")
    if dell_revision_a_again in (dell_revision_a, dell_revision_b):
        raise RuntimeError("A-to-B-to-A allowlist restoration reused an old revision")
    cur.execute("set session authorization carr_reader")
    cur.execute("select sponsor,snapshot_at,event_count,participant_count from v_calendar_prebrief_snapshot_status order by sponsor")
    if cur.fetchall() != [("joe", colon_snapshot, 1, 1)]:
        raise RuntimeError("A-to-B-to-A allowlist restoration resurrected stale Dell status")
    cur.execute("select sponsor,count(*) from v_calendar_prebrief_events group by sponsor order by sponsor")
    if cur.fetchall() != [("joe", 1)]:
        raise RuntimeError("A-to-B-to-A allowlist restoration resurrected stale Dell events")
    cur.execute("reset session authorization")
    refused(cur, "update ops.calendar_prebrief_projection_receipt set event_count=9 where id=%s", (first_receipt,), "P0001", "append-only")
    cur.execute("select id from ops.calendar_prebrief_allowlist_receipt where sponsor='joe' order by configured_at limit 1")
    first_allowlist_receipt = required(cur, "Joe allowlist receipt id")[0]
    refused(cur, "update ops.calendar_prebrief_allowlist_receipt set configured_by='dell' where id=%s", (first_allowlist_receipt,), "P0001", "append-only")
    conn.rollback()

print("calendar prebrief projection local acceptance passed")
