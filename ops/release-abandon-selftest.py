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

These fixtures drive the REAL wrapper against a REAL throwaway Neon branch of
staging, guarded the same way ops/p1-rebuild-gate.py guards its own: never
production, never the default branch, fresh database, destroyed on every exit
path. Exit 78 when there is no Neon credential here, which bin/nightly.sh and
ops/ci.sh both read as "not configured" rather than as a failure.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ABANDON_DB = "abandon_check"

_spec = importlib.util.spec_from_file_location("db_tap", REPO / "tools" / "db-tap.py")
if _spec is None or _spec.loader is None:
    sys.exit("release-abandon-selftest: could not load tools/db-tap.py")
db_tap = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(db_tap)

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


def host_of(cs: str) -> str:
    return cs.split("@", 1)[1].split("/", 1)[0].split("?", 1)[0] if "@" in cs else ""


def neon(env, *args):
    return subprocess.run([db_tap.NEONCTL, *args], capture_output=True,
                          text=True, timeout=300, env=env)


def psql(dsn, *args):
    return subprocess.run(["psql", dsn, "-v", "ON_ERROR_STOP=1", *args],
                          capture_output=True, text=True, timeout=1800)


def record(dsn, *args):
    return subprocess.run(
        [sys.executable, str(REPO / "tools" / "ops-record.py"), *args],
        capture_output=True, text=True, timeout=300,
        env={**os.environ, "DATABASE_URL": dsn})


def main() -> int:
    key = db_tap._neon_api_key()
    if not key and not os.environ.get("NEON_API_KEY"):
        print("release-abandon-selftest: no Neon credential here — not configured")
        return 78
    env = {**os.environ,
           "PATH": "/usr/local/opt/node@22/bin:/opt/homebrew/bin:" + os.environ.get("PATH", "")}
    if key:
        env["NEON_API_KEY"] = key

    staging = db_tap.PROJECTS["staging"]
    prod = db_tap.PROJECTS["production"]
    project_id = staging.get("id") or db_tap._project_id_by_name(staging["name"], env)
    if project_id == prod.get("id"):
        sys.exit("release-abandon-selftest: staging resolved to PRODUCTION — refusing.")

    prod_host = host_of(db_tap.dsn(project="production"))
    stg_host = host_of(db_tap.dsn(project="staging"))
    branch_id = ""
    name = f"abandon-check-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
    try:
        out = neon(env, "branches", "create", "--project-id", project_id,
                   "--name", name, "--output", "json")
        if out.returncode != 0:
            sys.exit(f"could not create the branch: {out.stderr.strip()[:200]}")
        branch_id = (json.loads(out.stdout).get("branch") or json.loads(out.stdout)).get("id", "")
        listed = neon(env, "branches", "list", "--project-id", project_id, "--output", "json")
        defaults = {b.get("id") for b in (json.loads(listed.stdout) if listed.returncode == 0 else [])
                    if b.get("default")}
        if not branch_id or branch_id in defaults:
            branch_id = ""
            sys.exit("branch create returned nothing usable, or the default branch")
        cs = neon(env, "connection-string", branch_id, "--project-id", project_id,
                  "--role-name", "neondb_owner")
        bdsn = cs.stdout.strip()
        if host_of(bdsn) in (prod_host, stg_host):
            sys.exit("the branch shares a host with a real environment — refusing")
        psql(bdsn, "-c", f"create database {ABANDON_DB}")
        head, _, q = bdsn.partition("?")
        dsn = head.rsplit("/", 1)[0] + "/" + ABANDON_DB + (f"?{q}" if q else "")

        if psql(dsn, "-f", str(REPO / "db" / "schema.sql")).returncode != 0:
            check("the schema loads so the rest can run", False)
            return 1
        mig = subprocess.run([sys.executable, str(REPO / "tools" / "migrate.py"),
                              "--apply", "--yes"], capture_output=True, text=True,
                             timeout=1800, env={**os.environ, "DATABASE_URL": dsn})
        check("0. schema and every migration apply, 0134 included",
              mig.returncode == 0,
              (mig.stderr or mig.stdout).strip().splitlines()[-1][:160] if (mig.stderr or mig.stdout) else "")
        if mig.returncode != 0:
            return 1

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

        for k in ("rel-abandon-a", "rel-abandon-b", "rel-shipped"):
            record(dsn, "release", "candidate", "--key", k, "--manifest", str(mpath),
                   "--service", "carr-mcp", "--environment", "staging",
                   "--maker", "selftest", "--maker-verification", "ref",
                   "--test-evidence", "ref", "--security-evidence", "ref")

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
               "--plan-hash", "plan:selftest", "--actor", "selftest")
        record(dsn, "deployment", "--service", "carr-mcp", "--environment", "staging",
               "--state", "complete", "--git-sha", "a" * 40, "--verb-count", "1",
               "--release-key", "rel-shipped", "--source-kind", "wrapper",
               "--source-ref", "ops/release-abandon-selftest.py",
               "--read-back-at", "now", "--verification-evidence-ref", "selftest read-back")
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

    finally:
        if branch_id:
            gone = neon(env, "branches", "delete", branch_id, "--project-id", project_id)
            if gone.returncode == 0:
                print(f"\n  ok    the ephemeral branch {branch_id} is gone")
            else:
                FAILED.append("teardown deleted the ephemeral branch")
                print(f"\n  FAIL  COULD NOT DELETE branch {branch_id} — delete it by hand")

    print(f"\nrelease-abandon-selftest: {PASSED}/{PASSED + len(FAILED)} passed")
    if FAILED:
        print("FAILURES: " + ", ".join(FAILED))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
