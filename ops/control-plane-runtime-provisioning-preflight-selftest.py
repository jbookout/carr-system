#!/usr/bin/env python3
"""Hermetic tests for the opt-in, secret-safe runtime provisioning preflight."""
from __future__ import annotations

import importlib.util
import json
import copy
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "ops" / "control-plane-runtime-provisioning-preflight.py"
CONFIG = REPO / "ops" / "config" / "control-plane-provisioning.v1.json"
FAILED: list[str] = []


def load() -> Any:
    spec = importlib.util.spec_from_file_location("runtime_preflight", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def check(label: str, condition: bool) -> None:
    print(("  ok    " if condition else "  FAIL  ") + label)
    if not condition:
        FAILED.append(label)


class FakeCursor:
    def __init__(self) -> None:
        self.last = ""
        self.queries: list[str] = []
    def execute(self, query: str, params: object | None = None) -> None:
        self.last = query
        self.queries.append(query)
    def fetchone(self) -> tuple[Any, ...] | None:
        if "session_user" in self.last:
            return ("carr_jobs", "carr_jobs")
        if "has_table_privilege" in self.last:
            return (False,)
        if "count(*)" in self.last:
            return (3,)
        return None
    def fetchall(self) -> list[tuple[Any, ...]]:
        if "pg_roles" in self.last:
            return [("carr_backup", True), ("carr_authority_dell", True), ("carr_authority_joe", True),
                    ("carr_device_evidence", False), ("carr_jobs", True)]
        if "provider_route" in self.last:
            return [("primary", True), ("secondary", True)]
        return []


class FakeConnection:
    def __init__(self) -> None:
        self.closed = False
        self.query_log: list[str] = []
    def cursor(self) -> FakeCursor:
        cursor = FakeCursor()
        original_execute = cursor.execute
        def record(query: str, params: object | None = None) -> None:
            original_execute(query, params)
            self.query_log.append(query)
        cursor.execute = record  # type: ignore[method-assign]
        return cursor
    def close(self) -> None:
        self.closed = True


def main() -> int:
    print("control-plane-runtime-provisioning-preflight-selftest — opt-in and secret-safe\n")
    module = load()
    no_opt_in = subprocess.run([sys.executable, str(SCRIPT)], text=True, capture_output=True, check=False)
    check("default invocation does not read runtime state", no_opt_in.returncode == 2 and "runtime_opt_in_required" in no_opt_in.stdout)

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        db_env = root / "db.env"
        provider_env = root / "provider.env"
        age_key = root / "age.key"
        age_pub = root / "age.pub"
        db_env.write_text("CARR_DB_JOBS_URL='postgresql://carr_jobs:secret@host/db'\nCARR_DB_BACKUP_URL=postgresql://backup@host/db\n", encoding="utf-8")  # ci-secret-scan: allow — hermetic non-routable credential fixture
        provider_env.write_text("CARR_AI_ROUTE_PRIMARY_URL=https://provider\nCARR_AI_ROUTE_PRIMARY_TOKEN=token-primary\nCARR_AI_ROUTE_SECONDARY_URL=https://fallback\nCARR_AI_ROUTE_SECONDARY_TOKEN=token-secondary\n", encoding="utf-8")
        age_key.write_text("private-key-material\n", encoding="utf-8")
        age_pub.write_text("public-key\n", encoding="utf-8")
        for path, mode in ((db_env, 0o600), (provider_env, 0o600), (age_key, 0o600), (age_pub, 0o644)):
            path.chmod(mode)
        config = json.loads(CONFIG.read_text(encoding="utf-8"))
        connection = FakeConnection()
        report = module.collect_runtime(config, db_env=db_env, provider_env=provider_env, age_key=age_key,
                                        age_public_key=age_pub, environ={}, connect=lambda _: connection)
        serialized = json.dumps(report, sort_keys=True)
        check("report exposes file modes and presence but never credential values", "secret@host" not in serialized and "token-primary" not in serialized and report["files"]["db_env"]["mode"] == "0600")
        check("jobs identity is verified through a read-only aggregate seam", report["jobs"]["database"]["jobs_identity_matches"] is True)
        check("database evidence starts a read-only transaction before catalog reads", connection.query_log[0] == "begin transaction read only")
        check("unreadable device registry stays explicitly unobservable", report["device_evidence"]["principals"]["observable_by_jobs"] is False and report["jobs_runtime_identity_verified"] is True)
        check("provider presence needs both routes and both secrets", all(v["url_present"] and v["token_present"] for v in report["providers"]["routes"].values()))
        all_ready = copy.deepcopy(report)
        for actor in all_ready["authority"].values():
            actor["credential_present"] = True
        all_ready["providers"]["selector_present"] = True
        all_ready["npi_taxonomy"]["policy_present_and_nonempty"] = True
        check("declared external prerequisite presence is not external authentication", module.declared_external_prerequisites_present(all_ready)
              and all_ready["external_prerequisites_authenticated"] is False)
        for label, mutate in (
            ("authority credential", lambda value: value["authority"]["joe"].__setitem__("credential_present", False)),
            ("provider selector", lambda value: value["providers"].__setitem__("selector_present", False)),
            ("backup private key mode", lambda value: value["files"]["backup_age_key"].__setitem__("secure", False)),
            ("backup public key mode", lambda value: value["files"]["backup_age_public_key"].__setitem__("secure", False)),
        ):
            missing_surface = copy.deepcopy(all_ready)
            mutate(missing_surface)
            check(f"missing {label} refuses declared presence", not module.declared_external_prerequisites_present(missing_surface))
        provider_env.chmod(0o644)
        insecure = module.collect_runtime(config, db_env=db_env, provider_env=provider_env, age_key=age_key,
                                          age_public_key=age_pub, environ={}, connect=lambda _: FakeConnection())
        check("insecure provider credential file refuses declared presence", insecure["files"]["provider_env"]["secure"] is False and insecure["declared_external_prerequisites_present"] is False)
        provider_env.chmod(0o600)
        provider_env.write_text("CARR_AI_ROUTE_PRIMARY_URL=x\nCARR_AI_ROUTE_PRIMARY_TOKEN=y\n", encoding="utf-8")
        missing_route = module.collect_runtime(config, db_env=db_env, provider_env=provider_env, age_key=age_key,
                                               age_public_key=age_pub, environ={}, connect=lambda _: FakeConnection())
        check("missing secondary provider values refuse declared presence", missing_route["providers"]["routes"]["secondary"]["token_present"] is False and missing_route["declared_external_prerequisites_present"] is False)
        forged = json.loads(json.dumps(config))
        forged["device_evidence"]["receipt_tables"] = ["ops.device_evidence_receipt; select pg_sleep(1)"]
        attempted = False
        def forbidden_connect(_: str) -> FakeConnection:
            nonlocal attempted
            attempted = True
            return FakeConnection()
        refused = module.collect_runtime(forged, db_env=db_env, provider_env=provider_env, age_key=age_key,
                                         age_public_key=age_pub, environ={}, connect=forbidden_connect)
        check("unvalidated caller configuration cannot reach database query construction", refused["static_contract_valid"] is False and refused["jobs_runtime_identity_verified"] is False and not attempted)
        check("invalid-contract report preserves explicit non-authentication schema", refused.get("external_prerequisites_authenticated") is False
              and {"static_contract_valid", "jobs_runtime_identity_verified", "declared_external_prerequisites_present"}.issubset(refused))
        unavailable = module.collect_runtime(config, db_env=db_env, provider_env=provider_env, age_key=age_key,
                                             age_public_key=age_pub, environ={},
                                             connect=lambda _: (_ for _ in ()).throw(RuntimeError("postgresql://never-print-this")))
        check("connection failure remains redacted", "never-print-this" not in json.dumps(unavailable) and unavailable["jobs"]["database"]["error"] == "unavailable_or_not_authorized")
        for malformed in ("postgresql://carr_writer:secret@host/db", "postgresql://neondb_owner:secret@host/db",  # ci-secret-scan: allow — hermetic non-routable credential fixture
                          "user=carr_writer host=host dbname=db", "user=neondb_owner host=host dbname=db"):
            calls = 0
            def must_not_connect(_: str) -> FakeConnection:
                nonlocal calls
                calls += 1
                return FakeConnection()
            rejected = module.collect_runtime(config, db_env=db_env, provider_env=provider_env, age_key=age_key,
                                              age_public_key=age_pub, environ={"CARR_DB_JOBS_URL": malformed},
                                              connect=must_not_connect)
            check("owner or writer DSNs refuse before database connection", calls == 0 and rejected["jobs"]["database"]["error"] == "credential_identity_mismatch")
        db_env.write_text("CARR_DB_JOBS_URL='postgresql://carr_jobs:secret@host/db'\nCARR_DB_BACKUP_URL=postgresql://neondb_owner:backup-secret@host/db\n", encoding="utf-8")  # ci-secret-scan: allow — hermetic non-routable credential fixture
        used_dsns: list[str] = []
        def jobs_only_connect(dsn: str) -> FakeConnection:
            used_dsns.append(dsn)
            return FakeConnection()
        backup_owner = module.collect_runtime(config, db_env=db_env, provider_env=provider_env, age_key=age_key,
                                              age_public_key=age_pub, environ={}, connect=jobs_only_connect)
        check("backup DSN presence is never authenticated or used as a runtime identity",
              backup_owner["jobs_runtime_identity_verified"] is True
              and backup_owner["external_prerequisites_authenticated"] is False
              and len(used_dsns) == 1 and "neondb_owner" not in used_dsns[0])
        check("quoted URI and keyword jobs DSNs require exact carr_jobs user",
              module.jobs_dsn_has_expected_user("'postgresql://carr_jobs:secret@host/db'", "carr_jobs")  # ci-secret-scan: allow — hermetic non-routable credential fixture
              and module.jobs_dsn_has_expected_user("user=carr_jobs host=host dbname=db", "carr_jobs")
              and module.strip_one_outer_quote("'literal value'") == "literal value")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
