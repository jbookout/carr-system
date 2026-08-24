#!/usr/bin/env python3
"""Acceptance checks for the rendered CARR Visual Recipe comparison gallery."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "room-bridge"))

import design_kernel  # noqa: E402
import visual_recipe_gallery as gallery  # noqa: E402
import visual_recipe_library as recipes  # noqa: E402


LIBRARY = ROOT / "design" / "carr-visual-recipe-library.v1.json"
KERNEL = ROOT / "design" / "carr-design-kernel.v1.json"
ARTIFACT = ROOT / "design" / "visual-recipe-gallery.html"
REPORT = ROOT / "design" / "visual-recipe-gallery.visual-gate-report.v1.json"


def load(path: Path):
    return json.loads(path.read_text())


def test_committed_gallery_is_exactly_generated_from_carr_contracts():
    expected = gallery.render(load(LIBRARY), load(KERNEL))
    actual = ARTIFACT.read_text()
    assert actual == expected
    assert "third-party" not in actual.lower()
    for recipe in load(LIBRARY)["recipes"]:
        assert f'data-recipe-id="{recipe["recipe_id"]}"' in actual
    assert actual.count("Baldwin County urgent-care search") == len(load(LIBRARY)["recipes"])


def test_real_chrome_receipt_is_bound_to_the_exact_gallery_and_design_kernel():
    kernel = load(KERNEL)
    report = load(REPORT)
    actual_digest = "sha256:" + hashlib.sha256(ARTIFACT.read_bytes()).hexdigest()
    assert report["target"]["projection_digest"] == actual_digest
    checked = design_kernel.validate_visual_gate_report(report, kernel, expected_work_request_id="wr-visual-recipe-gallery-synthetic", expected_projection_digest=actual_digest)
    assert checked["evidence"]["runner"] == "real_browser_measurement"
    assert checked["admission"]["state"] == "eligible_for_controller_review"
    assert not checked["admission"]["critical_blockers"]
    assert checked["aesthetic_critique"]["authority"] == "advisory_never_promotion"


def test_gallery_carries_human_confirmation_and_kernel_tokens():
    actual = ARTIFACT.read_text()
    for token in ("Human choice required", "no automatic winner", "--surface-canvas", "--component-control-focus-outline", "min-height:var(--component-control-min-size)", "@media(prefers-reduced-motion:reduce)"):
        assert token in actual, token
    assert recipes.validate_library(load(LIBRARY), load(KERNEL))["selection_policy"]["human_confirmation_required"] is True


if __name__ == "__main__":
    for test in (
        test_committed_gallery_is_exactly_generated_from_carr_contracts,
        test_real_chrome_receipt_is_bound_to_the_exact_gallery_and_design_kernel,
        test_gallery_carries_human_confirmation_and_kernel_tokens,
    ):
        test()
        print("ok", test.__name__)
