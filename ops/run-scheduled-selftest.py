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
import uuid
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


# ── tier 1b: --heartbeat-interval / --also-heartbeat (Program 4 follow-up) ───
# partner-ping (2 min) and capture-poll (5 min) would otherwise flood ops.run
# with ~1000 succeeded rows/day. --heartbeat-interval throttles a job's own
# succeeded row to at most one per interval, failures always record and clear
# the throttle, and --also-heartbeat records a second, independent row for
# carr-local-edge-node (PROP-010's local-Mac presence signal) riding the same
# wake. Same no-mock discipline as tier1() above: this drives the REAL
# wrapper as a subprocess and reads the REAL provenance line back — no
# injectable recorder. Every check here keys off a fresh uuid4 service or run
# key, so exact substring/line matching is correct regardless of how much
# history out/run-scheduled.log already carries (the failure mode a windowed
# line-diff hit during this package's own development).

def drive_flags(flags: list, service: str, run_key: str, script: str,
                 env: Optional[dict[str, str]] = None) -> Any:
    """Run the real wrapper with leading flags, service, run key, then an
    `/bin/sh -c <script>` child — returns the CompletedProcess."""
    return subprocess.run(
        [WRAPPER, *flags, service, run_key, "/bin/sh", "-c", script],
        capture_output=True, text=True, timeout=120,
        env=env if env is not None else unreachable_env(), cwd=REPO)


def last_line_for(run_key: str, service: str) -> str:
    """The wrapper's own provenance line naming BOTH this run key and this
    service — last one wins. Distinct from tail_line() (run key alone)
    because --also-heartbeat's line always carries the fixed run key
    launchd.heartbeat, so multiple heartbeat services in one test run need
    the service name to disambiguate which line is whose."""
    try:
        with open(LOG) as fh:
            hits = [ln.rstrip("\n") for ln in fh
                    if f"key={run_key} " in ln and f"service={service} " in ln]
    except FileNotFoundError:
        return ""
    return hits[-1] if hits else ""


def tier1_throttle() -> None:
    print("\nTIER 1b — --heartbeat-interval and --also-heartbeat throttle "
          "without ever hiding a failure")

    # ── backward compatibility: no flags behaves exactly as tier1 proved ────
    # (already covered end-to-end by tier1() above, which passes zero flags on
    # every call — restated here as the explicit contract this section adds
    # flags on TOP of, never in place of.)

    # ── a fresh interval records; an immediate repeat throttles ─────────────
    rk = "selftest.throttle." + uuid.uuid4().hex[:8]
    p1 = drive_flags(["--heartbeat-interval", "1800"],
                      "carr-selftest-probe", rk, "exit 0")
    check("first success inside a fresh interval is recorded",
          field(tail_line(rk), "record_action") == "recorded", tail_line(rk))
    check("...exit code still passes through untouched",
          p1.returncode == 0, f"got {p1.returncode}")

    p2 = drive_flags(["--heartbeat-interval", "1800"],
                      "carr-selftest-probe", rk, "exit 0")
    check("a second success inside the SAME interval is throttled",
          field(tail_line(rk), "record_action") == "throttled", tail_line(rk))
    check("...recorder_exit says so rather than a stale/misleading number",
          field(tail_line(rk), "recorder_exit") == "throttled", tail_line(rk))
    check("...argv is empty — no recorder call was made to throttle",
          field(tail_line(rk), "argv") == "", tail_line(rk))
    check("...and the child's exit code is UNCHANGED by being throttled",
          p2.returncode == 0, f"got {p2.returncode}")

    # ── a failure inside the same interval is never throttled ───────────────
    p3 = drive_flags(["--heartbeat-interval", "1800"],
                      "carr-selftest-probe", rk, "exit 9")
    check("a FAILURE inside the same interval is recorded immediately, "
          "not throttled", field(tail_line(rk), "record_action") == "recorded",
          tail_line(rk))
    check("...state is failed with the real exit code's failure class",
          field(tail_line(rk), "state") == "failed"
          and "--failure-class exit_9" in tail_line(rk), tail_line(rk))
    check("...and the failure itself is still the child's own exit code",
          p3.returncode == 9, f"got {p3.returncode}")

    # ── that failure cleared the throttle: the NEXT success posts right away ─
    p4 = drive_flags(["--heartbeat-interval", "1800"],
                      "carr-selftest-probe", rk, "exit 0")
    check("a recovery right after a failure is recorded, not throttled — "
          "the failure cleared the interval", p4.returncode == 0 and
          field(tail_line(rk), "record_action") == "recorded", tail_line(rk))

    # ── --also-heartbeat rides the same wake as an independent second row ───
    hb_service = "carr-selftest-edge-" + uuid.uuid4().hex[:8]
    rk2 = "selftest.hb." + uuid.uuid4().hex[:8]
    drive_flags(["--heartbeat-interval", "1800", "--also-heartbeat", hb_service],
                "carr-selftest-probe", rk2, "exit 0")
    hb_line = last_line_for("launchd.heartbeat", hb_service)
    check("--also-heartbeat writes its OWN provenance line, "
          "key=launchd.heartbeat", hb_line != "", hb_line)
    check("...for the named heartbeat service, not the primary job's service",
          field(hb_line, "service") == hb_service, hb_line)
    check("...state is succeeded — the heartbeat is a presence signal, "
          "not the child job's outcome", field(hb_line, "state") == "succeeded",
          hb_line)
    check("...recorder argv names the heartbeat service and its fixed key",
          f"--service {hb_service}" in hb_line and "--key launchd.heartbeat" in hb_line,
          hb_line)

    # ── the heartbeat's own throttle is independent of the primary job's ────
    rk3 = "selftest.hb2." + uuid.uuid4().hex[:8]
    drive_flags(["--heartbeat-interval", "1800", "--also-heartbeat", hb_service],
                "carr-selftest-probe", rk3, "exit 0")
    hb_line2 = last_line_for("launchd.heartbeat", hb_service)
    check("a second wake's heartbeat (same service, fresh primary run key) "
          "is STILL throttled — the interval tracks the heartbeat service, "
          "not the primary job's run key",
          field(hb_line2, "record_action") == "throttled", hb_line2)

    # ── the heartbeat never depends on the primary job's own outcome ────────
    hb_service2 = "carr-selftest-edge-" + uuid.uuid4().hex[:8]
    rk4 = "selftest.hbfail." + uuid.uuid4().hex[:8]
    proc = drive_flags(["--heartbeat-interval", "1800", "--also-heartbeat", hb_service2],
                        "carr-selftest-probe", rk4, "exit 7")
    check("the primary job's own failure still passes through untouched",
          proc.returncode == 7, f"got {proc.returncode}")
    check("the primary job's own line records the real failure",
          field(tail_line(rk4), "state") == "failed", tail_line(rk4))
    hb_line3 = last_line_for("launchd.heartbeat", hb_service2)
    check("...and the heartbeat STILL records succeeded on the same wake — "
          "the edge node's presence signal never depends on the wrapped "
          "job's own success", field(hb_line3, "state") == "succeeded", hb_line3)

    # ── zero flags is unaffected: the field exists but always says recorded ─
    rk5 = "selftest.noflags." + uuid.uuid4().hex[:8]
    drive_flags([], "carr-selftest-probe", rk5, "exit 0")
    check("with no --heartbeat-interval, record_action is always 'recorded' "
          "— the new field never changes behavior for the six jobs that "
          "never pass it", field(tail_line(rk5), "record_action") == "recorded",
          tail_line(rk5))


