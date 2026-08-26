#!/usr/bin/env python3
"""Hermetic contract cases for the Program 5 staging-only prefix rehearsal."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "release-manifest.py"
SHA = subprocess.check_output(
    ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()


def call(*args: str, ok: bool = True) -> str:
    result = subprocess.run([sys.executable, str(TOOL), *args],
                            cwd=ROOT, text=True, capture_output=True)
    if result.returncode != 0 and ok:
        raise AssertionError(result.stderr)
    if result.returncode == 0 and not ok:
        raise AssertionError("expected refusal")
    return result.stdout if result.returncode == 0 else result.stderr


with tempfile.TemporaryDirectory() as tmp:
    tmp_path = Path(tmp)
    manifest_path = tmp_path / "full.json"
    manifest_path.write_text(call("build", "--sha", SHA, "--environment", "production"))
    bound_path = tmp_path / "bound.json"
    bound_path.write_text(call("bind-provider", "--manifest", str(manifest_path),
                               "--provider", "cloudflare-workers",
                               "--provider-version-id", "10000000-0000-4000-8000-000000000001"))

    contract_path = tmp_path / "prefix.json"
    contract_path.write_text(call(
        "staging-forward-fix-prefix", "--manifest", str(bound_path),
        "--through", "0315a_program5_bounded_forward_fix_rehearsal.sql",
        "--held-back", "0316_rule_delivery_audit_counts.sql",
        "--held-back", "0317_atomic_rule_delivery_cutover.sql",
        "--candidate-provider-version-id", "10000000-0000-4000-8000-000000000001"))
    contract = json.loads(contract_path.read_text())
    assert contract["purpose"] == "program5-forward-fix-staging-prefix"
    assert contract["environment"] == "staging"
    assert contract["production_deploy_authorized"] is False
    assert contract["source"]["git_sha"] == SHA
    assert contract["target_prefix"]["highest_migration"] == "0315a_program5_bounded_forward_fix_rehearsal.sql"
    assert contract["target_prefix"]["applied_count"] > len(contract["selected_migrations"])
    assert [item["filename"] for item in contract["held_back_migrations"]] == [
        "0316_rule_delivery_audit_counts.sql",
        "0317_atomic_rule_delivery_cutover.sql",
    ]
    assert all(set(item) == {"ordinal", "filename", "sha256"}
               for item in contract["selected_migrations"] + contract["held_back_migrations"])
    assert contract["selected_migrations"][-1]["ordinal"] + 1 == contract["held_back_migrations"][0]["ordinal"]

    # A subset or reordered held-back suffix is an attempted silent carry.
    refusal = call(
        "staging-forward-fix-prefix", "--manifest", str(bound_path),
        "--through", "0315a_program5_bounded_forward_fix_rehearsal.sql",
        "--held-back", "0317_atomic_rule_delivery_cutover.sql",
        "--candidate-provider-version-id", "10000000-0000-4000-8000-000000000001", ok=False)
    assert "held-back" in refusal

    altered = json.loads(contract_path.read_text())
    altered["held_back_migrations"][0]["sha256"] = "0" * 64
    altered_path = tmp_path / "altered.json"
    altered_path.write_text(json.dumps(altered))
    refusal = call("verify-staging-forward-fix-prefix", "--contract", str(altered_path), ok=False)
    assert "digest" in refusal or "contract" in refusal

print("program5 bounded forward-fix selftest: full source and staging prefix stay separately bound")
