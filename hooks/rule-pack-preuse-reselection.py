#!/usr/bin/env python3
"""Load dynamically introduced scheduled-work rules before the tool proceeds.

This is a shadow-compatible reselection rail, not an enforcement gate.  An exact
top-level ``run_in_background: true`` is observed work in the scheduled domain.
The hook calls the existing authenticated standing-context door, injects the
source-owned rules as additional context, and leaves the original tool input
untouched.  Any failure remains visible to Stop telemetry and never blocks.
"""

# doctrine: rule-delivery-load-layers

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable


REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from lib.rule_delivery_preuse import (  # noqa:E402
    PACK, RECEIPT_SCHEMA, canonical, digest, receipt_id,
    scheduled_rule_ids as _scheduled_rule_ids,
)
from lib.rule_delivery_shadow import (  # noqa:E402
    WINDOW_SOURCE_PATHS, file_sha256, source_sha256,
)


MAP = REPO / "ops/config/rule-enforcement-map.json"
FAILURE_CONTEXT = (
    "RULE PACK PREUSE RESELECTION FAILED: selector_unavailable. "
    "The scheduled action was not blocked or rewritten; Stop telemetry must "
    "treat scheduled-automation as not loaded."
)


def scheduled_rule_ids() -> list[str]:
    """Expose the current reviewed-map membership for the hook and its gate."""
    return _scheduled_rule_ids(REPO)


def _nonempty(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _client(payload: dict) -> str:
    return "codex" if _nonempty(payload.get("turn_id")) else "claude"


def _matches(payload: dict) -> bool:
    tool_input = payload.get("tool_input")
    return (payload.get("hook_event_name") == "PreToolUse"
            and payload.get("tool_name") in {"Bash", "functions.exec"}
            and isinstance(tool_input, dict)
            and tool_input.get("run_in_background") is True
            and _nonempty(payload.get("session_id"))
            and _nonempty(payload.get("tool_use_id")))


def _selector_args(ids: list[str]) -> str:
    return canonical({"packs": [PACK], "rule_ids": ids}).decode("utf-8")


def _selector_environment() -> dict[str, str]:
    env = dict(os.environ)
    for name in ("DATABASE_URL", "CARR_BREAK_GLASS", "CARR_BREAK_GLASS_REASON",
                 "CARR_MCP_CLIENT_PROFILE"):
        env.pop(name, None)
    return env


def _run_selector(ids: list[str], runner: Callable) -> dict:
    command = [str(REPO / "run.sh"), "call", "standing-context", _selector_args(ids)]
    result = runner(command, cwd=str(REPO), capture_output=True, text=True,
                    timeout=15, check=False, env=_selector_environment())
    if result.returncode != 0:
        raise RuntimeError("selector returned nonzero")
    try:
        response = json.loads(result.stdout)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("selector returned malformed JSON") from exc
    if not isinstance(response, dict) or response.get("ok") is not True:
        raise RuntimeError("selector response was not ok")
    return response


def _validate_selector(response: dict, ids: list[str]):
    identity = response.get("identity")
    if not isinstance(identity, dict) or not all(_nonempty(identity.get(key)) for key in (
            "agent_principal_id", "runtime_principal", "sponsoring_human_id")):
        raise RuntimeError("selector identity is incomplete")
    delivery = response.get("rule_delivery")
    if (not isinstance(delivery, dict)
            or delivery.get("mode") not in {"shadow", "enforced"}
            or delivery.get("declared_packs") != [PACK]
            or delivery.get("packs_not_found", []) != []):
        raise RuntimeError("selector delivery plan is not exact")
    found: dict[str, str] = {}
    shared = response.get("shared_rules")
    personal = response.get("personal_rules")
    if not isinstance(shared, list) or not isinstance(personal, list):
        raise RuntimeError("selector rule pools are malformed")
    for item in shared + personal:
        if not isinstance(item, dict):
            raise RuntimeError("selector returned a malformed rule")
        short = item.get("id")
        if short in ids:
            if short in found or not _nonempty(item.get("statement")):
                raise RuntimeError("selector returned duplicate or nonbinding rule")
            found[short] = item["statement"]
    if sorted(found) != ids:
        raise RuntimeError("selector did not return every scheduled rule")
    return identity, delivery, [{"id": short, "statement": found[short]} for short in ids]


def _receipt(payload: dict, response: dict, ids: list[str]) -> dict:
    identity, delivery, rules = _validate_selector(response, ids)
    client = _client(payload)
    row = {
        "schema": RECEIPT_SCHEMA,
        "client": client,
        "session_id": payload["session_id"],
        "turn_id": payload.get("turn_id") if client == "codex" else None,
        "tool_use_id": payload["tool_use_id"],
        "tool_name": payload["tool_name"],
        "tool_input_sha256": digest(payload["tool_input"]),
        "pack": PACK,
        "map_digest": file_sha256(MAP),
        "source_digest": source_sha256(REPO),
        "identity": {key: identity[key] for key in (
            "agent_principal_id", "runtime_principal", "sponsoring_human_id")},
        "rule_ids": ids,
        "rules": rules,
        "rule_delivery": {
            "mode": delivery["mode"],
            "declared_packs": [PACK],
            "packs_not_found": [],
        },
    }
    row["receipt_id"] = receipt_id(row)
    return row


def _context(text: str) -> dict:
    return {"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "additionalContext": text,
    }}


def process(payload: dict, *, runner: Callable = subprocess.run) -> dict | None:
    if not _matches(payload):
        return None
    try:
        ids = scheduled_rule_ids()
        response = _run_selector(ids, runner)
        return _context(canonical(_receipt(payload, response, ids)).decode("utf-8"))
    except Exception:
        # Never surface provider/auth/network exception text: it may contain a
        # bearer, URL, or local path.  The fixed category is enough for Stop to
        # preserve the miss and for the operator to reproduce through the door.
        return _context(FAILURE_CONTEXT)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    if not isinstance(payload, dict):
        return 0
    output = process(payload)
    if output is not None:
        print(json.dumps(output, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
