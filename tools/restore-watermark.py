#!/usr/bin/env python3
"""restore-watermark.py — the EXACT half of a restore rehearsal (V5-F08 item 4).

bin/restore-rehearse.sh has always compared the restored database against LIVE
production and gated on ">= 90% of production's rows". That is the right
truncation alarm, and it is approximate by construction: production keeps
moving after the dump is taken. V5-F08 asks for more — "restore from an
independently controlled copy succeeds with exact watermark/hash" — and the
only thing a restore can be compared EXACTLY against is the artifact itself.

So this tool reads two things and compares them for equality, nothing looser:

  count     the decrypted pg_dump plaintext on stdin -> per-table row counts
            the artifact CARRIES, read from its own COPY blocks. Prints JSON.
            The plaintext is streamed and never written anywhere.
  compare   that artifact watermark vs the restored database's counts (psql
            -At output, "schema.table|rows"). Exit 0 only on exact equality,
            table for table, with no table missing on either side.
  digest    the sha256 of the ENCRYPTED artifact file, as "sha256:<hex>" — the
            bytes that were stored, which is what a producer records.
  receipt   the typed restore-exercise-receipt.v1 that
            mcp-server/src/recovery-matrix.v5.js evaluateRestoreExercise reads.
            Needs --copy-json: who produced the copy, where it is held and the
            digest the producer recorded. Those are facts about the copy that
            this machine cannot derive, so they are supplied, and the evaluator
            decides whether they satisfy the floor.

Nothing here connects to a database, reads a credential or touches a network.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

COPY_RE = re.compile(r'^COPY ((?:"(?:[^"]|"")+"|[A-Za-z_][A-Za-z0-9_$]*))\.((?:"(?:[^"]|"")+"|[A-Za-z_][A-Za-z0-9_$]*)) .*FROM stdin;$')
RECEIPT_KIND = "restore-exercise-receipt.v1"
TARGET_KINDS = ("disposable_branch", "disposable_local_cluster", "staging")
COPY_KEYS = ("copy_id", "custody_domain", "primary_domain", "produced_at", "producer_id",
             "recorded_artifact_digest")


def _ident(token: str) -> str:
    if token.startswith('"'):
        return token[1:-1].replace('""', '"')
    return token


def count_copy_rows(lines) -> dict[str, int]:
    """Row counts per schema.table from a plain-format pg_dump stream.

    A COPY block is the header line, one line per row, then a line that is
    exactly backslash-dot. pg_dump escapes embedded newlines and backslashes
    inside COPY text, so a data row can never be that terminator. A stream that
    ends inside a block is a truncated artifact and raises rather than guessing.
    """
    counts: dict[str, int] = {}
    table = None
    rows = 0
    for raw in lines:
        line = raw.rstrip("\n")
        if table is None:
            m = COPY_RE.match(line)
            if m:
                table = f"{_ident(m.group(1))}.{_ident(m.group(2))}"
                if table in counts:
                    raise ValueError(f"table {table} has two COPY blocks")
                rows = 0
            continue
        if line == "\\.":
            counts[table] = rows
            table = None
        else:
            rows += 1
    if table is not None:
        raise ValueError(f"stream ended inside the COPY block for {table}: truncated artifact")
    if not counts:
        raise ValueError("no COPY blocks found: not a plain-format data dump")
    return counts


def parse_restored_counts(text: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        name, sep, rows = line.rpartition("|")
        if not sep or not name or not rows.isdigit():
            raise ValueError(f"restored counts line {n} is not schema.table|rows")
        if name in out:
            raise ValueError(f"restored counts repeat {name}")
        out[name] = int(rows)
    return out


def compare(artifact: dict[str, int], restored: dict[str, int]) -> list[dict]:
    tables = sorted(set(artifact) | set(restored))
    return [{"table": t, "artifact_rows": artifact.get(t), "restored_rows": restored.get(t)}
            for t in tables if artifact.get(t) != restored.get(t)]


def file_digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def build_receipt(*, copy: dict, target_kind: str, oracle_id: str, observed_digest: str,
                  artifact: dict[str, int], restored: dict[str, int],
                  started_at: str, finished_at: str) -> dict:
    missing = [k for k in COPY_KEYS if k not in copy]
    extra = [k for k in copy if k not in COPY_KEYS]
    if missing or extra:
        raise ValueError(f"--copy-json must hold exactly {', '.join(COPY_KEYS)} (missing {missing}, unknown {extra})")
    if target_kind not in TARGET_KINDS:
        raise ValueError(f"target kind must be one of {TARGET_KINDS}; a production restore is never receipted here")
    return {
        "receipt_kind": RECEIPT_KIND,
        "target_kind": target_kind,
        "copy": {k: copy[k] for k in COPY_KEYS},
        "oracle_id": oracle_id,
        "observed_artifact_digest": observed_digest,
        "artifact_watermark": artifact,
        "restored_watermark": restored,
        "started_at": started_at,
        "finished_at": finished_at,
    }


def _load_json(path: str) -> dict:
    return json.loads(Path(path).read_text())


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("count")
    c = sub.add_parser("compare")
    c.add_argument("--artifact", required=True)
    c.add_argument("--restored", required=True)
    d = sub.add_parser("digest")
    d.add_argument("path")
    r = sub.add_parser("receipt")
    r.add_argument("--copy-json", required=True)
    r.add_argument("--target-kind", required=True)
    r.add_argument("--oracle-id", required=True)
    r.add_argument("--observed-digest", required=True)
    r.add_argument("--artifact", required=True)
    r.add_argument("--restored", required=True)
    r.add_argument("--started-at", required=True)
    r.add_argument("--finished-at", required=True)
    a = p.parse_args(argv)
    try:
        if a.cmd == "count":
            print(json.dumps(count_copy_rows(sys.stdin), sort_keys=True))
            return 0
        if a.cmd == "digest":
            print(file_digest(Path(a.path)))
            return 0
        artifact = _load_json(a.artifact)
        restored = parse_restored_counts(Path(a.restored).read_text())
        if a.cmd == "compare":
            diffs = compare(artifact, restored)
            print(f"WATERMARK tables={len(artifact)} mismatches={len(diffs)}")
            for d_ in diffs[:40]:
                print(f"  MISMATCH {d_['table']}: artifact={d_['artifact_rows']} restored={d_['restored_rows']}")
            return 0 if not diffs else 1
        receipt = build_receipt(copy=_load_json(a.copy_json), target_kind=a.target_kind,
                                oracle_id=a.oracle_id, observed_digest=a.observed_digest,
                                artifact=artifact, restored=restored,
                                started_at=a.started_at, finished_at=a.finished_at)
        print(json.dumps(receipt, sort_keys=True))
        return 0
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"restore-watermark: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
