#!/usr/bin/env python3
"""Load dynamically introduced scheduled-work rules before the tool proceeds.

This is a shadow-compatible reselection rail, not an enforcement gate.  An exact
top-level ``run_in_background: true`` is observed work in the scheduled domain.
The hook calls the existing authenticated standing-context door, injects the
source-owned rules as additional context, and leaves the original tool input
untouched.  Any failure remains visible to Stop telemetry and never blocks.

GENERALIZED (WR-000019 slice S9). The paragraph above describes the ORIGINAL
rail exactly as it shipped, and that rail's logic, receipt schema and tests
are untouched below — proven, single-pack, single-shape, left alone. This
file now also drives a SECOND, more general rail off the declarative
compiled trigger table, ops/config/rule-jit-triggers.v1.json
(ops/rule-jit-compile.py is its only writer): an MCP verb call, a Bash
command family, a file-path write, or a general content fallback, each
mapped to up to ~5 rules. On any PreToolUse call the ORIGINAL rail's exact
shape does not match, this file checks the compiled table instead, and on a
match calls the same standing-context door with the union of every matched
trigger's packs and rule ids, validates the response, and injects a second,
differently-schemad receipt (GENERALIZED_RECEIPT_SCHEMA in
lib/rule_delivery_preuse.py) as additionalContext. The two rails are mutually
exclusive per call (the original shape, when it matches, is handled by the
original code path alone) so the proven rail's behavior cannot be disturbed
by the new one.

NOT DONE HERE, ON PURPOSE: hooks/rule-pack-drift-gate.py's Stop-side keyword
telemetry still only recognizes the original schema's receipt as "loaded"
evidence for scheduled-automation; it does not yet credit a generalized-rail
receipt for the packs it delivers. Wiring that up is a comparison this slice
chose to leave rather than force through the drift gate's byte-pinned
observation fixtures under scope pressure — see the note beside
`load_packs()` in that file.
"""

# doctrine: rule-delivery-load-layers

from __future__ import annotations

import fnmatch
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable


REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from lib.rule_delivery_preuse import (  # noqa:E402
    GENERALIZED_RECEIPT_SCHEMA, PACK, RECEIPT_SCHEMA, TRIGGER_TABLE_RELATIVE,
    canonical, digest, load_trigger_table, merge_trigger_delivery, receipt_id,
    scheduled_rule_ids as _scheduled_rule_ids, valid_local_identity,
    validate_generalized_receipt,
)
from lib.rule_delivery_shadow import (  # noqa:E402
    WINDOW_SOURCE_PATHS, file_sha256, source_sha256,
)


MAP = REPO / "ops/config/rule-enforcement-map.json"
TRIGGERS_PATH = REPO / TRIGGER_TABLE_RELATIVE
FAILURE_CONTEXT = (
    "RULE PACK PREUSE RESELECTION FAILED: selector_unavailable. "
    "The scheduled action was not blocked or rewritten; Stop telemetry must "
    "treat scheduled-automation as not loaded."
)
GENERALIZED_FAILURE_CONTEXT = (
    "RULE JIT TRIGGER DELIVERY FAILED: selector_unavailable. "
    "One or more matched triggers were not delivered; Stop telemetry must "
    "treat their packs as not loaded."
)
PATH_INPUT_KEYS = ("file_path", "path", "notebook_path")


def scheduled_rule_ids() -> list[str]:
    """Expose the current reviewed-map membership for the hook and its gate."""
    return _scheduled_rule_ids(REPO)


def _extract_command(tool_input: object) -> str | None:
    if isinstance(tool_input, dict):
        value = tool_input.get("command")
        if isinstance(value, str):
            return value
    return None


def _extract_paths(tool_input: object) -> list[str]:
    if not isinstance(tool_input, dict):
        return []
    return [tool_input[key] for key in PATH_INPUT_KEYS
            if isinstance(tool_input.get(key), str) and tool_input[key].strip()]


def _serialized_payload(tool_name: str, tool_input: object) -> str:
    try:
        blob = json.dumps(tool_input, sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError):
        blob = str(tool_input)
    return f"{tool_name}\n{blob}"


def _row_matches(tool_name: str, tool_input: object, row: dict) -> bool:
    kind = row.get("kind")
    pattern = row.get("pattern")
    if not isinstance(pattern, str):
        return False
    try:
        if kind == "verb":
            return re.search(pattern, tool_name) is not None
        if kind == "bash_family":
            if tool_name not in {"Bash", "functions.exec"}:
                return False
            command = _extract_command(tool_input)
            return command is not None and re.search(pattern, command, re.I) is not None
        if kind == "path_pattern":
            return any(fnmatch.fnmatch(path, pattern) for path in _extract_paths(tool_input))
        if kind == "content_regex":
            return re.search(pattern, _serialized_payload(tool_name, tool_input),
                             re.I) is not None
    except re.error:
        return False
    return False


