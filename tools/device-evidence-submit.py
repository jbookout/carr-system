#!/usr/bin/env python3
"""Submit one typed immutable device receipt through the narrow DB functions."""
from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from pathlib import Path
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from lib.device_evidence_submit import Submission, SubmissionRefused, validate_submission


DEDICATED_DSN_KEY = "CARR_DB_DEVICE_EVIDENCE_URL"
BROAD_CREDENTIAL_KEYS = frozenset({
    "DATABASE_URL", "CARR_DB_WRITER_URL", "CARR_DB_OWNER_URL", "CARR_DB_JOBS_URL", "CARR_DB_BACKUP_URL",
    "CARR_DB_EXPORTER_URL", "CARR_DB_AUTHORITY_URL", "CARR_DB_AUTHORITY_JOE_URL", "CARR_DB_AUTHORITY_DELL_URL",
})
DEFAULT_CREDENTIAL_FILE = Path.home() / ".config" / "carr" / "device-evidence.env"
EX_CONFIG = 78


def _mode_0600(path: Path) -> bool:
    try:
        return stat.S_IMODE(path.stat().st_mode) == 0o600
    except OSError:
        return False


def load_dedicated_dsn(path: Path) -> str:
    """Read exactly one literal dedicated DSN; never execute its contents."""
    if not path.is_file() or not _mode_0600(path):
        raise SubmissionRefused("device credential file must exist with mode 0600")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise SubmissionRefused("device credential file is unreadable") from exc
    values: dict[str, str] = {}
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or key != DEDICATED_DSN_KEY or key in values:
            raise SubmissionRefused("device credential file must contain exactly one dedicated DSN key")
        candidate = value.strip()
        if len(candidate) >= 2 and candidate[0] == candidate[-1] and candidate[0] in ("'", '"'):
            candidate = candidate[1:-1]
        if not candidate:
            raise SubmissionRefused("device credential value is empty")
        values[key] = candidate
    if set(values) != {DEDICATED_DSN_KEY}:
        raise SubmissionRefused("device credential file must contain exactly one dedicated DSN key")
    dsn = values[DEDICATED_DSN_KEY]
    if not is_direct_password_uri(dsn):
        raise SubmissionRefused("device credential must be a direct password-bearing PostgreSQL URI")
    return dsn


def is_direct_password_uri(value: str) -> bool:
    """Refuse keyword/service DSNs and ~/.pgpass fallback before connection."""
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    return (parsed.scheme.lower() in {"postgres", "postgresql"}
            and bool(parsed.username) and bool(parsed.password)
            and bool(parsed.hostname) and parsed.path not in {"", "/"}
            and not parsed.query and not parsed.fragment)


def reject_broad_environment(environ: dict[str, str]) -> None:
    # Reject every libpq PG* setting, not only the usual host/user/password
    # subset.  Otherwise PGPASSFILE, PGSERVICEFILE, or TLS material can alter
    # authentication despite the dedicated 0600 URI.
    if any(environ.get(key) for key in BROAD_CREDENTIAL_KEYS) or any(
            key.startswith("PG") and value for key, value in environ.items()):
        raise SubmissionRefused("broad database or authority credential is inherited")


def load_payload(input_path: Path | None) -> Any:
    try:
        if input_path is None:
            return json.load(sys.stdin)
        if not input_path.is_file() or not _mode_0600(input_path):
            raise SubmissionRefused("submission input file must exist with mode 0600")
        return json.loads(input_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SubmissionRefused("submission input is unreadable JSON") from exc


def statement_for_submission(submission: Submission) -> str:
    """Map a validated kind to one fixed function call; no generic SQL path."""
    if submission.function == "ops.record_device_evidence":
        return "select ops.record_device_evidence(%s::uuid,%s,%s::timestamptz,%s::jsonb,%s)"
    if submission.function == "ops.record_npi_device_evidence":
        return "select ops.record_npi_device_evidence(%s::uuid,%s::timestamptz,%s,%s,%s::jsonb,%s)"
    raise SubmissionRefused("stored function is unregistered")


def execute_submission(dsn: str, submission: Submission,
                       connect_factory: Callable[[str], Any] | None = None) -> str:
    """Call one fixed stored function and return its UUID; no generic SQL path."""
    if connect_factory is None:
        import psycopg
        connect_factory = psycopg.connect
    statement = statement_for_submission(submission)
    with connect_factory(dsn) as connection, connection.cursor() as cursor:
        cursor.execute(statement, (*submission.params, submission.idempotency_key))
        row = cursor.fetchone()
    if row is None or len(row) != 1:
        raise SubmissionRefused("stored function did not return a receipt id")
    try:
        return str(UUID(str(row[0])))
    except ValueError as exc:
        raise SubmissionRefused("stored function returned an invalid receipt id") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description="submit typed device evidence through the dedicated device credential")
    parser.add_argument("--input", type=Path, help="0600 JSON file; omit to read JSON from stdin")
    parser.add_argument("--credential-file", type=Path, default=DEFAULT_CREDENTIAL_FILE)
    parser.add_argument("--validate-only", action="store_true", help="validate only; do not contact a database")
    args = parser.parse_args()
    try:
        reject_broad_environment(dict(os.environ))
        submission = validate_submission(load_payload(args.input))
        if args.validate_only:
            print(json.dumps({"ok": True, "validated": True, "kind": submission.kind}, sort_keys=True))
            return 0
        dsn = load_dedicated_dsn(args.credential_file)
        receipt_id = execute_submission(dsn, submission)
    except SubmissionRefused:
        print("FAIL device-evidence-submit: refused", file=sys.stderr)
        return EX_CONFIG
    except Exception:
        print("FAIL device-evidence-submit: database submission unavailable", file=sys.stderr)
        return 1
    print(json.dumps({"ok": True, "kind": submission.kind, "receipt_id": receipt_id}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
