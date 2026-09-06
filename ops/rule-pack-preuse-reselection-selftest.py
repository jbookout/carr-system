#!/usr/bin/env python3
"""Behavioral contract for the shadow-compatible pre-use reselection rail."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import tempfile
import threading
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
contract = load("rule_delivery_preuse_test", REPO / "lib/rule_delivery_preuse.py")
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
                    declared: list[str] | None = None, unknown: list[str] | None = None,
                    agent: str = "joe-local", runtime: str | None = None,
                    sponsor: str = "joe") -> dict:
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
            "sponsoring_human_id": sponsor,
            "agent_principal_id": agent,
            "runtime_principal": agent if runtime is None else runtime,
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
            "command": "sleep 5",
            "description": "wait",
            "run_in_background": background,
        },
    }
    if client == "codex":
        row["turn_id"] = "turn-exact"
        row["permission_mode"] = "default"
    else:
        row["transcript_path"] = "/tmp/claude/session-exact.jsonl"
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

dell_output = rail.process(
    payload(), runner=Runner(selector_result(agent="dell-local", sponsor="dell")))
check("sanctioned Dell local identity receives the same rail",
      receipt(dell_output)["identity"] == {
          "agent_principal_id": "dell-local",
          "runtime_principal": "dell-local",
          "sponsoring_human_id": "dell",
      }, receipt(dell_output).get("identity"))

check("Dell receipt passes the exact receipt validator",
      contract.validate_receipt(receipt(dell_output), repo=REPO))
identity_cases = [
    ("mismatched local sponsor", {
        "agent_principal_id": "joe-local", "runtime_principal": "joe-local",
        "sponsoring_human_id": "dell",
    }),
    ("mismatched runtime and agent", {
        "agent_principal_id": "joe-local", "runtime_principal": "codex",
        "sponsoring_human_id": "joe",
    }),
    ("unknown local identity", {
        "agent_principal_id": "some-local", "runtime_principal": "some-local",
        "sponsoring_human_id": "joe",
    }),
    ("non-string local identity", {
        "agent_principal_id": ["joe-local"], "runtime_principal": "joe-local",
        "sponsoring_human_id": "joe",
    }),
]
for label, identity in identity_cases:
    forged = copy.deepcopy(row)
    forged["identity"] = identity
    forged["receipt_id"] = contract.receipt_id(forged)
    check(f"{label} receipt is rejected even with a recomputed receipt id",
          not contract.validate_receipt(forged, repo=REPO), forged)

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
    ("mismatched local sponsor", Runner(selector_result(sponsor="dell"))),
    ("mismatched runtime and agent", Runner(selector_result(runtime="codex"))),
    ("unknown local identity", Runner(selector_result(
        agent="some-local", sponsor="joe"))),
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
        "internal_chat_message_metadata_passthrough": {"turn_id": "turn-exact"},
    }}


def codex_custom_tool_call(*, name: str = "exec", wrapper: str = "response_item",
                           raw: object | None = None) -> dict:
    tool_input = before["tool_input"] if raw is None else raw
    if wrapper == "event_msg":
        return {"type": "event_msg", "payload": {
            "type": "custom_tool_call", "name": name,
            "arguments": tool_input,
        }}
    return {"type": "response_item", "payload": {
        "type": "custom_tool_call", "id": "ctc-exact", "status": "completed",
        "call_id": "tool-exact", "name": name,
        "input": (json.dumps(tool_input, sort_keys=True, separators=(",", ":"))
                  if not isinstance(tool_input, str) else tool_input),
        "internal_chat_message_metadata_passthrough": {"turn_id": "turn-exact"},
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

TRIGGERS, MEMBERS, _ = drift.load_packs()
for label, records in [
    ("Claude success", claude_records),
    ("Codex function success", codex_records),
    ("Codex response-item custom exec success",
     [codex_custom_tool_call(), codex_context(context(codex_output))]),
]:
    evaluated = drift.evaluate(records, TRIGGERS, MEMBERS)
    check(f"{label} structurally requires and loads scheduled automation",
          evaluated["needed"] == ["scheduled-automation"]
          and evaluated["loaded"] == ["scheduled-automation"]
          and evaluated["missing"] == [], evaluated)

custom_needed = drift.evaluate([codex_custom_tool_call()], TRIGGERS, MEMBERS)
check("Codex custom-tool background call structurally requires scheduled automation",
      custom_needed["needed"] == ["scheduled-automation"]
      and custom_needed["loaded"] == []
      and custom_needed["missing"] == ["scheduled-automation"], custom_needed)

for label, records in [
    ("Claude no receipt", [claude_tool_call()]),
    ("Claude selector failure", [claude_tool_call(), {
        "type": "attachment", "sessionId": "session-exact",
        "attachment": {
            "type": "hook_additional_context", "hookEvent": "PreToolUse",
            "hookName": "PreToolUse:Bash", "toolUseID": "tool-exact",
            "content": [rail.FAILURE_CONTEXT],
        },
    }]),
    ("Codex function no receipt", [codex_tool_call()]),
    ("Codex response-item custom exec no receipt", [codex_custom_tool_call()]),
    ("Codex event custom exec_command cannot claim an uncorrelated receipt",
     [codex_custom_tool_call(name="exec_command", wrapper="event_msg"),
      codex_context(context(codex_output))]),
]:
    evaluated = drift.evaluate(records, TRIGGERS, MEMBERS)
    check(f"{label} stays needed and missing",
          evaluated["needed"] == ["scheduled-automation"]
          and evaluated["loaded"] == []
          and evaluated["missing"] == ["scheduled-automation"], evaluated)

for label, record in [
    ("unknown custom alias", codex_custom_tool_call(name="other")),
    ("unstructured custom wrapper prose", codex_custom_tool_call(
        raw="tools.exec_command({run_in_background: true})")),
]:
    evaluated = drift.evaluate([record], TRIGGERS, MEMBERS)
    check(f"{label} does not create a structured background requirement",
          evaluated["needed"] == [] and evaluated["missing"] == [], evaluated)

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
CLAUDE_MATCHER = "Bash|Write|Edit|MultiEdit|Agent|WebFetch|WebSearch|mcp__.*"
CODEX_MATCHER = r"^(Bash|functions\.exec|Write|Edit|MultiEdit|Agent|WebFetch|WebSearch|mcp__.*)$"
check("Claude wiring is exact and unique, widened for the generalized rail (S9)",
      len(claude_rows) == 1 and claude_rows[0]["matcher"] == CLAUDE_MATCHER)
check("Codex wiring is exact and unique, widened for the generalized rail (S9)",
      len(codex_rows) == 1 and codex_rows[0]["matcher"] == CODEX_MATCHER)
check("new rail participates in the epoch source digest",
      "hooks/rule-pack-preuse-reselection.py" in rail.WINDOW_SOURCE_PATHS)
check("the compiled trigger table participates in the epoch source digest too",
      "ops/config/rule-jit-triggers.v1.json" in rail.WINDOW_SOURCE_PATHS
      and "ops/rule-jit-compile.py" in rail.WINDOW_SOURCE_PATHS)

# Claude-only compaction-scoped dedupe. Codex and disabled Claude retain the
# exact historical output path; active dedupe requires the continuity config
# digest, whose canonical hooks guarantee reset callbacks are installed.
dedupe = load("claude_rule_delivery_dedupe_test",
              REPO / "lib/claude_rule_delivery_dedupe.py")
runtime_dedupe = __import__("lib.claude_rule_delivery_dedupe", fromlist=["*"])
with tempfile.TemporaryDirectory(prefix="rule-dedupe-") as temp_name:
    temp = Path(temp_name)
    old_env = dict(os.environ)
    try:
        os.environ["CARR_CLAUDE_CONTINUITY_MODE_FILE"] = str(temp / "mode.json")
        os.environ["CARR_CLAUDE_RULE_DEDUPE_DIR"] = str(temp / "state")
        os.environ["CARR_CLAUDE_RULE_DEDUPE_AUDIT"] = str(temp / "audit.jsonl")
        (temp / "mode.json").write_text(json.dumps({
            "schema_version": 1, "mode": "checkpoint",
            # Prime the process contract before introducing a transient source
            # read failure. The verified receipt must keep every concurrent
            # caller on the same dedupe path after that successful read.
            "config_digest": runtime_dedupe.expected_config_digest(),
        }))
        parallel_outputs: list[dict | None] = []
        barrier = threading.Barrier(2)
        source_barrier = threading.Barrier(2)
        source_attempt = iter((True, False))
        source_lock = threading.Lock()
        real_contract_load = runtime_dedupe.continuity_config.load
        def unstable_contract_load(repo):
            with source_lock:
                fail = next(source_attempt)
            source_barrier.wait()
            if fail:
                raise OSError("transient canonical contract read failure")
            return real_contract_load(repo)
        def invoke(index):
            candidate = payload()
            candidate["tool_use_id"] = f"parallel-{index}"
            barrier.wait()
            parallel_outputs.append(rail.process(candidate, runner=Runner()))
        runtime_dedupe.continuity_config.load = unstable_contract_load
        try:
            threads = [threading.Thread(target=invoke, args=(index,)) for index in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        finally:
            runtime_dedupe.continuity_config.load = real_contract_load
        check("concurrent identical Claude rule sets inject exactly once",
              sum(output is not None for output in parallel_outputs) == 1,
              parallel_outputs)
        audit_rows = [json.loads(line) for line in (temp / "audit.jsonl").read_text().splitlines()]
        check("dedupe telemetry measures delivered and suppressed bytes",
              sum(row["delivered_bytes"] > 0 for row in audit_rows) == 1
              and sum(row["suppressed_bytes"] > 0 for row in audit_rows) == 1,
              audit_rows)

        check("identical Claude rule set stays suppressed in one generation",
              rail.process(payload(), runner=Runner()) is None)
        check("compaction reset allows the same Claude rule set once more",
              dedupe.reset("session-exact", dedupe.transcript_path_digest(
                  "/tmp/claude/session-exact.jsonl"))
              and rail.process(payload(), runner=Runner()) is not None)

        parent = payload()
        parent["session_id"] = "shared-native-session"
        parent["transcript_path"] = "/tmp/claude/shared-native-session.jsonl"
        subagent = copy.deepcopy(parent)
        subagent["tool_use_id"] = "subagent-tool"
        subagent["transcript_path"] = "/tmp/claude/subagents/agent-leaf.jsonl"
        check("parent leaf receives its first rule set",
              rail.process(parent, runner=Runner()) is not None)
        check("subagent leaf independently receives the same rule set",
              rail.process(subagent, runner=Runner()) is not None)
        check("subagent repeat is suppressed within only that leaf",
              rail.process(subagent, runner=Runner()) is None)
        check("subagent compaction reset does not reset the parent leaf",
              dedupe.reset("shared-native-session", dedupe.transcript_path_digest(
                  subagent["transcript_path"]))
              and rail.process(parent, runner=Runner()) is None
              and rail.process(subagent, runner=Runner()) is not None)

        baseline_receipt = receipt(output)
        # Claim the baseline receipt, then independently vary every provenance
        # component that is required to invalidate a prior dedupe claim.
        session = "provenance-change"
        base_payload = payload()
        base_payload["session_id"] = session
        check("baseline provenance set delivers",
              rail._deduped_context(base_payload, baseline_receipt) is not None)
        for field in ("source_digest", "map_digest"):
            changed = copy.deepcopy(baseline_receipt)
            changed[field] = "f" * 64
            check(f"changed {field} reinjects Claude rules",
                  rail._deduped_context(base_payload, changed) is not None)
        changed_trigger = copy.deepcopy(baseline_receipt)
        changed_trigger["schema"] = rail.GENERALIZED_RECEIPT_SCHEMA
        changed_trigger["trigger_ids"] = ["changed-trigger"]
        check("changed trigger digest reinjects Claude rules",
              rail._deduped_context(base_payload, changed_trigger) is not None)

        codex_candidate = payload(client="codex")
        first_codex = rail.process(codex_candidate, runner=Runner())
        codex_candidate["tool_use_id"] = "codex-repeat"
        second_codex = rail.process(codex_candidate, runner=Runner())
        check("Codex delivery remains byte-present on every matching call",
              first_codex is not None and second_codex is not None)
    finally:
        os.environ.clear()
        os.environ.update(old_env)


# ===========================================================================
# GENERALIZED RAIL (WR-000019 slice S9) — the declarative trigger-table path.
# The tests above are entirely unchanged and still exercise the ORIGINAL,
# single-pack scheduled-automation rail alone; everything below is new.

TRIGGER_TABLE = json.loads((REPO / "ops/config/rule-jit-triggers.v1.json").read_text())
TRIGGER_ROWS = {row["trigger_id"]: row for row in TRIGGER_TABLE["triggers"]}
MAX_PER_TRIGGER = TRIGGER_TABLE["max_rules_per_trigger"]


def gen_payload(*, tool: str, tool_input: dict, client: str = "claude",
               session: str = "g-session", tool_use_id: str = "g-tool") -> dict:
    row = {
        "hook_event_name": "PreToolUse",
        "cwd": str(REPO),
        "session_id": session,
        "tool_name": tool,
        "tool_use_id": tool_use_id,
        "tool_input": tool_input,
    }
    if client == "codex":
        row["turn_id"] = "g-turn"
        row["permission_mode"] = "default"
    else:
        row["transcript_path"] = f"/tmp/claude/{session}.jsonl"
    return row


def gen_selector_result(*, packs: list[str], ids: list[str], mode: str = "shadow",
                        agent: str = "joe-local", sponsor: str = "joe") -> dict:
    return {
        "ok": True,
        "identity": {
            "organization_tenant_id": "carr-internal", "sponsoring_human_id": sponsor,
            "agent_principal_id": agent, "runtime_principal": agent,
            "personal_brain_scope": "joe-personal",
            "personal_scope_source": "verified_grant_sponsor",
            "session_capability_profile": "sponsored_agent", "operational_profile": "full",
            "human_only_authority": False,
        },
        "shared_rules": [
            {"id": short, "statement": f"binding jit rule {short}", "human_quote": "reviewed"}
            for short in ids
        ],
        "personal_rules": [],
        "rule_delivery": {"mode": mode, "declared_packs": packs, "would_omit": ["deadbeef"],
                          "packs_not_found": []},
    }


def find_row(*, kind: str, contains: str):
    for row in TRIGGER_ROWS.values():
        if row["kind"] == kind and contains in row["pattern"]:
            return row
    raise AssertionError(f"no compiled {kind} trigger contains {contains!r}")


council_row = find_row(kind="verb", contains="^Agent$")
gitpush_row = find_row(kind="bash_family", contains="git")
path_row = find_row(kind="path_pattern", contains="hooks/")
governance_fallback_row = find_row(kind="content_regex", contains="doctrine")

# Non-match: an ordinary, keyword-free call matches nothing and injects nothing.
neutral = gen_payload(tool="Read", tool_input={"limit": 5})
check("matched_triggers is empty for a neutral, keyword-free call",
      rail.matched_triggers(neutral) == [])
neutral_runner = Runner()
check("process() returns None (no injection, no selector call) for a non-match",
      rail.process(neutral, runner=neutral_runner) is None and neutral_runner.calls == [])

# Verb match injects: the Agent tool exactly matches the council trigger.
agent_call = gen_payload(tool="Agent", tool_input={"description": "spawn helper", "prompt": "zzz"})
check("matched_triggers finds exactly the council verb trigger for an Agent call",
      [r["trigger_id"] for r in rail.matched_triggers(agent_call)] == [council_row["trigger_id"]])
agent_runner = Runner(gen_selector_result(packs=council_row["packs"], ids=council_row["rule_ids"]))
agent_output = rail.process(agent_call, runner=agent_runner)
agent_row = json.loads(context(agent_output))
check("verb-match Agent call fires exactly one selector call",
      len(agent_runner.calls) == 1)
check("generalized selector call declares the matched trigger's exact packs and rule_ids",
      agent_runner.calls[0][0][0] == [
          str(REPO / "run.sh"), "call", "standing-context",
          json.dumps({"packs": council_row["packs"], "rule_ids": council_row["rule_ids"]},
                     sort_keys=True, separators=(",", ":"))])
check("generalized receipt uses the new schema and passes its own validator",
      agent_row["schema"] == rail.GENERALIZED_RECEIPT_SCHEMA
      and contract.validate_generalized_receipt(agent_row, repo=REPO))
check("generalized receipt binds exactly the matched trigger, packs, and rule_ids",
      agent_row["trigger_ids"] == [council_row["trigger_id"]]
      and agent_row["packs"] == council_row["packs"]
      and agent_row["rule_ids"] == council_row["rule_ids"])
check("over-delivery stays inside the compiler's per-trigger cap",
      len(agent_row["rule_ids"]) <= MAX_PER_TRIGGER)
check("original scheduled-automation receipt fields are absent from the generalized shape",
      "pack" not in agent_row and "triggers_digest" in agent_row)

# path_pattern match: a hooks/ write hits the structural extra trigger.
write_call = gen_payload(tool="Write", tool_input={"file_path": "hooks/preuse.py",
                                                   "content": "print(1)\n"})
check("matched_triggers finds the hooks/ path_pattern trigger for a Write call",
      [r["trigger_id"] for r in rail.matched_triggers(write_call)] == [path_row["trigger_id"]])
write_output = rail.process(
    write_call, runner=Runner(gen_selector_result(packs=path_row["packs"], ids=path_row["rule_ids"])))
write_row = json.loads(context(write_output))
check("path_pattern match delivers exactly the structural extra rule",
      write_row["rule_ids"] == path_row["rule_ids"] and write_row["packs"] == path_row["packs"])
missing_session = gen_payload(tool="Agent", tool_input={"description": "spawn helper", "prompt": "zzz"})
missing_session["session_id"] = ""
missing_session_runner = Runner()
check("generalized rail refuses a call with no session_id even though it structurally matches",
      rail.process(missing_session, runner=missing_session_runner) is None
      and missing_session_runner.calls == [])
missing_tool_use = gen_payload(tool="Agent", tool_input={"description": "spawn helper", "prompt": "zzz"})
del missing_tool_use["tool_use_id"]
missing_tool_use_runner = Runner()
check("generalized rail refuses a call with no tool_use_id even though it structurally matches",
      rail.process(missing_tool_use, runner=missing_tool_use_runner) is None
      and missing_tool_use_runner.calls == [])
non_hooks_write = gen_payload(tool="Write", tool_input={"file_path": "lib/plain.py",
                                                        "content": "print(1)\n"})
check("a Write outside hooks/ does not match the path_pattern trigger",
      path_row["trigger_id"] not in
      [r["trigger_id"] for r in rail.matched_triggers(non_hooks_write)])

# content_regex fallback match: an ordinary Bash comment naming two governance words.
gov_call = gen_payload(tool="Bash",
                      tool_input={"command": "echo checking the retrieval doctrine index"})
check("matched_triggers finds the governance-rules pack fallback trigger by content",
      governance_fallback_row["trigger_id"] in
      [r["trigger_id"] for r in rail.matched_triggers(gov_call)])
gov_call_upper = gen_payload(tool="Bash",
                            tool_input={"command": "echo checking the retrieval DOCTRINE Index"})
check("content_regex matching is case-insensitive",
      governance_fallback_row["trigger_id"] in
      [r["trigger_id"] for r in rail.matched_triggers(gov_call_upper)])

# bash_family plus its own pack's content fallback can co-fire on one call —
# the documented multi-trigger over-delivery shape, still capped per row.
gitpush_call = gen_payload(tool="Bash", tool_input={"command": "git push origin main"})
gitpush_matches = rail.matched_triggers(gitpush_call)
gitpush_ids = {r["trigger_id"] for r in gitpush_matches}
check("a git push Bash command matches its seeded bash_family trigger",
      gitpush_row["trigger_id"] in gitpush_ids)
merged_trigger_ids, merged_packs, merged_rule_ids = contract.merge_trigger_delivery(gitpush_matches)
check("multi-trigger merge unions packs/rule_ids across every matched row",
      merged_rule_ids == sorted({rid for r in gitpush_matches for rid in r["rule_ids"]})
      and merged_packs == sorted({p for r in gitpush_matches for p in r["packs"]}))
check("this git push command matches more than one trigger row (bash_family plus its "
      "pack's own content fallback), the multi-match shape this section is testing",
      len(gitpush_matches) > 1, gitpush_ids)
non_bash_gitpush = gen_payload(tool="SomeOtherTool", tool_input={"command": "git push origin main"})
check("bash_family is gated to Bash/functions.exec — the same command on another "
      "tool name does not fire the bash_family trigger (its pack content fallback still can)",
      gitpush_row["trigger_id"] not in
      [r["trigger_id"] for r in rail.matched_triggers(non_bash_gitpush)])
check("every individual matched row still respects the per-trigger cap",
      all(len(r["rule_ids"]) <= MAX_PER_TRIGGER for r in gitpush_matches))
gitpush_output = rail.process(gitpush_call, runner=Runner(
    gen_selector_result(packs=merged_packs, ids=merged_rule_ids)))
gitpush_row_receipt = json.loads(context(gitpush_output))
check("git push call's receipt reflects the full multi-trigger union",
      gitpush_row_receipt["rule_ids"] == merged_rule_ids
      and gitpush_row_receipt["trigger_ids"] == merged_trigger_ids)

# Original rail still wins outright on its own exact shape, even though a
# background Bash git-push command would ALSO structurally match the new
# bash_family trigger above — mutual exclusion per call, by design.
background_gitpush = gen_payload(tool="Bash", tool_input={
    "command": "git push origin main", "run_in_background": True})
bg_runner = Runner()
bg_output = rail.process(background_gitpush, runner=bg_runner)
bg_row = json.loads(context(bg_output))
check("the original exact background shape still takes the original rail, not the generalized one",
      bg_row["schema"] == rail.RECEIPT_SCHEMA and bg_row.get("pack") == rail.PACK)

# Selector-failure paths are fixed, redacted, and never block, matching the
# original rail's own guarantee for its own failures.
gen_bad_cases = [
    ("nonzero", Runner(returncode=1, stderr="token=SUPER-SECRET")),
    ("exception", Runner(error=RuntimeError("postgres://SUPER-SECRET"))),
    ("wrong mode", Runner(gen_selector_result(
        packs=council_row["packs"], ids=council_row["rule_ids"], mode="mystery"))),
    ("extra declared pack", Runner(gen_selector_result(
        packs=council_row["packs"] + ["engineering-git"], ids=council_row["rule_ids"]))),
    ("missing rule", Runner(gen_selector_result(
        packs=council_row["packs"], ids=council_row["rule_ids"][:-1]))),
    ("mismatched local sponsor", Runner(gen_selector_result(
        packs=council_row["packs"], ids=council_row["rule_ids"], sponsor="dell"))),
]
for label, fake in gen_bad_cases:
    failed = rail.process(agent_call, runner=fake)
    rendered = context(failed)
    check(f"generalized rail: {label} is fixed redacted nonblocking failure",
          rendered == rail.GENERALIZED_FAILURE_CONTEXT
          and "SUPER-SECRET" not in json.dumps(failed)
          and "decision" not in failed and "updatedInput" not in failed, failed)

# Tampering with a generalized receipt's content fails validate_generalized_receipt.
tamper_cases = [
    ("wrong trigger_ids", {"trigger_ids": ["0" * 12]}),
    ("wrong packs", {"packs": ["some-other-pack"]}),
    ("extra rule id", {"rule_ids": agent_row["rule_ids"] + ["deadbeef"]}),
    ("unsorted rule_ids (same set, different order)",
     {"rule_ids": list(reversed(agent_row["rule_ids"]))}
     if len(agent_row["rule_ids"]) > 1 else {"rule_ids": agent_row["rule_ids"]}),
    ("duplicated rule_ids", {"rule_ids": agent_row["rule_ids"] + agent_row["rule_ids"][:1]}),
]
for label, patch in tamper_cases:
    forged = copy.deepcopy(agent_row)
    forged.update(patch)
    forged["receipt_id"] = contract.receipt_id(forged)
    check(f"generalized receipt tamper ({label}) fails validate_generalized_receipt",
          not contract.validate_generalized_receipt(forged, repo=REPO))

# A consistent-but-unsorted reordering (rule_ids AND rules moved together, same
# set, same content) isolates the sortedness invariant from the cross-check
# against the compiled table above, which a same-set reorder would not catch.
if len(agent_row["rule_ids"]) > 1:
    reordered = copy.deepcopy(agent_row)
    reordered["rule_ids"] = list(reversed(agent_row["rule_ids"]))
    reordered["rules"] = list(reversed(agent_row["rules"]))
    reordered["receipt_id"] = contract.receipt_id(reordered)
    check("a same-set, consistently-reordered (unsorted) rule_ids/rules pair still fails "
          "validate_generalized_receipt on the sortedness invariant alone",
          not contract.validate_generalized_receipt(reordered, repo=REPO))

if FAILURES:
    print("rule-pack-preuse-reselection-selftest: FAIL")
    for failure in FAILURES:
        print("  " + failure)
    raise SystemExit(1)
print("rule-pack-preuse-reselection-selftest: all cases passed")
