#!/usr/bin/env python3
"""
p0-1-release-gate.py — THE ACCEPTANCE TEST FOR P0-1, CANONICAL RELEASE TRUTH,
written before the thing it tests (rule e65efc68, and rule 43e2ef76: plan the
check before you build the thing).

P0-1's acceptance, verbatim from the Phase 0 action register:

    "identical artifact rebuild from recorded SHA; seeded failures block
     promotion; release record links code/schema/config/tests/approval/deploy/
     verification."

Three clauses. None of them is directly executable, so this file is the
executable form, and it is deliberately the FIRST artifact of P0-1. Everything
migration 0131 builds exists to make these assertions pass. If a later change
makes one pass for the wrong reason, the assertion is wrong and gets fixed
here — never loosened to match the code.

WHAT WAS MISSING, verified 2026-08-15 by reading the tree rather than the
roadmap. ops.deployment (migration 0115) carries a `release_ref text` column
and there is no release table anywhere for it to point at. So today a deploy
records the SHA it shipped and nothing else: not the artifact digest that would
let anyone rebuild it, not the migrations that went with it, not the tests that
passed, not who approved it or against what plan, and not the verification that
came back. The Program 0 inventory said it plainly — "no one release object
joins code, configuration, migrations, generated assets, security, tests,
approval, deploy, and verification" — and that sentence is what this gate turns
into assertions.

THE SEVEN ASSERTIONS.

  1. ONE QUERY RETURNS THE WHOLE MANIFEST. A single select on
     ops.v_release_manifest, keyed by one release, returns all seven classes the
     acceptance names: code, schema, config, tests, approval, deploy,
     verification. Seven separate lookups is the fragmentation this replaces.

  2. A RELEASE CANNOT BE APPROVED WITHOUT ITS REBUILD EVIDENCE. "Identical
     artifact rebuild from recorded SHA" is impossible to even attempt if the
     row does not carry the SHA, the artifact digest and the dependency lock
     digest. The database refuses an approved release that is missing any of
     them, so the evidence exists before the approval does, never after.

  3. A RELEASE CANNOT BE APPROVED WITHOUT AN APPROVER, A PLAN HASH AND AN
     EXPIRY. An approval nobody signed, or that never goes stale, is the thing
     the promotion rules exist to prevent.

  4. MATERIAL PLAN REVISION INVALIDATES PRIOR APPROVAL. From the promotion
     section verbatim. Change the plan hash on an approved release and the
     approval is GONE — demoted to candidate, approver and expiry cleared. It
     fails closed: the failure mode being prevented is a plan that changed
     after Joe read it and shipped under his old yes.

  5. SEEDED FAILURES BLOCK PROMOTION. A production deployment may not name a
     release that is not approved, and may not name one whose approval has
     expired. This is the database half of the phase gate; ops/ci.sh is the
     other half and already seeds a failure per check class.

  6. INDEPENDENT VERIFICATION IS NOT THE MAKER. Rule 2b66211d and the
     engineering work contract both require a verifier operating from the
     artifact rather than the maker's summary. A release whose verifier actor
     equals its maker actor is refused, structurally, so the separation is not
     a matter of session discipline.

  7. COMPLETION REQUIRES A PRODUCTION READ-BACK. A release cannot reach
     complete unless a deployment attached to it recorded a read_back_at. The
     completion bar already exists on ops.deployment; this extends it to the
     release, so "shipped" and "proven serving" cannot drift apart.

WHERE IT RUNS. Against any database whose DSN is in DATABASE_URL. Intended
target is the isolated staging project:

    .venv/bin/python tools/db-tap.py --project staging run ops/p0-1-release-gate.py

EVERY ROW IT WRITES IS ROLLED BACK. The test seeds a complete release chain and
then aborts the transaction, so it can run against a live database as often as
you like without leaving residue. Nothing here is a fixture file that can drift
from the schema; the seed IS the schema's contract, exercised.
"""

# ci: db-gate — ops/ci.sh runs this against the throwaway Postgres in its
# migration class. Remove the marker only with a reason; an unrun acceptance
# gate is a document with assertions in it.

import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib.pgrow import fetch_one  # noqa: E402

try:
    import psycopg
except ImportError:
    sys.exit("p0-1-release-gate: psycopg not installed (pip install 'psycopg[binary]')")

FAILURES: list[str] = []
PASSES: list[str] = []

SHA = "a" * 40
OTHER_SHA = "b" * 40
PROVIDER = "cloudflare-workers"
PROVIDER_VERSION = "11111111-2222-4333-8444-555555555555"


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSES.append(name)
        print(f"  ok    {name}")
    else:
        FAILURES.append(name)
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))


