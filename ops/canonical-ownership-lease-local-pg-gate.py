#!/usr/bin/env python3
# ci: runs-outside-ci — invoked by ops/local-pg-ci.py after canonical CI so committed concurrency fixtures cannot contaminate other DB gates
# doctrine: runbook
"""Disposable-Postgres proof for the dark canonical ownership lease kernel."""

from __future__ import annotations

import importlib.util
import io
import json
import os
from pathlib import Path
import sys
from contextlib import redirect_stderr, redirect_stdout
from threading import Barrier, Thread
import uuid

import psycopg
from psycopg.types.json import Jsonb


ROOT = Path(__file__).resolve().parents[1]
ACQUIRE_SQL = """select ops.acquire_canonical_ownership_lease(
  %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"""
RESPONSES: list[object] = []
SECRET_TOKENS: set[str] = set()
CATALOG_FINGERPRINT_SQL = r"""
with table_targets(obj) as (values
  ('ops.work_request'),('ops.engineering_slice_plan'),('ops.job'),
  ('ops.capability_agent_session'),('ops.engineering_execution_envelope'),
  ('ops.engineering_slice_receipt'),('ops.engineering_reviewer_fact'),
  ('public.actor'),('public.lease'),('public.deal_presence_lease')
), function_targets(obj) as (values
  ('ops.engineering_admission_source(text)'),
  ('ops.engineering_record_slice_receipt(uuid,uuid,jsonb,text,uuid)'),
  ('ops.engineering_finalize_slice_receipt(uuid,uuid,jsonb,text,uuid)'),
  ('ops.guard_engineering_reviewer_fact_insert()'),
  ('ops.guard_engineering_envelope_supersession()')
)
select jsonb_build_object(
  'tables',(select jsonb_object_agg(t.obj,jsonb_build_object(
    'owner',pg_get_userbyid(c.relowner),'acl',coalesce(c.relacl::text,''),
    'rls',c.relrowsecurity,'force_rls',c.relforcerowsecurity,
    'reloptions',coalesce(to_jsonb(c.reloptions),'null'::jsonb),
    'columns',(select coalesce(jsonb_agg(jsonb_build_object(
      'number',a.attnum,'name',a.attname,'type',format_type(a.atttypid,a.atttypmod),
      'not_null',a.attnotnull,'default',pg_get_expr(d.adbin,d.adrelid),
      'identity',a.attidentity,'generated',a.attgenerated) order by a.attnum),'[]'::jsonb)
      from pg_attribute a left join pg_attrdef d on d.adrelid=a.attrelid and d.adnum=a.attnum
      where a.attrelid=c.oid and a.attnum>0 and not a.attisdropped),
    'constraints',(select coalesce(jsonb_agg(jsonb_build_object(
      'name',con.conname,'type',con.contype,'definition',pg_get_constraintdef(con.oid,true))
      order by con.conname),'[]'::jsonb) from pg_constraint con where con.conrelid=c.oid),
    'triggers',(select coalesce(jsonb_agg(jsonb_build_object(
      'name',tg.tgname,'enabled',tg.tgenabled,'definition',pg_get_triggerdef(tg.oid,true))
      order by tg.tgname),'[]'::jsonb) from pg_trigger tg where tg.tgrelid=c.oid and not tg.tgisinternal),
    'policies',(select coalesce(jsonb_agg(jsonb_build_object(
      'name',p.polname,'permissive',p.polpermissive,'roles',p.polroles,
      'command',p.polcmd,'using',pg_get_expr(p.polqual,p.polrelid),
      'check',pg_get_expr(p.polwithcheck,p.polrelid)) order by p.polname),'[]'::jsonb)
      from pg_policy p where p.polrelid=c.oid),
    'indexes',(select coalesce(jsonb_agg(pg_get_indexdef(i.indexrelid) order by i.indexrelid::regclass::text),'[]'::jsonb)
      from pg_index i where i.indrelid=c.oid)
  )) from table_targets t join pg_class c on c.oid=to_regclass(t.obj)),
  'functions',(select jsonb_object_agg(f.obj,jsonb_build_object(
    'definition',pg_get_functiondef(p.oid),'owner',pg_get_userbyid(p.proowner),
    'security_definer',p.prosecdef,'config',coalesce(to_jsonb(p.proconfig),'null'::jsonb),
    'acl',coalesce(p.proacl::text,'')))
    from function_targets f join pg_proc p on p.oid=to_regprocedure(f.obj))
)
"""


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
    cur.execute("set local lock_timeout='5s'")
    cur.execute("set local deadlock_timeout='100ms'")
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
    value = one(
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
    RESPONSES.append(value)
    return value


def refusal(
    value,
    code: str,
    label: str,
    *,
    causal_object,
    expected,
    actual,
) -> None:
    RESPONSES.append(value)
    actual_code = value.get("refusal", {}).get("code")
    if value.get("ok") is not False or actual_code != code:
        raise RuntimeError(f"{label}: refusal code mismatch")
    if set(value["refusal"]) != {"code", "causal_object", "expected", "actual"}:
        raise RuntimeError(f"{label}: refusal shape drifted")
    fields = value["refusal"]
    if fields["causal_object"] != causal_object:
        raise RuntimeError(f"{label}: causal_object drifted")
    if fields["expected"] != expected:
        raise RuntimeError(f"{label}: expected field drifted")
    if fields["actual"] != actual:
        raise RuntimeError(f"{label}: actual field drifted")


def assert_secret_absent(value, secret: str, label: str) -> None:
    rendered = json.dumps(value, default=str, sort_keys=True)
    if secret in rendered:
        raise RuntimeError(f"{label}: submitted token escaped")


def safe_outcome(value) -> str:
    if isinstance(value, dict):
        if value.get("ok") is True:
            return "OK"
        return str(value.get("refusal", {}).get("code", "MALFORMED"))
    return type(value).__name__


def safe_error(exc: BaseException) -> str:
    rendered = str(exc)
    for secret in SECRET_TOKENS:
        rendered = rendered.replace(secret, "<redacted-token>")
    return rendered


def catalog_fingerprint(cur):
    return one(cur, CATALOG_FINGERPRINT_SQL)[0]


def set_plan_dependencies(cur, plan_id, slice_ref: str, dependency_refs: list[str]) -> None:
    cur.execute(
        """update ops.engineering_slice_plan p
              set plan=jsonb_set(
                    p.plan,'{slices}',
                    (select jsonb_agg(
                       case when item->>'slice_ref'=%s
                            then jsonb_set(item,'{dependency_refs}',%s::jsonb,false)
                            else item end order by ordinal)
                       from jsonb_array_elements(p.plan->'slices')
                            with ordinality as entries(item,ordinal)),false)
            where p.id=%s""",
        (slice_ref, Jsonb(dependency_refs), plan_id),
    )
    if cur.rowcount != 1:
        raise RuntimeError("canonical dependency drift fixture missed its plan")


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
    failures = [(name, type(value).__name__) for name, value in results
                if isinstance(value, BaseException)]
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
                "IDENTITY_CONTEXT_MISSING", "dark context",
                causal_object="identity_context",
                expected=["organization_tenant_id", "acting_actor_slug",
                          "ownership_session_id", "execution_host_id"],
                actual={"organization_tenant_id": False, "acting_actor_slug": False,
                        "ownership_session_id": False, "execution_host_id": False})
        one(cur, "select set_config('carr.organization_tenant_id','a2-invalid',false)")
        one(cur, "select set_config('carr.acting_actor_slug','joe',false)")
        one(cur, "select set_config('carr.ownership_session_id','x',false)")
        one(cur, "select set_config('carr.execution_host_id','host:a2',false)")
        refusal(one(cur, "select ops.canonical_ownership_context()")[0],
                "IDENTITY_CONTEXT_INVALID", "invalid identity",
                causal_object="identity_context",
                expected="canonical server identity refs", actual="invalid")
        assertions += 2
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
        invalid_paths = (
            "/absolute", "ops/./file", "ops/../file", "ops//file",
            "ops\\file", "ops/résumé", "ops/file/",
        )
        for path in invalid_paths:
            if one(cur, "select ops.canonical_ownership_path_valid(%s)", (path,))[0]:
                raise RuntimeError("invalid path form was accepted")
        for path in ("ops/a_b", "ops/a%b", "ops/file"):
            if not one(cur, "select ops.canonical_ownership_path_valid(%s)", (path,))[0]:
                raise RuntimeError("literal ASCII path byte was rejected")
        overlap_cases = (
            ("ops/tree", "tree", "ops/tree/child", "file", True),
            ("ops/tree/child", "file", "ops/tree", "tree", True),
            ("ops/tree", "file", "ops/tree/child", "file", False),
            ("ops/a_b", "tree", "ops/acb/child", "file", False),
            ("ops/a%b", "tree", "ops/anything/child", "file", False),
            ("ops/exact", "file", "ops/exact", "file", True),
        )
        for left, left_mode, right, right_mode, expected_overlap in overlap_cases:
            if one(
                cur,
                "select ops.canonical_ownership_paths_overlap(%s,%s,%s,%s)",
                (left, left_mode, right, right_mode),
            )[0] is not expected_overlap:
                raise RuntimeError("literal path overlap semantics drifted")
        if not one(
            cur,
            "select ops.canonical_ownership_path_case_alias('Ops/A','ops/B')",
        )[0]:
            raise RuntimeError("slash-component case alias was missed")
        assertions += len(invalid_paths) + 10

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
                raise RuntimeError("assigned tenant currentness failed")
            RESPONSES.append(assigned)
            context(cur, f"foreign-{tenant}", "joe", "session:a2:primary:joe")
            refusal(
                one(
                    cur,
                    "select ops.canonical_ownership_currentness(%s,%s,%s,%s,%s,%s,%s,%s)",
                    bound,
                )[0],
                "WORK_REQUEST_NOT_FOUND",
                "foreign assigned tenant",
                causal_object="work_request",
                expected="tenant-visible canonical work request",
                actual="absent",
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
            stale_lookup = list(bound)
            stale_lookup[1] += 1
            refusal(acquire(cur, tuple(stale_lookup), ttl=1), "INPUT_INVALID",
                    "top-level input precedes lookup", causal_object="lease.input",
                    expected="bounded exact A2 input", actual="invalid")
            precedence_duplicate = {
                "path": "ops/precedence.py", "mode": "file", "operation": "write"
            }
            alias_paths = [
                {"path": "Ops/A", "mode": "file", "operation": "write"},
                {"path": "ops/B", "mode": "file", "operation": "write"},
                precedence_duplicate, precedence_duplicate,
            ]
            invalid_resource = [{"resource": "résource:precedence"}]
            refusal(acquire(cur, bound, paths=[
                        {"path": "../escape", "mode": "file", "operation": "write"},
                        *alias_paths], resources=invalid_resource, dependencies=deps),
                    "PATH_INVALID", "path precedes case/resource/duplicate",
                    causal_object="path_claim", expected="exact A1a path claim",
                    actual={"field": "path_claim", "reason": "invalid",
                            "value_redacted": True})
            refusal(acquire(cur, bound, paths=alias_paths, resources=invalid_resource,
                            dependencies=deps),
                    "PATH_CASE_ALIAS", "case precedes resource/duplicate",
                    causal_object="path_claims", expected="one canonical path case",
                    actual={"reason": "case_alias", "value_redacted": True})
            refusal(acquire(cur, bound, paths=[precedence_duplicate,
                                              precedence_duplicate],
                            resources=invalid_resource, dependencies=deps),
                    "RESOURCE_INVALID", "resource precedes duplicate",
                    causal_object="resource_claim",
                    expected="exact ASCII resource identifier",
                    actual={"field": "resource_claim", "reason": "invalid",
                            "value_redacted": True})
            refusal(one(
                cur,
                "select ops.canonical_ownership_dependency_state(%s,%s,%s,%s)",
                (bound[0], bound[5], "slice:a2:missing", "independently_verified"),
            )[0], "DEPENDENCY_MISSING", "missing canonical dependency evidence",
                causal_object="dependency",
                expected="exactly one unsuperseded envelope leaf", actual=0)
            refusal(one(
                cur,
                "select ops.canonical_ownership_dependency_state(%s,%s,%s,%s)",
                (bound[0], bound[5], subject[5], "independently_verified"),
            )[0], "DEPENDENCY_UNSATISFIED", "unsatisfied canonical dependency evidence",
                causal_object="dependency", expected="independently_verified",
                actual={"receipt_id": None, "outcome": None})

            stale_work = list(bound)
            stale_work[1] += 1
            refusal(one(
                cur,
                "select ops.canonical_ownership_currentness(%s,%s,%s,%s,%s,%s,%s,%s)",
                tuple(stale_work),
            )[0], "WORK_REQUEST_BINDING_STALE", "stale work request",
                causal_object="work_request.binding",
                expected="submitted binding matches canonical source",
                actual={"reason": "binding_stale", "value_redacted": True})
            missing_plan = list(bound)
            missing_plan[5] = uuid.uuid4()
            refusal(one(
                cur,
                "select ops.canonical_ownership_currentness(%s,%s,%s,%s,%s,%s,%s,%s)",
                tuple(missing_plan),
            )[0], "SLICE_PLAN_NOT_FOUND", "missing slice plan",
                causal_object="slice_plan", expected="canonical slice plan",
                actual="absent")
            stale_plan = list(bound)
            stale_plan[6] = sha("f")
            refusal(one(
                cur,
                "select ops.canonical_ownership_currentness(%s,%s,%s,%s,%s,%s,%s,%s)",
                tuple(stale_plan),
            )[0], "SLICE_PLAN_BINDING_STALE", "stale slice plan",
                causal_object="slice_plan.binding",
                expected="submitted binding matches canonical slice plan",
                actual={"reason": "binding_stale", "value_redacted": True})

            refusal(acquire(cur, bound), "SLICE_PLAN_BINDING_STALE",
                    "omitted canonical dependency",
                    causal_object="slice_plan.dependencies",
                    expected="exact canonical dependency snapshot",
                    actual={"reason": "snapshot_mismatch", "value_redacted": True})
            refusal(acquire(cur, bound, dependencies=[*deps, {
                "slice_ref": "slice:a2:extra", "required_state": "independently_verified"
            }]), "SLICE_PLAN_BINDING_STALE", "extra canonical dependency",
                    causal_object="slice_plan.dependencies",
                    expected="exact canonical dependency snapshot",
                    actual={"reason": "snapshot_mismatch", "value_redacted": True})
            refusal(acquire(cur, bound, dependencies=[{
                "slice_ref": dependency[5], "required_state": "completed"
            }]), "SLICE_PLAN_BINDING_STALE", "downgraded canonical dependency",
                    causal_object="slice_plan.dependencies",
                    expected="exact canonical dependency snapshot",
                    actual={"reason": "snapshot_mismatch", "value_redacted": True})
            refusal(acquire(cur, bound, dependencies=[{
                "slice_ref": dependency[5]
            }]), "INPUT_INVALID", "malformed canonical dependency",
                    causal_object="dependency", expected="exact dependency object",
                    actual={"field": "dependency", "reason": "invalid",
                            "value_redacted": True})
            refusal(acquire(cur, bound, dependencies=[deps[0], deps[0]]),
                    "INPUT_INVALID", "duplicate canonical dependency",
                    causal_object="dependencies", expected="unique slice_ref values",
                    actual="duplicates present")

            refusal(acquire(cur, bound, ttl=1), "INPUT_INVALID", "ttl precedence",
                    causal_object="lease.input", expected="bounded exact A2 input",
                    actual="invalid")
            refusal(
                acquire(cur, bound, paths=[{"path": "../escape", "mode": "file",
                                            "operation": "write"}]),
                "PATH_INVALID",
                "repo-relative path",
                causal_object="path_claim", expected="exact A1a path claim",
                actual={"field": "path_claim", "reason": "invalid",
                        "value_redacted": True},
            )
            refusal(
                acquire(cur, bound, paths=[
                    {"path": "Ops/A", "mode": "file", "operation": "write"},
                    {"path": "ops/B", "mode": "file", "operation": "write"},
                ]),
                "PATH_CASE_ALIAS",
                "case-fold alias",
                causal_object="path_claims", expected="one canonical path case",
                actual={"reason": "case_alias", "value_redacted": True},
            )
            refusal(
                acquire(cur, bound, resources=[{"resource": "résource:bad"}]),
                "RESOURCE_INVALID",
                "ASCII resource",
                causal_object="resource_claim",
                expected="exact ASCII resource identifier",
                actual={"field": "resource_claim", "reason": "invalid",
                        "value_redacted": True},
            )
            duplicate = {"path": "ops/duplicate.py", "mode": "file", "operation": "write"}
            refusal(acquire(cur, bound, paths=[duplicate, duplicate]),
                    "DUPLICATE_CLAIM", "duplicate path", causal_object="claims",
                    expected="unique claims", actual="duplicates present")
            lease = acquire(
                cur, bound, paths=path_claims, resources=resources, dependencies=deps
            )
            if lease.get("ok") is not True or set(lease) != {
                "ok", "lease_id", "lease_token", "fencing_generation", "expires_at"
            }:
                raise RuntimeError("acquire result shape drifted")
            lease_id = lease["lease_id"]
            lease_token = lease["lease_token"]
            generation = lease["fencing_generation"]
            wrong_token = uuid.uuid4()
            SECRET_TOKENS.update({str(lease_token), str(wrong_token)})
            for label, value in (
                ("malformed path token", acquire(cur, bound, paths=[{
                    "path": f"ops/{lease_token}", "mode": "file", "operation": "write",
                    "submitted_token": str(lease_token)
                }], dependencies=deps)),
                ("malformed resource token", acquire(cur, bound, resources=[{
                    "resource": f"resource:{lease_token}",
                    "submitted_token": str(lease_token)
                }], dependencies=deps)),
                ("malformed dependency token", acquire(cur, bound, dependencies=[{
                    "slice_ref": dependency[5], "required_state": "independently_verified",
                    "submitted_token": str(lease_token)
                }])),
            ):
                refusal(value, "PATH_INVALID" if "path" in label else
                        "RESOURCE_INVALID" if "resource" in label else "INPUT_INVALID",
                        label,
                        causal_object="path_claim" if "path" in label else
                                      "resource_claim" if "resource" in label else
                                      "dependency",
                        expected="exact A1a path claim" if "path" in label else
                                 "exact ASCII resource identifier" if "resource" in label else
                                 "exact dependency object",
                        actual={"field": "path_claim" if "path" in label else
                                          "resource_claim" if "resource" in label else
                                          "dependency",
                                "reason": "invalid", "value_redacted": True})
                assert_secret_absent(value, str(lease_token), label)
            assertions += 14
        conn.commit()

        with conn.cursor() as cur:
            context(cur, tenant, "dell")
            collision_path = [{"path": "ops/ownership/child.py", "mode": "file",
                               "operation": "write"}]
            refusal(acquire(cur, tuple(stale_work), paths=collision_path,
                            dependencies=deps),
                    "WORK_REQUEST_BINDING_STALE", "work stale precedes collision",
                    causal_object="work_request.binding",
                    expected="submitted binding matches canonical source",
                    actual={"reason": "binding_stale", "value_redacted": True})
            refusal(acquire(cur, tuple(missing_plan), paths=collision_path,
                            dependencies=deps),
                    "SLICE_PLAN_NOT_FOUND", "slice missing precedes collision",
                    causal_object="slice_plan", expected="canonical slice plan",
                    actual="absent")
            refusal(acquire(cur, tuple(stale_plan), paths=collision_path,
                            dependencies=deps),
                    "SLICE_PLAN_BINDING_STALE", "slice stale precedes collision",
                    causal_object="slice_plan.binding",
                    expected="submitted binding matches canonical slice plan",
                    actual={"reason": "binding_stale", "value_redacted": True})

            cur.execute("savepoint dependency_missing_precedence")
            cur.execute("alter table ops.engineering_slice_plan disable trigger user")
            missing_dependency_ref = "slice:a2:missing-precedence"
            set_plan_dependencies(cur, bound[5], bound[7],
                                  [dependency[5], missing_dependency_ref])
            refusal(acquire(cur, bound, paths=collision_path, dependencies=[
                        deps[0], {"slice_ref": missing_dependency_ref,
                                  "required_state": "independently_verified"}]),
                    "DEPENDENCY_MISSING", "dependency missing precedes collision",
                    causal_object="dependency",
                    expected="exactly one unsuperseded envelope leaf", actual=0)
            cur.execute("rollback to savepoint dependency_missing_precedence")
            cur.execute("release savepoint dependency_missing_precedence")

            cur.execute("savepoint dependency_unsatisfied_precedence")
            cur.execute("alter table ops.engineering_reviewer_fact disable trigger user")
            cur.execute("update ops.engineering_reviewer_fact set state='failed' "
                        "where receipt_id=%s", (_receipt,))
            refusal(acquire(cur, bound, paths=collision_path, dependencies=deps),
                    "DEPENDENCY_UNSATISFIED",
                    "dependency unsatisfied precedes collision",
                    causal_object="dependency", expected="independently_verified",
                    actual={"receipt_id": str(_receipt),
                            "reviewer_fact_id": str(one(cur,
                                "select id from ops.engineering_reviewer_fact where receipt_id=%s",
                                (_receipt,))[0]),
                            "review_state": "failed"})
            cur.execute("rollback to savepoint dependency_unsatisfied_precedence")
            cur.execute("release savepoint dependency_unsatisfied_precedence")

            refusal(
                acquire(
                    cur,
                    bound,
                    paths=collision_path, dependencies=deps,
                ),
                "FOREIGN_LEASE_COLLISION",
                "tree ancestry collision",
                causal_object="lease.collision", expected="unclaimed scope",
                actual={"claim_kind": "path", "reason": "already_claimed",
                        "value_redacted": True},
            )
            refusal(
                acquire(cur, bound, resources=resources, dependencies=deps),
                "FOREIGN_LEASE_COLLISION",
                "resource collision",
                causal_object="lease.collision", expected="unclaimed scope",
                actual={"claim_kind": "resource", "reason": "already_claimed",
                        "value_redacted": True},
            )
            holder = one(
                cur,
                "select ops.check_canonical_ownership_lease(%s,%s,%s)",
                (lease_id, lease_token, generation),
            )[0]
            refusal(holder, "LEASE_HOLDER_MISMATCH", "Joe and Dell identity",
                    causal_object="lease.holder",
                    expected="acquiring actor, session, and host",
                    actual={"actor_matches": False, "session_matches": False,
                            "host_matches": True})
            context(cur, tenant, "joe", "session:a2:primary:changed")
            refusal(
                one(
                    cur,
                    "select ops.check_canonical_ownership_lease(%s,%s,%s)",
                    (lease_id, lease_token, generation),
                )[0],
                "LEASE_HOLDER_MISMATCH",
                "changed holder session",
                causal_object="lease.holder",
                expected="acquiring actor, session, and host",
                actual={"actor_matches": True, "session_matches": False,
                        "host_matches": True},
            )
            assertions += 4

            context(cur, tenant, "joe", "session:a2:primary:joe")
            refusal(
                one(cur, "select ops.check_canonical_ownership_lease(%s,%s,%s)",
                    (uuid.uuid4(), wrong_token, generation))[0],
                "LEASE_NOT_FOUND", "missing lease", causal_object="lease",
                expected="tenant lease", actual="absent",
            )
            refusal(
                one(cur, "select ops.check_canonical_ownership_lease(%s,%s,%s)",
                    (lease_id, uuid.uuid4(), generation))[0],
                "LEASE_TOKEN_STALE",
                "stale token",
                causal_object="lease.token", expected="current lease token",
                actual="redacted",
            )
            refusal(
                one(cur, "select ops.check_canonical_ownership_lease(%s,%s,%s)",
                    (lease_id, lease_token, generation + 1))[0],
                "FENCING_GENERATION_STALE",
                "stale fencing generation",
                causal_object="lease.fencing_generation", expected=generation,
                actual=generation + 1,
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
                causal_object="lease.claims", expected="claimed scope",
                actual={"reason": "unowned_claim", "value_redacted": True},
            )
            for label, paths, resource_claims in (
                ("valid path submitted token", [{
                    "path": f"ops/{wrong_token}", "mode": "file", "operation": "write"
                }], []),
                ("valid resource submitted token", [], [{
                    "resource": f"resource:{wrong_token}"
                }]),
            ):
                token_claim = one(
                    cur,
                    "select ops.check_canonical_ownership_lease(%s,%s,%s,%s,%s)",
                    (lease_id, lease_token, generation, Jsonb(paths), Jsonb(resource_claims)),
                )[0]
                refusal(token_claim, "LEASE_CLAIMS_MISMATCH", label,
                        causal_object="lease.claims", expected="claimed scope",
                        actual={"reason": "unowned_claim", "value_redacted": True})
                assert_secret_absent(token_claim, str(wrong_token), label)
            malformed_required = one(
                cur,
                "select ops.check_canonical_ownership_lease(%s,%s,%s,%s,%s)",
                (lease_id, lease_token, generation, Jsonb([{
                    "path": f"ops/{lease_token}", "mode": "file", "operation": "write",
                    "submitted_token": str(lease_token)
                }]), Jsonb([])),
            )[0]
            refusal(malformed_required, "INPUT_INVALID", "required claim token",
                    causal_object="required_claims.path", expected="exact path claim",
                    actual={"field": "path_claim", "reason": "invalid",
                            "value_redacted": True})
            assert_secret_absent(malformed_required, str(lease_token),
                                 "required claim token")
            cur.execute("savepoint post_acquire_dependency_drift")
            cur.execute("alter table ops.engineering_slice_plan disable trigger user")
            drift_ref = "slice:a2:post-acquire-drift"
            set_plan_dependencies(cur, bound[5], bound[7], [dependency[5], drift_ref])
            for label, sql, params in (
                ("check canonical drift precedes claims",
                 "select ops.check_canonical_ownership_lease(%s,%s,%s,%s,%s)",
                 (lease_id, lease_token, generation,
                  Jsonb([{"path": "ops/unowned-after-drift.py", "mode": "file",
                          "operation": "write"}]), Jsonb([]))),
                ("renew canonical drift",
                 "select ops.renew_canonical_ownership_lease(%s,%s,%s,600)",
                 (lease_id, lease_token, generation)),
                ("release canonical drift",
                 "select ops.release_canonical_ownership_lease(%s,%s,%s)",
                 (lease_id, lease_token, generation)),
            ):
                refusal(one(cur, sql, params)[0], "SLICE_PLAN_BINDING_STALE", label,
                        causal_object="slice_plan.dependencies",
                        expected="persisted dependencies match canonical plan",
                        actual={"reason": "canonical_drift", "value_redacted": True})
            cur.execute("rollback to savepoint post_acquire_dependency_drift")
            cur.execute("release savepoint post_acquire_dependency_drift")
            renewed = one(
                cur,
                "select ops.renew_canonical_ownership_lease(%s,%s,%s,600)",
                (lease_id, lease_token, generation),
            )[0]
            if renewed.get("ok") is not True:
                raise RuntimeError(f"renewal failed: {safe_outcome(renewed)}")
            RESPONSES.append(renewed)
            released = one(
                cur,
                "select ops.release_canonical_ownership_lease(%s,%s,%s)",
                (lease_id, lease_token, generation),
            )[0]
            if released.get("state") != "released":
                raise RuntimeError(f"release failed: {safe_outcome(released)}")
            RESPONSES.append(released)
            refusal(
                one(cur, "select ops.check_canonical_ownership_lease(%s,%s,%s)",
                    (lease_id, lease_token, generation))[0],
                "LEASE_RELEASED",
                "released lease",
                causal_object="lease.lifecycle", expected="active",
                actual="released",
            )
            context(cur, tenant, "dell", "session:a2:released:dell")
            refusal(one(cur, "select ops.check_canonical_ownership_lease(%s,%s,%s)",
                        (lease_id, wrong_token, generation + 1))[0],
                    "LEASE_HOLDER_MISMATCH", "holder precedes token/generation/lifecycle",
                    causal_object="lease.holder",
                    expected="acquiring actor, session, and host",
                    actual={"actor_matches": False, "session_matches": False,
                            "host_matches": True})
            context(cur, tenant, "joe", "session:a2:primary:joe")
            refusal(one(cur, "select ops.check_canonical_ownership_lease(%s,%s,%s)",
                        (lease_id, wrong_token, generation + 1))[0],
                    "LEASE_TOKEN_STALE", "token precedes generation/lifecycle",
                    causal_object="lease.token", expected="current lease token",
                    actual="redacted")
            refusal(one(cur, "select ops.check_canonical_ownership_lease(%s,%s,%s)",
                        (lease_id, lease_token, generation + 1))[0],
                    "FENCING_GENERATION_STALE", "generation precedes lifecycle",
                    causal_object="lease.fencing_generation", expected=generation,
                    actual=generation + 1)
            cur.execute("savepoint released_precedence")
            cur.execute("alter table ops.engineering_slice_plan disable trigger user")
            set_plan_dependencies(cur, bound[5], bound[7],
                                  [dependency[5], "slice:a2:released-drift"])
            refusal(one(cur,
                        "select ops.check_canonical_ownership_lease(%s,%s,%s,%s,%s)",
                        (lease_id, lease_token, generation,
                         Jsonb([{"path": "ops/released-unowned.py", "mode": "file",
                                 "operation": "write"}]), Jsonb([])))[0],
                    "LEASE_RELEASED", "released precedes downstream drift/claims",
                    causal_object="lease.lifecycle", expected="active",
                    actual="released")
            cur.execute("rollback to savepoint released_precedence")
            cur.execute("release savepoint released_precedence")
            assertions += 13
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
            cur.execute("savepoint wall_clock_expiry_precedence")
            cur.execute("alter table ops.engineering_slice_plan disable trigger user")
            set_plan_dependencies(cur, bound[5], bound[7],
                                  [dependency[5], "slice:a2:expired-drift"])
            refusal(one(cur,
                        "select ops.check_canonical_ownership_lease(%s,%s,%s,%s,%s)",
                        (expiring["lease_id"], expiring["lease_token"],
                         expiring["fencing_generation"],
                         Jsonb([{"path": "ops/expired-unowned.py", "mode": "file",
                                 "operation": "write"}]), Jsonb([])))[0],
                    "LEASE_EXPIRED", "wall clock expiry precedes drift/claims",
                    causal_object="lease.expiry", expected="future expiry",
                    actual="elapsed")
            cur.execute("rollback to savepoint wall_clock_expiry_precedence")
            cur.execute("release savepoint wall_clock_expiry_precedence")
            cleanup = one(cur, "select ops.expire_canonical_ownership_leases()")[0]
            if cleanup.get("ok") is not True or cleanup.get("expired_count") != 1:
                raise RuntimeError("expiry cleanup did not transition exactly one lease")
            RESPONSES.append(cleanup)
            cleanup_again = one(cur, "select ops.expire_canonical_ownership_leases()")[0]
            if cleanup_again.get("ok") is not True or cleanup_again.get("expired_count") != 0:
                raise RuntimeError("expiry cleanup was not idempotent")
            RESPONSES.append(cleanup_again)
            expired_check = one(
                cur,
                "select ops.check_canonical_ownership_lease(%s,%s,%s)",
                (expiring["lease_id"], expiring["lease_token"],
                 expiring["fencing_generation"]),
            )[0]
            refusal(expired_check, "LEASE_EXPIRED", "cleanup expired check",
                    causal_object="lease.lifecycle",
                    expected="active and unexpired", actual="expired")
            expired_renew = one(
                cur,
                "select ops.renew_canonical_ownership_lease(%s,%s,%s,600)",
                (expiring["lease_id"], expiring["lease_token"],
                 expiring["fencing_generation"]),
            )[0]
            refusal(expired_renew, "LEASE_EXPIRED", "cleanup expired renew",
                    causal_object="lease.lifecycle",
                    expected="active and unexpired", actual="expired")
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
            context(cur, tenant, "joe", "session:a2:expiry:joe")
            replaced_check = one(
                cur,
                "select ops.check_canonical_ownership_lease(%s,%s,%s)",
                (expiring["lease_id"], expiring["lease_token"],
                 expiring["fencing_generation"]),
            )[0]
            refusal(replaced_check, "LEASE_REPLACED", "replaced predecessor",
                    causal_object="lease.lifecycle", expected="active",
                    actual="replaced")
            cur.execute("savepoint replaced_precedence")
            cur.execute("alter table ops.engineering_slice_plan disable trigger user")
            set_plan_dependencies(cur, bound[5], bound[7],
                                  [dependency[5], "slice:a2:replaced-drift"])
            refusal(one(cur,
                        "select ops.check_canonical_ownership_lease(%s,%s,%s,%s,%s)",
                        (expiring["lease_id"], expiring["lease_token"],
                         expiring["fencing_generation"],
                         Jsonb([{"path": "ops/replaced-unowned.py", "mode": "file",
                                 "operation": "write"}]), Jsonb([])))[0],
                    "LEASE_REPLACED", "replaced precedes downstream drift/claims",
                    causal_object="lease.lifecycle", expected="active",
                    actual="replaced")
            cur.execute("rollback to savepoint replaced_precedence")
            cur.execute("release savepoint replaced_precedence")
            retained = one(
                cur,
                """select count(*),
                          (select count(*) from ops.canonical_ownership_claim where lease_id=%s),
                          (select count(*) from ops.canonical_ownership_dependency where lease_id=%s),
                          min(fencing_generation),max(fencing_generation)
                     from ops.canonical_ownership_lease_event where lease_id=%s""",
                (expiring["lease_id"], expiring["lease_id"], expiring["lease_id"]),
            )
            if retained[0] != 3 or retained[1] < 1 or retained[2] != len(deps) or retained[3] != retained[4] or retained[3] != expiring["fencing_generation"]:
                raise RuntimeError("cleanup/replacement did not retain exact lineage and fence")
            assertions += 8
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
                raise RuntimeError(f"tenant isolation failed: {safe_outcome(other)}")
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
        raise RuntimeError(
            "atomic acquisition had wrong outcomes: "
            + ",".join(sorted(safe_outcome(value) for value in atomic.values()))
        )
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
                dependencies=[],
            )
            peer.commit()
            return value

    receipt_race = race(append_receipt, acquire_during_receipt, "receipt append")
    lease_outcome = receipt_race["lease"]
    if lease_outcome.get("ok") is not True:
        raise RuntimeError(
            "receipt race crossed a noncausal boundary: "
            + safe_outcome(lease_outcome)
        )
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
                dependencies=[],
            )
            peer.commit()
            return value

    review_race = race(append_review, acquire_during_review, "review append")
    lease_outcome = review_race["lease"]
    if lease_outcome.get("ok") is not True:
        raise RuntimeError(
            "review race crossed a noncausal boundary: "
            + safe_outcome(lease_outcome)
        )
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
                dependencies=[],
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
    if not lease_outcome.get("ok") and lease_outcome["refusal"]["code"] != "SLICE_PLAN_BINDING_STALE":
        raise RuntimeError(
            "successor race crossed a noncausal boundary: "
            + safe_outcome(lease_outcome)
        )
    RESPONSES.append(lease_outcome)
    assertions += 1

    lifecycle_session = "session:a2:lifecycle:joe"

    def lifecycle_lease(label: str):
        with psycopg.connect(dsn) as peer, peer.cursor() as cur:
            context(cur, tenant, "joe", lifecycle_session)
            value = acquire(
                cur, bound,
                paths=[{"path": f"ops/lifecycle-{label}.py", "mode": "file",
                        "operation": "write"}],
                dependencies=deps,
            )
            peer.commit()
            if value.get("ok") is not True:
                raise RuntimeError(f"{label}: lifecycle fixture acquisition failed")
            return value

    def lifecycle_call(lease_value, operation: str):
        sql = {
            "renew": "select ops.renew_canonical_ownership_lease(%s,%s,%s,600)",
            "release": "select ops.release_canonical_ownership_lease(%s,%s,%s)",
        }[operation]
        with psycopg.connect(dsn) as peer, peer.cursor() as cur:
            context(cur, tenant, "joe", lifecycle_session)
            value = one(cur, sql, (
                lease_value["lease_id"], lease_value["lease_token"],
                lease_value["fencing_generation"],
            ))[0]
            peer.commit()
            RESPONSES.append(value)
            return value

    renew_release_lease = lifecycle_lease("renew-release")
    renew_release = race(
        lambda: lifecycle_call(renew_release_lease, "renew"),
        lambda: lifecycle_call(renew_release_lease, "release"),
        "renew versus release",
    )
    if renew_release["lease"].get("state") != "released":
        raise RuntimeError("renew versus release did not release exactly once")
    if renew_release["writer"].get("ok") is not True:
        refusal(renew_release["writer"], "LEASE_RELEASED",
                "release won before renew", causal_object="lease.lifecycle",
                expected="active", actual="released")
    with psycopg.connect(dsn) as proof, proof.cursor() as cur:
        event_counts = one(cur,
            """select count(*) filter(where event_kind='released'),
                      count(*) filter(where event_kind='renewed'),
                      min(fencing_generation),max(fencing_generation)
                 from ops.canonical_ownership_lease_event where lease_id=%s""",
            (renew_release_lease["lease_id"],))
        if event_counts[0] != 1 or event_counts[1] not in (0, 1) \
           or event_counts[2] != event_counts[3] \
           or event_counts[2] != renew_release_lease["fencing_generation"]:
            raise RuntimeError("renew versus release event/fence proof drifted")
    assertions += 1

    cleanup_lease = lifecycle_lease("renew-cleanup")
    with psycopg.connect(dsn) as fixture, fixture.cursor() as cur:
        context(cur, tenant, "joe", lifecycle_session)
        cur.execute("update ops.canonical_ownership_lease set "
                    "acquired_at=clock_timestamp()-interval '2 seconds',"
                    "expires_at=clock_timestamp()-interval '1 second' where id=%s",
                    (cleanup_lease["lease_id"],))
        fixture.commit()

    def cleanup_call():
        with psycopg.connect(dsn) as peer, peer.cursor() as cur:
            context(cur, tenant, "joe", lifecycle_session)
            value = one(cur, "select ops.expire_canonical_ownership_leases()")[0]
            peer.commit()
            RESPONSES.append(value)
            return value

    renew_cleanup = race(cleanup_call,
                         lambda: lifecycle_call(cleanup_lease, "renew"),
                         "renew versus cleanup")
    if renew_cleanup["writer"].get("expired_count") != 1:
        raise RuntimeError("cleanup did not win exactly one expiry transition")
    cleanup_renew = renew_cleanup["lease"]
    if cleanup_renew.get("refusal", {}).get("causal_object") == "lease.expiry":
        refusal(cleanup_renew, "LEASE_EXPIRED", "wall expiry won before cleanup",
                causal_object="lease.expiry", expected="future expiry", actual="elapsed")
    else:
        refusal(cleanup_renew, "LEASE_EXPIRED", "cleanup won before renew",
                causal_object="lease.lifecycle", expected="active and unexpired",
                actual="expired")
    with psycopg.connect(dsn) as proof, proof.cursor() as cur:
        state_events = one(cur,
            """select l.state,count(*) filter(where e.event_kind='expired'),
                      min(e.fencing_generation),max(e.fencing_generation)
                 from ops.canonical_ownership_lease l
                 join ops.canonical_ownership_lease_event e on e.lease_id=l.id
                where l.id=%s group by l.state""",
            (cleanup_lease["lease_id"],))
        if state_events != ("expired", 1, cleanup_lease["fencing_generation"],
                            cleanup_lease["fencing_generation"]):
            raise RuntimeError("renew versus cleanup lifecycle proof drifted")
    assertions += 1

    def replacement_race(label: str, competitor: str) -> None:
        predecessor = lifecycle_lease(label)
        with psycopg.connect(dsn) as fixture, fixture.cursor() as cur:
            context(cur, tenant, "joe", lifecycle_session)
            cur.execute("update ops.canonical_ownership_lease set "
                        "acquired_at=clock_timestamp()-interval '2 seconds',"
                        "expires_at=clock_timestamp()-interval '1 second' where id=%s",
                        (predecessor["lease_id"],))
            fixture.commit()

        def replace_call():
            with psycopg.connect(dsn) as peer, peer.cursor() as cur:
                context(cur, tenant, "dell", f"session:a2:{label}:dell")
                value = acquire(
                    cur, bound,
                    paths=[{"path": f"ops/lifecycle-{label}.py", "mode": "file",
                            "operation": "write"}], dependencies=deps,
                )
                peer.commit()
                return value

        competing_call = cleanup_call if competitor == "cleanup" else \
            lambda: lifecycle_call(predecessor, competitor)
        outcomes = race(replace_call, competing_call,
                        f"replacement versus {competitor}")
        successor = outcomes["writer"]
        if successor.get("ok") is not True:
            raise RuntimeError(f"replacement versus {competitor} did not replace")
        competing = outcomes["lease"]
        if competitor == "cleanup":
            if competing.get("ok") is not True or competing.get("expired_count") not in (0, 1):
                raise RuntimeError("replacement versus cleanup returned a noncausal result")
        elif competing.get("ok") is True:
            raise RuntimeError(f"expired predecessor was revived by {competitor}")
        else:
            cause = competing.get("refusal", {}).get("causal_object")
            if cause == "lease.expiry":
                refusal(competing, "LEASE_EXPIRED", f"{competitor} observed wall expiry",
                        causal_object="lease.expiry", expected="future expiry",
                        actual="elapsed")
            elif cause == "lease.lifecycle" and competing["refusal"]["actual"] == "expired":
                refusal(competing, "LEASE_EXPIRED", f"{competitor} observed cleanup",
                        causal_object="lease.lifecycle", expected="active and unexpired",
                        actual="expired")
            else:
                refusal(competing, "LEASE_REPLACED", f"{competitor} observed replacement",
                        causal_object="lease.lifecycle", expected="active",
                        actual="replaced")
        with psycopg.connect(dsn) as proof, proof.cursor() as cur:
            row = one(cur,
                """select l.state,l.superseded_by_lease_id,l.fencing_generation,
                          count(*) filter(where e.event_kind='expired'),
                          count(*) filter(where e.event_kind='replaced'),
                          min(e.fencing_generation),max(e.fencing_generation)
                     from ops.canonical_ownership_lease l
                     join ops.canonical_ownership_lease_event e on e.lease_id=l.id
                    where l.id=%s
                    group by l.state,l.superseded_by_lease_id,l.fencing_generation""",
                (predecessor["lease_id"],))
            successor_events = one(cur,
                "select fencing_generation,count(*) from ops.canonical_ownership_lease_event where lease_id=%s and event_kind='acquired' group by fencing_generation",
                (successor["lease_id"],))
            if row[0] != "replaced" or str(row[1]) != str(successor["lease_id"]) \
               or row[2] != predecessor["fencing_generation"] \
               or row[3:] != (1, 1, predecessor["fencing_generation"],
                               predecessor["fencing_generation"]) \
               or successor_events != (successor["fencing_generation"], 1) \
               or successor["fencing_generation"] <= predecessor["fencing_generation"]:
                raise RuntimeError(f"replacement versus {competitor} fence/events drifted")

    for competitor in ("renew", "release", "cleanup"):
        replacement_race(f"replace-{competitor}", competitor)
        assertions += 1

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        private_functions = [
            "ops.canonical_ownership_refusal(text,text,jsonb,jsonb)",
            "ops.canonical_ownership_path_valid(text)",
            "ops.canonical_ownership_resource_valid(text)",
            "ops.canonical_ownership_path_case_alias(text,text)",
            "ops.canonical_ownership_paths_overlap(text,text,text,text)",
            "ops.canonical_ownership_context()",
            "ops.canonical_ownership_lock_lineage(uuid,text[])",
            "ops.canonical_ownership_currentness(uuid,integer,text,uuid,text,uuid,text,text)",
            "ops.canonical_ownership_plan_dependencies(uuid,text)",
            "ops.canonical_ownership_dependency_state(uuid,uuid,text,text)",
            "ops.acquire_canonical_ownership_lease(uuid,integer,text,uuid,text,uuid,text,text,text,jsonb,jsonb,jsonb,integer)",
            "ops.canonical_ownership_validate_live(uuid,uuid,bigint,boolean)",
            "ops.check_canonical_ownership_lease(uuid,uuid,bigint,jsonb,jsonb)",
            "ops.renew_canonical_ownership_lease(uuid,uuid,bigint,integer)",
            "ops.release_canonical_ownership_lease(uuid,uuid,bigint)",
            "ops.expire_canonical_ownership_leases()",
            "ops.canonical_ownership_append_only()",
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
                for privilege in (
                    "SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE",
                    "REFERENCES", "TRIGGER",
                ):
                    if one(cur, "select has_table_privilege(%s,%s,%s)",
                           (role, table, privilege))[0]:
                        raise RuntimeError(
                            f"dark kernel table privilege leaked: {role}/{privilege}"
                        )
                if one(
                    cur,
                    """select exists(
                         select 1 from information_schema.columns
                          where table_schema=split_part(%s,'.',1)
                            and table_name=split_part(%s,'.',2)
                            and (has_column_privilege(%s,%s,column_name,'SELECT')
                              or has_column_privilege(%s,%s,column_name,'INSERT')
                              or has_column_privilege(%s,%s,column_name,'UPDATE')
                              or has_column_privilege(%s,%s,column_name,'REFERENCES')))""",
                    (table, table, role, table, role, table, role, table, role, table),
                )[0]:
                    raise RuntimeError(f"dark kernel column privilege leaked: {role}")
            for privilege in ("USAGE", "SELECT", "UPDATE"):
                if one(
                    cur,
                    "select has_sequence_privilege(%s,%s,%s)",
                    (role, "ops.canonical_ownership_fencing_generation", privilege),
                )[0]:
                    raise RuntimeError(
                        f"dark kernel sequence privilege leaked: {role}/{privilege}"
                    )

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

        baseline_text = os.environ.get("CARR_OWNERSHIP_PRE_0450_FINGERPRINT", "")
        if not baseline_text:
            raise RuntimeError("true pre-0450 catalog fingerprint is required")
        baseline = json.loads(baseline_text)
        if catalog_fingerprint(cur) != baseline:
            raise RuntimeError("existing catalog/grant fingerprint drifted across 0450")
        assertions += 1

    response_evidence = json.dumps(RESPONSES, default=str, sort_keys=True)
    if response_evidence.count(str(lease_token)) != 1:
        raise RuntimeError("live token did not occur exactly once in allowed acquire output")
    if str(wrong_token) in response_evidence:
        raise RuntimeError("wrong submitted token escaped into a response")
    print(f"canonical ownership lease local PG gate — {assertions} assertion groups passed")
    return 0


if __name__ == "__main__":
    if sys.argv[1:] == ["--fingerprint-only"]:
        fingerprint_dsn = os.environ.get("CARR_LOCAL_PG_DSN", "")
        with psycopg.connect(fingerprint_dsn) as fingerprint_conn:
            with fingerprint_conn.cursor() as fingerprint_cur:
                print(json.dumps(catalog_fingerprint(fingerprint_cur), sort_keys=True))
        raise SystemExit(0)
    captured_out = io.StringIO()
    captured_err = io.StringIO()
    failure: Exception | None = None
    result = 1
    with redirect_stdout(captured_out), redirect_stderr(captured_err):
        try:
            result = main()
        except Exception as exc:
            failure = exc
            print(
                "canonical-ownership-lease-local-pg-gate: FAIL — "
                f"{type(exc).__name__}: {safe_error(exc)}",
                file=sys.stderr,
            )
    rendered_output = captured_out.getvalue() + captured_err.getvalue()
    escaped = next((secret for secret in SECRET_TOKENS if secret in rendered_output), None)
    if escaped is not None:
        print(
            "canonical-ownership-lease-local-pg-gate: FAIL — submitted token "
            "escaped into captured stdout/stderr",
            file=sys.stderr,
        )
        raise SystemExit(1)
    sys.stdout.write(captured_out.getvalue())
    sys.stderr.write(captured_err.getvalue())
    raise SystemExit(1 if failure is not None else result)
