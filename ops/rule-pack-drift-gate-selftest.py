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
import io
import json
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from lib.rule_delivery_shadow import WINDOW_SOURCE_PATHS, source_sha256  # noqa:E402
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


def synthetic_user(text: str, kind: str | None = None) -> dict:
    record = user(text)
    if kind:
        record["origin"] = {"kind": kind}
    return record


def scheduled_user(repo: Path = REPO) -> dict:
    source = (repo / "ops/scheduled-tasks/nightly-record-layer.SKILL.md").read_text()
    body = source.split("---\n", 2)[2].lstrip().replace("{{REPO}}", str(repo))
    preamble = (
        "This is an automated run of a scheduled task. The user is not present "
        "to answer questions. For implementation details, execute autonomously "
        "without asking clarifying questions — make reasonable choices and note "
        "them in your output. \"write\" actions (e.g. MCP tools that send, post, "
        "create, update, or delete), only take them if the task file asks for that "
        "specific action. When in doubt, producing a report of what you found is "
        "the correct output."
    )
    return synthetic_user(
        '<scheduled-task name="nightly-record-layer" '
        'file="/Users/booko/.claude/scheduled-tasks/nightly-record-layer/SKILL.md">\n'
        f"{preamble}\n\n{body}</scheduled-task>"
    )


def assistant_tool(name: str, payload: dict) -> dict:
    return {"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": "t1", "name": name, "input": payload}]}}


def standing_context_result(mode: str, declared: list[str], omit: list[str],
                            packs_not_found: list[str] | None = None) -> dict:
    # The REAL payload carries the pack index, and the pack index carries every
    # pack's triggers. A gate that scanned tool results would therefore fire
    # every pack the moment a session booted. It is in this fixture on purpose.
    index = [{"pack": name, "title": name, "triggers": pack["triggers"], "rules": 1}
             for name, pack in json.loads(
                 (REPO / "ops" / "config" / "rule-enforcement-map.json").read_text()
             )["rule_packs"].items()]
    delivery = {"mode": mode, "enforcing": mode == "enforced",
                "declared_packs": declared, "would_omit": omit,
                "pack_index": index}
    if packs_not_found:
        delivery["packs_not_found"] = packs_not_found
    body = {"ok": True, "rule_delivery": delivery}
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


def codex_tool(name: str, input_text: str) -> dict:
    return {"type": "response_item", "payload": {"type": "custom_tool_call",
            "name": name, "input": input_text}}


def codex_assistant(text: str) -> dict:
    return {"type": "response_item", "payload": {"type": "message",
            "role": "assistant", "content": [{"type": "output_text", "text": text}]}}


def codex_user(text: str) -> dict:
    return {"type": "response_item", "payload": {"type": "message",
            "role": "user", "content": [{"type": "input_text", "text": text}]}}


def engineering_receipt() -> dict:
    digest = "sha256:" + "a" * 64
    evidence = {"ref": "evidence:blocked", "redaction_class": "metadata_only",
                "content_digest": digest}
    return {
        "schema_version": "engineering-slice-receipt.v1",
        "envelope_digest": digest, "attempt_id": "attempt:2",
        "slice_ref": "slice:tour-foundation-contracts", "plan_digest": digest,
        "attribution": {"actor_ref": "agent:codex", "session_ref": "session:receipt",
                        "adapter_ref": "adapter:codex-desktop"},
        "planned_resource_refs": ["repository:carr-system"],
        "actual_resource_refs": ["repository:carr-system"],
        "planned_component_refs": ["component:evidence-ledger"],
        "actual_component_refs": [],
        "checks": [{"check_ref": "check:foundation", "state": "blocked",
                    "evidence_refs": [evidence]}],
        "outcome": "blocked", "artifact_refs": [], "evidence_refs": [evidence],
        "deviations": [],
        "source_evidence": {"worktree_ref": "worktree:not-created",
                            "branch_ref": "branch:not-created",
                            "source_sha": "not-created", "evidence_refs": [evidence]},
        "reset_reconstruction": {"fresh_session": True,
                                 "inherited_transcript_used": False,
                                 "reconstruction_free": True,
                                 "remediation_action": None},
        "executor_claim": {"claim_state": "executor_claim", "claimed_by": "codex",
                           "claimed_at": "2026-08-26T09:41:27Z"},
        "independent_verification_required": True,
    }


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

