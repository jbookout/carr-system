#!/usr/bin/env python3
"""Read a bounded, allowlisted EventKit projection for canonical prebriefs.

This process deliberately emits only the minimal event projection.  Calendar
identifiers and attendee emails are transient inputs: they are hashed or resolved
before JSON is written to stdout.  The caller owns durable ingestion.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

WINDOW_BACK_DAYS = 7
WINDOW_FORWARD_DAYS = 45
INTERNAL_DOMAIN = "@carr.us"


class Refusal(RuntimeError):
    """The acquisition contract was not met; do not emit a partial snapshot."""


def _secure_regular_json(path: Path, label: str) -> Any:
    try:
        st = path.lstat()
    except OSError as exc:
        raise Refusal(f"{label} is missing") from exc
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        raise Refusal(f"{label} must be a regular non-symlink file")
    if st.st_mode & (stat.S_IWGRP | stat.S_IWOTH | stat.S_IRGRP | stat.S_IROTH):
        raise Refusal(f"{label} must not be readable or writable by group/other")
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise Refusal(f"{label} is malformed") from exc


def load_allowlist(path: Path) -> list[dict[str, str]]:
    raw = _secure_regular_json(path, "allowlist config")
    if not isinstance(raw, dict) or raw.get("version") != 1 or not isinstance(raw.get("calendars"), list):
        raise Refusal("allowlist config has an unsupported shape")
    entries: list[dict[str, str]] = []
    ids: set[str] = set()
    for item in raw["calendars"]:
        if not isinstance(item, dict) or set(item) != {"identifier", "sponsor"}:
            raise Refusal("allowlist calendar entry is malformed")
        identifier, sponsor = item.get("identifier"), item.get("sponsor")
        if (not isinstance(identifier, str) or not identifier.strip() or
                sponsor not in {"joe", "dell"}):
            raise Refusal("allowlist calendar entry is incomplete")
        if identifier in ids:
            raise Refusal("allowlist identifiers must be unique")
        ids.add(identifier)
        entries.append({"identifier": identifier, "sponsor": sponsor})
    if not entries:
        raise Refusal("allowlist contains no calendars")
    return entries


def load_resolver_map_data(raw: Any) -> Callable[[str], str]:
    """Build an exact resolver from process-memory JSON.

    The CLI accepts this sensitive mapping only through a pipe on stdin.  It is
    never admitted as a path because a 0600 file is still a durable copy of raw
    contact addresses.
    """
    if not isinstance(raw, dict):
        raise Refusal("resolver map has an unsupported shape")
    normalized: dict[str, str | list[Any]] = {}
    for email, ref in raw.items():
        if not isinstance(email, str) or not _valid_email(email):
            raise Refusal("resolver map has an invalid email key")
        email = email.lower().strip()
        if email in normalized:
            raise Refusal("resolver map has duplicate email keys")
        # A list is represented explicitly so lookup can refuse the affected
        # event.  That makes ambiguity observable rather than silently choosing
        # a first candidate.
        if isinstance(ref, list):
            if not all(isinstance(item, str) and item.strip() for item in ref):
                raise Refusal("resolver map has invalid canonical references")
            normalized[email] = ref
        elif isinstance(ref, str) and ref.strip():
            normalized[email] = ref.strip()
        else:
            raise Refusal("resolver map has invalid canonical references")

    def resolve(email: str) -> str:
        value = normalized.get(email.lower().strip())
        if not isinstance(value, str) or not value:
            raise Refusal("external attendee has no unique canonical reference")
        return value
    return resolve


def load_resolver_map_stdin() -> Callable[[str], str]:
    try:
        mode = os.fstat(sys.stdin.fileno()).st_mode
    except (OSError, AttributeError) as exc:
        raise Refusal("resolver map requires a process pipe on stdin") from exc
    if sys.stdin.isatty() or stat.S_ISREG(mode):
        raise Refusal("resolver map requires a process pipe on stdin")
    try:
        raw_text = sys.stdin.read(1_048_577)
        if len(raw_text) > 1_048_576:
            raise Refusal("resolver map exceeds the bounded input size")
        raw = json.loads(raw_text)
    except (OSError, json.JSONDecodeError) as exc:
        raise Refusal("resolver map is malformed") from exc
    return load_resolver_map_data(raw)


def _valid_email(value: str) -> bool:
    return value.count("@") == 1 and not value.startswith("@") and not value.endswith("@") and not any(c.isspace() for c in value)


def opaque_key(kind: str, *parts: str) -> str:
    if kind not in {"calendar", "event", "occurrence"}:
        raise ValueError("unsupported opaque key kind")
    return hashlib.sha256((kind + "\0" + "\0".join(parts)).encode("utf-8")).hexdigest()


def _identifier(obj: Any, method: str) -> str:
    value = getattr(obj, method, None)
    value = value() if callable(value) else value
    if not isinstance(value, str) or not value.strip():
        raise Refusal(f"EventKit object lacks {method}")
    return value.strip()


def _event_calendar(event: Any) -> Any:
    value = getattr(event, "calendar", None)
    value = value() if callable(value) else value
    if value is None:
        raise Refusal("EventKit event lacks calendar")
    return value


def _utc(value: Any) -> datetime:
    if isinstance(value, datetime):
        result = value
    elif hasattr(value, "timeIntervalSince1970"):
        result = datetime.fromtimestamp(float(value.timeIntervalSince1970()), tz=timezone.utc)
    else:
        raise Refusal("EventKit event has invalid date")
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc).replace(microsecond=0)


def _compact_text(value: Any, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    text = " ".join(value.split())
    if len(text) > 240:
        raise Refusal(f"event {field} exceeds bounded projection")
    return text or None


def _emails(event: Any) -> set[str]:
    result: set[str] = set()
    for attendee in (event.attendees() or []):
        url = attendee.URL() if hasattr(attendee, "URL") else None
        resource = url.resourceSpecifier() if url and hasattr(url, "resourceSpecifier") else ""
        value = str(resource).strip().lower()
        if value.startswith("mailto:"):
            value = value[7:]
        if value:
            if not _valid_email(value):
                raise Refusal("EventKit attendee address is malformed")
            result.add(value)
    organizer = event.organizer() if hasattr(event, "organizer") else None
    if organizer and hasattr(organizer, "URL") and organizer.URL():
        value = str(organizer.URL().resourceSpecifier()).strip().lower()
        if value.startswith("mailto:"):
            value = value[7:]
        if value:
            if not _valid_email(value):
                raise Refusal("EventKit organizer address is malformed")
            result.add(value)
    return result


def _request_access(store: Any) -> None:
    received: dict[str, bool | None] = {"allowed": None}
    def done(ok: bool, _error: Any) -> None:
        received["allowed"] = bool(ok)
    if hasattr(store, "requestFullAccessToEventsWithCompletion_"):
        store.requestFullAccessToEventsWithCompletion_(done)
    elif hasattr(store, "requestAccessToEntityType_completion_"):
        store.requestAccessToEntityType_completion_(0, done)
    else:
        raise Refusal("EventKit store cannot request event access")
    for _ in range(600):
        if received["allowed"] is not None:
            break
        time.sleep(0.1)
    if received["allowed"] is not True:
        raise Refusal("calendar access was denied or did not finish")


def capture_snapshot(store: Any, allowlist: list[dict[str, str]], resolver: Callable[[str], str], *, now: datetime | None = None, predicate_date: Callable[[datetime], Any] | None = None) -> dict[str, Any]:
    """Return a complete projection or raise Refusal; never return partial data."""
    _request_access(store)
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(microsecond=0)
    start, end = now - timedelta(days=WINDOW_BACK_DAYS), now + timedelta(days=WINDOW_FORWARD_DAYS)
    all_calendars = store.calendarsForEntityType_(0) or []
    by_identifier = {_identifier(cal, "calendarIdentifier"): cal for cal in all_calendars}
    if len(by_identifier) != len(all_calendars):
        raise Refusal("EventKit calendar identifiers are not unique")
    configured: list[tuple[dict[str, str], Any]] = []
    for entry in allowlist:
        calendar = by_identifier.get(entry["identifier"])
        if calendar is None:
            raise Refusal("configured allowlisted calendar is absent")
        configured.append((entry, calendar))
    allowed_objects = [calendar for _, calendar in configured]
    if not allowed_objects:
        raise Refusal("no allowlisted calendar objects")
    converter = predicate_date or (lambda value: value)
    predicate = store.predicateForEventsWithStartDate_endDate_calendars_(converter(start), converter(end), allowed_objects)
    events = store.eventsMatchingPredicate_(predicate) or []
    entry_by_id = {entry["identifier"]: entry for entry, _ in configured}
    projection: list[dict[str, Any]] = []
    for event in events:
        calendar_id = _identifier(_event_calendar(event), "calendarIdentifier")
        matched_entry = entry_by_id.get(calendar_id)
        if matched_entry is None:
            raise Refusal("EventKit returned event outside configured calendar coverage")
        event_id = _identifier(event, "eventIdentifier")
        event_start, event_end = _utc(event.startDate()), _utc(event.endDate())
        if event_end <= event_start or event_start < start or event_start >= end:
            raise Refusal("EventKit returned event outside requested bounded window")
        refs = sorted({resolver(email) for email in _emails(event) if not email.endswith(INTERNAL_DOMAIN)})
        start_text, end_text = event_start.isoformat().replace("+00:00", "Z"), event_end.isoformat().replace("+00:00", "Z")
        projection.append({
            "sponsor": matched_entry["sponsor"],
            "calendar_key": opaque_key("calendar", calendar_id),
            "event_key": opaque_key("event", calendar_id, event_id),
            "occurrence_key": opaque_key("occurrence", calendar_id, event_id, start_text),
            "starts_at": start_text,
            "ends_at": end_text,
            "title": _compact_text(event.title(), "title") or "(untitled)",
            "location": _compact_text(event.location(), "location"),
            "participant_refs": refs,
        })
    projection.sort(key=lambda row: (row["starts_at"], row["occurrence_key"]))
    # This is coverage evidence, not an event-derived list: an empty allowed
    # calendar must still reach the ingest boundary so a missing calendar can
    # never masquerade as an empty schedule.
    observed_calendars = sorted(
        ({"sponsor": entry["sponsor"], "calendar_key": opaque_key("calendar", entry["identifier"])}
         for entry, _ in configured),
        key=lambda row: (row["sponsor"], row["calendar_key"]),
    )
    return {"version": 1,
            "window": {"starts_at": start.isoformat().replace("+00:00", "Z"),
                       "ends_at": end.isoformat().replace("+00:00", "Z")},
            "observed_calendars": observed_calendars,
            "events": projection}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allowlist", required=True, type=Path, help="0600 JSON EventKit calendar allowlist")
    parser.add_argument("--resolver-map-stdin", action="store_true",
                        help="read the canonical email-to-ref map from a process pipe")
    parser.add_argument("--as-of", help="UTC anchor timestamp (ISO-8601), default now")
    args = parser.parse_args()
    try:
        allowlist = load_allowlist(args.allowlist)
        if not args.resolver_map_stdin:
            raise Refusal("--resolver-map-stdin is required")
        resolver = load_resolver_map_stdin()
        if args.as_of:
            raw = args.as_of.replace("Z", "+00:00")
            now = datetime.fromisoformat(raw)
            if now.tzinfo is None:
                raise Refusal("--as-of must include a UTC offset")
            now = now.astimezone(timezone.utc)
        else:
            now = None
        from EventKit import EKEventStore
        from Foundation import NSDate
        store = EKEventStore.alloc().init()
        snapshot = capture_snapshot(store, allowlist, resolver, now=now,
                                    predicate_date=lambda value: NSDate.dateWithTimeIntervalSince1970_(value.timestamp()))
        print(json.dumps(snapshot, sort_keys=True, separators=(",", ":")))
        return 0
    except Refusal as exc:
        print(f"calendar prebrief EventKit: REFUSE {exc}", file=sys.stderr)
        return 78
    except Exception as exc:
        print(f"calendar prebrief EventKit: FAIL {type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