# ── tier 1c: bin/refresh-rules.sh's own exit code (Program 4 follow-up) ──────
# Found while building this package: the script always ended via its trailing
# `tail | mv` log-trim, so its own exit code was always whatever THAT
# returned (0) regardless of a FAIL logged above it — wrapped here, a real
# export failure would still be recorded as succeeded. Fixed by threading an
# EXIT_CODE variable to an explicit `exit $EXIT_CODE` at the bottom. Proven
# WITHOUT ever running a live refresh: CARR_REFRESH_RULES_EXPORT_CMD (a
# test-only hook the script itself defines, inert unless set) substitutes a
# stub for the real network/DB-touching export, and HOME points at an empty
# fixture directory so the script's hardcoded $HOME/carr-system never
# resolves to the real checkout.

def tier1_refresh_rules() -> None:
    print("\nTIER 1c — bin/refresh-rules.sh propagates its own internal "
          "failure honestly")
    script = os.path.join(REPO, "bin", "refresh-rules.sh")
    if not check("bin/refresh-rules.sh exists", os.path.exists(script), script):
        return

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
    proc = subprocess.run(["/bin/zsh", script], capture_output=True,
                           text=True, timeout=30, env=env)
    check("a stubbed export failure makes the SCRIPT's own exit code nonzero "
          "(the bug: it used to always be 0, from the trailing tail/mv chain)",
          proc.returncode != 0, f"rc={proc.returncode}")
    check("...and it carries the REAL failing exit code through (9), not a "
          "generic 1 — so bin/run-scheduled.sh's failure_class is accurate",
          proc.returncode == 9, f"rc={proc.returncode}")
    log_path = os.path.join(fixture_home, "carr-system", "out", "rules-refresh.log")
    log_text = open(log_path, encoding="utf-8").read() if os.path.exists(log_path) else ""
    check("the durable log still names the failure (FAIL rules refresh rc=9)",
          "FAIL rules refresh rc=9" in log_text, log_text)

    env["CARR_REFRESH_RULES_EXPORT_CMD"] = make_stub(0)
    proc = subprocess.run(["/bin/zsh", script], capture_output=True,
                           text=True, timeout=30, env=env)
    check("a stubbed export SUCCESS still exits 0 — the fix does not touch "
          "the success path", proc.returncode == 0, f"rc={proc.returncode}")


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
    tier1_throttle()
    tier1_refresh_rules()
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
