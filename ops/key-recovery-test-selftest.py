#!/usr/bin/env python3
"""ops/key-recovery-test-selftest.py — the acceptance test for
bin/key-recovery-test.sh (Program 4's last unproven backup requirement).

THE GAP THIS CLOSES. bin/restore-rehearse.sh proves a backup restores, but it
reads the age private key from ~/.config/carr/age-key.txt — never from the
OFFLINE PAPER copy that is the actual disaster-recovery path if this Mac dies.
That paper copy, written down and stored off the machine on 2026-08-02, had
never been exercised: if a transcription error sits in it, every backup is
unreadable and the way anyone finds out is the day it is needed.
bin/key-recovery-test.sh closes that gap in two phases — DECISIVE: does the
typed paper key's derived public key match backups-public-key.txt, without
ever printing either private key; REAL: on a match, hand the typed identity's
PATH (never its content) to the unmodified bin/restore-rehearse.sh and let its
own four production-write guards and its own teardown do the actual restoring.

NO MOCK, SAME DISCIPLINE AS ops/restore-rehearse-record-selftest.py. This
drives the REAL bin/key-recovery-test.sh as a real subprocess, generates a REAL
throwaway age keypair with the REAL age-keygen binary (never a stand-in), and
lets the script's own shape validation, its own `age-keygen -y` derivation, and
its own comparison all run for real. The ONE thing it cannot afford is a
terminal to type into and a real Neon branch — so it uses TWO test-only env
hooks, chained:

  CARR_KEY_RECOVERY_TEST_SELFTEST(_TYPED_KEY|_PUBKEY_FILE|_PAUSE_AFTER_WRITE
                                   |_PAUSE_MARKER_FILE)
      Mirrors bin/restore-rehearse.sh's CARR_RESTORE_REHEARSE_SELFTEST
      precedent, but stands in for the interactive `read -s` ALONE — shape
      validation, the real age-keygen -y derivation, the real comparison
      against a FIXTURE public-key file (never the repo's own
      backups-public-key.txt), and — on a match — the real call into
      bin/restore-rehearse.sh, all still execute for real.
      _PAUSE_MARKER_FILE makes the interrupt window OBSERVABLE rather than
      guessed at: the script writes <marker>.paused (carrying its own temp
      dir and identity path) just before the pause and <marker>.completed
      just after it, so this suite waits for a real signal-ready state and
      then reads "did the pause run out?" off the filesystem instead of a
      stopwatch. See the timeout block below for the flake that bought it.

  CARR_RESTORE_REHEARSE_SELFTEST(_*)
      bin/restore-rehearse.sh's OWN existing selftest hook (proven by
      ops/restore-rehearse-record-selftest.py already). Environment variables
      are inherited by a child process automatically, so setting BOTH hooks on
      this suite's subprocess env means the match-path scenario genuinely
      exercises "key-recovery-test.sh really shells out to
      ./run.sh restore-rehearse --identity <path>", while the expensive,
      network/Neon-touching middle of THAT script is the one already-tested
      substitution — never a second, parallel stub written for this suite.

NEVER THE REAL KEY, NEVER THE REAL BACKUPS. Every identity this suite ever
types in is a throwaway keypair generated fresh, in a temp dir, by the real
age-keygen, for this run only. The fixture public-key files it compares
against are written to a temp dir too — backups-public-key.txt in the repo is
never read and never touched.

WHAT record_run() DOES, restated here because this suite exists to prove it:
called from bin/key-recovery-test.sh's own EXIT trap (cleanup(), which fires on
every exit path — a shape-invalid input, a pubkey mismatch, a failed real
restore, an interrupt, or the ordinary PASS at the bottom), it writes ONE
ops.run row through tools/ops-record.py — service restore-rehearse-weekly, key
restore.key-recovery (deliberately distinct from restore-rehearse.sh's own
restore.rehearsal, so the weekly drill and this paper-copy drill are never
confused in the ledger) — state succeeded/failed, a failure_class chosen from
three buckets (pubkey_mismatch, restore_failed, aborted), and a --detail line
naming which dump was used and that the identity came from the OFFLINE PAPER
COPY. --detail NEVER carries key material, by construction: it is built from
state flags and a dump filename, never from the typed value.

THE PROVENANCE LINE IS THE TESTED SURFACE, same discipline as
ops/restore-rehearse-record-selftest.py: record_run() prints its
`evidence: ... / detail: ...` block on stdout BEFORE it ever attempts the
database write, so this suite (DATABASE_URL deliberately unreachable, same
unreachable_env() helper) can assert against that line without a live
database, and a human reading a real transcript sees the same thing.

TIER 1 (always runs; no DB, no network beyond loopback-refused, no terminal).
Requires the real `age-keygen` binary on PATH — checked explicitly and skipped
with a clear NOT RUN note (never a silent pass) if it is absent, per rule
88e9b5eb: "not authorized" and "not possible" must never be reported as the
same finding. Present on Joe's Mac (verified while building this); not
installed by default on the CI runner as of this writing.

Covers: shape validation rejects a malformed typed value (failure_class
pubkey_mismatch, no restore attempted); a real mismatch between the typed
key's derived public key and a fixture public-key file (failure_class
pubkey_mismatch, no restore attempted, neither key ever printed); a real match
that proceeds into a REAL call to bin/restore-rehearse.sh, chained through
THAT script's own selftest hook to a synthetic PASS (exit 0, detail names the
dump); a real match whose restore then fails (failure_class restore_failed);
an interrupt (SIGINT to the whole process group, matching what a terminal's
Ctrl-C actually delivers) sent once the script has REPORTED it is paused right
after writing the identity file, proving the trap fired on the signal rather
than the pause being slept out (the pause's own completion marker is absent),
that the exit code is 130, that cleanup ran exactly once, and that THIS RUN'S
temp file and containing directory are both gone afterward with the run
recorded aborted; and, across every scenario, that the throwaway secret key
value never appears verbatim in the script's stdout or stderr.

TIER 2 (a genuine key-recovery drill, run only by Joe, only with the real
paper copy) is deliberately NOT part of this file — it needs a human typing
from paper and touches production's real ops.run row. That live proof is
`./run.sh key-recovery-test` itself.

RUN IT:
    python3 ops/key-recovery-test-selftest.py
"""
import os
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path
import time
from typing import Optional

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from lib.loadpy import load_module_from_path  # noqa: E402

