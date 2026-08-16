#!/usr/bin/env python3
"""Disposable, loopback-only integration evidence for ``cc-update-audit``.

This is deliberately *not* a provider canary or workflow acceptance.  It creates
one fresh database on an ephemeral child of the pinned staging project, runs one
shadow ledger job against an in-process HTTP fake, prints a redacted evidence
envelope, and deletes the child branch in ``finally``.  ``--run`` is required;
the default is a no-network dry-run and ``--self-test`` is hermetic.
"""
from __future__ import annotations

import argparse, hashlib, importlib.util, json, os, re, secrets, subprocess, sys, threading, uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
_spec = importlib.util.spec_from_file_location("db_tap", REPO / "tools" / "db-tap.py")
assert _spec and _spec.loader
db_tap = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(db_tap)

WORKFLOW = "cc-update-audit"
INSTANT = "2026-08-17T14:45:00+00:00"
DB_NAME = "cc_shadow_check"


class HarnessRefusal(RuntimeError): pass


def safe_failure_detail(value: Any) -> str:
    """Keep the assertion message while stripping URLs and control characters."""
    if not isinstance(value, str):
        return "[missing]"
    clean = re.sub(r"\b(?:postgres(?:ql)?|https?)://\S+", "[url]", value)
    clean = " ".join(clean.split())
    return clean[-240:]


def redacted(value: Any) -> Any:
    """Permit only IDs/counts/booleans/numbers in the public evidence envelope."""
    if isinstance(value, dict): return {str(k): redacted(v) for k, v in value.items()}
    if isinstance(value, list): return [redacted(v) for v in value]
    if isinstance(value, str):
        return value if value in {"shadow", "succeeded", WORKFLOW, INSTANT, "primary", "secondary"} else "[redacted]"
    return value


def host_of(dsn: str) -> str:
    host = urlsplit(dsn).hostname
    if not host: raise HarnessRefusal("connection URL has no host")
    return host


def jobs_dsn(owner_dsn: str, password: str) -> str:
    parsed = urlsplit(owner_dsn)
    if parsed.scheme not in {"postgres", "postgresql"} or not parsed.hostname:
        raise HarnessRefusal("disposable database URL is not PostgreSQL")
    host = parsed.hostname if ":" not in parsed.hostname else f"[{parsed.hostname}]"
    if parsed.port: host += f":{parsed.port}"
    return urlunsplit((parsed.scheme, f"carr_jobs:{quote(password, safe='')}@{host}", parsed.path, parsed.query, ""))


def exact_branch_id(rows: Any, branch_name: str) -> str:
    """Resolve only our exact name; never use a prefix as a deletion target."""
    if not isinstance(rows, list):
        raise HarnessRefusal("branch listing is not an array")
    matches = [row for row in rows if isinstance(row, dict) and row.get("name") == branch_name]
    if len(matches) != 1 or not isinstance(matches[0].get("id"), str) or not matches[0]["id"]:
        raise HarnessRefusal("ephemeral branch cannot be resolved by its exact name")
    if matches[0].get("default") is True:
        raise HarnessRefusal("refusing to target a default branch")
    return matches[0]["id"]


def created_branch_id(output: str) -> str:
    """Best-effort parse only; cleanup resolves the exact name independently."""
    try:
        parsed = json.loads(output)
        branch = parsed.get("branch", parsed) if isinstance(parsed, dict) else {}
        value = branch.get("id", "") if isinstance(branch, dict) else ""
        return value if isinstance(value, str) else ""
    except json.JSONDecodeError:
        return ""


def cleanup_ephemeral_branch(*, created_ok: bool, branch_id: str, branch_name: str,
                             list_branches: Any, delete_branch: Any) -> None:
    """Delete exactly one known ephemeral branch, or fail closed.

    Dependencies are injected so malformed create output and teardown failures
    are tested without an external control-plane call.
    """
    if not created_ok:
        return
    # The create response is not an authority to delete.  Always resolve the
    # exact generated name from a fresh listing; a nonempty create ID is only a
    # cross-check that can make this fail closed before any delete occurs.
    target = exact_branch_id(list_branches(), branch_name)
    if branch_id and branch_id != target:
        raise HarnessRefusal("create response branch id differs from exact branch listing")
    if not delete_branch(target):
        raise HarnessRefusal("ephemeral branch teardown failed")


