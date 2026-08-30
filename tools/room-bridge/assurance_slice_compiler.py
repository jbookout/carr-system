"""Pure Assurance Slice Compiler v1.

Compiles one already-canonical Work Request and Engineering Slice into a
deterministic, non-authorizing execution manifest.  The module performs no I/O.
"""

from __future__ import annotations

import copy
import re
from datetime import datetime, timezone
from typing import Any, NoReturn

import engineering_passport
import execution_contract


COMPILER_ID = "carr-assurance-slice-compiler"
COMPILER_VERSION = "1.0.0"
INPUT_SCHEMA = "assurance-compiler-input.v1"
CONTRACT_SCHEMA = "assurance-slice-contract.v1"
MANIFEST_SCHEMA = "assurance-execution-manifest.v1"

REFUSAL_CODES = (
    "INPUT_NOT_OBJECT", "INPUT_UNKNOWN_FIELD", "INPUT_MISSING_FIELD", "INPUT_SCHEMA_UNSUPPORTED",
    "FIELD_INVALID", "ENGINEERING_SLICE_PLAN_INVALID", "ASSURANCE_SLICE_ABSENT",
    "ASSURANCE_CONTRACT_DIGEST_MISMATCH", "WORK_REQUEST_BINDING_MISMATCH",
    "ACCEPTED_PLAN_BINDING_MISMATCH", "ENGINEERING_SLICE_PLAN_BINDING_MISMATCH",
    "SLICE_BINDING_MISMATCH", "REPOSITORY_IDENTITY_MISMATCH", "RULE_SNAPSHOT_DIGEST_MISMATCH",
    "RULE_SNAPSHOT_STALE", "COORDINATION_SNAPSHOT_DIGEST_MISMATCH", "COORDINATION_SNAPSHOT_STALE",
    "LEASE_NOT_FOUND", "LEASE_RELEASED", "LEASE_EXPIRED", "LEASE_BINDING_MISMATCH",
    "LEASE_CLAIMS_MISMATCH", "FOREIGN_LEASE_COLLISION", "DEPENDENCY_MISSING",
    "DEPENDENCY_UNSATISFIED", "PATH_INVALID", "PATH_CASE_ALIAS", "PATH_SCOPE_COLLISION",
    "REVIEWER_POLICY_INVALID", "COMPILER_INTERNAL_ERROR",
)

_INPUT_FIELDS = {
    "schema_version", "work_request", "accepted_plan_revision", "engineering_slice_plan",
    "assurance_slice", "repository", "applicable_rules", "coordination_snapshot",
}
_CONTRACT_FIELDS = {
    "schema_version", "contract_digest", "work_request", "accepted_plan_revision",
    "engineering_slice_plan_digest", "slice_ref", "outcome", "risk", "path_claims",
    "forbidden_paths", "dependencies", "required_tests", "evidence_requirements",
    "reviewer_policy", "observable_output", "rollback", "release_class", "unfinished_work",
    "repository_binding", "rule_snapshot_binding", "lease_binding", "executor_identity",
}
_ID = execution_contract.ID
_DIGEST = execution_contract.SHA256
_SHA = re.compile(r"^[0-9a-f]{40}$")
_GLOB = re.compile(r"[*?\[\]{}!]")


class _Refusal(Exception):
    def __init__(self, code: str, causal_object: str, expected: Any, actual: Any):
        super().__init__(code)
        self.fact = {"code": code, "causal_object": causal_object, "expected": expected, "actual": actual}


def _refuse(code: str, causal_object: str, expected: Any, actual: Any) -> NoReturn:
    raise _Refusal(code, causal_object, expected, actual)


