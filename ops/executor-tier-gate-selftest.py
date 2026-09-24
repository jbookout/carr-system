#!/usr/bin/env python3
"""Selftest for hooks/executor-tier-gate.py, including Jev's tier pick (loop 615).

Runs the real hook as a subprocess with a stubbed Jev answer, so it is offline
and deterministic. Cases: the pre-existing refusal and exemptions still hold;
Jev's pick rides on the refusal; a confident cheaper pick on a named model is
advice, never a refusal; a low-confidence or equal pick is silent; an
unavailable judge changes nothing.
"""
import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(REPO, "hooks", "executor-tier-gate.py")
PASS = 0


def run(tool_input, stub):
    env = {**os.environ, "CARR_EXECUTOR_TIER_JEV_STUB": stub}
    payload = json.dumps({"tool_name": "Agent", "tool_input": tool_input})
    out = subprocess.run([sys.executable, HOOK], input=payload, capture_output=True,
                         text=True, env=env, timeout=30).stdout.strip()
    return json.loads(out)["hookSpecificOutput"] if out else None


def check(label, condition, detail=""):
    global PASS
    if not condition:
        print(f"FAIL {label}: {detail}")
        sys.exit(1)
    PASS += 1


brief = {"description": "Find grants", "prompt": "grep db/schema.sql for grants", "subagent_type": "Explore"}

r = run({**brief}, "sonnet:0.89")
check("no model is still refused", r and r.get("permissionDecision") == "deny", r)
check("the refusal carries Jev's pick", "JEV'S PICK for this task: `sonnet` at 0.89." in r["permissionDecisionReason"], r)

r = run({**brief}, "haiku:0.40")
check("a low-confidence pick is labelled a hint", "below the acting threshold" in r["permissionDecisionReason"], r)

r = run({**brief}, "none")
check("an unavailable judge still refuses, without a Jev line",
      r and r.get("permissionDecision") == "deny" and "JEV'S PICK" not in r["permissionDecisionReason"], r)

r = run({**brief, "model": "opus"}, "haiku:0.95")
check("a confident cheaper pick on a named model is advice, not a refusal",
      r and "permissionDecision" not in r and "EXECUTOR ADVICE" in r.get("additionalContext", ""), r)

r = run({**brief, "model": "opus"}, "haiku:0.40")
check("a low-confidence cheaper pick is silent", r is None, r)

r = run({**brief, "model": "haiku"}, "haiku:0.99")
check("an equal pick is silent", r is None, r)

r = run({**brief, "model": "haiku"}, "opus:0.99")
check("a dearer pick never pushes a spawn upward", r is None, r)

r = run({**brief, "subagent_type": "fork"}, "haiku:0.99")
check("forks stay exempt", r is None, r)

# A fixture run with no stub makes no live judgment, so its output is stable.
env = {k: v for k, v in os.environ.items() if k != "CARR_EXECUTOR_TIER_JEV_STUB"}
env["CARR_HOOK_FIXTURE"] = "1"
outs = [subprocess.run([sys.executable, HOOK], input=json.dumps({"tool_name": "Agent", "tool_input": brief}),
                       capture_output=True, text=True, env=env, timeout=30).stdout for _ in range(2)]
check("a fixture run makes no live judgment and is deterministic",
      outs[0] == outs[1] and "JEV'S PICK" not in outs[0], outs)

print(f"executor-tier-gate-selftest: all {PASS} checks passed")