class FakeProvider:
    """Loopback-only typed provider.  It derives its citation from received input."""
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        parent = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_: Any) -> None: pass
            def do_POST(self) -> None:
                size = int(self.headers.get("content-length", "0"))
                try: body = json.loads(self.rfile.read(size))
                except json.JSONDecodeError: self.send_error(400); return
                parent.requests.append(body)
                facts = body.get("input", {}).get("facts", {})
                source = facts.get("release_source_ref")
                if not isinstance(source, str) or not source:
                    self.send_error(422); return
                payload = {"job_type":"audit.proposal", "schema_version":1,
                           "proposal":{"findings":[{"source_refs":[source], "action":"propose"}],
                                       "proposed_actions":[]},
                           "usage":{"total_tokens":7,"cost_usd":0.01}}
                encoded = json.dumps(payload).encode()
                self.send_response(200); self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(encoded))); self.end_headers(); self.wfile.write(encoded)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
    @property
    def url(self) -> str: return f"http://127.0.0.1:{self.server.server_port}/typed-proposal"
    def __enter__(self) -> "FakeProvider": self.thread.start(); return self
    def __exit__(self, *_: Any) -> None: self.server.shutdown(); self.thread.join(timeout=5); self.server.server_close()


def run(cmd: list[str], *, env: dict[str, str], timeout: int = 900) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=REPO, env=env, text=True, capture_output=True, timeout=timeout)


