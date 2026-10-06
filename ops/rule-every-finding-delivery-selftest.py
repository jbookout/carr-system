#!/usr/bin/env python3
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("delivery_eval", ROOT / "evals/rule-delivery/run_eval.py")
assert spec and spec.loader
evaluation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluation)
world = evaluation.World()
cases = json.loads((ROOT / "evals/rule-delivery/fixtures/every-finding.v1.json").read_text())["cases"]
failures = []
for case in cases:
    replay_case = {"id": case["id"], "prompt": "", "tool_calls": [case]}
    ids = evaluation.replay(world, replay_case)[0]["ids"]
    if ("729770dd" in ids) != case["must_deliver"]:
        failures.append(case["id"])
assert not failures, f"delivery mismatch: {failures}"
from lib.rule_delivery_preuse import semantic_delivery
kept, packs = semantic_delivery(ROOT, ["729770dd"])
assert kept == ["729770dd"], (kept, packs)
assert packs == ["engineering-git", "governance-rules"], packs
assert world.hook._route_packs(["729770dd"]) == ["engineering-git", "governance-rules"]
print(f"PASS every-finding delivery: {len(cases)} production-route cases")
