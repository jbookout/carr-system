#!/usr/bin/env python3
"""Hermetic adversarial checks for DB-bound collector-envelope verification."""
from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools/calendar-prebrief-coordinator.py"
spec = importlib.util.spec_from_file_location("coordinator", SCRIPT)
assert spec and spec.loader
coordinator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(coordinator)
bad: list[str] = []


def check(name: str, value: bool) -> None:
    print(("  ok " if value else "  FAIL ") + name)
    if not value:
        bad.append(name)


def refuses(fn) -> bool:
    try:
        fn()
    except coordinator.Refusal:
        return True
    return False


def contract() -> dict[str, object]:
    return {"challenge_id": "00000000-0000-4000-8000-000000000003", "sponsor": "joe",
            "job_id": "00000000-0000-4000-8000-000000000001", "attempt": 2,
            "lease_token": "00000000-0000-4000-8000-000000000002", "scheduled_for": "2026-08-20T06:30:00Z",
            "window_starts_at": "2026-08-13T06:30:00Z", "window_ends_at": "2026-10-04T06:30:00Z",
            "mode": "live", "destination": "live", "allowlist_revision_id": "00000000-0000-4000-8000-000000000004",
            "allowlist_digest": "d" * 64, "calendar_keys": ["a" * 64]}


with tempfile.TemporaryDirectory() as raw:
    root = Path(raw)
    private, public = root / "private.pem", root / "public.pem"
    subprocess.run(["openssl", "genpkey", "-algorithm", "ED25519", "-out", str(private)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    private.chmod(0o600)
    subprocess.run(["openssl", "pkey", "-in", str(private), "-pubout", "-out", str(public)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    raw_payload = {"version": 1, "window": {"starts_at": "2026-08-13T06:30:00Z", "ends_at": "2026-10-04T06:30:00Z"}, "observed_calendars": [{"sponsor": "joe", "calendar_key": "a" * 64}], "events": [{"sponsor": "joe", "calendar_key": "a" * 64, "event_key": "b" * 64, "occurrence_key": "c" * 64, "starts_at": "2026-08-20T08:00:00Z", "ends_at": "2026-08-20T09:00:00Z", "title": "Meeting", "location": None, "attendee_emails": ["raw.attendee@example.test"]}]}
    envelope = contract() | {"raw_payload": raw_payload, "raw_payload_digest": hashlib.sha256(coordinator._canonical(raw_payload)).hexdigest(), "raw_payload_count": 1, "collector_version": "fixture-1", "key_fingerprint": hashlib.sha256(public.read_bytes()).hexdigest()}
    signature = subprocess.run(["openssl", "pkeyutl", "-sign", "-inkey", str(private), "-rawin", "-in", "/dev/stdin"], input=coordinator._canonical(envelope), capture_output=True, check=True).stdout
    envelope["signature"] = base64.b64encode(signature).decode("ascii")
    got_raw, evidence = coordinator.verify_envelope(envelope, public, contract())
    check("exact DB contract signed envelope verifies", got_raw == raw_payload and evidence["signature_sha256"] == hashlib.sha256(signature).hexdigest())
    for name, key, value in (("cross-job replay", "job_id", "00000000-0000-4000-8000-000000000010"), ("altered scheduled window", "window_starts_at", "2026-08-12T06:30:00Z"), ("altered destination", "destination", "calendar-prebrief-canary-joe"), ("altered allowlist revision", "allowlist_revision_id", "00000000-0000-4000-8000-000000000010"), ("altered challenge", "challenge_id", "00000000-0000-4000-8000-000000000010")):
        changed = dict(envelope)
        changed[key] = value
        check(name + " refuses before resolver", refuses(lambda changed=changed: coordinator.verify_envelope(changed, public, contract())))
    # The DB API consumes challenge and unique signature evidence atomically;
    # ensure the coordinator cannot omit either while calling that boundary.
    source = SCRIPT.read_text(encoding="utf-8")
    check("verified-envelope API carries DB challenge and signature digest", "contract[\"challenge_id\"]" in source and "evidence[\"signature_sha256\"]" in source)
    live = root / "live.env"
    live.write_text("\n".join(("CARR_DB_CALENDAR_PREBRIEF_ATTESTOR_JOE_URL=postgresql://carr_calendar_prebrief_attestor_joe:x@db/carr", "CARR_DB_CALENDAR_PREBRIEF_RESOLVER_JOE_URL=postgresql://carr_calendar_prebrief_resolver_joe:x@db/carr", "CARR_DB_CALENDAR_PREBRIEF_JOE_URL=postgresql://carr_calendar_prebrief_joe:x@db/carr")) + "\n")
    live.chmod(0o600)
    canary = root / "canary.env"
    canary.write_text("\n".join(("CARR_DB_CALENDAR_PREBRIEF_ATTESTOR_JOE_URL=postgresql://carr_calendar_prebrief_attestor_joe:x@db/carr", "CARR_DB_CALENDAR_PREBRIEF_RESOLVER_JOE_URL=postgresql://carr_calendar_prebrief_resolver_joe:x@db/carr", "CARR_DB_CALENDAR_PREBRIEF_CANARY_JOE_URL=postgresql://carr_calendar_prebrief_canary_joe:x@db/carr")) + "\n")
    canary.chmod(0o600)
    check("live and canary profiles accept only their own execution identity", coordinator._profile_file(live, "joe", "live")["CARR_DB_CALENDAR_PREBRIEF_JOE_URL"].startswith("postgresql://carr_calendar_prebrief_joe:") and coordinator._profile_file(canary, "joe", "canary")["CARR_DB_CALENDAR_PREBRIEF_CANARY_JOE_URL"].startswith("postgresql://carr_calendar_prebrief_canary_joe:"))
    check("a live profile cannot run canary and a canary profile cannot run live", refuses(lambda: coordinator._profile_file(live, "joe", "canary")) and refuses(lambda: coordinator._profile_file(canary, "joe", "live")))
    check("raw attendee material has no temporary-file or argv sink", all(token not in source for token in ("NamedTemporaryFile", "mkstemp", "--snapshot", "--email")))

print("OK" if not bad else "FAIL " + ", ".join(bad))
raise SystemExit(bool(bad))
