#!/usr/bin/env python3
"""Deterministic acceptance checks for the CARR-owned Visual Recipe Library."""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "room-bridge"))

import visual_recipe_library as recipes  # noqa: E402


def load(path: Path):
    return json.loads(path.read_text())


KERNEL = ROOT / "design" / "carr-design-kernel.v1.json"
LIBRARY = ROOT / "design" / "carr-visual-recipe-library.v1.json"


def refuse(callable_, contains: str):
    try:
        callable_()
    except recipes.VisualRecipeError as error:
        assert contains in str(error), error
    else:
        raise AssertionError("expected VisualRecipeError")


def test_carr_owned_curated_archetypes_inherit_not_replace_the_design_kernel():
    library, kernel = load(LIBRARY), load(KERNEL)
    checked = recipes.validate_library(library, kernel)
    assert len(checked["recipes"]) == 10
    assert {row["recipe_id"] for row in checked["recipes"]} >= {"recipe:operational-command-center", "recipe:spatial-market-map", "recipe:evidence-timeline", "recipe:comparison-review"}
    assert checked["inherits"]["inheritance_rule"] == "recipes_may_not_redefine_authority_truth_accessibility_tokens_or_component_states"
    assert [row["step_id"] for row in checked["workflow"]["steps"]] == ["request", "compare", "confirm", "bind"]


def test_recipe_routing_is_narrow_and_never_hides_a_winner():
    package = recipes.recommend_candidates(load(LIBRARY), load(KERNEL), intent_id="intent:workspace", requested_jobs=["compare records", "orient"], count=4)
    assert 2 <= len(package["candidates"]) <= 4
    assert package["selection_state"] == "human_confirmation_required"
    assert package["promotion_authority"] == "none_recipe_conformance_and_aesthetic_preference_are_advisory"
    assert package["design_context"]["intent_id"] == "intent:workspace"
    assert all("recipes" not in row for row in package["design_context"]["context_slices"])
    assert all("intent:workspace" in row["recipe"]["intent_ids"] for row in package["candidates"])
    refuse(lambda: recipes.recommend_candidates(load(LIBRARY), load(KERNEL), intent_id="intent:generated-artifact", count=1), "between 2 and 4")


def test_selection_is_a_human_bound_receipt_not_an_aesthetic_promotion():
    package = recipes.recommend_candidates(load(LIBRARY), load(KERNEL), intent_id="intent:workspace", count=2)
    selected = package["candidates"][0]["recipe"]["recipe_id"]
    receipt = recipes.selection_receipt(package, selected_recipe_id=selected, selected_by="joe", rationale="The field work needs an obvious next action.", selected_at="2026-08-24T18:00:00Z")
    assert receipt["selected_recipe_id"] == selected
    assert receipt["selected_recipe_digest"] == package["candidates"][0]["recipe_digest"]
    assert receipt["authority"] == "human_presentation_choice_only_not_promotion_or_truth_authority"
    refuse(lambda: recipes.selection_receipt(package, selected_recipe_id="recipe:not-shown", selected_by="joe", rationale="because", selected_at="2026-08-24T18:00:00Z"), "displayed recipe")
    refuse(lambda: recipes.selection_receipt(package, selected_recipe_id=selected, selected_by="joe", rationale="", selected_at="2026-08-24T18:00:00Z"), "rationale")


def test_recipes_cannot_smuggle_a_new_design_authority_or_unknown_intent():
    library, kernel = load(LIBRARY), load(KERNEL)
    changed = copy.deepcopy(library)
    changed["inherits"]["design_kernel_version"] = "v999"
    refuse(lambda: recipes.validate_library(changed, kernel), "inherit")
    changed = copy.deepcopy(library)
    changed["recipes"][0]["intent_ids"] = ["intent:made-up"]
    refuse(lambda: recipes.validate_library(changed, kernel), "unknown Design Kernel intent")


if __name__ == "__main__":
    for test in (
        test_carr_owned_curated_archetypes_inherit_not_replace_the_design_kernel,
        test_recipe_routing_is_narrow_and_never_hides_a_winner,
        test_selection_is_a_human_bound_receipt_not_an_aesthetic_promotion,
        test_recipes_cannot_smuggle_a_new_design_authority_or_unknown_intent,
    ):
        test()
        print("ok", test.__name__)
