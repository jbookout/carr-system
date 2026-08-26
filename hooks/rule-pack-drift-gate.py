#!/usr/bin/env python3
"""rule-pack-drift-gate.py — the turn's OBSERVED WORK is diffed against the rule
packs the session actually loaded, and the gap is either recorded or reopened.

# doctrine: rule-delivery-load-layers

WHY THIS EXISTS. Scoping rule delivery is safe only while one thing stays true:
a session that wanders into work it never declared still gets that work's rules.
Rule 347a9ca6 is the law — "a session's name does not predict the work it will
do", taught after Joe pointed out that he does full system builds inside a
session called nightly-record-layer. Both 2026-08-23 council chairs made the
same requirement structural rather than hopeful:

    "A Stop gate diffs the turn's verbs and nouns against loaded packs. If
     git/CI ran and the engineering pack was not loaded, reopen and load."

Without this gate, scoping does not merely risk the old failure — it INSTALLS
it, because the boot payload would shrink on the guess that a session's declared
work is its whole work.

WHAT IT LOOKS AT. Only the current turn: every tool call the session made, the
verbs it named, and the text on both sides, from the last genuine user message
onward. Triggers come from ops/config/rule-enforcement-map.json, the same
reviewed file the database tags are compiled from, so the gate and the compiler
cannot drift into disagreeing about what a pack is for.

SHADOW FIRST, AND THE MODE IS NOT THIS FILE'S TO DECIDE. Both chairs required a
week of running the selector beside full recitation before anything is cut. That
switch lives in one place — ops.rule_delivery_policy in the database — and this
gate reads it the only way a hook can: out of the standing-context result already
sitting in the transcript. No second copy of the policy, no local flag to fall
out of sync with the verb (rule 0f38532e). If the mode cannot be established,
the gate RECORDS and does not block, because a gate that blocks on an
unestablished policy is a gate that will be removed within a week.

WHAT IT WRITES, and this is the shadow week's whole evidence base:
out/rule-delivery-shadow.jsonl, one row per turn — which packs the work implied,
which were loaded, which rules a scoped boot would have omitted, and the subset
of those the work actually needed. ops/rule-delivery-shadow-watch.py reads it
nightly, so the comparison cannot quietly stop running (the lesson of the
admission contract, which sat unmeasured in Production for months because the
only thing that could measure it was a door a human had to open).

A MISS is the row that matters: a rule this turn's work needed, that a scoped
boot would not have delivered. Enforcement flips on at zero unexplained misses.
"""
from __future__ import annotations

import ast
import importlib
import json
import os
import re
import sys
from pathlib import Path

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
from lib.rule_delivery_shadow import (  # noqa:E402
    append_locked, file_sha256, make_error_observation, make_observation,
    source_sha256, stamp,
)
MAP = os.path.join(REPO, "ops", "config", "rule-enforcement-map.json")
LOG = os.path.join(REPO, "out", "rule-delivery-shadow.jsonl")
RECEIPT_SCHEMA = os.path.join(
    REPO, "control-room", "contracts", "engineering-slice-receipt.v1.schema.json")
CARR_PATH_MARKERS = ("/carr-system/", "/carr-system", "my drive/carr ai")
SYNTHETIC_PREFIXES = ("The following is the Codex agent history", "<environment_context>",
                      "<app-context>", "<skills_instructions>", "<permissions instructions>",
                      "<task-notification>", "<scheduled-task")
SCHEDULED_TASK_PREAMBLE = (
    "This is an automated run of a scheduled task. The user is not present "
    "to answer questions. For implementation details, execute autonomously "
    "without asking clarifying questions — make reasonable choices and note "
    "them in your output. \"write\" actions (e.g. MCP tools that send, post, "
    "create, update, or delete), only take them if the task file asks for that "
    "specific action. When in doubt, producing a report of what you found is "
    "the correct output."
)
ENGINEERING_WORKFLOW_PACKS = (
    "engineering-git", "delegation-council", "scheduled-automation", "source-study")
