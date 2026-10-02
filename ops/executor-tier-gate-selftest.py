#!/usr/bin/env python3
"""Selftest for hooks/executor-tier-gate.py, including Jev's tier pick (loop 615).

Runs the real hook as a subprocess with a stubbed Jev answer, so it is offline
and deterministic. Cases: a named model is never denied, only advised, and a
confident cheaper pick on it is advice; a low-confidence or equal pick on a
named model is silent; a fork stays exempt.

ACTING (Joe, 2026-09-24, decision 5ec806a4): with no model named, a confident
Jev pick (>= ACT_AT) is filled in as the call's model and the spawn is allowed,
said in the context line. The abstention path -- unavailable or under ACT_AT --
keeps the deterministic deny this gate always gave: an abstaining judge never
loosens it.
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
    # Real Claude Code shape (verified against a live ~/.claude/projects/*.jsonl
    # session): attachment.type == "hook_additional_context", content a list of
    # already-decoded JSON text — no "stdout"/hookSpecificOutput wrapper.
    record = {"attachment": {"type": "hook_additional_context",
                             "content": [json.dumps(receipt)]}}
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
check("no model, confident Jev pick: allowed with the pick filled in",
      r and r.get("permissionDecision") == "allow" and r.get("updatedInput", {}).get("model") == "sonnet", r)
check("the rest of the call is passed through unchanged",
      all(r["updatedInput"].get(k) == v for k, v in brief.items()), r)
check("the context line says Jev named the executor",
      "EXECUTOR NAMED BY JEV" in r.get("additionalContext", "") and "`sonnet` at 0.89" in r["additionalContext"], r)

r = run({**brief}, "haiku:0.40")
check("no model, a low-confidence pick: still refused", r and r.get("permissionDecision") == "deny", r)
check("the refusal carries the pick as a hint", "below the acting threshold" in r["permissionDecisionReason"], r)

r = run({**brief}, "none")
check("no model, an unavailable judge: still refused, with no Jev line",
      r and r.get("permissionDecision") == "deny" and "JEV'S PICK" not in r["permissionDecisionReason"], r)
check("the refusal still names the fix", "EXECUTOR NOT NAMED" in r["permissionDecisionReason"], r)

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

# The routing policy (ops/jev_model_route.py dispatch) already chose this tier: a pinned or routed spawn states it
# in its executor line, and this hook must not give the opposite advice on the same launch (Orchestrator session,
# 2026-09-26: "Jev puts 0.82-0.84 on sonnet" on spawns the merge_review and gate_authority_code pins set to opus).
pinned = {**brief, "model": "opus", "prompt": "executor: opus per routing pin merge_review\nReview PR 1 adversarially."}
r = run(pinned, "sonnet:0.84")
check("a spawn carrying a known routing pin gets no cheaper-tier advice", r is None, r)

routed = {**brief, "model": "opus", "prompt": "executor: opus per routing dispatch\nChange the parser."}
r = run(routed, "sonnet:0.84")
check("a spawn the routing dispatch chose gets no cheaper-tier advice", r is None, r)

made_up = {**brief, "model": "opus", "prompt": "executor: opus per routing pin because_i_said_so\nSweep grants."}
r = run(made_up, "sonnet:0.84")
check("an unknown pin name does not silence the advice",
      r and "EXECUTOR ADVICE" in r.get("additionalContext", ""), r)

mismatch = {**brief, "model": "opus", "prompt": "executor: haiku per routing dispatch\nSweep grants."}
r = run(mismatch, "sonnet:0.84")
check("a dispatch line naming a different model than the call does not silence the advice",
      r and "EXECUTOR ADVICE" in r.get("additionalContext", ""), r)

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
