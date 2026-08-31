#!/usr/bin/env python3
# ci: runs-outside-ci — invoked by ops/local-pg-ci.py after canonical CI so committed concurrency fixtures cannot contaminate other DB gates
# doctrine: runbook
"""Disposable-Postgres proof for the dark canonical ownership lease kernel."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import sys
from threading import Barrier, Thread
import uuid

import psycopg
from psycopg.types.json import Jsonb


ROOT = Path(__file__).resolve().parents[1]
ACQUIRE_SQL = """select ops.acquire_canonical_ownership_lease(
  %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"""


def load_controller_gate():
    path = ROOT / "ops/zz-engineering-controller-concurrency-gate.py"
    spec = importlib.util.spec_from_file_location("ownership_controller_fixture", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load the canonical Engineering fixture")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cc = load_controller_gate()
fixture = cc.load_fixture()


def one(cur, query: str, args: tuple = ()):
    row = cur.execute(query, args).fetchone()
    if row is None:
        raise RuntimeError(f"ownership gate expected one row: {query[:120]}")
    return row


def sha(seed: str) -> str:
    return "sha256:" + (seed * 64)[:64]


def context(cur, tenant: str, actor: str = "joe", session: str | None = None) -> None:
    values = {
        "carr.organization_tenant_id": tenant,
        "carr.acting_actor_slug": actor,
        "carr.ownership_session_id": session or f"session:a2:{actor}:{uuid.uuid4().hex}",
        "carr.execution_host_id": "host:a2-disposable-pg",
    }
    for key, value in values.items():
        one(cur, "select set_config(%s,%s,false)", (key, value))


def binding(cur, envelope_id) -> tuple:
    return tuple(
        one(
            cur,
            """select e.work_request_id,w.version,
                      source->'work_request'->>'canonical_record_digest',
                      e.accepted_plan_id,source->'accepted_plan'->>'digest',
                      e.slice_plan_id,sp.plan_digest,e.slice_ref
                 from ops.engineering_execution_envelope e
                 join ops.work_request w on w.id=e.work_request_id
                 join ops.engineering_slice_plan sp on sp.id=e.slice_plan_id
                cross join lateral ops.engineering_admission_source(w.ref) source
                where e.id=%s""",
            (envelope_id,),
        )
    )


def acquire(
    cur,
    bound: tuple,
    *,
    paths: list[dict] | None = None,
    resources: list[dict] | None = None,
    dependencies: list[dict] | None = None,
    ttl: int = 900,
    contract: str = sha("9"),
):
    return one(
        cur,
        ACQUIRE_SQL,
        (
            *bound,
            contract,
            Jsonb(paths or []),
            Jsonb(resources or []),
            Jsonb(dependencies or []),
            ttl,
        ),
    )[0]


def refusal(value, code: str, label: str) -> None:
    actual = value.get("refusal", {}).get("code")
    if value.get("ok") is not False or actual != code:
        raise RuntimeError(f"{label}: expected {code}, got {value}")
    if set(value["refusal"]) != {"code", "causal_object", "expected", "actual"}:
        raise RuntimeError(f"{label}: refusal shape drifted: {value}")


def insert_review(cur, fixture_row, receipt_id) -> None:
    work_request_id = one(
        cur,
        "select work_request_id from ops.engineering_execution_envelope where id=%s",
        (fixture_row[1],),
    )[0]
    joe_id = one(cur, "select id from actor where slug='joe' and active and kind='human'")[0]
    fact = cc.reviewer_fact_payload(fixture_row[5])
    cur.execute("set local role carr_writer")
    cur.execute(
        """insert into ops.engineering_reviewer_fact
             (receipt_id,work_request_id,slice_ref,reviewer_actor_id,
              reviewer_session_ref,state,fact,idempotency_key)
           values (%s,%s,%s,%s,%s,'passed',%s,%s)""",
        (
            receipt_id,
            work_request_id,
            fixture_row[5],
            joe_id,
            fact["session_ref"],
            Jsonb(fact),
            uuid.uuid4(),
        ),
    )
    cur.execute("reset role")


def seed_lineage(conn, tenant: str, label: str, *, reviewed: bool = True):
    dependency_ref = f"slice:{label}:dependency"
    subject_ref = f"slice:{label}:subject"
    with conn.cursor() as cur:
        context(cur, tenant)
        dependency = fixture(
            cur,
            slice_refs=[dependency_ref, subject_ref],
            slice_dependencies={subject_ref: [dependency_ref]},
        )
    conn.commit()
    claim = cc.claim_one(conn, dependency[0], f"ownership-{label}", [dependency[0]])
    with conn.cursor() as cur:
        cc.set_jobs(cur)
        receipt_id = cc.receipt(cur, dependency, claim, "claimed_complete")
        cc.reset_role(cur)
    conn.commit()
    if reviewed:
        with conn.cursor() as cur:
            insert_review(cur, dependency, receipt_id)
        conn.commit()
        subject = cc.create_dag_b_after_exact_review(conn, dependency, subject_ref)
    else:
        subject = None
    return dependency, subject, claim, receipt_id


def seed_single(conn, tenant: str, label: str):
    with conn.cursor() as cur:
        context(cur, tenant)
        row = fixture(cur, slice_refs=[f"slice:{label}"])
    conn.commit()
    return row


def race(left, right, label: str):
    barrier = Barrier(2)
    results: list[tuple[str, object]] = []

    def run(name, fn):
        try:
            barrier.wait(timeout=10)
            results.append((name, fn()))
        except BaseException as exc:  # reported with the exact peer name
            results.append((name, exc))

    threads = [Thread(target=run, args=("writer", left), daemon=True),
               Thread(target=run, args=("lease", right), daemon=True)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(15)
    if any(thread.is_alive() for thread in threads):
        raise RuntimeError(f"{label}: peer did not finish (deadlock or unbounded wait)")
    failures = [(name, value) for name, value in results if isinstance(value, BaseException)]
    if failures:
        raise RuntimeError(f"{label}: peer failed: {failures}")
    return dict(results)


def main() -> int:
    dsn = os.environ.get("CARR_LOCAL_PG_DSN", "")
    if not dsn:
        raise RuntimeError(
            "canonical ownership gate requires disposable CARR_LOCAL_PG_DSN"
        )
    assertions = 0

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cc.hard_fence(cur, dsn)
        # A3 owns trusted context production and runtime grants. Missing
        # identity must therefore fail closed in this deliberately dark slice.
        refusal(one(cur, "select ops.canonical_ownership_context()")[0],
                "IDENTITY_CONTEXT_MISSING", "dark context")
        assertions += 1
        try:
            one(
                cur,
                "select ops.canonical_ownership_refusal('UNREGISTERED','x','null','null')",
            )
        except psycopg.Error as exc:
            conn.rollback()
            if "not registered" not in str(exc):
                raise
        else:
            raise RuntimeError("unregistered refusal code was accepted")

    tenant = f"a2-tenant-{uuid.uuid4().hex}"
    with psycopg.connect(dsn) as conn:
        dependency, subject, _claim, _receipt = seed_lineage(conn, tenant, "primary")
        assert subject is not None
        with conn.cursor() as cur:
            context(cur, tenant, "joe", "session:a2:primary:joe")
            bound = binding(cur, subject[1])
            cur.execute("savepoint assigned_tenant_currentness")
            cur.execute(
                "alter table ops.work_request drop constraint work_request_sourced_capture_shape"
            )
            cur.execute(
                "update ops.work_request set organization_tenant_id=%s where id=%s",
                (tenant, bound[0]),
            )
            assigned = one(
                cur,
                "select ops.canonical_ownership_currentness(%s,%s,%s,%s,%s,%s,%s,%s)",
                bound,
            )[0]
            if assigned.get("ok") is not True:
                raise RuntimeError(f"assigned tenant was not current: {assigned}")
            context(cur, f"foreign-{tenant}", "joe", "session:a2:primary:joe")
            refusal(
                one(
                    cur,
                    "select ops.canonical_ownership_currentness(%s,%s,%s,%s,%s,%s,%s,%s)",
                    bound,
                )[0],
                "WORK_REQUEST_NOT_FOUND",
                "foreign assigned tenant",
            )
            cur.execute("rollback to savepoint assigned_tenant_currentness")
            cur.execute("release savepoint assigned_tenant_currentness")
            assertions += 2
            path_claims = [
                {"path": "migrations/0450_canonical_ownership_lease_kernel.sql",
                 "mode": "file", "operation": "write"},
                {"path": "ops/ownership", "mode": "tree", "operation": "rename_source"},
                {"path": "ops/ownership-renamed", "mode": "tree",
                 "operation": "rename_destination"},
            ]
            resources = [{"resource": "migration:0450"}]
            deps = [{"slice_ref": dependency[5], "required_state": "independently_verified"}]

            refusal(acquire(cur, bound, ttl=1), "INPUT_INVALID", "ttl precedence")
            refusal(
                acquire(cur, bound, paths=[{"path": "../escape", "mode": "file",
                                            "operation": "write"}]),
                "PATH_INVALID",
                "repo-relative path",
            )
            refusal(
                acquire(cur, bound, paths=[
                    {"path": "Ops/A", "mode": "file", "operation": "write"},
                    {"path": "ops/B", "mode": "file", "operation": "write"},
                ]),
                "PATH_CASE_ALIAS",
                "case-fold alias",
            )
            refusal(
                acquire(cur, bound, resources=[{"resource": "résource:bad"}]),
                "RESOURCE_INVALID",
                "ASCII resource",
            )
            duplicate = {"path": "ops/duplicate.py", "mode": "file", "operation": "write"}
            refusal(acquire(cur, bound, paths=[duplicate, duplicate]),
                    "DUPLICATE_CLAIM", "duplicate path")
            refusal(
                acquire(cur, bound, dependencies=[
                    {"slice_ref": "slice:a2:missing", "required_state": "completed"}
                ]),
                "DEPENDENCY_MISSING",
                "missing dependency",
            )
            refusal(
                acquire(cur, bound, dependencies=[
                    {"slice_ref": subject[5], "required_state": "completed"}
                ]),
                "DEPENDENCY_UNSATISFIED",
                "uncompleted dependency",
            )
            lease = acquire(
                cur, bound, paths=path_claims, resources=resources, dependencies=deps
            )
            if lease.get("ok") is not True or set(lease) != {
                "ok", "lease_id", "lease_token", "fencing_generation", "expires_at"
            }:
                raise RuntimeError(f"acquire result drifted: {lease}")
            lease_id = lease["lease_id"]
            lease_token = lease["lease_token"]
            generation = lease["fencing_generation"]
            assertions += 8
        conn.commit()

        with conn.cursor() as cur:
            context(cur, tenant, "dell")
            refusal(
                acquire(
                    cur,
                    bound,
                    paths=[{"path": "ops/ownership/child.py", "mode": "file",
                            "operation": "write"}],
                ),
                "FOREIGN_LEASE_COLLISION",
                "tree ancestry collision",
            )
            refusal(
                acquire(cur, bound, resources=resources),
                "FOREIGN_LEASE_COLLISION",
                "resource collision",
            )
            holder = one(
                cur,
                "select ops.check_canonical_ownership_lease(%s,%s,%s)",
                (lease_id, lease_token, generation),
            )[0]
            refusal(holder, "LEASE_HOLDER_MISMATCH", "Joe and Dell identity")
            context(cur, tenant, "joe", "session:a2:primary:changed")
            refusal(
                one(
                    cur,
                    "select ops.check_canonical_ownership_lease(%s,%s,%s)",
                    (lease_id, lease_token, generation),
                )[0],
                "LEASE_HOLDER_MISMATCH",
                "changed holder session",
            )
            assertions += 4

            context(cur, tenant, "joe", "session:a2:primary:joe")
            refusal(
                one(cur, "select ops.check_canonical_ownership_lease(%s,%s,%s)",
                    (lease_id, uuid.uuid4(), generation))[0],
                "LEASE_TOKEN_STALE",
                "stale token",
            )
            refusal(
                one(cur, "select ops.check_canonical_ownership_lease(%s,%s,%s)",
                    (lease_id, lease_token, generation + 1))[0],
                "FENCING_GENERATION_STALE",
                "stale fencing generation",
            )
            refusal(
                one(
                    cur,
                    "select ops.check_canonical_ownership_lease(%s,%s,%s,%s,%s)",
                    (
                        lease_id,
                        lease_token,
                        generation,
                        Jsonb([{"path": "ops/not-owned.py", "mode": "file",
                                "operation": "write"}]),
                        Jsonb([]),
                    ),
                )[0],
                "LEASE_CLAIMS_MISMATCH",
                "claim subset",
            )
            renewed = one(
                cur,
                "select ops.renew_canonical_ownership_lease(%s,%s,%s,600)",
                (lease_id, lease_token, generation),
            )[0]
            if renewed.get("ok") is not True:
                raise RuntimeError(f"renewal failed: {renewed}")
            released = one(
                cur,
                "select ops.release_canonical_ownership_lease(%s,%s,%s)",
                (lease_id, lease_token, generation),
            )[0]
            if released.get("state") != "released":
                raise RuntimeError(f"release failed: {released}")
            refusal(
                one(cur, "select ops.check_canonical_ownership_lease(%s,%s,%s)",
                    (lease_id, lease_token, generation))[0],
                "LEASE_RELEASED",
                "released lease",
            )
            assertions += 6
        conn.commit()

        # Expired scopes can be reacquired, but never revived. The new grant
        # receives a strictly larger fence and preserves expired/replaced facts.
        with conn.cursor() as cur:
            context(cur, tenant, "joe", "session:a2:expiry:joe")
            expiring = acquire(
                cur,
                bound,
                paths=[{"path": "ops/expired.py", "mode": "file", "operation": "write"}],
                dependencies=deps,
            )
            expired_update = cur.execute(
                """update ops.canonical_ownership_lease
                      set acquired_at=clock_timestamp()-interval '2 hours',
                          expires_at=clock_timestamp()-interval '1 hour',
                          updated_at=clock_timestamp()
                    where id=%s""",
                (expiring["lease_id"],),
            )
            if expired_update.rowcount != 1:
                raise RuntimeError("expiry fixture did not update exactly one lease")
            context(cur, tenant, "dell", "session:a2:expiry:dell")
            replacement = acquire(
                cur,
                bound,
                paths=[{"path": "ops/expired.py", "mode": "file", "operation": "write"}],
                dependencies=deps,
            )
            if replacement.get("ok") is not True or replacement["fencing_generation"] <= expiring["fencing_generation"]:
                raise RuntimeError("reacquisition did not mint a monotonic fence")
            state = one(
                cur,
                "select state,superseded_by_lease_id from ops.canonical_ownership_lease where id=%s",
                (expiring["lease_id"],),
            )
            if state[0] != "replaced" or str(state[1]) != str(replacement["lease_id"]):
                raise RuntimeError(f"expired predecessor was not replaced: {state}")
            events = one(
                cur,
                """select array_agg(event_kind order by id)
                     from ops.canonical_ownership_lease_event where lease_id=%s""",
                (expiring["lease_id"],),
            )[0]
            if events[-2:] != ["expired", "replaced"]:
                raise RuntimeError(f"expiry/replacement audit sequence drifted: {events}")
            assertions += 2
        conn.commit()

        # A separate tenant can hold byte-identical claims; it cannot observe
        # or collide with the first tenant's rows.
        other_tenant = f"a2-other-{uuid.uuid4().hex}"
        other_dep, other_subject, *_ = seed_lineage(conn, other_tenant, "other")
        assert other_subject is not None
        with conn.cursor() as cur:
            context(cur, other_tenant, "joe")
            other = acquire(
                cur,
                binding(cur, other_subject[1]),
                paths=path_claims,
                resources=resources,
                dependencies=[{"slice_ref": other_dep[5],
                               "required_state": "independently_verified"}],
            )
            if other.get("ok") is not True:
                raise RuntimeError(f"tenant isolation rejected independent claim: {other}")
            if one(
                cur,
                """select count(*) from ops.canonical_ownership_lease
                    where id=%s and organization_tenant_id=current_setting(
                      'carr.organization_tenant_id')""",
                (replacement["lease_id"],),
            )[0] != 0:
                raise RuntimeError("cross-tenant lease became visible")
            assertions += 2
        conn.commit()

    # Atomic same-tenant acquisition: exactly one winner, one typed collision.
    concurrency_tenant = f"a2-race-{uuid.uuid4().hex}"
    with psycopg.connect(dsn) as setup:
        race_dep, race_subject, *_ = seed_lineage(setup, concurrency_tenant, "atomic")
        assert race_subject is not None
        race_bound = binding(setup.cursor(), race_subject[1])
    race_claim = [{"path": "ops/atomic-owner.py", "mode": "file", "operation": "write"}]

    def acquire_peer(actor):
        with psycopg.connect(dsn) as peer, peer.cursor() as cur:
            context(cur, concurrency_tenant, actor)
            value = acquire(
                cur,
                race_bound,
                paths=race_claim,
                dependencies=[{"slice_ref": race_dep[5],
                               "required_state": "independently_verified"}],
            )
            peer.commit()
            return value

    atomic = race(lambda: acquire_peer("joe"), lambda: acquire_peer("dell"),
                  "atomic acquisition")
    codes = sorted(
        "OK" if value.get("ok") else value["refusal"]["code"]
        for value in atomic.values()
    )
    if codes != ["FOREIGN_LEASE_COLLISION", "OK"]:
        raise RuntimeError(f"atomic acquisition had wrong outcomes: {atomic}")
    assertions += 1

    # Real writers and A2 take the same session -> actor -> lineage ordering.
    receipt_tenant = f"a2-receipt-race-{uuid.uuid4().hex}"
    with psycopg.connect(dsn) as setup:
        receipt_fixture = seed_single(setup, receipt_tenant, "receipt-race")
        receipt_claim = cc.claim_one(
            setup, receipt_fixture[0], "ownership-receipt-race", [receipt_fixture[0]]
        )
        receipt_bound = binding(setup.cursor(), receipt_fixture[1])

    def append_receipt():
        with psycopg.connect(dsn) as peer, peer.cursor() as cur:
            cc.set_jobs(cur)
            result = cc.receipt(cur, receipt_fixture, receipt_claim, "claimed_complete")
            cc.reset_role(cur)
            peer.commit()
            return result

    def acquire_during_receipt():
        with psycopg.connect(dsn) as peer, peer.cursor() as cur:
            context(cur, receipt_tenant)
            value = acquire(
                cur,
                receipt_bound,
                paths=[{"path": "ops/receipt-race.py", "mode": "file",
                        "operation": "write"}],
                dependencies=[{"slice_ref": receipt_fixture[5],
                               "required_state": "completed"}],
            )
            peer.commit()
            return value

    receipt_race = race(append_receipt, acquire_during_receipt, "receipt append")
    lease_outcome = receipt_race["lease"]
    if not lease_outcome.get("ok") and lease_outcome["refusal"]["code"] != "DEPENDENCY_UNSATISFIED":
        raise RuntimeError(f"receipt race crossed a noncausal boundary: {receipt_race}")
    assertions += 1

    review_tenant = f"a2-review-race-{uuid.uuid4().hex}"
    with psycopg.connect(dsn) as setup:
        review_fixture = seed_single(setup, review_tenant, "review-race")
        review_claim = cc.claim_one(
            setup, review_fixture[0], "ownership-review-race", [review_fixture[0]]
        )
        with setup.cursor() as cur:
            cc.set_jobs(cur)
            review_receipt = cc.receipt(
                cur, review_fixture, review_claim, "claimed_complete"
            )
            cc.reset_role(cur)
        setup.commit()
        review_bound = binding(setup.cursor(), review_fixture[1])

    def append_review():
        with psycopg.connect(dsn) as peer, peer.cursor() as cur:
            insert_review(cur, review_fixture, review_receipt)
            peer.commit()
            return "reviewed"

    def acquire_during_review():
        with psycopg.connect(dsn) as peer, peer.cursor() as cur:
            context(cur, review_tenant)
            value = acquire(
                cur,
                review_bound,
                paths=[{"path": "ops/review-race.py", "mode": "file",
                        "operation": "write"}],
                dependencies=[{"slice_ref": review_fixture[5],
                               "required_state": "independently_verified"}],
            )
            peer.commit()
            return value

    review_race = race(append_review, acquire_during_review, "review append")
    lease_outcome = review_race["lease"]
    if not lease_outcome.get("ok") and lease_outcome["refusal"]["code"] != "DEPENDENCY_UNSATISFIED":
        raise RuntimeError(f"review race crossed a noncausal boundary: {review_race}")
    assertions += 1

    successor_tenant = f"a2-successor-race-{uuid.uuid4().hex}"
    with psycopg.connect(dsn) as setup:
        successor_fixture = seed_single(setup, successor_tenant, "successor-race")
        successor_claim = cc.claim_one(
            setup,
            successor_fixture[0],
            "ownership-successor-race",
            [successor_fixture[0]],
        )
        with setup.cursor() as cur:
            cc.set_jobs(cur)
            successor_receipt = cc.receipt(
                cur, successor_fixture, successor_claim, "claimed_complete"
            )
            cc.reset_role(cur)
        setup.commit()
        with setup.cursor() as cur:
            insert_review(cur, successor_fixture, successor_receipt)
        setup.commit()
        successor_bound = binding(setup.cursor(), successor_fixture[1])
        with setup.cursor() as cur:
            context(
                cur,
                successor_tenant,
                "joe",
                "session:a2:successor-race:joe",
            )
            successor_lease = acquire(
                cur,
                successor_bound,
                paths=[{"path": "ops/successor-race.py", "mode": "file",
                        "operation": "write"}],
                dependencies=[{"slice_ref": successor_fixture[5],
                               "required_state": "independently_verified"}],
            )
        setup.commit()

    def append_successor():
        with psycopg.connect(dsn) as peer, peer.cursor() as cur:
            result = cc.insert_successor(cur, successor_fixture, cancel_prior=True)
            peer.commit()
            return result[1]

    def check_during_successor():
        with psycopg.connect(dsn) as peer, peer.cursor() as cur:
            context(
                cur,
                successor_tenant,
                "joe",
                "session:a2:successor-race:joe",
            )
            value = one(
                cur,
                "select ops.check_canonical_ownership_lease(%s,%s,%s)",
                (
                    successor_lease["lease_id"],
                    successor_lease["lease_token"],
                    successor_lease["fencing_generation"],
                ),
            )[0]
            peer.commit()
            return value

    successor_race = race(
        append_successor, check_during_successor, "successor insertion"
    )
    lease_outcome = successor_race["lease"]
    if not lease_outcome.get("ok") and lease_outcome["refusal"]["code"] not in {
        "DEPENDENCY_MISSING", "DEPENDENCY_UNSATISFIED"
    }:
        raise RuntimeError(f"successor race crossed a noncausal boundary: {successor_race}")
    assertions += 1

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        private_functions = [
            "ops.acquire_canonical_ownership_lease(uuid,integer,text,uuid,text,uuid,text,text,text,jsonb,jsonb,jsonb,integer)",
            "ops.check_canonical_ownership_lease(uuid,uuid,bigint,jsonb,jsonb)",
            "ops.renew_canonical_ownership_lease(uuid,uuid,bigint,integer)",
            "ops.release_canonical_ownership_lease(uuid,uuid,bigint)",
            "ops.expire_canonical_ownership_leases()",
        ]
        for role in ("public", "carr_reader", "carr_writer", "carr_jobs", "carr_authority"):
            for function in private_functions:
                if one(
                    cur,
                    "select has_function_privilege(%s,%s::regprocedure,'EXECUTE')",
                    (role, function),
                )[0]:
                    raise RuntimeError(f"dark kernel grant leaked: {role} can execute {function}")
            for table in (
                "ops.canonical_ownership_lease",
                "ops.canonical_ownership_claim",
                "ops.canonical_ownership_dependency",
                "ops.canonical_ownership_lease_event",
            ):
                if one(cur, "select has_table_privilege(%s,%s,'SELECT')",
                       (role, table))[0]:
                    raise RuntimeError(f"dark kernel table leaked: {role} can read {table}")

        tokens = one(
            cur,
            """select coalesce(array_agg(lease_token::text),'{}'::text[])
                 from ops.canonical_ownership_lease""",
        )[0]
        evidence = one(
            cur,
            """select coalesce(string_agg(event_kind||cause::text||session_ref||
                                         host_ref,E'\n'),'')
                 from ops.canonical_ownership_lease_event""",
        )[0]
        if any(token in evidence for token in tokens):
            raise RuntimeError("raw lease token leaked into lifecycle evidence")
        if one(
            cur,
            """select count(*) from information_schema.columns
                where table_schema='ops'
                  and table_name in ('canonical_ownership_claim',
                                     'canonical_ownership_dependency',
                                     'canonical_ownership_lease_event')
                  and column_name='lease_token'""",
        )[0] != 0:
            raise RuntimeError("raw token escaped the private lease row")

        for table in ("canonical_ownership_claim", "canonical_ownership_dependency",
                      "canonical_ownership_lease_event"):
            cur.execute("savepoint append_only")
            try:
                cur.execute(f"delete from ops.{table}")
            except psycopg.Error as exc:
                cur.execute("rollback to savepoint append_only")
                cur.execute("release savepoint append_only")
                if "append-only" not in str(exc):
                    raise
            else:
                raise RuntimeError(f"{table} accepted destructive cleanup")
        assertions += 3

    print(f"canonical ownership lease local PG gate — {assertions} assertion groups passed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"canonical-ownership-lease-local-pg-gate: FAIL — {exc}", file=sys.stderr)
        raise