ENGINEERING_WORKFLOW_HEADER = (
    "You are the fresh, dedicated Codex executor for one bounded CARR Engineering Passport slice.\n\n"
    "RULE-DELIVERY WORKFLOW: engineering-slice\n"
    "RULE-DELIVERY PACKS: engineering-git,delegation-council,scheduled-automation,source-study\n")


def load_packs():
    """Pack name -> compiled trigger regex, and pack name -> its rules."""
    with open(MAP, encoding="utf-8") as handle:
        data = json.load(handle)
    triggers, members = {}, {}
    for name, pack in data.get("rule_packs", {}).items():
        words = [re.escape(t) for t in pack.get("triggers", []) if str(t).strip()]
        if not words:
            continue
        # \b around a term that ends in punctuation (x.com) never matches, so the
        # boundary is only asserted where the term's own edge is a word character.
        parts = []
        for word in words:
            left = r"\b" if re.match(r"\w", word[0]) else ""
            right = r"\b" if re.match(r"\w", word[-1]) else ""
            parts.append(f"{left}{word}{right}")
        triggers[name] = re.compile("|".join(parts), re.I)
    for short, entry in data.get("rule_load_layers", {}).items():
        for name in entry.get("packs", []):
            members.setdefault(name, []).append(short)
    return triggers, members


def _content_text(content, kinds=("text", "input_text", "output_text")):
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "\n".join(str(b.get("text", "")) for b in content
                     if isinstance(b, dict) and b.get("type") in kinds)


def _payload_message(record):
    payload = record.get("payload")
    if isinstance(payload, dict) and payload.get("type") == "message":
        return payload
    return None


def role_and_text(record):
    payload = record.get("payload")
    if isinstance(payload, dict) and payload.get("type") == "message":
        return payload.get("role"), _content_text(payload.get("content"))
    message = record.get("message") if isinstance(record.get("message"), dict) else record
    return message.get("role") or record.get("type"), _content_text(message.get("content"))


def _schema_valid(value, schema, root):
    """The dependency-free subset of JSON Schema used by the receipt contract."""
    if "$ref" in schema:
        prefix = "#/$defs/"
        ref = schema["$ref"]
        return (isinstance(ref, str) and ref.startswith(prefix)
                and _schema_valid(value, root["$defs"].get(ref[len(prefix):], {}), root))
    if "oneOf" in schema:
        return sum(_schema_valid(value, item, root) for item in schema["oneOf"]) == 1
    if "const" in schema and value != schema["const"]:
        return False
    if "enum" in schema and value not in schema["enum"]:
        return False
    kind = schema.get("type")
    if kind == "object":
        if not isinstance(value, dict):
            return False
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        if any(key not in value for key in required):
            return False
        if schema.get("additionalProperties") is False and set(value) - set(properties):
            return False
        return all(key not in properties or _schema_valid(item, properties[key], root)
                   for key, item in value.items())
    if kind == "array":
        if not isinstance(value, list) or len(value) < schema.get("minItems", 0):
            return False
        if schema.get("uniqueItems"):
            rows = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in value]
            if len(rows) != len(set(rows)):
                return False
        return all(_schema_valid(item, schema.get("items", {}), root) for item in value)
    if kind == "string":
        return (isinstance(value, str) and len(value) >= schema.get("minLength", 0)
                and ("pattern" not in schema or re.fullmatch(schema["pattern"], value) is not None))
    if kind == "boolean":
        return isinstance(value, bool)
    if kind == "null":
        return value is None
    return True


