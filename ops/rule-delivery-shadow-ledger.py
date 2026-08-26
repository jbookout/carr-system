#!/usr/bin/env python3
"""Sanctioned append-only dispositions and epoch starts for shadow evidence."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from lib.rule_delivery_shadow import (  # noqa:E402
    can_start_epoch, current_identity, finding, inspect, make_disposition, make_epoch,
)

DEFAULT_LOG = REPO / "out/rule-delivery-shadow.jsonl"


def read_locked(handle) -> tuple[list[dict], int]:
    handle.seek(0)
    rows: list[dict] = []
    bad = 0
    for line in handle:
        try:
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError
            rows.append(row)
        except ValueError:
            bad += 1
    return rows, bad


def append(path: Path, build) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        rows, bad = read_locked(handle)
        if bad:
            raise RuntimeError(f"refusing append: {bad} unreadable telemetry line(s)")
        row = build(rows)
        handle.seek(0, os.SEEK_END)
        handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
        return row


def routine_dsn() -> str:
    value = os.environ.get("CARR_DB_JOBS_URL", "").strip()
    if not value:
        raise RuntimeError("CARR_DB_JOBS_URL is required to bind an epoch to live policy")
    login = unquote(urlsplit(value).username or "").strip().lower()
    if login in {"carr_writer", "carr_owner", "owner", "writer", "postgres"}:
        raise RuntimeError("refusing owner/writer credential; use the carr_jobs reader")
    return value


def live_identity() -> dict:
    import psycopg

    with psycopg.connect(routine_dsn()) as conn, conn.cursor() as cur:
        cur.execute("begin transaction read only")
        cur.execute("select session_user,current_user")
        role = cur.fetchone()
        if not role or {str(value) for value in role} != {"carr_jobs"}:
            raise RuntimeError(f"not a provisioned carr_jobs identity: {role}")
        cur.execute("""select mode,changed_by,reason,changed_at
                         from ops.rule_delivery_policy where singleton""")
        policy = cur.fetchone()
        identity = current_identity(REPO, policy)
        conn.rollback()
        return identity


def add_disposition(path: Path, args) -> dict:
    def build(rows: list[dict]) -> dict:
        state = inspect(rows)
        if state["errors"]:
            raise RuntimeError("; ".join(state["errors"]))
        observed = state["observations"].get(args.event_id)
        if observed is None:
            raise RuntimeError("event-id is not an immutable observation in this log")
        if not finding(observed[1]):
            raise RuntimeError("event-id is not a miss/error finding")
        if args.event_id in state["dispositions"]:
            raise RuntimeError("event-id already has a disposition")
        return make_disposition(
            args.event_id, args.disposition, owner=args.owner,
            remedy_ref=args.remedy_ref, evidence_ref=args.evidence_ref,
            rollback_ref=args.rollback_ref)
    return append(path, build)


def start_epoch(path: Path, args) -> dict:
    identity = live_identity()

    def build(rows: list[dict]) -> dict:
        allowed, reason = can_start_epoch(rows, identity)
        if not allowed:
            raise RuntimeError(f"epoch refused: {reason}")
        return make_epoch(identity, owner=args.owner, reason=args.reason,
                          remedy_ref=args.remedy_ref, rollback_ref=args.rollback_ref)
    return append(path, build)


def list_findings(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as handle:
        rows, bad = read_locked(handle)
    if bad:
        raise RuntimeError(f"{bad} unreadable telemetry line(s)")
    state = inspect(rows)
    if state["errors"]:
        raise RuntimeError("; ".join(state["errors"]))
    result = []
    for event_id, (_index, row) in state["observations"].items():
        if finding(row):
            disp = state["dispositions"].get(event_id)
            result.append({"event_id": event_id, "ts": row.get("ts"),
                           "session": row.get("session"),
                           "missed_rules": row.get("missed_rules", []),
                           "error": row.get("error"),
                           "disposition": disp[1] if disp else None})
    return result


def fields(parser: argparse.ArgumentParser, *, evidence: bool = False) -> None:
    parser.add_argument("--owner", required=True)
    parser.add_argument("--remedy-ref", required=True)
    parser.add_argument("--rollback-ref", required=True)
    if evidence:
        parser.add_argument("--evidence-ref", required=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list-findings")
    disposition = commands.add_parser("disposition")
    disposition.add_argument("--event-id", required=True)
    disposition.add_argument("--disposition", choices=("explained", "remediated"),
                             required=True)
    fields(disposition, evidence=True)
    epoch = commands.add_parser("start-epoch")
    epoch.add_argument("--reason", required=True)
    fields(epoch)
    args = parser.parse_args()
    try:
        if args.command == "list-findings":
            result = list_findings(args.log)
        elif args.command == "disposition":
            result = add_disposition(args.log, args)
        else:
            result = start_epoch(args.log, args)
        print(json.dumps(result, sort_keys=True, default=str))
        return 0
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"rule-delivery-shadow-ledger: REFUSED — {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
