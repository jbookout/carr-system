#!/usr/bin/env python3
"""Opt-in, read-only runtime provisioning evidence for the Control Plane.

This is deliberately narrower than a phase-exit gate.  It checks whether the
checked-in provisioning contract can be observed locally with the *routine
jobs* identity.  It never provisions roles, calls providers, writes the
ledger, or prints credential values, DSNs, receipt contents, or device names.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shlex
import stat
import sys
from urllib.parse import unquote, urlsplit
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Protocol, cast


REPO = Path(__file__).resolve().parents[1]
STATIC_PREFLIGHT = REPO / "ops" / "control-plane-provisioning-preflight.py"
DEFAULT_CONFIG = REPO / "ops" / "config" / "control-plane-provisioning.v1.json"
DEFAULT_DB_ENV = Path.home() / ".config" / "carr" / "db.env"
DEFAULT_PROVIDER_ENV = Path.home() / ".config" / "carr" / "control-plane.env"
DEFAULT_AGE_KEY = Path.home() / ".config" / "carr" / "age-key.txt"
DEFAULT_AGE_PUBLIC_KEY = REPO / "backups-public-key.txt"
REQUIRED_SYSTEM_AUTHORITY_CREDENTIALS = ("joe",)
OPTIONAL_AUTHORITY_CREDENTIALS = ("dell", "single_seat_fallback")


class Cursor(Protocol):
    def execute(self, query: str, params: object | None = None) -> Any: ...
    def fetchone(self) -> tuple[Any, ...] | None: ...
    def fetchall(self) -> list[tuple[Any, ...]]: ...


class Connection(Protocol):
    def cursor(self) -> Cursor: ...
    def close(self) -> None: ...


Connect = Callable[[str], Connection]


def _static_validator() -> tuple[Callable[[dict[str, Any]], list[str]], Callable[[Path], dict[str, Any]]]:
    spec = importlib.util.spec_from_file_location("control_plane_static_preflight", STATIC_PREFLIGHT)
    if spec is None or spec.loader is None:
        raise RuntimeError("static preflight is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.validate, module.read_json


def file_state(path: Path, *, required_mode: int = 0o600) -> dict[str, Any]:
    """Return only non-secret file presence and permission evidence."""
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except OSError:
        return {"present": False, "mode": None, "secure": False}
    return {"present": True, "mode": f"{mode:04o}", "secure": mode == required_mode}


def env_key_presence(path: Path) -> dict[str, bool]:
    """Parse a dotenv file without returning (or logging) its values."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    found: dict[str, bool] = {}
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, sep, value = line.partition("=")
        if not sep or not key or not (key.replace("_", "").isalnum()):
            continue
        found[key] = bool(value.strip())
    return found


def key_present(name: str, file_keys: Mapping[str, bool], environ: Mapping[str, str]) -> bool:
    return bool(file_keys.get(name, False) or environ.get(name, ""))


def strip_one_outer_quote(value: str) -> str:
    """Remove one matching dotenv quote pair; do not expand or evaluate it."""
    candidate = value.strip()
    if len(candidate) >= 2 and candidate[0] == candidate[-1] and candidate[0] in ("'", '"'):
        return candidate[1:-1]
    return candidate


def jobs_dsn_has_expected_user(dsn: str, expected_user: str) -> bool:
    """Accept only a literal URI/keyword DSN that names the jobs login."""
    candidate = strip_one_outer_quote(dsn)
    try:
        parsed = urlsplit(candidate)
    except ValueError:
        return False
    if parsed.scheme.lower() in {"postgres", "postgresql"}:
        return unquote(parsed.username or "") == expected_user
    try:
        fields = dict(item.split("=", 1) for item in shlex.split(candidate) if "=" in item)
    except ValueError:
        return False
    return fields.get("user") == expected_user


def _rows_by_name(rows: list[tuple[Any, ...]]) -> dict[str, bool]:
    return {str(name): bool(login) for name, login in rows if isinstance(name, str)}


