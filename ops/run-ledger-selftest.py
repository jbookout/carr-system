#!/usr/bin/env python3
"""
run-ledger-selftest.py — the acceptance test for the Program 3 COLLECTOR,
written before the collector itself (rule e65efc68).

WHAT THIS GATES, and why it is a separate file from program3-trace-gate.py.
That gate proves the SUBSTRATE: given a journey, one query explains it. It seeds
its own journey and rolls it back, so it passes today against a database in which
no real journey has ever been recorded. 0115 built the tables and nothing writes
to them. This file gates the other half — the collector that turns real work into
rows — and the properties it asserts are the ones that decide whether the ledger
can be trusted rather than whether it exists.

THE FIVE PROPERTIES.

  1. THE LEDGER MAY NEVER FAIL THE THING IT OBSERVES. A collector that breaks the
     nightly chain when the database is unreachable has made reliability worse in
     the name of measuring it. Recording is a local append and cannot touch the
     network; every failure path exits 0.

  2. ENVIRONMENT IS NEVER INFERRED. The read contract says so in as many words
     ("Environment is required on each operational object and never inferred"),
     and release.js already set the idiom for the unlabelled case: "an unlabelled
     deployment is never assumed to be production". With no environment the
     collector writes NOTHING and says so loudly. It does not guess, and because
     ops.run.environment is NOT NULL under a four-value check, there is no
     'unknown' row to fall back to — refusing is the only honest option.

  3. ONE CHAIN RUN IS ONE CORRELATION ID. The whole gate rests on this. Every
     step of one nightly chain must carry the same correlation id, and a second
     chain must not collide with the first.

  4. A FAILURE NAMES ITS CLASS, BEFORE THE DATABASE ASKS. ops.run has a check
     constraint refusing a failed run with no failure_class. If the collector
     relies on that constraint it converts a nightly failure into a SECOND,
     silent failure at flush time — the row is simply rejected and the failure it
     was reporting disappears. So the collector supplies a class itself.

  5. DELIVERY IS AT-LEAST-ONCE, AND A CRASH LOSES NOTHING. The spool is drained
     only after the rows are committed, so a flush that dies mid-way leaves the
     spool intact and the next flush retries. A chain that dies before its own
     flush leaves its rows on disk for the NEXT run to deliver, which is why the
     chain flushes at the start as well as at the end.

RUNNING IT. The spool half needs no database and runs anywhere, including CI:

    .venv/bin/python ops/run-ledger-selftest.py

The delivery half needs a database and runs when one is offered, against the
throwaway CI Postgres or the isolated staging project:

    CARR_CI_DATABASE_URL=... .venv/bin/python ops/run-ledger-selftest.py
    .venv/bin/python tools/db-tap.py --project staging run ops/run-ledger-selftest.py

RESIDUE, AND WHY THIS FILE CANNOT SIMPLY ROLL BACK LIKE THE TRACE GATE. That gate
seeds through its own transaction and aborts it. This one cannot: the whole point
is to exercise the REAL delivery path, and flush() commits — an at-least-once
collector that did not commit would not be the thing under test. So the delivery
half cleans up after itself explicitly, deleting exactly the rows carrying its own
generated correlation id and nothing else. That cleanup is not tidiness. A
synthetic run left in ops.run is a fake failure sitting in the ledger a human will
later read as real, which is the precise way an observability store rots.
"""

import json
import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LEDGER = REPO / "ops" / "run-ledger.py"
PY = sys.executable

passed = 0
failed = 0


def ok(label):
    global passed
    passed += 1
    print(f"  ok    {label}")


def bad(label, why):
    global failed
    failed += 1
    print(f"  FAIL  {label} — {why}")


def check(label, cond, why=""):
    ok(label) if cond else bad(label, why or "assertion false")


def run_ledger(args, env=None, expect_exit=None):
    """Invoke the collector. Returns (exit_code, stdout, stderr)."""
    e = dict(os.environ)
    # Never let the caller's real spool or DSN leak into a test.
    e.pop("DATABASE_URL", None)
    e.pop("CARR_ENV", None)
    e.pop("CARR_CORRELATION_ID", None)
    if env:
        e.update(env)
    p = subprocess.run([PY, str(LEDGER)] + args, capture_output=True, text=True, env=e)
    return p.returncode, p.stdout, p.stderr


