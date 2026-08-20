#!/usr/bin/env python3
"""release-abandon-selftest.py — a release can be ended without shipping, and it
has to say why. Fixtures written before the verb (rule e65efc68).

WHAT PROMPTED IT. The first real releases went through ops.release on 2026-08-16
and left two candidates behind — one overtaken when main moved two commits before
Joe signed, one replaced by a version carrying the security evidence the approval
constraint requires. Both are inert: they cannot ship, because a deploy needs a
live approval matching a freshly recomputed plan hash. But nothing could move
them out of `candidate`, so the table kept two rows whose real status lived only
in a decision entry.

WHY `abandoned` AND NOT `superseded`, settled by the schema rather than by
preference. 0131 exempts only draft/candidate/abandoned from needing rebuild
evidence and an approval, so reaching `superseded` requires a full artifact
digest, dependency lock, plan hash, approver and expiry — a release that was
APPROVED, and usually one that shipped and was replaced by a later deploy. An
unapproved candidate overtaken before signing has none of that. It is abandoned,
and its reason names the successor in words.

WHAT MUST NOT BECOME POSSIBLE. Abandoning is a way to end a release, never a way
to erase one that shipped. A row that reached approved-and-deployed is history;
letting it be marked abandoned would let a deploy be written out of the record
after the fact, which is the opposite of what a release ledger is for.

WHERE THESE RUN. They need a Postgres carrying the schema and nothing more —
NOT Neon specifically. CI supplies a disposable loopback PostgreSQL service via
CARR_CI_DATABASE_URL. A developer push without that explicit fixture DSN does
not substitute a metered Neon branch: it reports the database cases as not run,
while hosted CI executes them against its already-running local service.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from contextlib import contextmanager
from collections.abc import Iterator
from pathlib import Path

import psycopg
from psycopg import sql

REPO = Path(__file__).resolve().parent.parent
ABANDON_DB = "abandon_check"
PROVIDER = "cloudflare-workers"
PROVIDER_VERSION = "11111111-2222-4333-8444-555555555555"

PASSED: int = 0
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED
    if condition:
        PASSED += 1
        print(f"  ok    {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))


def psql(dsn, *args):
    return subprocess.run(["psql", dsn, "-v", "ON_ERROR_STOP=1", *args],
                          capture_output=True, text=True, timeout=1800)


def record(dsn, *args):
    return subprocess.run(
        [sys.executable, str(REPO / "tools" / "ops-record.py"), *args],
        capture_output=True, text=True, timeout=300,
        env={**os.environ, "DATABASE_URL": dsn})


@contextmanager
def isolated_ci_database(base_dsn: str) -> Iterator[str]:
    """Give this stateful fixture its own database on CI's loopback cluster.

    The gates class and migration class intentionally share a PostgreSQL
    server, but the migration class must receive a *fresh* database.  Loading
    db/schema.sql directly into CARR_CI_DATABASE_URL contaminated that database
    before the migration class ran.  A sibling database preserves the cheap
    local/CI execution path without weakening either test.
    """
    params = psycopg.conninfo.conninfo_to_dict(base_dsn)
    host = str(params.get("host") or "")
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError("release-abandon isolation requires loopback PostgreSQL")
    database = f"release_abandon_{os.getpid()}_{time.time_ns()}"[:63]
    admin = psycopg.conninfo.make_conninfo(base_dsn, dbname="postgres")
    with psycopg.connect(admin, autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                sql.SQL("create database {} template template0").format(
                    sql.Identifier(database)
                )
            )
    isolated = psycopg.conninfo.make_conninfo(base_dsn, dbname=database)
    try:
        yield isolated
    finally:
        with psycopg.connect(admin, autocommit=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "select pg_terminate_backend(pid) from pg_stat_activity "
                    "where datname=%s and pid <> pg_backend_pid()",
                    (database,),
                )
                cursor.execute(
                    sql.SQL("drop database {}").format(sql.Identifier(database))
                )


def _cases(dsn: str) -> None:
    record(dsn, "sync-registry")
    # A FULL manifest. 0131 exempts only draft/candidate/abandoned from
    # needing rebuild evidence, so a row that must legitimately reach
    # `complete` (case 4) needs the digest and the lock present from the
    # start. The first run of these fixtures used a thin manifest, the
    # setup UPDATE silently failed the constraint, and case 4 then tested
    # nothing — it passed abandon on a row still sitting at `candidate`.
    manifest = {"service": "carr-mcp", "environment": "staging",
                "git_sha": "a" * 40, "plan_hash": "plan:selftest",
                "artifact_digest": "d" * 64,
                "dependency_lock_digest": "e" * 64, "migration_set": []}
    mpath = Path(os.environ.get("TMPDIR", "/tmp")) / "abandon-manifest.json"
    mpath.write_text(json.dumps(manifest))

    for k in ("rel-abandon-a", "rel-abandon-b"):
        record(dsn, "release", "candidate", "--key", k, "--manifest", str(mpath),
               "--service", "carr-mcp", "--environment", "staging",
               "--maker", "selftest", "--maker-verification", "ref",
               "--test-evidence", "ref", "--security-evidence", "ref")
    # Production candidate intake now rebuilds the manifest before it opens a
    # DB connection. Build and bind the fixture through the canonical tool so
    # this test carries real source evidence instead of a synthetic digest that
    # the real intake path must correctly refuse.
    built = subprocess.run(
        [sys.executable, str(REPO / "tools" / "release-manifest.py"),
         "build", "--sha", "HEAD", "--environment", "production",
         "--performance-budget-ref", "runbook:worker-performance-v1",
         "--performance-budget-ms", "1500",
         "--recovery-strategy", "rollback",
         "--rollback-plan-ref", "runbook:rollback-worker-v1"],
        cwd=REPO, capture_output=True, text=True, timeout=300)
    check("0a. canonical Production source manifest builds",
          built.returncode == 0, (built.stderr or built.stdout).strip()[:160])
    if built.returncode != 0:
        return
    production_mpath = Path(os.environ.get("TMPDIR", "/tmp")) / "abandon-production-manifest.json"
    source_mpath = Path(os.environ.get("TMPDIR", "/tmp")) / "abandon-production-source.json"
    source_mpath.write_text(built.stdout)
    bound = subprocess.run(
        [sys.executable, str(REPO / "tools" / "release-manifest.py"),
         "bind-provider", "--manifest", str(source_mpath),
         "--provider", PROVIDER, "--provider-version-id", PROVIDER_VERSION],
        cwd=REPO, capture_output=True, text=True, timeout=300)
    check("0aa. canonical Production manifest binds the provider version",
          bound.returncode == 0, (bound.stderr or bound.stdout).strip()[:160])
    if bound.returncode != 0:
        return
    production_mpath.write_text(bound.stdout)
    production_manifest = json.loads(bound.stdout)
    production_sha = production_manifest["git_sha"]
    production_plan = production_manifest["plan_hash"]
    candidate = record(dsn, "release", "candidate", "--key", "rel-shipped",
                       "--manifest", str(production_mpath), "--service", "carr-mcp",
                       "--environment", "production", "--maker", "selftest",
                       "--provider", PROVIDER, "--provider-version-id", PROVIDER_VERSION,
                       "--maker-verification", "ref", "--test-evidence", "ref",
                       "--security-evidence", "ref")
    check("0ab. verified Production candidate reaches the ledger",
          candidate.returncode == 0,
          (candidate.stderr or candidate.stdout).strip()[:160])
    if candidate.returncode != 0:
        return

    # Program 5 makes independent verification and rollback readiness
    # prerequisites for approval, rather than evidence added after deployment.
    # Only the two fixtures that will enter a promoted state need them; the
    # abandoned candidate remains a deliberately ordinary candidate.
    ready = psql(
        dsn, "-c",
        "update ops.release "
        "set verifier_actor='independent-selftest', "
        "    verifier_evidence_ref='ops/release-abandon-selftest.py#verification', "
        "    rollback_ready=true, "
        "    rollback_plan_ref='ops/release-abandon-selftest.py#rollback' "
        "where release_key='rel-abandon-b'; "
        "update ops.release "
        "set verifier_actor='independent-selftest', "
        "    verifier_evidence_ref='ops/release-abandon-selftest.py#verification' "
        "where release_key='rel-shipped'")
    check("0b. promoted fixtures carry verifier and rollback evidence",
          ready.returncode == 0, (ready.stderr or ready.stdout).strip()[:160])
    # This test owns an explicit throwaway database. Do not call the routine
    # writer here: it intentionally authenticates only through CARR_DB_JOBS_URL,
    # which may point at a real environment on a developer machine. The exact
    # writer contract is covered separately; this fixture needs only the
    # release-linked receipt required to exercise abandon semantics.
    recovery = psql(
        dsn, "-c",
        "insert into ops.run "
        "(correlation_id,kind,service_id,release_id,environment,run_key,state,"
        " started_at,ended_at,recovery_strategy,recovery_plan_ref,source_kind,"
        " source_ref,evidence_ref) "
        "select gen_random_uuid(),'check',service_id,id,'staging',"
        " 'recovery.rehearsal.worker','succeeded',now(),"
        " now()+interval '1 millisecond',recovery_strategy,rollback_plan_ref,"
        " 'wrapper','ops/release-abandon-selftest.py',"
        " 'evidence:release-abandon-recovery' "
        "from ops.release where release_key='rel-shipped'")
    check("0c. Production fixture carries a linked recovery rehearsal",
          recovery.returncode == 0,
          (recovery.stderr or recovery.stdout).strip()[:160])

    # ── 1. a candidate can be abandoned, with a reason ──────────────────
    r = record(dsn, "release", "abandon", "--key", "rel-abandon-a",
               "--reason", "superseded before approval by a later candidate")
    check("1. a candidate can be abandoned with a reason", r.returncode == 0,
          (r.stderr or r.stdout).strip()[:160])
    got = psql(dsn, "-At", "-c",
               "select state, abandoned_reason is not null, ended_at is not null "
               "from ops.release where release_key='rel-abandon-a'")
    check("1b. it lands as abandoned, with its reason and an end time",
          got.stdout.strip() == "abandoned|t|t", f"got {got.stdout.strip()!r}")

    # ── 2. no reason, no abandonment ────────────────────────────────────
    r = record(dsn, "release", "abandon", "--key", "rel-abandon-b")
    check("2. abandoning without a reason is REFUSED", r.returncode != 0,
          "a terminal state with no recorded reason is the thing this exists to prevent")

    # ── 3. an APPROVED release can still be abandoned before it ships ──
    # The window between a signature and a deploy is real, and a plan can be
    # withdrawn inside it. `approved` is therefore in the allowed set.
    record(dsn, "release", "approve", "--key", "rel-abandon-b",
           "--plan-hash", "plan:selftest", "--actor", "selftest")
    r = record(dsn, "release", "abandon", "--key", "rel-abandon-b",
               "--reason", "withdrawn after signing, before any deploy ran")
    check("3. an approved release can be abandoned before it ships",
          r.returncode == 0, (r.stderr or r.stdout).strip()[:160])

    # ── 4. history is not erasable ──────────────────────────────────────
    # WALK THE REAL LIFECYCLE rather than forcing the state. 0131's trigger
    # refuses `complete` unless a deployment attached to the release recorded
    # a read-back — "shipped is not the same as serving" — so the fixture has
    # to approve, deploy and read back exactly as a real release does. The
    # earlier version set state directly, the constraint refused it silently,
    # and case 4 then proved nothing on a row still sitting at `candidate`.
    record(dsn, "release", "approve", "--key", "rel-shipped",
           "--plan-hash", production_plan, "--actor", "selftest")
    journey_corr = "77777777-7777-4777-8777-777777777777"
    record(dsn, "deployment", "--service", "carr-mcp", "--environment", "production",
           "--state", "complete", "--git-sha", production_sha, "--verb-count", "1",
           "--provider", PROVIDER, "--provider-version-id", PROVIDER_VERSION,
           "--correlation", journey_corr,
           "--release-key", "rel-shipped", "--source-kind", "wrapper",
           "--source-ref", "ops/release-abandon-selftest.py",
           "--read-back-at", "now", "--verification-evidence-ref", "selftest read-back")
    performance = psql(
        dsn, "-c",
        "insert into ops.run "
        "(correlation_id,kind,service_id,release_id,environment,run_key,state,"
        " started_at,ended_at,budget_ms,source_kind,source_ref,evidence_ref) "
        f"select '{journey_corr}'::uuid,'check',service_id,id,'production',"
        " 'performance.release','succeeded',now()-interval '1 second',now(),"
        " performance_budget_ms,'wrapper','ops/release-abandon-selftest.py',"
        " 'evidence:release-abandon-performance' "
        "from ops.release where release_key='rel-shipped'")
    check("4aa. shipped fixture carries a measured performance receipt",
          performance.returncode == 0,
          (performance.stderr or performance.stdout).strip()[:160])
    done = record(dsn, "release", "complete", "--key", "rel-shipped")
    check("4a. the shipped fixture really reached `complete`",
          done.returncode == 0,
          f"setup did not land, so case 4 would test nothing: "
          f"{(done.stderr or done.stdout).strip()[:140]}")
    r = record(dsn, "release", "abandon", "--key", "rel-shipped",
               "--reason", "trying to erase a release that already shipped")
    check("4. a release that already shipped CANNOT be abandoned",
          r.returncode != 0,
          "abandoning is a way to END a release, never a way to erase one that shipped")

    # ── 5. an unknown key is a clear refusal, not a silent no-op ────────
    r = record(dsn, "release", "abandon", "--key", "rel-does-not-exist",
               "--reason", "this key was never recorded anywhere")
    check("5. an unknown release key is refused, not silently ignored",
          r.returncode != 0 and "rel-does-not-exist" in (r.stderr + r.stdout),
          (r.stderr or r.stdout).strip()[:160])



def run_cases(dsn: str) -> None:
    """Every assertion, against whatever Postgres was handed in."""
    if psql(dsn, "-f", str(REPO / "db" / "schema.sql")).returncode != 0:
        check("the schema loads so the rest can run", False)
        return
    mig = subprocess.run([sys.executable, str(REPO / "tools" / "migrate.py"),
                          "--apply", "--yes"], capture_output=True, text=True,
                         timeout=1800, env={**os.environ, "DATABASE_URL": dsn})
    check("0. schema and every migration apply, 0134 included",
          mig.returncode == 0,
          (mig.stderr or mig.stdout).strip().splitlines()[-1][:160]
          if (mig.stderr or mig.stdout) else "")
    if mig.returncode != 0:
        return
    _cases(dsn)


def main() -> int:
    # CI's throwaway Postgres first: it is cheaper, faster, and means these
    # fixtures actually run on the surface that gates the merge.
    ci_dsn = os.environ.get("CARR_CI_DATABASE_URL")
    if ci_dsn:
        print("release-abandon-selftest: using an isolated database on CI Postgres")
        try:
            with isolated_ci_database(ci_dsn) as isolated_dsn:
                run_cases(isolated_dsn)
        except Exception:
            print("release-abandon-selftest: isolated CI database unavailable",
                  file=sys.stderr)
            return 1
        print(f"\nrelease-abandon-selftest: {PASSED}/{PASSED + len(FAILED)} passed")
        if FAILED:
            print("FAILURES: " + ", ".join(FAILED))
            return 1
        return 0

    print("release-abandon-selftest: database cases NOT RUN — "
          "CARR_CI_DATABASE_URL is absent; metered-provider fallback is disabled")
    return 0


if __name__ == "__main__":
    sys.exit(main())