def psql(dsn: str, sql: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([db_tap.psql_bin(), "-v", "ON_ERROR_STOP=1", "-q", "-d", dsn, "-c", sql], text=True, capture_output=True, timeout=900)


def must(result: subprocess.CompletedProcess[str], label: str) -> None:
    if result.returncode: raise HarnessRefusal(f"{label} failed: {(result.stderr or result.stdout).strip()[-240:]}")


def preflight_migration_contract(env: dict[str, str]) -> None:
    """Check the committed frozen collision contract before provisioning a DB."""
    result = run([sys.executable, str(REPO / "ops" / "control-plane-migration-convergence-selftest.py")], env=env)
    must(result, "committed control-plane migration convergence contract")


def fixture_releases(dsn: str, token: str) -> tuple[str, str]:
    """Exact canonical fixture seam: two completed releases plus read-back deployments.

    This is intentionally owner-only and only targets the fresh disposable DB.
    It mirrors P0-1's release/deployment lifecycle rather than fabricating an
    input payload or weakening the collector.
    """
    key_a, key_b = f"cc-shadow-{token}-a", f"cc-shadow-{token}-b"
    sql = f"""
    do $$ declare svc uuid; r uuid; now_utc timestamptz := now(); k text;
                   highest text := (select max(filename) from schema_migrations);
    begin
      if highest is null then raise exception 'fixture requires a populated migration ledger'; end if;
      insert into ops.service(key,name,family,criticality,owner_actor)
        values ('cc-shadow-{token}','CC shadow fixture','Platform','critical','joe') returning id into svc;
      insert into ops.service_environment(service_id,environment,expected_cadence_seconds) values (svc,'production',86400);
      foreach k in array array['{key_a}','{key_b}'] loop
        insert into ops.release(correlation_id,release_key,service_id,environment,state,git_sha,artifact_digest,
          dependency_lock_digest,sbom_ref,schema_highest_migration,migration_set,config_fingerprint,
          declared_env_differences,asset_versions,maker_actor,maker_verification_ref,test_evidence_ref,
          security_evidence_ref,verifier_actor,verifier_evidence_ref,rollback_ready,rollback_plan_ref,
          work_request_ref,source_kind,source_ref,observed_at,expires_at)
        values (gen_random_uuid(),k,svc,'production','candidate',repeat('a',40),'sha256:'||repeat('b',64),
          'sha256:'||repeat('c',64),'sbom/fixture',highest,array[highest],'cfg:fixture','disposable fixture',
          '{{}}','codex','fixture:maker','fixture:test','fixture:security','joe','fixture:verify',true,
          'fixture:rollback','WR-FIXTURE','wrapper','fixture:release:'||k,now_utc,now_utc+interval '1 day') returning id into r;
        update ops.release set state='approved',plan_hash='plan:fixture',approved_by_actor='joe',approved_at=now_utc,
          approval_expires_at=now_utc+interval '1 day' where id=r;
        insert into ops.deployment(correlation_id,service_id,environment,state,git_sha,release_id,started_at,ended_at,
          read_back_at,verification_evidence_ref,source_kind,source_ref,observed_at,expires_at)
        values (gen_random_uuid(),svc,'production','complete',repeat('a',40),r,now_utc-interval '5 minutes',now_utc-interval '4 minutes',
          now_utc-interval '3 minutes','fixture:readback','wrapper','fixture:deployment',now_utc,now_utc+interval '1 day');
        update ops.release set state='complete',ended_at=now_utc + case when k='{key_b}' then interval '1 second' else interval '0 seconds' end where id=r;
      end loop;
    end $$;"""
    must(psql(dsn, sql), "completed release fixtures")
    return key_a, key_b


def assert_evidence(dsn: str, job_id: str, before_releases: tuple[int, str]) -> dict[str, Any]:
    import psycopg
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("select state,mode,definition_key from ops.job where id=%s", (job_id,)); job = cur.fetchone()
        cur.execute("select provider_route,input_tokens,output_tokens,cost_usd,state,failure_class,detail from ops.job_attempt where job_id=%s", (job_id,)); attempts = cur.fetchall()
        cur.execute("select evidence from ops.job_receipt where job_id=%s and kind='completion'", (job_id,)); receipt = cur.fetchone()
        cur.execute("select kind,count(*) from ops.job_receipt where job_id=%s group by kind order by kind", (job_id,)); receipt_kinds = cur.fetchall()
        cur.execute("select count(*) from ops.release"); release_row = cur.fetchone()
        cur.execute("select count(*) from ops.job where mode in ('live','canary')"); forbidden_row = cur.fetchone()
    if job != ('succeeded','shadow',WORKFLOW) or len(attempts) != 1 or not receipt:
        attempt_state = [(row[4], row[5], safe_failure_detail(row[6])) for row in attempts]
        raise HarnessRefusal(
            f"shadow evidence mismatch: job={job!r} attempts={attempt_state!r} receipts={receipt_kinds!r}")
    if release_row is None or forbidden_row is None: raise HarnessRefusal("post-run isolation counts were unavailable")
    release_count, forbidden = release_row[0], forbidden_row[0]
    if not isinstance(release_count, int) or not isinstance(forbidden, int): raise HarnessRefusal("post-run isolation counts were malformed")
    evidence = receipt[0]
    cognition = evidence.get('cognition') if isinstance(evidence, dict) else None
    proposal = evidence.get('proposal') if isinstance(evidence, dict) else None
    typed_input = evidence.get('input') if isinstance(evidence, dict) else None
    if not isinstance(cognition, dict) or cognition.get('canonical_write_authority') is not False: raise HarnessRefusal("receipt lacks proposal-only cognition contract")
    source = typed_input.get('facts', {}).get('release_source_ref') if isinstance(typed_input, dict) and isinstance(typed_input.get('facts'), dict) else None
    findings = proposal.get('findings') if isinstance(proposal, dict) else None
    if not isinstance(source, str) or not source or not isinstance(findings, list) or not findings:
        raise HarnessRefusal("receipt lacks typed release input or proposal findings")
    if any(not isinstance(row, dict) or row.get('source_refs') != [source] for row in findings):
        raise HarnessRefusal("proposal citations do not exactly reconcile to immutable typed input")
    assert_receipt_append_only(dsn, job_id)
    if (release_count, release_fingerprint(dsn)) != before_releases or forbidden != 0 or float(attempts[0][3]) > 4.0: raise HarnessRefusal("post-run isolation/cost assertion failed")
    return {"job_state":job[0],"mode":job[1],"workflow":job[2],"attempts":len(attempts),"cost_usd":float(attempts[0][3]),"release_count":release_count,"live_or_canary_jobs":forbidden,"completion_receipt":True}


def release_count(dsn: str) -> int:
    import psycopg
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("select count(*) from ops.release")
        row = cur.fetchone()
    if not row or not isinstance(row[0], int):
        raise HarnessRefusal("could not read disposable release fixture count")
    return row[0]


def release_fingerprint(dsn: str) -> str:
    """Canonical all-column release snapshot; no subset can hide a mutation."""
    import psycopg
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("select coalesce(jsonb_agg(to_jsonb(r) order by release_key)::text,'[]') from ops.release r")
        row = cur.fetchone()
    if not row or not isinstance(row[0], str): raise HarnessRefusal("could not fingerprint disposable releases")
    return fingerprint_release_snapshot(row[0])


def fingerprint_release_snapshot(canonical_all_column_json: str) -> str:
    """Hash a canonical all-column JSON snapshot returned by ``to_jsonb(r)``."""
    if not isinstance(canonical_all_column_json, str):
        raise HarnessRefusal("release fingerprint snapshot must be text")
    return hashlib.sha256(canonical_all_column_json.encode()).hexdigest()


def assert_receipt_append_only(dsn: str, job_id: str) -> None:
    """Exercise immutable receipt evidence without retaining a mutation."""
    import psycopg
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        assert_receipt_append_only_cursor(cur, job_id, psycopg.Error)


def assert_receipt_append_only_cursor(cur: Any, job_id: str, database_error: type[Exception]) -> None:
    """Cursor seam for the rollback-safe append-only negative assertion."""
    cur.execute("select 1 from pg_trigger where tgrelid='ops.job_receipt'::regclass and tgname='job_receipt_append_only' and not tgisinternal")
    if cur.fetchone() is None: raise HarnessRefusal("job receipt append-only trigger is absent")
    cur.execute("savepoint receipt_immutability")
    try:
        cur.execute("update ops.job_receipt set evidence=evidence where job_id=%s and kind='completion'", (job_id,))
    except database_error:
        cur.execute("rollback to savepoint receipt_immutability")
    else:
        cur.execute("rollback to savepoint receipt_immutability")
        raise HarnessRefusal("immutable completion receipt update was accepted")


def self_test() -> int:
    assert redacted({"dsn":"postgres://secret","mode":"shadow","n":1}) == {"dsn":"[redacted]","mode":"shadow","n":1}
    assert safe_failure_detail("failed at https://example.invalid/token\nnext") == "failed at [url] next"
    try: host_of("not-a-dsn")
    except HarnessRefusal: pass
    else: raise AssertionError("non-Postgres URL accepted")
    assert exact_branch_id([{"name":"cc-shadow-a","id":"branch-a","default":False}], "cc-shadow-a") == "branch-a"
    try: exact_branch_id([{"name":"cc-shadow-a","id":"branch-a","default":True}], "cc-shadow-a")
    except HarnessRefusal: pass
    else: raise AssertionError("default branch accepted as cleanup target")
    with FakeProvider() as fake:
        import urllib.request
        body = json.dumps({"input":{"facts":{"release_source_ref":"fixture:ref"}}}).encode()
        with urllib.request.urlopen(urllib.request.Request(fake.url,data=body,method="POST")) as response: result=json.loads(response.read())
        assert result["proposal"]["findings"][0]["source_refs"] == ["fixture:ref"] and len(fake.requests)==1
    print("cc-update-audit-shadow-harness-selftest: 5 cases passed")
    return 0


def main() -> int:
    ap=argparse.ArgumentParser(); ap.add_argument("--run",action="store_true"); ap.add_argument("--self-test",action="store_true"); args=ap.parse_args()
    if args.self_test: return self_test()
    if not args.run:
        print(json.dumps({"ok":True,"mode":"dry_run","workflow":WORKFLOW,"instant":INSTANT,"notice":"requires --run; no branch, database, provider, or schedule was touched"},sort_keys=True)); return 0
    key=db_tap._neon_api_key()
    if not key: raise HarnessRefusal("NEON API credential is required only for --run")
    env={**os.environ,"NEON_API_KEY":key,"PATH":"/usr/local/opt/node@22/bin:/opt/homebrew/bin:"+os.environ.get("PATH","")}
    preflight_migration_contract(env)
    staging, production=db_tap.PROJECTS["staging"],db_tap.PROJECTS["production"]
    project=staging.get("id") or db_tap._project_id_by_name(staging["name"],env)
    if not project or project==production.get("id"): raise HarnessRefusal("staging project is unresolved or production")
    default_staging=db_tap.dsn(project="staging"); prod_host=host_of(db_tap.dsn(project="production")); stg_host=host_of(default_staging)
    branch_id=""; token=uuid.uuid4().hex[:12]; branch_name=f"cc-shadow-{token}"; succeeded=False; created_ok=False
    try:
      created=run([db_tap.NEONCTL,"branches","create","--project-id",project,"--name",branch_name,"--output","json"],env=env,timeout=180); must(created,"ephemeral staging branch create")
      created_ok=True
      branch_id=created_branch_id(created.stdout)
      listed=run([db_tap.NEONCTL,"branches","list","--project-id",project,"--output","json"],env=env,timeout=180); must(listed,"branch list")
      branch_id=exact_branch_id(json.loads(listed.stdout),branch_name)
      conn=run([db_tap.NEONCTL,"connection-string",branch_id,"--project-id",project,"--role-name","neondb_owner"],env=env,timeout=180); must(conn,"branch DSN")
      owner=conn.stdout.strip()
      if host_of(owner) in {prod_host,stg_host}: raise HarnessRefusal("branch endpoint collides with staging/production")
      must(psql(owner,f"create database {DB_NAME}"),"fresh disposable database")
      parsed=owner.split("?",1); fresh=parsed[0].rsplit("/",1)[0]+"/"+DB_NAME+(("?"+parsed[1]) if len(parsed)>1 else "")
      must(run([db_tap.psql_bin(),"-v","ON_ERROR_STOP=1","-q","-d",fresh,"-f",str(REPO/"db/schema.sql")],env=env,timeout=1800),"schema load")
      must(run([sys.executable,str(REPO/"tools/migrate.py"),"--apply","--yes"],env={**env,"DATABASE_URL":fresh},timeout=1800),"migrations")
      migration_status=run([sys.executable,str(REPO/"tools/migrate.py")],env={**env,"DATABASE_URL":fresh},timeout=900)
      must(migration_status,"post-apply migration ledger readback")
      if "pending: 0" not in migration_status.stdout: raise HarnessRefusal("fresh disposable database retains pending migrations")
      password=secrets.token_urlsafe(32); must(psql(fresh,"alter role carr_jobs login password '"+password.replace("'","''")+"'"),"disposable carr_jobs login")
      jobs=jobs_dsn(fresh,password)
      with FakeProvider() as fake:
        routes={"CARR_AI_ROUTE_PRIMARY_URL":fake.url,"CARR_AI_ROUTE_SECONDARY_URL":fake.url}
        must(run([sys.executable,str(REPO/"tools/control-plane.py"),"sync"],env={**env,**routes,"DATABASE_URL":fresh},timeout=900),"registry sync")
        fixture_releases(fresh,token)
        before=(release_count(fresh), release_fingerprint(fresh))
        scheduled=run([sys.executable,str(REPO/"tools/control-plane.py"),"schedule","--mode","shadow","--at",INSTANT],env={**env,**routes,"CARR_DB_JOBS_URL":jobs},timeout=900); must(scheduled,"shadow schedule")
        rows=json.loads(scheduled.stdout); target=[x for x in rows if x.get("workflow")==WORKFLOW]
        if len(rows)!=1 or len(target)!=1: raise HarnessRefusal("isolated instant did not schedule only cc-update-audit")
        tick=run([sys.executable,str(REPO/"tools/control-plane.py"),"tick","--mode","shadow","--max-jobs","1"],env={**env,**routes,"CARR_DB_JOBS_URL":jobs},timeout=1200); must(tick,"mode-filtered shadow tick")
        evidence=assert_evidence(fresh,target[0]["job_id"],before)
        if len(fake.requests)!=1: raise HarnessRefusal("fake provider call count was not exactly one")
        print(json.dumps(redacted({"ok":True,"kind":"disposable_cognition_shadow_integration","not_real_provider_canary":True,"not_workflow_acceptance":True,"provider":"loopback_fake","request_count":len(fake.requests),"evidence":evidence}),sort_keys=True))
      succeeded=True
    finally:
      def list_exact() -> Any:
        listed=run([db_tap.NEONCTL,"branches","list","--project-id",project,"--output","json"],env=env,timeout=180)
        if listed.returncode: raise HarnessRefusal("ephemeral branch cleanup listing failed")
        try: return json.loads(listed.stdout)
        except json.JSONDecodeError as exc: raise HarnessRefusal("ephemeral branch cleanup listing malformed") from exc
      def delete_exact(target: str) -> bool:
        return run([db_tap.NEONCTL,"branches","delete",target,"--project-id",project],env=env,timeout=180).returncode == 0
      cleanup_ephemeral_branch(created_ok=created_ok,branch_id=branch_id,branch_name=branch_name,
                               list_branches=list_exact,delete_branch=delete_exact)
    if not succeeded: raise HarnessRefusal("shadow harness did not reach success")
    return 0

if __name__=="__main__":
    try: raise SystemExit(main())
    except HarnessRefusal as exc: print(f"cc-update-audit-shadow-harness: REFUSED — {exc}",file=sys.stderr); raise SystemExit(2)
