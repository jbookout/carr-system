#!/usr/bin/env python3
"""Pipe a redacted EventKit prebrief snapshot into its sponsor-bound DB boundary.

This adapter has no calendar access and deliberately accepts no snapshot path:
raw EventKit material must remain in a single pipe chain.  The sponsor is a
deployment-selected command argument, never a field trusted from the snapshot.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import unquote, urlsplit

HEX64 = re.compile(r"^[0-9a-f]{64}$")
REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
SPONSORS = {"joe", "dell"}
EVENT_FIELDS = {"sponsor", "calendar_key", "event_key", "occurrence_key", "starts_at", "ends_at", "title", "location", "participant_refs"}


class Refusal(RuntimeError):
    pass


def _timestamp(value: Any, label: str) -> datetime:
    if not isinstance(value, str):
        raise Refusal(f"{label} must be an RFC3339 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise Refusal(f"{label} must be an RFC3339 timestamp") from exc
    if parsed.tzinfo is None:
        raise Refusal(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc).replace(microsecond=0)


def read_snapshot_stdin() -> dict[str, Any]:
    try:
        mode = os.fstat(sys.stdin.fileno()).st_mode
    except OSError as exc:
        raise Refusal("snapshot requires a process pipe on stdin") from exc
    if sys.stdin.isatty() or stat.S_ISREG(mode):
        raise Refusal("snapshot requires a process pipe on stdin")
    text = sys.stdin.read(4_194_305)
    if len(text) > 4_194_304:
        raise Refusal("snapshot exceeds bounded input size")
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise Refusal("snapshot is malformed") from exc
    if not isinstance(value, dict):
        raise Refusal("snapshot must be an object")
    return value


def normalize_snapshot(snapshot: dict[str, Any], sponsor: str) -> tuple[list[str], list[dict[str, Any]]]:
    """Validate the entire sanitised topology and return the selected projection.

    Events belonging to the other fixed sponsor are validated before filtering;
    therefore a malformed cross-sponsor event cannot be silently discarded.
    """
    if set(snapshot) != {"version", "window", "observed_calendars", "events"} or snapshot.get("version") != 1:
        raise Refusal("snapshot has an unsupported shape or version")
    window = snapshot.get("window")
    if not isinstance(window, dict) or set(window) != {"starts_at", "ends_at"}:
        raise Refusal("snapshot window is malformed")
    window_start = _timestamp(window["starts_at"], "window.starts_at")
    window_end = _timestamp(window["ends_at"], "window.ends_at")
    if window_end - window_start != timedelta(days=52):
        raise Refusal("snapshot window must be exactly -7/+45 days")
    observed = snapshot.get("observed_calendars")
    events = snapshot.get("events")
    if not isinstance(observed, list) or not isinstance(events, list):
        raise Refusal("snapshot coverage and events must be arrays")
    keys_by_sponsor: dict[str, list[str]] = {"joe": [], "dell": []}
    for item in observed:
        if not isinstance(item, dict) or set(item) != {"sponsor", "calendar_key"}:
            raise Refusal("observed calendar has prohibited fields")
        item_sponsor, key = item.get("sponsor"), item.get("calendar_key")
        if item_sponsor not in SPONSORS or not isinstance(key, str) or not HEX64.fullmatch(key):
            raise Refusal("observed calendar is malformed")
        keys_by_sponsor[item_sponsor].append(key)
    for owner, keys in keys_by_sponsor.items():
        if not keys or len(set(keys)) != len(keys):
            raise Refusal(f"{owner} observed calendar coverage is missing or duplicated")
    normalized: list[dict[str, Any]] = []
    occurrences: set[tuple[str, str]] = set()
    for item in events:
        if not isinstance(item, dict) or set(item) != EVENT_FIELDS:
            raise Refusal("event has prohibited or missing fields")
        owner_value = item.get("sponsor")
        if not isinstance(owner_value, str) or owner_value not in SPONSORS:
            raise Refusal("event sponsor is malformed")
        owner = owner_value
        required_keys = ("calendar_key", "event_key", "occurrence_key")
        if any(not isinstance(item.get(key), str) or not HEX64.fullmatch(item[key]) for key in required_keys):
            raise Refusal("event opaque key is malformed")
        if item["calendar_key"] not in keys_by_sponsor[owner]:
            raise Refusal("event calendar is outside its observed sponsor coverage")
        starts, ends = _timestamp(item.get("starts_at"), "event.starts_at"), _timestamp(item.get("ends_at"), "event.ends_at")
        if ends <= starts or starts < window_start or ends > window_end:
            raise Refusal("event is outside the bounded snapshot window")
        title, location, refs = item.get("title"), item.get("location"), item.get("participant_refs")
        if not isinstance(title, str) or not title.strip() or len(title) > 240 or not (location is None or isinstance(location, str)) or (isinstance(location, str) and len(location) > 240):
            raise Refusal("event text fields are malformed")
        if not isinstance(refs, list) or len(set(refs)) != len(refs) or any(not isinstance(ref, str) or not REF.fullmatch(ref) for ref in refs):
            raise Refusal("event participant refs are malformed")
        if (owner, item["occurrence_key"]) in occurrences:
            raise Refusal("snapshot has duplicate occurrence keys")
        occurrences.add((owner, item["occurrence_key"]))
        # Prevent obvious source leakage before the database repeats its own
        # invariant checks.  IDs and email addresses can never cross this seam.
        rendered = json.dumps(item, sort_keys=True)
        if "@" in rendered or "://" in rendered or "description" in rendered or "notes" in rendered:
            raise Refusal("event contains raw source material")
        if owner == sponsor:
            normalized.append({key: item[key] for key in EVENT_FIELDS if key != "sponsor"})
    normalized.sort(key=lambda row: (row["starts_at"], row["occurrence_key"]))
    return sorted(keys_by_sponsor[sponsor]), normalized


def _dsn_login(dsn: str) -> str:
    try:
        return unquote(urlsplit(dsn).username or "").strip().lower()
    except ValueError:
        return ""


def ingest(*, sponsor: str, job_id: str, lease: str, snapshot: dict[str, Any], environ: dict[str, str] | None = None, connector: Any = None) -> dict[str, Any]:
    if sponsor not in SPONSORS:
        raise Refusal("sponsor must be joe or dell")
    observed_keys, events = normalize_snapshot(snapshot, sponsor)
    env = os.environ if environ is None else environ
    env_name = f"CARR_DB_CALENDAR_PREBRIEF_{sponsor.upper()}_URL"
    dsn = env.get(env_name, "").strip()
    expected_login = f"carr_calendar_prebrief_{sponsor}"
    if not dsn or _dsn_login(dsn) != expected_login:
        raise Refusal(f"{env_name} must name exact {expected_login} login")
    payload: Any
    if connector is None:
        try:
            import psycopg
            from psycopg.types.json import Jsonb
        except ImportError as exc:
            raise Refusal("psycopg is required for ingestion") from exc
        connector = psycopg.connect
        payload = Jsonb(events)
    else:
        payload = events
    with connector(dsn) as conn, conn.cursor() as cur:
        cur.execute("select session_user,current_user")
        identity = cur.fetchone()
        if tuple(identity or ()) != (expected_login, expected_login):
            raise Refusal("database session is not the exact sponsor login")
        cur.execute(
            "select row_to_json(ops.ingest_calendar_prebrief_projection(%s,%s,%s,%s))",
            (job_id, lease, observed_keys, payload),
        )
        row = cur.fetchone()
        if row is None:
            raise Refusal("ingest returned no receipt")
        conn.commit()
    return {"sponsor": sponsor, "receipt": row[0]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sponsor", required=True, choices=sorted(SPONSORS))
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--lease", required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(ingest(sponsor=args.sponsor, job_id=args.job_id, lease=args.lease, snapshot=read_snapshot_stdin()), sort_keys=True))
        return 0
    except Refusal as exc:
        print(f"calendar prebrief ingest: REFUSE {exc}", file=sys.stderr)
        return 78
    except Exception as exc:
        print(f"calendar prebrief ingest: FAIL {type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
