#!/usr/bin/env python3
"""Run one calendar-prebrief claim through isolated jobs and sponsor processes.

The parent owns only the jobs DSN and an exact leased claim.  It gives the
child that claim through stdin.  The child opens its own 0600 scoped profile,
verifies a signed collector envelope with a public key, resolves attendee
emails in memory, then calls resolver -> attestor -> ingest in that order.
Neither process writes source material to a file, argv, or log.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shlex
import stat
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import unquote, urlsplit

HEX64 = re.compile(r"^[0-9a-f]{64}$")
REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
SPONSORS = {"joe", "dell"}
MAX_PIPE = 1_048_576


class Refusal(RuntimeError):
    pass


def _read_pipe(limit: int = MAX_PIPE) -> dict[str, Any]:
    mode = os.fstat(sys.stdin.fileno()).st_mode
    if sys.stdin.isatty() or stat.S_ISREG(mode):
        raise Refusal("coordinator input must be a process pipe")
    text = sys.stdin.read(limit + 1)
    if len(text) > limit:
        raise Refusal("coordinator input exceeds its bound")
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise Refusal("coordinator input is malformed") from exc
    if not isinstance(value, dict):
        raise Refusal("coordinator input must be an object")
    return value


def _claim(value: Mapping[str, Any]) -> dict[str, str]:
    if set(value) != {"job_id", "lease", "scheduled_for"} or any(
        not isinstance(value.get(key), str) or not value[key] for key in value
    ):
        raise Refusal("jobs parent requires one exact leased claim")
    try:
        parsed = datetime.fromisoformat(value["scheduled_for"].replace("Z", "+00:00"))
    except ValueError as exc:
        raise Refusal("leased claim scheduled_for must be RFC3339") from exc
    if parsed.tzinfo is None:
        raise Refusal("leased claim scheduled_for must include timezone")
    return {key: value[key] for key in ("job_id", "lease", "scheduled_for")}


def _dsn_login(value: str) -> str:
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"postgres", "postgresql"} or not parsed.hostname or not parsed.path.strip("/"):
            return ""
        if not parsed.password or parsed.fragment:
            return ""
        return unquote(parsed.username or "")
    except ValueError:
        return ""


def _profile_file(path: Path, sponsor: str) -> dict[str, str]:
    expected = {
        f"CARR_DB_CALENDAR_PREBRIEF_ATTESTOR_{sponsor.upper()}_URL": f"carr_calendar_prebrief_attestor_{sponsor}",
        f"CARR_DB_CALENDAR_PREBRIEF_RESOLVER_{sponsor.upper()}_URL": f"carr_calendar_prebrief_resolver_{sponsor}",
        f"CARR_DB_CALENDAR_PREBRIEF_{sponsor.upper()}_URL": f"carr_calendar_prebrief_{sponsor}",
    }
    try:
        info = path.lstat()
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise Refusal("sponsor credential profile is unavailable") from exc
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600:
        raise Refusal("sponsor credential profile must be a 0600 regular non-symlink")
    values: dict[str, str] = {}
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or key not in expected or key in values:
            raise Refusal("sponsor credential profile has unknown or duplicate content")
        value = value.strip().strip("'\"")
        if _dsn_login(value) != expected[key]:
            raise Refusal("sponsor credential profile has an unsafe identity")
        values[key] = value
    if set(values) != set(expected):
        raise Refusal("sponsor credential profile is incomplete")
    return values


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def verify_envelope(value: Mapping[str, Any], public_key: Path) -> tuple[dict[str, Any], dict[str, str]]:
    if set(value) != {"payload", "signature", "collector_key_fingerprint", "collector_version"}:
        raise Refusal("collector envelope has an unsupported shape")
    payload = value.get("payload")
    fingerprint = value.get("collector_key_fingerprint")
    version = value.get("collector_version")
    signature_text = value.get("signature")
    if not isinstance(payload, dict) or not isinstance(fingerprint, str) or not HEX64.fullmatch(fingerprint) or not isinstance(version, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", version) or not isinstance(signature_text, str):
        raise Refusal("collector envelope fields are malformed")
    try:
        info = public_key.lstat()
        key_bytes = public_key.read_bytes()
        signature = base64.b64decode(signature_text, validate=True)
    except (OSError, ValueError) as exc:
        raise Refusal("collector public key or signature is invalid") from exc
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or not signature or len(signature) > 4096:
        raise Refusal("collector public key or signature is invalid")
    if hashlib.sha256(key_bytes).hexdigest() != fingerprint:
        raise Refusal("collector envelope key fingerprint does not match configured public key")
    read_fd, write_fd = os.pipe()
    try:
        os.write(write_fd, signature)
        os.close(write_fd)
        verified = subprocess.run(
            ["openssl", "pkeyutl", "-verify", "-pubin", "-inkey", str(public_key), "-rawin", "-in", "/dev/fd/0", "-sigfile", f"/dev/fd/{read_fd}"],
            input=_canonical(payload), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            pass_fds=(read_fd,), check=False,
        )
    finally:
        try: os.close(read_fd)
        except OSError: pass
        try: os.close(write_fd)
        except OSError: pass
    if verified.returncode != 0:
        raise Refusal("collector envelope signature verification failed")
    return payload, {"collector_key_fingerprint": fingerprint, "signature_sha256": hashlib.sha256(signature).hexdigest(), "collector_version": version}


def _snapshot_from_raw(payload: Mapping[str, Any], sponsor: str, resolve) -> dict[str, Any]:
    if set(payload) != {"version", "window", "observed_calendars", "events"} or payload.get("version") != 1 or not isinstance(payload.get("events"), list):
        raise Refusal("collector payload has an unsupported shape")
    if not isinstance(payload.get("window"), dict) or set(payload["window"]) != {"starts_at", "ends_at"} or not isinstance(payload.get("observed_calendars"), list):
        raise Refusal("collector payload coverage is malformed")
    for calendar in payload["observed_calendars"]:
        if not isinstance(calendar, dict) or set(calendar) != {"sponsor", "calendar_key"} or calendar.get("sponsor") != sponsor or not isinstance(calendar.get("calendar_key"), str) or not HEX64.fullmatch(calendar["calendar_key"]):
            raise Refusal("collector payload crosses the selected sponsor boundary")
    events: list[dict[str, Any]] = []
    allowed = {"sponsor", "calendar_key", "event_key", "occurrence_key", "starts_at", "ends_at", "title", "location", "attendee_emails"}
    for event in payload["events"]:
        if not isinstance(event, dict) or set(event) != allowed or event.get("sponsor") != sponsor or not isinstance(event.get("attendee_emails"), list) or len(event["attendee_emails"]) > 16:
            raise Refusal("collector event has an unsupported shape")
        refs: set[str] = set()
        for email in event["attendee_emails"]:
            if not isinstance(email, str) or len(email) > 320 or email.count("@") != 1 or any(char.isspace() for char in email):
                raise Refusal("collector attendee address is malformed")
            if not email.lower().endswith("@carr.us"):
                ref = resolve(email)
                if not isinstance(ref, str) or not REF.fullmatch(ref):
                    raise Refusal("resolver returned an invalid canonical ref")
                refs.add(ref)
        events.append({key: event[key] for key in allowed - {"attendee_emails"}} | {"participant_refs": sorted(refs)})
    return {"version": 1, "window": payload["window"], "observed_calendars": payload["observed_calendars"], "events": events}


def _connector():
    try:
        import psycopg
        from psycopg.types.json import Jsonb
    except ImportError as exc:
        raise Refusal("psycopg is required for coordinator execution") from exc
    return psycopg.connect, Jsonb


def _call(dsn: str, identity: str, query: str, args: tuple[Any, ...]) -> Any:
    connect, Jsonb = _connector()
    with connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("select session_user,current_user")
        if tuple(cur.fetchone() or ()) != (identity, identity):
            raise Refusal("scoped database session identity mismatch")
        cooked = tuple(Jsonb(value) if isinstance(value, (list, dict)) else value for value in args)
        cur.execute(query, cooked)
        row = cur.fetchone()
        if row is None:
            raise Refusal("scoped database call returned no receipt")
        conn.commit()
    return row[0]


def _capture(command: str) -> dict[str, Any]:
    if not command or len(command) > 4096:
        raise Refusal("collector connector is not configured")
    try:
        argv = shlex.split(command, posix=True)
    except ValueError as exc:
        raise Refusal("collector connector command is malformed") from exc
    if not argv:
        raise Refusal("collector connector command is malformed")
    safe = {key: os.environ[key] for key in ("PATH", "LANG", "LC_ALL", "LC_CTYPE", "HOME") if os.environ.get(key)}
    result = subprocess.run(argv, input=b"", stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=safe, timeout=60, check=False)
    if result.returncode or len(result.stdout) > MAX_PIPE:
        raise Refusal("collector connector failed")
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise Refusal("collector connector returned malformed envelope") from exc
    if not isinstance(value, dict):
        raise Refusal("collector connector returned malformed envelope")
    return value


def child_execute(*, sponsor: str, mode: str, claim: Mapping[str, Any], profile: Path, public_key: Path, capture_command: str) -> dict[str, Any]:
    if sponsor not in SPONSORS or mode not in {"live", "canary"}:
        raise Refusal("invalid sponsor or mode")
    if any(os.environ.get(key) for key in os.environ if key.startswith("CARR_DB_") or key.startswith("PG")):
        raise Refusal("sponsor child must receive credentials only through its profile")
    claim = _claim(claim)
    scoped = _profile_file(profile, sponsor)
    payload, evidence = verify_envelope(_capture(capture_command), public_key)
    resolver_dsn = scoped[f"CARR_DB_CALENDAR_PREBRIEF_RESOLVER_{sponsor.upper()}_URL"]
    def resolve(email: str) -> str:
        return _call(resolver_dsn, f"carr_calendar_prebrief_resolver_{sponsor}", "select ops.resolve_calendar_prebrief_email_ref(%s)", (email,))
    snapshot = _snapshot_from_raw(payload, sponsor, resolve)
    import importlib.util
    bridge_spec = importlib.util.spec_from_file_location("calendar_prebrief_ingest", Path(__file__).with_name("calendar-prebrief-ingest.py"))
    assert bridge_spec and bridge_spec.loader
    bridge = importlib.util.module_from_spec(bridge_spec); bridge_spec.loader.exec_module(bridge)
    observed, events = bridge.normalize_snapshot(snapshot, sponsor)
    destination = "live" if mode == "live" else f"calendar-prebrief-canary-{sponsor}"
    attestor_dsn = scoped[f"CARR_DB_CALENDAR_PREBRIEF_ATTESTOR_{sponsor.upper()}_URL"]
    attestation = _call(attestor_dsn, f"carr_calendar_prebrief_attestor_{sponsor}",
        "select (ops.record_calendar_prebrief_verified_envelope(%s,%s,%s,%s,%s,%s,%s,%s)).id",
        (claim["job_id"], claim["lease"], observed, events, destination, evidence["collector_key_fingerprint"], evidence["signature_sha256"], evidence["collector_version"]))
    ingest_dsn = scoped[f"CARR_DB_CALENDAR_PREBRIEF_{sponsor.upper()}_URL"]
    if mode == "live":
        receipt = _call(ingest_dsn, f"carr_calendar_prebrief_{sponsor}", "select (ops.ingest_calendar_prebrief_projection(%s,%s,%s,%s)).id", (claim["job_id"], claim["lease"], observed, events))
    else:
        receipt = _call(ingest_dsn, f"carr_calendar_prebrief_{sponsor}", "select (ops.ingest_calendar_prebrief_canary_projection(%s,%s,%s,%s,%s)).id", (claim["job_id"], claim["lease"], destination, observed, events))
    return {"sponsor": sponsor, "mode": mode, "attestation_id": str(attestation), "receipt_id": str(receipt)}


def parent_execute(*, sponsor: str, mode: str, claim_command: str, child_profile: Path, public_key: Path, capture_command: str, environ: Mapping[str, str]) -> dict[str, Any]:
    if sponsor not in SPONSORS or mode not in {"live", "canary"} or any(environ.get(key) for key in environ if (key.startswith("CARR_DB_") and key != "CARR_DB_JOBS_URL") or key in {"DATABASE_URL", "CARR_DB_WRITER_URL", "CARR_DB_OWNER_URL"}):
        raise Refusal("jobs parent requires only its scoped jobs credential")
    jobs_dsn = environ.get("CARR_DB_JOBS_URL", "")
    if _dsn_login(jobs_dsn) != "carr_jobs":
        raise Refusal("jobs parent lacks exact carr_jobs credential")
    try: argv = shlex.split(claim_command, posix=True)
    except ValueError as exc: raise Refusal("claim connector command is malformed") from exc
    if not argv: raise Refusal("claim connector command is malformed")
    claim_run = subprocess.run(argv, input=b"", stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env={"CARR_DB_JOBS_URL": jobs_dsn, "PATH": environ.get("PATH", "")}, timeout=30, check=False)
    if claim_run.returncode or len(claim_run.stdout) > 8192: raise Refusal("jobs claim connector failed")
    try: claim = _claim(json.loads(claim_run.stdout))
    except (json.JSONDecodeError, TypeError, Refusal) as exc: raise Refusal("jobs claim connector returned malformed claim") from exc
    child_env = {"PATH": environ.get("PATH", ""), "CARR_CALENDAR_PREBRIEF_CAPTURE_COMMAND": capture_command, "CARR_CALENDAR_PREBRIEF_CHILD_PROFILE": str(child_profile), "CARR_CALENDAR_PREBRIEF_COLLECTOR_PUBLIC_KEY": str(public_key)}
    # Preserve only the interpreter import path needed by a locally installed
    # connector.  Database credentials remain confined to the child profile.
    if environ.get("PYTHONPATH"):
        child_env["PYTHONPATH"] = environ["PYTHONPATH"]
    child = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--child", "--sponsor", sponsor, "--mode", mode], input=_canonical(claim), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=child_env, timeout=90, check=False)
    if child.returncode or len(child.stdout) > 8192: raise Refusal("sponsor child failed")
    try: result = json.loads(child.stdout)
    except json.JSONDecodeError as exc: raise Refusal("sponsor child returned malformed result") from exc
    if not isinstance(result, dict) or set(result) != {"sponsor", "mode", "attestation_id", "receipt_id"}: raise Refusal("sponsor child returned malformed result")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--child", action="store_true")
    parser.add_argument("--sponsor", required=True, choices=sorted(SPONSORS))
    parser.add_argument("--mode", required=True, choices=("live", "canary"))
    args = parser.parse_args()
    try:
        if args.child:
            result = child_execute(sponsor=args.sponsor, mode=args.mode, claim=_read_pipe(), profile=Path(os.environ.get("CARR_CALENDAR_PREBRIEF_CHILD_PROFILE", "")), public_key=Path(os.environ.get("CARR_CALENDAR_PREBRIEF_COLLECTOR_PUBLIC_KEY", "")), capture_command=os.environ.get("CARR_CALENDAR_PREBRIEF_CAPTURE_COMMAND", ""))
        else:
            result = parent_execute(sponsor=args.sponsor, mode=args.mode, claim_command=os.environ.get("CARR_CALENDAR_PREBRIEF_CLAIM_COMMAND", ""), child_profile=Path(os.environ.get("CARR_CALENDAR_PREBRIEF_CHILD_PROFILE", "")), public_key=Path(os.environ.get("CARR_CALENDAR_PREBRIEF_COLLECTOR_PUBLIC_KEY", "")), capture_command=os.environ.get("CARR_CALENDAR_PREBRIEF_CAPTURE_COMMAND", ""), environ=os.environ)
        print(json.dumps(result, sort_keys=True)); return 0
    except (Refusal, OSError, subprocess.SubprocessError):
        print("calendar prebrief coordinator: REFUSE", file=sys.stderr); return 78
    except Exception:
        print("calendar prebrief coordinator: FAIL", file=sys.stderr); return 1


if __name__ == "__main__":
    raise SystemExit(main())
