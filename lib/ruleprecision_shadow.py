"""Opt-in local observation; no rule text fetches or hook context changes."""
from __future__ import annotations

from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any

from lib import rule_delivery_precision

SCHEMA = "ruleprecision-shadow/v1"
ID = re.compile(r"^[0-9a-f]{8}$")
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
SELECTOR_KEYS = {"refine", "add_actions", "refine_ids", "add_ids", "allow_ids"}


def _canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True).encode("utf-8")


def _hash(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _ids(value) -> bool:
    return (isinstance(value, list) and all(isinstance(item, str) and ID.fullmatch(item)
                                          for item in value)
            and len(value) == len(set(value)))


def _load(path: Path):
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
        if os.fstat(stream.fileno()).st_size > 4_000_000:
            raise ValueError("oversize")
        return json.load(stream)


def _config(path: Path) -> dict[str, Any]:
    value = _load(path)
    if (not isinstance(value, dict) or value.get("schema") != "ruleprecision/v1"
            or not isinstance(value.get("candidate"), str)
            or not re.fullmatch(r"[A-Za-z0-9._-]{1,100}", value["candidate"])
            or not _ids(value.get("active_ids")) or not _ids(value.get("boot_ids"))
            or not set(value["boot_ids"]).issubset(value["active_ids"])
            or not all(isinstance(value.get(key), str) and DIGEST.fullmatch(value[key])
                       for key in ("corpus_digest", "boot_digest"))):
        raise ValueError("invalid config")
    selector = value.get("selector")
    if not isinstance(selector, dict) or set(selector) - SELECTOR_KEYS:
        raise ValueError("invalid selector")
    for key, item in selector.items():
        if key in {"refine", "add_actions"}:
            if not isinstance(item, bool):
                raise ValueError("invalid switch")
        elif not _ids(item) or not set(item).issubset(value["active_ids"]):
            raise ValueError("invalid selector ids")
    return value


def selector_snapshot(repo: Path) -> tuple[dict[str, Any], str]:
    """Return the validated selection configuration and its source-bound digest."""
    path = Path(os.environ.get("CARR_RULEPRECISION_CONFIG",
                               str(repo / "ops/config/ruleprecision.v1.json")))
    config = _config(path)
    sources = {"lib/rule_delivery_precision.py": _hash(Path(rule_delivery_precision.__file__).read_bytes()),
               "lib/rule_routes.py": _hash(Path(rule_delivery_precision.rule_routes.__file__).read_bytes())}
    digest = _hash(_canonical({"schema": "ruleprecision-selector/v1",
                              "config": config, "sources": sources}))
    return config, digest


def _receipt(output) -> tuple[list[str], list[str], list[str], bool]:
    """Full text, pointer, and declaration have different availability meaning."""
    try:
        text = (output.get("hookSpecificOutput") or {}).get("additionalContext")
        if not isinstance(text, str):
            return [], [], [], False
        value = json.loads(text)
        if not isinstance(value, dict):
            return [], [], [], False
    except (AttributeError, TypeError, ValueError):
        return [], [], [], False
    full, pointers, declared = set(), set(), set()
    for item in value.get("rules", []) if isinstance(value.get("rules"), list) else []:
        if (isinstance(item, dict) and isinstance(item.get("id"), str)
                and ID.fullmatch(item["id"]) and isinstance(item.get("statement"), str)
                and item["statement"].strip()):
            full.add(item["id"])
    for item in value.get("overflow", []) if isinstance(value.get("overflow"), list) else []:
        rid = item.get("id") if isinstance(item, dict) else item
        if isinstance(rid, str) and ID.fullmatch(rid):
            pointers.add(rid)
    for rid in value.get("rule_ids", []) if isinstance(value.get("rule_ids"), list) else []:
        if isinstance(rid, str) and ID.fullmatch(rid):
            declared.add(rid)
    return sorted(full), sorted(pointers - full), sorted(declared), True


def _locked(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    stream = os.fdopen(descriptor, "a+")
    try:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
    except BaseException:
        stream.close()
        raise
    return stream


def _append(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_APPEND |
                         getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(descriptor, "ab") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        stream.write(_canonical(row) + b"\n")
        stream.flush()


def _save(path: Path, value: dict) -> None:
    with tempfile.NamedTemporaryFile(mode="wb", dir=path.parent,
                                     prefix=path.name + ".", delete=False) as stream:
        stream.write(_canonical(value) + b"\n")
        stream.flush()
        os.fsync(stream.fileno())
        temporary = stream.name
    os.replace(temporary, path)


def _record(log: Path, state_path: Path, row: dict) -> None:
    if row["session_hash"] is None:
        row.update(proposed_new_ids=[], proposal_dedupe_available=False)
        _append(log, row)
        return
    with _locked(state_path.with_name(state_path.name + ".lock")):
        try:
            state = _load(state_path)
        except FileNotFoundError:
            state = {"schema": "ruleprecision-proposals/v1", "sessions": {}}
        if (not isinstance(state, dict) or state.get("schema") != "ruleprecision-proposals/v1"
                or not isinstance(state.get("sessions"), dict)):
            raise ValueError("invalid proposal state")
        generation = row["selector_digest"]
        key = row["session_hash"] + ":" + generation
        seen = state["sessions"].get(key, [])
        if not _ids(seen):
            raise ValueError("invalid proposal ids")
        row.update(proposed_new_ids=sorted(set(row["candidate_ids"]) - set(seen)),
                   proposal_dedupe_available=True)
        _append(log, row)
        state["sessions"][key] = sorted(set(seen) | set(row["candidate_ids"]))
        _save(state_path, state)


def observe(repo: Path, payload: dict, output) -> None:
    """Observe today's returned context, without changing it or the input."""
    if os.environ.get("CARR_RULEPRECISION_SHADOW") != "1":
        return
    directory = repo / "out/orch/ruleprecision"
    log = Path(os.environ.get("CARR_RULEPRECISION_LOG", str(directory / "shadow.jsonl")))
    state_path = Path(os.environ.get("CARR_RULEPRECISION_STATE", str(directory / "proposal-state.json")))
    session = payload.get("session_id")
    session_hash = _hash(session.encode("utf-8")) if isinstance(session, str) and session else None
    row: dict[str, Any] = {"schema": SCHEMA, "ts": datetime.now(timezone.utc).isoformat(),
                          "input_sha256": _hash(_canonical(payload)), "session_hash": session_hash,
                          "proposal_only": True, "status": "ok", "error": None}
    try:
        config, digest = selector_snapshot(repo)
    except (OSError, TypeError, ValueError):
        row.update(status="config_invalid", error="config_invalid")
        _append(log, row)
        return
    full, pointers, declared, known = _receipt(output)
    selector = dict(config["selector"], active_ids=config["active_ids"])
    selected = rule_delivery_precision.select(repo, payload, full, config["boot_ids"], selector)
    row.update(candidate=config["candidate"], selector_version=rule_delivery_precision.VERSION,
               selector_digest=digest,
               active_count=len(config["active_ids"]), active_ids_hash=_hash(_canonical(config["active_ids"])),
               corpus_digest=config["corpus_digest"], boot_count=len(config["boot_ids"]),
               boot_ids_hash=_hash(_canonical(config["boot_ids"])), boot_digest=config["boot_digest"],
               boot_basis="configured_snapshot", today_receipt_known=known,
               today_full_ids=full, today_pointer_ids=pointers, today_declared_ids=declared,
               candidate_ids=selected)
    try:
        _record(log, state_path, row)
    except (OSError, TypeError, ValueError):
        row.update(status="observation_state_failed", error="observation_state_failed", proposed_new_ids=[],
                   proposal_dedupe_available=False)
        _append(log, row)
