#!/usr/bin/env python3
"""Block a CARR map task from ending before the live map method is loaded."""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(REPO, "out", "map-architecture-gate.jsonl")
CARR_PATH_MARKERS = ("/carr-system/", "/carr-system", "my drive/carr ai")
SYNTHETIC_PREFIXES = ("The following is the Codex agent history", "<environment_context>",
                      "<app-context>", "<skills_instructions>", "<permissions instructions>")
FIGURATIVE = re.compile(r"\b(?:roadmap|road map|mind map|concept map|relationship map|map out)\b", re.I)
MAP_CONTEXT = re.compile(
    r"\b(?:interactive\s+map|tour\s+map|property\s+map|market\s+map|route\s+map|"
    r"google\s+maps?|mapbox|maplibre|leaflet|geospatial|GIS|waypoints?|map\s+pins?|"
    r"day[ -]trip|mapping\s+(?:stack|system|workflow|architecture)|"
    r"map\s+(?:for|of|showing|with|that|system|architecture|stack|workflow))\b", re.I)
TASK_ACTION = re.compile(
    r"\b(?:build|create|make|design|redesign|revise|update|fix|generate|publish|deliver|"
    r"recommend|choose|which|best|should|integrate|review|audit|help|want|need|architecture)\b", re.I)
CALL_MARKER = re.compile(
    r"(?:mcp__(?:carr|carr_records)__(?:map[-_]architecture)|"
    r"run\.sh\s+call\s+map-architecture\b|"
    r"call[_-]verb[^\n]{0,240}map-architecture)", re.I)
CONTRACT_ID = "carr-workspace-market-map-route-planning"
CONTRACT_VERSION = "1.2.0"
CONTRACT_PATH = "workspace/contracts/market-map-route-planning.v1.json"
REQUIRED_METHOD_IDS = {
    "recursive_source_intake",
    "typed_domain_queries",
    "spatial_authoring_workbench",
    "deterministic_component_registry",
    "portable_geospatial_interchange",
    "entrance_level_coordinate_verification",
    "route_label_identity_separation",
    "search_and_tour_modes",
    "map_event_contract",
    "provider_rights_receipt",
    "human_promotion_receipt",
}
REQUIRED_SOURCES = {
    ("maps-and-demographics", "ai-built-interactive-tour-maps-source-rendering-routing-and-promotion-gate"),
    ("carr-workspace-bduf", "s13-ipad-application-and-tour-mode"),
}


def _content_text(content, kinds=("text", "input_text", "output_text")):
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    values = []
    for block in content:
        if isinstance(block, dict) and block.get("type") in kinds:
            values.append(str(block.get("text", "")))
    return "\n".join(values)


def role_and_text(record):
    payload = record.get("payload")
    if isinstance(payload, dict) and payload.get("type") == "message":
        return payload.get("role"), _content_text(payload.get("content"))
    message = record.get("message") if isinstance(record.get("message"), dict) else record
    return message.get("role") or record.get("type"), _content_text(message.get("content"))


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
    if value.lstrip().startswith(SYNTHETIC_PREFIXES):
        return ""
    message = record.get("message") if isinstance(record.get("message"), dict) else record
    content = message.get("content")
    if isinstance(content, list) and content and all(
            isinstance(block, dict) and block.get("type") == "tool_result" for block in content):
        return ""
    return value


def is_map_task(value):
    if not value or FIGURATIVE.search(value):
        return False
    return bool(MAP_CONTEXT.search(value) and TASK_ACTION.search(value))


def latest_task(records):
    for index in range(len(records) - 1, -1, -1):
        value = genuine_user_task(records[index])
        if value:
            return index, value
    return -1, ""


def _parse_json_value(value):
    if isinstance(value, dict):
        for key in ("structuredContent", "structured_content", "result"):
            nested = value.get(key)
            if isinstance(nested, dict):
                return _parse_json_value(nested)
        content = value.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and isinstance(block.get("text"), str):
                    parsed = _parse_json_value(block["text"])
                    if parsed is not None:
                        return parsed
        return value
    if isinstance(value, list):
        for item in value:
            parsed = _parse_json_value(item)
            if parsed is not None:
                return parsed
        return None
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return None
    return _parse_json_value(parsed)