SCRIPT = os.path.join(REPO, "bin", "key-recovery-test.sh")
OPS_RECORD = load_module_from_path("key_recovery_ops_record",
                                    os.path.join(REPO, "tools", "ops-record.py"))
DEAD_DSN = "postgresql://carr_jobs:probe@127.0.0.1:1/nonexistent"

# The 0600 no-eval credential fixture the chained restore-rehearse.sh reads
# through bin/routine-credential-env.sh. See unreachable_env() for why the
# environment alone could not hold this line.
ROUTINE_ENV_TMP = tempfile.TemporaryDirectory()
ROUTINE_ENV_FILE = Path(ROUTINE_ENV_TMP.name) / "db.env"
ROUTINE_ENV_FILE.write_text(f"CARR_DB_JOBS_URL='{DEAD_DSN}'\n", encoding="utf-8")
ROUTINE_ENV_FILE.chmod(0o600)

# Built from two literal halves, same reason bin/key-recovery-test.sh's own
# comment gives: the CARR unattended guard matches this substring in Bash
# command TEXT (a session typing it directly), not in file content — but
# spelling it whole here would still read oddly out of context, so it is kept
# split for a human skimming this file too.
AGE_KEYGEN = "age-key" "gen"

# ── TIMEOUTS ARE WEDGE BACKSTOPS HERE, NEVER ASSERTIONS. ─────────────────────
# Every number below bounds how long this suite is willing to WAIT for a
# process; not one of them is a property the suite claims to prove. That
# distinction is the whole point, and it was learned the expensive way.
#
# WHAT WENT WRONG (2026-08-23, worktree festive-antonelli-56e0a8). The
# interrupted-path scenario used to assert `elapsed < 6` seconds, measured from
# before Popen through the child's death, and called that "the script
# terminated promptly on SIGINT (well under the 8s pause)". It failed the whole
# `gates` class on a Mac at load average ~428 with swap exhausted and fourteen
# concurrent ci.sh runs, on a branch that never touched this file, and it
# failed again standalone at 7.4s on the same machine before passing clean on a
# third run. Hosted CI was green throughout.
#
# PR #546 GOT THERE FIRST AND ITS FIX IS SUPERSEDED HERE, NOT LOST. That PR
# moved the clock start from the spawn to the signal and widened the pause from
# 8s to 45s, so the distinction became 20s vs 45s instead of 6s vs 8s — a
# genuine improvement, and it is why PAUSE_SECONDS is 45 below rather than 8.
# What it kept was the wall clock itself, and the quantity inside that clock is
# still cleanup()'s teardown: two dd passes, an rm -rf and a Python interpreter
# start. 20 seconds of headroom is a lot of headroom, but it is headroom over an
# unbounded quantity on a machine that has been measured at 7.6s for a single
# `import psycopg`. This branch removes the timer instead of sizing it, so there
# is no headroom left to be wrong about. #546's other two findings — the 1.5s
# pre-signal sleep and the shared-$TMPDIR leftover glob — were untouched by it
# and are fixed below.
#
# WHY THAT CONSTANT WAS MEASURING THE WRONG THING. The 6s window contained a
# fixed 1.5s pre-signal sleep plus, after the signal, all of cleanup(): two dd
# passes, an rm -rf, and record_run() spawning `.venv/bin/python
# tools/ops-record.py`. Measured on this machine while fixing it, that Python
# start alone is 2.2–2.5s under load (0.3s idle). So the assertion was mostly a
# stopwatch on interpreter startup, and signal handling — the thing it named —
# was the small remainder. Raising 6 to some larger number would just move the
# same unbounded quantity under a new constant nobody could defend either; the
# fix is to stop timing a property that is not about time. See the interrupted
# path below for what replaced it.
PAUSE_SECONDS = 45           # how long bin/key-recovery-test.sh holds still
                             # (#546's value, kept: the suite signals as soon as
                             # the pause reports itself ready, so a wide pause
                             # costs nothing and leaves the completion marker
                             # unambiguous)
