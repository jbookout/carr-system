#!/usr/bin/env python3
"""The first executable acceptance gate for Program 5 controlled promotion.

Program 5's release bar is deliberately stricter than P0-1's release-record
bar.  A production release is not complete merely because a deployment row
exists: its verifier and rollback receipt must be present, and the completed
deployment must be the same release, SHA, and Production target that was
approved.  This gate is written before the schema enforcement that makes those
statements true.

It runs against CI's throwaway Postgres.  All rows use a synthetic service and
are rolled back, so the same test is safe to run repeatedly against an isolated
rehearsal database.
"""

# ci: db-gate — discovered by ops/ci.sh's migration class.

import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib.pgrow import fetch_one  # noqa: E402

try:
    import psycopg
except ImportError:
    sys.exit("program5-promotion-gate: psycopg not installed")


FAILURES: list[str] = []
PASSES: list[str] = []

SHA = "a" * 40
OTHER_SHA = "b" * 40


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSES.append(name)
        print(f"  ok    {name}")
    else:
        FAILURES.append(name)
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))


def refuses(cur, sql: str, params: tuple, name: str) -> None:
    """Prove a proposed write is structurally refused, not merely discouraged."""
    cur.execute("savepoint program5_refusal")
    try:
        cur.execute(sql, params)
    except psycopg.errors.Error:
        cur.execute("rollback to savepoint program5_refusal")
        check(name, True)
        return
    cur.execute("rollback to savepoint program5_refusal")
    check(name, False, "the write was ACCEPTED")


def seed_release(cur, service_id, now: datetime, *, environment: str = "production",
                 git_sha: str = SHA):
    """Return an otherwise promotion-ready candidate release."""
    correlation = uuid.uuid4()
    cur.execute(
        """insert into ops.release
               (correlation_id, release_key, service_id, environment, state,
                git_sha, artifact_digest, dependency_lock_digest,
                maker_actor, maker_verification_ref, test_evidence_ref,
                security_evidence_ref, verifier_actor, verifier_evidence_ref,
                rollback_ready, rollback_plan_ref, plan_hash, approved_by_actor,
                approved_at, approval_expires_at, source_kind, source_ref,
                observed_at, expires_at)
           values (%s, %s, %s, %s, 'candidate',
                   %s, %s, %s,
                   'claude', 'ops/ci.sh#maker', 'ops/ci.sh#tests',
                   'ops/ci.sh#security', 'codex', 'ops/ci.sh#independent',
                   true, 'runbooks/rollback-worker.md', %s, 'joe',
                   %s, %s, 'wrapper', 'ops/program5-promotion-gate.py',
                   %s, %s)
           returning id, correlation_id""",
        (correlation, f"program5-{correlation}", service_id, environment,
         git_sha, "sha256:" + "c" * 64, "sha256:" + "d" * 64,
         "plan:" + "e" * 16, now, now + timedelta(hours=24),
         now, now + timedelta(days=1)),
    )
    return fetch_one(cur)


def insert_deployment(cur, service_id, release_id, correlation_id, now: datetime, *,
                      environment: str, git_sha: str, state: str,
                      read_back: bool = False):
    """Insert a deployment receipt with only redacted pointer evidence."""
    ended = now if state in {"complete", "failed", "aborted", "rolled_back", "superseded"} else None
    cur.execute(
        """insert into ops.deployment
               (correlation_id, service_id, environment, state, git_sha, release_id,
                started_at, ended_at, read_back_at, verification_evidence_ref,
                source_kind, source_ref)
           values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                   'wrapper', 'ops/program5-promotion-gate.py')
           returning id""",
        (correlation_id, service_id, environment, state, git_sha, release_id,
         now, ended, now if read_back else None,
         "probe:/release" if read_back else None))
    return fetch_one(cur)[0]


