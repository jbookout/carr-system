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
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(REPO, "hooks", "executor-tier-gate.py")
PASS = 0


def run(tool_input, stub, transcript_path=None):
    env = {**os.environ, "CARR_EXECUTOR_TIER_JEV_STUB": stub}
    payload = {"tool_name": "Agent", "tool_input": tool_input}
    if transcript_path:
        payload["transcript_path"] = transcript_path
    out = subprocess.run([sys.executable, HOOK], input=json.dumps(payload), capture_output=True,
                         text=True, env=env, timeout=30).stdout.strip()
    return json.loads(out)["hookSpecificOutput"] if out else None


def build_advisory_transcript(facets):
    advisory = {
        "schema": "jev-build-advisory/v1", "partner_request_sha256": "0" * 64,
        "model": "jev-1.13.0",
        "facets": {f: 0.9 for f in facets} or {"architecture_or_design": 0.9},
        "guidance": {},
        "required_actions": [{"facet": f, "instruction": f"do {f}"} for f in facets],
        "usage": {}, "authority": "required", "deterministic_exclusions": [],
    }
    receipt = {
        "schema": "jev-build-turn-receipt/v1", "client": "claude", "session_id": "s1",
        "turn_id": None, "prompt_sha256": "0" * 64, "adviser_digest": "sha256:" + "0" * 64,
        "configuration_digest": "sha256:" + "0" * 64, "source_digest": "sha256:" + "0" * 64,
        "semantic_rule_delivery": "delivered", "advisory": advisory,
    }
    record = {"attachment": {"type": "hook_success", "hookName": "UserPromptSubmit",
                             "hookEvent": "UserPromptSubmit",
                             "stdout": json.dumps({"hookSpecificOutput": {
                                 "hookEventName": "UserPromptSubmit",
                                 "additionalContext": json.dumps(receipt)}})}}
    fh = tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False)
    fh.write(json.dumps({"type": "user", "message": {"role": "user", "content": "design it"}}) + "\n")
    fh.write(json.dumps(record) + "\n")
    fh.close()
    return fh.name


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

# ── decision 0b11c89b: required actions must reach the subagent prompt ─────
path = build_advisory_transcript(["architecture_or_design"])
try:
    r = run({**brief, "model": "haiku"}, "haiku:0.99", transcript_path=path)
    check("KNOWN-BAD: a required facet missing from the prompt is denied",
          r and r.get("permissionDecision") == "deny"
          and "architecture_or_design" in r["permissionDecisionReason"], r)

    named_brief = {**brief, "prompt": brief["prompt"] + "\narchitecture_or_design: judge the seam with Jev."}
    r = run({**named_brief, "model": "haiku"}, "haiku:0.99", transcript_path=path)
    check("KNOWN-GOOD: naming the required facet in the prompt is not denied",
          not (r and r.get("permissionDecision") == "deny"), r)

    na_brief = {**brief, "prompt": brief["prompt"] +
               "\nJev required actions: not applicable — read-only lookup."}
    r = run({**na_brief, "model": "haiku"}, "haiku:0.99", transcript_path=path)
    check("KNOWN-GOOD: an explicit not-applicable line is not denied",
          not (r and r.get("permissionDecision") == "deny"), r)
finally:
    os.unlink(path)

no_actions_path = build_advisory_transcript([])
try:
    r = run({**brief, "model": "haiku"}, "haiku:0.99", transcript_path=no_actions_path)
    check("an advisory with no required actions is not denied",
          not (r and r.get("permissionDecision") == "deny"), r)
finally:
    os.unlink(no_actions_path)

r = run({**brief, "model": "haiku"}, "haiku:0.99", transcript_path="/nonexistent/path.jsonl")
check("a missing transcript fails open (no required-actions denial)",
      not (r and r.get("permissionDecision") == "deny"), r)

print(f"executor-tier-gate-selftest: all {PASS} checks passed")
