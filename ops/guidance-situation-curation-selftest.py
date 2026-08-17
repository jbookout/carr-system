#!/usr/bin/env python3
"""Validate the proposed human review package for Typed Guidance curation."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from lib import guidance_registry as registry  # noqa: E402
from lib.reviewed_artifact import ReviewedArtifactError, assert_head_committed  # noqa: E402

MAP = REPO / "ops/config/rule-enforcement-map.json"
MANIFEST = REPO / "audits/guidance-migration-manifest.v1.tsv"
REVIEW = REPO / "audits/guidance-situation-curation-review.v1.json"
PREFLIGHT = REPO / "ops/guidance-situation-curation-preflight.py"
UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    errors: list[str] = []
    with tempfile.TemporaryDirectory(prefix="reviewed-artifact-") as temp:
        fixture_repo = Path(temp)
        fixture = fixture_repo / "review.json"
        subprocess.run(["git", "init", "-q"], cwd=fixture_repo, check=True)
        fixture.write_text("reviewed\n", encoding="utf-8")
        subprocess.run(["git", "add", "review.json"], cwd=fixture_repo, check=True)
        subprocess.run(
            ["git", "-c", "user.name=CARR selftest",
             "-c", "user.email=selftest@example.invalid",
             "-c", "core.hooksPath=/dev/null", "commit", "-qm", "fixture"],
            cwd=fixture_repo, check=True,
        )
        assert_head_committed(fixture_repo, fixture, "review.json")
        fixture.write_text("changed\n", encoding="utf-8")
        try:
            assert_head_committed(fixture_repo, fixture, "review.json")
        except ReviewedArtifactError:
            pass
        else:
            errors.append("reviewed artifact guard accepted uncommitted bytes")
        subprocess.run(["git", "add", "review.json"], cwd=fixture_repo, check=True)
        try:
            assert_head_committed(fixture_repo, fixture, "review.json")
        except ReviewedArtifactError:
            pass
        else:
            errors.append("reviewed artifact guard accepted staged bytes")
    source_map = json.loads(MAP.read_text(encoding="utf-8"))
    manifest, manifest_errors = registry.load_migration_manifest(str(MANIFEST))
    errors.extend(manifest_errors)
    compiled, compile_errors = registry.build_registry(source_map, manifest)
    errors.extend(compile_errors)
    review = json.loads(REVIEW.read_text(encoding="utf-8"))

    if review.get("schema") != "guidance-situation-curation-review/v1":
        errors.append("review schema mismatch")
    if review.get("review_state") != "proposed":
        errors.append("curation review must remain proposed until a human decision")
    for field, path in (
        ("reviewed_source_manifest", MANIFEST),
        ("reviewed_base_inventory", MAP),
    ):
        provenance = review.get(field, {})
        if provenance.get("sha256") != digest(path):
            errors.append(f"{field} digest drift")

    doctrine_items = {
        item["guidance_id"]: item
        for item in compiled.get("items", [])
        if item.get("guidance_type") == "doctrine"
    }
    rows = review.get("doctrine_guidance")
    if not isinstance(rows, list):
        errors.append("doctrine_guidance must be an array")
        rows = []
    ids = [row.get("guidance_id") for row in rows if isinstance(row, dict)]
    if set(ids) != set(doctrine_items) or len(ids) != len(set(ids)):
        errors.append("curation review must exactly and uniquely cover compiled doctrine guidance")

    concepts: set[str] = set()
    phrases: set[str] = set()
    mapping_count = 0
    for row in rows:
        if not isinstance(row, dict):
            errors.append("doctrine guidance row must be an object")
            continue
        guidance_id = row.get("guidance_id", "<missing>")
        concept_key = row.get("concept_key")
        if not isinstance(concept_key, str) or not re.fullmatch(
            r"[a-z0-9]+(?:-[a-z0-9]+)*", concept_key
        ):
            errors.append(f"{guidance_id}: invalid concept_key")
        elif concept_key in concepts:
            errors.append(f"{guidance_id}: duplicate concept_key")
        else:
            concepts.add(concept_key)
        for field in ("label", "definition", "phrase"):
            if not isinstance(row.get(field), str) or not row[field].strip():
                errors.append(f"{guidance_id}: missing {field}")
        phrase = " ".join(str(row.get("phrase", "")).lower().split())
        if len(phrase.split()) < 2:
            errors.append(f"{guidance_id}: phrase must contain at least two words")
        elif phrase in phrases:
            errors.append(f"{guidance_id}: duplicate phrase")
        else:
            phrases.add(phrase)
        mappings = row.get("mappings")
        if not isinstance(mappings, list) or not mappings:
            errors.append(f"{guidance_id}: mappings must be a non-empty array")
            continue
        mapping_count += len(mappings)
        seen_targets: set[tuple[str, str]] = set()
        for mapping in mappings:
            if not isinstance(mapping, dict):
                errors.append(f"{guidance_id}: mapping must be an object")
                continue
            if mapping.get("concept_key") != concept_key:
                errors.append(f"{guidance_id}: mapping concept_key differs")
            address = mapping.get("section_address")
            section_id = mapping.get("doctrine_section_id")
            if not isinstance(address, str) or address.count("#") != 1:
                errors.append(f"{guidance_id}: invalid section_address")
            if not isinstance(section_id, str) or not UUID_RE.fullmatch(section_id):
                errors.append(f"{guidance_id}: invalid doctrine_section_id")
            target = (str(address), str(section_id))
            if target in seen_targets:
                errors.append(f"{guidance_id}: duplicate mapping target")
            seen_targets.add(target)
            if mapping.get("role") not in {"governs", "supports"}:
                errors.append(f"{guidance_id}: invalid mapping role")
            if mapping.get("weight") != 1:
                errors.append(f"{guidance_id}: initial review weight must be 1")
            if not isinstance(mapping.get("rationale"), str) or not mapping["rationale"].strip():
                errors.append(f"{guidance_id}: mapping rationale is required")

    constitution = review.get("proposed_constitution_guidance_ids")
    if not isinstance(constitution, list) or not 5 <= len(constitution) <= 10:
        errors.append("proposed constitution must select five to ten guidance ids")
        constitution = []
    if len(constitution) != len(set(constitution)):
        errors.append("proposed constitution contains duplicate guidance ids")
    compiled_by_id = {item["guidance_id"]: item for item in compiled.get("items", [])}
    for guidance_id in constitution:
        item = compiled_by_id.get(guidance_id)
        if item is None or not item.get("is_primary"):
            errors.append(f"constitution id is absent or non-primary: {guidance_id}")

    spec = importlib.util.spec_from_file_location("guidance_curation_preflight", PREFLIGHT)
    assert spec and spec.loader
    preflight = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(preflight)
    sections = {
        (mapping["section_address"], mapping["doctrine_section_id"]): mapping
        for row in rows for mapping in row["mappings"]
    }
    concept_rows = {
        row["concept_key"]: {
            "concept_id": f"{index:08x}-0000-4000-8000-{index:012x}",
            "status": "approved",
        }
        for index, row in enumerate(rows, start=1)
    }
    bridges = {
        (concept_rows[row["concept_key"]]["concept_id"], mapping["doctrine_section_id"])
        for row in rows for mapping in row["mappings"]
    }
    runtime_review_errors = preflight.validate_review(review)
    if runtime_review_errors:
        errors.append("runtime review validation failed: " + "; ".join(runtime_review_errors))
    review_digest = digest(REVIEW)
    plan, plan_errors = preflight.compile_plan(
        review, sections, concept_rows, bridges, review_digest=review_digest
    )
    if plan_errors or set(plan.get("doctrine_mappings", {})) != set(doctrine_items):
        errors.append("fully approved curation fixture did not compile an exact mapping plan")
    if plan.get("review_provenance") != {
        "path": "audits/guidance-situation-curation-review.v1.json",
        "sha256": review_digest,
    }:
        errors.append("mapping plan does not bind the exact curation review digest")
    if bridges:
        refused_plan, refused_errors = preflight.compile_plan(
            review, sections, concept_rows, set(list(bridges)[1:]),
            review_digest=review_digest,
        )
        if not refused_errors or set(refused_plan.get("doctrine_mappings", {})) == set(doctrine_items):
            errors.append("missing approved bridge did not refuse exact mapping-plan coverage")
    mutated_review = json.loads(json.dumps(review))
    mutated_review["doctrine_guidance"][0]["guidance_id"] = "rule-unreviewed-whole-v1"
    if not preflight.validate_review(mutated_review):
        errors.append("runtime review validation accepted changed doctrine coverage")

    if errors:
        for error in errors:
            print(f"guidance-situation-curation-selftest: FAIL — {error}", file=sys.stderr)
        return 1
    print(
        "guidance-situation-curation-selftest: OK — "
        f"{len(rows)} doctrine guidance, {len(concepts)} concepts, "
        f"{mapping_count} exact section mappings, {len(constitution)} constitution candidates"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