def main() -> int:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        sys.exit("program5-promotion-gate: DATABASE_URL is not set")

    now = datetime.now(timezone.utc)
    with psycopg.connect(dsn, autocommit=False) as conn, conn.cursor() as cur:
        cur.execute(
            """insert into ops.service (key, name, family, criticality, owner_actor)
               values (%s, 'Program 5 promotion gate probe', 'Platform', 'critical', 'joe')
               returning id""",
            (f"program5-promotion-{uuid.uuid4()}",))
        service_id = fetch_one(cur)[0]

        # Every promoted state needs both independent verification and a usable
        # rollback plan.  Candidate is intentionally excluded: it is where that
        # evidence is assembled.
        for state in ("approved", "deploying", "verifying", "complete"):
            release_id, correlation = seed_release(cur, service_id, now)
            if state == "complete":
                cur.execute("update ops.release set state = 'approved' where id = %s", (release_id,))
                insert_deployment(cur, service_id, release_id, correlation, now,
                                  environment="production", git_sha=SHA,
                                  state="complete", read_back=True)
            refuses(
                cur,
                """update ops.release
                      set state = %s, verifier_actor = null,
                          verifier_evidence_ref = null,
                          ended_at = case when %s = 'complete' then %s else null end
                    where id = %s""",
                (state, state, now, release_id),
                f"{state} release without independent verifier is refused",
            )
            refuses(
                cur,
                """update ops.release
                      set state = %s, rollback_ready = true, rollback_plan_ref = null,
                          ended_at = case when %s = 'complete' then %s else null end
                    where id = %s""",
                (state, state, now, release_id),
                f"{state} release without a rollback plan is refused",
            )

        # A Production deployment must be exactly the release that was approved,
        # not merely an approved release in the same service family.
        release_id, correlation = seed_release(cur, service_id, now)
        cur.execute("update ops.release set state = 'approved' where id = %s", (release_id,))
        refuses(
            cur,
            """insert into ops.deployment
                   (correlation_id, service_id, environment, state, git_sha, release_id,
                    started_at, source_kind, source_ref)
               values (%s, %s, 'production', 'deploying', %s, %s, %s,
                       'wrapper', 'ops/program5-promotion-gate.py')""",
            (correlation, service_id, OTHER_SHA, release_id, now),
            "Production deployment with a different SHA is refused",
        )
        release_id, correlation = seed_release(
            cur, service_id, now, environment="staging")
        cur.execute("update ops.release set state = 'approved' where id = %s", (release_id,))
        refuses(
            cur,
            """insert into ops.deployment
                   (correlation_id, service_id, environment, state, git_sha, release_id,
                    started_at, source_kind, source_ref)
               values (%s, %s, 'production', 'deploying', %s, %s, %s,
                       'wrapper', 'ops/program5-promotion-gate.py')""",
            (correlation, service_id, SHA, release_id, now),
            "deployment to a target other than the release environment is refused",
        )

        # Completion needs a complete Production receipt, with live read-back,
        # for the same SHA.  A staging proof and a partial (verifying) receipt
        # are both deliberately insufficient.
        for deployment_environment, deployment_state, read_back, description in (
            ("staging", "complete", True, "a non-Production read-back"),
            ("production", "verifying", True, "a partial Production read-back"),
        ):
            release_id, correlation = seed_release(cur, service_id, now)
            cur.execute("update ops.release set state = 'approved' where id = %s", (release_id,))
            insert_deployment(cur, service_id, release_id, correlation, now,
                              environment=deployment_environment, git_sha=SHA,
                              state=deployment_state, read_back=read_back)
            refuses(
                cur,
                """update ops.release set state = 'complete', ended_at = %s
                     where id = %s""",
                (now, release_id),
                f"release completion with only {description} is refused",
            )

        # Positive control: the exact approved Production release completes only
        # after its matching complete deployment supplies a live read-back.
        release_id, correlation = seed_release(cur, service_id, now)
        cur.execute("update ops.release set state = 'approved' where id = %s", (release_id,))
        insert_deployment(cur, service_id, release_id, correlation, now,
                          environment="production", git_sha=SHA,
                          state="complete", read_back=True)
        cur.execute(
            "update ops.release set state = 'complete', ended_at = %s where id = %s",
            (now, release_id))
        cur.execute(
            """select r.state, d.environment, d.state, d.git_sha, d.read_back_at
                 from ops.release r
                 join ops.deployment d on d.release_id = r.id
                where r.id = %s""",
            (release_id,))
        state, environment, deploy_state, deploy_sha, read_back_at = fetch_one(cur)
        check(
            "matching Production deployment with read-back completes the release",
            state == "complete" and environment == "production"
            and deploy_state == "complete" and deploy_sha == SHA and read_back_at is not None,
            f"release={state} deployment={environment}/{deploy_state}/{deploy_sha}/{read_back_at}",
        )

        conn.rollback()

    print(f"\n{len(PASSES)} passed, {len(FAILURES)} failed")
    if FAILURES:
        print("PROGRAM 5 PROMOTION GATE NOT MET:")
        for failure in FAILURES:
            print(f"  - {failure}")
        return 1
    print("PROGRAM 5 PROMOTION GATE MET: releases are verified, recoverable, and exact.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
