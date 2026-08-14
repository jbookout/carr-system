#!/usr/bin/env python3
"""ops/program4-launchd-obs-selftest.py — the acceptance test for Program 4's
first work package: every recurring launchd job records a durable ops.run row
through bin/with-run-record.sh, the doctrine lines (quoted verbatim):

  "Job failure becomes a durable failed run, incident, or Work Request. It
  cannot exist only in stdout or a local log."
  "Manual Run Now and scheduled execution invoke the same implementation."

Same house style as ops/scheduled-run-record-selftest.py and
ops/program3-trace-gate.py: inline assertions printed as ok/FAIL, isolated
fixtures, no durable production writes. TIER 1 (always runs, no DB, no
credential) proves the wrapper's own contract — exit-code passthrough,
correlation threading, the heartbeat throttle, and "a missing/unreachable DB
credential fails loud without touching the wrapped job's exit code" — entirely
from bin/with-run-record.sh's own out/with-run-record.log, which is
wrapper-owned truth independent of whether any database is reachable at all.

RUN IT:
    .venv/bin/python ops/program4-launchd-obs-selftest.py
"""
import os
import re
import subprocess
import sys
import tempfile
import time
import uuid

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

WRAPPER = os.path.join(REPO, "bin", "with-run-record.sh")
LOG = os.path.join(REPO, "out", "with-run-record.log")

PASSES: list[str] = []
FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSES.append(name)
        print(f"  ok    {name}")
    else:
        FAILURES.append(name)
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))


def unreachable_db_env(extra: dict | None = None) -> dict:
    """An environment where DATABASE_URL points at a port nothing listens on,
    so ops-record.py's connection attempt fails FAST and LOCALLY — proves the
    "fails loud, never blocks the wrapped job" contract without ever reaching
    a real database, staging or production. Same technique as
    ops/scheduled-run-record-selftest.py's unreachable_db_env()."""
    env = dict(os.environ)
    env["DATABASE_URL"] = "postgresql://probe:probe@127.0.0.1:1/nonexistent"
    env.pop("CARR_DB_JOBS_URL", None)
    env.pop("CARR_DB_EXPORTER_URL", None)
    if extra:
        env.update(extra)
    return env


def no_credential_env() -> dict:
    """A HOME with no ~/.config/carr/db.env and no DSN env var set anywhere —
    the true "missing credential" case (dsn() raises SystemExit), distinct
    from "credential present but the DB is unreachable" above. Python's
    Path.home() resolves via the HOME env var first, so overriding it to an
    empty temp directory is sufficient (same caveat noted in
    ops/scheduled-run-record-selftest.py: clearing HOME alone is not enough on
    every platform, but pointing it at a real, empty temp dir is)."""
    env = dict(os.environ)
    env["HOME"] = tempfile.mkdtemp(prefix="carr-selftest-home-")
    for k in ("DATABASE_URL", "CARR_DB_JOBS_URL", "CARR_DB_EXPORTER_URL"):
        env.pop(k, None)
    return env


def run_wrapper(args: list[str], env: dict) -> subprocess.CompletedProcess:
    return subprocess.run([WRAPPER, *args], capture_output=True, text=True,
                           timeout=30, env=env)


def tail_log(n: int = 40) -> str:
    if not os.path.exists(LOG):
        return ""
    with open(LOG, encoding="utf-8") as fh:
        lines = fh.readlines()
    return "".join(lines[-n:])


def clear_state(service: str) -> None:
    p = os.path.join(REPO, "out", "with-run-record-state", f"{service}.last-success")
    if os.path.exists(p):
        os.unlink(p)


