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
PROVIDER = "cloudflare-workers"
PROVIDER_VERSION = "11111111-2222-4333-8444-555555555555"
OTHER_PROVIDER_VERSION = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"


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
                 git_sha: str = SHA, provider: str | None = PROVIDER,
                 provider_version_id: str | None = PROVIDER_VERSION,
                 performance_budget_ref: str | None = "ops/performance#budget",
                 performance_budget_ms: int | None = 250,
                 recovery_strategy: str | None = "rollback"):
    """Return an otherwise promotion-ready candidate release."""
    correlation = uuid.uuid4()
    cur.execute(
        """insert into ops.release
               (correlation_id, release_key, service_id, environment, state,
                git_sha, provider, provider_version_id, performance_budget_ref,
                performance_budget_ms, recovery_strategy, artifact_digest, dependency_lock_digest,
                maker_actor, maker_verification_ref, test_evidence_ref,
                security_evidence_ref, verifier_actor, verifier_evidence_ref,
                rollback_ready, rollback_plan_ref, plan_hash, approved_by_actor,
                approved_at, approval_expires_at, source_kind, source_ref,
                observed_at, expires_at)
           values (%s, %s, %s, %s, 'candidate',
                   %s, %s, %s, %s, %s, %s, %s, %s,
                   'claude', 'ops/ci.sh#maker', 'ops/ci.sh#tests',
                   'ops/ci.sh#security', 'codex', 'ops/ci.sh#independent',
                   true, 'runbooks/rollback-worker.md', %s, 'joe',
                   %s, %s, 'wrapper', 'ops/program5-promotion-gate.py',
                   %s, %s)
           returning id, correlation_id""",
        (correlation, f"program5-{correlation}", service_id, environment,
         git_sha, provider, provider_version_id, performance_budget_ref, performance_budget_ms,
         recovery_strategy, "sha256:" + "c" * 64, "sha256:" + "d" * 64,
         "plan:" + "e" * 16, now, now + timedelta(hours=24),
         now, now + timedelta(days=1)),
    )
    release = fetch_one(cur)
    # Recovery is pre-approval evidence.  Production performance is recorded
    # only after the same-correlation deployment identity read-back.
    insert_assurance_runs(cur, service_id, release[0], now, include_performance=False)
    return release


def insert_assurance_runs(cur, service_id, release_id, now: datetime, *, budget: int = 250,
                          correlation_id=None, include_performance=True):
    rows = [("staging", "recovery.rehearsal.worker", 1)]
    if include_performance:
        rows.insert(0, ("production", "performance.api", budget))
    for environment, key, duration in rows:
        cur.execute(
            """insert into ops.run (correlation_id, kind, service_id, release_id, environment,
                                     run_key, state, started_at, ended_at, budget_ms,
                                     recovery_strategy, recovery_plan_ref,
                                     source_kind, source_ref, evidence_ref)
               values (%s,'check',%s,%s,%s,%s,'succeeded',%s,%s,%s,%s,%s,'collector',%s,%s)""",
            (correlation_id if key.startswith("performance.") and correlation_id else uuid.uuid4(),
             service_id, release_id, environment, key, now,
             now + timedelta(milliseconds=duration),
             budget if key.startswith("performance.") else None,
             "rollback" if key.startswith("recovery.rehearsal.") else None,
             "runbooks/rollback-worker.md" if key.startswith("recovery.rehearsal.") else None,
             "ops/program5-promotion-gate.py", "evidence:assurance"))


def insert_run(cur, service_id, now: datetime, *, release_id=None,
               environment="production", run_key="performance.api",
               state="succeeded", evidence_ref="evidence:assurance",
               budget_ms=250, duration_ms=0, recovery_strategy=None,
               recovery_plan_ref=None, correlation_id=None):
    """Insert one receipt-shaped run for a structural refusal probe."""
    started = now
    ended = now + timedelta(milliseconds=duration_ms)
    cur.execute(
        """insert into ops.run (correlation_id, kind, service_id, release_id, environment,
                                 run_key, state, failure_class, started_at, ended_at,
                                 budget_ms, recovery_strategy, recovery_plan_ref,
                                 source_kind, source_ref, evidence_ref)
           values (%s, 'check', %s, %s, %s, %s, %s, %s, %s, %s, %s,
                   %s, %s, 'collector', 'ops/program5-promotion-gate.py', %s)""",
        (correlation_id or uuid.uuid4(), service_id, release_id, environment, run_key, state,
         "probe_failure" if state in {"failed", "timed_out"} else None,
         started, ended, budget_ms, recovery_strategy, recovery_plan_ref, evidence_ref),
    )