def spool_rows(path):
    if not Path(path).exists():
        return []
    out = []
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


# ══════════════════════════════════════════════════════════════════════════════
# 1. THE LEDGER MAY NEVER FAIL THE THING IT OBSERVES
# ══════════════════════════════════════════════════════════════════════════════
print("\n1. recording never fails the observed job")

with tempfile.TemporaryDirectory() as td:
    spool = Path(td) / "spool.jsonl"
    rc, out, err = run_ledger(
        ["record", "--service", "nightly-record-layer", "--run-key", "nightly.exports",
         "--state", "succeeded", "--exit-code", "0", "--started", "2026-08-14T01:00:00Z",
         "--ended", "2026-08-14T01:00:05Z", "--source-ref", "bin/nightly.sh"],
        env={"CARR_ENV": "production", "CARR_RUN_LEDGER_SPOOL": str(spool)})
    check("a normal record exits 0", rc == 0, f"exit {rc}: {err}")
    check("a normal record writes one spool row", len(spool_rows(spool)) == 1,
          f"{len(spool_rows(spool))} rows")

    # The network must not be reachable from `record` at all. A bogus DSN is the
    # cheapest proof: if record touched the database this would hang or fail.
    rc, out, err = run_ledger(
        ["record", "--service", "nightly-record-layer", "--run-key", "nightly.backup",
         "--state", "failed", "--exit-code", "3", "--started", "2026-08-14T01:00:06Z",
         "--ended", "2026-08-14T01:00:09Z", "--source-ref", "bin/nightly.sh"],
        env={"CARR_ENV": "production", "CARR_RUN_LEDGER_SPOOL": str(spool),
             "DATABASE_URL": "postgresql://nobody:nobody@127.0.0.1:1/nope"})  # ci-secret-scan: allow — port 1, nobody/nobody: a deliberately dead DSN, not a credential
    check("record exits 0 with an unreachable database", rc == 0, f"exit {rc}: {err}")
    check("record still spooled the row", len(spool_rows(spool)) == 2,
          f"{len(spool_rows(spool))} rows")

    # An unwritable spool is the last thing that could take the chain down.
    rc, out, err = run_ledger(
        ["record", "--service", "nightly-record-layer", "--run-key", "nightly.x",
         "--state", "succeeded", "--exit-code", "0", "--started", "2026-08-14T01:00:00Z",
         "--ended", "2026-08-14T01:00:01Z", "--source-ref", "bin/nightly.sh"],
        env={"CARR_ENV": "production",
             "CARR_RUN_LEDGER_SPOOL": "/proc/nonexistent-dir/spool.jsonl"})
    check("record exits 0 when the spool itself cannot be written", rc == 0, f"exit {rc}")

    # And flush, which DOES touch the network, must not fail its caller either.
    rc, out, err = run_ledger(
        ["flush"],
        env={"CARR_ENV": "production", "CARR_RUN_LEDGER_SPOOL": str(spool),
             "DATABASE_URL": "postgresql://nobody:nobody@127.0.0.1:1/nope"})  # ci-secret-scan: allow — port 1, nobody/nobody: a deliberately dead DSN, not a credential
    check("flush exits 0 with an unreachable database", rc == 0, f"exit {rc}: {err}")
    check("a failed flush leaves the spool intact for the next run",
          len(spool_rows(spool)) == 2, f"{len(spool_rows(spool))} rows")

# ══════════════════════════════════════════════════════════════════════════════
# 2. ENVIRONMENT IS NEVER INFERRED
# ══════════════════════════════════════════════════════════════════════════════
print("\n2. an unlabelled run is never assumed to be production")

