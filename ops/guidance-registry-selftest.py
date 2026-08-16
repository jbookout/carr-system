#!/usr/bin/env python3
"""Seeded regressions for the typed Guidance Registry contract.

The registry replaces the dishonest assumption that every taught item is the
same kind of mechanically enforceable rule.  These tests keep classification
and delivery honest before any database migration or live-store write exists.
"""
from __future__ import annotations

import copy
import importlib.util
import json
import os
import sys
import tempfile


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODULE = os.path.join(REPO, "lib", "guidance_registry.py")
if not os.path.exists(MODULE):
    sys.exit("guidance-registry-selftest: missing lib/guidance_registry.py (expected red before implementation)")

spec = importlib.util.spec_from_file_location("guidance_registry", MODULE)
assert spec and spec.loader
registry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(registry)


BASE = {
    "registry": "carr-guidance-registry",
    "schema_version": "1.0.0",
    "items": [
        {
            "guidance_id": "g-constraint",
            "source_id": "aaaaaaaa",
            "source_clause": "whole",
            "guidance_type": "constraint",
            "scope": {"tenant": "carr", "actor": "all"},
            "activation": {"trigger": "before a governed write"},
            "consumer": "control_plane",
            "verification": {"mechanism": "deny fixture"},
            "provenance": {"source": "rule", "preserve_source_record": True},
            "lifecycle": {"status": "proposed", "version": 1},
            "delivery": {
                "projection": "constraint_enforcement",
                "enforcement_control": "fixture-control",
                "evidence": ["hooks/fixture.py"],
                "tests": ["ops/fixture-selftest.py"],
            },
        },
        {
            "guidance_id": "g-procedure",
            "source_id": "bbbbbbbb",
            "source_clause": "whole",
            "guidance_type": "procedure",
            "scope": {"tenant": "carr", "actor": "all"},
            "activation": {"trigger": "a task begins", "entry_condition": "task is in scope"},
            "consumer": "workflow_startup",
            "verification": {"mechanism": "completion receipt", "completion_condition": "receipt exists"},
            "provenance": {"source": "rule", "preserve_source_record": True},
            "lifecycle": {"status": "proposed", "version": 1},
            "delivery": {"projection": "procedure_workflow"},
        },
        {
            "guidance_id": "g-doctrine",
            "source_id": "cccccccc",
            "source_clause": "whole",
            "guidance_type": "doctrine",
            "scope": {"tenant": "carr", "actor": "all"},
            "activation": {"trigger": "a matching situation is detected", "situation_mappings": ["fixture-situation"]},
            "consumer": "situation_retrieval",
            "verification": {"mechanism": "retrieval golden case"},
            "provenance": {"source": "rule", "preserve_source_record": True},
            "lifecycle": {"status": "proposed", "version": 1},
            "delivery": {"projection": "doctrine_retrieval"},
        },
        {
            "guidance_id": "g-rubric",
            "source_id": "dddddddd",
            "source_clause": "whole",
            "guidance_type": "rubric",
            "scope": {"tenant": "carr", "actor": "all"},
            "activation": {"trigger": "an artifact is ready for review"},
            "consumer": "independent_verifier",
            "verification": {"mechanism": "review", "verifier": "independent", "acceptance_criteria": ["criterion"]},
            "provenance": {"source": "rule", "preserve_source_record": True},
            "lifecycle": {"status": "proposed", "version": 1},
            "delivery": {"projection": "verification_rubric"},
        },
        {
            "guidance_id": "g-preference",
            "source_id": "eeeeeeee",
            "source_clause": "whole",
            "guidance_type": "preference",
            "scope": {"tenant": "carr", "actor": "joe"},
            "activation": {"trigger": "Joe is the relevant partner"},
            "consumer": "partner_context",
            "verification": {"mechanism": "relevant-partner selector"},
            "provenance": {"source": "rule", "preserve_source_record": True},
            "lifecycle": {"status": "proposed", "version": 1},
            "delivery": {"projection": "scoped_preference"},
        },
        {
            "guidance_id": "g-precedent",
            "source_id": "ffffffff",
            "source_clause": "whole",
            "guidance_type": "precedent",
            "scope": {"tenant": "carr", "actor": "all"},
            "activation": {"trigger": "a similar decision is under review"},
            "consumer": "decision_history",
            "verification": {"mechanism": "searchable rationale"},
            "provenance": {"source": "rule", "preserve_source_record": True},
            "lifecycle": {"status": "proposed", "version": 1},
            "delivery": {"projection": "precedent_search"},
        },
        {
            "guidance_id": "g-example",
            "source_id": "11111111",
            "source_clause": "whole",
            "guidance_type": "example",
            "scope": {"tenant": "carr", "actor": "all"},
            "activation": {"trigger": "a matching teaching case is useful"},
            "consumer": "contextual_example",
            "verification": {"mechanism": "source-linked case readback"},
            "provenance": {"source": "rule", "preserve_source_record": True},
            "lifecycle": {"status": "proposed", "version": 1},
            "delivery": {"projection": "example_retrieval"},
        },
    ],
    "source_analysis": [],
}