def main() -> int:
    if not os.path.exists(WRAPPER):
        check("bin/with-run-record.sh exists", False, WRAPPER)
        print(f"\n{len(PASSES)} passed, {len(FAILURES)} failed")
        return 1
    os.makedirs(os.path.join(REPO, "out"), exist_ok=True)

    print("TIER 1 — the wrapper's own contract, no real database\n")

    # ── 1. succeeded, with duration, exit code passthrough ──────────────────
    print("1. exit 0 -> succeeded, duration recorded, wrapper exits 0")
    svc = "selftest-ok-" + uuid.uuid4().hex[:8]
    proc = run_wrapper([svc, "--", "/bin/sh", "-c", "exit 0"], unreachable_db_env())
    check("wrapper exits 0 (the wrapped command's own exit code)", proc.returncode == 0,
          f"rc={proc.returncode}")
    log_tail = tail_log()
    m = re.search(
        rf"ATTEMPT service={re.escape(svc)} key=launchd\.{re.escape(svc)} "
        rf"state=succeeded exit_code=0 duration_ms=(\d+) failure_class=- "
        rf"correlation=([0-9a-f-]{{36}})", log_tail)
    check("the ATTEMPT line records state=succeeded exit_code=0 with a numeric duration",
          m is not None, log_tail[-400:])
    if m:
        check("duration_ms is a real, non-negative measurement", int(m.group(1)) >= 0)

    # ── 2. failed + failure_class exit_N ─────────────────────────────────────
    print("\n2. nonzero exit -> failed, failure_class=exit_N, wrapper exits with it")
    svc2 = "selftest-fail-" + uuid.uuid4().hex[:8]
    proc = run_wrapper([svc2, "--", "/bin/sh", "-c", "exit 17"], unreachable_db_env())
    check("wrapper exits 17 (the wrapped command's own exit code)", proc.returncode == 17,
          f"rc={proc.returncode}")
    log_tail = tail_log()
    check("the ATTEMPT line records state=failed failure_class=exit_17",
          f"service={svc2} key=launchd.{svc2} state=failed exit_code=17 "
          f"duration_ms=" in log_tail and "failure_class=exit_17" in log_tail,
          log_tail[-400:])

    # ── 3. correlation id threads through ────────────────────────────────────
    print("\n3. correlation id threading")
    fixed = str(uuid.uuid4())
    env = unreachable_db_env({"CARR_CORRELATION_ID": fixed})
    proc = run_wrapper(["selftest-corr-inherit", "--", "/bin/sh", "-c",
                         "echo CHILD_SEES=$CARR_CORRELATION_ID"], env)
    check("an inherited CARR_CORRELATION_ID is passed unchanged to the wrapped command",
          f"CHILD_SEES={fixed}" in proc.stdout, proc.stdout)
    check("...and the same id appears in the ATTEMPT line (one journey, one id)",
          f"correlation={fixed}" in tail_log(), tail_log()[-400:])

    env2 = unreachable_db_env()
    env2.pop("CARR_CORRELATION_ID", None)
    proc = run_wrapper(["selftest-corr-mint", "--", "/bin/sh", "-c",
                         "echo CHILD_SEES=$CARR_CORRELATION_ID"], env2)
    minted = None
    mo = re.search(r"CHILD_SEES=([0-9a-f-]{36})", proc.stdout)
    if mo:
        minted = mo.group(1)
    check("no inherited id -> the wrapper mints a fresh uuid and exports it to the child",
          minted is not None, proc.stdout)
    if minted:
        check("...and that same minted id appears in the ATTEMPT line",
              f"correlation={minted}" in tail_log(), tail_log()[-400:])

    # ── 4. high-frequency throttle ────────────────────────────────────────────
    print("\n4. heartbeat throttle (partner-ping / capture-poll shape)")
    svc4 = "selftest-throttle-" + uuid.uuid4().hex[:8]
    clear_state(svc4)
    p1 = run_wrapper([svc4, "--heartbeat-interval", "1800", "--", "/bin/sh", "-c", "exit 0"],
                      unreachable_db_env())
    check("first success inside a fresh interval is recorded",
          f"service={svc4} " in tail_log() and "state=succeeded" in tail_log(),
          tail_log()[-400:])
    log_before = tail_log(200)
    p2 = run_wrapper([svc4, "--heartbeat-interval", "1800", "--", "/bin/sh", "-c", "exit 0"],
                      unreachable_db_env())
    log_after = tail_log(200)
    new_lines = log_after[len(log_before):] if log_after.startswith(log_before) else log_after
    check("a second success inside the SAME interval is throttled (no new ATTEMPT row)",
          f"THROTTLE service={svc4}" in new_lines and "ATTEMPT" not in new_lines,
          new_lines)
    check("both throttled fires still exit 0 — throttling never touches the wrapped exit code",
          p1.returncode == 0 and p2.returncode == 0)
    p3 = run_wrapper([svc4, "--heartbeat-interval", "1800", "--", "/bin/sh", "-c", "exit 9"],
                      unreachable_db_env())
    check("a FAILURE inside the same interval is recorded immediately regardless of throttle",
          f"service={svc4} key=launchd.{svc4} state=failed exit_code=9" in tail_log(),
          tail_log()[-400:])
    check("...and the failure itself is still returned as the wrapper's exit code",
          p3.returncode == 9)
    clear_state(svc4)

    # ── 5. missing/unreachable DB credential never touches the wrapped job ──
    print("\n5. a missing or unreachable DB credential fails loud, never fails the job")
    svc5 = "selftest-nocred-" + uuid.uuid4().hex[:8]
    proc = run_wrapper([svc5, "--", "/bin/sh", "-c", "exit 0"], no_credential_env())
    check("wrapped job still exits 0 with truly no DB credential anywhere",
          proc.returncode == 0, f"rc={proc.returncode}, stderr={proc.stderr}")
    check("...but the miss is loud on the wrapper's own stderr (reaches StandardErrorPath)",
          "could not record" in proc.stderr, proc.stderr)
    check("...and loud in the durable log too, not only stdout/a session that vanishes",
          "could not record" in tail_log() or "no credential" in tail_log(),
          tail_log()[-400:])

    svc5b = "selftest-unreach-" + uuid.uuid4().hex[:8]
    proc = run_wrapper([svc5b, "--", "/bin/sh", "-c", "exit 0"], unreachable_db_env())
    check("wrapped job still exits 0 when the DSN is present but the DB is unreachable",
          proc.returncode == 0, f"rc={proc.returncode}")
    check("...loud there too", "could not record" in proc.stderr, proc.stderr)

    # ── 6. recording is bounded and cannot hang the wrapped job ─────────────
    print("\n6. a stuck recorder cannot block the wrapped job past its own bound")
    svc6 = "selftest-timeout-" + uuid.uuid4().hex[:8]
    stub = os.path.join(tempfile.mkdtemp(prefix="carr-selftest-stub-"), "sleepy-python")
    with open(stub, "w", encoding="utf-8") as fh:
        fh.write("#!/bin/sh\nsleep 30\n")
    os.chmod(stub, 0o755)
    env6 = unreachable_db_env({
        "CARR_WITH_RUN_RECORD_PYTHON": stub,
        "CARR_WITH_RUN_RECORD_TIMEOUT": "2",
    })
    t0 = time.monotonic()
    proc = run_wrapper([svc6, "--", "/bin/sh", "-c", "exit 0"], env6)
    elapsed = time.monotonic() - t0
    check("wrapper still exits 0 (the wrapped command's own exit code) even though "
          "the recorder hung", proc.returncode == 0, f"rc={proc.returncode}")
    check(f"wrapper returns well within the recorder's stub sleep (elapsed={elapsed:.1f}s, "
          f"bounded near the 2s CARR_WITH_RUN_RECORD_TIMEOUT, not the 30s stub sleep)",
          elapsed < 10, f"elapsed={elapsed:.1f}s")
    check("the timeout is logged durably", "TIMEOUT" in tail_log(), tail_log()[-400:])

    # ── 7. bin/refresh-rules.sh's own exit code is honest ───────────────────
    # Found while building this package: the script always ended via its
    # trailing `tail | mv` log-trim, so its own exit code was whatever THAT
    # returned (0) regardless of a FAIL logged above it. Wrapped by
    # with-run-record.sh, that meant a real export failure would still be
    # recorded as succeeded. Fixed by threading an EXIT_CODE variable to an
    # explicit `exit $EXIT_CODE` at the bottom. Proven here WITHOUT ever
    # running a live refresh: CARR_REFRESH_RULES_EXPORT_CMD (a test-only hook
    # the script itself defines, inert unless set) substitutes a stub in
    # place of the real network/DB-touching export, and HOME is pointed at an
    # empty fixture directory so the script's hardcoded $HOME/carr-system
    # never resolves to the real checkout.
    print("\n7. bin/refresh-rules.sh propagates its own internal failure honestly")
    refresh_script = os.path.join(REPO, "bin", "refresh-rules.sh")
    if not os.path.exists(refresh_script):
        check("bin/refresh-rules.sh exists", False, refresh_script)
    else:
        fixture_home = tempfile.mkdtemp(prefix="carr-selftest-refresh-rules-home-")
        os.makedirs(os.path.join(fixture_home, ".config", "carr"), exist_ok=True)
        os.makedirs(os.path.join(fixture_home, "carr-system", "out"), exist_ok=True)
        with open(os.path.join(fixture_home, ".config", "carr", "db.env"), "w") as fh:
            fh.write("# selftest fixture — never read for a real credential\n")

        def make_stub(exit_code: int) -> str:
            stub_dir = tempfile.mkdtemp(prefix="carr-selftest-refresh-rules-stub-")
            stub_path = os.path.join(stub_dir, "stub-export")
            with open(stub_path, "w", encoding="utf-8") as fh:
                fh.write(f"#!/bin/sh\nexit {exit_code}\n")
            os.chmod(stub_path, 0o755)
            return stub_path

        env = dict(os.environ)
        env["HOME"] = fixture_home
        env["CARR_REFRESH_RULES_EXPORT_CMD"] = make_stub(9)
        proc = subprocess.run(["/bin/zsh", refresh_script], capture_output=True,
                               text=True, timeout=30, env=env)
        check("a stubbed export failure makes the SCRIPT's own exit code nonzero "
              "(the bug: it used to always be 0, from the trailing tail/mv chain)",
              proc.returncode != 0, f"rc={proc.returncode}")
        check("...and it carries the REAL failing exit code through (exit_9), "
              "not a generic 1 — so with-run-record.sh's failure_class is accurate",
              proc.returncode == 9, f"rc={proc.returncode}")
        log_path = os.path.join(fixture_home, "carr-system", "out", "rules-refresh.log")
        log_text = open(log_path, encoding="utf-8").read() if os.path.exists(log_path) else ""
        check("the durable log still names the failure (FAIL rules refresh rc=9)",
              "FAIL rules refresh rc=9" in log_text, log_text)

        env["CARR_REFRESH_RULES_EXPORT_CMD"] = make_stub(0)
        proc = subprocess.run(["/bin/zsh", refresh_script], capture_output=True,
                               text=True, timeout=30, env=env)
        check("a stubbed export SUCCESS still exits 0 — the fix does not touch "
              "the success path", proc.returncode == 0, f"rc={proc.returncode}")

    print(f"\nTIER 1: {len(PASSES)} passed, {len(FAILURES)} failed")

    # ── TIER 2 — only with a real (staging) DATABASE_URL already set ────────
    if not os.environ.get("DATABASE_URL"):
        print("\nTIER 2 SKIPPED — no DATABASE_URL in the environment. Run via:\n"
              "  .venv/bin/python tools/db-tap.py --project staging run "
              "ops/program4-launchd-obs-selftest.py\nto prove the real ops.run write "
              "path end to end against staging.")
    else:
        print("\nTIER 2 — real write against the database named by DATABASE_URL "
              "(intended: staging)\n")
        run_tier2()

    print(f"\n{len(PASSES)} passed, {len(FAILURES)} failed")
    if FAILURES:
        print("\nSELFTEST NOT MET:")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("SELFTEST MET: bin/with-run-record.sh records succeeded/failed runs with "
          "duration and correlation, throttles high-frequency heartbeats without ever "
          "hiding a failure, and a missing or unreachable or stuck DB path is loud in "
          "the log and never changes the wrapped job's own exit code.")
    return 0


