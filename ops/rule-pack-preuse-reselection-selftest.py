#!/usr/bin/env python3
"""Behavioral contract for the shadow-compatible pre-use reselection rail."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace


REPO = Path(__file__).resolve().parent.parent


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rail = load("rule_pack_preuse_reselection", REPO / "hooks/rule-pack-preuse-reselection.py")
drift = load("rule_pack_drift_preuse_test", REPO / "hooks/rule-pack-drift-gate.py")


FAILURES: list[str] = []


def check(name: str, condition: bool, detail: object = "") -> None:
    if condition:
        print(f"PASS  {name}")
    else:
        FAILURES.append(f"{name}: {detail}")
        print(f"FAIL  {name}: {detail}")


MAP = json.loads((REPO / "ops/config/rule-enforcement-map.json").read_text())
EXPECTED_IDS = sorted(
    short for short, row in MAP["rule_load_layers"].items()
    if "scheduled-automation" in row.get("packs", [])
)
MAP_DIGEST = hashlib.sha256(
    (REPO / "ops/config/rule-enforcement-map.json").read_bytes()).hexdigest()
SOURCE_DIGEST = rail.source_sha256(REPO)


def selector_result(*, mode: str = "shadow", ids: list[str] | None = None,
                    declared: list[str] | None = None, unknown: list[str] | None = None) -> dict:
    wanted = EXPECTED_IDS if ids is None else ids
    block = {
        "mode": mode,
        "declared_packs": ["scheduled-automation"] if declared is None else declared,
        "would_omit": ["deadbeef"],
    }
    if unknown is not None:
        block["packs_not_found"] = unknown
    return {
        "ok": True,
        "identity": {
            "organization_tenant_id": "carr-internal",
            "sponsoring_human_id": "joe",
            "agent_principal_id": "joe-local",
            "runtime_principal": "joe-local",
            "personal_brain_scope": "joe-personal",
            "personal_scope_source": "verified_grant_sponsor",
            "session_capability_profile": "sponsored_agent",
            "operational_profile": "full",
            "human_only_authority": False,
        },
        "shared_rules": [
            {"id": short, "statement": f"binding scheduled rule {short}",
             "human_quote": "reviewed"} for short in wanted
        ],
        "personal_rules": [],
        "rule_delivery": block,
    }


class Runner:
    def __init__(self, result: dict | None = None, *, returncode: int = 0,
                 stderr: str = "", error: Exception | None = None):
        self.result = selector_result() if result is None else result
        self.returncode = returncode
        self.stderr = stderr
        self.error = error
        self.calls: list[tuple[tuple, dict]] = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self.error:
            raise self.error
        return SimpleNamespace(
            returncode=self.returncode,
            stdout=json.dumps(self.result),
            stderr=self.stderr,
        )


def payload(*, tool: str = "Bash", background: object = True,
            client: str = "claude") -> dict:
    row = {
        "hook_event_name": "PreToolUse",
        "cwd": str(REPO),
        "session_id": "session-exact",
        "tool_name": tool,
        "tool_use_id": "tool-exact",
        "tool_input": {
            "command": "until test -f /tmp/ready; do sleep 5; done",
            "description": "wait for source-owned readiness",
            "run_in_background": background,
        },
    }
    if client == "codex":
        row["turn_id"] = "turn-exact"
        row["permission_mode"] = "default"
    return row


def context(output: dict | None) -> str:
    if not output:
        return ""
    return output.get("hookSpecificOutput", {}).get("additionalContext", "")


def receipt(output: dict) -> dict:
    return json.loads(context(output))


# The source map, not a typed count or list, owns membership.
check("scheduled ids derive from the reviewed map",
      rail.scheduled_rule_ids() == EXPECTED_IDS and bool(EXPECTED_IDS),
      rail.scheduled_rule_ids())

# The exact observed event is selected before the unchanged action proceeds.
runner = Runner()
original = payload()
before = copy.deepcopy(original)
output = rail.process(original, runner=runner)
row = receipt(output)
check("exact top-level boolean triggers one selector call", len(runner.calls) == 1)
check("hook leaves the tool payload byte-for-byte equivalent", original == before, original)
expected_args = json.dumps({
    "packs": ["scheduled-automation"], "rule_ids": EXPECTED_IDS,
}, sort_keys=True, separators=(",", ":"))
check("selector uses the sanctioned existing door with exact dynamic ids",
      runner.calls[0][0][0] == [str(REPO / "run.sh"), "call", "standing-context", expected_args],
      runner.calls[0] if runner.calls else "no call")
specific = output.get("hookSpecificOutput", {})
check("cross-client output is context-only and never enforcing",
      specific.get("hookEventName") == "PreToolUse"
      and set(specific) == {"hookEventName", "additionalContext"}
      and not any(key in output for key in ("decision", "reason", "updatedInput")), output)
check("receipt binds exact map/source/tool provenance",
      row["schema"] == rail.RECEIPT_SCHEMA
      and row["map_digest"] == MAP_DIGEST
      and row["source_digest"] == SOURCE_DIGEST
      and row["tool_input_sha256"] == rail.digest(before["tool_input"])
      and row["session_id"] == "session-exact"
      and row["tool_use_id"] == "tool-exact", row)
check("receipt carries every dynamic member and full binding text",
      row["rule_ids"] == EXPECTED_IDS
      and [item["id"] for item in row["rules"]] == EXPECTED_IDS
      and all(item["statement"].startswith("binding scheduled rule") for item in row["rules"]),
      row.get("rules"))

# Exact booleans and exact tool names only. Text and nested values never fire it.
for label, candidate in [
    ("false", False), ("string true", "true"), ("integer one", 1),
    ("missing", None),
]:
    probe = payload(background=candidate)
    if candidate is None:
        del probe["tool_input"]["run_in_background"]
    fake = Runner()
    check(f"{label} does not trigger", rail.process(probe, runner=fake) is None
          and fake.calls == [])
nested = payload(background=False)
nested["tool_input"]["metadata"] = {"run_in_background": True}
check("nested boolean does not trigger", rail.process(nested, runner=Runner()) is None)
prose = payload(background=False)
prose["tool_input"]["command"] += " # run_in_background=true"
check("command prose does not trigger", rail.process(prose, runner=Runner()) is None)
check("unrelated tool does not trigger",
      rail.process(payload(tool="Read"), runner=Runner()) is None)
check("Codex structured exec receives the same rail",
      receipt(rail.process(payload(tool="functions.exec", client="codex"), runner=Runner()))["client"]
      == "codex")

# Selector responses are strict; failures are fixed/redacted and never block.
bad_cases = [
    ("nonzero", Runner(returncode=1, stderr="token=SUPER-SECRET")),
    ("exception", Runner(error=RuntimeError("postgres://SUPER-SECRET"))),
    ("unknown pack", Runner(selector_result(unknown=["scheduled-automation"]))),
    ("wrong mode", Runner(selector_result(mode="mystery"))),
    ("extra declared pack", Runner(selector_result(
        declared=["scheduled-automation", "engineering-git"]))),
    ("missing rule", Runner(selector_result(ids=EXPECTED_IDS[:-1]))),
]
for label, fake in bad_cases:
    failed = rail.process(payload(), runner=fake)
    rendered = context(failed)
    check(f"{label} is fixed redacted nonblocking failure",
          rendered == rail.FAILURE_CONTEXT
          and "SUPER-SECRET" not in json.dumps(failed)
          and "decision" not in failed and "updatedInput" not in failed, failed)

# Stop telemetry credits only a platform-proven receipt bound to the exact tool call.
def claude_tool_call() -> dict:
    return {"type": "assistant", "message": {"role": "assistant", "content": [{
        "type": "tool_use", "id": "tool-exact", "name": "Bash",
        "input": before["tool_input"],
    }]}, "sessionId": "session-exact"}


def claude_attachment(text: str) -> dict:
    return {
        "type": "attachment", "sessionId": "session-exact",
        "attachment": {
            "type": "hook_additional_context", "hookEvent": "PreToolUse",
            "hookName": "PreToolUse:Bash", "toolUseID": "tool-exact",
            "content": [text],
        },
    }


def codex_tool_call(tool: str = "functions.exec") -> dict:
    return {"type": "response_item", "payload": {
        "type": "function_call", "call_id": "tool-exact", "name": tool,
        "arguments": json.dumps(before["tool_input"], sort_keys=True,
                                separators=(",", ":")),
    }}


def codex_context(text: str) -> dict:
    return {"type": "response_item", "payload": {
        "type": "message", "role": "developer",
        "content": [{"type": "input_text", "text": text}],
        "internal_chat_message_metadata_passthrough": {"turn_id": "turn-exact"},
    }}


claude_records = [claude_tool_call(), claude_attachment(context(output))]
mode, loaded, _ = drift.delivery_state(claude_records)
check("Claude exact hook envelope credits scheduled automation",
      mode == "shadow" and loaded == ["scheduled-automation"], (mode, loaded))

codex_output = rail.process(payload(tool="functions.exec", client="codex"), runner=Runner())
codex_records = [codex_tool_call(), codex_context(context(codex_output))]
mode, loaded, _ = drift.delivery_state(codex_records)
check("Codex exact developer context credits scheduled automation",
      mode == "shadow" and loaded == ["scheduled-automation"], (mode, loaded))
codex_bash_output = rail.process(payload(tool="Bash", client="codex"), runner=Runner())
mode, loaded, _ = drift.delivery_state([
    codex_tool_call("Bash"), codex_context(context(codex_bash_output)),
])
check("Codex Bash alias receives and proves the same receipt",
      mode == "shadow" and loaded == ["scheduled-automation"], (mode, loaded))

for label, records in [
    ("copied user context", [claude_tool_call(), {
        "type": "user", "message": {"role": "user", "content": context(output)}}]),
    ("wrong Claude hook name", [claude_tool_call(), {
        **claude_attachment(context(output)),
        "attachment": {**claude_attachment(context(output))["attachment"],
                       "hookName": "PreToolUse:Read"}}]),
    ("wrong tool id", [claude_tool_call(), {
        **claude_attachment(context(output)),
        "attachment": {**claude_attachment(context(output))["attachment"],
                       "toolUseID": "tool-other"}}]),
    ("tampered receipt", [claude_tool_call(), claude_attachment(
        context(output).replace("scheduled-automation", "engineering-git", 1))]),
    ("Codex user role", [codex_tool_call(), {
        **codex_context(context(codex_output)),
        "payload": {**codex_context(context(codex_output))["payload"], "role": "user"}}]),
]:
    found = drift.delivery_state(records)
    check(f"{label} does not count as loaded", found[1] == [], found)

malformed = receipt(output)
malformed["unexpected"] = True
found = drift.delivery_state([
    claude_tool_call(),
    claude_attachment(json.dumps(malformed, sort_keys=True, separators=(",", ":"))),
])
check("extra-key additionalContext does not count as loaded",
      found[1] == [], found)
for record_type, message_role in (("user", "user"), ("assistant", "user"),
                                  ("user", "assistant")):
    forged = claude_tool_call()
    forged["type"] = record_type
    forged["message"]["role"] = message_role
    found = drift.delivery_state([forged, claude_attachment(context(output))])
    check(f"Claude {record_type}/{message_role} tool-call provenance refuses",
          found[1] == [], found)

# Config parity and shadow-window source identity travel with the rail.
claude = json.loads((REPO / "ops/config/hooks.json").read_text())
codex = json.loads((REPO / "ops/config/codex-hooks.json").read_text())["hooks"]
command = "hooks/rule-pack-preuse-reselection.py"
claude_rows = [group for group in claude["PreToolUse"]
               if any(command in hook.get("command", "") for hook in group.get("hooks", []))]
codex_rows = [group for group in codex["PreToolUse"]
              if any(command in hook.get("command", "") for hook in group.get("hooks", []))]
check("Claude wiring is exact and unique",
      len(claude_rows) == 1 and claude_rows[0]["matcher"] == "Bash")
check("Codex wiring is exact and unique",
      len(codex_rows) == 1 and codex_rows[0]["matcher"] == r"^(Bash|functions\.exec)$")
check("new rail participates in the epoch source digest",
      "hooks/rule-pack-preuse-reselection.py" in rail.WINDOW_SOURCE_PATHS)

if FAILURES:
    print("rule-pack-preuse-reselection-selftest: FAIL")
    for failure in FAILURES:
        print("  " + failure)
    raise SystemExit(1)
print("rule-pack-preuse-reselection-selftest: all cases passed")
