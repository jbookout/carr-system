"""Pure validation and projection rules for CARR's typed Guidance Registry.

This module deliberately performs no database or network I/O.  The versioned
registry is validated once, then every consumer receives a deterministic
projection from the same primary classification.
"""
from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from typing import Any, Iterable


GUIDANCE_TYPES = (
    "constraint",
    "procedure",
    "doctrine",
    "rubric",
    "preference",
    "precedent",
    "example",
)

DELIVERY_PROJECTIONS = {
    "constraint": "constraint_enforcement",
    "procedure": "procedure_workflow",
    "doctrine": "doctrine_retrieval",
    "rubric": "verification_rubric",
    "preference": "scoped_preference",
    "precedent": "precedent_search",
    "example": "example_retrieval",
}

LIFECYCLE_STATUSES = {"proposed", "active", "retired", "superseded"}


def _present(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return value is not None


def _required(item: dict[str, Any], path: tuple[str, ...], label: str,
              errors: list[str]) -> Any:
    value: Any = item
    for key in path:
        if not isinstance(value, dict) or key not in value:
            errors.append(f"{item.get('guidance_id', '<unknown>')}: missing {label}")
            return None
        value = value[key]
    if not _present(value):
        errors.append(f"{item.get('guidance_id', '<unknown>')}: empty {label}")
    return value


def validate_registry(data: dict[str, Any]) -> list[str]:
    """Return every contract error; an empty list means the registry is valid."""
    errors: list[str] = []
    if data.get("registry") != "carr-guidance-registry":
        errors.append("registry: expected carr-guidance-registry")
    if data.get("schema_version") != "1.0.0":
        errors.append("registry: unsupported schema version")

    items = data.get("items")
    if not isinstance(items, list) or not items:
        return errors + ["registry: items must be a non-empty array"]

    guidance_ids: set[str] = set()
    source_items: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for raw in items:
        if not isinstance(raw, dict):
            errors.append("registry: every item must be an object")
            continue
        item = raw
        guidance_id = _required(item, ("guidance_id",), "guidance id", errors)
        source_id = _required(item, ("source_id",), "stable source id", errors)
        _required(item, ("source_clause",), "source clause", errors)
        guidance_type = _required(item, ("guidance_type",), "guidance type", errors)
        _required(item, ("scope", "tenant"), "tenant scope", errors)
        _required(item, ("scope", "actor"), "actor scope", errors)
        _required(item, ("activation", "trigger"), "activation trigger", errors)
        _required(item, ("consumer",), "intended consumer", errors)
        _required(item, ("verification", "mechanism"), "verification mechanism", errors)
        _required(item, ("provenance", "source"), "source provenance", errors)
        preserved = _required(
            item, ("provenance", "preserve_source_record"),
            "source-record preservation flag", errors)
        if preserved is not True:
            errors.append(f"{guidance_id}: source record must be preserved")
        status = _required(item, ("lifecycle", "status"), "lifecycle status", errors)
        version = _required(item, ("lifecycle", "version"), "lifecycle version", errors)
        if status and status not in LIFECYCLE_STATUSES:
            errors.append(f"{guidance_id}: invalid lifecycle status {status!r}")
        if version is not None and (not isinstance(version, int) or version < 1):
            errors.append(f"{guidance_id}: lifecycle version must be a positive integer")

        if guidance_id in guidance_ids:
            errors.append(f"{guidance_id}: duplicate guidance id")
        elif isinstance(guidance_id, str):
            guidance_ids.add(guidance_id)
        if isinstance(source_id, str):
            source_items[source_id].append(item)

        if guidance_type not in GUIDANCE_TYPES:
            errors.append(f"{guidance_id}: unknown guidance type {guidance_type!r}")
            continue
        projection = _required(item, ("delivery", "projection"), "delivery projection", errors)
        expected = DELIVERY_PROJECTIONS[guidance_type]
        if projection and projection != expected:
            errors.append(
                f"{guidance_id}: delivery projection {projection!r} does not match {expected!r}")

        if guidance_type == "constraint":
            _required(item, ("delivery", "enforcement_control"), "enforcement control", errors)
            _required(item, ("delivery", "evidence"), "enforcement evidence", errors)
            _required(item, ("delivery", "tests"), "enforcement tests", errors)
        elif guidance_type == "procedure":
            _required(item, ("activation", "entry_condition"), "entry condition", errors)
            _required(item, ("verification", "completion_condition"), "completion condition", errors)
        elif guidance_type == "doctrine":
            _required(item, ("activation", "situation_mappings"), "situation mapping", errors)
        elif guidance_type == "rubric":
            _required(item, ("verification", "acceptance_criteria"), "acceptance criteria", errors)
            _required(item, ("verification", "verifier"), "verifier", errors)
        elif guidance_type == "preference":
            actor = item.get("scope", {}).get("actor")
            if actor in (None, "", "all"):
                errors.append(f"{guidance_id}: preference requires a scoped actor")

    analyses = data.get("source_analysis", [])
    if not isinstance(analyses, list):
        errors.append("registry: source_analysis must be an array")
        analyses = []
    seen_analysis: set[str] = set()
    for analysis in analyses:
        if not isinstance(analysis, dict):
            errors.append("registry: every source analysis must be an object")
            continue
        source_id = analysis.get("source_id")
        if not _present(source_id):
            errors.append("source analysis: missing source id")
            continue
        if source_id in seen_analysis:
            errors.append(f"{source_id}: duplicate source analysis")
        seen_analysis.add(source_id)
        split_group = analysis.get("split_group_id")
        if analysis.get("mixed_type_detected") is True or _present(split_group):
            clause_types = analysis.get("clause_types")
            linked = source_items.get(str(source_id), [])
            linked_types = {i.get("guidance_type") for i in linked}
            linked_clauses = {i.get("source_clause") for i in linked}
            mixed_types_invalid = (
                analysis.get("mixed_type_detected") is True
                and (not isinstance(clause_types, list) or len(set(clause_types)) < 2
                     or len(linked_types) < 2))
            if (not isinstance(clause_types, list) or not clause_types
                    or len(linked) < 2 or len(linked_clauses) < 2
                    or not _present(split_group) or mixed_types_invalid
                    or any(i.get("split_group_id") != split_group for i in linked)):
                errors.append(
                    f"{source_id}: compound source requires explicit split records "
                    "with distinct clauses, declared types, and one split group")

    return errors


def coverage_errors(data: dict[str, Any], active_source_ids: Iterable[str]) -> list[str]:
    """Prove exact coverage without pretending a split is a duplicate primary."""
    active = set(active_source_ids)
    by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in data.get("items", []):
        if isinstance(item, dict) and isinstance(item.get("source_id"), str):
            by_source[item["source_id"]].append(item)

    errors: list[str] = []
    missing = sorted(active - set(by_source))
    extra = sorted(set(by_source) - active)
    if missing:
        errors.append("missing active source(s): " + ", ".join(missing))
    if extra:
        errors.append("inactive/unknown source(s): " + ", ".join(extra))

    split_sources = {
        a.get("source_id")
        for a in data.get("source_analysis", [])
        if isinstance(a, dict) and _present(a.get("split_group_id"))
    }
    for source_id, items in sorted(by_source.items()):
        if source_id not in active or len(items) < 2:
            continue
        if source_id not in split_sources or any(not i.get("split_group_id") for i in items):
            errors.append(f"{source_id}: multiple unsplit primary records")
    return errors


def projection(data: dict[str, Any], guidance_type: str) -> list[dict[str, Any]]:
    """Return the stable delivery projection for one validated guidance type."""
    if guidance_type not in GUIDANCE_TYPES:
        raise ValueError(f"unknown guidance type: {guidance_type}")
    errors = validate_registry(data)
    if errors:
        raise ValueError("invalid guidance registry: " + "; ".join(errors))
    return sorted(
        (item for item in data["items"] if item["guidance_type"] == guidance_type),
        key=lambda item: (item["source_id"], item["guidance_id"]),
    )


def type_counts(data: dict[str, Any]) -> dict[str, int]:
    counts = Counter(item.get("guidance_type") for item in data.get("items", []))
    return {kind: counts.get(kind, 0) for kind in GUIDANCE_TYPES}


def _actor_by_source(source_map: dict[str, Any]) -> dict[str, str]:
    active = source_map.get("active_rule_ids", {})
    actors: dict[str, str] = {}
    for source_id in active.get("shared", []):
        actors[str(source_id)] = "all"
    for source_id in active.get("joe", []):
        actors[str(source_id)] = "joe"
    for source_id in active.get("dell", []):
        actors[str(source_id)] = "dell"
    return actors


def _base_item(source_id: str, guidance_id: str, kind: str, actor: str,
               trigger: str, consumer: str, verification: str,
               lifecycle_status: str) -> dict[str, Any]:
    return {
        "guidance_id": guidance_id,
        "source_id": source_id,
        "source_clause": "whole",
        "guidance_type": kind,
        "scope": {"tenant": "carr", "actor": actor},
        "activation": {"trigger": trigger},
        "consumer": consumer,
        "verification": {"mechanism": verification},
        "provenance": {
            "source": "rule",
            "preserve_source_record": True,
            "classification_author": "typed-guidance-build",
            "classification_basis": "rule enforcement map plus reviewed migration manifest",
        },
        "lifecycle": {"status": lifecycle_status, "version": 1},
        "delivery": {"projection": DELIVERY_PROJECTIONS[kind]},
    }


def _manifest_item(source_id: str, actor: str, entry: dict[str, Any],
                   part: dict[str, Any] | None = None, index: int = 0) -> tuple[dict[str, Any], list[str]]:
    row = entry if part is None else {**entry, **part}
    kind = row.get("proposed_type") or row.get("guidance_type") or row.get("type")
    suffix = "whole" if part is None else f"split-{index}"
    guidance_id = f"rule-{source_id}-{suffix}-v1"
    errors: list[str] = []
    if kind not in GUIDANCE_TYPES:
        return {}, [f"{source_id}: manifest has invalid guidance type {kind!r}"]
    trigger = row.get("activation_trigger") or "when the classified guidance is relevant"
    consumer = row.get("consumer") or "typed_guidance_consumer"
    verification = row.get("verification") or "reviewed delivery receipt"
    scoped_actor = row.get("actor") or actor
    if kind == "preference" and scoped_actor == "all":
        scoped_actor = "carr"
    item = _base_item(
        source_id, guidance_id, kind, scoped_actor, trigger, consumer,
        verification, "proposed")
    item["source_clause"] = row.get("source_clause") or row.get("clause") or "whole"
    # ``destination`` in the review manifest is a human-readable routing home;
    # the executable projection is deliberately derived from the primary type
    # so no manifest author can route one type through another type's loader.
    item["delivery"]["projection"] = DELIVERY_PROJECTIONS[kind]

    if kind == "constraint":
        control = row.get("enforcement_control")
        evidence = row.get("enforcement_evidence")
        tests = row.get("enforcement_tests")
        if not (_present(control) and _present(evidence) and _present(tests)):
            errors.append(
                f"{source_id}: proposed constraint lacks installed enforcement evidence/tests")
        else:
            item["delivery"].update({
                "enforcement_control": control, "evidence": evidence, "tests": tests})
    elif kind == "procedure":
        item["activation"]["entry_condition"] = row.get("entry_condition") or trigger
        item["verification"]["completion_condition"] = (
            row.get("completion_condition") or verification)
    elif kind == "doctrine":
        # A stable proposal key is enough at build time.  The WR-AI-006
        # curation path still has to approve and bind it to an active retrieval
        # concept before it can affect results.
        mappings = row.get("situation_mappings") or [f"guidance-{source_id}"]
        item["activation"]["situation_mappings"] = mappings
        item["delivery"]["situation_concepts"] = mappings
    elif kind == "rubric":
        item["verification"]["verifier"] = row.get("verifier") or "independent"
        item["verification"]["acceptance_criteria"] = (
            row.get("acceptance_criteria") or [verification])
    return item, errors


def build_registry(source_map: dict[str, Any], migration_manifest: dict[str, Any]
                   ) -> tuple[dict[str, Any], list[str]]:
    """Compile the current enforcement map and reviewed judgment manifest.

    Built gates become active constraint projections, session rails become
    active procedures, and judgment classifications remain proposed until the
    human review/activation named in the handoff.  An ``unbuilt`` row is a hard
    compile error: calling it an active constraint would overstate enforcement.
    """
    errors: list[str] = []
    controls = source_map.get("rule_controls", {})
    catalog = source_map.get("control_catalog", {})
    actors = _actor_by_source(source_map)
    active = set(actors)
    if set(controls) != active:
        missing = sorted(active - set(controls))
        extra = sorted(set(controls) - active)
        if missing:
            errors.append("source map lacks control rows: " + ", ".join(missing))
        if extra:
            errors.append("source map has inactive control rows: " + ", ".join(extra))

    ambient = {
        source_id for source_id, control in controls.items()
        if control.get("enforcement_class") == "judgment_ambient"
    }
    entries = migration_manifest.get("entries", [])
    manifest_by_id = {
        row.get("source_id"): row for row in entries if isinstance(row, dict)
    }
    if set(manifest_by_id) != ambient or len(manifest_by_id) != len(entries):
        missing = sorted(ambient - set(manifest_by_id))
        extra = sorted(set(manifest_by_id) - ambient)
        errors.append(
            "judgment manifest coverage mismatch"
            + (f"; missing={','.join(missing)}" if missing else "")
            + (f"; extra={','.join(str(x) for x in extra)}" if extra else "")
            + ("; duplicate source ids" if len(manifest_by_id) != len(entries) else ""))

    items: list[dict[str, Any]] = []
    analyses: list[dict[str, Any]] = []
    for source_id, control in controls.items():
        actor = actors.get(source_id, "all")
        klass = control.get("enforcement_class")
        if klass == "judgment_ambient":
            entry = manifest_by_id.get(source_id)
            if not entry:
                continue
            primary, item_errors = _manifest_item(source_id, actor, entry)
            errors.extend(item_errors)
            if primary:
                items.append(primary)
            splits = entry.get("split_records") or []
            split_group = f"rule-{source_id}-split-v1"
            clause_types = [entry.get("proposed_type")]
            if splits:
                primary["split_group_id"] = split_group
                if primary.get("source_clause") == "whole":
                    primary["source_clause"] = entry.get("primary_clause") or "judgment-clause"
                for index, split in enumerate(splits, start=1):
                    child, child_errors = _manifest_item(
                        source_id, actor, entry, split, index)
                    errors.extend(child_errors)
                    if child:
                        child["split_group_id"] = split_group
                        items.append(child)
                        clause_types.append(child["guidance_type"])
            unique_clause_types = list(dict.fromkeys(clause_types))
            analyses.append({
                "source_id": source_id,
                "mixed_type_detected": len(unique_clause_types) > 1,
                "clause_types": unique_clause_types,
                **({"split_group_id": split_group} if splits else {}),
                "rationale": entry.get("rationale") or "reviewed typed classification",
            })
            continue
        if klass == "unbuilt":
            errors.append(
                f"{source_id}: unbuilt control cannot enter the active Guidance projection")
            continue
        if klass == "surfacing":
            kind = "procedure"
            trigger = control.get("binding_moment") or "at task startup"
            item = _base_item(
                source_id, f"rule-{source_id}-whole-v1", kind, actor, trigger,
                "workflow_startup", "session rail delivery receipt", "active")
            item["activation"]["entry_condition"] = trigger
            item["verification"]["completion_condition"] = "configured session rail ran"
        elif klass in {"deny_gate", "schema", "stop_gate"}:
            kind = "constraint"
            trigger = control.get("binding_moment") or "at the governed boundary"
            item = _base_item(
                source_id, f"rule-{source_id}-whole-v1", kind, actor, trigger,
                "control_plane", control.get("failure_mode") or "governed refusal", "active")
        else:
            errors.append(f"{source_id}: unsupported enforcement class {klass!r}")
            continue

        control_name = control.get("control")
        evidence = catalog.get(control_name, {}) if control_name else {}
        if kind == "constraint":
            if not control_name or not evidence.get("implementation") or not evidence.get("test"):
                errors.append(f"{source_id}: built constraint lacks catalog evidence/tests")
            else:
                item["delivery"].update({
                    "enforcement_control": control_name,
                    "evidence": evidence["implementation"],
                    "tests": evidence["test"],
                })
        items.append(item)

    result = {
        "registry": "carr-guidance-registry",
        "schema_version": "1.0.0",
        "items": items,
        "source_analysis": analyses,
    }
    return result, errors


MANIFEST_FIELDS = (
    "source_id", "plain_name", "old_classification", "proposed_type",
    "rationale", "destination", "activation_trigger", "consumer",
    "verification", "split_records_json",
)

MANIFEST_PLACEHOLDERS = {
    "rationale": {"Contextual guidance from current rule text."},
    "destination": {"typed-guidance manifest"},
    "activation_trigger": {"when this rule situation occurs"},
    "verification": {"review guidance use and supporting evidence"},
}


def load_migration_manifest(path: str) -> tuple[dict[str, Any], list[str]]:
    """Read and validate the reviewable judgment-classification TSV."""
    errors: list[str] = []
    entries: list[dict[str, Any]] = []
    try:
        with open(path, encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh, delimiter="\t")
            if tuple(reader.fieldnames or ()) != MANIFEST_FIELDS:
                return {"entries": []}, [
                    "manifest columns must be exactly: " + ", ".join(MANIFEST_FIELDS)]
            for line_number, row in enumerate(reader, start=2):
                source_id = (row.get("source_id") or "").strip()
                prefix = source_id or f"line {line_number}"
                if len(source_id) != 8 or any(c not in "0123456789abcdef" for c in source_id):
                    errors.append(f"{prefix}: source_id must be eight lowercase hex characters")
                for field in MANIFEST_FIELDS[:-1]:
                    if not (row.get(field) or "").strip():
                        errors.append(f"{prefix}: empty {field}")
                if row.get("plain_name", "").strip() == source_id:
                    errors.append(f"{prefix}: plain_name cannot be the source id")
                if row.get("old_classification") != "judgment_ambient":
                    errors.append(f"{prefix}: old classification must be judgment_ambient")
                kind = row.get("proposed_type")
                if kind not in GUIDANCE_TYPES:
                    errors.append(f"{prefix}: invalid proposed type {kind!r}")
                if kind == "constraint":
                    errors.append(
                        f"{prefix}: ambient guidance cannot become a constraint without installed evidence")
                for field, banned in MANIFEST_PLACEHOLDERS.items():
                    if (row.get(field) or "").strip() in banned:
                        errors.append(f"{prefix}: placeholder {field} is not reviewable")
                rationale = (row.get("rationale") or "").strip().lower()
                trigger = (row.get("activation_trigger") or "").strip().lower()
                verification = (row.get("verification") or "").strip().lower()
                if (rationale.startswith("guidance for ")
                        or (trigger.startswith("when ") and trigger.endswith(" is relevant"))
                        or verification == "review application against the rule's stated outcome"):
                    errors.append(f"{prefix}: generated boilerplate is not a reviewable classification")
                try:
                    splits = json.loads(row.get("split_records_json") or "")
                except json.JSONDecodeError as exc:
                    errors.append(f"{prefix}: split_records_json is invalid: {exc.msg}")
                    splits = []
                if not isinstance(splits, list):
                    errors.append(f"{prefix}: split_records_json must be an array")
                    splits = []
                normalized_splits: list[dict[str, Any]] = []
                for split_index, split in enumerate(splits, start=1):
                    if not isinstance(split, dict):
                        errors.append(f"{prefix}: split {split_index} must be an object")
                        continue
                    required = (
                        "clause", "type", "destination", "activation_trigger",
                        "consumer", "verification",
                    )
                    missing = [key for key in required if not _present(split.get(key))]
                    if missing:
                        errors.append(
                            f"{prefix}: split {split_index} missing {', '.join(missing)}")
                    split_type = split.get("type")
                    if split_type not in GUIDANCE_TYPES:
                        errors.append(f"{prefix}: split {split_index} has invalid type {split_type!r}")
                    if split_type == "constraint":
                        errors.append(
                            f"{prefix}: split {split_index} cannot claim a constraint without installed evidence")
                    if (split.get("clause") ==
                            "separable mechanical clause; enforcement-backlog candidate"):
                        errors.append(
                            f"{prefix}: split {split_index} uses generated boilerplate instead of an actual clause")
                    normalized_splits.append(split)
                entries.append({
                    "source_id": source_id,
                    "name": row.get("plain_name", "").strip(),
                    "old_classification": row.get("old_classification", "").strip(),
                    "proposed_type": kind,
                    "rationale": row.get("rationale", "").strip(),
                    "destination": row.get("destination", "").strip(),
                    "activation_trigger": row.get("activation_trigger", "").strip(),
                    "consumer": row.get("consumer", "").strip(),
                    "verification": row.get("verification", "").strip(),
                    "split_records": normalized_splits,
                })
    except OSError as exc:
        return {"entries": []}, [f"cannot read migration manifest: {exc}"]
    return {
        "manifest": "carr-guidance-migration",
        "schema_version": "1.0.0",
        "source_classification": "judgment_ambient",
        "entries": entries,
    }, errors
