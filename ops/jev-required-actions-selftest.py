#!/usr/bin/env python3
"""The retired prompt-facet contract cannot reopen a Stop turn."""
from pathlib import Path
import sys
repo = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(repo))
from lib.rule_delivery_preuse import validate_build_advisory

# Every retired enforcement site must retain the later decision's provenance.
decision = "c136a8e1-c135-4553-9e50-64c9640d12b7"
for path in ("hooks/completion-evidence-gate.py", "hooks/executor-tier-gate.py",
             "lib/rule_delivery_preuse.py", "lib/jev_required_actions.py"):
    assert decision in (repo / path).read_text(), f"missing retirement provenance: {path}"

stop = (repo / "hooks/completion-evidence-gate.py").read_text()
preuse = (repo / "hooks/rule-pack-preuse-reselection.py").read_text()
assert "evaluate_required_actions" not in stop
assert "jev_required_actions_check(" not in stop
assert "jev_required_actions_message(" not in stop
assert "return module.deferred()" in preuse
assert validate_build_advisory({"schema": "jev-build-advisory-skipped/v1",
                                "status": "skipped", "reason": "boundary_deferred",
                                "effect": "no_prompt_obligation"}, prompt_sha256="x")
assert not validate_build_advisory({"schema": "jev-build-advisory/v1",
                                    "partner_request_sha256": "x", "model": "jev-test",
                                    "facets": {}, "usage": {}, "authority": "required",
                                    "required_actions": []}, prompt_sha256="x")
print("jev-required-actions-selftest: prompt facets cannot impose Stop obligations")