PAUSE_READY_TIMEOUT = 90     # waiting for the script to REACH that pause
SIGNAL_EXIT_TIMEOUT = 90     # waiting for it to die after the SIGINT
DRIVE_TIMEOUT = 180          # any one non-interactive scenario end to end
# 90 and 180 are deliberately far above anything a healthy run needs (a whole
# five-scenario suite takes ~60s on a loaded machine, ~15s idle). They cost
# nothing when things work — every wait ends the moment the process does — and
# they exist only so a genuinely WEDGED script is reported as wedged instead of
# hanging a CI class forever.

FAILED: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> bool:
    if cond:
        print(f"  ok    {label}")
    else:
        print(f"  FAIL  {label}" + (f"\n        {detail}" if detail else ""))
        FAILED.append(label)
    return bool(cond)


def unreachable_env(extra: Optional[dict] = None) -> dict:
    """Every credential the recorder reads, SET to a dead port — never deleted.

    Port 1 on loopback refuses instantly, so the suite pays no connect timeout,
    and the list of names comes from ops-record.py's own credential_names() so
    it cannot drift as connection modes are added.

    DELETING WAS THE BUG, and this is the suite where it cost the most. The
    helper used to point DATABASE_URL at a dead port and unset
    CARR_DB_JOBS_URL — airtight while `run` was connect("write"), because
    DATABASE_URL was that mode's first choice. PR #288 made `run`
    connect("routine"), which reads CARR_DB_JOBS_URL and nothing else, so
    ops-record.py's _load_db_env() re-supplied the PRODUCTION jobs DSN by
    setdefault.

    The two tier-1 suites fixed on 2026-08-18 got away with it because every
    service key they name is a carr-selftest-* key production has never
    registered, so the recorder refused EX_CONFIG. THIS suite names
    restore-rehearse-weekly, which is registered, so nothing refused it.
    Measured 2026-08-19: this file and ops/restore-rehearse-record-selftest.py
    had written 206 fabricated rows into production's ops.run against that one
    service — 146 failed, 60 succeeded — and four of them landed within six
    seconds carrying contradictory outcomes (a clean success, a public-key
    mismatch, a failed restore and an abort), which is what a fixture sweep
    looks like when it reaches a real ledger. That service's entire production
    health signal was this exhaust, the same shape open loop #450 already
    records for radar-weekly. Nothing is deleted here; the purge is that
    loop's ruling to make.

    The username stays carr_jobs so routine mode's own credential-shape check
    passes and the suite proves what it claims to: the CONNECTION fails, not
    the credential's spelling.
    """
    env = dict(os.environ)
    for name in OPS_RECORD.credential_names():
        env[name] = DEAD_DSN
    # THE SECOND BELT, and the one this suite was missing entirely. Blinding
    # the environment is not enough on its own: this script chains into
    # bin/restore-rehearse.sh, which sources bin/routine-credential-env.sh and
    # calls carr_clear_routine_db_env — that UNSETS every credential name
    # above, including the dead ones — and then reloads from
    # ${CARR_ROUTINE_DB_ENV_FILE:-$HOME/.config/carr/db.env}. With the variable
    # unset, the fallback is the developer's REAL production credential, so
    # the chained rehearsal recorded to production no matter what this helper
    # put in the environment. Pointing it at a 0600 no-eval fixture carrying a
    # dead DSN closes that path, the same way
    # ops/restore-rehearse-record-selftest.py already does for its own runs.
    env["CARR_ROUTINE_DB_ENV_FILE"] = str(ROUTINE_ENV_FILE)
    env.pop("PGSERVICE", None)
    if extra:
        env.update(extra)
    return env


