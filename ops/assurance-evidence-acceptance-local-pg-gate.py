#!/usr/bin/env python3
# ci: runs-outside-ci — invoked by ops/local-pg-ci.py after the A2 ownership gate
# doctrine: runbook
"""Disposable-Postgres acceptance for immutable A3a assurance persistence."""

from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
from threading import Barrier, Lock, Thread
import uuid

import psycopg
from psycopg.types.json import Jsonb


ROOT = Path(__file__).resolve().parents[1]
PASS = 0
FAIL = 0
FAILED_LABELS: list[str] = []
REFUSALS: dict[str, int] = {}
SECRET_TOKENS: set[str] = set()
RAW_RESULTS: list[object] = []
RAW_ERRORS: list[str] = []
RAW_LOCK = Lock()


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    module_dir = str(path.parent)
    added_to_path = module_dir not in sys.path
    if added_to_path:
        sys.path.insert(0, module_dir)
    try:
        spec.loader.exec_module(module)
    finally:
        if added_to_path:
            sys.path.remove(module_dir)
    return module


a2 = load_module("a3a_a2_fixture", ROOT / "ops/canonical-ownership-lease-local-pg-gate.py")
cc = a2.cc


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value).encode()).hexdigest()


def one(cur, query: str, args: tuple = ()):
    row = cur.execute(query, args).fetchone()
    if row is None:
        raise RuntimeError(f"expected one row: {query[:120]}")
    return row