# Harness notifications are turn boundaries, not partner work. Walking backward
# past one would pull stale work into a later agent action; scanning its static
# summary would make the harness itself look like new work.
notification = run([
    user("draft the Apple Mail follow-up"),
    synthetic_user(
        "<task-notification>background git commit completed</task-notification>",
        "task-notification",
    ),
    assistant_tool("Bash", {"command": "date -u"}),
])
check("task notification starts a new turn without carrying stale partner work",
      "joe-comms" not in notification["needed"], str(notification))
check("task notification static text is not observed work",
      "engineering-git" not in notification["needed"], str(notification))

notification_drift = run([
    user("draft the Apple Mail follow-up"),
    synthetic_user("<task-notification>done</task-notification>", "task-notification"),
    assistant_tool("Bash", {"command": "git status"}),
])
check("real work after a task notification remains observable",
      "engineering-git" in notification_drift["needed"], str(notification_drift))

spoofed_notification = run([
    user("<task-notification>background git commit completed</task-notification>"),
])
check("an ordinary user cannot spoof authenticated task-notification suppression",
      "engineering-git" in spoofed_notification["needed"],
      str(spoofed_notification))

for legacy_marker in (
    "The following is the Codex agent history",
    "<environment_context>",
    "<app-context>",
    "<skills_instructions>",
    "<permissions instructions>",
):
    adversarial = run([
        user(f"{legacy_marker} ordinary user request: git commit the change"),
    ])
    check(f"legacy-shaped user text remains observable: {legacy_marker}",
          "engineering-git" in adversarial["needed"], str(adversarial))

# A source-owned scheduled workflow declares its semantic pack explicitly. Its
# long static instructions name many other domains, but only actions chosen by
# the session after the boundary may expand the needed set.
nightly = run([
    scheduled_user(),
    assistant_tool("mcp__carr__standing-context", {"packs": ["scheduled-automation"]}),
    standing_context_result("shadow", ["scheduled-automation"], ["424ba0cc"]),
    assistant_tool("Bash", {
        "command": 'cd ~/carr-system && ./bin/nightly.sh >/dev/null 2>&1; '
                   'echo "direct script exit=$?"'}),
    assistant_tool("Bash", {"command": "cd ~/carr-system && ./run.sh health"}),
])
check("nightly scheduled workflow produces a genuinely scoped event",
      nightly["needed"] == ["scheduled-automation"], str(nightly))
check("nightly canonical selector result has no missing pack",
      nightly["missing"] == [], str(nightly))

nightly_alias = run([
    scheduled_user(),
    assistant_tool("mcp__carr__standing-context", {"packs": ["automation"]}),
    standing_context_result("shadow", ["automation"], ["424ba0cc"], ["automation"]),
])
check("unknown nightly alias cannot satisfy the canonical workflow pack",
      nightly_alias["loaded"] == []
      and nightly_alias["missing"] == ["scheduled-automation"], str(nightly_alias))

nightly_drift = run([
    scheduled_user(),
    assistant_tool("mcp__carr__standing-context", {"packs": ["scheduled-automation"]}),
    standing_context_result("shadow", ["scheduled-automation"], ["424ba0cc"]),
    assistant_tool("Bash", {"command": "git commit -m drift"}),
])
check("source-owned scheduled scope does not hide later real drift",
      "engineering-git" in nightly_drift["missing"], str(nightly_drift))

tampered = scheduled_user()
tampered["message"]["content"][0]["text"] = tampered["message"]["content"][0][
    "text"].replace("seven generated files", "seventeen generated files", 1)
tampered_result = run([tampered])
check("changed scheduled instructions lose authoritative static-text exclusion",
      len(tampered_result["needed"]) > 1, str(tampered_result))