def matched_triggers(payload: dict) -> list[dict]:
    """Every compiled trigger row this exact PreToolUse call structurally hits."""
    if payload.get("hook_event_name") != "PreToolUse":
        return []
    tool_name = payload.get("tool_name")
    if not isinstance(tool_name, str) or not tool_name:
        return []
    try:
        rows = load_trigger_table(REPO)
    except Exception:
        return []
    tool_input = payload.get("tool_input")
    return [row for row in rows if _row_matches(tool_name, tool_input, row)]


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
    if not valid_local_identity(identity):
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


# ---------------------------------------------------------------------------
# GENERALIZED RAIL (WR-000019 slice S9) — same shape as the four functions
# above, parameterized over the compiled trigger table's (trigger_ids, packs,
# rule_ids) instead of the fixed PACK/scheduled_rule_ids pair. Kept separate
# rather than folded into the originals so the proven rail above never has to
# change to accommodate a shape it was never asked to handle.


def _generalized_selector_args(packs: list[str], ids: list[str]) -> str:
    return canonical({"packs": packs, "rule_ids": ids}).decode("utf-8")


def _run_generalized_selector(packs: list[str], ids: list[str], runner: Callable) -> dict:
    command = [str(REPO / "run.sh"), "call", "standing-context",
               _generalized_selector_args(packs, ids)]
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


def _validate_generalized_selector(response: dict, packs: list[str], ids: list[str]):
    identity = response.get("identity")
    if not valid_local_identity(identity):
        raise RuntimeError("selector identity is incomplete")
    delivery = response.get("rule_delivery")
    if (not isinstance(delivery, dict)
            or delivery.get("mode") not in {"shadow", "enforced"}
            or sorted(delivery.get("declared_packs") or []) != packs
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
        raise RuntimeError("selector did not return every triggered rule")
    return identity, delivery, [{"id": short, "statement": found[short]} for short in ids]


def _generalized_receipt(payload: dict, response: dict, trigger_ids: list[str],
                         packs: list[str], ids: list[str]) -> dict:
    identity, delivery, rules = _validate_generalized_selector(response, packs, ids)
    client = _client(payload)
    row = {
        "schema": GENERALIZED_RECEIPT_SCHEMA,
        "client": client,
        "session_id": payload["session_id"],
        "turn_id": payload.get("turn_id") if client == "codex" else None,
        "tool_use_id": payload["tool_use_id"],
        "tool_name": payload["tool_name"],
        "tool_input_sha256": digest(payload["tool_input"]),
        "trigger_ids": trigger_ids,
        "packs": packs,
        "triggers_digest": file_sha256(TRIGGERS_PATH),
        "map_digest": file_sha256(MAP),
        "source_digest": source_sha256(REPO),
        "identity": {key: identity[key] for key in (
            "agent_principal_id", "runtime_principal", "sponsoring_human_id")},
        "rule_ids": ids,
        "rules": rules,
        "rule_delivery": {
            "mode": delivery["mode"],
            "declared_packs": packs,
            "packs_not_found": [],
        },
    }
    row["receipt_id"] = receipt_id(row)
    return row


def process(payload: dict, *, runner: Callable = subprocess.run) -> dict | None:
    if _matches(payload):
        # THE ORIGINAL RAIL, untouched: exact shape, exact pack, exact receipt.
        try:
            ids = scheduled_rule_ids()
            response = _run_selector(ids, runner)
            return _context(canonical(_receipt(payload, response, ids)).decode("utf-8"))
        except Exception:
            # Never surface provider/auth/network exception text: it may contain a
            # bearer, URL, or local path.  The fixed category is enough for Stop to
            # preserve the miss and for the operator to reproduce through the door.
            return _context(FAILURE_CONTEXT)

    # THE GENERALIZED RAIL (WR-000019 slice S9). Only reached when the
    # original exact shape did not match, so a background Bash/functions.exec
    # call keeps getting exactly the original behavior above and nothing from
    # this rail layers onto it.
    if not (_nonempty(payload.get("session_id")) and _nonempty(payload.get("tool_use_id"))):
        return None
    rows = matched_triggers(payload)
    if not rows:
        return None
    try:
        trigger_ids, packs, ids = merge_trigger_delivery(rows)
        response = _run_generalized_selector(packs, ids, runner)
        return _context(canonical(
            _generalized_receipt(payload, response, trigger_ids, packs, ids)
        ).decode("utf-8"))
    except Exception:
        return _context(GENERALIZED_FAILURE_CONTEXT)


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
