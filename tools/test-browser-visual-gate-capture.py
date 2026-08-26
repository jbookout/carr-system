#!/usr/bin/env python3
"""Hermetic tests for the dependency-free real-browser visual gate runner."""
from __future__ import annotations

import argparse
import inspect
import json
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "room-bridge"))

import browser_visual_gate_capture as capture  # noqa: E402
import design_kernel  # noqa: E402
import job_passport_artifact  # noqa: E402


def args(artifact: Path, out: Path, chrome: str | None = None) -> argparse.Namespace:
    return argparse.Namespace(
        artifact=artifact, out=out, kernel=ROOT / "design" / "carr-design-kernel.v1.json",
        intent="intent:job-passport", surface_family="model-room", work_request_id="wr-synthetic-read-only",
        projection_digest="sha256:7ef1df546040c0aa000a437902fdc4d83decaf1960f63001790b11a422b5dfb9",
        adapter_id="adapter:job-passport-html", report_id="report:hermetic-browser-gate", rtl="not_applicable", chrome=chrome,
    )


def test_browser_absence_is_a_typed_block_not_a_fabricated_pass():
    with tempfile.TemporaryDirectory() as directory:
        artifact = Path(directory) / "artifact.html"
        artifact.write_text("<!doctype html><button>test</button>")
        report = capture.capture(args(artifact, Path(directory) / "report.json", chrome="/not/a/browser"))
    kernel = json.loads((ROOT / "design" / "carr-design-kernel.v1.json").read_text())
    assert design_kernel.validate_visual_gate_report(report, kernel) == report
    assert report["evidence"]["runner"] == "browser_unavailable"
    assert report["admission"]["state"] == "not_admitted"
    assert report["admission"]["critical_blockers"]
    assert not any(row["status"] in {"passed", "failed"} for row in report["gate_results"])


def test_measurement_parser_requires_true_browser_observations():
    source = capture.MEASURE + inspect.getsource(capture._capture)
    for token in ("getBoundingClientRect", "document.getAnimations", "Input.dispatchKeyEvent", "unintended_clip_count", "unapproved_direct_color_declarations"):
        assert token in source, token
    assert capture.chrome_binary("/not/a/browser") is None


def test_job_passport_emits_the_semantic_tokens_the_browser_runner_measures():
    fixture = ROOT / "control-room" / "contracts" / "fixtures" / "execution-fabric" / "codex_desktop.observatory-projection.v1.json"
    rendered = job_passport_artifact.render_job_passport_html(json.loads(fixture.read_text()))
    for token in ("--surface-canvas", "--component-card-background", "--component-control-focus-outline", "min-height:var(--component-control-min-size)", "width:min(960px,100%)", "overflow-wrap:anywhere"):
        assert token in rendered, token


if __name__ == "__main__":
    test_browser_absence_is_a_typed_block_not_a_fabricated_pass()
    print("ok  browser absence becomes typed not_verified report")
    test_measurement_parser_requires_true_browser_observations()
    print("ok  browser runner measures rendered DOM concerns")
    test_job_passport_emits_the_semantic_tokens_the_browser_runner_measures()
    print("ok  Job Passport emits measured semantic and narrow-width controls")
