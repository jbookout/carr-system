#!/usr/bin/env python3
"""Narrow transactional installer for native Claude continuity hooks."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import pathlib
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[1]
SOURCE = REPO / "ops/config/claude-continuity-hooks.json"
MODES = {"disabled", "shadow", "checkpoint", "inject"}
HOOK_BASENAME = "/ops/claude-continuity-hook.py"
HOOK_PATH = REPO / "ops/claude-continuity-hook.py"
MCP_SERVER_NAME = "carr-continuity"
MCP_PROXY = REPO / "mcp-server/continuity-stdio-proxy.mjs"


def settings_path() -> pathlib.Path:
    return pathlib.Path(os.environ.get("CARR_CLAUDE_SETTINGS_FILE",
                                       pathlib.Path.home() / ".claude/settings.json"))


def mode_path() -> pathlib.Path:
    return pathlib.Path(os.environ.get("CARR_CLAUDE_CONTINUITY_MODE_FILE",
                                       pathlib.Path.home() / ".config/carr/claude-continuity-mode.json"))


def claude_config_path() -> pathlib.Path:
    return pathlib.Path(os.environ.get("CARR_CLAUDE_CONFIG_FILE",
                                       pathlib.Path.home() / ".claude.json"))


def _load_json(path: pathlib.Path, missing: dict | None = None) -> dict:
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        if missing is not None:
            return copy.deepcopy(missing)
        raise RuntimeError(f"missing required file: {path}")
    if len(raw) > 2_000_000:
        raise RuntimeError(f"configuration too large: {path}")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid JSON: {path}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"configuration root must be an object: {path}")
    return value


def desired_hooks() -> dict[str, list[dict]]:
    source = _load_json(SOURCE)
    rendered = json.loads(json.dumps(source).replace("{{REPO}}", str(REPO)))
    hooks = rendered.get("hooks")
    if not isinstance(hooks, dict) or set(hooks) != {
            "UserPromptSubmit", "PostToolUse", "PreCompact", "SessionStart", "Stop"}:
        raise RuntimeError("canonical Claude continuity hook set is invalid")
    return hooks


def expected_config_digest() -> str:
    rendered = SOURCE.read_text(encoding="utf-8").replace("{{REPO}}", str(REPO)).encode("utf-8")
    digest = hashlib.sha256()
    digest.update(rendered)
    digest.update(b"\0")
    digest.update(HOOK_PATH.read_bytes())
    digest.update(b"\0")
    digest.update(MCP_PROXY.read_bytes())
    return "sha256:" + digest.hexdigest()


def mode_document(mode: str) -> dict:
    return {"schema_version": 1, "mode": mode,
            "config_digest": expected_config_digest()}


def desired_mcp_server() -> dict:
    return {
        "type": "stdio",
        "command": "/usr/bin/env",
        "args": ["node", str(MCP_PROXY)],
        "env": {"CARR_MCP_CLIENT_PROFILE": "claude-continuity"},
    }


def _commands(entry: object) -> list[str]:
    if not isinstance(entry, dict) or not isinstance(entry.get("hooks"), list):
        return []
    commands: list[str] = []
    for hook in entry["hooks"]:
        if isinstance(hook, dict) and isinstance(hook.get("command"), str):
            commands.append(hook["command"])
    return commands


def _is_continuity(entry: object) -> bool:
    return any(HOOK_BASENAME in command for command in _commands(entry))


def install_document(current: dict) -> dict:
    result = copy.deepcopy(current)
    hooks = result.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise RuntimeError("settings hooks must be an object")
    for event, wanted in desired_hooks().items():
        entries = hooks.setdefault(event, [])
        if not isinstance(entries, list):
            raise RuntimeError(f"settings hook event must be a list: {event}")
        observed = [entry for entry in entries if _is_continuity(entry)]
        if observed and observed != wanted:
            raise RuntimeError(f"noncanonical Claude continuity hook present: {event}")
        if not observed:
            entries.extend(copy.deepcopy(wanted))
    return result


def remove_document(current: dict) -> dict:
    result = copy.deepcopy(current)
    hooks = result.get("hooks")
    if not isinstance(hooks, dict):
        return result
    for event in desired_hooks():
        entries = hooks.get(event)
        if isinstance(entries, list):
            hooks[event] = [entry for entry in entries if not _is_continuity(entry)]
    return result


def install_mcp_document(current: dict) -> dict:
    result = copy.deepcopy(current)
    servers = result.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raise RuntimeError("Claude mcpServers must be an object")
    observed = servers.get(MCP_SERVER_NAME)
    wanted = desired_mcp_server()
    if observed is not None and observed != wanted:
        raise RuntimeError("noncanonical carr-continuity MCP server present")
    servers[MCP_SERVER_NAME] = wanted
    return result


def remove_mcp_document(current: dict) -> dict:
    result = copy.deepcopy(current)
    servers = result.get("mcpServers")
    if not isinstance(servers, dict):
        return result
    observed = servers.get(MCP_SERVER_NAME)
    if observed is not None and observed != desired_mcp_server():
        raise RuntimeError("refusing to remove noncanonical carr-continuity MCP server")
    servers.pop(MCP_SERVER_NAME, None)
    if not servers:
        result.pop("mcpServers", None)
    return result


def _encoded(value: dict) -> bytes:
    return (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def _write_atomic(path: pathlib.Path, data: bytes, failure_name: str) -> None:
    if os.environ.get("CARR_CLAUDE_CONTINUITY_INJECT_FAILURE") == failure_name:
        raise OSError(f"injected {failure_name} failure")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
        pathlib.Path(temp_name).unlink(missing_ok=True)


def _restore(path: pathlib.Path, before: bytes | None) -> None:
    if before is None:
        path.unlink(missing_ok=True)
    else:
        _write_atomic(path, before, "never")


def apply_transaction(settings: dict, mcp_config: dict, mode: str | None, remove: bool) -> None:
    target_settings, target_mode, target_mcp = settings_path(), mode_path(), claude_config_path()
    before_settings = target_settings.read_bytes() if target_settings.exists() else None
    before_mode = target_mode.read_bytes() if target_mode.exists() else None
    before_mcp = target_mcp.read_bytes() if target_mcp.exists() else None
    try:
        _write_atomic(target_settings, _encoded(settings), "settings")
        if remove:
            if os.environ.get("CARR_CLAUDE_CONTINUITY_INJECT_FAILURE") == "mode":
                raise OSError("injected mode failure")
            target_mode.unlink(missing_ok=True)
        else:
            assert mode is not None
            _write_atomic(target_mode, _encoded(mode_document(mode)), "mode")
        _write_atomic(target_mcp, _encoded(mcp_config), "mcp")
        # Parse both resources after the transaction before declaring success.
        _load_json(target_settings)
        if not remove:
            assert mode is not None
            parsed_mode = _load_json(target_mode)
            if parsed_mode != mode_document(mode):
                raise RuntimeError("mode verification failed")
        if _load_json(target_mcp) != mcp_config:
            raise RuntimeError("MCP configuration verification failed")
    except Exception:
        _restore(target_settings, before_settings)
        _restore(target_mode, before_mode)
        _restore(target_mcp, before_mcp)
        raise


def verify() -> tuple[bool, str]:
    current = _load_json(settings_path(), missing={})
    hooks = current.get("hooks", {})
    for event, wanted in desired_hooks().items():
        entries = hooks.get(event, []) if isinstance(hooks, dict) else []
        observed = [entry for entry in entries if _is_continuity(entry)] if isinstance(entries, list) else []
        if observed != wanted:
            return False, f"{event} does not contain exactly the canonical continuity hook"
    mode = _load_json(mode_path())
    mode_name = mode.get("mode")
    if mode_name not in MODES or mode != mode_document(mode_name):
        return False, "continuity mode is invalid"
    config = _load_json(claude_config_path())
    if config.get("mcpServers", {}).get(MCP_SERVER_NAME) != desired_mcp_server():
        return False, "carr-continuity MCP server is absent or noncanonical"
    return True, f"all five Claude continuity hooks and dedicated MCP server match; mode={mode['mode']}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("install", "remove", "verify"))
    parser.add_argument("--mode", choices=sorted(MODES), default="shadow")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        if args.action == "verify":
            ok, message = verify()
            print(message)
            return 0 if ok else 1
        current = _load_json(settings_path(), missing={})
        updated = remove_document(current) if args.action == "remove" else install_document(current)
        mcp_current = _load_json(claude_config_path(), missing={})
        mcp_updated = (remove_mcp_document(mcp_current) if args.action == "remove"
                       else install_mcp_document(mcp_current))
        if not args.apply:
            print(f"DRY RUN: would {args.action} Claude continuity hooks; settings and mode unchanged")
            return 0
        apply_transaction(updated, mcp_updated, None if args.action == "remove" else args.mode,
                          args.action == "remove")
        print(f"{args.action.upper()} OK: unrelated Claude settings and MCP servers preserved")
        return 0
    except (OSError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
