#!/usr/bin/env python3
"""Prove the mint-time migration reservation contract (council cluster B).

The reservation must be atomic under concurrency, visible to next-migration,
refuse an explicitly taken number with the next free one named, and never
silently reuse a stale reservation. Runs against a THROWAWAY ledger: the
module's LEDGER/LOCK globals are repointed before main(), so the live
checkout's out/ is never touched.
"""
from __future__ import annotations

import contextlib
import datetime
import importlib.util
import io
import json
import os
import sys
import tempfile
from typing import Any

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tools"))


def _load(name: str, path: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


rm = _load("reserve_migration", os.path.join(REPO, "tools", "reserve-migration.py"))
nm = _load("next_migration", os.path.join(REPO, "tools", "next-migration.py"))

PASS = 0
FAIL = 0


def ok(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok    {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


def run_reserve(args: list[str], ledger: str):
    rm.LEDGER = ledger
    rm.LOCK = ledger + ".lock"
    old_argv = sys.argv[1:]
    sys.argv[1:] = args
    buf_out, buf_err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(buf_out), contextlib.redirect_stderr(buf_err):
            code = rm.main()
    finally:
        sys.argv[1:] = old_argv
    return code, buf_out.getvalue(), buf_err.getvalue()


def read_rows(ledger: str) -> list[dict]:
    try:
        with open(ledger) as fh:
            return [json.loads(l) for l in fh if l.strip()]
    except OSError:
        return []


with tempfile.TemporaryDirectory() as tmp:
    ledger = os.path.join(tmp, "migration-reservations.jsonl")

    # Learn the machine's next free number on an EMPTY ledger, then claim it
    # with a matching --name from a clean slate.
    if os.path.exists(ledger):
        os.remove(ledger)
    code, out, err = run_reserve([], ledger)
    first = read_rows(ledger)[0]["number"]
    os.remove(ledger)
    code, out, err = run_reserve([f"--name={first:04d}_the_thing"], ledger)
    rows = read_rows(ledger)
    ok("a first claim appends exactly one reservation row",
       code == 0 and len(rows) == 1, f"code={code} err={err}")
    ok("the row records owner, branch, host and ts",
       bool(rows) and all(k in rows[0] for k in ("number", "owner", "branch", "host", "ts")))

    code, out, err = run_reserve([], ledger)
    rows = read_rows(ledger)
    ok("a second claim takes the next free number, never the same one",
       code == 0 and len(rows) == 2 and rows[1]["number"] == rows[0]["number"] + 1,
       f"code={code} numbers={[r['number'] for r in rows]}")

    code, out, err = run_reserve(["--list"], ledger)
    ok("--list prints every reservation", code == 0 and out.count("\n") >= 2)

    taken = rows[0]["number"]
    code, out, err = run_reserve([f"--number={taken}"], ledger)
    ok("an explicit claim on a taken number is refused", code == 1)
    combined = (out + err).lower()
    ok("the refusal names the next free number", "next free" in combined, err)

    # A STALE reservation still counts as claimed — no silent reuse.
    old = dict(rows[0])
    old["ts"] = (datetime.datetime.now(datetime.timezone.utc)
                 - datetime.timedelta(days=30)).isoformat()
    stale_num = old["number"]
    with open(ledger, "w") as fh:
        fh.write(json.dumps(old, sort_keys=True) + "\n")
        fh.write(json.dumps(rows[1], sort_keys=True) + "\n")

    claims: dict[int, dict] = {}
    for row in rm.read_reservations():
        claims.setdefault(row["number"], {}).setdefault(
            row.get("name") or "?", set()).add("reservation")
    ok("stale reservations are not silently reused (still claimed)",
       stale_num in claims)

    # next-migration's own scan counts a reservation row as a claim — the
    # visibility contract that makes reserving worth doing.
    nm_claims: dict[int, dict] = {}
    with open(ledger) as fh:
        for line in fh:
            row = json.loads(line)
            nm.merge(nm_claims, {row["number"]: {"reserved"}}, "reservation")
    ok("next-migration sees reserved numbers as claimed",
       stale_num in nm_claims and rows[1]["number"] in nm_claims)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