def _optional_probe(cursor: Cursor, operation: Callable[[], Any]) -> tuple[bool, Any]:
    """Run one bounded read behind a savepoint so its failure stays local."""
    cursor.execute("savepoint runtime_evidence_probe")
    try:
        value = operation()
    except Exception:
        # PostgreSQL aborts the transaction after a failed statement.  Roll
        # back only this optional read so a missing view/grant cannot erase an
        # already verified jobs connection identity.
        cursor.execute("rollback to savepoint runtime_evidence_probe")
        cursor.execute("release savepoint runtime_evidence_probe")
        return False, None
    cursor.execute("release savepoint runtime_evidence_probe")
    return True, value


def _database_evidence(connect: Connect, jobs_dsn: str, config: dict[str, Any]) -> dict[str, Any]:
    """Collect bounded aggregate evidence under the jobs login only."""
    expected_jobs = config["routine_jobs"]["login_role"]
    expected_roles = [expected_jobs, config["routine_backup"]["login_role"],
                      config["authority"]["login_roles"]["joe"],
                      config["authority"]["login_roles"]["dell"],
                      config["device_evidence"]["privilege_bundle_role"]]
    connection = connect(jobs_dsn)
    try:
        cursor = connection.cursor()
        # A DB connection is not evidence of write authority; every query below
        # is inside a read-only transaction before any catalog/table access.
        cursor.execute("begin transaction read only")
        cursor.execute("select session_user, current_user")
        identity = cursor.fetchone() or (None, None)

        def read_roles() -> dict[str, bool]:
            cursor.execute("select rolname, rolcanlogin from pg_roles where rolname = any(%s) order by rolname", (expected_roles,))
            return _rows_by_name(cursor.fetchall())

        roles_ok, roles_value = _optional_probe(cursor, read_roles)
        roles = roles_value if roles_ok else {}

        def read_device_principals() -> dict[str, Any]:
            cursor.execute("select has_table_privilege(current_user, %s, 'select')", (config["device_evidence"]["principal_registry"]["table"],))
            readable = bool((cursor.fetchone() or (False,))[0])
            value: dict[str, Any] = {"observable_by_jobs": readable, "active_count": None}
            if readable:
                cursor.execute("select count(*) from ops.device_evidence_principal where active")
                value["active_count"] = int((cursor.fetchone() or (0,))[0])
            return value

        principal_ok, principal_value = _optional_probe(cursor, read_device_principals)
        principal = principal_value if principal_ok else {"observable_by_jobs": False, "active_count": None}

        receipt_counts: dict[str, int | None] = {}
        receipt_probes: dict[str, str] = {}
        for table in config["device_evidence"]["receipt_tables"]:
            short_name = table.rsplit(".", 1)[-1]

            def read_receipt_count(table_name: str = table) -> int:
                cursor.execute(f"select count(*) from {table_name}")
                return int((cursor.fetchone() or (0,))[0])

            receipt_ok, receipt_value = _optional_probe(cursor, read_receipt_count)
            receipt_counts[short_name] = receipt_value if receipt_ok else None
            receipt_probes[short_name] = "verified" if receipt_ok else "unavailable_or_not_authorized"

        def read_provider_routes() -> dict[str, bool]:
            cursor.execute("select route_key, enabled from ops.provider_route where route_key in ('primary','secondary') order by route_key")
            return {str(key): bool(enabled) for key, enabled in cursor.fetchall()}

        routes_ok, routes_value = _optional_probe(cursor, read_provider_routes)
        routes = routes_value if routes_ok else {}
        return {
            "reachable": True,
            "session_role": str(identity[0]) if identity[0] is not None else None,
            "current_role": str(identity[1]) if identity[1] is not None else None,
            "jobs_identity_matches": identity[0] == expected_jobs and identity[1] == expected_jobs,
            "login_roles": {role: roles.get(role) for role in expected_roles},
            "device_principals": principal,
            "immutable_receipt_counts": receipt_counts,
            "provider_routes": {route: routes.get(route, False) for route in ("primary", "secondary")},
            "evidence_probes": {
                "login_roles": "verified" if roles_ok else "unavailable_or_not_authorized",
                "device_principals": "verified" if principal_ok else "unavailable_or_not_authorized",
                "receipt_counts": receipt_probes,
                "provider_routes": "verified" if routes_ok else "unavailable_or_not_authorized",
            },
        }
    finally:
        connection.close()