with tempfile.TemporaryDirectory() as td:
    spool = Path(td) / "spool.jsonl"
    rc, out, err = run_ledger(
        ["record", "--service", "nightly-record-layer", "--run-key", "nightly.exports",
         "--state", "succeeded", "--exit-code", "0", "--started", "2026-08-14T01:00:00Z",
         "--ended", "2026-08-14T01:00:05Z", "--source-ref", "bin/nightly.sh"],
        env={"CARR_RUN_LEDGER_SPOOL": str(spool)})   # no CARR_ENV
    check("a run with no environment exits 0 (fail-soft)", rc == 0, f"exit {rc}")
    check("a run with no environment writes NO row", len(spool_rows(spool)) == 0,
          f"{len(spool_rows(spool))} rows — it guessed an environment")
    check("and it says why, loudly",
          "environment" in (out + err).lower(), "no explanation on stdout/stderr")

    rc, out, err = run_ledger(
        ["record", "--service", "nightly-record-layer", "--run-key", "k",
         "--state", "succeeded", "--exit-code", "0", "--started", "2026-08-14T01:00:00Z",
         "--ended", "2026-08-14T01:00:05Z", "--source-ref", "bin/nightly.sh"],
        env={"CARR_ENV": "wishful", "CARR_RUN_LEDGER_SPOOL": str(spool)})
    check("an environment outside the contract's four values writes no row",
          len(spool_rows(spool)) == 0, "a non-contract environment was accepted")

# ══════════════════════════════════════════════════════════════════════════════
# 3. ONE CHAIN RUN IS ONE CORRELATION ID
# ══════════════════════════════════════════════════════════════════════════════
print("\n3. one chain run is one correlation id")

with tempfile.TemporaryDirectory() as td:
    spool = Path(td) / "spool.jsonl"
    corr = str(uuid.uuid4())
    base = {"CARR_ENV": "production", "CARR_RUN_LEDGER_SPOOL": str(spool),
            "CARR_CORRELATION_ID": corr}
    for key in ("nightly.exports", "nightly.graph", "nightly.backup"):
        run_ledger(
            ["record", "--service", "nightly-record-layer", "--run-key", key,
             "--state", "succeeded", "--exit-code", "0",
             "--started", "2026-08-14T01:00:00Z", "--ended", "2026-08-14T01:00:05Z",
             "--source-ref", "bin/nightly.sh"], env=base)
    rows = spool_rows(spool)
    check("every step of one chain spooled a row", len(rows) == 3, f"{len(rows)} rows")
    check("every step carries the SAME correlation id",
          len({r["correlation_id"] for r in rows}) == 1,
          f'{len({r["correlation_id"] for r in rows})} distinct ids')
    check("and it is the id the chain was given",
          rows and rows[0]["correlation_id"] == corr, "the chain minted its own")

    # A second chain must not join the first one's story.
    corr2 = str(uuid.uuid4())
    run_ledger(["record", "--service", "nightly-record-layer", "--run-key", "nightly.exports",
                "--state", "succeeded", "--exit-code", "0",
                "--started", "2026-08-14T02:00:00Z", "--ended", "2026-08-14T02:00:05Z",
                "--source-ref", "bin/nightly.sh"],
               env={**base, "CARR_CORRELATION_ID": corr2})
    check("a second chain gets its own correlation id",
          len({r["correlation_id"] for r in spool_rows(spool)}) == 2,
          "the two chains blended into one")

    # An unthreaded caller still produces a pointable row — a chain of one, never
    # a chain of none. That is 0115's own stated rule for the column default.
    with tempfile.TemporaryDirectory() as td2:
        spool2 = Path(td2) / "s.jsonl"
        run_ledger(["record", "--service", "nightly-record-layer", "--run-key", "solo",
                    "--state", "succeeded", "--exit-code", "0",
                    "--started", "2026-08-14T01:00:00Z", "--ended", "2026-08-14T01:00:05Z",
                    "--source-ref", "bin/nightly.sh"],
                   env={"CARR_ENV": "production", "CARR_RUN_LEDGER_SPOOL": str(spool2)})
        r = spool_rows(spool2)
        check("an unthreaded run still gets a correlation id of its own",
              len(r) == 1 and r[0].get("correlation_id"), "no id was minted")

# ══════════════════════════════════════════════════════════════════════════════
# 4. A FAILURE NAMES ITS CLASS BEFORE THE DATABASE HAS TO ASK
# ══════════════════════════════════════════════════════════════════════════════
print("\n4. a failure names its class before the database has to ask")