def run_tier2() -> None:
    """Prove the row actually lands, with the fields a launchd wrapper is
    responsible for, against a real (intended: staging) database — then
    delete every row this test wrote. Same isolation technique as
    ops/scheduled-run-record-selftest.py's run_tier2(): ops-record.py's
    connections are autocommit, so explicit cleanup IS the isolation."""
    try:
        import psycopg
    except ImportError:
        check("psycopg importable for tier 2", False, "pip install 'psycopg[binary]'")
        return

    from lib.pgrow import fetch_one  # noqa: E402

    dsn = os.environ["DATABASE_URL"]
    probe_key = "p4-launchd-probe-" + uuid.uuid4().hex[:8]

    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        try:
            cur.execute(
                """insert into ops.service (key, name, family, criticality, owner_actor)
                   values (%s, 'Program 4 launchd-observability probe',
                           'Local Mac edge', 'low', 'joe')
                   returning id""",
                (probe_key,))
            service_id = fetch_one(cur, "the inserted probe service's id")[0]
            cur.execute(
                """insert into ops.service_environment (service_id, environment,
                       expected_cadence_seconds, cadence_grace_seconds)
                   values (%s, 'production', 1800, 900)""",
                (service_id,))

            corr = str(uuid.uuid4())
            env = dict(os.environ)
            env["CARR_CORRELATION_ID"] = corr
            proc = subprocess.run([WRAPPER, probe_key, "--", "/bin/sh", "-c", "exit 0"],
                                   capture_output=True, text=True, timeout=30, env=env)
            check("tier 2: the wrapper itself still exits 0 against a real database",
                  proc.returncode == 0, proc.stderr)

            cur.execute(
                """select service_id, environment, run_key, state, exit_code, kind,
                          source_ref, detail
                     from ops.run where correlation_id = %s""",
                (corr,))
            row = cur.fetchone()
            check("tier 2: exactly one row landed in ops.run", row is not None)
            if row:
                sid, env_, key, state, exit_code, kind, source_ref, detail = row
                check("tier 2: service_id matches the probe service", sid == service_id)
                check("tier 2: environment defaulted to production", env_ == "production")
                check("tier 2: run_key is launchd.<service-key>",
                      key == f"launchd.{probe_key}", key)
                check("tier 2: state is succeeded", state == "succeeded")
                check("tier 2: exit_code is 0", exit_code == 0)
                check("tier 2: kind defaulted to job", kind == "job")
                check("tier 2: source_ref names the wrapper",
                      source_ref == "bin/with-run-record.sh", source_ref)
                check("tier 2: detail carries a duration_ms figure",
                      detail is not None and "duration_ms=" in detail, detail)
        finally:
            cur.execute("delete from ops.run where service_id = "
                        "(select id from ops.service where key = %s)", (probe_key,))
            cur.execute("delete from ops.service_environment where service_id = "
                        "(select id from ops.service where key = %s)", (probe_key,))
            cur.execute("delete from ops.service where key = %s", (probe_key,))
            print("  (tier 2 probe rows deleted)")


if __name__ == "__main__":
    sys.exit(main())