with tempfile.TemporaryDirectory() as directory:
    fixture = Path(directory)
    task_path = fixture / "ops/scheduled-tasks/nightly-record-layer.SKILL.md"
    task_path.parent.mkdir(parents=True)
    map_path = fixture / "ops/config/rule-enforcement-map.json"
    map_path.parent.mkdir(parents=True)
    shutil.copyfile(REPO / "ops/config/rule-enforcement-map.json", map_path)
    original_source = (REPO / task_path.relative_to(fixture)).read_text()
    old_repo, old_map = getattr(gate, "REPO"), getattr(gate, "MAP")
    try:
        setattr(gate, "REPO", str(fixture))
        setattr(gate, "MAP", str(map_path))
        for label, declaration in (
            ("unknown", "scheduled-automatio"),
            ("empty", ""),
            ("duplicate", "scheduled-automation,scheduled-automation"),
        ):
            task_path.write_text(
                original_source.replace(
                    "RULE-DELIVERY PACKS: scheduled-automation",
                    f"RULE-DELIVERY PACKS: {declaration}", 1),
                encoding="utf-8")
            record = scheduled_user(fixture)
            check(f"{label} scheduled pack declaration fails closed",
                  gate.scheduled_workflow_packs(record) == []
                  and bool(gate.work_text(record)))
    finally:
        setattr(gate, "REPO", old_repo)
        setattr(gate, "MAP", old_map)

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

# A typed Engineering Passport receipt is machine output, not prose. The exact
# event 10529812 was polluted by lexical terms inside a blocked receipt (tour,
# ledger, artifact) even though the receipt said no source or component work
# occurred. Only a schema-exact assistant receipt may be normalized; every
# near-shape and every surrounding tool action remains observable.
receipt = engineering_receipt()
receipt_json = json.dumps(receipt, separators=(",", ":"))
machine_only = run([codex_assistant(receipt_json)])
check("exact assistant engineering receipt is not scanned as prose",
      machine_only["needed"] == [], str(machine_only))

extra = dict(receipt)
extra["unreviewed_extra"] = "tour ledger artifact"
check("extra-key receipt-shaped assistant JSON remains observable",
      "client-deal" in run([codex_assistant(json.dumps(extra))])["needed"])
check("trailing prose after an exact receipt remains observable",
      "client-deal" in run([codex_assistant(receipt_json + "\nplease tour")])["needed"])
check("user-supplied exact receipt remains observable",
      "client-deal" in run([user(receipt_json)])["needed"])
check("Codex user-supplied exact receipt remains observable",
      "client-deal" in run([codex_user(receipt_json)])["needed"])

python_literal = repr(receipt)
embedded = run([
    codex_tool("exec", "const r = await tools.exec_command({cmd: "
               + json.dumps("python3 - <<'PY'\nreceipt = " + python_literal
                            + "\nprint(receipt)\nPY")
               + ", workdir: '/Users/booko/carr-system'}); text(r.output);"),
    codex_tool("exec", "const r = await tools.exec_command({cmd: "
               + json.dumps("git status --short") + "}); text(r.output);"),
])
check("exact Python receipt literal in assistant tool payload is normalized",
      set(embedded["needed"]) == {"engineering-git"}, str(embedded))
json_emission = run([codex_tool(
    "exec", "const r = await tools.exec_command({cmd: "
    + json.dumps("printf '%s' '" + receipt_json + "'") + "});")])
check("whole-command strict JSON receipt emission is normalized",
      json_emission["needed"] == [], str(json_emission))
json_then_action = run([codex_tool(
    "exec", "const r = await tools.exec_command({cmd: "
    + json.dumps("printf '%s' '" + receipt_json + "'; git status") + "});")])
check("JSON receipt followed by executable work is not normalized",
      "engineering-git" in json_then_action["needed"], str(json_then_action))

malformed_literal = dict(receipt)
malformed_literal["extra"] = "tour ledger artifact"
malformed_embedded = run([codex_tool(
    "exec", "const r = await tools.exec_command({cmd: "
    + json.dumps("receipt = " + repr(malformed_literal)) + "});")])
check("malformed embedded receipt literal fails closed to lexical scanning",
      "client-deal" in malformed_embedded["needed"], str(malformed_embedded))
nested_malformed = json.loads(receipt_json)
nested_malformed["source_evidence"]["extra"] = "tour"
check("nested extra-key receipt literal fails closed",
      "client-deal" in run([codex_tool(
          "exec", "const r = await tools.exec_command({cmd: "
          + json.dumps("receipt = " + repr(nested_malformed)) + "});")])["needed"])
trailing_tool_prose = run([codex_tool(
    "exec", "const r = await tools.exec_command({cmd: "
    + json.dumps("receipt = " + python_literal + "\nprint('tour remains visible')") + "});")])
