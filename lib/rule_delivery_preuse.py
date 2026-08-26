"""Exact receipt contract shared by pre-use selection and Stop telemetry."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from lib.rule_delivery_shadow import file_sha256, source_sha256


PACK = "scheduled-automation"
RECEIPT_SCHEMA = "rule-delivery-preuse-reselection/v1"
RECEIPT_KEYS = frozenset({
    "schema", "receipt_id", "client", "session_id", "turn_id", "tool_use_id",
    "tool_name", "tool_input_sha256", "pack", "map_digest", "source_digest",
    "identity", "rule_ids", "rules", "rule_delivery",
})
IDENTITY_KEYS = frozenset({
    "agent_principal_id", "runtime_principal", "sponsoring_human_id",
})
DELIVERY_KEYS = frozenset({"mode", "declared_packs", "packs_not_found"})
RULE_KEYS = frozenset({"id", "statement"})


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def scheduled_rule_ids(repo: Path) -> list[str]:
    """Derive membership from the current reviewed map, never a typed list."""
    path = repo / "ops/config/rule-enforcement-map.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    layers = data.get("rule_load_layers")
    if not isinstance(layers, dict):
        raise ValueError("reviewed map has no rule_load_layers object")
    found = sorted(
        short for short, row in layers.items()
        if isinstance(short, str) and isinstance(row, dict)
        and isinstance(row.get("packs"), list) and PACK in row["packs"]
    )
    if not found or any(len(short) != 8 for short in found):
        raise ValueError("reviewed map has no valid scheduled-automation members")
    return found


def receipt_id(row: dict) -> str:
    return digest({key: value for key, value in row.items() if key != "receipt_id"})


def _nonempty(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def validate_receipt(row: object, *, repo: Path) -> bool:
    if not isinstance(row, dict) or set(row) != RECEIPT_KEYS:
        return False
    if row.get("schema") != RECEIPT_SCHEMA or row.get("pack") != PACK:
        return False
    if row.get("client") not in {"claude", "codex"}:
        return False
    if not all(_nonempty(row.get(key)) for key in (
            "receipt_id", "session_id", "tool_use_id", "tool_name",
            "tool_input_sha256", "map_digest", "source_digest")):
        return False
    turn_id = row.get("turn_id")
    if row["client"] == "codex":
        if not _nonempty(turn_id):
            return False
    elif turn_id is not None:
        return False
    if ((row["client"] == "claude" and row["tool_name"] != "Bash")
            or (row["client"] == "codex"
                and row["tool_name"] not in {"Bash", "functions.exec"})):
        return False
    identity = row.get("identity")
    if (not isinstance(identity, dict) or set(identity) != IDENTITY_KEYS
            or not all(_nonempty(identity.get(key)) for key in IDENTITY_KEYS)):
        return False
    expected_ids = scheduled_rule_ids(repo)
    if row.get("rule_ids") != expected_ids:
        return False
    rules = row.get("rules")
    if (not isinstance(rules, list) or len(rules) != len(expected_ids)
            or any(not isinstance(item, dict) or set(item) != RULE_KEYS
                   or not _nonempty(item.get("id"))
                   or not _nonempty(item.get("statement")) for item in rules)
            or [item["id"] for item in rules] != expected_ids):
        return False
    delivery = row.get("rule_delivery")
    if (not isinstance(delivery, dict) or set(delivery) != DELIVERY_KEYS
            or delivery.get("mode") not in {"shadow", "enforced"}
            or delivery.get("declared_packs") != [PACK]
            or delivery.get("packs_not_found") != []):
        return False
    map_path = repo / "ops/config/rule-enforcement-map.json"
    if row["map_digest"] != file_sha256(map_path):
        return False
    if row["source_digest"] != source_sha256(repo):
        return False
    return row["receipt_id"] == receipt_id(row)


def _json_text(value: object) -> dict | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def receipt_from_envelope(record: object) -> dict | None:
    """Accept only platform-owned Claude/Codex context envelopes."""
    if not isinstance(record, dict):
        return None
    attachment = record.get("attachment")
    if (record.get("type") == "attachment" and isinstance(attachment, dict)
            and attachment.get("type") == "hook_additional_context"
            and attachment.get("hookEvent") == "PreToolUse"
            and isinstance(attachment.get("content"), list)
            and len(attachment["content"]) == 1):
        row = _json_text(attachment["content"][0])
        if (row and row.get("client") == "claude"
                and attachment.get("hookName") == f"PreToolUse:{row.get('tool_name')}"
                and attachment.get("toolUseID") == row.get("tool_use_id")
                and record.get("sessionId") == row.get("session_id")):
            return row
        return None

    payload = record.get("payload")
    if (record.get("type") == "response_item" and isinstance(payload, dict)
            and payload.get("type") == "message" and payload.get("role") == "developer"):
        content = payload.get("content")
        if (not isinstance(content, list) or len(content) != 1
                or not isinstance(content[0], dict)
                or set(content[0]) != {"type", "text"}
                or content[0].get("type") != "input_text"):
            return None
        row = _json_text(content[0].get("text"))
        metadata = payload.get("internal_chat_message_metadata_passthrough")
        if (row and row.get("client") == "codex" and isinstance(metadata, dict)
                and metadata.get("turn_id") == row.get("turn_id")):
            return row
    return None


def _tool_calls(record: object):
    if not isinstance(record, dict):
        return
    message = record.get("message")
    if (record.get("type") == "assistant" and isinstance(message, dict)
            and message.get("role") == "assistant"):
        for block in message.get("content", []) if isinstance(message.get("content"), list) else []:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                yield (block.get("id"), block.get("name"), block.get("input"),
                       record.get("sessionId"))
    payload = record.get("payload")
    if (record.get("type") == "response_item" and isinstance(payload, dict)
            and payload.get("type") in {"function_call", "custom_tool_call"}):
        raw = payload.get("arguments", payload.get("input"))
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except (TypeError, ValueError):
                raw = None
        yield (payload.get("call_id"), payload.get("name"), raw, None)


def matched_tool_call(row: dict, prior_records: list[dict]) -> bool:
    matches = []
    for record in prior_records:
        for tool_id, name, tool_input, session_id in _tool_calls(record):
            if tool_id == row["tool_use_id"]:
                matches.append((name, tool_input, session_id))
    if len(matches) != 1:
        return False
    name, tool_input, session_id = matches[0]
    return (name == row["tool_name"]
            and isinstance(tool_input, dict)
            and tool_input.get("run_in_background") is True
            and digest(tool_input) == row["tool_input_sha256"]
            and (row["client"] != "claude" or session_id == row["session_id"]))


def preuse_delivery(record: dict, prior_records: list[dict], *, repo: Path):
    row = receipt_from_envelope(record)
    if (row is None or not validate_receipt(row, repo=repo)
            or not matched_tool_call(row, prior_records)):
        return None
    delivery = row["rule_delivery"]
    return delivery["mode"], [PACK], []


def contains_receipt_marker(value: object) -> bool:
    if isinstance(value, dict):
        return (value.get("schema") == RECEIPT_SCHEMA
                or any(contains_receipt_marker(item) for item in value.values()))
    if isinstance(value, list):
        return any(contains_receipt_marker(item) for item in value)
    return isinstance(value, str) and RECEIPT_SCHEMA in value