def check(label: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ok    {label}")
    else:
        FAIL += 1
        FAILED_LABELS.append(label)
        print(f"  FAIL  {label}  {detail}")


def assert_secret_absent(value: object, label: str) -> None:
    rendered = json.dumps(value, default=str, sort_keys=True)
    for token in SECRET_TOKENS:
        if token in rendered:
            raise RuntimeError(f"{label}: lease token escaped before redaction")


def observe(value: object, label: str) -> object:
    assert_secret_absent(value, label)
    with RAW_LOCK:
        RAW_RESULTS.append(value)
    return value


def refusal(label: str, value: dict, code: str, causal_object: str) -> None:
    actual = value.get("refusal", {}).get("code") if isinstance(value, dict) else None
    shape = set(value.get("refusal", {})) if isinstance(value, dict) else set()
    matches = value.get("ok") is False and actual == code and shape == {
        "code", "causal_object", "expected", "actual"
    } and value["refusal"]["causal_object"] == causal_object
    check(label, matches,
        f"actual={actual} causal={value.get('refusal', {}).get('causal_object')}")
    if not matches:
        FAILED_LABELS[-1] += (
            f" [actual={actual}; causal={value.get('refusal', {}).get('causal_object')}]")
    REFUSALS[code] = REFUSALS.get(code, 0) + 1


def safe(value: object) -> str:
    rendered = json.dumps(value, default=str, sort_keys=True)
    for token in SECRET_TOKENS:
        rendered = rendered.replace(token, "<redacted-token>")
    return rendered[:1000]


def call(cur, signature: str, args: tuple):
    placeholders = ",".join(["%s"] * len(args))
    try:
        return observe(one(cur, f"select {signature}({placeholders})", args)[0], signature)
    except BaseException as exc:
        assert_secret_absent(str(exc), f"{signature} exception")
        with RAW_LOCK:
            RAW_ERRORS.append(str(exc))
        raise


def set_context(cur, tenant: str, actor: str, session: str, host: str) -> None:
    for key, value in {
        "carr.organization_tenant_id": tenant,
        "carr.acting_actor_slug": actor,
        "carr.ownership_session_id": session,
        "carr.execution_host_id": host,
    }.items():
        one(cur, "select set_config(%s,%s,false)", (key, value))


def iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def make_rules(seed: str = "a") -> dict:
    value = {
        "schema_version": "applicable-rule-snapshot.v1",
        "snapshot_ref": f"rules:a3a:{seed}",
        "rules": [
            {"rule_ref": "rule:independent-review", "revision": 1,
             "digest": "sha256:" + seed * 64},
        ],
    }
    value["snapshot_digest"] = digest({"snapshot_ref": value["snapshot_ref"], "rules": value["rules"]})
    return value


def make_coord(cur, lease: dict, session: str, host: str, *, seconds: int = 300) -> dict:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    expires_at = one(cur, """select to_char(expires_at at time zone 'UTC',
      'YYYY-MM-DD"T"HH24:MI:SS"Z"') from ops.canonical_ownership_lease where id=%s""",
      (lease["lease_id"],))[0]
    claims = one(cur, """select coalesce(jsonb_agg(jsonb_build_object(
      'path',claim_value,'mode',claim_mode,'operation',operation)
      order by ops.assurance_digest(jsonb_build_object(
      'path',claim_value,'mode',claim_mode,'operation',operation))),'[]'::jsonb)
      from ops.canonical_ownership_claim where lease_id=%s and claim_kind='path'""",
      (lease["lease_id"],))[0]
    dependencies = one(cur, """select coalesce(jsonb_agg(value order by ops.assurance_digest(value)),'[]'::jsonb)
      from (select jsonb_build_object('slice_ref',d.dependency_slice_ref,
        'state',d.required_state,'evidence_digest',
        case when d.required_state='independently_verified'
          then ops.assurance_digest(f.fact) else r.receipt_digest end) value
        from ops.canonical_ownership_dependency d
        join ops.engineering_slice_receipt r on r.id=d.observed_receipt_id
        left join ops.engineering_reviewer_fact f on f.id=d.observed_reviewer_fact_id
        where d.lease_id=%s) rows""", (lease["lease_id"],))[0]
    valid_until = min(now + timedelta(seconds=seconds),
                      datetime.fromisoformat(expires_at.replace("Z", "+00:00")) - timedelta(seconds=1))
    value = {
        "schema_version": "assurance-coordination-snapshot.v1",
        "as_of": iso(now),
        "valid_until": iso(valid_until),
        "manifest_phase": "baseline",
        "requesting_session_id": session,
        "requesting_host_id": host,
        "leases": [{
            "lease_id": f"lease:{lease['lease_id']}",
            "state": "active",
            "holder_session_id": session,
            "holder_host_id": host,
            "expires_at": expires_at,
            "fencing_generation": lease["fencing_generation"],
            "claims": claims,
        }],
        "dependencies": dependencies,
    }
    value["snapshot_digest"] = digest(value)
    return value


def compiler_fixture() -> dict:
    return json.loads((ROOT / "control-room/contracts/fixtures/execution-fabric/"
                       "assurance-compiler.valid.v1.json").read_text())


def compile_canonical(value: dict) -> dict:
    compiler = load_module(f"a3a_compiler_{uuid.uuid4().hex}",
                           ROOT / "tools/room-bridge/assurance_slice_compiler.py")
    compiled = compiler.compile_assurance_slice(copy.deepcopy(value))
    if compiled.get("ok") is not True:
        raise RuntimeError(f"final canonical A1a input did not compile: {compiled}")
    return compiled["manifest"]


def normalize_contract(value: dict) -> None:
    for field in ("path_claims", "forbidden_paths", "dependencies", "required_tests",
                  "evidence_requirements", "unfinished_work"):
        value[field] = sorted(value[field], key=digest)
    value["contract_digest"] = digest({k: v for k, v in value.items()
                                       if k != "contract_digest"})


def compile_input(cur, lease: dict, plan: dict, rules: dict, coord: dict,
                  session: str, host: str, *, commit: str = "a" * 40,
                  tree: str = "b" * 40) -> tuple[dict, dict]:
    compiler = load_module("a3a_compiler", ROOT / "tools/room-bridge/assurance_slice_compiler.py")
    template = compiler_fixture()
    contract = copy.deepcopy(template["assurance_slice"])
    selected = next(row for row in plan["slices"] if row["slice_ref"] == lease["slice_ref"])
    claims = coord["leases"][0]["claims"]
    dependencies = one(cur, """select coalesce(jsonb_agg(jsonb_build_object(
      'slice_ref',dependency_slice_ref,'required_state',required_state)
      order by ops.assurance_digest(jsonb_build_object(
      'slice_ref',dependency_slice_ref,'required_state',required_state))),'[]'::jsonb)
      from ops.canonical_ownership_dependency where lease_id=%s""",
      (lease["lease_id"],))[0]
    repository = {"repository_id": "repo:jbookout-carr-system",
                  "commit_sha": commit, "tree_sha": tree}
    contract.update({
        "work_request": copy.deepcopy(plan["work_request"]),
        "accepted_plan_revision": copy.deepcopy(plan["accepted_plan_revision"]),
        "engineering_slice_plan_digest": plan["plan_digest"],
        "slice_ref": lease["slice_ref"],
        "risk": {"risk_class": selected["risk_class"], "summary": "A3a immutable persistence"},
        "path_claims": claims,
        "dependencies": dependencies,
        "repository_binding": repository,
        "rule_snapshot_binding": {"snapshot_ref": rules["snapshot_ref"],
                                  "snapshot_digest": rules["snapshot_digest"]},
        "lease_binding": {"lease_id": f"lease:{lease['lease_id']}",
                          "fencing_generation": lease["fencing_generation"],
                          "holder_session_id": session, "holder_host_id": host},
        "executor_identity": {"actor_ref": "actor:codex",
                              "session_ref": session, "host_ref": host},
        "reviewer_policy": {"minimum_independent_reviewers": 1,
                            "executor_actor_ref": "actor:codex",
                            "executor_session_ref": session,
                            "owner_acceptance_is_review": False,
                            "distinct_actor_and_session": True},
    })
    planned_check = selected["planned_checks"][0]
    contract["required_tests"][0]["planned_check_digest"] = digest(planned_check)
    contract["required_tests"][0]["causal_failure"]["expected"] = planned_check["failure_condition"]
    normalize_contract(contract)
    value = {
        "schema_version": "assurance-compiler-input.v1",
        "work_request": copy.deepcopy(plan["work_request"]),
        "accepted_plan_revision": copy.deepcopy(plan["accepted_plan_revision"]),
        "engineering_slice_plan": copy.deepcopy(plan),
        "assurance_slice": contract,
        "repository": repository,
        "applicable_rules": copy.deepcopy(rules),
        "coordination_snapshot": copy.deepcopy(coord),
        "declared_evaluation_time": coord["as_of"],
    }
    return value, compile_canonical(value)


def record_manifest(cur, lease: dict, stage: str, compiler_input: dict,
                    manifest: dict, rules: dict, coord: dict, key: uuid.UUID):
    return call(cur, "ops.record_assurance_execution_manifest", (
        lease["lease_id"], lease["lease_token"], lease["fencing_generation"], stage,
        Jsonb(compiler_input), Jsonb(manifest), Jsonb(rules), Jsonb(coord), key,
    ))


def race_calls(dsn: str, setups: list, signature: str, args: tuple) -> list[dict]:
    barrier = Barrier(len(setups))
    results: list[dict] = []
    errors: list[BaseException] = []
    lock = Lock()

    def run(setup) -> None:
        try:
            with psycopg.connect(dsn) as peer:
                with peer.cursor() as cur:
                    setup(cur)
                    barrier.wait(timeout=15)
                    value = call(cur, signature, args)
                peer.commit()
            with lock:
                results.append(value)
        except BaseException as exc:
            assert_secret_absent(str(exc), f"{signature} race exception")
            with lock:
                errors.append(exc)

    threads = [Thread(target=run, args=(setup,), daemon=True) for setup in setups]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    if errors or any(thread.is_alive() for thread in threads):
        raise RuntimeError(f"{signature} race failed without raw leakage: {len(errors)} errors")
    return results


def manifest_id(value: dict) -> uuid.UUID:
    if value.get("ok") is not True:
        raise RuntimeError(f"manifest insert failed: {safe(value)}")
    return uuid.UUID(str(value["manifest_id"]))


def main() -> int:
    dsn = os.environ.get("CARR_LOCAL_PG_DSN", "").strip()
    if not dsn.startswith(("postgres://", "postgresql://")):
        print("assurance A3a gate: CARR_LOCAL_PG_DSN must name disposable PostgreSQL", file=sys.stderr)
        return 2
    tenant = f"tenant:a3a:{uuid.uuid4().hex}"
    host = "host:a3a-disposable-pg"
    dependency_ref, subject_ref = "slice:a3a-dependency", "slice:a3a-subject"

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            a2.context(cur, tenant)
            dependency = a2.fixture(cur, slice_refs=[dependency_ref, subject_ref],
                slice_dependencies={subject_ref: [dependency_ref]})
            plan = one(cur, """select plan from ops.engineering_slice_plan where id=(
              select slice_plan_id from ops.engineering_execution_envelope where id=%s)""",
              (dependency[1],))[0]
            checks = copy.deepcopy(compiler_fixture()["engineering_slice_plan"]["slices"][1]["planned_checks"])
            checks[0]["evidence_requirement"] = "metadata_only_sufficient"
            next(row for row in plan["slices"] if row["slice_ref"] == subject_ref)["planned_checks"] = checks
            plan["plan_digest"] = digest({k: v for k, v in plan.items() if k != "plan_digest"})
            cur.execute("alter table ops.engineering_slice_plan disable trigger user")
            cur.execute("""update ops.engineering_slice_plan set plan=%s,plan_digest=%s where id=(
              select slice_plan_id from ops.engineering_execution_envelope where id=%s)""",
              (Jsonb(plan), plan["plan_digest"], dependency[1]))
            cur.execute("alter table ops.engineering_slice_plan enable trigger user")
            cur.execute("update ops.job set payload=jsonb_set(payload,'{plan_digest}',to_jsonb(%s::text),true) where id=%s",
              (plan["plan_digest"], dependency[0]))
            dependency = (*dependency[:4], plan["plan_digest"], *dependency[5:])
        conn.commit()
        with conn.cursor() as cur:
            cur.execute("""update ops.job set next_attempt_at=now()+interval '1 day'
              where definition_key='engineering-slice' and state='queued' and id<>%s""",
              (dependency[0],))
        conn.commit()
        dependency_claim = cc.claim_one(conn, dependency[0], "a3a-dependency", [dependency[0]])
        with conn.cursor() as cur:
            cc.set_jobs(cur)
            dependency_receipt_id = cc.receipt(cur, dependency, dependency_claim, "claimed_complete")
            cc.reset_role(cur)
        conn.commit()
        with conn.cursor() as cur:
            a2.insert_review(cur, dependency, dependency_receipt_id)
        conn.commit()
        fixture = cc.create_dag_b_after_exact_review(conn, dependency, subject_ref)
        with conn.cursor() as cur:
            session = one(cur, "select envelope#>>'{agent_session,id}' from ops.engineering_execution_envelope where id=%s", (fixture[1],))[0]
            set_context(cur, tenant, "codex", session, host)
            bound = a2.binding(cur, fixture[1])
            lease = a2.acquire(cur, bound, paths=[{
                "path": "ops/a3a-fixture.sql", "mode": "file", "operation": "write"
            }], dependencies=[{"slice_ref": dependency_ref,
                               "required_state": "independently_verified"}],
                contract=a2.sha("9"))
            if lease.get("ok") is not True:
                raise RuntimeError(f"A2 fixture lease failed: {safe(lease)}")
            lease["slice_ref"] = subject_ref
            SECRET_TOKENS.add(str(lease["lease_token"]))
        conn.commit()

        with conn.cursor() as cur:
            cur.execute("""update ops.job set next_attempt_at=now()+interval '1 day'
              where definition_key='engineering-slice' and state='queued' and id<>%s""",
              (fixture[0],))
        conn.commit()
        claim = cc.claim_one(conn, fixture[0], "a3a-controller", [fixture[0]])
        with conn.cursor() as cur:
            cc.set_jobs(cur)
            receipt_id = cc.receipt(cur, fixture, claim, "claimed_complete")
            cc.reset_role(cur)
        conn.commit()
        with conn.cursor() as cur:
            a2.insert_review(cur, fixture, receipt_id)
        conn.commit()

        with conn.cursor() as cur:
            set_context(cur, tenant, "codex", session, host)
            plan = one(cur, "select plan from ops.engineering_slice_plan where id=%s", (bound[5],))[0]
            rules = make_rules("a")
            coord = make_coord(cur, lease, session, host)
            compiler_input, post_manifest = compile_input(
                cur, lease, plan, rules, coord, session, host)
            post_key = uuid.uuid4()
            post = record_manifest(cur, lease, "post_commit", compiler_input,
                                   post_manifest, rules, coord, post_key)
            post_id = manifest_id(post)
            check("post-commit manifest persists", post.get("replayed") is False)
            replay = record_manifest(cur, lease, "post_commit", compiler_input,
                                     post_manifest, rules, coord, post_key)
            check("manifest exact replay is idempotent", replay.get("ok") is True and replay.get("replayed") is True and replay.get("manifest_id") == post.get("manifest_id"))
            refusal("manifest key reuse with changed stage refuses",
                record_manifest(cur, lease, "push", compiler_input, post_manifest,
                                rules, coord, post_key),
                "IDEMPOTENCY_CONFLICT", "assurance_execution_manifest.idempotency_key")
            refusal("identical manifest hash cannot be relabeled to another stage",
                record_manifest(cur, lease, "review", compiler_input, post_manifest,
                                rules, coord, uuid.uuid4()),
                "ASSURANCE_STAGE_MISMATCH", "manifest.manifest_hash")
            token_input = copy.deepcopy(compiler_input)
            token_input["assurance_slice"]["outcome"] = str(lease["lease_token"])
            refusal("nested manifest token refuses", record_manifest(
                cur, lease, "post_commit", token_input, post_manifest, rules, coord, uuid.uuid4()),
                "ASSURANCE_INPUT_INVALID", "assurance.token_nondisclosure")
            unsupported_input = copy.deepcopy(compiler_input)
            unsupported_input["assurance_slice"]["reviewer_policy"]["minimum_independent_reviewers"] = 2
            normalize_contract(unsupported_input["assurance_slice"])
            unsupported = compile_canonical(unsupported_input)
            refusal("two-reviewer policy refuses against one-reviewer Passport",
                record_manifest(cur, lease, "post_commit", unsupported_input, unsupported,
                                rules, coord, uuid.uuid4()),
                "REVIEWER_POLICY_UNSUPPORTED", "manifest.slice.reviewer_policy")
            bad_hash = copy.deepcopy(post_manifest); bad_hash["manifest_hash"] = "sha256:" + "0" * 64
            refusal("manifest output forgery is causal", record_manifest(
                cur, lease, "post_commit", compiler_input, bad_hash, rules, coord, uuid.uuid4()),
                "ASSURANCE_INPUT_INVALID", "manifest.compiler_output")
            bad_fence = dict(lease); bad_fence["fencing_generation"] += 1
            stale = record_manifest(cur, bad_fence, "post_commit", compiler_input,
                                    post_manifest, rules, coord, uuid.uuid4())
            refusal("A2 stale fence refusal is preserved", stale,
                    "FENCING_GENERATION_STALE", "lease.fencing_generation")
            check("lease token is never returned", str(lease["lease_token"]) not in safe(post) and str(lease["lease_token"]) not in safe(stale))

            current = call(cur, "ops.assurance_manifest_currentness", (
                post_id, "post_commit", "a" * 40, "b" * 40,
                rules["snapshot_digest"], coord["snapshot_digest"], lease["lease_token"],
            ))
            check("exact post-commit currentness is non-authorizing", current.get("ok") is True and current.get("authorizes_action") is False)
            refusal("stage mismatch refuses", call(cur, "ops.assurance_manifest_currentness", (
                post_id, "push", "a" * 40, "b" * 40, rules["snapshot_digest"], coord["snapshot_digest"], lease["lease_token"],
            )), "ASSURANCE_STAGE_MISMATCH", "manifest.repository_stage")
            refusal("resulting commit makes old manifest stale", call(cur, "ops.assurance_manifest_currentness", (
                post_id, "post_commit", "c" * 40, "b" * 40, rules["snapshot_digest"], coord["snapshot_digest"], lease["lease_token"],
            )), "ASSURANCE_BINDING_STALE", "manifest.repository")
            refusal("rule snapshot drift refuses", call(cur, "ops.assurance_manifest_currentness", (
                post_id, "post_commit", "a" * 40, "b" * 40, "sha256:" + "f" * 64, coord["snapshot_digest"], lease["lease_token"],
            )), "ASSURANCE_RULE_SNAPSHOT_STALE", "manifest.applicable_rule_snapshot_digest")
            refusal("coordination snapshot drift refuses", call(cur, "ops.assurance_manifest_currentness", (
                post_id, "post_commit", "a" * 40, "b" * 40, rules["snapshot_digest"], "sha256:" + "f" * 64, lease["lease_token"],
            )), "ASSURANCE_COORDINATION_SNAPSHOT_STALE", "manifest.coordination_snapshot_digest")

            expired_coord = make_coord(cur, lease, session, host, seconds=1)
            expired_input, expired_manifest = compile_input(
                cur, lease, plan, rules, expired_coord, session, host, commit="1" * 40)
            expired_row = record_manifest(cur, lease, "write", expired_input,
                expired_manifest, rules, expired_coord, uuid.uuid4())
            expired_id = manifest_id(expired_row)
            one(cur, "select pg_sleep(1.1)")
            renewed = call(cur, "ops.renew_canonical_ownership_lease", (
                lease["lease_id"], lease["lease_token"], lease["fencing_generation"], 900,
            ))
            check("A2 lease renews after manifest snapshot", renewed.get("ok") is True)
            refusal("expired snapshot refuses despite renewed A2 lease", call(cur, "ops.assurance_manifest_currentness", (
                expired_id, "write", "1" * 40, "b" * 40, rules["snapshot_digest"], expired_coord["snapshot_digest"], lease["lease_token"],
            )), "ASSURANCE_SNAPSHOT_EXPIRED", "manifest.snapshot_valid_until")

            # Evidence has one canonical home for each observed field. Requirement
            # coverage points at those homes through pinned JSON pointers.
            artifact_kind = post_manifest["slice"]["evidence_requirements"][0]["artifact_kind"]
            required_fields = post_manifest["slice"]["evidence_requirements"][0]["required_fields"]
            pinned = {
                "argv": "/command/argv", "cwd": "/command/cwd",
                "commit_sha": "/repository/commit_sha", "tree_sha": "/repository/tree_sha",
                "environment": "/environment", "toolchain": "/toolchain",
                "output": "/output", "timestamps": "/timestamps", "artifacts": "/artifacts",
                "exit_code": "/output/exit_code", "stdout_digest": "/output/stdout_digest",
                "stderr_digest": "/output/stderr_digest",
            }
            evidence = {
                "schema_version": "assurance-evidence.v1",
                "manifest_hash": post_manifest["manifest_hash"],
                "engineering_receipt_digest": one(cur, "select receipt_digest from ops.engineering_slice_receipt where id=%s", (receipt_id,))[0],
                "repository": {"commit_sha": "a" * 40, "tree_sha": "b" * 40, "stage": "post_commit"},
                "command": {"argv": ["python3", "-m", "pytest", "-q"], "cwd": "."},
                "environment": {"environment_ref": "environment:repository-python-lock",
                                "network_access": False},
                "toolchain": {"runtime": "python3", "runtime_version": "3.12",
                              "database": "postgresql", "database_version": "17"},
                "output": {"exit_code": 0, "stdout_digest": "sha256:" + "3" * 64, "stderr_digest": "sha256:" + "4" * 64},
                "timestamps": {"started_at": iso(datetime.now(timezone.utc)), "finished_at": iso(datetime.now(timezone.utc))},
                "artifacts": [{"artifact_ref": "artifact:a3a-test-receipt", "path": "out/a3a-test-receipt.json", "digest": "sha256:" + "5" * 64, "artifact_kind": artifact_kind}],
                "requirements": [{
                    "evidence_ref": post_manifest["slice"]["evidence_requirements"][0]["evidence_ref"],
                    "artifact_kind": artifact_kind,
                    "field_bindings": {field: pinned[field] for field in required_fields},
                    "artifact_refs": ["artifact:a3a-test-receipt"],
                }],
                "fencing_generation": lease["fencing_generation"],
            }
            evidence_key = uuid.uuid4(); evidence_digest = digest(evidence)
            ev = call(cur, "ops.record_assurance_evidence_extension", (
                receipt_id, post_id, lease["lease_token"], Jsonb(evidence), evidence_digest, evidence_key,
            ))
            check("one post-commit evidence extension persists", ev.get("ok") is True and ev.get("replayed") is False)
            ev_id = uuid.UUID(str(ev["evidence_id"]))
            ev_replay = call(cur, "ops.record_assurance_evidence_extension", (
                receipt_id, post_id, lease["lease_token"], Jsonb(evidence), evidence_digest, evidence_key,
            ))
            check("evidence exact replay is idempotent", ev_replay.get("replayed") is True and ev_replay.get("evidence_id") == ev.get("evidence_id"))
            changed_ev = copy.deepcopy(evidence); changed_ev["output"]["exit_code"] = 9
            refusal("evidence key reuse with changed content refuses", call(cur, "ops.record_assurance_evidence_extension", (
                receipt_id, post_id, lease["lease_token"], Jsonb(changed_ev), digest(changed_ev), evidence_key,
            )), "IDEMPOTENCY_CONFLICT", "assurance_evidence_extension.idempotency_key")
            refusal("second evidence for one receipt refuses", call(cur, "ops.record_assurance_evidence_extension", (
                receipt_id, post_id, lease["lease_token"], Jsonb(evidence), evidence_digest, uuid.uuid4(),
            )), "ASSURANCE_BINDING_STALE", "assurance_evidence_extension.one_to_one")
            bad_pointer = copy.deepcopy(evidence); bad_pointer["requirements"][0]["field_bindings"][required_fields[0]] = "/output"
            refusal("unpinned evidence pointer refuses", call(cur, "ops.record_assurance_evidence_extension", (
                receipt_id, post_id, lease["lease_token"], Jsonb(bad_pointer), digest(bad_pointer), uuid.uuid4(),
            )), "EVIDENCE_POINTER_INVALID", "evidence.field_bindings." + required_fields[0])
            bad_kind = copy.deepcopy(evidence); bad_kind["requirements"][0]["artifact_kind"] = "artifact:wrong"
            refusal("requirement artifact-kind drift refuses", call(cur, "ops.record_assurance_evidence_extension", (
                receipt_id, post_id, lease["lease_token"], Jsonb(bad_kind), digest(bad_kind), uuid.uuid4(),
            )), "EVIDENCE_REQUIREMENT_MISMATCH",
                "evidence.requirements." + post_manifest["slice"]["evidence_requirements"][0]["evidence_ref"])
            token_evidence = copy.deepcopy(evidence)
            token_evidence["toolchain"]["runtime_version"] = str(lease["lease_token"])
            refusal("nested evidence token refuses", call(cur, "ops.record_assurance_evidence_extension", (
                receipt_id, post_id, lease["lease_token"], Jsonb(token_evidence),
                digest(token_evidence), uuid.uuid4(),
            )), "ASSURANCE_INPUT_INVALID", "evidence")

            review_coord = make_coord(cur, lease, session, host, seconds=240)
            review_input, review_manifest = compile_input(
                cur, lease, plan, rules, review_coord, session, host, commit="a" * 40)
            review_row = record_manifest(cur, lease, "review", review_input,
                review_manifest, rules, review_coord, uuid.uuid4())
            review_manifest_id = manifest_id(review_row)
            reviewer_fact_id, reviewer_session, reviewer_fact = one(cur,
                "select id,reviewer_session_ref,fact from ops.engineering_reviewer_fact where receipt_id=%s",
                (receipt_id,))
            review_host = "host:a3a-independent-review"
            set_context(cur, tenant, "joe", reviewer_session, review_host)
            review = {
                "schema_version": "assurance-review.v1", "manifest_hash": review_manifest["manifest_hash"],
                "evidence_digest": evidence_digest, "state": "passed", "self_issued": False,
                "owner_acceptance": False, "reviewer_actor_ref": "actor:joe",
                "reviewer_session_ref": reviewer_session, "reviewer_host_ref": review_host,
                "evidence_refs": reviewer_fact["evidence_refs"],
                "reviewed_deviation_refs": reviewer_fact["reviewed_deviation_refs"],
                "resolved_deviation_refs": reviewer_fact["resolved_deviation_refs"],
                "reviewed_at": iso(datetime.now(timezone.utc)),
            }
            review_key = uuid.uuid4(); review_digest = digest(review)
            review_result = call(cur, "ops.record_assurance_review_extension", (
                reviewer_fact_id, review_manifest_id, ev_id, Jsonb(review), review_digest, review_key,
            ))
            check("independent review extends existing Passport fact", review_result.get("ok") is True)
            review_replay = call(cur, "ops.record_assurance_review_extension", (
                reviewer_fact_id, review_manifest_id, ev_id, Jsonb(review), review_digest, review_key,
            ))
            check("review exact replay is idempotent", review_replay.get("replayed") is True)
            token_review = copy.deepcopy(review)
            token_review["evidence_refs"] = [str(lease["lease_token"])]
            refusal("nested review token refuses", call(cur, "ops.record_assurance_review_extension", (
                reviewer_fact_id, review_manifest_id, ev_id, Jsonb(token_review),
                digest(token_review), uuid.uuid4(),
            )), "ASSURANCE_INPUT_INVALID", "review")
            changed_review = copy.deepcopy(review); changed_review["reviewed_at"] = "2099-01-01T00:00:00Z"
            refusal("review key reuse with changed content refuses", call(cur, "ops.record_assurance_review_extension", (
                reviewer_fact_id, review_manifest_id, ev_id, Jsonb(changed_review), digest(changed_review), review_key,
            )), "ASSURANCE_INPUT_INVALID", "review.reviewed_at")
        conn.commit()

        # Owner authority is session_user-derived. For this disposable database
        # only, a test login assumes the migration-owner role while retaining
        # carr_authority_joe as session_user; no production role is created.
        with conn.cursor() as cur:
            owner_role = one(cur, "select current_user")[0]
            cur.execute("do $$ begin if not exists(select 1 from pg_roles where rolname='carr_authority_joe') then create role carr_authority_joe login; end if; end $$")
            cur.execute(f"grant {psycopg.sql.Identifier(owner_role).as_string(cur)} to carr_authority_joe")
        conn.commit()
        with conn.cursor() as cur:
            cur.execute("set session authorization carr_authority_joe")
            cur.execute(psycopg.sql.SQL("set role {}").format(psycopg.sql.Identifier(owner_role)))
            set_context(cur, tenant, "dell", "session:a3a:owner", host)
            acceptance = {
                "schema_version": "assurance-owner-acceptance.v1",
                "manifest_hash": review_manifest["manifest_hash"], "evidence_digest": evidence_digest,
                "review_digest": review_digest,
                "decision": "accept", "owner_acceptance": True, "independent_review": False,
                "actor_ref": "actor:joe", "session_ref": "session:a3a:owner", "host_ref": host,
                "reason": "fixture accepted", "decided_at": iso(datetime.now(timezone.utc)),
            }
            mismatch = call(cur, "ops.record_assurance_owner_acceptance", (
                review_manifest_id, ev_id, "accept", Jsonb(acceptance), digest(acceptance), uuid.uuid4(),
            ))
            refusal("Joe authority cannot use Dell trusted context", mismatch,
                    "OWNER_IDENTITY_MISMATCH", "owner.identity")
            set_context(cur, tenant, "joe", "session:a3a:owner", host)
            owner_key = uuid.uuid4()
            owner = call(cur, "ops.record_assurance_owner_acceptance", (
                review_manifest_id, ev_id, "accept", Jsonb(acceptance), digest(acceptance), owner_key,
            ))
            check("Joe owner acceptance persists separately", owner.get("ok") is True and owner.get("decision") == "accept")
            owner_replay = call(cur, "ops.record_assurance_owner_acceptance", (
                review_manifest_id, ev_id, "accept", Jsonb(acceptance), digest(acceptance), owner_key,
            ))
            check("owner exact replay is idempotent", owner_replay.get("replayed") is True)
            token_owner = copy.deepcopy(acceptance)
            token_owner["reason"] = str(lease["lease_token"])
            refusal("nested owner token refuses", call(cur, "ops.record_assurance_owner_acceptance", (
                review_manifest_id, ev_id, "accept", Jsonb(token_owner),
                digest(token_owner), uuid.uuid4(),
            )), "ASSURANCE_INPUT_INVALID", "assurance.token_nondisclosure")
            changed_owner = copy.deepcopy(acceptance); changed_owner["decision"] = "hold"
            refusal("owner key reuse with changed content refuses", call(cur, "ops.record_assurance_owner_acceptance", (
                review_manifest_id, ev_id, "hold", Jsonb(changed_owner), digest(changed_owner), owner_key,
            )), "IDEMPOTENCY_CONFLICT",
                "assurance_owner_acceptance_fact.idempotency_key")
            cur.execute("reset role")
            cur.execute("reset session authorization")
        conn.commit()

        setup_codex = lambda cur: set_context(cur, tenant, "codex", session, host)
        with conn.cursor() as cur:
            setup_codex(cur)
            race_coord = make_coord(cur, lease, session, host, seconds=180)
            race_input, race_manifest = compile_input(
                cur, lease, plan, rules, race_coord, session, host,
                commit="9" * 40, tree="8" * 40)
            race_key = uuid.uuid4()
        conn.commit()
        manifest_race = race_calls(dsn, [setup_codex, setup_codex],
            "ops.record_assurance_execution_manifest", (
                lease["lease_id"], lease["lease_token"], lease["fencing_generation"],
                "push", Jsonb(race_input), Jsonb(race_manifest),
                Jsonb(rules), Jsonb(race_coord), race_key))
        check("manifest two-connection exact replay is serialized",
              len(manifest_race) == 2
              and all(row.get("ok") is True for row in manifest_race)
              and len({row.get("manifest_id") for row in manifest_race}) == 1
              and sorted(row.get("replayed") for row in manifest_race) == [False, True])
        evidence_race = race_calls(dsn, [setup_codex, setup_codex],
            "ops.record_assurance_evidence_extension", (
                receipt_id, post_id, lease["lease_token"], Jsonb(evidence),
                evidence_digest, evidence_key))
        check("evidence two-connection exact replay is serialized",
              len(evidence_race) == 2 and all(row.get("replayed") is True for row in evidence_race))
        setup_review = lambda cur: set_context(cur, tenant, "joe", reviewer_session, review_host)
        review_race = race_calls(dsn, [setup_review, setup_review],
            "ops.record_assurance_review_extension", (
                reviewer_fact_id, review_manifest_id, ev_id, Jsonb(review),
                review_digest, review_key))
        check("review two-connection exact replay is serialized",
              len(review_race) == 2 and all(row.get("replayed") is True for row in review_race))
        def setup_owner(cur):
            cur.execute("set session authorization carr_authority_joe")
            cur.execute(psycopg.sql.SQL("set role {}").format(
                psycopg.sql.Identifier(owner_role)))
            set_context(cur, tenant, "joe", "session:a3a:owner", host)
        owner_race = race_calls(dsn, [setup_owner, setup_owner],
            "ops.record_assurance_owner_acceptance", (
                review_manifest_id, ev_id, "accept", Jsonb(acceptance),
                digest(acceptance), owner_key))
        check("owner two-connection exact replay is serialized",
              len(owner_race) == 2 and all(row.get("replayed") is True for row in owner_race))

        with conn.cursor() as cur:
            persisted = one(cur, """select jsonb_build_object(
              'manifest',coalesce((select jsonb_agg(to_jsonb(t)) from ops.assurance_execution_manifest t),'[]'::jsonb),
              'evidence',coalesce((select jsonb_agg(to_jsonb(t)) from ops.assurance_evidence_extension t),'[]'::jsonb),
              'review',coalesce((select jsonb_agg(to_jsonb(t)) from ops.assurance_review_extension t),'[]'::jsonb),
              'owner',coalesce((select jsonb_agg(to_jsonb(t)) from ops.assurance_owner_acceptance_fact t),'[]'::jsonb))""")[0]
            assert_secret_absent(persisted, "all persisted A3a JSON/text columns")
            check("raw A3a results and exceptions contain no registered token",
                  all(token not in json.dumps(RAW_RESULTS, default=str)
                      and all(token not in error for error in RAW_ERRORS)
                      for token in SECRET_TOKENS))
            check("owner acceptance cannot satisfy review structurally", one(cur,
                "select count(*) from ops.assurance_review_extension")[0] == 1 and one(cur,
                "select count(*) from ops.assurance_owner_acceptance_fact")[0] == 1)
            for table in ["assurance_execution_manifest", "assurance_evidence_extension",
                          "assurance_review_extension", "assurance_owner_acceptance_fact"]:
                for verb in ("update", "delete"):
                    cur.execute("savepoint append_only")
                    try:
                        statement = ("update ops.{} set created_at=created_at where true"
                                     if verb == "update" else "delete from ops.{} where true")
                        cur.execute(psycopg.sql.SQL(statement).format(
                            psycopg.sql.Identifier(table)))
                    except psycopg.Error as exc:
                        cur.execute("rollback to savepoint append_only")
                        check(f"{table} rejects {verb}", "append-only" in str(exc))
                    else:
                        cur.execute("rollback to savepoint append_only")
                        check(f"{table} rejects {verb}", False, f"{verb} succeeded")
            acl = one(cur, """select
              has_table_privilege('carr_reader','ops.assurance_execution_manifest','select'),
              has_table_privilege('carr_writer','ops.assurance_evidence_extension','insert'),
              has_table_privilege('carr_jobs','ops.assurance_review_extension','insert'),
              has_table_privilege('carr_authority','ops.assurance_owner_acceptance_fact','insert'),
              has_function_privilege('carr_writer','ops.record_assurance_execution_manifest(uuid,uuid,bigint,text,jsonb,jsonb,jsonb,jsonb,uuid)','execute'),
              has_function_privilege('carr_authority','ops.record_assurance_owner_acceptance(uuid,uuid,text,jsonb,text,uuid)','execute')
            """)
            check("all A3a persistence doors remain dark", not any(acl))

    summary = {
        "contract": "assurance-evidence-acceptance-local-pg.v1",
        "passed": PASS, "failed": FAIL, "refusals": dict(sorted(REFUSALS.items())),
        "token_nondisclosure": all(token not in json.dumps(REFUSALS) for token in SECRET_TOKENS),
        "provider": "disposable_loopback_postgresql",
    }
    print(json.dumps(summary, sort_keys=True))
    if FAILED_LABELS:
        print("assurance A3a failed labels: " + " | ".join(FAILED_LABELS),
              file=sys.stderr)
    return 1 if FAIL else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"assurance A3a gate failed: {safe(exc)}", file=sys.stderr)
        raise SystemExit(1)
