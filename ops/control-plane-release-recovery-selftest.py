#!/usr/bin/env python3
"""Hermetic checks for the procedure-only Control Plane recovery contract."""

from __future__ import annotations

import copy
import json
import tempfile
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
CONTRACT_PATH = REPO / "ops/config/control-plane-release-recovery.v1.json"
SCHEMA_PATH = REPO / "ops/config/control-plane-release-recovery.schema.v1.json"
FAILURES: list[str] = []
MIGRATIONS = [
    "0193_session_work", "0194_atomic_rule_approval", "0195_control_plane_cache_observations",
    "0199_guidance_standing_context_boundary", "0212_doctrine_meta_singleton",
    "0215_program5_completion_hash_grant", "0220_calendar_canary_record_layer",
    "0221_nightly_availability_canary_record_layer", "0222_atomic_rule_lifecycle_forward_upgrade",
    "0223_calendar_prebrief_projection", "0224_renewal_decision_delivery",
]
STEPS = [
    {"id": "contain", "action_kind": "contain"},
    {"id": "repair", "action_kind": "repair"},
    {"id": "verify", "action_kind": "verify"},
]
PATHS = [
    "migrations/0172_program5_release_assurance.sql", "tools/release-manifest.py",
    "tools/ops-record.py", "bin/deploy-worker.sh", "ops/verify-worker-release.py",
    "bin/migrate-prod.sh",
]


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  ok    {name}")
    else:
        FAILURES.append(name)
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))


def validate(value: Any, schema: dict[str, Any], path: str = "$") -> list[str]:
    """Dependency-free validator for precisely the JSON Schema subset used here."""
    errors: list[str] = []
    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: constant mismatch")
    types = schema.get("type")
    if types:
        allowed = types if isinstance(types, list) else [types]
        matches = {"object": isinstance(value, dict), "array": isinstance(value, list),
                   "string": isinstance(value, str), "boolean": isinstance(value, bool),
                   "integer": isinstance(value, int) and not isinstance(value, bool)}
        if not any(matches.get(kind, False) for kind in allowed):
            return errors + [f"{path}: wrong type"]
    if isinstance(value, str) and len(value) < schema.get("minLength", 0):
        errors.append(f"{path}: empty string")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0): errors.append(f"{path}: too few items")
        if "items" in schema:
            for i, item in enumerate(value): errors.extend(validate(item, schema["items"], f"{path}[{i}]"))
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value: errors.append(f"{path}: missing {key}")
        props = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            for key in value:
                if key not in props: errors.append(f"{path}: unknown {key}")
        for key, child in props.items():
            if key in value: errors.extend(validate(value[key], child, f"{path}.{key}"))
    return errors


def contains_receipt_or_evidence(value: Any) -> bool:
    if isinstance(value, dict):
        return any("receipt" in str(k).lower() or "evidence" in str(k).lower() or contains_receipt_or_evidence(v)
                   for k, v in value.items())
    if isinstance(value, list): return any(contains_receipt_or_evidence(v) for v in value)
    return isinstance(value, str) and ("receipt" in value.lower() or "evidence" in value.lower())


def missing_paths(paths: list[str]) -> list[str]:
    return [path for path in paths if not (REPO / path).is_file()]


def schema_closed(schema: dict[str, Any]) -> bool:
    if schema.get("type") == "object" and schema.get("additionalProperties") is not False:
        return False
    for child in schema.get("properties", {}).values():
        if not schema_closed(child): return False
    if isinstance(schema.get("items"), dict) and not schema_closed(schema["items"]): return False
    return True


def rejected(name: str, candidate: dict[str, Any], schema: dict[str, Any]) -> None:
    check(name, bool(validate(candidate, schema) or contains_receipt_or_evidence(candidate)))