def _tool_result(record, expected_tool_id=None):
    payload = record.get("payload")
    if isinstance(payload, dict) and payload.get("type") == "custom_tool_call_output":
        return _parse_json_value(payload.get("output"))
    message = record.get("message") if isinstance(record.get("message"), dict) else record
    content = message.get("content")
    if not isinstance(content, list):
        return None
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_result":
            continue
        if block.get("is_error"):
            continue
        if expected_tool_id and block.get("tool_use_id") != expected_tool_id:
            continue
        return _parse_json_value(block.get("content"))
    return None


def _map_call_id(record):
    raw = serialized(record)
    if not CALL_MARKER.search(raw):
        return None
    message = record.get("message") if isinstance(record.get("message"), dict) else record
    content = message.get("content")
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use" and CALL_MARKER.search(serialized(block)):
                return block.get("id") or "__unidentified__"
    payload = record.get("payload")
    if isinstance(payload, dict) and payload.get("type") == "custom_tool_call":
        return "__codex__"
    return "__unidentified__"


def _valid_architecture_payload(value):
    if not isinstance(value, dict):
        return False
    if value.get("ok") is not True or value.get("architecture") != "carr-map-tour-v1":
        return False
    contract = value.get("contract")
    if not isinstance(contract, dict) or contract.get("id") != CONTRACT_ID:
        return False
    if contract.get("version") != CONTRACT_VERSION or contract.get("path") != CONTRACT_PATH:
        return False
    methods = value.get("method_ids")
    if not isinstance(methods, list) or not REQUIRED_METHOD_IDS.issubset(set(methods)):
        return False
    sources = value.get("sources")
    if not isinstance(sources, list):
        return False
    seen = set()
    for source in sources:
        if not isinstance(source, dict) or not isinstance(source.get("body_text"), str):
            return False
        if not source["body_text"].strip() or not isinstance(source.get("version"), int):
            return False
        seen.add((source.get("document"), source.get("section_key")))
    return REQUIRED_SOURCES.issubset(seen)


def successful_architecture_read(records):
    call_id = None
    for record in records:
        found_call = _map_call_id(record)
        if found_call:
            call_id = found_call
            continue
        expected = None if call_id in {None, "__codex__", "__unidentified__"} else call_id
        result = _tool_result(record, expected)
        if call_id and _valid_architecture_payload(result):
            return True
    return False


def evaluate(records):
    index, task = latest_task(records)
    if index < 0 or not is_map_task(task):
        return False, "current task is not governed map work"
    if successful_architecture_read(records[index + 1:]):
        return False, "current task loaded carr-map-tour-v1"
    return True, "current map task did not load carr-map-tour-v1 after the request"


def payload_is_carr(payload):
    cwd = payload.get("cwd") or payload.get("working_directory") or payload.get("workingDirectory")
    if not isinstance(cwd, str) or not cwd.strip():
        return True
    normalized = cwd.replace("\\", "/").lower()
    repo = REPO.replace("\\", "/").lower().rstrip("/")
    return (normalized == repo or normalized.startswith(repo + "/")
            or any(marker in normalized for marker in CARR_PATH_MARKERS))


def audit(row):
    if row.get("session") == "selftest":
        return
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a") as handle:
            handle.write(json.dumps(row) + "\n")
    except Exception:
        pass


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
        blocked, reason = evaluate(records)
        if not blocked:
            return 0
        audit({"ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
               "hook": "map-architecture-gate",
               "session": payload.get("session_id") or payload.get("sessionId"),
               "reason": reason})
        print(json.dumps({"decision": "block", "reason":
            "MAP ARCHITECTURE GATE — call the live `map-architecture` verb now. "
            "Read its two current doctrine sections and machine-contract pointer, then do the "
            "map work against carr-map-tour-v1. A prior task's read does not satisfy this one."}))
        return 0
    except Exception as exc:
        if payload_is_carr(payload):
            audit({"ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                   "hook": "map-architecture-gate",
                   "session": payload.get("session_id") or payload.get("sessionId"),
                   "reason": "gate internal failure", "error": type(exc).__name__})
            print(json.dumps({"decision": "block", "reason":
                "MAP ARCHITECTURE GATE FAILED CLOSED — the governed map-method check could not be verified. Repair the gate or transcript read before ending this CARR task."}))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
