#!/usr/bin/env python3
"""Read-only production preflight and exact mapping-plan compiler.

The reviewed curation package names stable concept keys plus exact active
doctrine section addresses and UUIDs. This tool verifies those targets against
the ambient read-only database. After the separately governed retrieval
proposals are human-approved, it resolves their production concept UUIDs and
refuses to compile a guidance-situation-mapping-plan/v1 unless every exact
concept-to-section bridge is approved.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row


REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from lib import guidance_registry as registry  # noqa: E402
from lib.reviewed_artifact import (  # noqa: E402
    ReviewedArtifactError,
    assert_head_committed,
)

DEFAULT_REVIEW = REPO / "audits/guidance-situation-curation-review.v1.json"
SOURCE_MANIFEST = REPO / "audits/guidance-migration-manifest.v1.tsv"
BASE_INVENTORY = REPO / "ops/config/rule-enforcement-map.json"
MAPPING_PLAN_SCHEMA = "guidance-situation-mapping-plan/v1"
REVIEW_SCHEMA = "guidance-situation-curation-review/v1"
UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


class PreflightRefusal(ValueError):
    """The live curation state does not match the reviewed package."""


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_review(review: dict[str, Any]) -> list[str]:
    """Bind runtime compilation to the complete checked-in v1 review package."""
    errors: list[str] = []
    if review.get("schema") != REVIEW_SCHEMA:
        errors.append(f"review schema must be {REVIEW_SCHEMA}")
    if review.get("review_state") != "proposed":
        errors.append("review_state must remain proposed; authority approval lives in receipts")
    for field, path, expected_path in (
        ("reviewed_source_manifest", SOURCE_MANIFEST,
         "audits/guidance-migration-manifest.v1.tsv"),
        ("reviewed_base_inventory", BASE_INVENTORY,
         "ops/config/rule-enforcement-map.json"),
    ):
        provenance = review.get(field)
        if not isinstance(provenance, dict):
            errors.append(f"{field} must be an object")
            continue
        if provenance.get("path") != expected_path:
            errors.append(f"{field} path must be {expected_path}")
        if provenance.get("sha256") != file_sha256(path):
            errors.append(f"{field} sha256 does not match the checked-in input")

    source_map = json.loads(BASE_INVENTORY.read_text(encoding="utf-8"))
    manifest, manifest_errors = registry.load_migration_manifest(str(SOURCE_MANIFEST))
    errors.extend(manifest_errors)
    compiled, compile_errors = registry.build_registry(source_map, manifest)
    errors.extend(compile_errors)
    base_provenance = review.get("reviewed_base_inventory")
    if not isinstance(base_provenance, dict):
        base_provenance = {}
    compiled_source_count = len({
        item.get("source_id") for item in compiled.get("items", [])
        if isinstance(item, dict)
    })
    if (base_provenance.get("standing_source_count") != compiled_source_count
            or base_provenance.get("excluded_rule_surface") != "intro_politics"):
        errors.append("reviewed base inventory corpus metadata does not match compilation")
    doctrine_ids = {
        item["guidance_id"] for item in compiled.get("items", [])
        if item.get("guidance_type") == "doctrine"
    }
    rows = review.get("doctrine_guidance")
    if not isinstance(rows, list):
        return errors + ["doctrine_guidance must be an array"]
    ids = [row.get("guidance_id") for row in rows if isinstance(row, dict)]
    if len(ids) != len(rows) or len(ids) != len(set(ids)) or set(ids) != doctrine_ids:
        errors.append("review must exactly and uniquely cover compiled doctrine guidance")
    concept_keys: set[str] = set()
    phrases: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        guidance_id = str(row.get("guidance_id", "<missing>"))
        concept_key = row.get("concept_key")
        if (not isinstance(concept_key, str)
                or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", concept_key)
                or concept_key in concept_keys):
            errors.append(f"{guidance_id}: concept_key must be unique lowercase kebab-case")
        else:
            concept_keys.add(concept_key)
        for field in ("label", "definition", "phrase"):
            if not isinstance(row.get(field), str) or not row[field].strip():
                errors.append(f"{guidance_id}: {field} is required")
        phrase = " ".join(str(row.get("phrase", "")).lower().split())
        if len(phrase.split()) < 2 or phrase in phrases:
            errors.append(f"{guidance_id}: phrase must be unique and contain at least two words")
        else:
            phrases.add(phrase)
        mappings = row.get("mappings")
        if not isinstance(mappings, list) or not mappings:
            errors.append(f"{guidance_id}: mappings must be a non-empty array")
            continue
        seen_targets: set[tuple[str, str]] = set()
        for mapping in mappings:
            if not isinstance(mapping, dict):
                errors.append(f"{guidance_id}: mapping must be an object")
                continue
            address = mapping.get("section_address")
            section_id = mapping.get("doctrine_section_id")
            if mapping.get("concept_key") != concept_key:
                errors.append(f"{guidance_id}: mapping concept_key differs")
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
                errors.append(f"{guidance_id}: mapping weight must be 1")
            if not isinstance(mapping.get("rationale"), str) or not mapping["rationale"].strip():
                errors.append(f"{guidance_id}: mapping rationale is required")
    constitution = review.get("proposed_constitution_guidance_ids")
    compiled_by_id = {item["guidance_id"]: item for item in compiled.get("items", [])}
    if (not isinstance(constitution, list) or not 5 <= len(constitution) <= 10
            or len(constitution) != len(set(constitution))):
        errors.append("proposed constitution must contain five to ten unique guidance ids")
    else:
        for guidance_id in constitution:
            item = compiled_by_id.get(guidance_id)
            if item is None or not item.get("is_primary"):
                errors.append(f"constitution id is absent or non-primary: {guidance_id}")
    return errors


def compile_plan(
    review: dict[str, Any],
    sections: dict[tuple[str, str], dict[str, Any]],
    concepts: dict[str, dict[str, Any]],
    approved_bridges: set[tuple[str, str]],
    *,
    review_digest: str,
) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    doctrine_mappings: dict[str, list[dict[str, str]]] = {}
    for row in review.get("doctrine_guidance", []):
        guidance_id = row["guidance_id"]
        concept_key = row["concept_key"]
        concept = concepts.get(concept_key)
        if concept is None:
            errors.append(f"{guidance_id}: approved concept is absent: {concept_key}")
            continue
        if concept.get("status") != "approved":
            errors.append(f"{guidance_id}: concept is not approved: {concept_key}")
            continue
        concept_id = str(concept["concept_id"])
        bindings: list[dict[str, str]] = []
        for mapping in row["mappings"]:
            address = mapping["section_address"]
            expected_section_id = mapping["doctrine_section_id"]
            target = sections.get((address, expected_section_id))
            if target is None:
                errors.append(
                    f"{guidance_id}: active doctrine target drifted: "
                    f"{address} expected {expected_section_id}"
                )
                continue
            if (concept_id, expected_section_id) not in approved_bridges:
                errors.append(
                    f"{guidance_id}: approved doctrine bridge is absent: "
                    f"{concept_key} -> {address}"
                )
                continue
            bindings.append(
                {
                    "concept_key": concept_key,
                    "concept_id": concept_id,
                    "doctrine_section_id": expected_section_id,
                    "reason": mapping["rationale"],
                }
            )
        if bindings:
            doctrine_mappings[guidance_id] = bindings
    plan = {
        "schema": MAPPING_PLAN_SCHEMA,
        "review_provenance": {
            "path": "audits/guidance-situation-curation-review.v1.json",
            "sha256": review_digest,
        },
        "doctrine_mappings": doctrine_mappings,
    }
    expected_ids = {
        row["guidance_id"] for row in review.get("doctrine_guidance", [])
    }
    if set(doctrine_mappings) != expected_ids:
        missing = sorted(expected_ids - set(doctrine_mappings))
        if missing:
            errors.append("mapping plan has unresolved doctrine guidance: " + ",".join(missing))
    return plan, errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-mapping-plan", type=Path)
    args = parser.parse_args()
    try:
        dsn = os.environ.get("DATABASE_URL", "").strip()
        if not dsn:
            raise PreflightRefusal("DATABASE_URL is required; no DSN argument is accepted")
        for path, relative in (
            (DEFAULT_REVIEW, "audits/guidance-situation-curation-review.v1.json"),
            (SOURCE_MANIFEST, "audits/guidance-migration-manifest.v1.tsv"),
            (BASE_INVENTORY, "ops/config/rule-enforcement-map.json"),
        ):
            assert_head_committed(REPO, path, relative)
        review = json.loads(DEFAULT_REVIEW.read_text(encoding="utf-8"))
        review_errors = validate_review(review)
        if review_errors:
            raise PreflightRefusal("review package refused: " + "; ".join(review_errors))
        review_digest = file_sha256(DEFAULT_REVIEW)
        expected_concepts = [
            row["concept_key"] for row in review.get("doctrine_guidance", [])
        ]
        expected_section_ids = sorted(
            {
                mapping["doctrine_section_id"]
                for row in review.get("doctrine_guidance", [])
                for mapping in row["mappings"]
            }
        )
        with psycopg.connect(dsn, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute("set transaction read only")
                identity = cur.execute(
                    "select session_user::text,current_user::text,"
                    "current_setting('transaction_read_only')::text as transaction_read_only"
                ).fetchone()
                section_rows = cur.execute(
                    """select s.id::text doctrine_section_id,
                              d.slug||'#'||s.section_key section_address
                         from doctrine_section s
                         join doctrine_document d on d.id=s.document_id
                        where s.status='active' and s.current_revision_id is not null
                          and s.id=any(%s::uuid[])""",
                    (expected_section_ids,),
                ).fetchall()
                concept_rows = cur.execute(
                    """select id::text concept_id,concept_key,status
                         from retrieval_concept where concept_key=any(%s::text[])""",
                    (expected_concepts,),
                ).fetchall()
                concept_ids = [row["concept_id"] for row in concept_rows]
                bridge_rows = []
                if concept_ids:
                    bridge_rows = cur.execute(
                        """select concept_id::text,section_id::text
                             from doctrine_concept_mapping
                            where status='approved'
                              and concept_id=any(%s::uuid[])
                              and section_id=any(%s::uuid[])""",
                        (concept_ids, expected_section_ids),
                    ).fetchall()
                conn.rollback()
        if identity is None or identity.get("transaction_read_only") != "on":
            raise PreflightRefusal("preview connection is not transaction_read_only=on")
        sections = {
            (row["section_address"], row["doctrine_section_id"]): row
            for row in section_rows
        }
        concepts = {row["concept_key"]: row for row in concept_rows}
        approved_bridges = {
            (row["concept_id"], row["section_id"]) for row in bridge_rows
        }
        plan, errors = compile_plan(
            review, sections, concepts, approved_bridges,
            review_digest=review_digest,
        )
        ready = not errors
        if args.output_mapping_plan:
            if not ready:
                raise PreflightRefusal("mapping plan compilation refused: " + "; ".join(errors))
            encoded = (json.dumps(plan, sort_keys=True, indent=2) + "\n").encode("utf-8")
            with args.output_mapping_plan.open("xb") as handle:
                handle.write(encoded)
        report = {
            "ok": ready,
            "mode": "read_only",
            "identity": identity,
            "reviewed_doctrine_guidance": len(review.get("doctrine_guidance", [])),
            "curation_review_sha256": review_digest,
            "exact_active_sections": len(section_rows),
            "approved_concepts": sum(
                row.get("status") == "approved" for row in concept_rows
            ),
            "approved_exact_bridges": len(approved_bridges),
            "mapping_plan_ready": ready,
            "errors": errors,
        }
        if args.output_mapping_plan:
            report["output_mapping_plan"] = str(args.output_mapping_plan)
        print(json.dumps(report, sort_keys=True, indent=2))
        return 0 if ready else 2
    except (PreflightRefusal, ReviewedArtifactError, OSError,
            json.JSONDecodeError, psycopg.Error) as exc:
        print(f"guidance-situation-curation-preflight: REFUSED — {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
