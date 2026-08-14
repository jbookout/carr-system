#!/usr/bin/env python3
"""ops/run-scheduled-selftest.py — the acceptance test for bin/run-scheduled.sh,
the generic recording wrapper that closes Program 4's first gap.

WHAT THE GAP WAS, measured 2026-08-14. `tools/ops-record.py health` showed 21 of
its 25 registered service/environment rows at "last seen never" — only
nightly-record-layer and social-batch-weekly healthy. Not because those jobs
were down; they run on schedule. Only bin/nightly.sh and bin/smoke-and-record.sh
ever called `ops-record run`, so the seven other launchd jobs (rules-refresh,
partner-ping, capture-poll, local-briefs, notes-sweep, recordings-purge,
cc-version-sentinel) invoked their script directly and a failure in any of them
was durable NOWHERE. Program 4's gate reads "forced job failure is durable and
actionable"; for those seven it was neither. This wrapper closes those seven;
the rest of the 21 are the Claude Code scheduled tasks (a Stop hook's job, not a
wrapper's) and two staging rows nothing observes yet.

WHY A WRAPPER RATHER THAN SEVEN EDITS. The seven scripts are zsh, sh and python,
written by different hands over months. Teaching each one to record would create
seven copies of the same recording decision and guarantee they drift — rule
a8c55a47 in the other direction. One wrapper is one implementation, and the
launchd plist is the only thing that changes per job.

THE ONE DESIGN RULE THIS FILE EXISTS TO HOLD: THE WRAPPER IS TRANSPARENT.
It never changes what the job does, what the job prints, or what the job's exit
code says. A wrapper that can turn a passing job red is worse than no recording
at all, because it puts the observer in the failure path of the thing observed.
Every tier-1 check below is some restatement of that sentence.

TESTED THROUGH A LINE PRODUCTION ALSO WRITES. The wrapper appends one provenance
line to out/run-scheduled.log on EVERY run, carrying the state it derived and
the exact recorder argv it built. This file asserts against that line. It does
NOT introduce a test-only seam — an injectable recorder path, a dry-run flag, a
mock — and that is deliberate: on 2026-08-14 the settings-change gate shipped
with two defects, and both were "a test that exercised a path production never
takes" (team loop T75). The line the test reads is the line the job writes at
02:05, so a test that passes proves something about the real thing.

TWO TIERS, matching ops/scheduled-run-record-selftest.py:

  TIER 1 (always runs; no DB, no credential). Drives the REAL wrapper as a
  subprocess against real child commands, with a DELIBERATELY UNREACHABLE
  DATABASE_URL. That is not a degraded mode being tolerated — it is the exact
  production path bin/nightly.sh already handles as EX_CONFIG, so proving the
  wrapper stays transparent while the recorder refuses is proving the case that
  actually happens on a Mac whose credential has not loaded yet.

  TIER 2 (only when DATABASE_URL is already set — i.e. run through
  `tools/db-tap.py --project staging run ops/run-scheduled-selftest.py`, never
  bare). Registers a throwaway probe service, runs a deliberately failing job
  through the real wrapper, reads the row back out of ops.run, and DELETES it
  before exiting. Never runs against production: it activates only when
  DATABASE_URL is already present, which is what db-tap --project staging sets.

RUN IT:
    python3 ops/run-scheduled-selftest.py
    tools/db-tap.py --project staging run ops/run-scheduled-selftest.py
"""
import os
import re
import subprocess
import sys
import tempfile
from typing import Any, Optional

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
WRAPPER = os.path.join(REPO, "bin", "run-scheduled.sh")
LOG = os.path.join(REPO, "out", "run-scheduled.log")

FAILED: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> bool:
    if cond:
        print(f"  ok    {label}")
    else:
        print(f"  FAIL  {label}" + (f"\n        {detail}" if detail else ""))
        FAILED.append(label)
    return bool(cond)


# ── tier 1 ───────────────────────────────────────────────────────────────────

def unreachable_env() -> dict[str, str]:
    """The environment a Mac has before its DB credential loads.

    Port 1 on loopback refuses instantly rather than hanging, so a suite that
    runs on every push does not pay a connect timeout nine times over.
    """
    env = dict(os.environ)
    env["DATABASE_URL"] = "postgresql://nobody@127.0.0.1:1/nothing"
    for leak in ("CARR_DB_JOBS_URL", "CARR_DB_URL", "PGSERVICE"):
        env.pop(leak, None)
    return env