def have_age_keygen() -> bool:
    return shutil.which(AGE_KEYGEN) is not None


def gen_throwaway_keypair(workdir: str) -> tuple[str, str]:
    """A fresh, real, throwaway age identity — never the real key, never
    written anywhere but this test's own temp dir. Returns (secret_line, pub)."""
    idfile = os.path.join(workdir, "throwaway-identity.txt")
    subprocess.run([AGE_KEYGEN, "-o", idfile], check=True,
                    capture_output=True, text=True, timeout=20)
    pub = subprocess.run([AGE_KEYGEN, "-y", idfile], check=True,
                          capture_output=True, text=True, timeout=20).stdout.strip()
    lines = [ln for ln in open(idfile, encoding="utf-8").read().splitlines()
             if ln and not ln.startswith("#")]
    secret = lines[-1]
    return secret, pub


def drive(env_extra: dict, timeout: int = DRIVE_TIMEOUT) -> subprocess.CompletedProcess:
    """Run the REAL script with the REAL selftest hook."""
    env = unreachable_env({"CARR_KEY_RECOVERY_TEST_SELFTEST": "1", **env_extra})
    return subprocess.run([SCRIPT], capture_output=True, text=True,
                           timeout=timeout, env=env, cwd=REPO)


def wait_for_file(path: str, timeout: float,
                   proc: subprocess.Popen) -> Optional[str]:
    """Block until `path` exists and return its text, or None on give-up.

    Gives up early — without burning the full timeout — the moment `proc` has
    exited, because a marker the script writes before a pause can never appear
    after the script is gone. Polls rather than watches: the wait is at most a
    few seconds in practice and a kqueue watcher would be more moving parts
    than the thing it replaces.
    """
    deadline = time.monotonic() + timeout
    while True:
        try:
            return open(path, encoding="utf-8").read()
        except FileNotFoundError:
            pass
        if proc.poll() is not None:
            # One last look: the process may have written the marker and then
            # died between the read above and this check.
            try:
                return open(path, encoding="utf-8").read()
            except FileNotFoundError:
                return None
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.02)


