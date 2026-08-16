#!/usr/bin/env python3
"""Dry-run-first operational importer for the reviewed typed Guidance Registry.

This is deliberately not an authority path.  Its default mode compiles the
checked-in enforcement map and reviewed TSV, resolves source-rule UUIDs from
the *ambient* DATABASE_URL read-only, validates an explicit doctrine mapping
plan, and renders the exact canonical activation-manifest preimage.  `--apply`
only stages then applies that reviewed digest through the writer functions; it
cannot decide a batch or activate/deactivate a registry.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping

import psycopg


REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from lib import guidance_registry as registry
DEFAULT_MAP = REPO / "ops" / "config" / "rule-enforcement-map.json"
DEFAULT_MANIFEST = REPO / "audits" / "guidance-migration-manifest.v1.tsv"
MAPPING_PLAN_SCHEMA = "guidance-situation-mapping-plan/v1"


class ImportRefusal(ValueError):
    """A reviewable precondition failed before any writer call."""


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_mapping_plan(path: Path) -> dict[str, list[dict[str, str]]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ImportRefusal(f"mapping plan does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ImportRefusal(f"mapping plan is not JSON: {path}: {exc.msg}") from exc
    if not isinstance(raw, dict) or raw.get("schema") != MAPPING_PLAN_SCHEMA:
        raise ImportRefusal(f"mapping plan must declare schema {MAPPING_PLAN_SCHEMA}")
    bindings = raw.get("doctrine_mappings")
    if not isinstance(bindings, dict):
        raise ImportRefusal("mapping plan requires doctrine_mappings object")
    return bindings


def resolve_active_rules(rows: Iterable[Mapping[str, Any]], expected_source_ids: set[str]) -> dict[str, str]:
    """Resolve all reviewed short IDs against the active source of truth.

    Refuse ambiguity, malformed UUIDs, inactive/missing sources, and active
    extras.  The exact-set comparison prevents an apparently harmless new rule
    from entering a reviewed batch under an old manifest.
    """
    by_short: dict[str, list[str]] = {}
    seen_full: set[str] = set()
    for row in rows:
        full = row.get("id")
        short = row.get("source_id")
        if not isinstance(full, str) or not isinstance(short, str):
            raise ImportRefusal("active rule inventory returned malformed identifier")
        if len(full) != 36 or full[8:9] != "-" or short != full[:8]:
            raise ImportRefusal(f"active rule inventory returned invalid UUID mapping for {short!r}")
        if full in seen_full:
            raise ImportRefusal(f"active rule inventory repeats UUID {full}")
        seen_full.add(full)
        by_short.setdefault(short, []).append(full)
    ambiguous = sorted(short for short, values in by_short.items() if len(values) != 1)
    if ambiguous:
        raise ImportRefusal("active rule short IDs are ambiguous: " + ",".join(ambiguous))
    resolved = {short: values[0] for short, values in by_short.items()}
    actual = set(resolved)
    if actual != expected_source_ids:
        missing = sorted(expected_source_ids - actual)
        extra = sorted(actual - expected_source_ids)
        raise ImportRefusal(
            "active rule inventory does not exactly match reviewed registry"
            + ("; missing=" + ",".join(missing) if missing else "")
            + ("; extra=" + ",".join(extra) if extra else ""))
    return {source_id: resolved[source_id] for source_id in sorted(expected_source_ids)}


def resolve_classifier_actor(rows: Iterable[Mapping[str, Any]], slug: str) -> str:
    ids = [row.get("id") for row in rows]
    if len(ids) != 1 or not isinstance(ids[0], str):
        raise ImportRefusal(f"classifier actor {slug!r} must resolve to exactly one active non-human actor")
    return ids[0]


def stage_and_apply(cur: Any, *, digest: str, canonical_manifest_text: str,
                    classifier_actor_id: str, stage_idempotency_key: str,
                    stage_reason: str, apply_idempotency_key: str,
                    apply_reason: str) -> tuple[Any, Any]:
    """The only writer sequence in this CLI; no decision/activation call exists."""
    batch_id = cur.execute(
        "select ops.stage_guidance_import_batch(%s,%s,%s,%s,%s) as id",
        (digest, canonical_manifest_text, classifier_actor_id,
         stage_idempotency_key, stage_reason),
    ).fetchone()["id"]
    apply_event_id = cur.execute(
        "select ops.apply_guidance_import_batch(%s,%s,%s,%s) as id",
        (batch_id, digest, apply_idempotency_key, apply_reason),
    ).fetchone()["id"]
    return batch_id, apply_event_id


def build_review_artifact(source_map: dict[str, Any], manifest: dict[str, Any],
                          active_rule_rows: Iterable[Mapping[str, Any]],
                          doctrine_mappings: dict[str, list[dict[str, str]]],
                          constitution_guidance_ids: Iterable[str], *,
                          map_digest: str, manifest_digest: str) -> tuple[dict[str, Any], bytes, str]:
    compiled, errors = registry.build_registry(source_map, manifest)
    errors.extend(registry.validate_registry(compiled))
    if errors:
        raise ImportRefusal("compiled registry refused: " + "; ".join(errors))
    source_ids = {item["source_id"] for item in compiled["items"]}
    source_rule_ids = resolve_active_rules(active_rule_rows, source_ids)
    activation_manifest, errors = registry.build_activation_manifest(
        compiled,
        constitution_guidance_ids=constitution_guidance_ids,
        source_manifest_provenance={
            "path": "audits/guidance-migration-manifest.v1.tsv",
            "sha256": manifest_digest,
            "manifest": "carr-guidance-migration",
            "schema_version": "1.0.0",
            "source_classification": "judgment_ambient",
            "entry_count": len(manifest.get("entries", [])),
        },
        base_inventory={
            "path": "ops/config/rule-enforcement-map.json",
            "sha256": map_digest,
            "active_source_ids": sorted(source_ids),
            "source_rule_ids": source_rule_ids,
        },
        situation_mapping_bindings=doctrine_mappings,
    )
    if errors:
        raise ImportRefusal("activation manifest refused: " + "; ".join(errors))
    canonical = registry.activation_manifest_bytes(activation_manifest)
    return activation_manifest, canonical, hashlib.sha256(canonical).hexdigest()


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--enforcement-map", type=Path, default=DEFAULT_MAP)
    p.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    p.add_argument("--mapping-plan", type=Path, required=True,
                   help="explicit guidance-situation-mapping-plan/v1 JSON")
    p.add_argument("--constitution-guidance-id", action="append", default=[],
                   help="primary guidance id selected for the 5–10-item constitution; repeat")
    p.add_argument("--apply", action="store_true",
                   help="after rendering the same review artifact, stage then apply it through writer functions")
    p.add_argument("--stage-idempotency-key")
    p.add_argument("--stage-reason")
    p.add_argument("--classifier-actor-slug", default="codex",
                   help="active non-human actor credited by the writer function when staging (default: codex)")
    p.add_argument("--apply-idempotency-key")
    p.add_argument("--apply-reason")
    p.add_argument("--output-manifest", type=Path,
                   help="write the exact canonical UTF-8 bytes reviewed by this run")
    return p


def require_apply_args(args: argparse.Namespace) -> None:
    if not args.apply:
        return
    missing = [name for name in ("stage_idempotency_key", "stage_reason",
                                 "apply_idempotency_key", "apply_reason")
               if not getattr(args, name)]
    if missing:
        raise ImportRefusal("--apply requires explicit " + ", ".join("--" + name.replace("_", "-") for name in missing))
    if args.stage_idempotency_key == args.apply_idempotency_key:
        raise ImportRefusal("stage and apply require distinct idempotency keys")


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        require_apply_args(args)
        if not args.constitution_guidance_id:
            raise ImportRefusal("at least one --constitution-guidance-id is required")
        dsn = os.environ.get("DATABASE_URL")
        if not dsn:
            raise ImportRefusal("DATABASE_URL is required; this CLI never accepts a DSN argument")
        source_map = json.loads(args.enforcement_map.read_text(encoding="utf-8"))
        manifest, errors = registry.load_migration_manifest(str(args.manifest))
        if errors:
            raise ImportRefusal("reviewed migration manifest refused: " + "; ".join(errors))
        mappings = load_mapping_plan(args.mapping_plan)
        with psycopg.connect(dsn) as conn:
            with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
                preview, preview_errors = registry.build_registry(source_map, manifest)
                if preview_errors:
                    raise ImportRefusal("compiled registry refused: " + "; ".join(preview_errors))
                active_rows = cur.execute(
                    "select id::text as id, left(id::text,8) as source_id from rule "
                    "where status='active' order by id").fetchall()
                artifact, canonical, digest = build_review_artifact(
                    source_map, manifest, active_rows, mappings, args.constitution_guidance_id,
                    map_digest=file_sha256(args.enforcement_map), manifest_digest=file_sha256(args.manifest))
                review = {
                    "ok": True, "mode": "apply" if args.apply else "dry_run",
                    "manifest_digest": digest, "canonicalization": artifact["canonicalization"],
                    "canonical_manifest_utf8": canonical.decode("utf-8"),
                    "entry_count": len(artifact["entries"]),
                    "constitution_guidance_ids": artifact["constitution_guidance_ids"],
                }
                if args.output_manifest:
                    # A reviewed digest artifact must never silently overwrite
                    # another run's preimage.  Pick a new path deliberately.
                    with args.output_manifest.open("xb") as fh:
                        fh.write(canonical)
                    review["output_manifest"] = str(args.output_manifest)
                if args.apply:
                    actor_rows = cur.execute(
                        "select id::text as id from actor where slug=%s "
                        "and kind in ('automation','system') and active",
                        (args.classifier_actor_slug,)).fetchall()
                    classifier_actor_id = resolve_classifier_actor(actor_rows, args.classifier_actor_slug)
                    batch_id, apply_event_id = stage_and_apply(
                        cur, digest=digest, canonical_manifest_text=canonical.decode("utf-8"),
                        classifier_actor_id=classifier_actor_id,
                        stage_idempotency_key=args.stage_idempotency_key, stage_reason=args.stage_reason,
                        apply_idempotency_key=args.apply_idempotency_key, apply_reason=args.apply_reason)
                    conn.commit()
                    review.update({"batch_id": str(batch_id), "apply_event_id": str(apply_event_id)})
                print(json.dumps(review, sort_keys=True, indent=2))
                return 0
    except (ImportRefusal, OSError, json.JSONDecodeError, psycopg.Error) as exc:
        print(f"guidance-registry-import: REFUSED — {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
