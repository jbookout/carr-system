"""CARR-owned visual recipe contract, narrow routing, and human choice receipts.

Recipes are presentation archetypes, not a substitute for the Design Kernel.
They never choose a provider, change canonical state, relax accessibility, or
promote work.  A future surface receives the core Design Kernel slices and no
more than four candidate recipes; a human binds the chosen recipe explicitly.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import design_kernel


class VisualRecipeError(ValueError):
    """Raised when a recipe library or choice cannot be safely consumed."""


TOP = {"schema_version", "library_id", "version", "status", "inherits", "selection_policy", "workflow", "recipes", "provenance"}
RECIPE = {"recipe_id", "label", "fit_statement", "intent_ids", "audiences", "jobs", "layout_grammar", "information_density", "hierarchy", "visualization_choices", "motion_posture", "component_emphasis", "evidence_freshness", "responsive_behavior", "exclusions"}


def digest(value: Any) -> str:
    return design_kernel.canonical_digest(value)


def _exact(value: Any, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        actual = sorted(value) if isinstance(value, dict) else type(value).__name__
        raise VisualRecipeError(f"{label} fields must be exactly {sorted(fields)}, got {actual}")
    return value


def _strings(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or not value or any(not isinstance(item, str) or not item for item in value):
        raise VisualRecipeError(f"{label} must be a non-empty list of strings")
    return value


def validate_library(raw: Any, kernel: Any) -> dict[str, Any]:
    """Fail closed unless a CARR recipe library inherits the active kernel."""
    value = _exact(raw, TOP, "visual recipe library")
    source = design_kernel.validate_design_kernel(kernel)
    if value["schema_version"] != "carr-visual-recipe-library.v1" or value["status"] != "active_contract_not_a_runtime_or_authority":
        raise VisualRecipeError("unsupported recipe library version or authority posture")
    if not isinstance(value["library_id"], str) or not value["library_id"] or not isinstance(value["version"], str) or not value["version"]:
        raise VisualRecipeError("recipe library identity is required")
    inherits = _exact(value["inherits"], {"design_kernel_contract_id", "design_kernel_version", "inheritance_rule"}, "recipe inheritance")
    if inherits != {
        "design_kernel_contract_id": source["contract_id"], "design_kernel_version": source["version"],
        "inheritance_rule": "recipes_may_not_redefine_authority_truth_accessibility_tokens_or_component_states",
    }:
        raise VisualRecipeError("recipes must inherit the exact CARR Design Kernel boundary")
    policy = _exact(value["selection_policy"], {"candidate_count", "human_confirmation_required", "hidden_taste_selection_forbidden", "evaluation_posture"}, "recipe selection policy")
    if policy != {
        "candidate_count": [2, 4], "human_confirmation_required": True, "hidden_taste_selection_forbidden": True,
        "evaluation_posture": "recipe_conformance_is_advisory_critical_design_kernel_gates_remain_unmaskable",
    }:
        raise VisualRecipeError("recipe selection must remain 2–4 human-confirmed candidates with advisory conformance")
    workflow = _exact(value["workflow"], {"steps"}, "recipe workflow")
    expected_steps = ["request", "compare", "confirm", "bind"]
    if not isinstance(workflow["steps"], list) or [row.get("step_id") if isinstance(row, dict) else None for row in workflow["steps"]] != expected_steps:
        raise VisualRecipeError("recipe workflow must make request, compare, confirm, and bind explicit and ordered")
    for step in workflow["steps"]:
        _exact(step, {"step_id", "instruction", "enforcement"}, "recipe workflow step")
        if not isinstance(step["instruction"], str) or not step["instruction"] or not isinstance(step["enforcement"], str) or not step["enforcement"]:
            raise VisualRecipeError("recipe workflow steps require instruction and enforcement")
    known_intents = {row["intent_id"] for row in source["design_intents"]}
    seen: set[str] = set()
    recipes = value["recipes"]
    if not isinstance(recipes, list) or not 8 <= len(recipes) <= 12:
        raise VisualRecipeError("library must contain a small, curated set of 8–12 recipes")
    for row in recipes:
        recipe = _exact(row, RECIPE, "visual recipe")
        recipe_id = recipe["recipe_id"]
        if not isinstance(recipe_id, str) or not recipe_id.startswith("recipe:") or recipe_id in seen:
            raise VisualRecipeError("recipe ids must be unique typed identifiers")
        seen.add(recipe_id)
        if not isinstance(recipe["label"], str) or not recipe["label"] or not isinstance(recipe["fit_statement"], str) or not recipe["fit_statement"]:
            raise VisualRecipeError("recipe labels and fit statements are required")
        intent_ids = _strings(recipe["intent_ids"], "recipe intent_ids")
        if not set(intent_ids).issubset(known_intents):
            raise VisualRecipeError("recipe references an unknown Design Kernel intent")
        for field in ("audiences", "jobs", "layout_grammar", "hierarchy", "visualization_choices", "component_emphasis", "evidence_freshness", "responsive_behavior", "exclusions"):
            _strings(recipe[field], f"recipe {field}")
        if recipe["information_density"] not in {"low", "medium", "high", "adaptive"}:
            raise VisualRecipeError("recipe information density is invalid")
        if recipe["motion_posture"] not in {"still", "restrained", "ambient_optional"}:
            raise VisualRecipeError("recipe motion posture is invalid")
    provenance = _exact(value["provenance"], {"source_class", "external_inspiration", "data_class"}, "recipe provenance")
    if provenance != {
        "source_class": "carr_owned_general_design_patterns",
        "external_inspiration": "general_visual_archetype_pattern_only_no_third_party_files_brand_names_or_licensed_content_copied",
        "data_class": "metadata_only",
    }:
        raise VisualRecipeError("recipe library provenance must remain CARR-owned metadata only")
    return value


def recommend_candidates(raw: Any, kernel: Any, *, intent_id: str, requested_jobs: list[str] | None = None, count: int = 4) -> dict[str, Any]:
    """Return 2–4 explainable candidates, never a covert aesthetic winner."""
    library = validate_library(raw, kernel)
    if count < 2 or count > 4:
        raise VisualRecipeError("candidate count must be between 2 and 4")
    design_context = design_kernel.design_context(kernel, intent_id)
    wanted = {item.strip().lower() for item in requested_jobs or [] if isinstance(item, str) and item.strip()}
    matching = [row for row in library["recipes"] if intent_id in row["intent_ids"]]
    if len(matching) < 2:
        raise VisualRecipeError("intent does not have enough distinct recipes for a human comparison")

    def score(recipe: dict[str, Any]) -> tuple[int, int]:
        words = " ".join(recipe["jobs"] + recipe["audiences"] + recipe["fit_statement"].split()).lower()
        return (sum(word in words for word in wanted), -library["recipes"].index(recipe))

    candidates = sorted(matching, key=score, reverse=True)[:count]
    return {
        "schema_version": "carr-visual-recipe-candidates.v1",
        "library_binding": {"library_id": library["library_id"], "version": library["version"], "content_digest": digest(library)},
        "design_context": design_context,
        "requested_jobs": sorted(wanted),
        "candidates": [{"recipe": recipe, "recipe_digest": digest(recipe), "selection_reason": "matches requested Design Kernel intent" + (" and stated job vocabulary" if wanted else "")} for recipe in candidates],
        "selection_state": "human_confirmation_required",
        "promotion_authority": "none_recipe_conformance_and_aesthetic_preference_are_advisory",
    }


def selection_receipt(candidates: Any, *, selected_recipe_id: str, selected_by: str, rationale: str, selected_at: str | None = None) -> dict[str, Any]:
    """Record a human recipe selection, bound to the exact candidates shown."""
    if not isinstance(candidates, dict) or candidates.get("schema_version") != "carr-visual-recipe-candidates.v1":
        raise VisualRecipeError("selection needs a typed candidate set")
    rows = candidates.get("candidates")
    match = next((row for row in rows or [] if row.get("recipe", {}).get("recipe_id") == selected_recipe_id), None)
    if match is None or not isinstance(selected_by, str) or not selected_by or not isinstance(rationale, str) or not rationale.strip():
        raise VisualRecipeError("selection must name a displayed recipe, human, and rationale")
    when = selected_at or datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    if not isinstance(when, str) or not when.endswith("Z"):
        raise VisualRecipeError("selection timestamp must be an explicit UTC receipt")
    return {
        "schema_version": "carr-visual-recipe-selection.v1",
        "candidate_set_digest": digest(candidates),
        "library_binding": candidates["library_binding"],
        "design_binding": {key: candidates["design_context"][key] for key in ("contract_id", "contract_version", "contract_digest", "intent_id", "evaluation_profile")},
        "selected_recipe_id": selected_recipe_id,
        "selected_recipe_digest": match["recipe_digest"],
        "selected_by": selected_by,
        "rationale": rationale.strip(),
        "selected_at": when,
        "authority": "human_presentation_choice_only_not_promotion_or_truth_authority",
    }


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))