check("prose surrounding an exact tool receipt remains observable",
      "client-deal" in trailing_tool_prose["needed"], str(trailing_tool_prose))
executable_receipt = dict(receipt)
executable_receipt["source_evidence"] = {
    **receipt["source_evidence"], "source_sha": "git"}
executable_tool = run([codex_tool(
    "exec", "const r = await tools.exec_command({cmd: "
    + json.dumps("import subprocess\nreceipt = " + repr(executable_receipt)
                 + "\nsubprocess.run([receipt['source_evidence']['source_sha'], 'status'])")
    + "});")])
check("receipt-derived executable work is never normalized away",
      "engineering-git" in executable_tool["needed"], str(executable_tool))

# Compact replay of the immutable source behind event 10529812. It preserves
# the two relevant custom-tool shapes: a validated receipt literal and the
# job-passport source inspection. The source-owned workflow now declares the
# four packs that were actually implicated; receipt-only client/ledger/surface
# vocabulary must disappear without hiding git, executor, source, or job.
event_replay = run([
    codex_user("Execute the admitted engineering slice."),
    codex_tool("exec", "const r = await tools.mcp__carr__standing_context("
               "{packs:['engineering-git','delegation-council',"
               "'scheduled-automation','source-study']}); text(r);"),
    codex_standing_context_result(
        "shadow", ["engineering-git", "delegation-council",
                   "scheduled-automation", "source-study"], ["424ba0cc"]),
    codex_tool("exec", "const r = await tools.exec_command({cmd: "
               + json.dumps("python3 - <<'PY'\nreceipt = " + python_literal
                            + "\nprint(receipt)\nPY")
               + ", workdir:'/Users/booko/carr-system'}); text(r.output);"),
    codex_tool("exec", "const r = await tools.exec_command({cmd: "
               + json.dumps("sed -n '1,230p' tools/room-bridge/engineering_passport.py\n"
                            "sed -n '300,365p' dealroom/js/job-passport.js\n"
                            "git status --short\nexecutor source")
               + "}); text(r.output);"),
    codex_assistant(receipt_json),
])
check("event 10529812 replay needs exactly the four engineering-slice packs",
      event_replay["needed"] == ["delegation-council", "engineering-git",
                                  "scheduled-automation", "source-study"],
      str(event_replay))
check("event 10529812 replay has no missing pack",
      event_replay["missing"] == [], str(event_replay))

# On the evidence-owning machine, replay the immutable 69-record prefix that
# produced event 10529812. The only hypothetical inputs are the landed
# source-owned wrapper rail and its required four-pack readback; every original
# tool action and the exact final receipt remain byte-for-byte source evidence.
exact_replay_path = Path(
    "/Users/booko/.codex/sessions/2026/08/26/"
    "rollout-2026-08-26T04-40-10-01a03d70-fd22-7e63-bbcf-78a0cee529fa.jsonl")
if exact_replay_path.is_file():
    exact_raw = exact_replay_path.read_bytes()
    check("event 10529812 transcript digest is immutable",
          gate.file_sha256(exact_replay_path)
          == "1e1dc9a09fc10cbb1906fc38c22b178207a64e40ac84f25d347abc157000eb73")
    exact_records = [json.loads(line) for line in exact_raw.splitlines()[:69]]
    exact_records.append(codex_standing_context_result(
        "shadow", list(gate.ENGINEERING_WORKFLOW_PACKS), ["424ba0cc"]))
    exact_result = run(exact_records)
    check("immutable event 10529812 action replay removes receipt-only packs",
          exact_result["needed"] == ["engineering-git", "scheduled-automation",
                                     "source-study"],
          str(exact_result))
    check("immutable event 10529812 action replay closes drift",
          exact_result["missing"] == [], str(exact_result))

# ── every new observation can be bound to the exact epoch source/map ───────
source_digest = gate.source_sha256(REPO)
map_digest = gate.file_sha256(REPO / "ops/config/rule-enforcement-map.json")
check("shadow source identity is a sha256", len(source_digest) == 64, source_digest)
check("reviewed map identity is a sha256", len(map_digest) == 64, map_digest)
check("nightly consumer is part of the strict shadow source identity",
      "ops/scheduled-tasks/nightly-record-layer.SKILL.md" in WINDOW_SOURCE_PATHS,
      str(WINDOW_SOURCE_PATHS))