def declared_external_prerequisites_present(result: Mapping[str, Any]) -> bool:
    """Presence only: this intentionally makes no authentication assertion."""
    provider_ok = all(item["url_present"] and item["token_present"]
                      for item in result["providers"]["routes"].values())
    authority_credentials_ok = all(
        result["authority"].get(actor, {}).get("credential_present") is True
        for actor in REQUIRED_SYSTEM_AUTHORITY_CREDENTIALS
    )
    backup_keys_ok = result["files"]["backup_age_key"]["secure"] and result["files"]["backup_age_public_key"]["secure"]
    return bool(
        result["static_contract_valid"] and result["files"]["db_env"]["secure"]
        and result["files"]["provider_env"]["secure"] and authority_credentials_ok
        and result["providers"]["selector_present"] and provider_ok
        and result["backup"]["credential_present"] and backup_keys_ok
        and result["npi_taxonomy"]["policy_present_and_nonempty"])


def collect_runtime(config: dict[str, Any], *, db_env: Path, provider_env: Path,
                    age_key: Path, age_public_key: Path, environ: Mapping[str, str],
                    connect: Connect | None = None) -> dict[str, Any]:
    """Return secret-safe, scoped evidence.  Missing evidence always stays false."""
    # Do not use caller-provided names in SQL or credential lookups.  The
    # checked-in static contract is the allowlist for every runtime query.
    try:
        validate, read_json = _static_validator()
        if validate(config):
            return {"scope": "runtime-provisioning-only; not a phase exit, canary authorization, or workflow acceptance",
                    "static_contract_valid": False, "jobs_runtime_identity_verified": False,
                    "declared_external_prerequisites_present": False,
                    "external_prerequisites_authenticated": False}
    except (OSError, ValueError, RuntimeError):
        return {"scope": "runtime-provisioning-only; not a phase exit, canary authorization, or workflow acceptance",
                "static_contract_valid": False, "jobs_runtime_identity_verified": False,
                "declared_external_prerequisites_present": False,
                "external_prerequisites_authenticated": False}
    db_keys = env_key_presence(db_env)
    provider_keys = env_key_presence(provider_env)
    jobs_env = config["routine_jobs"]["credential_env"]
    backup_env = config["routine_backup"]["credential_env"]
    authority_env = config["authority"]["environment_variables"]
    route_config = config["providers"]["routes"]
    result: dict[str, Any] = {
        "scope": "runtime-provisioning-only; not a phase exit, canary authorization, or workflow acceptance",
        "static_contract_valid": False,
        "files": {"db_env": file_state(db_env), "provider_env": file_state(provider_env),
                  "backup_age_key": file_state(age_key), "backup_age_public_key": file_state(age_public_key, required_mode=0o644)},
        "authority": {actor: {"credential_present": key_present(name, db_keys, environ)} for actor, name in authority_env.items()},
        "authority_readiness": {
            "required_for_system_rollout": list(REQUIRED_SYSTEM_AUTHORITY_CREDENTIALS),
            "optional_nonblocking": list(OPTIONAL_AUTHORITY_CREDENTIALS),
        },
        "jobs": {"credential_present": key_present(jobs_env, db_keys, environ), "database": {"reachable": False}},
        "device_evidence": {"principals": {"observable_by_jobs": False, "active_count": None}, "immutable_receipt_counts": {}},
        "providers": {"selector_present": key_present(config["providers"]["file_selector_env"], provider_keys, environ),
                      "routes": {route: {"url_present": key_present(values["url_env"], provider_keys, environ),
                                         "token_present": key_present(values["token_env"], provider_keys, environ)}
                                 for route, values in route_config.items()}},
        "backup": {"credential_present": key_present(backup_env, db_keys, environ), "login_role_observable": None},
        "npi_taxonomy": {"required": None, "allowlist_count": 0, "policy_present_and_nonempty": False},
        "jobs_runtime_identity_verified": False,
        "declared_external_prerequisites_present": False,
        "external_prerequisites_authenticated": False,
    }
    try:
        result["static_contract_valid"] = True
        policy = read_json(REPO / "ops" / "config" / "npi-sweep-policy.v1.json")
        taxonomy = policy.get("taxonomy", {})
        codes = taxonomy.get("approved_codes", []) if isinstance(taxonomy, dict) else []
        result["npi_taxonomy"] = {"required": taxonomy.get("required") is True if isinstance(taxonomy, dict) else None,
                                  "allowlist_count": len(codes) if isinstance(codes, list) else 0,
                                  "policy_present_and_nonempty": isinstance(codes, list) and len(codes) > 0}
    except (OSError, ValueError, RuntimeError):
        result["static_contract_valid"] = False
    jobs_dsn = environ.get(jobs_env) or ("" if not db_keys.get(jobs_env) else "__from_file__")
    # Reading the actual DSN is intentionally deferred until it is needed; it
    # is never copied into the report or an exception.
    if jobs_dsn and connect is not None:
        if jobs_dsn == "__from_file__":
            jobs_dsn = _env_value(db_env, jobs_env)
        expected_jobs = config["routine_jobs"]["login_role"]
        if not jobs_dsn_has_expected_user(jobs_dsn, expected_jobs):
            result["jobs"]["database"] = {"reachable": False, "credential_identity_matches": False,
                                              "error": "credential_identity_mismatch"}
        else:
            result["jobs"]["database"]["credential_identity_matches"] = True
            try:
                database = _database_evidence(connect, jobs_dsn, config)
                database["credential_identity_matches"] = True
                result["jobs"]["database"] = database
                result["device_evidence"] = {"principals": database["device_principals"],
                                              "immutable_receipt_counts": database["immutable_receipt_counts"]}
                result["providers"]["registered_routes"] = database["provider_routes"]
                result["backup"]["login_role_observable"] = database["login_roles"].get(config["routine_backup"]["login_role"])
            except Exception:
                result["jobs"]["database"] = {"reachable": False, "credential_identity_matches": True,
                                                  "error": "unavailable_or_not_authorized"}
    database = result["jobs"]["database"]
    result["jobs_runtime_identity_verified"] = (
        database.get("credential_identity_matches") is True and database.get("jobs_identity_matches") is True)
    result["declared_external_prerequisites_present"] = declared_external_prerequisites_present(result)
    return result