with tempfile.TemporaryDirectory() as td:
    spool = Path(td) / "spool.jsonl"
    env = {"CARR_ENV": "production", "CARR_RUN_LEDGER_SPOOL": str(spool)}
    run_ledger(["record", "--service", "nightly-record-layer", "--run-key", "nightly.exports",
                "--state", "failed", "--exit-code", "3",
                "--started", "2026-08-14T01:00:00Z", "--ended", "2026-08-14T01:00:05Z",
                "--source-ref", "bin/nightly.sh"], env=env)
    rows = spool_rows(spool)
    check("a failed run with no class given still carries one",
          rows and rows[0].get("failure_class"),
          "the row would be refused by ops.run at flush time and the failure lost")
    check("a failed run keeps its exit code", rows and rows[0].get("exit_code") == 3,
          "exit code dropped")

    # SKIP is exit 78 in the nightly chain (EX_CONFIG) and is explicitly NOT a
    # failed night — it must not be recorded as one, and needs no class.
    run_ledger(["record", "--service", "nightly-record-layer", "--run-key", "nightly.calendar",
                "--state", "skipped", "--exit-code", "78",
                "--started", "2026-08-14T01:00:06Z", "--ended", "2026-08-14T01:00:07Z",
                "--source-ref", "bin/nightly.sh"], env=env)
    skipped = [r for r in spool_rows(spool) if r["state"] == "skipped"]
    check("a skipped step records as skipped, not failed", len(skipped) == 1,
          "exit 78 was recorded as a failure")
    check("and a skipped step needs no failure class",
          skipped and not skipped[0].get("failure_class"),
          "a skip was given a failure class it does not have")

    # An unknown state is a bug in the caller, not something to coerce.
    rc, out, err = run_ledger(
        ["record", "--service", "nightly-record-layer", "--run-key", "k",
         "--state", "banana", "--exit-code", "0",
         "--started", "2026-08-14T01:00:00Z", "--ended", "2026-08-14T01:00:05Z",
         "--source-ref", "bin/nightly.sh"], env=env)
    check("a state outside the contract's ten values is refused, not coerced",
          len([r for r in spool_rows(spool) if r["state"] == "banana"]) == 0,
          "an off-contract state reached the spool")
    check("and refusing it still exits 0", rc == 0, f"exit {rc}")

# ══════════════════════════════════════════════════════════════════════════════
# 5. DELIVERY IS AT-LEAST-ONCE (needs a database)
# ══════════════════════════════════════════════════════════════════════════════
print("\n5. delivery is at-least-once and a crash loses nothing")

DSN = os.environ.get("DATABASE_URL") or os.environ.get("CARR_CI_DATABASE_URL") or ""
if not DSN:
    print("  SKIP  no DATABASE_URL / CARR_CI_DATABASE_URL — the delivery half needs one.")
    print("        Spool behaviour above is fully covered without a database.")