def insert_deployment(cur, service_id, release_id, correlation_id, now: datetime, *,
                      environment: str, git_sha: str, state: str,
                      read_back: bool = False, provider: str | None = PROVIDER,
                      provider_version_id: str | None = PROVIDER_VERSION):
    """Insert a deployment receipt with only redacted pointer evidence."""
    ended = now if state in {"complete", "failed", "aborted", "rolled_back", "superseded"} else None
    cur.execute(
        """insert into ops.deployment
               (correlation_id, service_id, environment, state, git_sha, provider,
                provider_version_id, release_id,
                started_at, ended_at, read_back_at, verification_evidence_ref,
                source_kind, source_ref)
           values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                   'wrapper', 'ops/program5-promotion-gate.py')
           returning id""",
        (correlation_id, service_id, environment, state, git_sha, provider,
         provider_version_id, release_id,
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

        # Candidates remain a planning surface.  Provider identity is required
        # only when a Production release is promoted, so a legacy-compatible
        # candidate may still be assembled before its provider receipt exists.
        candidate_id, _ = seed_release(
            cur, service_id, now, provider=None, provider_version_id=None)
        cur.execute("select state, provider, provider_version_id from ops.release where id = %s",
                    (candidate_id,))
        candidate_state, candidate_provider, candidate_version = fetch_one(cur)
        check(
            "candidate release may omit provider identity while evidence is assembled",
            candidate_state == "candidate" and candidate_provider is None
            and candidate_version is None,
        )
        empty_version_id, _ = seed_release(
            cur, service_id, now, provider_version_id="")
        refuses(
            cur,
            "update ops.release set state = 'approved' where id = %s",
            (empty_version_id,),
            "Production approval with an empty provider version is refused",
        )
        mutable_alias_id, _ = seed_release(
            cur, service_id, now, provider_version_id="latest")
        refuses(
            cur,
            "update ops.release set state = 'approved' where id = %s",
            (mutable_alias_id,),
            "Production approval with a mutable provider alias is refused",
        )

        # The plan fields are a gate at approval, not documentation attached
        # after the traffic change.  Recovery is seeded so each probe isolates
        # exactly one missing assurance field.
        for column in ("performance_budget_ref", "performance_budget_ms", "recovery_strategy"):
            release_id, _ = seed_release(cur, service_id, now)
            refuses(
                cur,
                f"update ops.release set {column} = null, state = 'approved' where id = %s",
                (release_id,),
                f"Production approval without {column} is refused",
            )
        direct_key = f"program5-direct-{uuid.uuid4()}"
        refuses(
            cur,
            """insert into ops.release
                   (correlation_id, release_key, service_id, environment, state, git_sha,
                    provider, provider_version_id, performance_budget_ref, performance_budget_ms,
                    recovery_strategy, artifact_digest, dependency_lock_digest, maker_actor,
                    maker_verification_ref, test_evidence_ref, security_evidence_ref,
                    verifier_actor, verifier_evidence_ref, rollback_ready, rollback_plan_ref,
                    plan_hash, approved_by_actor, approved_at, approval_expires_at,
                    source_kind, source_ref)
               values (%s, %s, %s, 'production', 'approved', %s, %s, %s, 'budget:direct', 250,
                       'rollback', %s, %s, 'claude', 'maker', 'tests', 'security', 'codex',
                       'verifier', true, 'rollback', 'plan:direct', 'joe', %s, %s, 'wrapper', 'gate')""",
            (uuid.uuid4(), direct_key, service_id, SHA, PROVIDER, PROVIDER_VERSION,
             "sha256:" + "c" * 64, "sha256:" + "d" * 64, now, now + timedelta(hours=1)),
            "direct Production approval without a recovery rehearsal is refused",
        )
        refuses(
            cur,
            """insert into ops.release
                   (correlation_id, release_key, service_id, environment, state, git_sha,
                    provider, provider_version_id, performance_budget_ref, performance_budget_ms,
                    recovery_strategy, artifact_digest, dependency_lock_digest, maker_actor,
                    maker_verification_ref, test_evidence_ref, security_evidence_ref,
                    verifier_actor, verifier_evidence_ref, rollback_ready, rollback_plan_ref,
                    plan_hash, approved_by_actor, approved_at, approval_expires_at, ended_at,
                    source_kind, source_ref)
               values (%s, %s, %s, 'production', 'complete', %s, %s, %s, 'budget:direct', 250,
                       'rollback', %s, %s, 'claude', 'maker', 'tests', 'security', 'codex',
                       'verifier', true, 'rollback', 'plan:direct-complete', 'joe', %s, %s, %s,
                       'wrapper', 'gate')""",
            (uuid.uuid4(), f"program5-complete-{uuid.uuid4()}", service_id, SHA, PROVIDER,
             PROVIDER_VERSION, "sha256:" + "c" * 64, "sha256:" + "d" * 64,
             now, now + timedelta(hours=1), now),
            "direct Production completion without receipts is refused",
        )

        retarget_id, _ = seed_release(cur, service_id, now, environment="staging")
        cur.execute("delete from ops.run where release_id = %s and run_key like 'recovery.rehearsal.%%'", (retarget_id,))
        cur.execute("update ops.release set state = 'approved' where id = %s", (retarget_id,))
        refuses(
            cur,
            "update ops.release set environment = 'production', plan_hash = 'plan:retargeted' where id = %s",
            (retarget_id,),
            "retargeting an approved release to Production without a recovery rehearsal is refused",
        )

        mismatch_id, _ = seed_release(cur, service_id, now)
        cur.execute("delete from ops.run where release_id = %s and run_key like 'recovery.rehearsal.%%'", (mismatch_id,))
        insert_run(cur, service_id, now, release_id=mismatch_id, environment="staging",
                   run_key="recovery.rehearsal.worker", recovery_strategy="forward_fix",
                   recovery_plan_ref="runbooks/other.md")
        refuses(
            cur,
            "update ops.release set state = 'approved' where id = %s",
            (mismatch_id,),
            "Production approval with a recovery receipt for another strategy or plan is refused",
        )

        # Receipt shapes refuse missing identity/evidence and prevent a passing
        # duration from being asserted for the wrong environment or budget.
        receipt_release_id, _ = seed_release(cur, service_id, now)
        cur.execute("update ops.release set state = 'approved' where id = %s",
                    (receipt_release_id,))
        receipt_corr = uuid.uuid4()
        insert_deployment(cur, service_id, receipt_release_id, receipt_corr, now,
                          environment="production", git_sha=SHA,
                          state="verifying", read_back=True)
        for kwargs, description in (
            ({"release_id": None}, "missing a release link"),
            ({"evidence_ref": None}, "missing evidence"),
            ({"budget_ms": None}, "missing a budget"),
            ({"duration_ms": 0}, "with a zero measured duration"),
            ({"duration_ms": 251}, "over budget"),
            ({"environment": "staging"}, "from the wrong environment"),
        ):
            duration_ms = kwargs.get("duration_ms", 1)
            assert isinstance(duration_ms, int)
            refuses(
                cur,
                """insert into ops.run (correlation_id, kind, service_id, release_id, environment,
                                         run_key, state, started_at, ended_at, budget_ms,
                                         source_kind, source_ref, evidence_ref)
                   values (%s, 'check', %s, %s, %s, 'performance.api', 'succeeded', %s, %s,
                           %s, 'collector', 'gate', %s)""",
                (receipt_corr, service_id,
                 kwargs["release_id"] if "release_id" in kwargs else receipt_release_id,
                 kwargs.get("environment", "production"), now,
                 now + timedelta(milliseconds=duration_ms),
                 kwargs.get("budget_ms", 250), kwargs.get("evidence_ref", "evidence:ok")),
                f"performance receipt {description} is refused",
            )
        recovery_release_id, _ = seed_release(cur, service_id, now)
        for environment, evidence, description in (
            ("staging", None, "missing evidence"),
            ("production", "evidence:bad-env", "in production"),
        ):
            refuses(
                cur,
                """insert into ops.run (correlation_id, kind, service_id, release_id, environment,
                                         run_key, state, started_at, ended_at, source_kind, source_ref,
                                         evidence_ref, recovery_strategy, recovery_plan_ref)
                   values (%s, 'check', %s, %s, %s, 'recovery.rehearsal.worker', 'succeeded',
                           %s, %s, 'collector', 'gate', %s, 'rollback', 'runbooks/rollback-worker.md')""",
                (uuid.uuid4(), service_id, recovery_release_id, environment, now, now, evidence),
                f"recovery rehearsal receipt {description} is refused",
            )

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
                insert_assurance_runs(cur, service_id, release_id, now, correlation_id=correlation)
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
            refuses(
                cur,
                """update ops.release
                      set state = %s, provider = null, provider_version_id = null,
                          ended_at = case when %s = 'complete' then %s else null end
                    where id = %s""",
                (state, state, now, release_id),
                f"Production {state} release without provider identity is refused",
            )

        # A Production deployment must be exactly the release that was approved,
        # not merely an approved release in the same service family.
        release_id, correlation = seed_release(cur, service_id, now)
        cur.execute("update ops.release set state = 'approved' where id = %s", (release_id,))
        refuses(
            cur,
            "update ops.release set performance_budget_ms = 500 where id = %s",
            (release_id,),
            "approved Production release performance budget is immutable",
        )
        refuses(
            cur,
            "update ops.release set recovery_strategy = 'forward_fix' where id = %s",
            (release_id,),
            "approved Production release recovery strategy is immutable",
        )
        refuses(
            cur,
            "update ops.release set rollback_plan_ref = 'runbooks/other.md' where id = %s",
            (release_id,),
            "approved Production release recovery plan is immutable",
        )
        refuses(
            cur,
            "update ops.release set provider_version_id = %s where id = %s",
            (OTHER_PROVIDER_VERSION, release_id),
            "approved Production release provider version is immutable",
        )
        refuses(
            cur,
            """insert into ops.deployment
                   (correlation_id, service_id, environment, state, git_sha, provider,
                    provider_version_id, release_id, started_at, source_kind, source_ref)
               values (%s, %s, 'production', 'deploying', %s, %s, %s, %s, %s,
                       'wrapper', 'ops/program5-promotion-gate.py')""",
            (correlation, service_id, OTHER_SHA, PROVIDER, PROVIDER_VERSION,
             release_id, now),
            "Production deployment with a different SHA is refused",
        )
        refuses(
            cur,
            """insert into ops.deployment
                   (correlation_id, service_id, environment, state, git_sha, provider,
                    provider_version_id, release_id, started_at, source_kind, source_ref)
               values (%s, %s, 'production', 'deploying', %s, %s, %s, %s, %s,
                       'wrapper', 'ops/program5-promotion-gate.py')""",
            (correlation, service_id, SHA, PROVIDER, OTHER_PROVIDER_VERSION,
             release_id, now),
            "Production deployment with a different provider version is refused",
        )
        deployment_id = insert_deployment(
            cur, service_id, release_id, correlation, now,
            environment="production", git_sha=SHA, state="deploying")
        refuses(
            cur,
            "update ops.deployment set provider_version_id = %s where id = %s",
            (OTHER_PROVIDER_VERSION, deployment_id),
            "promoted Production deployment provider version is immutable",
        )
        release_id, correlation = seed_release(
            cur, service_id, now, environment="staging")
        cur.execute("update ops.release set state = 'approved' where id = %s", (release_id,))
        refuses(
            cur,
            """insert into ops.deployment
                   (correlation_id, service_id, environment, state, git_sha, provider,
                    provider_version_id, release_id, started_at, source_kind, source_ref)
               values (%s, %s, 'production', 'deploying', %s, %s, %s, %s, %s,
                       'wrapper', 'ops/program5-promotion-gate.py')""",
            (correlation, service_id, SHA, PROVIDER, PROVIDER_VERSION,
             release_id, now),
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

        # Completion consumes receipts for this release only.  A failed probe,
        # a receipt belonging to another release, or a deleted prerequisite is
        # never enough to turn a serving version into a complete release.
        release_id, correlation = seed_release(cur, service_id, now)
        cur.execute("update ops.release set state = 'approved' where id = %s", (release_id,))
        cur.execute("delete from ops.run where release_id = %s and run_key like 'performance.%%'", (release_id,))
        insert_deployment(cur, service_id, release_id, correlation, now,
                          environment="production", git_sha=SHA, state="complete", read_back=True)
        insert_run(cur, service_id, now, release_id=release_id, state="failed",
                   correlation_id=correlation)
        refuses(cur, "update ops.release set state = 'complete', ended_at = %s where id = %s",
                (now, release_id), "completion with a failed performance receipt is refused")

        release_id, correlation = seed_release(cur, service_id, now)
        other_release_id, _ = seed_release(cur, service_id, now)
        cur.execute("update ops.release set state = 'approved' where id = %s", (release_id,))
        cur.execute("delete from ops.run where release_id = %s and run_key like 'performance.%%'", (release_id,))
        insert_deployment(cur, service_id, release_id, correlation, now,
                          environment="production", git_sha=SHA, state="complete", read_back=True)
        refuses(cur, "update ops.release set state = 'complete', ended_at = %s where id = %s",
                (now, release_id), "completion with a performance receipt for a different release is refused")

        release_id, correlation = seed_release(cur, service_id, now)
        cur.execute("update ops.release set state = 'approved' where id = %s", (release_id,))
        cur.execute("delete from ops.run where release_id = %s and run_key like 'recovery.rehearsal.%%'", (release_id,))
        insert_run(cur, service_id, now, release_id=release_id, environment="staging",
                   run_key="recovery.rehearsal.worker", state="failed", budget_ms=None,
                   recovery_strategy="rollback", recovery_plan_ref="runbooks/rollback-worker.md")
        insert_deployment(cur, service_id, release_id, correlation, now,
                          environment="production", git_sha=SHA, state="complete", read_back=True)
        refuses(cur, "update ops.release set state = 'complete', ended_at = %s where id = %s",
                (now, release_id), "completion with a failed recovery rehearsal is refused")

        release_id, correlation = seed_release(cur, service_id, now)
        other_release_id, _ = seed_release(cur, service_id, now)
        cur.execute("update ops.release set state = 'approved' where id = %s", (release_id,))
        cur.execute("delete from ops.run where release_id = %s and run_key like 'recovery.rehearsal.%%'", (release_id,))
        insert_deployment(cur, service_id, release_id, correlation, now,
                          environment="production", git_sha=SHA, state="complete", read_back=True)
        refuses(cur, "update ops.release set state = 'complete', ended_at = %s where id = %s",
                (now, release_id), "completion with a recovery rehearsal for a different release is refused")

        # Positive control: the exact approved Production release completes only
        # after its matching complete deployment supplies a live read-back.
        release_id, correlation = seed_release(cur, service_id, now)
        cur.execute("update ops.release set state = 'approved' where id = %s", (release_id,))
        insert_deployment(cur, service_id, release_id, correlation, now,
                          environment="production", git_sha=SHA,
                          state="complete", read_back=True)
        insert_assurance_runs(cur, service_id, release_id, now, correlation_id=correlation)
        cur.execute(
            "update ops.release set state = 'complete', ended_at = %s where id = %s",
            (now, release_id))
        cur.execute(
            """select r.state, r.provider, r.provider_version_id, d.environment,
                      d.state, d.git_sha, d.provider, d.provider_version_id, d.read_back_at
                 from ops.release r
                 join ops.deployment d on d.release_id = r.id
                where r.id = %s""",
            (release_id,))
        (state, release_provider, release_version, environment, deploy_state,
         deploy_sha, deployment_provider, deployment_version, read_back_at) = fetch_one(cur)
        check(
            "matching Production deployment with read-back completes the release",
            state == "complete" and environment == "production"
            and deploy_state == "complete" and deploy_sha == SHA
            and release_provider == deployment_provider == PROVIDER
            and release_version == deployment_version == PROVIDER_VERSION
            and read_back_at is not None,
            f"release={state}/{release_provider}/{release_version} "
            f"deployment={environment}/{deploy_state}/{deploy_sha}/"
            f"{deployment_provider}/{deployment_version}/{read_back_at}",
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
