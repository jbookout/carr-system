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
import uuid

import psycopg
from psycopg.types.json import Jsonb


ROOT = Path(__file__).resolve().parents[1]
PASS = 0
FAIL = 0
REFUSALS: dict[str, int] = {}
SECRET_TOKENS: set[str] = set()


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
        print(f"  FAIL  {label}  {detail}")


def refusal(label: str, value: dict, code: str) -> None:
    actual = value.get("refusal", {}).get("code") if isinstance(value, dict) else None
    shape = set(value.get("refusal", {})) if isinstance(value, dict) else set()
    check(label, value.get("ok") is False and actual == code and shape == {
        "code", "causal_object", "expected", "actual"
    }, f"actual={actual} value={safe(value)}")
    REFUSALS[code] = REFUSALS.get(code, 0) + 1


def safe(value: object) -> str:
    rendered = json.dumps(value, default=str, sort_keys=True)
    for token in SECRET_TOKENS:
        rendered = rendered.replace(token, "<redacted-token>")
    return rendered[:1000]


def call(cur, signature: str, args: tuple):
    placeholders = ",".join(["%s"] * len(args))
    return one(cur, f"select {signature}({placeholders})", args)[0]


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


def make_coord(lease: dict, session: str, host: str, *, seconds: int = 300, seed: str = "a") -> dict:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    value = {
        "schema_version": "assurance-coordination-snapshot.v1",
        "as_of": iso(now),
        "valid_until": iso(now + timedelta(seconds=seconds)),
        "manifest_phase": "baseline",
        "requesting_session_id": session,
        "requesting_host_id": host,
        "leases": [{
            "lease_id": f"lease:{lease['lease_id']}",
            "state": "active",
            "holder_session_id": session,
            "holder_host_id": host,
            "expires_at": str(lease["expires_at"]).replace("+00:00", "Z"),
            "fencing_generation": lease["fencing_generation"],
            "claims": [{"path": "ops/a3a-fixture.sql", "mode": "file", "operation": "write"}],
        }],
        "dependencies": [],
    }
    value["snapshot_digest"] = digest(value)
    return value


def template_manifest() -> dict:
    compiler = load_module("a3a_compiler", ROOT / "tools/room-bridge/assurance_slice_compiler.py")
    fixture = json.loads((ROOT / "control-room/contracts/fixtures/execution-fabric/assurance-compiler.valid.v1.json").read_text())
    compiled = compiler.compile_assurance_slice(fixture)
    if compiled.get("ok") is not True:
        raise RuntimeError(f"landed A1a fixture no longer compiles: {compiled}")
    return compiled["manifest"]


def make_manifest(
    base: dict, *, lease_row: tuple, plan_row: tuple, work_row: tuple,
    plan_ref: str, stage: str, rules: dict, coord: dict, session: str, host: str,
    seed: str,
) -> dict:
    lease_id, fence, contract_digest = lease_row
    slice_plan_id, slice_ref, plan_digest, accepted_plan_digest, accepted_revision = plan_row
    work_id, work_version, work_digest = work_row
    value = copy.deepcopy(base)
    value["compiler"]["version"] = "1.0.0"
    value["input_digest"] = "sha256:" + seed * 64
    value["input_bindings"]["work_request"] = {
        "id": f"wr:{work_id}", "state_version": work_version,
        "canonical_record_digest": work_digest,
    }
    value["input_bindings"]["accepted_plan_revision"] = {
        "id": plan_ref, "revision": accepted_revision, "digest": accepted_plan_digest,
    }
    value["input_bindings"]["engineering_slice_plan_digest"] = plan_digest
    value["input_bindings"]["assurance_slice_contract_digest"] = contract_digest
    value["input_bindings"]["repository"] = {
        "repository_id": "repo:jbookout-carr-system",
        "commit_sha": "a" * 40,
        "tree_sha": "b" * 40,
    }
    value["input_bindings"]["applicable_rule_snapshot_digest"] = rules["snapshot_digest"]
    value["input_bindings"]["coordination_snapshot_digest"] = coord["snapshot_digest"]
    value["slice"]["slice_ref"] = slice_ref
    value["slice"]["lease_binding"] = {
        "lease_id": f"lease:{lease_id}", "fencing_generation": fence,
        "holder_session_id": session, "holder_host_id": host,
    }
    value["slice"]["executor_identity"] = {
        "actor_ref": "actor:codex", "session_ref": session, "host_ref": host,
    }
    value["slice"]["reviewer_policy"] = {
        "minimum_independent_reviewers": 1,
        "executor_actor_ref": "actor:codex", "executor_session_ref": session,
        "owner_acceptance_is_review": False, "distinct_actor_and_session": True,
    }
    value["currentness"]["declared_evaluation_time"] = coord["as_of"]
    value["currentness"]["snapshot_as_of"] = coord["as_of"]
    value["currentness"]["snapshot_valid_until"] = coord["valid_until"]
    value["currentness"]["lease_expires_at"] = coord["leases"][0]["expires_at"]
    value["manifest_hash"] = digest({k: v for k, v in value.items() if k != "manifest_hash"})
    return value