else:
    try:
        import psycopg
    except ImportError:
        print("  SKIP  psycopg not installed")
        psycopg = None

    # ops/ci.sh runs this file in its `gates` class, which comes BEFORE the
    # `migration` class in CLASS_ORDER — so on a fresh CI database the DSN is
    # real but 0115 has not been applied yet and ops.run does not exist. That is
    # a missing precondition, not a failing collector, and reporting it as a
    # failure would make CI red for a reason nobody could act on. Checked
    # explicitly rather than caught from the first query, so a genuinely broken
    # ops.run still fails loudly instead of hiding behind this skip.
    if psycopg:
        try:
            with psycopg.connect(DSN) as c, c.cursor() as cur:
                cur.execute("select to_regclass('ops.run') is not null")
                if not cur.fetchone()[0]:
                    print("  SKIP  this database has no ops.run — 0115 is not applied here.")
                    print("        (ci.sh runs `gates` before `migration`, so a fresh CI")
                    print("        database reaches this file before the table exists.)")
                    psycopg = None
        except Exception as exc:
            print(f"  SKIP  could not reach the database ({exc})")
            psycopg = None

    if psycopg:
        with tempfile.TemporaryDirectory() as td:
            spool = Path(td) / "spool.jsonl"
            corr = str(uuid.uuid4())
            env = {"CARR_ENV": "staging", "CARR_RUN_LEDGER_SPOOL": str(spool),
                   "CARR_CORRELATION_ID": corr, "DATABASE_URL": DSN}
            for key, state, code in (("selftest.a", "succeeded", 0),
                                     ("selftest.b", "failed", 3),
                                     ("selftest.c", "skipped", 78)):
                run_ledger(["record", "--service", "nightly-record-layer",
                            "--run-key", key, "--state", state, "--exit-code", str(code),
                            "--started", "2026-08-14T01:00:00Z",
                            "--ended", "2026-08-14T01:00:05Z",
                            "--source-ref", "bin/nightly.sh"], env=env)
            check("three steps are spooled before any database contact",
                  len(spool_rows(spool)) == 3, f"{len(spool_rows(spool))} rows")

            rc, out, err = run_ledger(["flush"], env=env)
            check("flush exits 0", rc == 0, f"exit {rc}: {err}")
            check("flush drains the spool", len(spool_rows(spool)) == 0,
                  f"{len(spool_rows(spool))} rows left")

            with psycopg.connect(DSN, autocommit=False) as conn, conn.cursor() as cur:
                cur.execute("select run_key, state, exit_code, failure_class, "
                            "source_kind, source_ref, environment "
                            "from ops.run where correlation_id = %s order by run_key",
                            (corr,))
                got = cur.fetchall()
                check("all three runs landed under one correlation id", len(got) == 3,
                      f"{len(got)} rows")
                by_key = {r[0]: r for r in got}
                check("the failure kept its class and exit code",
                      "selftest.b" in by_key and by_key["selftest.b"][3]
                      and by_key["selftest.b"][2] == 3, "class or exit code lost")
                check("the skip is recorded as skipped",
                      "selftest.c" in by_key and by_key["selftest.c"][1] == "skipped",
                      "skip was not preserved")
                check("every row names its source",
                      all(r[4] and r[5] for r in got), "a row has no provenance")
                check("every row names the environment it was told",
                      all(r[6] == "staging" for r in got), "environment drifted")

                # ONE QUERY, THE WHOLE CHAIN — the gate's sentence, now against
                # rows a collector produced rather than rows a test seeded.
                cur.execute("select count(*) from ops.v_trace where correlation_id = %s",
                            (corr,))
                check("the chain is visible in ops.v_trace by that one id",
                      cur.fetchone()[0] >= 3, "v_trace does not see collector rows")

                # Re-flushing an empty spool must be a no-op, not a duplicate.
                rc, _, _ = run_ledger(["flush"], env=env)
                cur.execute("select count(*) from ops.run where correlation_id = %s", (corr,))
                check("re-running flush on an empty spool duplicates nothing",
                      cur.fetchone()[0] == 3, "rows were duplicated")

                # CLEAN UP THE COMMITTED ROWS. flush() commits for real — see the
                # module docstring — so this test is the only thing that can
                # remove them, and it must, because a synthetic 'failed' run left
                # in the ledger is a fake incident waiting to be believed. Scoped
                # to this run's own generated correlation id: it touches nothing
                # else, and the id was minted by this process a moment ago.
                cur.execute("delete from ops.run where correlation_id = %s", (corr,))
                removed = cur.rowcount
                conn.commit()
                check("the test removes every row it committed", removed == 3,
                      f"{removed} of 3 removed — synthetic runs are still in the ledger")

            with psycopg.connect(DSN) as c2, c2.cursor() as cur:
                cur.execute("select count(*) from ops.run where correlation_id = %s", (corr,))
                check("and the ledger is left exactly as it was found",
                      cur.fetchone()[0] == 0, "residue remains in ops.run")

print(f"\n{passed} passed, {failed} failed")
if failed:
    print("RUN LEDGER SELFTEST FAILED")
    sys.exit(1)
print("RUN LEDGER SELFTEST PASSED: the collector cannot break what it measures, "
      "cannot invent an environment, and threads one id through one chain.")
