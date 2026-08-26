#!/usr/bin/env python3
"""Selftest for hooks/rule-pack-drift-gate.py.

The gate exists to keep rule 347a9ca6 true under scoping, so the cases here are
built around that rule's own evidence: a session whose declared work and actual
work differ. Each case is a transcript, not a mock — the gate's real job is
reading one.

Written before the gate was wired into any Stop set (rule e65efc68: the bar is
highest for anything that can refuse other work).
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "rule_pack_drift_gate", REPO / "hooks" / "rule-pack-drift-gate.py")
assert SPEC and SPEC.loader
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)

TRIGGERS, MEMBERS = gate.load_packs()
FAILURES: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    if not ok:
        FAILURES.append(f"{label}{': ' + detail if detail else ''}")


def user(text: str) -> dict:
    return {"type": "user", "message": {"role": "user",
                                        "content": [{"type": "text", "text": text}]}}


def assistant_tool(name: str, payload: dict) -> dict:
    return {"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": "t1", "name": name, "input": payload}]}}


def standing_context_result(mode: str, declared: list[str], omit: list[str]) -> dict:
    # The REAL payload carries the pack index, and the pack index carries every
    # pack's triggers. A gate that scanned tool results would therefore fire
    # every pack the moment a session booted. It is in this fixture on purpose.
    index = [{"pack": name, "title": name, "triggers": pack["triggers"], "rules": 1}
             for name, pack in json.loads(
                 (REPO / "ops" / "config" / "rule-enforcement-map.json").read_text()
             )["rule_packs"].items()]
    body = {"ok": True, "rule_delivery": {"mode": mode, "enforcing": mode == "enforced",
                                          "declared_packs": declared, "would_omit": omit,
                                          "pack_index": index}}
    return {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t1",
         "content": [{"type": "text", "text": json.dumps(body)}]}]}}


def codex_standing_context_result(mode: str, declared: list[str], omit: list[str]) -> dict:
    """The real Codex JSONL wrapper captured from an mcp_tool_call_end event."""
    body = {"ok": True, "rule_delivery": {"mode": mode,
                                          "enforcing": mode == "enforced",
                                          "declared_packs": declared,
                                          "would_omit": omit}}
    return {"type": "event_msg", "payload": {"type": "mcp_tool_call_end",
            "result": {"Ok": {"content": [
                {"type": "text", "text": json.dumps(body)}]}}}}


def run(records):
    return gate.evaluate(records, TRIGGERS, MEMBERS)


# ── the pack catalog itself has to be usable ────────────────────────────────
check("every pack in the reviewed map compiles a trigger", len(TRIGGERS) >= 8,
      f"only {len(TRIGGERS)} packs have triggers")
check("every pack has at least one rule in it",
      all(MEMBERS.get(name) for name in TRIGGERS),
      str(sorted(set(TRIGGERS) - set(MEMBERS))))

# The recorder must be installed on every interactive client that consumes the
# standing-context payload. A parity check alone cannot catch a source contract
# that forgot one client: source and machine would agree on the same omission.
claude_hooks = json.loads((REPO / "ops" / "config" / "hooks.json").read_text())
codex_hooks = json.loads((REPO / "ops" / "config" / "codex-hooks.json").read_text())


def stop_commands(document):
    hooks = document.get("hooks", document)
    return [hook.get("command", "")
            for group in hooks.get("Stop", [])
            for hook in group.get("hooks", [])]


check("Claude Stop set carries the rule-pack drift recorder",
      any("hooks/rule-pack-drift-gate.py" in command
          for command in stop_commands(claude_hooks)))
check("Codex Stop set carries the rule-pack drift recorder",
      any("hooks/rule-pack-drift-gate.py" in command
          for command in stop_commands(codex_hooks)))

# ── the case the gate was built for: declared one thing, did another ────────
drifted = run([
    user("clean up the loop board please"),
    assistant_tool("mcp__carr__standing-context", {"packs": ["governance-rules"]}),
    standing_context_result("enforced", ["governance-rules"], ["4a53ff82", "424ba0cc"]),
    assistant_tool("Bash", {"command": "git commit -m 'loop cleanup' && git push"}),
])
check("git work in a loop-cleanup session names the engineering pack",
      "engineering-git" in drifted["missing"], str(drifted))
check("the boot payload's own pack index does not fire every pack",
      set(drifted["needed"]) <= {"engineering-git", "governance-rules"},
      "fired: " + str(drifted["needed"]))
check("and the rule the scoped boot omitted is named as a MISS",
      "4a53ff82" in drifted["missed_rules"], str(drifted["missed_rules"]))
check("the pack that WAS loaded is not reported missing",
      "governance-rules" not in drifted["missing"])
check("the block text names the pack and the words that fired it",
      "engineering-git" in gate.block_reason(drifted)
      and "347a9ca6" in gate.block_reason(drifted))

# ── shadow mode records and never blocks ────────────────────────────────────
shadow = run([
    user("clean up the loop board please"),
    assistant_tool("mcp__carr__standing-context", {}),
    standing_context_result("shadow", [], ["4a53ff82"]),
    assistant_tool("Bash", {"command": "git commit -m x"}),
])
check("shadow mode still computes the miss", shadow["missed_rules"] == ["4a53ff82"],
      str(shadow))
check("shadow mode is reported as shadow", shadow["mode"] == "shadow")

# ── a session that never called the verb is not blocked on a guess ──────────
unknown = run([user("do a git thing"), assistant_tool("Bash", {"command": "git status"})])
check("no standing-context call means no established mode", unknown["mode"] is None)
check("and the observed pack is still recorded for the shadow week",
      "engineering-git" in unknown["needed"])

# ── the loaded pack satisfies the work ──────────────────────────────────────
clean = run([
    user("take a worktree and land the migration"),
    assistant_tool("mcp__carr__standing-context", {"packs": ["engineering-git"]}),
    standing_context_result("enforced", ["engineering-git"], ["424ba0cc"]),
    assistant_tool("Bash", {"command": "git worktree add ../x && ./run.sh migrate"}),
])
check("declaring the pack the work needs leaves nothing missing",
      clean["missing"] == [], str(clean))
check("and a rule omitted from an UNRELATED pack is not counted as a miss",
      clean["missed_rules"] == [], str(clean["missed_rules"]))

# ── only THIS turn is judged ────────────────────────────────────────────────
older = run([
    user("draft an LOI for the Pensacola deal"),
    assistant_tool("Bash", {"command": "echo loi"}),
    user("now just tell me the time"),
    assistant_tool("Bash", {"command": "date -u"}),
])
check("a previous turn's deal work does not follow the session forever",
      "client-deal" not in older["needed"], str(older["needed"]))

# ── a turn with no pack signal at all writes nothing ────────────────────────
quiet = run([user("what time is it"), assistant_tool("Bash", {"command": "date -u"})])
check("a turn implying no pack and loading none is silent",
      not quiet["needed"] and not quiet["loaded"], str(quiet))

# ── the gate never blocks outside enforced mode, whatever it found ──────────
for mode, should_block in (("shadow", False), ("enforced", True), (None, False)):
    records = [
        user("clean the loop board"),
        assistant_tool("mcp__carr__standing-context", {"packs": ["governance-rules"]}),
    ]
    if mode:
        records.append(standing_context_result(mode, ["governance-rules"], ["4a53ff82"]))
    records.append(assistant_tool("Bash", {"command": "git push"}))
    result = run(records)
    blocks = result["mode"] == "enforced" and bool(result["missing"])
    check(f"mode {mode!r} blocks = {should_block}", blocks == should_block, str(result))

# ── a later bare call does not unload what an earlier one loaded ────────────
reloaded = run([
    user("take a worktree and land the migration"),
    assistant_tool("mcp__carr__standing-context", {"packs": ["engineering-git"]}),
    standing_context_result("enforced", ["engineering-git"], ["424ba0cc"]),
    assistant_tool("Bash", {"command": "git worktree add ../x"}),
    # the ordinary second call: reading one rule's binding text by id
    assistant_tool("mcp__carr__standing-context", {"rule_ids": ["4a53ff82"]}),
    standing_context_result("enforced", [], ["424ba0cc", "4a53ff82"]),
])
check("a bare lookup call does not unload the pack already loaded",
      reloaded["loaded"] == ["engineering-git"], str(reloaded["loaded"]))
check("and so it reports nothing missing", reloaded["missing"] == [], str(reloaded))

# ── Codex's real MCP result wrapper is part of the delivery contract ─────────
codex = run([
    user("take a worktree and land the migration"),
    {"type": "event_msg", "payload": {"type": "mcp_tool_call_begin",
      "server": "carr", "tool": "standing_context",
      "arguments": {"packs": ["engineering-git"]}}},
    codex_standing_context_result("shadow", ["engineering-git"], ["424ba0cc"]),
    {"type": "event_msg", "payload": {"type": "custom_tool_call",
      "name": "exec_command", "arguments": {"cmd": "git worktree add ../x"}}},
])
check("Codex mcp_tool_call_end yields the declared pack",
      codex["loaded"] == ["engineering-git"], str(codex))
check("Codex mcp_tool_call_end yields the scoped omission count",
      codex["would_omit_count"] == 1, str(codex))
check("Codex transcript fixture has no false drift",
      codex["missing"] == [], str(codex))

# ── every new observation can be bound to the exact epoch source/map ───────
source_digest = gate.source_sha256(REPO)
map_digest = gate.file_sha256(REPO / "ops/config/rule-enforcement-map.json")
check("shadow source identity is a sha256", len(source_digest) == 64, source_digest)
check("reviewed map identity is a sha256", len(map_digest) == 64, map_digest)

# ── a trigger that ends in punctuation still matches ────────────────────────
xcom = run([user("pull the metrics from x.com for last week"),
            assistant_tool("Bash", {"command": "grok x search"})])
check("x.com fires the joe-comms pack despite the word boundary",
      "joe-comms" in xcom["needed"], str(xcom["needed"]))

if FAILURES:
    print("rule-pack-drift-gate-selftest: FAIL", file=sys.stderr)
    for line in FAILURES:
        print(f"  {line}", file=sys.stderr)
    raise SystemExit(1)
print("rule-pack-drift-gate-selftest: 26 cases passed")
