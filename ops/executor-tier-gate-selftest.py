#!/usr/bin/env python3
"""Selftest for hooks/executor-tier-gate.py, including Jev's tier pick (loop 615).

Runs the real hook as a subprocess with a stubbed Jev answer, so it is offline
and deterministic. Cases: a named model is never denied, only advised, and a
confident cheaper pick on it is advice; a low-confidence or equal pick on a
named model is silent; a fork stays exempt.

ACTING (Joe, 2026-09-24, decision 5ec806a4): with no model named, a confident
Jev pick (>= ACT_AT) now DENIES, naming the pick and how to override. The
abstention path -- unavailable or under ACT_AT -- falls back to the same
advisory text this gate always showed, printed as advice rather than a
refusal, never as a silent allow.
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
check("no model, confident Jev pick: now refused", r and r.get("permissionDecision") == "deny", r)
check("the refusal names Jev's pick and the acting threshold",
      "JEV'S PICK for this task: `sonnet` at 0.89, which clears the acting threshold (0.60)."
      in r["permissionDecisionReason"], r)
check("the refusal says how to override",
      'Pass `model="sonnet"` to accept it' in r["permissionDecisionReason"], r)

r = run({**brief}, "haiku:0.40")
check("no model, a low-confidence pick: advises, does not refuse",
      r and "permissionDecision" not in r, r)
check("the advice is labelled a hint", "below the acting threshold" in r.get("additionalContext", ""), r)

r = run({**brief}, "none")
check("no model, an unavailable judge: advises without refusing or a Jev line",
      r and "permissionDecision" not in r and "JEV'S PICK" not in r.get("additionalContext", ""), r)
check("the advice still names the fix",
      "EXECUTOR NOT NAMED" in r.get("additionalContext", ""), r)

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
