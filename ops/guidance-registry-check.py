#!/usr/bin/env python3
"""Validate the typed Guidance Registry proposal against the live code inventory."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODULE = os.path.join(REPO, "lib", "guidance_registry.py")
MAP = os.path.join(REPO, "ops", "config", "rule-enforcement-map.json")
MANIFEST = os.path.join(REPO, "audits", "guidance-migration-manifest.v1.tsv")
SCHEMA = os.path.join(REPO, "ops", "config", "guidance-registry.schema.v1.json")

spec = importlib.util.spec_from_file_location("guidance_registry", MODULE)
assert spec and spec.loader
registry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(registry)


def fail(errors: list[str]) -> int:
    for error in errors:
        print(f"guidance-registry-check: FAIL — {error}", file=sys.stderr)
    return 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--manifest-only", action="store_true",
        help="check the reviewed judgment manifest without compiling built controls")
    args = parser.parse_args()

    with open(MAP, encoding="utf-8") as fh:
        source_map = json.load(fh)
    with open(SCHEMA, encoding="utf-8") as fh:
        schema = json.load(fh)
    manifest, errors = registry.load_migration_manifest(MANIFEST)

    schema_types = tuple(schema.get("$defs", {}).get("guidanceType", {}).get("enum", []))
    if schema_types != registry.GUIDANCE_TYPES:
        errors.append("JSON schema and runtime guidance-type vocabularies differ")
    # THE EXCLUDED SURFACE IS SUBTRACTED HERE TOO, AND UNTIL 2026-08-23 IT WAS NOT.
    # registry.build_registry() drops rules tagged rule_surface intro_politics —
    # the vendor-introduction doctrine that renders on its own surface and is not
    # part of the registry's corpus. This check computed its own ambient set and
    # forgot to, so it demanded the reviewed migration manifest cover fourteen
    # rules the compiler will never compile. It failed on main's own tree for
    # exactly that reason, naming all fourteen, and the failure sat in front of
    # the Guidance Registry as if the manifest were incomplete. Both halves now
    # ask registry.excluded_source_ids(), which is the function whose docstring
    # already says every caller must (rule 0f38532e: one home, and the second
    # copy is a future contradiction).
    ambient = {
        source_id for source_id, control in source_map.get("rule_controls", {}).items()
        if control.get("enforcement_class") == "judgment_ambient"
    } - registry.excluded_source_ids(source_map)
    manifest_ids = [entry.get("source_id") for entry in manifest.get("entries", [])]
    if set(manifest_ids) != ambient or len(manifest_ids) != len(set(manifest_ids)):
        missing = sorted(ambient - set(manifest_ids))
        extra = sorted(set(manifest_ids) - ambient)
        errors.append(
            "manifest must exactly cover current judgment_ambient sources"
            + (f"; missing={','.join(missing)}" if missing else "")
            + (f"; extra={','.join(extra)}" if extra else "")
            + ("; duplicate ids" if len(manifest_ids) != len(set(manifest_ids)) else ""))
    if errors:
        return fail(errors)
    if args.manifest_only:
        proposal_counts = registry.type_counts({
            "items": [
                {"guidance_type": entry["proposed_type"]}
                for entry in manifest["entries"]
            ]
        })
        print(
            f"guidance-registry-check: OK — {len(manifest_ids)} reviewed judgment sources; "
            f"types={proposal_counts}")
        return 0

    compiled, compile_errors = registry.build_registry(source_map, manifest)
    errors.extend(compile_errors)
    errors.extend(registry.validate_registry(compiled))
    # Same subtraction, one layer up and for the same reason: the registry's
    # corpus is the active rules MINUS the excluded surface, which is also
    # exactly what production's ops.assert_guidance_registry_coverage() counts —
    # 204 of 218 on 2026-08-23. Without this the coverage proof demanded the
    # compiler produce records for rules it is designed never to compile.
    active = {
        source_id
        for scope_ids in source_map.get("active_rule_ids", {}).values()
        for source_id in scope_ids
    } - registry.excluded_source_ids(source_map)
    errors.extend(registry.coverage_errors(compiled, active))
    if errors:
        return fail(errors)
    split_sources = sum(
        bool(entry.get("split_records")) for entry in manifest.get("entries", []))
    print(
        "guidance-registry-check: OK — "
        f"{len(active)} active sources, {len(compiled['items'])} typed records, "
        f"{split_sources} split sources, types={registry.type_counts(compiled)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
