#!/usr/bin/env python3
"""Fixtures for ops/migration-shadow.py.

The shadow run builds a throwaway PostgreSQL 18 cluster from the BASE branch's
db/schema.sql (production's structure and ledger), applies every pending
migration on top, dumps the result with bin/schema-snapshot.sh and compares it
with the committed db/schema.sql. These cases pin the comparison: it ignores
only the time each migration happened to apply, and it never passes when the
structure, the ledger membership or a checksum differs. The database path
itself runs in the CI migration class, where PostgreSQL 18 is installed.

Run: .venv/bin/python ops/migration-shadow-selftest.py
"""
import importlib.util
import pathlib
import subprocess
import sys
import tempfile
from typing import Any

REPO = pathlib.Path(__file__).resolve().parent.parent
TOOL = REPO / "ops" / "migration-shadow.py"

spec = importlib.util.spec_from_file_location("shadow", TOOL)
assert spec is not None and spec.loader is not None, f"cannot load {TOOL}"
shadow: Any = importlib.util.module_from_spec(spec)
sys.modules["shadow"] = shadow
spec.loader.exec_module(shadow)

passed: int = 0
failures: list[str] = []


def check(name: str, cond: object, detail: str = "") -> None:
    global passed
    if cond:
        passed += 1
        print(f"  ok    {name}")
    else:
        failures.append(name)
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))


def snapshot(tables: str, rows: list[tuple[str, str, str]], seq: int = 7) -> str:
    body = "".join(f"{f}\t{s}\t{t}\n" for f, s, t in rows)
    return (tables
            + "select pg_catalog.setval('ops.work_request_ref_seq', " + str(seq) + ", true);\n"
            + "COPY public.schema_migrations (filename, sha256, applied_at) FROM stdin;\n"
            + body + "\\.\n\n-- CARR GRANTS\n")


T = "CREATE TABLE public.deal (id integer NOT NULL);\n"
R1 = [("0001_a.sql", "aa", "2026-09-01 10:00:00+00"), ("0002_b.sql", "bb", "2026-09-02 10:00:00+00")]

print("comparison")
check("identical snapshots match", shadow.differences(snapshot(T, R1), snapshot(T, R1)) == [])
later = [(f, s, "2026-10-05 12:34:56.789+00") for f, s, _ in R1]
check("only applied_at differs: match (apply time is not structure)",
      shadow.differences(snapshot(T, R1), snapshot(T, later)) == [])
check("ledger rows in a different order still match",
      shadow.differences(snapshot(T, R1), snapshot(T, list(reversed(R1)))) == [])
check("the operational work-request sequence value is ignored",
      shadow.differences(snapshot(T, R1, seq=7), snapshot(T, R1, seq=99)) == [])

d = shadow.differences(snapshot(T, R1), snapshot(T, R1[:1]))
check("a migration missing from the committed ledger is a difference",
      d and any("0002_b.sql" in line for line in d), repr(d))
d = shadow.differences(snapshot(T, R1), snapshot(T, [R1[0], ("0002_b.sql", "zz", R1[1][2])]))
check("a checksum difference is a difference", d and any("0002_b.sql" in line for line in d), repr(d))
d = shadow.differences(snapshot(T, R1),
                       snapshot("CREATE TABLE public.deal (id integer NOT NULL, region text);\n", R1))
check("a structural difference is a difference and is shown as a diff",
      d and any("region" in line for line in d), repr(d))
check("a snapshot with no ledger is never a match",
      shadow.differences(snapshot(T, R1), T) != [])

print("applied set")
check("newly applied migrations are the after-ledger minus the before-ledger, in order",
      shadow.newly_applied(snapshot(T, R1[:1]), snapshot(T, R1)) == ["0002_b.sql"])

print("refusal")
with tempfile.TemporaryDirectory() as empty:
    r = subprocess.run([sys.executable, str(TOOL), "--pg-bin-dir", empty, "--base", "HEAD"],
                       cwd=REPO, capture_output=True, text=True)
    check("without PostgreSQL 18 binaries the run is unavailable (exit 3), not a pass",
          r.returncode == 3, r.stdout + r.stderr)
    check("the refusal says which major it needs", "18" in r.stdout + r.stderr, r.stdout + r.stderr)

print(f"\n{passed} passed, {len(failures)} failed")
sys.exit(1 if failures else 0)