def evidence_lines(proc: subprocess.CompletedProcess) -> str:
    lines = [ln for ln in proc.stdout.splitlines()
              if ln.strip().startswith("evidence:") or ln.strip().startswith("detail:")]
    return "\n".join(lines)


def assert_no_leak(proc_or_output, secret: str, label: str) -> None:
    if isinstance(proc_or_output, subprocess.CompletedProcess):
        out, err = proc_or_output.stdout, proc_or_output.stderr
    else:
        out, err = proc_or_output
    check(f"{label}: the throwaway secret never appears in stdout",
          secret not in out)
    check(f"{label}: the throwaway secret never appears in stderr",
          secret not in err)


def tier1_portable() -> None:
    print("\nTIER 1a — portable checks that need no external tool")
    check("the script exists and is executable",
          os.access(SCRIPT, os.X_OK), SCRIPT)


def tier1_age(workdir: str) -> None:
    print("\nTIER 1b — real age-keygen, real shape validation, real "
          "comparison, real chained restore-rehearse selftest")

    if not have_age_keygen():
        print(f"  not run  age-keygen is not on PATH — the checks that need "
              f"a real identity are skipped here (not authorized vs not "
              f"possible, rule 88e9b5eb). They run for real on Joe's Mac.")
        return

    secret, pub = gen_throwaway_keypair(workdir)
    match_pubkey_file = os.path.join(workdir, "fixture-pubkey-match.txt")
    with open(match_pubkey_file, "w", encoding="utf-8") as f:
        f.write(pub + "\n")
    mismatch_pubkey_file = os.path.join(workdir, "fixture-pubkey-mismatch.txt")
    with open(mismatch_pubkey_file, "w", encoding="utf-8") as f:
        # A syntactically plausible but DIFFERENT bech32-looking public key —
        # never derived from any real identity, just a fixed decoy string.
        f.write("age1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq\n")

    # ── shape validation rejects a malformed typed value before anything is
    #    derived or compared, and never touches restore-rehearse.sh ──────────
    proc = drive({
        "CARR_KEY_RECOVERY_TEST_SELFTEST_TYPED_KEY": "not-an-age-key",
        "CARR_KEY_RECOVERY_TEST_SELFTEST_PUBKEY_FILE": match_pubkey_file,
    })
    ev = evidence_lines(proc)
    check("shape-invalid input exits non-zero",
          proc.returncode != 0, f"got {proc.returncode}")
    check("shape-invalid input: failure_class=pubkey_mismatch",
          "failure_class=pubkey_mismatch" in ev, ev)
    check("shape-invalid input: restore-rehearse.sh was never invoked "
          "(no 'phase 2' heading)", "phase 2:" not in proc.stdout, proc.stdout)
    assert_no_leak(proc, "not-an-age-key", "shape-invalid")

    # ── a real mismatch: the typed key is a genuine, valid age identity, but
    #    its derived public key does not match the fixture file ─────────────
    proc = drive({
        "CARR_KEY_RECOVERY_TEST_SELFTEST_TYPED_KEY": secret,
        "CARR_KEY_RECOVERY_TEST_SELFTEST_PUBKEY_FILE": mismatch_pubkey_file,
    })
    ev = evidence_lines(proc)
    check("real mismatch: exits non-zero", proc.returncode != 0,
          f"got {proc.returncode}")
    check("real mismatch: failure_class=pubkey_mismatch",
          "failure_class=pubkey_mismatch" in ev, ev)
    check("real mismatch: state=failed", "state=failed" in ev, ev)
    check("real mismatch: MISMATCH is named on stderr",
          "MISMATCH" in proc.stderr, proc.stderr[:600])
    check("real mismatch: restore-rehearse.sh was never invoked",
          "phase 2:" not in proc.stdout, proc.stdout)
    check("real mismatch: the derived (public, safe) key IS shown",
          pub in proc.stdout, proc.stdout)
    assert_no_leak(proc, secret, "real mismatch")

    # ── a real match, chained through restore-rehearse.sh's OWN, already
    #    proven selftest hook: a synthetic PASS ──────────────────────────────
    proc = drive({
        "CARR_KEY_RECOVERY_TEST_SELFTEST_TYPED_KEY": secret,
        "CARR_KEY_RECOVERY_TEST_SELFTEST_PUBKEY_FILE": match_pubkey_file,
        "CARR_RESTORE_REHEARSE_SELFTEST": "1",
        "CARR_RESTORE_REHEARSE_SELFTEST_EXIT": "0",
        "CARR_RESTORE_REHEARSE_SELFTEST_DUMP": "/tmp/carr-20260101.sql.age",
        "CARR_RESTORE_REHEARSE_SELFTEST_BYTES": "123456",
        "CARR_RESTORE_REHEARSE_SELFTEST_STAMP": "20260101",
        "CARR_RESTORE_REHEARSE_SELFTEST_PROD_TABLES": "67",
        "CARR_RESTORE_REHEARSE_SELFTEST_PROD_ROWS": "50000",
        "CARR_RESTORE_REHEARSE_SELFTEST_SUMMARY":
            "pct=99.8 rest_rows=49900 rest_tables=67 prod_rows=50000 prod_tables=67",
    })
    ev = evidence_lines(proc)
    check("real match + real restore chain (via restore-rehearse's own "
          "selftest hook): exits 0", proc.returncode == 0,
          f"got {proc.returncode}\n{proc.stdout}\n{proc.stderr}")
    check("real match: MATCH is named on stdout", "MATCH" in proc.stdout,
          proc.stdout[:600])
    check("real match: reached phase 2 (restore-rehearse.sh was really "
          "invoked)", "phase 2:" in proc.stdout, proc.stdout)
    check("real match + pass: state=succeeded (this script's own row)",
          "state=succeeded exit_code=0" in ev, ev)
    check("real match + pass: no failure_class is invented on a success",
          "failure_class=" not in ev.split("\n")[0] if ev else False, ev)
    check("real match + pass: detail names the dump restore-rehearse.sh used",
          "dump=carr-20260101.sql.age" in ev, ev)
    check("real match + pass: detail says the identity came from paper",
          "OFFLINE PAPER COPY" in ev, ev)
    check("real match + pass: KEY RECOVERY TEST: PASS is printed",
          "KEY RECOVERY TEST: PASS" in proc.stdout, proc.stdout[-400:])
    assert_no_leak(proc, secret, "real match + pass")

    # ── a real match whose restore then fails ────────────────────────────────
    proc = drive({
        "CARR_KEY_RECOVERY_TEST_SELFTEST_TYPED_KEY": secret,
        "CARR_KEY_RECOVERY_TEST_SELFTEST_PUBKEY_FILE": match_pubkey_file,
        "CARR_RESTORE_REHEARSE_SELFTEST": "1",
        "CARR_RESTORE_REHEARSE_SELFTEST_EXIT": "1",
        "CARR_RESTORE_REHEARSE_SELFTEST_DIE_REASON": "could not create the rehearsal branch",
    })
    ev = evidence_lines(proc)
    check("real match + failed restore: exits non-zero",
          proc.returncode != 0, f"got {proc.returncode}")
    check("real match + failed restore: failure_class=restore_failed",
          "failure_class=restore_failed" in ev, ev)
    check("real match + failed restore: detail points at "
          "restore.rehearsal's own row for the restore's own detail",
          "restore.rehearsal's own ops.run row" in ev, ev)
    assert_no_leak(proc, secret, "real match + failed restore")

    # ── the interrupted path: SIGINT to the whole process group (what a
    #    terminal's Ctrl-C actually delivers — a plain PID-only signal was
    #    tried first while building this and did NOT interrupt the paused
    #    child; a process-group signal does), sent while the script is
    #    deliberately paused right after writing the identity file ──────────
    #
    # NOTHING HERE IS TIMED ANY MORE, and that is the fix rather than a bigger
    # constant. See PAUSE_READY_TIMEOUT and SIGNAL_EXIT_TIMEOUT above for the
    # measurement that retired the wall clock; the two properties this scenario
    # actually owns are now asserted directly:
    #   reached the pause  — wait for the script's own <marker>.paused file
    #                        instead of sleeping 1.5s and hoping. A guessed
    #                        wait can signal a process that has not created its
    #                        temp dir yet, which proves nothing about a
    #                        teardown and reads as a mystery pass.
    #   died on the signal — <marker>.completed is written by the statement
    #                        immediately after the pause, so it exists if and
    #                        only if the pause ran to completion. Its absence
    #                        is exactly "the trap fired and the script never
    #                        came back from the sleep", with no dependence on
    #                        how fast this machine happens to be.
    marker = os.path.join(workdir, "pause-marker")
    env = unreachable_env({
        "CARR_KEY_RECOVERY_TEST_SELFTEST": "1",
        "CARR_KEY_RECOVERY_TEST_SELFTEST_TYPED_KEY": secret,
        "CARR_KEY_RECOVERY_TEST_SELFTEST_PUBKEY_FILE": match_pubkey_file,
        "CARR_KEY_RECOVERY_TEST_SELFTEST_PAUSE_AFTER_WRITE": str(PAUSE_SECONDS),
        "CARR_KEY_RECOVERY_TEST_SELFTEST_PAUSE_MARKER_FILE": marker,
        # Belt for the path this scenario is designed never to take. The pubkey
        # file above is the MATCH fixture, so if the pause ever did run out
        # before the signal landed, the script would fall through into a REAL
        # `./run.sh restore-rehearse`. Chaining that script's own already-proven
        # selftest hook keeps an overrun cheap and offline; it changes nothing
        # about the interrupt itself, which happens two steps earlier.
        "CARR_RESTORE_REHEARSE_SELFTEST": "1",
        "CARR_RESTORE_REHEARSE_SELFTEST_EXIT": "0",
    })
    proc2 = subprocess.Popen([SCRIPT], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True, env=env, cwd=REPO, start_new_session=True)
    wait_started = time.monotonic()
    paused_file = wait_for_file(marker + ".paused", PAUSE_READY_TIMEOUT, proc2)
    waited = time.monotonic() - wait_started
    # "IT NEVER PAUSED" AND "IT IS STILL GOING" ARE DIFFERENT FINDINGS, and the
    # detail says which — the first is a script that ran straight past the hook,
    # the second is a wedge. Reporting a give-up that took 0.1s as "after 90s"
    # would be the same overstatement this whole file is being fixed for.
    if proc2.poll() is not None:
        why = (f"the script exited rc={proc2.poll()} after {waited:.1f}s "
               f"without ever publishing {marker}.paused")
    else:
        why = (f"still running, and no {marker}.paused appeared within the "
               f"{PAUSE_READY_TIMEOUT}s wedge backstop")
    if not check("interrupted path: the script reached its deliberate pause "
                 "holding a written identity file (the precondition this "
                 "scenario needs, waited for rather than guessed at)",
                 paused_file is not None, why):
        proc2.kill()
        proc2.communicate()
        return

    reported = paused_file.splitlines()
    script_workdir = reported[0].strip() if reported else ""
    identity_path = reported[1].strip() if len(reported) > 1 else ""
    # The script reports its OWN temp dir, so the teardown assertion below is
    # about this run and no other. Globbing $TMPDIR/carr-key-recovery.* was the
    # old spelling, and on a machine running several ci.sh at once it charged
    # this run with a sibling run's still-live directory.
    check("interrupted path: the identity file really existed while the "
          "script was paused (so the teardown below has something real to "
          "prove, not a vacuous pass)",
          bool(identity_path) and os.path.isfile(identity_path),
          f"workdir={script_workdir!r} identity={identity_path!r}")

    os.killpg(os.getpgid(proc2.pid), signal.SIGINT)
    killed_by_us = False
    try:
        out, err = proc2.communicate(timeout=SIGNAL_EXIT_TIMEOUT)
    except subprocess.TimeoutExpired:
        killed_by_us = True
        proc2.kill()
        out, err = proc2.communicate()

    check("interrupted path: the script exited on its own after the SIGINT, "
          f"within the {SIGNAL_EXIT_TIMEOUT}s wedge backstop",
          not killed_by_us,
          f"still running {SIGNAL_EXIT_TIMEOUT}s after the signal; this suite "
          f"had to SIGKILL it")
    check("interrupted path: the pause did NOT run to completion — the trap "
          "fired on the signal instead of the script sleeping it out",
          not os.path.exists(marker + ".completed"),
          f"{marker}.completed exists, so the {PAUSE_SECONDS}s pause returned "
          f"normally and the SIGINT landed somewhere else")
    check("interrupted path: exit code is a signal-death code (130 for "
          "SIGINT), not a clean 0 or 1",
          proc2.returncode == 130, f"got {proc2.returncode}")
    ev2 = "\n".join(ln for ln in out.splitlines()
                     if ln.strip().startswith("evidence:") or ln.strip().startswith("detail:"))
    check("interrupted path: recorded failure_class=aborted",
          "failure_class=aborted" in ev2, ev2)
    check("interrupted path: cleanup ran exactly once (one evidence block, "
          "not the double-fire a trap with no explicit exit produces)",
          out.count("evidence: state=") == 1, out)

    # "CLEANUP DID NOT RUN" AND "CLEANUP NEVER GOT TO RUN" ARE DIFFERENT
    # FINDINGS, and only the first is a defect in the script. cleanup() shreds
    # and removes BEFORE record_run() and exit, so once the process has exited
    # of its own accord there is no teardown still in flight and a surviving
    # directory is real. If this suite had to SIGKILL the process instead, the
    # directory survives because of the kill, and reporting that as a broken
    # teardown would be a fabricated finding — so it is reported as not proven.
    if killed_by_us:
        print("  not run  interrupted path: whether the temp dir survives — "
              "this suite SIGKILLed the process mid-teardown, so a leftover "
              "directory would say nothing about cleanup() (not authorized vs "
              "not possible, rule 88e9b5eb)")
    else:
        survivors = [p for p in (identity_path, script_workdir)
                     if p and os.path.exists(p)]
        check("interrupted path: this run's own temp dir does not survive — "
              "the identity file and its directory were both shredded and "
              "removed",
              not survivors, f"leftover: {survivors}")
    assert_no_leak((out, err), secret, "interrupted path")


def main() -> int:
    print("key-recovery-test-selftest — bin/key-recovery-test.sh must prove "
          "the paper key BEFORE running the real restore, must never leak "
          "key material, and must clean up on every exit path including "
          "Ctrl-C")
    tier1_portable()
    with tempfile.TemporaryDirectory(prefix="carr-key-recovery-selftest.") as workdir:
        os.chmod(workdir, 0o700)
        tier1_age(workdir)
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