def mutate(path, value):
    data = copy.deepcopy(BASE)
    target = data
    for key in path[:-1]:
        target = target[key]
    if value is None:
        target.pop(path[-1], None)
    else:
        target[path[-1]] = value
    return data


def case(name, data, expected_fragment=None):
    errors = registry.validate_registry(data)
    passed = not errors if expected_fragment is None else any(expected_fragment in error for error in errors)
    print(f"{'PASS' if passed else 'FAIL'}  {name}: {errors or 'valid'}")
    return passed


def main():
    schema_path = os.path.join(REPO, "ops", "config", "guidance-registry.schema.v1.json")
    with open(schema_path, encoding="utf-8") as fh:
        schema = json.load(fh)
    schema_types = schema["$defs"]["guidanceType"]["enum"]
    schema_pass = tuple(schema_types) == registry.GUIDANCE_TYPES
    print(f"{'PASS' if schema_pass else 'FAIL'}  JSON schema and runtime type vocabulary agree")
    cases = [
        schema_pass,
        case("all seven valid types", BASE),
        case("constraint requires enforcement evidence",
             mutate(["items", 0, "delivery", "evidence"], None), "enforcement evidence"),
        case("constraint requires a test fixture",
             mutate(["items", 0, "delivery", "tests"], []), "enforcement tests"),
        case("procedure requires entry condition",
             mutate(["items", 1, "activation", "entry_condition"], None), "entry condition"),
        case("procedure requires completion condition",
             mutate(["items", 1, "verification", "completion_condition"], None), "completion condition"),
        case("doctrine requires situation activation",
             mutate(["items", 2, "activation", "situation_mappings"], []), "situation mapping"),
        case("rubric requires acceptance criteria",
             mutate(["items", 3, "verification", "acceptance_criteria"], []), "acceptance criteria"),
        case("rubric requires an independent verifier",
             mutate(["items", 3, "verification", "verifier"], None), "verifier"),
        case("preference requires a scoped actor",
             mutate(["items", 4, "scope", "actor"], "all"), "scoped actor"),
        case("unknown type is refused",
             mutate(["items", 6, "guidance_type"], "suggestion"), "guidance type"),
        case("wrong delivery projection is refused",
             mutate(["items", 2, "delivery", "projection"], "standing_context"), "delivery projection"),
    ]

    mixed = copy.deepcopy(BASE)
    mixed["source_analysis"] = [{
        "source_id": "cccccccc", "mixed_type_detected": True,
        "clause_types": ["doctrine", "constraint"], "split_group_id": "split-c"}]
    cases.append(case("mixed source cannot masquerade as one record", mixed, "split records"))
    mixed["items"].append({
        **copy.deepcopy(mixed["items"][0]),
        "guidance_id": "g-constraint-split",
        "source_id": "cccccccc",
        "source_clause": "mechanical-clause",
        "split_group_id": "split-c",
    })
    mixed["items"][2]["source_clause"] = "judgment-clause"
    mixed["items"][2]["split_group_id"] = "split-c"
    cases.append(case("explicit mixed-source split is valid", mixed))

    compound = copy.deepcopy(BASE)
    compound_child = copy.deepcopy(compound["items"][1])
    compound_child.update({
        "guidance_id": "g-procedure-second-clause",
        "source_clause": "second-procedure-clause",
        "split_group_id": "split-b",
    })
    compound["items"][1]["source_clause"] = "first-procedure-clause"
    compound["items"][1]["split_group_id"] = "split-b"
    compound["items"].append(compound_child)
    compound["source_analysis"] = [{
        "source_id": "bbbbbbbb", "mixed_type_detected": False,
        "clause_types": ["procedure"], "split_group_id": "split-b"}]
    compound_ok = not registry.validate_registry(compound)
    compound_ok = compound_ok and not registry.coverage_errors(
        compound, {item["source_id"] for item in BASE["items"]})
    print(f"{'PASS' if compound_ok else 'FAIL'}  explicit same-type compound split is valid")
    cases.append(compound_ok)

    active = {item["source_id"] for item in BASE["items"]}
    cases.append(case("exact active coverage", BASE) and not registry.coverage_errors(BASE, active))
    missing = set(active)
    missing.add("22222222")
    coverage = registry.coverage_errors(BASE, missing)
    coverage_pass = any("missing active source" in error for error in coverage)
    print(f"{'PASS' if coverage_pass else 'FAIL'}  missing active source is refused: {coverage}")
    cases.append(coverage_pass)

    duplicate = copy.deepcopy(BASE)
    duplicate["items"].append({**copy.deepcopy(duplicate["items"][0]), "guidance_id": "g-duplicate"})
    duplicate_errors = registry.coverage_errors(duplicate, active)
    duplicate_pass = any("multiple unsplit primary records" in error for error in duplicate_errors)
    print(f"{'PASS' if duplicate_pass else 'FAIL'}  duplicate unsplit source is refused: {duplicate_errors}")
    cases.append(duplicate_pass)

    source_map = {
        "control_catalog": {
            "deny-fixture": {
                "implementation": ["hooks/deny-fixture.py"],
                "test": ["ops/deny-fixture-selftest.py"],
                "failure_mode": "deny",
            },
            "surface-fixture": {
                "implementation": ["hooks/surface-fixture.py"],
                "test": ["ops/surface-fixture-selftest.py"],
                "failure_mode": "surface",
            },
        },
        "rule_controls": {
            "aaaaaaaa": {
                "category": "hard_pre_action", "enforcement_class": "deny_gate",
                "binding_moment": "before governed write", "control": "deny-fixture", "exceptions": "none",
            },
            "bbbbbbbb": {
                "category": "session_task_rail", "enforcement_class": "surfacing",
                "binding_moment": "at task startup", "control": "surface-fixture", "exceptions": "none",
            },
            "cccccccc": {
                "category": "judgment_advisory", "enforcement_class": "judgment_ambient",
                "why_unenforceable": "contextual quality judgment",
            },
        },
        "active_rule_ids": {"shared": ["aaaaaaaa", "bbbbbbbb"], "joe": ["cccccccc"]},
    }
    migration_manifest = {
        "manifest": "carr-guidance-migration",
        "schema_version": "1.0.0",
        "source_classification": "judgment_ambient",
        "entries": [{
            "source_id": "cccccccc", "name": "Fixture quality standard",
            "proposed_type": "rubric", "rationale": "quality is independently reviewable",
            "destination": "verification_rubric", "activation_trigger": "artifact is ready",
            "consumer": "independent_verifier", "verification": "fixture criterion review",
            "acceptance_criteria": ["fixture criterion passes"], "split_records": [],
        }],
    }
    compiled, compile_errors = registry.build_registry(source_map, migration_manifest)
    compile_pass = not compile_errors and not registry.validate_registry(compiled)
    compile_pass = compile_pass and not registry.coverage_errors(
        compiled, {"aaaaaaaa", "bbbbbbbb", "cccccccc"})
    compile_pass = compile_pass and registry.type_counts(compiled) == {
        "constraint": 1, "procedure": 1, "doctrine": 0, "rubric": 1,
        "preference": 0, "precedent": 0, "example": 0,
    }
    print(f"{'PASS' if compile_pass else 'FAIL'}  deterministic map + manifest compilation: {compile_errors}")
    cases.append(compile_pass)

    unbuilt_map = copy.deepcopy(source_map)
    unbuilt_map["rule_controls"]["aaaaaaaa"] = {
        "category": "judgment_advisory", "enforcement_class": "unbuilt",
        "planned_control": "future fixture",
    }
    _, unbuilt_errors = registry.build_registry(unbuilt_map, migration_manifest)
    unbuilt_pass = any("unbuilt control" in error for error in unbuilt_errors)
    print(f"{'PASS' if unbuilt_pass else 'FAIL'}  unbuilt constraint cannot enter active projection: {unbuilt_errors}")
    cases.append(unbuilt_pass)

    missing_manifest = copy.deepcopy(migration_manifest)
    missing_manifest["entries"] = []
    _, manifest_errors = registry.build_registry(source_map, missing_manifest)
    manifest_pass = any("judgment manifest coverage" in error for error in manifest_errors)
    print(f"{'PASS' if manifest_pass else 'FAIL'}  judgment manifest must exactly cover ambient set: {manifest_errors}")
    cases.append(manifest_pass)

    with tempfile.NamedTemporaryFile("w", suffix=".tsv", delete=False) as fixture:
        fixture.write(
            "source_id\tplain_name\told_classification\tproposed_type\trationale\t"
            "destination\tactivation_trigger\tconsumer\tverification\tsplit_records_json\n"
            "cccccccc\tFixture quality\tjudgment_ambient\trubric\tContextual quality standard\t"
            "verification-rubric\tartifact ready\tindependent verifier\tcriterion reviewed\t[]\n")
        fixture_path = fixture.name
    try:
        loaded, load_errors = registry.load_migration_manifest(fixture_path)
    finally:
        os.unlink(fixture_path)
    loader_pass = not load_errors and loaded["entries"][0]["source_id"] == "cccccccc"
    print(f"{'PASS' if loader_pass else 'FAIL'}  TSV migration manifest loader: {load_errors}")
    cases.append(loader_pass)

    with tempfile.NamedTemporaryFile("w", suffix=".tsv", delete=False) as fixture:
        fixture.write(
            "source_id\tplain_name\told_classification\tproposed_type\trationale\t"
            "destination\tactivation_trigger\tconsumer\tverification\tsplit_records_json\n"
            "cccccccc\tFixture quality\tjudgment_ambient\trubric\tGuidance for fixture quality.\t"
            "rubric-guidance\twhen fixture quality is relevant\tsession agent\t"
            "review application against the rule's stated outcome\t"
            "[{\"clause\":\"separable mechanical clause; enforcement-backlog candidate\","
            "\"type\":\"procedure\",\"destination\":\"rubric-guidance\","
            "\"activation_trigger\":\"when fixture quality is relevant\","
            "\"consumer\":\"session agent\",\"verification\":\"review application against the rule's stated outcome\"}]\n")
        fixture_path = fixture.name
    try:
        _, boilerplate_errors = registry.load_migration_manifest(fixture_path)
    finally:
        os.unlink(fixture_path)
    boilerplate_pass = any("generated boilerplate" in error for error in boilerplate_errors)
    print(f"{'PASS' if boilerplate_pass else 'FAIL'}  generated manifest boilerplate is refused: {boilerplate_errors}")
    cases.append(boilerplate_pass)

    if not all(cases):
        return 1
    print(f"guidance-registry-selftest: {len(cases)} seeded cases passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