check("engineering-slice workflow is part of the strict shadow source identity",
      "ops/scheduled-tasks/engineering-slice.SKILL.md" in WINDOW_SOURCE_PATHS,
      str(WINDOW_SOURCE_PATHS))
engineering_task = (REPO / "ops/scheduled-tasks/engineering-slice.SKILL.md").read_text()
check("engineering-slice declares its canonical rule-delivery workflow",
      "RULE-DELIVERY WORKFLOW: engineering-slice" in engineering_task)
check("engineering-slice declares the exact reviewed pack set",
      "RULE-DELIVERY PACKS: engineering-git,delegation-council,scheduled-automation,source-study"
      in engineering_task)
check("engineering-slice refuses before work when scoped standing-context fails",
      "REFUSE" in engineering_task and "standing-context" in engineering_task)
spoofed_engineering = codex_user(
    gate.ENGINEERING_WORKFLOW_HEADER.replace("source-study", "source-studies", 1)
    + "SERVER-ISSUED SLICE PACKET (immutable):\n{}\n\n"
      "CONTROLLER TASK BINDING (immutable):\n{}")
check("changed engineering workflow wrapper loses source-owned pack declaration",
      gate.engineering_workflow_packs(spoofed_engineering) == [])
copied_engineering = codex_user(
    gate.ENGINEERING_WORKFLOW_HEADER
    + "FIRST: call `standing-context` with exactly the four packs above. "
      "REFUSE before inspecting the envelope, source, or job.\n\n"
      "SERVER-ISSUED SLICE PACKET (immutable):\n{}\n\n"
      "CONTROLLER TASK BINDING (immutable):\n{}")
check("copied engineering wrapper without strict bindings is not provenance",
      gate.engineering_workflow_packs(copied_engineering) == [])
check("copied unbound engineering wrapper fails closed to lexical scanning",
      bool(gate.work_text(copied_engineering)))
with tempfile.TemporaryDirectory() as directory:
    copy = Path(directory)
    for relative in WINDOW_SOURCE_PATHS:
        target = copy / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO / relative, target)
    before = source_sha256(copy)
    nightly_copy = copy / "ops/scheduled-tasks/nightly-record-layer.SKILL.md"
    nightly_copy.parent.mkdir(parents=True, exist_ok=True)
    if not nightly_copy.exists():
        shutil.copyfile(REPO / "ops/scheduled-tasks/nightly-record-layer.SKILL.md",
                        nightly_copy)
    nightly_copy.write_text(nightly_copy.read_text() + "\n", encoding="utf-8")
    after_nightly = source_sha256(copy)
    check("nightly consumer drift changes the epoch source digest",
          after_nightly != before)
    adapter_copy = copy / "tools/room-bridge/engineering_dispatch_adapter.py"
    adapter_copy.write_text(adapter_copy.read_text() + "\n", encoding="utf-8")
    check("engineering dispatch contract drift changes the epoch source digest",
          source_sha256(copy) != after_nightly)

# Arbitrary exception detail (including credentials) is never persisted.
with tempfile.TemporaryDirectory() as directory:
    transcript = Path(directory) / "transcript.jsonl"
    secret = "postgresql://user:SUPER-SECRET@example.invalid/db"  # ci-secret-scan: allow
    transcript.write_text("{not-json " + secret + "\n", encoding="utf-8")
    old_log, old_stdin = getattr(gate, "LOG"), sys.stdin
    setattr(gate, "LOG", str(Path(directory) / "shadow.jsonl"))
    sys.stdin = io.StringIO(json.dumps({"transcript_path": str(transcript),
                                        "session_id": "secret-test"}))
    try:
        gate.main()
    finally:
        setattr(gate, "LOG", old_log)
        sys.stdin = old_stdin
    persisted = Path(directory, "shadow.jsonl").read_text()
    error_row = json.loads(persisted)
    check("hook exception detail is redacted", secret not in persisted, persisted)
    check("hook error row retains exact v2 shape",
          gate.make_error_observation(
              session=error_row["session"], error=error_row["error"],
              detail=error_row["detail"], map_digest=error_row["map_digest"],
              source_digest=error_row["source_digest"],
              at=gate.stamp(error_row)) == error_row, str(error_row))

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
print("rule-pack-drift-gate-selftest: all cases passed")