def _exact(value: Any, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _refuse("FIELD_INVALID", label, "object", type(value).__name__)
    unknown = sorted(set(value) - fields)
    if unknown:
        _refuse("INPUT_UNKNOWN_FIELD", label, sorted(fields), unknown)
    missing = sorted(fields - set(value))
    if missing:
        _refuse("INPUT_MISSING_FIELD", label, sorted(fields), missing)
    return value


def _string(value: Any, label: str, *, identifier: bool = False) -> str:
    valid = isinstance(value, str) and bool(value.strip())
    if identifier:
        valid = valid and bool(_ID.fullmatch(value))
    if not valid:
        _refuse("FIELD_INVALID", label, "non-empty opaque identifier" if identifier else "non-empty string", value)
    return value


def _digest(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        _refuse("FIELD_INVALID", label, "sha256:<64 lowercase hex characters>", value)
    return value


def _positive_int(value: Any, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        _refuse("FIELD_INVALID", label, "positive integer", value)
    return value


def _timestamp(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not execution_contract.TIMESTAMP.fullmatch(value):
        _refuse("FIELD_INVALID", label, "UTC timestamp to whole seconds", value)
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _sort_set(rows: list[Any]) -> list[Any]:
    return sorted(rows, key=execution_contract.canonical_digest)


def _unique(rows: list[Any], label: str, *, key: str | None = None) -> None:
    values = [row[key] if key is not None and isinstance(row, dict) and key in row else execution_contract.canonical_digest(row) for row in rows]
    duplicates = sorted({str(item) for item in values if values.count(item) > 1})
    if duplicates:
        _refuse("FIELD_INVALID", label, "unique set-like items", duplicates)


def _normalized_contract(raw: dict[str, Any]) -> dict[str, Any]:
    value = copy.deepcopy(raw)
    for field in ("path_claims", "forbidden_paths", "dependencies", "required_tests", "evidence_requirements", "unfinished_work"):
        value[field] = _sort_set(value[field])
    for test in value["required_tests"]:
        test["evidence_fields"] = sorted(test["evidence_fields"])
    return value


def _normalized_input(raw: dict[str, Any]) -> dict[str, Any]:
    value = copy.deepcopy(raw)
    value["assurance_slice"] = _normalized_contract(value["assurance_slice"])
    value["applicable_rules"]["rules"] = _sort_set(value["applicable_rules"]["rules"])
    coord = value["coordination_snapshot"]
    coord["dependencies"] = _sort_set(coord["dependencies"])
    coord["leases"] = _sort_set(coord["leases"])
    for lease in coord["leases"]:
        lease["claims"] = _sort_set(lease["claims"])
    return value


def _binding(value: Any, label: str) -> dict[str, Any]:
    row = _exact(value, {"id", "state_version", "canonical_record_digest"}, label)
    _string(row["id"], label + ".id", identifier=True)
    _positive_int(row["state_version"], label + ".state_version")
    _digest(row["canonical_record_digest"], label + ".canonical_record_digest")
    return row


def _plan_binding(value: Any, label: str) -> dict[str, Any]:
    row = _exact(value, {"id", "revision", "digest"}, label)
    _string(row["id"], label + ".id", identifier=True)
    _positive_int(row["revision"], label + ".revision")
    _digest(row["digest"], label + ".digest")
    return row


def _repository(value: Any, label: str) -> dict[str, str]:
    row = _exact(value, {"repository_id", "commit_sha", "tree_sha"}, label)
    _string(row["repository_id"], label + ".repository_id", identifier=True)
    for field in ("commit_sha", "tree_sha"):
        if not isinstance(row[field], str) or not _SHA.fullmatch(row[field]):
            _refuse("FIELD_INVALID", f"{label}.{field}", "40 lowercase hex characters", row[field])
    return row


def _path(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or not value.isascii():
        _refuse("PATH_INVALID", label, "non-empty ASCII repo-relative path", value)
    parts = value.split("/")
    if value.startswith("/") or value.endswith("/") or "\\" in value or _GLOB.search(value) or any(p in {"", ".", ".."} for p in parts):
        _refuse("PATH_INVALID", label, "normalized repo-relative path without globs, dot components, or backslashes", value)
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        _refuse("PATH_INVALID", label, "printable ASCII repo-relative path", value)
    return value


def _claim(value: Any, label: str, *, forbidden: bool = False) -> dict[str, Any]:
    fields = {"path", "mode"} if forbidden else {"path", "mode", "operation"}
    row = _exact(value, fields, label)
    _path(row["path"], label + ".path")
    if row["mode"] not in {"file", "tree"}:
        _refuse("FIELD_INVALID", label + ".mode", ["file", "tree"], row["mode"])
    if not forbidden and row["operation"] not in {"write", "rename_source", "rename_destination"}:
        _refuse("FIELD_INVALID", label + ".operation", ["write", "rename_source", "rename_destination"], row["operation"])
    return row


def _paths_overlap(a: dict[str, Any], b: dict[str, Any]) -> bool:
    ap, bp = a["path"], b["path"]
    return ap == bp or (a["mode"] == "tree" and bp.startswith(ap + "/")) or (b["mode"] == "tree" and ap.startswith(bp + "/"))


def _validate_paths(allowed: list[dict[str, Any]], forbidden: list[dict[str, Any]]) -> None:
    seen: list[str] = []
    for index, row in enumerate(allowed):
        _claim(row, f"assurance_slice.path_claims[{index}]")
        seen.append(row["path"])
    for index, row in enumerate(forbidden):
        _claim(row, f"assurance_slice.forbidden_paths[{index}]", forbidden=True)
        seen.append(row["path"])
    for index, left in enumerate(seen):
        for right in seen[index + 1:]:
            lparts, rparts = left.split("/"), right.split("/")
            if any(a.lower() == b.lower() and a != b for a, b in zip(lparts, rparts)):
                _refuse("PATH_CASE_ALIAS", "assurance_slice.path_claims", "one canonical path case", [left, right])
    for allow in allowed:
        for deny in forbidden:
            if allow["path"] == deny["path"] or allow["path"].startswith(deny["path"] + "/") or deny["path"].startswith(allow["path"] + "/"):
                _refuse("PATH_SCOPE_COLLISION", allow["path"], "no allowed/forbidden component ancestry overlap", deny["path"])


def _validate_contract(value: Any) -> dict[str, Any]:
    if value is None:
        _refuse("ASSURANCE_SLICE_ABSENT", "assurance_slice", CONTRACT_SCHEMA, None)
    row = _exact(value, _CONTRACT_FIELDS, "assurance_slice")
    if row["schema_version"] != CONTRACT_SCHEMA:
        _refuse("INPUT_SCHEMA_UNSUPPORTED", "assurance_slice.schema_version", CONTRACT_SCHEMA, row["schema_version"])
    _digest(row["contract_digest"], "assurance_slice.contract_digest")
    _binding(row["work_request"], "assurance_slice.work_request")
    _plan_binding(row["accepted_plan_revision"], "assurance_slice.accepted_plan_revision")
    _digest(row["engineering_slice_plan_digest"], "assurance_slice.engineering_slice_plan_digest")
    _string(row["slice_ref"], "assurance_slice.slice_ref", identifier=True)
    _string(row["outcome"], "assurance_slice.outcome")
    risk = _exact(row["risk"], {"risk_class", "summary"}, "assurance_slice.risk")
    if risk["risk_class"] not in {f"R{i}" for i in range(7)}:
        _refuse("FIELD_INVALID", "assurance_slice.risk.risk_class", "R0..R6", risk["risk_class"])
    _string(risk["summary"], "assurance_slice.risk.summary")
    if not isinstance(row["path_claims"], list) or not row["path_claims"]:
        _refuse("FIELD_INVALID", "assurance_slice.path_claims", "non-empty list", row["path_claims"])
    if not isinstance(row["forbidden_paths"], list) or not row["forbidden_paths"]:
        _refuse("FIELD_INVALID", "assurance_slice.forbidden_paths", "non-empty list", row["forbidden_paths"])
    _unique(row["path_claims"], "assurance_slice.path_claims")
    _unique(row["forbidden_paths"], "assurance_slice.forbidden_paths")
    _validate_paths(row["path_claims"], row["forbidden_paths"])
    if not isinstance(row["dependencies"], list):
        _refuse("FIELD_INVALID", "assurance_slice.dependencies", "list", row["dependencies"])
    _unique(row["dependencies"], "assurance_slice.dependencies", key="slice_ref")
    for index, dep in enumerate(row["dependencies"]):
        item = _exact(dep, {"slice_ref", "required_state"}, f"assurance_slice.dependencies[{index}]")
        _string(item["slice_ref"], f"assurance_slice.dependencies[{index}].slice_ref", identifier=True)
        if item["required_state"] not in {"completed", "independently_verified"}:
            _refuse("FIELD_INVALID", f"assurance_slice.dependencies[{index}].required_state", ["completed", "independently_verified"], item["required_state"])
    if not isinstance(row["required_tests"], list) or not row["required_tests"]:
        _refuse("FIELD_INVALID", "assurance_slice.required_tests", "non-empty list", row["required_tests"])
    _unique(row["required_tests"], "assurance_slice.required_tests", key="check_ref")
    for index, test in enumerate(row["required_tests"]):
        item = _exact(test, {"check_ref", "argv", "cwd", "causal_failure", "evidence_fields"}, f"assurance_slice.required_tests[{index}]")
        _string(item["check_ref"], f"assurance_slice.required_tests[{index}].check_ref", identifier=True)
        if not isinstance(item["argv"], list) or not item["argv"] or not all(isinstance(x, str) and x for x in item["argv"]):
            _refuse("FIELD_INVALID", f"assurance_slice.required_tests[{index}].argv", "non-empty argv string list", item["argv"])
        _path(item["cwd"], f"assurance_slice.required_tests[{index}].cwd")
        failure = _exact(item["causal_failure"], {"code", "object", "expected"}, f"assurance_slice.required_tests[{index}].causal_failure")
        for field in failure:
            _string(failure[field], f"assurance_slice.required_tests[{index}].causal_failure.{field}")
        if not isinstance(item["evidence_fields"], list) or not item["evidence_fields"] or not all(isinstance(x, str) and x for x in item["evidence_fields"]):
            _refuse("FIELD_INVALID", f"assurance_slice.required_tests[{index}].evidence_fields", "non-empty string list", item["evidence_fields"])
    if not isinstance(row["evidence_requirements"], list) or not row["evidence_requirements"]:
        _refuse("FIELD_INVALID", "assurance_slice.evidence_requirements", "non-empty list", row["evidence_requirements"])
    _unique(row["evidence_requirements"], "assurance_slice.evidence_requirements", key="evidence_ref")
    for index, evidence in enumerate(row["evidence_requirements"]):
        item = _exact(evidence, {"evidence_ref", "artifact_kind", "required_fields"}, f"assurance_slice.evidence_requirements[{index}]")
        _string(item["evidence_ref"], f"assurance_slice.evidence_requirements[{index}].evidence_ref", identifier=True)
        _string(item["artifact_kind"], f"assurance_slice.evidence_requirements[{index}].artifact_kind", identifier=True)
        if not isinstance(item["required_fields"], list) or not item["required_fields"]:
            _refuse("FIELD_INVALID", f"assurance_slice.evidence_requirements[{index}].required_fields", "non-empty list", item["required_fields"])
    policy = _exact(row["reviewer_policy"], {"minimum_independent_reviewers", "executor_actor_ref", "executor_session_ref", "owner_acceptance_is_review", "distinct_actor_and_session"}, "assurance_slice.reviewer_policy")
    _positive_int(policy["minimum_independent_reviewers"], "assurance_slice.reviewer_policy.minimum_independent_reviewers")
    _string(policy["executor_actor_ref"], "assurance_slice.reviewer_policy.executor_actor_ref", identifier=True)
    _string(policy["executor_session_ref"], "assurance_slice.reviewer_policy.executor_session_ref", identifier=True)
    if policy["owner_acceptance_is_review"] is not False or policy["distinct_actor_and_session"] is not True:
        _refuse("REVIEWER_POLICY_INVALID", "assurance_slice.reviewer_policy", {"owner_acceptance_is_review": False, "distinct_actor_and_session": True}, policy)
    for field, fields in (("observable_output", {"description", "evidence_ref"}), ("rollback", {"strategy", "argv", "cwd", "observable_success"})):
        _exact(row[field], fields, f"assurance_slice.{field}")
    _string(row["observable_output"]["description"], "assurance_slice.observable_output.description")
    _string(row["observable_output"]["evidence_ref"], "assurance_slice.observable_output.evidence_ref", identifier=True)
    rollback = row["rollback"]
    _string(rollback["strategy"], "assurance_slice.rollback.strategy")
    if not isinstance(rollback["argv"], list) or not rollback["argv"] or not all(isinstance(x, str) and x for x in rollback["argv"]):
        _refuse("FIELD_INVALID", "assurance_slice.rollback.argv", "non-empty argv string list", rollback["argv"])
    _path(rollback["cwd"], "assurance_slice.rollback.cwd")
    _string(rollback["observable_success"], "assurance_slice.rollback.observable_success")
    if row["release_class"] not in {"none", "repository_only", "runtime", "production"}:
        _refuse("FIELD_INVALID", "assurance_slice.release_class", ["none", "repository_only", "runtime", "production"], row["release_class"])
    if not isinstance(row["unfinished_work"], list) or not all(isinstance(x, str) and x for x in row["unfinished_work"]):
        _refuse("FIELD_INVALID", "assurance_slice.unfinished_work", "string list", row["unfinished_work"])
    _repository(row["repository_binding"], "assurance_slice.repository_binding")
    rules = _exact(row["rule_snapshot_binding"], {"snapshot_ref", "snapshot_digest"}, "assurance_slice.rule_snapshot_binding")
    _string(rules["snapshot_ref"], "assurance_slice.rule_snapshot_binding.snapshot_ref", identifier=True)
    _digest(rules["snapshot_digest"], "assurance_slice.rule_snapshot_binding.snapshot_digest")
    lease = _exact(row["lease_binding"], {"lease_id", "fencing_generation", "holder_session_id", "holder_host_id"}, "assurance_slice.lease_binding")
    for field in ("lease_id", "holder_session_id", "holder_host_id"):
        _string(lease[field], f"assurance_slice.lease_binding.{field}", identifier=True)
    _positive_int(lease["fencing_generation"], "assurance_slice.lease_binding.fencing_generation")
    identity = _exact(row["executor_identity"], {"actor_ref", "session_ref", "host_ref"}, "assurance_slice.executor_identity")
    for field in identity:
        _string(identity[field], f"assurance_slice.executor_identity.{field}", identifier=True)
    normalized = _normalized_contract(row)
    without_digest = {key: item for key, item in normalized.items() if key != "contract_digest"}
    actual = execution_contract.canonical_digest(without_digest)
    if row["contract_digest"] != actual:
        _refuse("ASSURANCE_CONTRACT_DIGEST_MISMATCH", "assurance_slice.contract_digest", actual, row["contract_digest"])
    return normalized


def _validate_rules(value: Any) -> dict[str, Any]:
    row = _exact(value, {"schema_version", "snapshot_ref", "snapshot_digest", "rules"}, "applicable_rules")
    if row["schema_version"] != "applicable-rule-snapshot.v1":
        _refuse("INPUT_SCHEMA_UNSUPPORTED", "applicable_rules.schema_version", "applicable-rule-snapshot.v1", row["schema_version"])
    _string(row["snapshot_ref"], "applicable_rules.snapshot_ref", identifier=True)
    _digest(row["snapshot_digest"], "applicable_rules.snapshot_digest")
    if not isinstance(row["rules"], list):
        _refuse("FIELD_INVALID", "applicable_rules.rules", "list", row["rules"])
    _unique(row["rules"], "applicable_rules.rules", key="rule_ref")
    for index, rule in enumerate(row["rules"]):
        item = _exact(rule, {"rule_ref", "revision", "digest"}, f"applicable_rules.rules[{index}]")
        _string(item["rule_ref"], f"applicable_rules.rules[{index}].rule_ref", identifier=True)
        _positive_int(item["revision"], f"applicable_rules.rules[{index}].revision")
        _digest(item["digest"], f"applicable_rules.rules[{index}].digest")
    normalized_rules = _sort_set(row["rules"])
    actual = execution_contract.canonical_digest({"snapshot_ref": row["snapshot_ref"], "rules": normalized_rules})
    if row["snapshot_digest"] != actual:
        _refuse("RULE_SNAPSHOT_DIGEST_MISMATCH", "applicable_rules.snapshot_digest", actual, row["snapshot_digest"])
    result = copy.deepcopy(row); result["rules"] = normalized_rules
    return result


def _validate_coordination(value: Any) -> dict[str, Any]:
    fields = {"schema_version", "snapshot_digest", "as_of", "valid_until", "manifest_phase", "requesting_session_id", "requesting_host_id", "leases", "dependencies"}
    row = _exact(value, fields, "coordination_snapshot")
    if row["schema_version"] != "assurance-coordination-snapshot.v1":
        _refuse("INPUT_SCHEMA_UNSUPPORTED", "coordination_snapshot.schema_version", "assurance-coordination-snapshot.v1", row["schema_version"])
    _digest(row["snapshot_digest"], "coordination_snapshot.snapshot_digest")
    as_of = _timestamp(row["as_of"], "coordination_snapshot.as_of")
    valid_until = _timestamp(row["valid_until"], "coordination_snapshot.valid_until")
    if valid_until <= as_of:
        _refuse("COORDINATION_SNAPSHOT_STALE", "coordination_snapshot.valid_until", f"> {row['as_of']}", row["valid_until"])
    if row["manifest_phase"] != "baseline":
        _refuse("FIELD_INVALID", "coordination_snapshot.manifest_phase", "baseline", row["manifest_phase"])
    for field in ("requesting_session_id", "requesting_host_id"):
        _string(row[field], f"coordination_snapshot.{field}", identifier=True)
    if not isinstance(row["leases"], list):
        _refuse("FIELD_INVALID", "coordination_snapshot.leases", "list", row["leases"])
    _unique(row["leases"], "coordination_snapshot.leases", key="lease_id")
    for index, lease in enumerate(row["leases"]):
        item = _exact(lease, {"lease_id", "state", "holder_session_id", "holder_host_id", "expires_at", "fencing_generation", "claims"}, f"coordination_snapshot.leases[{index}]")
        for field in ("lease_id", "holder_session_id", "holder_host_id"):
            _string(item[field], f"coordination_snapshot.leases[{index}].{field}", identifier=True)
        if item["state"] not in {"active", "released"}:
            _refuse("FIELD_INVALID", f"coordination_snapshot.leases[{index}].state", ["active", "released"], item["state"])
        _timestamp(item["expires_at"], f"coordination_snapshot.leases[{index}].expires_at")
        _positive_int(item["fencing_generation"], f"coordination_snapshot.leases[{index}].fencing_generation")
        if not isinstance(item["claims"], list):
            _refuse("FIELD_INVALID", f"coordination_snapshot.leases[{index}].claims", "list", item["claims"])
        _unique(item["claims"], f"coordination_snapshot.leases[{index}].claims")
        for claim_index, claim in enumerate(item["claims"]):
            _claim(claim, f"coordination_snapshot.leases[{index}].claims[{claim_index}]")
    if not isinstance(row["dependencies"], list):
        _refuse("FIELD_INVALID", "coordination_snapshot.dependencies", "list", row["dependencies"])
    _unique(row["dependencies"], "coordination_snapshot.dependencies", key="slice_ref")
    for index, dep in enumerate(row["dependencies"]):
        item = _exact(dep, {"slice_ref", "state", "evidence_digest"}, f"coordination_snapshot.dependencies[{index}]")
        _string(item["slice_ref"], f"coordination_snapshot.dependencies[{index}].slice_ref", identifier=True)
        if item["state"] not in {"pending", "completed", "independently_verified"}:
            _refuse("FIELD_INVALID", f"coordination_snapshot.dependencies[{index}].state", ["pending", "completed", "independently_verified"], item["state"])
        if item["evidence_digest"] is not None:
            _digest(item["evidence_digest"], f"coordination_snapshot.dependencies[{index}].evidence_digest")
    normalized = copy.deepcopy(row)
    normalized["leases"] = _sort_set(normalized["leases"])
    for lease in normalized["leases"]:
        lease["claims"] = _sort_set(lease["claims"])
    normalized["dependencies"] = _sort_set(normalized["dependencies"])
    actual = execution_contract.canonical_digest({key: item for key, item in normalized.items() if key != "snapshot_digest"})
    if row["snapshot_digest"] != actual:
        _refuse("COORDINATION_SNAPSHOT_DIGEST_MISMATCH", "coordination_snapshot.snapshot_digest", actual, row["snapshot_digest"])
    return normalized


def _enforce_bindings(value: dict[str, Any], contract: dict[str, Any], plan: dict[str, Any], rules: dict[str, Any], coord: dict[str, Any]) -> dict[str, Any]:
    if value["work_request"] != plan["work_request"] or contract["work_request"] != value["work_request"]:
        _refuse("WORK_REQUEST_BINDING_MISMATCH", "work_request", plan["work_request"], {"input": value["work_request"], "contract": contract["work_request"]})
    if value["accepted_plan_revision"] != plan["accepted_plan_revision"] or contract["accepted_plan_revision"] != value["accepted_plan_revision"]:
        _refuse("ACCEPTED_PLAN_BINDING_MISMATCH", "accepted_plan_revision", plan["accepted_plan_revision"], {"input": value["accepted_plan_revision"], "contract": contract["accepted_plan_revision"]})
    if contract["engineering_slice_plan_digest"] != plan["plan_digest"]:
        _refuse("ENGINEERING_SLICE_PLAN_BINDING_MISMATCH", "assurance_slice.engineering_slice_plan_digest", plan["plan_digest"], contract["engineering_slice_plan_digest"])
    slices = {row["slice_ref"]: row for row in plan["slices"]}
    if contract["slice_ref"] not in slices:
        _refuse("SLICE_BINDING_MISMATCH", "assurance_slice.slice_ref", sorted(slices), contract["slice_ref"])
    selected = slices[contract["slice_ref"]]
    if selected["risk_class"] != contract["risk"]["risk_class"]:
        _refuse("SLICE_BINDING_MISMATCH", "assurance_slice.risk.risk_class", selected["risk_class"], contract["risk"]["risk_class"])
    if sorted(selected["dependency_refs"]) != sorted(dep["slice_ref"] for dep in contract["dependencies"]):
        _refuse("SLICE_BINDING_MISMATCH", "assurance_slice.dependencies", sorted(selected["dependency_refs"]), sorted(dep["slice_ref"] for dep in contract["dependencies"]))
    planned_checks = sorted(check["check_ref"] for check in selected["planned_checks"])
    required_checks = sorted(check["check_ref"] for check in contract["required_tests"])
    if planned_checks != required_checks:
        _refuse("SLICE_BINDING_MISMATCH", "assurance_slice.required_tests", planned_checks, required_checks)
    identity = contract["executor_identity"]
    policy = contract["reviewer_policy"]
    if policy["executor_actor_ref"] != identity["actor_ref"] or policy["executor_session_ref"] != identity["session_ref"]:
        _refuse("REVIEWER_POLICY_INVALID", "assurance_slice.reviewer_policy", {"executor_actor_ref": identity["actor_ref"], "executor_session_ref": identity["session_ref"]}, policy)
    lease_identity = contract["lease_binding"]
    if lease_identity["holder_session_id"] != identity["session_ref"] or lease_identity["holder_host_id"] != identity["host_ref"]:
        _refuse("LEASE_BINDING_MISMATCH", "assurance_slice.lease_binding", {"holder_session_id": identity["session_ref"], "holder_host_id": identity["host_ref"]}, lease_identity)
    repo = _repository(value["repository"], "repository")
    if repo != contract["repository_binding"]:
        _refuse("REPOSITORY_IDENTITY_MISMATCH", "repository", contract["repository_binding"], repo)
    expected_rules = contract["rule_snapshot_binding"]
    actual_rules = {"snapshot_ref": rules["snapshot_ref"], "snapshot_digest": rules["snapshot_digest"]}
    if expected_rules != actual_rules:
        _refuse("RULE_SNAPSHOT_STALE", "applicable_rules", expected_rules, actual_rules)
    lease_binding = contract["lease_binding"]
    lease = next((row for row in coord["leases"] if row["lease_id"] == lease_binding["lease_id"]), None)
    if lease is None:
        _refuse("LEASE_NOT_FOUND", "assurance_slice.lease_binding.lease_id", lease_binding["lease_id"], None)
    if lease["state"] == "released":
        _refuse("LEASE_RELEASED", f"lease:{lease['lease_id']}", "active", "released")
    if _timestamp(lease["expires_at"], "lease.expires_at") <= _timestamp(coord["as_of"], "coordination_snapshot.as_of"):
        _refuse("LEASE_EXPIRED", f"lease:{lease['lease_id']}", f"> {coord['as_of']}", lease["expires_at"])
    actual_binding = {key: lease[key] for key in ("lease_id", "fencing_generation", "holder_session_id", "holder_host_id")}
    if actual_binding != lease_binding or lease["holder_session_id"] != coord["requesting_session_id"] or lease["holder_host_id"] != coord["requesting_host_id"]:
        _refuse("LEASE_BINDING_MISMATCH", f"lease:{lease['lease_id']}", lease_binding, actual_binding)
    if _sort_set(lease["claims"]) != _sort_set(contract["path_claims"]):
        _refuse("LEASE_CLAIMS_MISMATCH", f"lease:{lease['lease_id']}.claims", _sort_set(contract["path_claims"]), _sort_set(lease["claims"]))
    as_of = _timestamp(coord["as_of"], "coordination_snapshot.as_of")
    for foreign in coord["leases"]:
        if foreign["lease_id"] == lease["lease_id"] or foreign["state"] == "released":
            continue
        if _timestamp(foreign["expires_at"], "foreign lease.expires_at") <= as_of:
            _refuse("LEASE_EXPIRED", f"lease:{foreign['lease_id']}", f"> {coord['as_of']}", foreign["expires_at"])
        for own_claim in contract["path_claims"]:
            for foreign_claim in foreign["claims"]:
                if _paths_overlap(own_claim, foreign_claim):
                    _refuse("FOREIGN_LEASE_COLLISION", own_claim["path"], {"lease_id": lease["lease_id"], "holder_session_id": lease["holder_session_id"]}, {"lease_id": foreign["lease_id"], "holder_session_id": foreign["holder_session_id"], "claim": foreign_claim})
    dependency_rows = {row["slice_ref"]: row for row in coord["dependencies"]}
    rank = {"pending": 0, "completed": 1, "independently_verified": 2}
    for dependency in contract["dependencies"]:
        actual = dependency_rows.get(dependency["slice_ref"])
        if actual is None:
            _refuse("DEPENDENCY_MISSING", dependency["slice_ref"], dependency["required_state"], None)
        if rank[actual["state"]] < rank[dependency["required_state"]] or actual["evidence_digest"] is None:
            _refuse("DEPENDENCY_UNSATISFIED", dependency["slice_ref"], dependency["required_state"], actual)
    return selected


def _compile(value: Any, *, compiler_version: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _refuse("INPUT_NOT_OBJECT", "compiler_input", "object", type(value).__name__)
    row = _exact(value, _INPUT_FIELDS, "compiler_input")
    if row["schema_version"] != INPUT_SCHEMA:
        _refuse("INPUT_SCHEMA_UNSUPPORTED", "compiler_input.schema_version", INPUT_SCHEMA, row["schema_version"])
    _binding(row["work_request"], "work_request")
    _plan_binding(row["accepted_plan_revision"], "accepted_plan_revision")
    if row["assurance_slice"] is None:
        _refuse("ASSURANCE_SLICE_ABSENT", "assurance_slice", CONTRACT_SCHEMA, None)
    try:
        plan = engineering_passport.validate_engineering_slice_plan(copy.deepcopy(row["engineering_slice_plan"]))
    except engineering_passport.EngineeringContractError as exc:
        _refuse("ENGINEERING_SLICE_PLAN_INVALID", "engineering_slice_plan", "valid engineering-slice-plan.v1", str(exc))
    contract = _validate_contract(row["assurance_slice"])
    rules = _validate_rules(row["applicable_rules"])
    coord = _validate_coordination(row["coordination_snapshot"])
    selected = _enforce_bindings(row, contract, plan, rules, coord)
    normalized_input = _normalized_input(row)
    input_digest = execution_contract.canonical_digest(normalized_input)
    manifest = {
        "schema_version": MANIFEST_SCHEMA,
        "compiler": {"id": COMPILER_ID, "version": compiler_version},
        "authority_state": "compiled_not_authorized",
        "verification_state": "unverified",
        "self_certification": False,
        "input_digest": input_digest,
        "input_bindings": {
            "work_request": copy.deepcopy(row["work_request"]),
            "accepted_plan_revision": copy.deepcopy(row["accepted_plan_revision"]),
            "engineering_slice_plan_digest": plan["plan_digest"],
            "assurance_slice_contract_digest": contract["contract_digest"],
            "repository": copy.deepcopy(row["repository"]),
            "applicable_rule_snapshot_digest": rules["snapshot_digest"],
            "coordination_snapshot_digest": coord["snapshot_digest"],
        },
        "slice": {
            "slice_ref": contract["slice_ref"], "objective": selected["objective"],
            "outcome": contract["outcome"], "risk": copy.deepcopy(contract["risk"]),
            "allowed_paths": copy.deepcopy(contract["path_claims"]),
            "forbidden_paths": copy.deepcopy(contract["forbidden_paths"]),
            "dependency_gates": copy.deepcopy(contract["dependencies"]),
            "required_tests": copy.deepcopy(contract["required_tests"]),
            "evidence_requirements": copy.deepcopy(contract["evidence_requirements"]),
            "reviewer_policy": copy.deepcopy(contract["reviewer_policy"]),
            "observable_output": copy.deepcopy(contract["observable_output"]),
            "rollback": copy.deepcopy(contract["rollback"]),
            "release_class": contract["release_class"],
            "unfinished_work": copy.deepcopy(contract["unfinished_work"]),
            "lease_binding": copy.deepcopy(contract["lease_binding"]),
            "executor_identity": copy.deepcopy(contract["executor_identity"]),
        },
        "currentness": {
            "manifest_phase": "baseline", "authorizes_action": False,
            "usable_only_as_preflight_for": ["write", "test"],
            "recompile_against_resulting_commit_tree_before": ["commit", "push", "pr_update", "review", "merge", "runtime_action"],
        },
        "refusal_vocabulary": list(REFUSAL_CODES),
    }
    manifest["manifest_hash"] = execution_contract.canonical_digest(manifest)
    return manifest


def compile_assurance_slice(value: Any, *, compiler_version: str = COMPILER_VERSION) -> dict[str, Any]:
    """Return a deterministic success or exact refusal object; never raise."""
    try:
        _string(compiler_version, "compiler_version")
        return {"ok": True, "manifest": _compile(copy.deepcopy(value), compiler_version=compiler_version)}
    except _Refusal as exc:
        return {"ok": False, "refusal": exc.fact}
    except Exception as exc:  # fail closed without exposing an exception to callers
        return {"ok": False, "refusal": {"code": "COMPILER_INTERNAL_ERROR", "causal_object": "compiler", "expected": "deterministic validated input", "actual": type(exc).__name__}}