def tail_line(run_key: str) -> str:
    """The wrapper's own provenance line for this run key — last one wins."""
    try:
        with open(LOG) as fh:
            hits = [ln.rstrip("\n") for ln in fh if f"key={run_key} " in ln]
    except FileNotFoundError:
        return ""
    return hits[-1] if hits else ""


def field(line: str, name: str) -> str:
    m = re.search(rf"\b{re.escape(name)}=(\S*)", line)
    return m.group(1) if m else ""


def drive(run_key: str, script: str, env: Optional[dict[str, str]] = None,
          cwd: Optional[str] = None) -> tuple[Any, str]:
    """Run the real wrapper over a real child, return (proc, provenance line)."""
    proc = subprocess.run(
        [WRAPPER, "carr-selftest-probe", run_key, "/bin/sh", "-c", script],
        capture_output=True, text=True, timeout=120,
        env=env if env is not None else unreachable_env(),
        cwd=cwd or REPO,
    )
    return proc, tail_line(run_key)


def tier1() -> None:
    print("\nTIER 1 — the wrapper is transparent (no DB, unreachable credential)")

    check("the wrapper exists and is executable",
          os.access(WRAPPER, os.X_OK), WRAPPER)
    if not os.access(WRAPPER, os.X_OK):
        return

    # ── exit-code pass-through, the whole point ──────────────────────────────
    # A job's exit code is what launchd, and every human reading a log, treats
    # as the truth about that job. The wrapper must never author it.
    for rc, state in ((0, "succeeded"), (3, "failed"), (1, "failed")):
        proc, line = drive(f"selftest.exit{rc}", f"exit {rc}")
        check(f"child exit {rc} passes through untouched",
              proc.returncode == rc, f"got {proc.returncode}")
        check(f"child exit {rc} is recorded as state={state}",
              field(line, "state") == state, line or "(no provenance line)")

    # ── EX_CONFIG is a skip, not a failed night ──────────────────────────────
    # Same convention bin/nightly.sh and bin/smoke-and-record.sh already hold:
    # a step that ran, found a credential absent, wrote nothing and said so did
    # not FAIL. Alarming nightly until someone pastes a token is how the smoke
    # suite was lost the first time (see bin/smoke-and-record.sh's re-arm note).
    proc, line = drive("selftest.exit78", "exit 78")
    check("exit 78 (EX_CONFIG) passes through as 78",
          proc.returncode == 78, f"got {proc.returncode}")
    check("exit 78 is recorded as state=skipped, NOT failed",
          field(line, "state") == "skipped", line)

    # ── a killed job is not a failed job ─────────────────────────────────────
    # The Mac sleeping mid-job is the documented 2026-08-14 failure. Recording
    # "failed" for a job the machine killed sends whoever reads it hunting for
    # a bug in the job.
    proc, line = drive("selftest.sigkill", "kill -9 $$")
    check("a SIGKILLed child reports 137 to the caller",
          proc.returncode == 137, f"got {proc.returncode}")
    check("a SIGKILLed child is recorded as timed_out, not failed",
          field(line, "state") == "timed_out", line)

    proc, line = drive("selftest.sigterm", "kill -15 $$")
    check("a SIGTERMed child is recorded as cancelled, not failed",
          field(line, "state") == "cancelled", line)

    # ── the recorder is never in the job's failure path ──────────────────────
    # Every tier-1 run above used an unreachable database. If the wrapper let
    # that surface, every check above would already have failed — assert it
    # explicitly anyway, because this is the property, not a side effect.
    proc, line = drive("selftest.recorder-down", "exit 0")
    check("the recorder genuinely could not reach the database",
          field(line, "recorder_exit") not in ("0", ""), line)
    check("...and the job still reports success",
          proc.returncode == 0, f"got {proc.returncode}")
    check("...and the failure is logged rather than hidden",
          "recorder_exit=" in line, line)

    # ── output belongs to the job ────────────────────────────────────────────
    # launchd captures the child's stdout/stderr via StandardOutPath. A wrapper
    # that swallows or reorders it silently blinds every existing job log.
    proc, _ = drive("selftest.stdout", "echo CHILD_OUT; echo CHILD_ERR >&2")
    check("child stdout reaches the caller untouched",
          "CHILD_OUT" in proc.stdout, repr(proc.stdout[:200]))
    check("child stderr reaches the caller untouched",
          "CHILD_ERR" in proc.stderr, repr(proc.stderr[:200]))
    check("the recorder's own chatter stays OUT of the job's stdout",
          "ops-record" not in proc.stdout, repr(proc.stdout[:200]))

    # ── the child's world is the child's ─────────────────────────────────────
    # None of the seven plists sets WorkingDirectory, so each script currently
    # starts in launchd's cwd. A wrapper that cd's to REPO first would silently
    # change every relative path those scripts resolve.
    with tempfile.TemporaryDirectory() as tmp:
        real_tmp = os.path.realpath(tmp)
        proc, _ = drive("selftest.cwd", "pwd", cwd=real_tmp)
        check("the child inherits the caller's cwd, not the repo root",
              os.path.realpath(proc.stdout.strip() or "/") == real_tmp,
              f"child saw {proc.stdout.strip()!r}, expected {real_tmp!r}")

    # ── arguments survive ────────────────────────────────────────────────────
    # notes-sweep is invoked as `notes-sweep-post.sh --scheduled`, and that flag
    # is what gates it to weekday business hours. A wrapper that drops or
    # re-splits trailing arguments would make it run at 3am.
    proc = subprocess.run(
        [WRAPPER, "carr-selftest-probe", "selftest.args",
         "/bin/sh", "-c", 'printf "%s|" "$@"', "_", "one", "two three", "--flag"],
        capture_output=True, text=True, env=unreachable_env(), cwd=REPO)
    check("child arguments pass through intact, including one carrying a space",
          proc.stdout.strip() == "one|two three|--flag|", repr(proc.stdout))

    # ── the run key is not rewritten ─────────────────────────────────────────
    # bin/nightly.sh DERIVES its key from a label and documents the tradeoff.
    # This wrapper is handed the key explicitly, so it must record it verbatim:
    # a mangled key silently starts a new history and orphans the old one.
    _, line = drive("selftest.key.with.dots-and-dashes", "exit 0")
    check("the run key is recorded verbatim, dots and dashes included",
          field(line, "key") == "selftest.key.with.dots-and-dashes", line)

    # ── the recorder argv is the contract ────────────────────────────────────
    # This is what makes the provenance line worth asserting on: it carries the
    # exact call the wrapper made, so a change in flag names cannot pass this
    # suite while quietly writing nothing.
    _, line = drive("selftest.argv", "exit 5")
    argv = line.split("argv=", 1)[1] if "argv=" in line else ""
    for flag in ("--service carr-selftest-probe", "--key selftest.argv",
                 "--state failed", "--exit-code 5",
                 "--source-kind wrapper", "--source-ref bin/run-scheduled.sh"):
        check(f"recorder argv carries {flag!r}", flag in argv, argv[:400])
    check("a failed run carries a failure-class "
          "(ops.run's own constraint refuses one without it)",
          "--failure-class" in argv, argv[:400])
    check("a succeeded run does NOT invent a failure-class",
          "--failure-class" not in (
              tail_line("selftest.exit0").split("argv=", 1) + [""])[1],
          tail_line("selftest.exit0")[:400])

    # ── it joins a journey rather than starting a lone one ───────────────────
    # bin/smoke-and-record.sh inherits CARR_CORRELATION_ID for exactly this
    # reason: a job a nightly chain launched should trace with that chain.
    env = unreachable_env()
    env["CARR_CORRELATION_ID"] = "11111111-2222-3333-4444-555555555555"
    _, line = drive("selftest.corr", "exit 0", env=env)
    check("an inherited CARR_CORRELATION_ID is passed to the recorder",
          "--correlation 11111111-2222-3333-4444-555555555555"
          in line.split("argv=", 1)[-1], line[:400])

    env2 = unreachable_env()
    env2.pop("CARR_CORRELATION_ID", None)
    _, line = drive("selftest.nocorr", "exit 0", env=env2)
    check("...and no correlation flag is invented when none was inherited",
          "--correlation" not in line.split("argv=", 1)[-1], line[:400])

    # ── misuse is loud and harmless ──────────────────────────────────────────
    # A plist edited wrong must fail visibly at install time, not run a job
    # under the wrong key for a month.
    for bad, why in (([], "no arguments"),
                     (["only-a-service"], "a service but no run key"),
                     (["svc", "key"], "a service and key but no command")):
        proc = subprocess.run([WRAPPER] + bad, capture_output=True,
                              text=True, env=unreachable_env(), cwd=REPO)
        check(f"usage error on {why}: exits 64 (EX_USAGE)",
              proc.returncode == 64, f"got {proc.returncode}")
        check(f"usage error on {why}: says so on stderr",
              "usage" in proc.stderr.lower(), repr(proc.stderr[:200]))

    # ── a missing command is the job's failure, reported as one ──────────────
    proc = subprocess.run(
        [WRAPPER, "carr-selftest-probe", "selftest.enoent",
         "/nonexistent/definitely-not-here"],
        capture_output=True, text=True, env=unreachable_env(), cwd=REPO)
    check("an unrunnable command exits 127, the shell's own convention",
          proc.returncode == 127, f"got {proc.returncode}")
    check("...and is still recorded rather than vanishing",
          field(tail_line("selftest.enoent"), "state") == "failed",
          tail_line("selftest.enoent"))