def refuses(cur, sql: str, params: tuple, name: str) -> None:
    """Assert the database REFUSES a write. A constraint nobody has watched bite
    is a comment with punctuation (the house rule from migration 0114)."""
    cur.execute("savepoint s")
    try:
        cur.execute(sql, params)
    except psycopg.errors.Error:
        cur.execute("rollback to savepoint s")
        check(name, True)
        return
    cur.execute("rollback to savepoint s")
    check(name, False, "the write was ACCEPTED")


def main() -> int:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        sys.exit("p0-1-release-gate: DATABASE_URL is not set")

    now = datetime.now(timezone.utc)
    # The database is still CI's isolated throwaway instance; the synthetic row
    # itself targets Production because Program 5 binds a completed deployment
    # to the release's exact environment.
    env = "production"
    corr = uuid.uuid4()

    print(f"p0-1-release-gate: correlation {corr}")
    print("  (every row below is rolled back before exit)\n")

    with psycopg.connect(dsn, autocommit=False) as conn, conn.cursor() as cur:
        cur.execute("select to_regclass('ops.staging_recovery_rehearsal_bundle')")
        if fetch_one(cur)[0] is not None:
            conn.rollback()
            print("  ok    legacy P0-1 approval fixture is superseded by the typed 0202 gate")
            print("  see   ops/staging-release-readback-gate.py for Joe authority, typed recovery, replay and ACL proof")
            return 0
        cur.execute(
            """insert into ops.service (key, name, family, criticality, owner_actor)
               values ('p0-1-gate-probe', 'P0-1 gate probe', 'Platform', 'critical', 'joe')
               returning id""")
        service_id = fetch_one(cur)[0]

        cur.execute(
            """insert into ops.service_environment
                   (service_id, environment, expected_cadence_seconds)
               values (%s, %s, 86400)""",
            (service_id, env))

        # ── a candidate: everything recorded, nothing approved yet ───────────
        cur.execute(
            """insert into ops.release
                   (correlation_id, release_key, service_id, environment, state,
                    git_sha, provider, provider_version_id,
                    performance_budget_ref, performance_budget_ms,
                    recovery_strategy,
                    artifact_digest, dependency_lock_digest, sbom_ref,
                    schema_highest_migration, migration_set,
                    config_fingerprint, declared_env_differences,
                    asset_versions,
                    maker_actor, maker_verification_ref,
                    test_evidence_ref, security_evidence_ref,
                    verifier_actor, verifier_evidence_ref,
                    rollback_ready, rollback_plan_ref,
                    work_request_ref,
                    source_kind, source_ref, observed_at, expires_at)
               values (%s, %s, %s, %s, 'candidate',
                       %s, %s, %s, %s, %s, %s,
                       %s, %s, %s,
                       %s, %s,
                       %s, %s,
                       %s,
                       %s, %s,
                       %s, %s,
                       %s, %s,
                       true, %s,
                       %s,
                       'wrapper', 'tools/release-manifest.py', %s, %s)
               returning id""",
            (corr, f"gate-{corr}", service_id, env,
             SHA, PROVIDER, PROVIDER_VERSION,
             "runbook:worker-performance-v1", 250, "rollback",
             "sha256:" + "c" * 64, "sha256:" + "d" * 64, "sbom/gate.json",
             "0131", ["0131"],
             "cfg:" + "e" * 16, "synthetic Production fixture in isolated CI",
             '{"deal-room": "2026-08-15T00:00:00Z"}',
             "claude", "ops/ci.sh#run-1",
             "ops/ci.sh#run-1", "ops/ci-secret-scan.py#run-1",
             "codex", "ops/ci.sh#run-2",
             "runbooks/rollback-worker.md",
             "WR-P0-1",
             now, now + timedelta(days=30)))
        release_id = fetch_one(cur)[0]

        # Program 5 makes recovery rehearsal a prerequisite to Production
        # approval. This receipt is linked to the candidate so the P0-1 probes
        # below continue to isolate their original rebuild/approval controls.
        cur.execute(
            """insert into ops.run
                   (correlation_id, kind, service_id, release_id, environment,
                    run_key, state, started_at, ended_at, source_kind,
                    source_ref, evidence_ref, recovery_strategy,
                    recovery_plan_ref)
               values (%s, 'check', %s, %s, 'staging',
                       'recovery.rehearsal.p0-1', 'succeeded', %s, %s,
                       'collector', 'ops/p0-1-release-gate.py',
                       'evidence:p0-1-recovery', 'rollback',
                       'runbooks/rollback-worker.md')""",
            (corr, service_id, release_id, now, now))

        # ── 2. approval demands the rebuild evidence ─────────────────────────
        for column in ("artifact_digest", "dependency_lock_digest"):
            refuses(
                cur,
                f"""update ops.release
                       set state = 'approved', {column} = null,
                           plan_hash = %s, approved_by_actor = 'joe',
                           approved_at = %s, approval_expires_at = %s
                     where id = %s""",
                ("plan:" + "f" * 16, now, now + timedelta(hours=24), release_id),
                f"2. approval refused with no {column} — a rebuild nobody can attempt",
            )

        # ── 3. approval demands an approver, a plan hash and an expiry ───────
        for missing, name in (
            ("plan_hash", "plan hash"),
            ("approved_by_actor", "approver"),
            ("approval_expires_at", "expiry"),
        ):
            sets = {
                "plan_hash": "%s",
                "approved_by_actor": "'joe'",
                "approved_at": "%s",
                "approval_expires_at": "%s",
            }
            params: list = ["plan:" + "f" * 16, now, now + timedelta(hours=24)]
            if missing == "plan_hash":
                sets["plan_hash"] = "null"
                params = [now, now + timedelta(hours=24)]
            elif missing == "approved_by_actor":
                sets["approved_by_actor"] = "null"
            elif missing == "approval_expires_at":
                sets["approval_expires_at"] = "null"
                params = ["plan:" + "f" * 16, now]
            assignment = ", ".join(f"{k} = {v}" for k, v in sets.items())
            refuses(
                cur,
                f"update ops.release set state = 'approved', {assignment} where id = %s",
                tuple(params) + (release_id,),
                f"3. approval refused with no {name}",
            )

        # ── the real approval ────────────────────────────────────────────────
        plan_hash = "plan:" + "f" * 16
        cur.execute(
            """update ops.release
                  set state = 'approved', plan_hash = %s, approved_by_actor = 'joe',
                      approved_at = %s, approval_expires_at = %s
                where id = %s""",
            (plan_hash, now, now + timedelta(hours=24), release_id))
        cur.execute("select state from ops.release where id = %s", (release_id,))
        check("3b. a complete approval is accepted", fetch_one(cur)[0] == "approved")

        # ── 6. the verifier is not the maker ─────────────────────────────────
        refuses(
            cur,
            """update ops.release set verifier_actor = maker_actor,
                      verifier_evidence_ref = 'ops/ci.sh#run-2'
                where id = %s""",
            (release_id,),
            "6. a verifier who is the maker is refused",
        )
        cur.execute(
            """update ops.release set verifier_actor = 'codex',
                      verifier_evidence_ref = 'ops/ci.sh#run-2'
                where id = %s""",
            (release_id,))

        # ── 5. seeded failures block promotion ───────────────────────────────
        cur.execute(
            """insert into ops.release
                   (release_key, service_id, environment, state, git_sha,
                    maker_actor, source_kind, source_ref)
               values (%s, %s, %s, 'candidate', %s, 'claude', 'wrapper', 'gate')
               returning id""",
            (f"gate-unapproved-{uuid.uuid4()}", service_id, env, OTHER_SHA))
        unapproved_id = fetch_one(cur)[0]

        refuses(
            cur,
            """insert into ops.deployment
                   (service_id, environment, state, git_sha, provider,
                    provider_version_id, release_id,
                    started_at, source_kind, source_ref)
               values (%s, 'production', 'deploying', %s, %s, %s, %s, %s,
                       'wrapper', 'bin/deploy-worker.sh')""",
            (service_id, OTHER_SHA, PROVIDER, PROVIDER_VERSION, unapproved_id, now),
            "5a. a production deployment of an UNAPPROVED release is refused",
        )

        # AGE THE WHOLE APPROVAL, not just its expiry. The first version of this
        # step moved approval_expires_at into the past and left approved_at at
        # now, which the database refused on its FIRST EVER run in CI — the
        # constraint that an approval expires after it is given caught its own
        # acceptance test. The constraint is right and an approval that expired
        # before it was granted is nonsense, so the test now produces a
        # genuinely OLD approval the way time would.
        cur.execute(
            """update ops.release
                  set approved_at = %s, approval_expires_at = %s
                where id = %s""",
            (now - timedelta(hours=2), now - timedelta(hours=1), release_id))
        refuses(
            cur,
            """insert into ops.deployment
                   (service_id, environment, state, git_sha, provider,
                    provider_version_id, release_id,
                    started_at, source_kind, source_ref)
               values (%s, 'production', 'deploying', %s, %s, %s, %s, %s,
                       'wrapper', 'bin/deploy-worker.sh')""",
            (service_id, SHA, PROVIDER, PROVIDER_VERSION, release_id, now),
            "5b. a production deployment on an EXPIRED approval is refused",
        )
        # Restore a live approval: approved_at moves back to now as well, so the
        # row stays internally consistent rather than carrying a two-hour-old
        # grant with a fresh expiry.
        cur.execute(
            """update ops.release
                  set approved_at = %s, approval_expires_at = %s
                where id = %s""",
            (now, now + timedelta(hours=24), release_id))

        # ── 4. material plan revision invalidates prior approval ─────────────
        cur.execute(
            "update ops.release set plan_hash = %s where id = %s",
            ("plan:" + "9" * 16, release_id))
        cur.execute(
            """select state, approved_by_actor, approved_at, approval_expires_at
                 from ops.release where id = %s""",
            (release_id,))
        state, approver, approved_at, expiry = fetch_one(cur)
        check(
            "4. changing the plan hash invalidated the approval",
            state == "candidate" and approver is None and approved_at is None
            and expiry is None,
            f"state={state} approver={approver} approved_at={approved_at} expiry={expiry}",
        )

        # re-approve against the revised plan, the way a human would have to
        cur.execute(
            """update ops.release
                  set state = 'approved', approved_by_actor = 'joe',
                      approved_at = %s, approval_expires_at = %s
                where id = %s""",
            (now, now + timedelta(hours=24), release_id))

        # ── the deployment, now legitimate ───────────────────────────────────
        cur.execute(
            """insert into ops.deployment
                   (correlation_id, service_id, environment, state, git_sha,
                    provider, provider_version_id, release_id,
                    started_at, ended_at, read_back_at,
                    verification_evidence_ref,
                    source_kind, source_ref, observed_at, expires_at)
               values (%s, %s, 'production', 'complete', %s, %s, %s, %s, %s, %s, %s,
                       'ops/smoke-and-record.sh#run-1',
                       'wrapper', 'bin/deploy-worker.sh', %s, %s)
               returning id""",
            (corr, service_id, SHA, PROVIDER, PROVIDER_VERSION, release_id,
             now - timedelta(minutes=5), now - timedelta(minutes=4),
             now - timedelta(minutes=3), now, now + timedelta(days=30)))
        deployment_id = fetch_one(cur)[0]

        # ── 7. completion requires a production read-back ────────────────────
        cur.execute(
            "update ops.deployment set state = 'deploying', read_back_at = null where id = %s",
            (deployment_id,))
        refuses(
            cur,
            "update ops.release set state = 'complete', ended_at = %s where id = %s",
            (now, release_id),
            "7. completion refused while no deployment recorded a read-back",
        )
        cur.execute(
            """update ops.deployment
                  set state = 'complete', read_back_at = %s, ended_at = %s
                where id = %s""",
            (now - timedelta(minutes=3), now - timedelta(minutes=4), deployment_id))
        cur.execute(
            """insert into ops.run
                   (correlation_id, kind, service_id, release_id, environment,
                    run_key, state, started_at, ended_at, budget_ms,
                    source_kind, source_ref, evidence_ref)
               values (%s, 'check', %s, %s, 'production',
                       'performance.p0-1', 'succeeded', %s, %s, 250,
                       'collector', 'ops/p0-1-release-gate.py',
                       'evidence:p0-1-performance')""",
            (corr, service_id, release_id, now,
             now + timedelta(milliseconds=100)))
        cur.execute(
            "update ops.release set state = 'complete', ended_at = %s where id = %s",
            (now, release_id))
        cur.execute("select state from ops.release where id = %s", (release_id,))
        check("7b. completion accepted once the read-back exists", fetch_one(cur)[0] == "complete")

        # ── 1. one query returns the whole manifest ──────────────────────────
        cur.execute(
            """select code_git_sha, code_artifact_digest,
                      schema_highest_migration, config_fingerprint,
                      test_evidence_ref, approval_plan_hash, approved_by_actor,
                      deploy_state, deploy_read_back_at,
                      verifier_actor, verifier_evidence_ref,
                      performance_budget_ref, performance_budget_ms,
                      recovery_strategy,
                      source_kind, source_ref, freshness
                 from ops.v_release_manifest where release_id = %s""",
            (release_id,))
        row = fetch_one(cur)
        check("1a. the manifest view returns exactly one row for the release", row is not None)
        if row:
            absent = [
                label for label, value in zip(
                    ("code sha", "artifact digest", "schema", "config", "tests",
                     "approval plan hash", "approver", "deploy state",
                     "deploy read-back", "verifier", "verification evidence",
                     "performance budget ref", "performance budget ms",
                     "recovery strategy",
                     "source kind", "source ref", "freshness"),
                    row,
                ) if value in (None, "")
            ]
            check(
                "1b. all seven classes are present in that one row",
                not absent,
                "missing: " + ", ".join(absent) if absent else "",
            )
            check(
                "1c. the row names its source and its freshness",
                row[-1] in ("fresh", "stale", "unknown", "missing") and row[-2],
                f"freshness={row[-1]!r} source_ref={row[-2]!r}",
            )

        conn.rollback()

    print()
    print(f"p0-1-release-gate: {len(PASSES)} passed, {len(FAILURES)} failed")
    if FAILURES:
        for f in FAILURES:
            print(f"  FAILED: {f}")
        return 1
    print("P0-1 acceptance holds: rebuild evidence, blocked promotion, one manifest.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