def record_manifest(cur, lease: dict, stage: str, manifest: dict, rules: dict, coord: dict, key: uuid.UUID):
    return call(cur, "ops.record_assurance_execution_manifest", (
        lease["lease_id"], lease["lease_token"], lease["fencing_generation"], stage,
        Jsonb(manifest), Jsonb(rules), Jsonb(coord), key,
    ))


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

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            a2.context(cur, tenant)
            fixture = a2.fixture(cur, slice_refs=["slice:a3a-subject"])
        conn.commit()
        with conn.cursor() as cur:
            session = one(cur, "select envelope#>>'{agent_session,id}' from ops.engineering_execution_envelope where id=%s", (fixture[1],))[0]
            set_context(cur, tenant, "codex", session, host)
            bound = a2.binding(cur, fixture[1])
            lease = a2.acquire(cur, bound, paths=[{
                "path": "ops/a3a-fixture.sql", "mode": "file", "operation": "write"
            }], contract=a2.sha("9"))
            if lease.get("ok") is not True:
                raise RuntimeError(f"A2 fixture lease failed: {safe(lease)}")
            SECRET_TOKENS.add(str(lease["lease_token"]))
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

        base = template_manifest()
        with conn.cursor() as cur:
            set_context(cur, tenant, "codex", session, host)
            lease_row = one(cur, "select id,fencing_generation,contract_digest from ops.canonical_ownership_lease where id=%s", (lease["lease_id"],))
            plan_row = one(cur, "select id,slice_ref,slice_plan_digest,accepted_plan_digest,(select plan_version from ops.sourced_work_request_plan where id=accepted_plan_id) from ops.canonical_ownership_lease where id=%s", (lease["lease_id"],))
            work_row = one(cur, "select work_request_id,work_request_version,work_request_digest from ops.canonical_ownership_lease where id=%s", (lease["lease_id"],))
            plan_ref = one(cur, "select plan_ref from ops.sourced_work_request_plan where id=%s", (bound[3],))[0]
            rules = make_rules("a")
            coord = make_coord(lease, session, host)
            post_manifest = make_manifest(base, lease_row=lease_row, plan_row=plan_row,
                work_row=work_row, plan_ref=plan_ref, stage="post_commit", rules=rules,
                coord=coord, session=session, host=host, seed="1")
            post_key = uuid.uuid4()
            post = record_manifest(cur, lease, "post_commit", post_manifest, rules, coord, post_key)
            post_id = manifest_id(post)
            check("post-commit manifest persists", post.get("replayed") is False)
            replay = record_manifest(cur, lease, "post_commit", post_manifest, rules, coord, post_key)
            check("manifest exact replay is idempotent", replay.get("ok") is True and replay.get("replayed") is True and replay.get("manifest_id") == post.get("manifest_id"))
            changed = copy.deepcopy(post_manifest); changed["compiler"]["version"] = "changed"
            changed["manifest_hash"] = digest({k: v for k, v in changed.items() if k != "manifest_hash"})
            refusal("manifest key reuse with changed content refuses", record_manifest(cur, lease, "post_commit", changed, rules, coord, post_key), "IDEMPOTENCY_CONFLICT")
            refusal("identical manifest hash cannot be relabeled to another stage", record_manifest(cur, lease, "review", post_manifest, rules, coord, uuid.uuid4()), "ASSURANCE_STAGE_MISMATCH")
            unsupported = copy.deepcopy(post_manifest)
            unsupported["slice"]["reviewer_policy"]["minimum_independent_reviewers"] = 2
            unsupported["manifest_hash"] = digest({k: v for k, v in unsupported.items() if k != "manifest_hash"})
            refusal("two-reviewer policy refuses against one-reviewer Passport", record_manifest(cur, lease, "post_commit", unsupported, rules, coord, uuid.uuid4()), "REVIEWER_POLICY_UNSUPPORTED")
            bad_hash = copy.deepcopy(post_manifest); bad_hash["manifest_hash"] = "sha256:" + "0" * 64
            refusal("manifest hash mismatch is causal", record_manifest(cur, lease, "post_commit", bad_hash, rules, coord, uuid.uuid4()), "ASSURANCE_DIGEST_MISMATCH")
            bad_fence = dict(lease); bad_fence["fencing_generation"] += 1
            stale = record_manifest(cur, bad_fence, "post_commit", post_manifest, rules, coord, uuid.uuid4())
            refusal("A2 stale fence refusal is preserved", stale, "FENCING_GENERATION_STALE")
            check("lease token is never returned", str(lease["lease_token"]) not in safe(post) and str(lease["lease_token"]) not in safe(stale))

            current = call(cur, "ops.assurance_manifest_currentness", (
                post_id, "post_commit", "a" * 40, "b" * 40,
                rules["snapshot_digest"], coord["snapshot_digest"], lease["lease_token"],
            ))
            check("exact post-commit currentness is non-authorizing", current.get("ok") is True and current.get("authorizes_action") is False)
            refusal("stage mismatch refuses", call(cur, "ops.assurance_manifest_currentness", (
                post_id, "push", "a" * 40, "b" * 40, rules["snapshot_digest"], coord["snapshot_digest"], lease["lease_token"],
            )), "ASSURANCE_STAGE_MISMATCH")
            refusal("resulting commit makes old manifest stale", call(cur, "ops.assurance_manifest_currentness", (
                post_id, "post_commit", "c" * 40, "b" * 40, rules["snapshot_digest"], coord["snapshot_digest"], lease["lease_token"],
            )), "ASSURANCE_BINDING_STALE")
            refusal("rule snapshot drift refuses", call(cur, "ops.assurance_manifest_currentness", (
                post_id, "post_commit", "a" * 40, "b" * 40, "sha256:" + "f" * 64, coord["snapshot_digest"], lease["lease_token"],
            )), "ASSURANCE_RULE_SNAPSHOT_STALE")
            refusal("coordination snapshot drift refuses", call(cur, "ops.assurance_manifest_currentness", (
                post_id, "post_commit", "a" * 40, "b" * 40, rules["snapshot_digest"], "sha256:" + "f" * 64, lease["lease_token"],
            )), "ASSURANCE_COORDINATION_SNAPSHOT_STALE")

            expired_coord = make_coord(lease, session, host, seconds=1, seed="x")
            expired_manifest = make_manifest(base, lease_row=lease_row, plan_row=plan_row,
                work_row=work_row, plan_ref=plan_ref, stage="write", rules=rules,
                coord=expired_coord, session=session, host=host, seed="2")
            expired_row = record_manifest(cur, lease, "write", expired_manifest, rules, expired_coord, uuid.uuid4())
            expired_id = manifest_id(expired_row)
            one(cur, "select pg_sleep(1.1)")
            renewed = call(cur, "ops.renew_canonical_ownership_lease", (
                lease["lease_id"], lease["lease_token"], lease["fencing_generation"], 900,
            ))
            check("A2 lease renews after manifest snapshot", renewed.get("ok") is True)
            refusal("expired snapshot refuses despite renewed A2 lease", call(cur, "ops.assurance_manifest_currentness", (
                expired_id, "write", "a" * 40, "b" * 40, rules["snapshot_digest"], expired_coord["snapshot_digest"], lease["lease_token"],
            )), "ASSURANCE_SNAPSHOT_EXPIRED")

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
                "environment": {"runtime": "python3", "network": "disabled"},
                "toolchain": {"python": "3.12", "postgres": "17"},
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
            )), "IDEMPOTENCY_CONFLICT")
            refusal("second evidence for one receipt refuses", call(cur, "ops.record_assurance_evidence_extension", (
                receipt_id, post_id, lease["lease_token"], Jsonb(evidence), evidence_digest, uuid.uuid4(),
            )), "EVIDENCE_STAGE_UNSUPPORTED")
            bad_pointer = copy.deepcopy(evidence); bad_pointer["requirements"][0]["field_bindings"][required_fields[0]] = "/output"
            refusal("unpinned evidence pointer refuses", call(cur, "ops.record_assurance_evidence_extension", (
                receipt_id, post_id, lease["lease_token"], Jsonb(bad_pointer), digest(bad_pointer), uuid.uuid4(),
            )), "EVIDENCE_POINTER_INVALID")
            bad_kind = copy.deepcopy(evidence); bad_kind["requirements"][0]["artifact_kind"] = "artifact:wrong"
            refusal("requirement artifact-kind drift refuses", call(cur, "ops.record_assurance_evidence_extension", (
                receipt_id, post_id, lease["lease_token"], Jsonb(bad_kind), digest(bad_kind), uuid.uuid4(),
            )), "EVIDENCE_REQUIREMENT_MISMATCH")

            review_coord = make_coord(lease, session, host, seed="r")
            review_manifest = make_manifest(base, lease_row=lease_row, plan_row=plan_row,
                work_row=work_row, plan_ref=plan_ref, stage="review", rules=rules,
                coord=review_coord, session=session, host=host, seed="6")
            review_row = record_manifest(cur, lease, "review", review_manifest, rules, review_coord, uuid.uuid4())
            review_manifest_id = manifest_id(review_row)
            reviewer_fact_id = one(cur, "select id from ops.engineering_reviewer_fact where receipt_id=%s", (receipt_id,))[0]
            review = {
                "schema_version": "assurance-review.v1", "manifest_hash": review_manifest["manifest_hash"],
                "evidence_digest": evidence_digest, "state": "passed", "self_issued": False,
                "owner_acceptance": False, "evidence_refs": ["evidence:a3a"],
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
            changed_review = copy.deepcopy(review); changed_review["reviewed_at"] = "2099-01-01T00:00:00Z"
            refusal("review key reuse with changed content refuses", call(cur, "ops.record_assurance_review_extension", (
                reviewer_fact_id, review_manifest_id, ev_id, Jsonb(changed_review), digest(changed_review), review_key,
            )), "IDEMPOTENCY_CONFLICT")
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
                "decision": "accept", "owner_acceptance": True, "independent_review": False,
                "actor_ref": "actor:joe", "session_ref": "session:a3a:owner", "host_ref": host,
                "reason": "fixture accepted", "decided_at": iso(datetime.now(timezone.utc)),
            }
            mismatch = call(cur, "ops.record_assurance_owner_acceptance", (
                review_manifest_id, ev_id, "accept", Jsonb(acceptance), digest(acceptance), uuid.uuid4(),
            ))
            refusal("Joe authority cannot use Dell trusted context", mismatch, "OWNER_IDENTITY_MISMATCH")
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
            changed_owner = copy.deepcopy(acceptance); changed_owner["decision"] = "hold"
            refusal("owner key reuse with changed content refuses", call(cur, "ops.record_assurance_owner_acceptance", (
                review_manifest_id, ev_id, "hold", Jsonb(changed_owner), digest(changed_owner), owner_key,
            )), "IDEMPOTENCY_CONFLICT")
            cur.execute("reset role")
            cur.execute("reset session authorization")
        conn.commit()

        with conn.cursor() as cur:
            check("owner acceptance cannot satisfy review structurally", one(cur,
                "select count(*) from ops.assurance_review_extension")[0] == 1 and one(cur,
                "select count(*) from ops.assurance_owner_acceptance_fact")[0] == 1)
            for table in ["assurance_execution_manifest", "assurance_evidence_extension",
                          "assurance_review_extension", "assurance_owner_acceptance_fact"]:
                cur.execute("savepoint append_only")
                try:
                    cur.execute(psycopg.sql.SQL("update ops.{} set created_at=created_at where true").format(psycopg.sql.Identifier(table)))
                except psycopg.Error as exc:
                    cur.execute("rollback to savepoint append_only")
                    check(f"{table} rejects update", "append-only" in str(exc))
                else:
                    cur.execute("rollback to savepoint append_only")
                    check(f"{table} rejects update", False, "update succeeded")
            acl = one(cur, """select
              has_table_privilege('carr_reader','ops.assurance_execution_manifest','select'),
              has_table_privilege('carr_writer','ops.assurance_evidence_extension','insert'),
              has_table_privilege('carr_jobs','ops.assurance_review_extension','insert'),
              has_table_privilege('carr_authority','ops.assurance_owner_acceptance_fact','insert'),
              has_function_privilege('carr_writer','ops.record_assurance_execution_manifest(uuid,uuid,bigint,text,jsonb,jsonb,jsonb,uuid)','execute'),
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
    return 1 if FAIL else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"assurance A3a gate failed: {safe(exc)}", file=sys.stderr)
        raise SystemExit(1)