# ── tier 2 ───────────────────────────────────────────────────────────────────

def tier2() -> None:
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        print("\nTIER 2 — skipped (no DATABASE_URL; run through "
              "tools/db-tap.py --project staging to include it)")
        return

    print("\nTIER 2 — a forced failure lands a real, readable ops.run row")
    try:
        import psycopg
    except ImportError:
        print("  (psycopg unavailable — tier 2 skipped)")
        return

    probe_key = "carr-run-scheduled-probe"
    run_key = "selftest.forced-failure"
    corr = "9f9f9f9f-1111-4222-8333-444444444444"

    conn = psycopg.connect(dsn, autocommit=True)
    cur = conn.cursor()
    try:
        cur.execute(
            """insert into ops.service (key, name, purpose, family, criticality,
                                        owner_actor, runtime)
               values (%s, 'run-scheduled selftest probe',
                       'throwaway; deleted at the end of this run',
                       'Local Mac edge', 'low', 'joe', 'launchd')
               on conflict (key) do update set name = excluded.name
               returning id""", (probe_key,))
        inserted = cur.fetchone()
        assert inserted is not None, "the probe service insert returned no id"
        service_id = inserted[0]
        cur.execute(
            """insert into ops.service_environment (service_id, environment)
               values (%s, 'production') on conflict do nothing""", (service_id,))

        env = dict(os.environ)
        env["CARR_CORRELATION_ID"] = corr
        proc = subprocess.run(
            [WRAPPER, probe_key, run_key, "/bin/sh", "-c", "exit 9"],
            capture_output=True, text=True, env=env, cwd=REPO, timeout=120)
        check("the wrapper still reports the child's 9 with a live database",
              proc.returncode == 9, f"got {proc.returncode}")

        cur.execute(
            """select run_key, state, exit_code, failure_class, source_kind,
                      source_ref, started_at, ended_at
                 from ops.run where correlation_id = %s""", (corr,))
        row = cur.fetchone()
        check("exactly one row landed in ops.run", row is not None)
        if row:
            key, state, code, fclass, skind, sref, started, ended = row
            check("run_key is verbatim", key == run_key, str(key))
            check("state is failed", state == "failed", str(state))
            check("exit_code is the child's 9", code == 9, str(code))
            check("failure_class names the exit", fclass == "exit_9", str(fclass))
            check("source_kind is 'wrapper', which is what it is",
                  skind == "wrapper", str(skind))
            check("source_ref points at the wrapper",
                  sref == "bin/run-scheduled.sh", str(sref))
            check("the row carries a real elapsed window, not a single instant",
                  started is not None and ended is not None and ended >= started,
                  f"{started} -> {ended}")
    finally:
        # Autocommit, same as tools/ops-record.py's own connections: there is no
        # transaction to roll back, so deleting what we inserted IS the isolation.
        cur.execute("delete from ops.run where correlation_id = %s", (corr,))
        cur.execute("""delete from ops.service_environment where service_id =
                       (select id from ops.service where key = %s)""", (probe_key,))
        cur.execute("delete from ops.service where key = %s", (probe_key,))
        conn.close()
        print("  (tier 2 probe rows deleted)")


def main() -> int:
    print("run-scheduled-selftest — bin/run-scheduled.sh must never change what "
          "a job does, prints, or returns")
    tier1()
    tier2()
    print()
    if FAILED:
        print(f"FAILED {len(FAILED)} check(s):")
        for f in FAILED:
            print(f"  - {f}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
