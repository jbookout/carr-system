#!/usr/bin/env python3
"""Hermetic checks for the narrow device evidence submitter."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from lib.device_evidence_submit import SubmissionRefused, validate_submission

CLI_PATH = REPO / "tools" / "device-evidence-submit.py"
FAILED: list[str] = []


def check(label: str, condition: bool) -> None:
    print(("  ok    " if condition else "  FAIL  ") + label)
    if not condition:
        FAILED.append(label)


def social() -> dict[str, Any]:
    return {"schema_version": 1, "kind": "social_device_evidence", "job_id": "00000000-0000-4000-8000-000000000001",
            "builder_key": "linkedin.source-posts", "observed_at": "2026-08-16T12:00:00Z",
            "values": {"platform": "linkedin", "collector_state": "available", "voice_version": 1,
                       "source_posts": [{"url": f"https://linkedin.example/{n}", "network_priority": True} for n in range(3)]}}


def npi() -> dict[str, Any]:
    return {"schema_version": 1, "kind": "npi_device_evidence", "job_id": "00000000-0000-4000-8000-000000000002",
            "observed_at": "2026-08-16T12:00:00+00:00", "source_release": "nppes-weekly-2026-08-16",
            "source_checksum": "a" * 64,
            "results": [{"source_ref": "nppes:weekly:1", "npi": "1234567890", "enumeration_type": "NPI-2",
                         "last_updated": "2026-08-15T00:00:00Z", "addresses": [{"postal_code": "32501"}],
                         "taxonomies": ["207Q00000X"]}]}


def refused(value: Any) -> bool:
    try:
        validate_submission(value)
    except SubmissionRefused:
        return True
    return False


class FakeCursor:
    def __init__(self, receipt: object) -> None:
        self.receipt = receipt
        self.statement = ""
        self.params: tuple[str, ...] = ()
    def __enter__(self) -> "FakeCursor": return self
    def __exit__(self, *_: object) -> None: pass
    def execute(self, statement: str, params: tuple[str, ...]) -> None:
        self.statement, self.params = statement, params
    def fetchone(self) -> tuple[object, ...]: return (self.receipt,)


class FakeConnection:
    def __init__(self, cursor: FakeCursor) -> None: self.cursor_value = cursor
    def __enter__(self) -> "FakeConnection": return self
    def __exit__(self, *_: object) -> None: pass
    def cursor(self) -> FakeCursor: return self.cursor_value


def main() -> int:
    print("device-evidence-submitter-selftest — typed input and credential isolation\n")
    social_submission = validate_submission(social())
    check("LinkedIn submission selects only the registered stored function", social_submission.function == "ops.record_device_evidence" and len(social_submission.params) == 4)
    check("canonical social input has a stable idempotency key", social_submission.idempotency_key == validate_submission(deepcopy(social())).idempotency_key)
    changed = social(); changed["values"]["source_posts"][0]["url"] = "https://linkedin.example/changed"
    check("changed social evidence receives a different idempotency key", social_submission.idempotency_key != validate_submission(changed).idempotency_key)
    for label, mutate in (
        ("caller device id", lambda value: value.__setitem__("device_id", "invented")),
        ("caller mode", lambda value: value.__setitem__("mode", "live")),
        ("wrong platform", lambda value: value["values"].__setitem__("platform", "x")),
        ("too few LinkedIn posts", lambda value: value["values"].__setitem__("source_posts", value["values"]["source_posts"][:2])),
        ("unknown post field", lambda value: value["values"]["source_posts"][0].__setitem__("extra", True)),
    ):
        value = social(); mutate(value)
        check(f"social input refuses {label}", refused(value))
    npi_submission = validate_submission(npi())
    check("NPI submission selects only the registered stored function", npi_submission.function == "ops.record_npi_device_evidence" and len(npi_submission.params) == 5)
    x_payload = social(); x_payload["builder_key"] = "x.source-posts"; x_payload["values"] = {
        "platform": "x", "collector_state": "available", "voice_version": 1,
        "source_posts": [{"url": "https://x.example/1", "read_at": "2026-08-16T11:00:00Z"}]}
    check("X submission accepts its separately typed source-post shape", validate_submission(x_payload).function == "ops.record_device_evidence")
    for label, mutate in (
        ("unknown NPI result field", lambda value: value["results"][0].__setitem__("extra", True)),
        ("uppercase checksum", lambda value: value.__setitem__("source_checksum", "A" * 64)),
        ("NPI-1 identifier", lambda value: value["results"][0].__setitem__("npi", "123")),
        ("unknown taxonomy", lambda value: value["results"][0].__setitem__("taxonomies", [])),
    ):
        value = npi(); mutate(value)
        check(f"NPI input refuses {label}", refused(value))
    spec = importlib.util.spec_from_file_location("device_submit_cli", CLI_PATH)
    assert spec and spec.loader
    cli = importlib.util.module_from_spec(spec); spec.loader.exec_module(cli)
    cursor = FakeCursor("00000000-0000-4000-8000-000000000099")
    returned = cli.execute_submission("opaque-dsn", social_submission, lambda _: FakeConnection(cursor))
    check("submitter executes the exact social stored function and returns its UUID", returned == "00000000-0000-4000-8000-000000000099"
          and cursor.statement == "select ops.record_device_evidence(%s::uuid,%s,%s::timestamptz,%s::jsonb,%s)"
          and cursor.params[-1] == social_submission.idempotency_key)
    check("submitter maps NPI only to its fixed stored function", cli.statement_for_submission(npi_submission)
          == "select ops.record_npi_device_evidence(%s::uuid,%s::timestamptz,%s,%s,%s::jsonb,%s)")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp); credential = root / "device.env"; payload = root / "payload.json"
        credential.write_text("CARR_DB_DEVICE_EVIDENCE_URL='postgresql://literal-device:secret@host/db'\n", encoding="utf-8")
        payload.write_text(json.dumps(social()), encoding="utf-8")
        credential.chmod(0o600); payload.chmod(0o600)
        check("quoted dedicated credential is loaded literally", cli.load_dedicated_dsn(credential).startswith("postgresql://literal-device:"))
        credential.write_text("CARR_DB_DEVICE_EVIDENCE_URL=x\nOTHER=y\n", encoding="utf-8")
        check("credential file refuses every non-dedicated key", refused_credential(cli, credential))
        credential.write_text("CARR_DB_DEVICE_EVIDENCE_URL=x\n", encoding="utf-8"); credential.chmod(0o644)
        check("credential file refuses insecure mode", refused_credential(cli, credential))
        credential.chmod(0o600)
        for dsn_candidate in ("postgresql://device@host/db", "postgresql://device:@host/db", "user=device host=host dbname=db",
                              "postgresql://device:secret@host/db?service=unexpected",
                              "postgresql://device:secret@host/db?passfile=unexpected",
                              "postgresql://device:secret@host/db?sslmode=require",
                              "postgresql://device:secret@host/db#fragment"):
            credential.write_text(f"CARR_DB_DEVICE_EVIDENCE_URL={dsn_candidate}\n", encoding="utf-8")
            check("credential file refuses incomplete or non-URI device DSN", refused_credential(cli, credential))
        credential.write_text("CARR_DB_DEVICE_EVIDENCE_URL='postgresql://literal-device:secret@host/db'\n", encoding="utf-8")
        result = subprocess.run([sys.executable, str(CLI_PATH), "--input", str(payload), "--validate-only"], cwd=REPO,
                                text=True, capture_output=True, check=False, env={"PATH": "/usr/bin:/bin"})
        check("validate-only stdin/file path never contacts a database", result.returncode == 0 and '"validated": true' in result.stdout.lower())
        blocked = subprocess.run([sys.executable, str(CLI_PATH), "--input", str(payload), "--validate-only"], cwd=REPO,
                                 text=True, capture_output=True, check=False,
                                 env={"PATH": "/usr/bin:/bin", "CARR_DB_JOBS_URL": "never-use"})
        check("inherited jobs credential refuses before submission", blocked.returncode == 78 and "never-use" not in blocked.stderr)
        for name in ("PGHOST", "PGPASSWORD", "PGPASSFILE", "PGSERVICE", "PGSERVICEFILE", "PGOPTIONS",
                     "PGSSLCERT", "PGSSLKEY", "PGSSLROOTCERT", "PGSSLCRL", "PGSSLSNI",
                     "PGCHANNELBINDING", "PGREQUIREAUTH", "PGGSSENCMODE", "PGTARGETSESSIONATTRS"):
            try:
                cli.reject_broad_environment({name: "untrusted"})
                inherited_pg_refused = False
            except SubmissionRefused:
                inherited_pg_refused = True
            check(f"inherited {name} refuses before submission", inherited_pg_refused)
    return 1 if FAILED else 0


def refused_credential(cli: Any, path: Path) -> bool:
    try:
        cli.load_dedicated_dsn(path)
    except SubmissionRefused:
        return True
    return False


if __name__ == "__main__":
    raise SystemExit(main())