def exact_engineering_receipt(value):
    try:
        schema = json.loads(Path(RECEIPT_SCHEMA).read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return False
    return _schema_valid(value, schema, schema)


def assistant_machine_receipt(record):
    """Return only a whole, strict, assistant-authored typed receipt."""
    message = _payload_message(record)
    if message is None:
        message = record.get("message") if isinstance(record.get("message"), dict) else record
    if message.get("role") != "assistant":
        return None
    content = message.get("content")
    if not isinstance(content, list) or len(content) != 1:
        return None
    block = content[0]
    if not isinstance(block, dict) or block.get("type") not in {
            "text", "output_text"} or not isinstance(block.get("text"), str):
        return None
    try:
        value = json.loads(block["text"].strip())
    except (TypeError, ValueError):
        return None
    return value if exact_engineering_receipt(value) else None


def serialized(record):
    values = []

    def walk(value):
        if isinstance(value, dict):
            for key, item in value.items():
                values.append(str(key))
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
        elif value is not None:
            values.append(str(value))

    walk(record)
    return "\n".join(values)


def genuine_user_task(record):
    role, value = role_and_text(record)
    if role not in {"user", "human"} or not value.strip():
        return ""
    if synthetic_turn_boundary(record):
        return ""
    message = record.get("message") if isinstance(record.get("message"), dict) else record
    content = message.get("content")
    if isinstance(content, list) and content and all(
            isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
        return ""
    return value


def synthetic_turn_boundary(record):
    role, value = role_and_text(record)
    if role not in {"user", "human"} or not value.strip():
        return False
    head = value.lstrip()
    if head.startswith("<task-notification>"):
        origin = record.get("origin")
        return isinstance(origin, dict) and origin.get("kind") == "task-notification"
    if head.startswith("<scheduled-task"):
        return bool(scheduled_workflow_packs(record))
    return head.startswith(SYNTHETIC_PREFIXES)


def suppress_static_text(record):
    """Only provenance-authenticated or exact source-owned wrappers are static."""
    role, value = role_and_text(record)
    if role not in {"user", "human"} or not value.strip():
        return False
    head = value.lstrip()
    if head.startswith("<task-notification>"):
        origin = record.get("origin")
        return isinstance(origin, dict) and origin.get("kind") == "task-notification"
    return head.startswith("<scheduled-task") and bool(scheduled_workflow_packs(record))


def current_turn(records):
    for index in range(len(records) - 1, -1, -1):
        if genuine_user_task(records[index]) or synthetic_turn_boundary(records[index]):
            return records[index:]
    return records


def scheduled_workflow_packs(record):
    """Packs from an exact source-owned scheduled-task wrapper, else none.

    The scheduler omits YAML frontmatter and prepends one fixed paragraph.  We
    compare the complete rendered body before honoring its declaration: a user
    cannot paste the marker into arbitrary instructions to suppress lexical
    drift detection, and a changed installed task fails closed to ordinary text
    scanning until config-as-code restores source parity.
    """
    role, value = role_and_text(record)
    if role not in {"user", "human"} or not value.lstrip().startswith("<scheduled-task"):
        return []
    matched = re.fullmatch(
        r'<scheduled-task name="([a-z0-9-]+)" file="([^"]+)">\n(.*)</scheduled-task>',
        value.strip(), re.S)
    if not matched:
        return []
    name, installed_path, inner = matched.groups()
    if not installed_path.endswith(f"/.claude/scheduled-tasks/{name}/SKILL.md"):
        return []
    source = Path(REPO) / "ops" / "scheduled-tasks" / f"{name}.SKILL.md"
    if not source.is_file():
        return []
    portable = source.read_text(encoding="utf-8")
    pieces = portable.split("---\n", 2)
    if len(pieces) != 3:
        return []
    body = pieces[2].lstrip().replace("{{REPO}}", REPO)
    if inner != f"{SCHEDULED_TASK_PREAMBLE}\n\n{body}":
        return []
    workflow = re.search(r"^RULE-DELIVERY WORKFLOW: ([a-z0-9-]+)$", body, re.M)
    packs = re.search(r"^RULE-DELIVERY PACKS: ([a-z0-9,-]+)$", body, re.M)
    if not workflow or workflow.group(1) != name or not packs:
        return []
    declared = packs.group(1).split(",")
    catalog = json.loads(Path(MAP).read_text(encoding="utf-8")).get("rule_packs", {})
    if (not declared or any(not pack for pack in declared)
            or len(set(declared)) != len(declared)
            or any(pack not in catalog for pack in declared)):
        return []
    return declared


def engineering_workflow_packs(record):
    """Packs from a fully bound controller Engineering Passport wrapper."""
    role, value = role_and_text(record)
    if role not in {"user", "human"} or not value.startswith(ENGINEERING_WORKFLOW_HEADER):
        return []
    if (value.count("SERVER-ISSUED SLICE PACKET (immutable):") != 1
            or value.count("CONTROLLER TASK BINDING (immutable):") != 1
            or "FIRST: call `standing-context` with exactly the four packs above" not in value
            or "REFUSE before inspecting the envelope, source, or job" not in value):
        return []
    packet_marker = "SERVER-ISSUED SLICE PACKET (immutable):\n"
    task_marker = "\n\nCONTROLLER TASK BINDING (immutable):\n"
    try:
        packet_text, task_text = value.split(packet_marker, 1)[1].split(task_marker, 1)
        packet = json.loads(packet_text)
        task = json.loads(task_text)
        bridge = os.path.join(REPO, "tools", "room-bridge")
        if bridge not in sys.path:
            sys.path.insert(0, bridge)
        passport = importlib.import_module("engineering_passport")
        expected_task_keys = {
            "attempt_id", "engineering_plan", "engineering_slice", "generation",
            "job_ref", "plan_digest", "slice_ref", "work_request",
        }
        if not isinstance(task, dict) or set(task) != expected_task_keys:
            return []
        plan = passport.validate_engineering_slice_plan(task["engineering_plan"])
        expected_packet_keys = {
            "schema_version", "slice_ref", "plan_digest", "envelope_digest",
            "fresh_native_session_required", "objective", "definition_of_done",
            "planned_checks", "scope_boundary", "forbidden_change_refs", "envelope",
            "packet_digest",
        }
        if not isinstance(packet, dict) or set(packet) != expected_packet_keys:
            return []
        envelope = passport.base.validate_execution_envelope(packet["envelope"])
        selected = next(item for item in plan["slices"]
                        if item["slice_ref"] == task["slice_ref"])
        packet_without_digest = {key: item for key, item in packet.items()
                                 if key != "packet_digest"}
        expected_scope = {
            "plan_step_refs": sorted(set(selected["declared_plan_step_refs"])),
            "component_refs": sorted(set(selected["declared_component_refs"])),
            "component_dependencies": sorted(
                envelope["request"]["declared_expectations"]["component_dependencies"],
                key=lambda edge: (edge["component_ref"], edge["depends_on_component_ref"])),
            "resource_refs": sorted(set(selected["declared_resource_refs"])),
        }
        if (task["engineering_slice"] != selected
                or task["plan_digest"] != plan["plan_digest"]
                or packet["plan_digest"] != plan["plan_digest"]
                or packet["slice_ref"] != task["slice_ref"]
                or packet["schema_version"] != "engineering-slice-packet.v1"
                or packet["fresh_native_session_required"] is not True
                or not isinstance(packet["envelope_digest"], str)
                or re.fullmatch(r"sha256:[a-f0-9]{64}", packet["envelope_digest"]) is None
                or packet["packet_digest"] != passport.base.canonical_digest(
                    packet_without_digest)
                or envelope["request"]["declared_expectations"] != expected_scope
                or envelope["request"]["job_ref"] != task["job_ref"]
                or any(packet[field] != selected[field] for field in (
                    "objective", "definition_of_done", "planned_checks",
                    "scope_boundary", "forbidden_change_refs"))
                or not isinstance(task["generation"], int) or isinstance(task["generation"], bool)
                or task["generation"] < 1
                or not all(isinstance(task[field], str) and task[field].strip()
                           for field in ("attempt_id", "job_ref"))):
            return []
    except (ImportError, KeyError, StopIteration, TypeError, ValueError):
        return []
    return list(ENGINEERING_WORKFLOW_PACKS)


def delivery_state(records):
    """What this session has loaded, across every standing-context call it made.

    Returns (mode, declared_packs, would_omit). A session that never called the
    verb yields (None, [], []) — nothing to compare against and nothing to block.

    PACKS ACCUMULATE; THEY DO NOT REPLACE. The council's word for it is monotonic:
    entering another domain ADDS its pack and never subtracts an earlier one. That
    is also what stops a false miss here — a session that loads the engineering
    pack, then calls standing-context again bare to look a rule up by id, has not
    unloaded anything, and reading only the latest call would say it had. The mode
    and the omission list come from the LATEST call, because those describe the
    policy and the payload as they stand now.
    """
    mode, declared, omit = None, set(), []
    for record in records:
        if "rule_delivery" not in serialized(record):
            continue
        found = _find_delivery(record)
        if found:
            mode, packs, omit = found
            declared.update(packs)
    return mode, sorted(declared), omit


def _find_delivery(value):
    """Depth-first hunt for a rule_delivery object, whatever wrapper it arrived in."""
    if isinstance(value, dict):
        block = value.get("rule_delivery")
        if isinstance(block, dict) and "mode" in block:
            not_found = {str(p).strip().lower()
                         for p in block.get("packs_not_found", []) or []}
            return (block.get("mode"),
                    [str(p) for p in block.get("declared_packs", []) or []
                     if str(p).strip().lower() not in not_found],
                    [str(r) for r in block.get("would_omit", []) or []])
        for item in value.values():
            found = _find_delivery(item)
            if found:
                return found
        return None
    if isinstance(value, list):
        for item in value:
            found = _find_delivery(item)
            if found:
                return found
        return None
    if isinstance(value, str) and "rule_delivery" in value:
        try:
            return _find_delivery(json.loads(value))
        except (TypeError, ValueError):
            return None
    return None


def _safe_literal(node, values):
    """Evaluate data-only Python literals and local literal-constructor lambdas."""
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id not in values:
            raise ValueError("unknown literal name")
        return values[node.id]
    if isinstance(node, ast.List):
        return [_safe_literal(item, values) for item in node.elts]
    if isinstance(node, ast.Tuple):
        return tuple(_safe_literal(item, values) for item in node.elts)
    if isinstance(node, ast.Dict):
        return {_safe_literal(key, values): _safe_literal(item, values)
                for key, item in zip(node.keys, node.values)}
    if isinstance(node, ast.Lambda):
        return node
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        function = values.get(node.func.id)
        if not isinstance(function, ast.Lambda) or node.keywords:
            raise ValueError("not a data-only literal constructor")
        names = [arg.arg for arg in function.args.args]
        if len(names) != len(node.args):
            raise ValueError("literal constructor arity mismatch")
        local = dict(values)
        local.update({name: _safe_literal(arg, values)
                      for name, arg in zip(names, node.args)})
        return _safe_literal(function.body, local)
    raise ValueError("not a data-only literal")


def _line_offsets(text):
    offsets = [0]
    for matched in re.finditer("\n", text):
        offsets.append(matched.end())
    return offsets


def _call_name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _call_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    if isinstance(node, ast.Call):
        return _call_name(node.func)
    return ""


def _uses_names(node, names):
    return any(isinstance(item, ast.Name) and item.id in names for item in ast.walk(node))


def _target_names(node):
    if isinstance(node, ast.Name):
        return {node.id}
    if isinstance(node, (ast.Tuple, ast.List)):
        return {name for item in node.elts for name in _target_names(item)}
    return set()


def _receipt_use_is_inert(tree, assignment, receipt_name):
    """Refuse normalization if receipt-derived data reaches executable code."""
    allowed = ("print", "json.dumps")
    tainted = {receipt_name}
    after = False
    for statement in tree.body:
        if statement is assignment:
            after = True
            continue
        if not after:
            continue
        for call in (item for item in ast.walk(statement) if isinstance(item, ast.Call)):
            if not _uses_names(call, tainted):
                continue
            name = _call_name(call.func)
            if (name not in allowed and not name.endswith(".validate")
                    and not name.endswith("._validate_receipt")):
                return False
        if isinstance(statement, (ast.Assign, ast.AnnAssign)):
            value = statement.value
            if value is not None and _uses_names(value, tainted):
                targets = statement.targets if isinstance(statement, ast.Assign) \
                    else [statement.target]
                for target in targets:
                    tainted.update(_target_names(target))
    return True


def _python_receipt_spans(text):
    """Locate strict receipt assignments without executing assistant code."""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return []
    offsets = _line_offsets(text)
    values = {}
    spans = []
    for statement in tree.body:
        if not isinstance(statement, ast.Assign) or len(statement.targets) != 1 \
                or not isinstance(statement.targets[0], ast.Name):
            continue
        name = statement.targets[0].id
        try:
            value = _safe_literal(statement.value, values)
        except (TypeError, ValueError):
            continue
        values[name] = value
        if (not exact_engineering_receipt(value)
                or not _receipt_use_is_inert(tree, statement, name)):
            continue
        start = offsets[statement.value.lineno - 1] + statement.value.col_offset
        end = offsets[statement.value.end_lineno - 1] + statement.value.end_col_offset
        spans.append((start, end))
    return spans


def _normalize_receipt_objects(text):
    """Replace only independently strict JSON/Python receipt object literals."""
    spans = _python_receipt_spans(text)
    # Replace outermost matched spans from right to left. Nested valid receipts
    # are impossible under the contract, but de-duplication keeps this total.
    selected = []
    for start, end in sorted(set(spans), key=lambda item: (item[0], -item[1])):
        if not any(existing[0] <= start and end <= existing[1] for existing in selected):
            selected.append((start, end))
    for start, end in sorted(selected, reverse=True):
        text = text[:start] + "[engineering-slice-receipt.v1]" + text[end:]
    return text


def _normalize_inert_json_emission(command):
    """Normalize a strict JSON receipt only when the whole command emits it."""
    matched = re.fullmatch(r"\s*(printf\s+['\"]%s['\"]\s+|echo\s+)'(.*)'\s*",
                           command, re.S)
    if not matched:
        return command
    try:
        value = json.loads(matched.group(2))
    except (TypeError, ValueError):
        return command
    if not exact_engineering_receipt(value):
        return command
    return matched.group(1) + "'[engineering-slice-receipt.v1]'"


def _decoded_command(input_text):
    """Decode a Codex tools.exec_command cmd string, preserving outer metadata."""
    matched = re.search(r"\bcmd\s*:\s*", input_text)
    if not matched:
        return None
    decoder = json.JSONDecoder()
    try:
        value, used = decoder.raw_decode(input_text[matched.end():])
    except (TypeError, ValueError):
        return None
    if not isinstance(value, str):
        return None
    start = matched.end()
    return value, input_text[:start] + '"[decoded-command]"' + input_text[start + used:]


def custom_tool_text(payload):
    """Observe every tool and command, minus strict typed receipt data only."""
    name = str(payload.get("name", ""))
    raw = payload.get("input")
    if not isinstance(raw, str):
        return "\n".join((name, serialized(raw)))
    if "mcp__carr__standing_context" in raw or "mcp__carr__standing-context" in raw:
        # This call establishes delivery state; its surface/tier/detail routing
        # metadata is not observed domain work. Keep the call and declared pack
        # names visible while excluding those fixed transport arguments.
        packs = re.search(r"\bpacks\s*:\s*\[([^]]*)\]", raw)
        return "\n".join((name, "standing_context", packs.group(1) if packs else ""))
    decoded = _decoded_command(raw)
    if decoded:
        command, outer = decoded
        # Python receipts normally sit inside a heredoc. Parsing the body as
        # Python lets aliases such as evidence=E(...) resolve as data-only
        # literals while the shell command around it remains observable.
        matched = re.search(r"<<['\"]?([A-Za-z0-9_]+)['\"]?\n(.*?)\n\1(?:\n|$)",
                            command, re.S)
        if matched:
            body = matched.group(2)
            normalized = _normalize_receipt_objects(body)
            command = command[:matched.start(2)] + normalized + command[matched.end(2):]
        command = _normalize_receipt_objects(command)
        command = _normalize_inert_json_emission(command)
        return "\n".join((name, outer, command))
    return "\n".join((name, _normalize_receipt_objects(raw)))


def work_text(record):
    """What the SESSION did and said this turn — never what a tool said back.

    Tool RESULTS are excluded on purpose, and the reason is not tidiness. The
    standing-context payload itself carries the pack index, which is a list of
    every pack's triggers; scanning results would make one boot call fire every
    pack in the catalog and the gate would demand all of them on every turn. A
    directory listing or a search result has the same shape of problem: the
    nouns in it are the world's, not the session's. What this gate is
    adjudicating is the work the session CHOSE to do — its tool calls and its
    prose — which is exactly what rule 347a9ca6 says to judge by.
    """
    parts = []
    payload = record.get("payload")
    if isinstance(payload, dict) and payload.get("type") == "custom_tool_call":
        parts.append(custom_tool_text(payload))
    message = record.get("message") if isinstance(record.get("message"), dict) else record
    role = message.get("role") or record.get("type")
    if role in {"user", "human"} and suppress_static_text(record):
        return ""
    content = message.get("content")
    payload_message = _payload_message(record)
    if payload_message is not None and payload_message.get("role") in {
            "user", "human", "assistant"}:
        payload_text = _content_text(payload_message.get("content"))
        # Codex response_item messages were not historically part of this
        # adapter's prose path. Admit receipt-shaped rows narrowly: exact
        # machine receipts disappear; malformed/extra/trailing near-shapes fail
        # closed to ordinary lexical scanning.
        if (payload_text.lstrip().startswith("{")
                and "engineering-slice-receipt.v1" in payload_text
                and assistant_machine_receipt(record) is None):
            parts.append(payload_text)
        elif (payload_text.startswith(ENGINEERING_WORKFLOW_HEADER)
              and not engineering_workflow_packs(record)):
            # An ordinary user can copy the visible header. Without a packet
            # rebuilt from the strict plan/envelope and exact task binding it
            # has no source provenance and fails closed to lexical scanning.
            parts.append(payload_text)
    if isinstance(content, str):
        if role in {"user", "human", "assistant"}:
            parts.append(content)
    elif isinstance(content, list):
        receipt = assistant_machine_receipt(record)
        for block in content:
            if not isinstance(block, dict):
                continue
            kind = block.get("type")
            if kind == "tool_use":
                parts.append(str(block.get("name", "")))
                parts.append(serialized(block.get("input")))
            elif kind in {"text", "input_text", "output_text"}:
                if receipt is None:
                    parts.append(str(block.get("text", "")))
    return "\n".join(p for p in parts if p)


def observed_packs(turn, triggers):
    """Which packs THIS TURN's work implies, with the words that fired each."""
    text = "\n".join(work_text(record) for record in turn)
    hits = {}
    for name, pattern in triggers.items():
        found = sorted({m.group(0).lower() for m in pattern.finditer(text)})
        if found:
            hits[name] = found[:6]
    for record in turn:
        for name in scheduled_workflow_packs(record):
            if name in triggers:
                hits.setdefault(name, ["workflow-contract"])
        for name in engineering_workflow_packs(record):
            if name in triggers:
                hits.setdefault(name, ["workflow-contract"])
    return hits


def evaluate(records, triggers, members):
    turn = current_turn(records)
    mode, declared, omit = delivery_state(records)
    hits = observed_packs(turn, triggers)
    needed = sorted(hits)
    loaded = sorted({str(p).strip().lower() for p in declared})
    missing = [p for p in needed if p not in loaded]
    omitted = {str(r).lower() for r in omit}
    # THE MISS: a rule this turn's work needed that a scoped boot would not have
    # handed over. Everything else in this row is context for reading it.
    missed = sorted({short for pack in missing
                     for short in members.get(pack, [])
                     if short.lower() in omitted})
    return {"mode": mode, "needed": needed, "loaded": loaded, "missing": missing,
            "triggers": hits, "would_omit_count": len(omitted), "missed_rules": missed}


def audit(row):
    if row.get("session") == "selftest":
        return
    try:
        append_locked(Path(LOG), lambda _rows: row)
    except Exception:
        pass


def payload_is_carr(payload):
    cwd = (payload.get("cwd") or payload.get("working_directory")
           or payload.get("workingDirectory"))
    if not isinstance(cwd, str) or not cwd.strip():
        return True
    normalized = cwd.replace("\\", "/").lower()
    repo = REPO.replace("\\", "/").lower().rstrip("/")
    return (normalized == repo or normalized.startswith(repo + "/")
            or any(marker in normalized for marker in CARR_PATH_MARKERS))


def block_reason(result):
    packs = ", ".join(result["missing"])
    words = "; ".join(f"{p}: {', '.join(result['triggers'][p])}" for p in result["missing"])
    return ("RULE PACK DRIFT — this turn did work you did not load the rules for. "
            f"Missing pack(s): {packs}. What named them: {words}. "
            f"Call standing-context with packs:[{', '.join(repr(p) for p in result['missing'])}] "
            "and read what comes back before you finish. A session's name does not "
            "predict its work (rule 347a9ca6), which is exactly why this fires on "
            "what you DID rather than on what you said you would do.")


def main():
    payload = {}
    try:
        payload = json.load(sys.stdin)
        if payload.get("stop_hook_active") or not payload_is_carr(payload):
            return 0
        path = payload.get("transcript_path") or payload.get("transcriptPath")
        if not path or not os.path.exists(path):
            return 0
        with open(path, errors="replace") as handle:
            records = [json.loads(line) for line in handle if line.strip()]
        triggers, members = load_packs()
        result = evaluate(records, triggers, members)
        raw_session = payload.get("session_id") or payload.get("sessionId")
        session = raw_session if isinstance(raw_session, str) and raw_session.strip() \
            else "unavailable"
        row = make_observation(
            session=session, map_digest=file_sha256(Path(MAP)),
            source_digest=source_sha256(Path(REPO)), result=result)
        # A turn that implied no pack and loaded none is the ordinary case and
        # writing a row per turn for it would bury the rows that matter.
        if result["needed"] or result["loaded"]:
            audit(row)
        if result["mode"] != "enforced" or not result["missing"]:
            return 0
        print(json.dumps({"decision": "block", "reason": block_reason(result)}))
        return 0
    except Exception as exc:
        # FAIL OPEN, DELIBERATELY, AND SAY SO IN THE LOG. Its siblings fail
        # closed because they guard a claim that would otherwise go out wrong.
        # This one guards DELIVERY: a bug here that blocked every turn would
        # stop all work over a check that has not cut a single rule yet, and the
        # first fix anyone reached for would be to uninstall it.
        raw_session = payload.get("session_id") or payload.get("sessionId")
        session = raw_session if isinstance(raw_session, str) and raw_session.strip() \
            else "unavailable"
        category = {"JSONDecodeError": "invalid-transcript-json",
                    "FileNotFoundError": "transcript-or-source-absent",
                    "PermissionError": "transcript-or-source-unreadable",
                    "ValueError": "invalid-shadow-observation"}.get(
                        type(exc).__name__, "unexpected-gate-error")
        try:
            map_digest = file_sha256(Path(MAP))
            source_digest = source_sha256(Path(REPO))
        except Exception:
            map_digest = "0" * 64
            source_digest = "0" * 64
        row = make_error_observation(
            session=session, error=category, detail="rule-pack-drift-gate-failed-open",
            map_digest=map_digest, source_digest=source_digest)
        audit(row)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