def _env_value(path: Path, target: str) -> str:
    """Read one value internally for a local connection, never for reporting."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    for raw in lines:
        line = raw.strip().removeprefix("export ").lstrip()
        key, sep, value = line.partition("=")
        if sep and key == target:
            return strip_one_outer_quote(value)
    return ""


def main() -> int:
    parser = argparse.ArgumentParser(description="opt-in, read-only Control Plane runtime provisioning preflight")
    parser.add_argument("--runtime", action="store_true", help="allow local file checks and jobs-role read-only database queries")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--db-env", type=Path, default=DEFAULT_DB_ENV)
    parser.add_argument("--provider-env", type=Path, default=DEFAULT_PROVIDER_ENV)
    parser.add_argument("--age-key", type=Path, default=DEFAULT_AGE_KEY)
    parser.add_argument("--age-public-key", type=Path, default=DEFAULT_AGE_PUBLIC_KEY)
    args = parser.parse_args()
    if not args.runtime:
        print(json.dumps({"ok": False, "runtime_opt_in_required": True,
                          "scope": "no local files or database were read"}, sort_keys=True))
        return 2
    try:
        _, read_json = _static_validator()
        config = read_json(args.config)
        import psycopg
        report = collect_runtime(config, db_env=args.db_env, provider_env=args.provider_env, age_key=args.age_key,
                                 age_public_key=args.age_public_key, environ=os.environ,
                                 connect=cast(Connect, psycopg.connect))
    except (ImportError, OSError, ValueError, RuntimeError):
        print(json.dumps({"ok": False, "scope": "runtime-provisioning-only", "error": "preflight_unavailable"}, sort_keys=True))
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0 if report["jobs_runtime_identity_verified"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