def main() -> int:
    print("control-plane-release-recovery-selftest: procedure claims fail closed")
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    check("1. canonical procedure validates", not validate(contract, schema))
    check("2. schema is closed at every object", schema_closed(schema))
    check("3. schema itself pins exact migration order", schema["properties"]["candidate"]["properties"]["migrations"]["const"] == MIGRATIONS)
    check("4. contract pins the reconciled migration list", contract["candidate"]["migrations"] == MIGRATIONS)
    check("5. every candidate migration file exists", not missing_paths([f"migrations/{migration}.sql" for migration in MIGRATIONS]))
    check("6. canonical procedure has no receipt or evidence claim", not contains_receipt_or_evidence(contract))
    check("7. schema version is an integer constant", schema["properties"]["schema_version"].get("type") == "integer")
    check("8. exact forward-fix steps are pinned", contract["forward_fix_steps"] == STEPS)
    check("9. every referenced enforcement path exists", not missing_paths(contract["existing_promotion_contract_paths"]))
    check("10. existing enforcement paths are pinned", contract["existing_promotion_contract_paths"] == PATHS)
    check("11. worker rollback is not authorized here", contract["previous_worker_traffic_rollback"] == "not_authorized_by_this_plan")
    check("12. recovery requires a fresh upload, never version promotion", contract["provider_version_recovery_path"] == "fresh_upload_only" and contract["existing_version_promotion"] == "forbidden_by_this_plan")

    with tempfile.TemporaryDirectory() as raw:
        copied = Path(raw) / "procedure.json"; copied.write_text(json.dumps(contract), encoding="utf-8")
        check("13. real procedure parses as a local artifact", json.loads(copied.read_text(encoding="utf-8")) == contract)

    bad = copy.deepcopy(contract); bad["candidate"]["migrations"].reverse()
    rejected("14. migration order mutation is refused", bad, schema)
    bad = copy.deepcopy(contract); bad["candidate"]["migrations"] = list(MIGRATIONS); bad["candidate"]["migrations"].remove("0212_doctrine_meta_singleton")
    rejected("15. omission of the main candidate migration is refused", bad, schema)
    bad = copy.deepcopy(contract); bad["candidate"]["migrations"] = list(MIGRATIONS); bad["candidate"]["migrations"][-2:] = list(reversed(bad["candidate"]["migrations"][-2:]))
    rejected("16. reordered calendar and renewal migrations are refused", bad, schema)
    bad = copy.deepcopy(contract); bad["candidate"]["migrations"] = list(MIGRATIONS); bad["candidate"]["migrations"][-1] = "0224_renewal_decision_delivery_replayed"
    rejected("17. substituted delivery migration is refused", bad, schema)
    bad = copy.deepcopy(contract); bad["forward_fix_steps"][1]["id"] = "retry"
    rejected("18. step ID mutation is refused", bad, schema)
    bad = copy.deepcopy(contract); bad["forward_fix_steps"] = [{"id": "be-careful", "action_kind": "prose"}]
    rejected("19. generic steps are refused", bad, schema)
    bad = copy.deepcopy(contract); bad["previous_worker_traffic_rollback"] = "authorized"
    rejected("20. rollback authorization is refused", bad, schema)
    bad = copy.deepcopy(contract); bad["rollback_receipt_ref"] = "invented"
    rejected("21. invented receipt fields are refused", bad, schema)
    bad = copy.deepcopy(contract); bad["calendar_activation"] = {"workflow": "calendar-prebrief-projection-joe-daily"}
    rejected("22. workflow activation claims are refused", bad, schema)
    bad = copy.deepcopy(contract); bad["existing_promotion_contract_paths"][0] = "invented/path"
    rejected("23. enforcement-path mutation is refused", bad, schema)
    bad = copy.deepcopy(contract); bad["provider_version_id"] = "locally-invented"
    rejected("24. locally fabricated provider IDs are refused", bad, schema)
    bad = copy.deepcopy(contract); bad["schema_version"] = True
    rejected("25. boolean schema version is refused", bad, schema)
    bad = copy.deepcopy(contract); bad["existing_promotion_contract_paths"][0] = "missing/not-a-contract"
    check("26. missing referenced path is detected and rejected", bool(missing_paths(bad["existing_promotion_contract_paths"])) and bool(validate(bad, schema)))

    if FAILURES:
        print(f"control-plane-release-recovery-selftest: {len(FAILURES)} FAILED")
        return 1
    print("control-plane-release-recovery-selftest: all procedure boundaries hold")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
