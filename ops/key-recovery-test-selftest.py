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

  CARR_KEY_RECOVERY_TEST_SELFTEST(_TYPED_KEY|_PUBKEY_FILE|_PAUSE_AFTER_WRITE)
      Mirrors bin/restore-rehearse.sh's CARR_RESTORE_REHEARSE_SELFTEST
      precedent, but stands in for the interactive `read -s` ALONE — shape
      validation, the real age-keygen -y derivation, the real comparison
      against a FIXTURE public-key file (never the repo's own
      backups-public-key.txt), and — on a match — the real call into
      bin/restore-rehearse.sh, all still execute for real.

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
Ctrl-C actually delivers) sent while the script is deliberately paused right
after writing the identity file, proving the temp file and its containing
directory are BOTH gone afterward and the run is recorded aborted; and, across
every scenario, that the throwaway secret key value never appears verbatim in
the script's stdout or stderr.

NO WALL-CLOCK CONSTANT SURVIVES IN THE INTERRUPT CHECK. It once sent the signal
a fixed 1.5s after spawn and asserted the whole thing finished in under 6s;
both numbers measured this 16GB laptop rather than the script, and the second
went red at 7.9s in the CANONICAL checkout at load average 110 and 340 while
every other interrupt check stayed green — blocking unrelated branches at the
ops/ci.sh gates class. The signal is now gated on the script's own observed
state (its identity file appearing), and promptness is judged against a control
run of the same script timed moments later under the same load, so load moves
both terms together, and the fixed-length pause a broken script would sleep out
is the only term load cannot move. Widening the constant was rejected: a bigger
constant measures the host too, just less often. See the block itself for the
full reasoning, and ops/hook-meter-selftest.py for the same correction made to
the meter's cost threshold on the same day.

TIER 2 (a genuine key-recovery drill, run only by Joe, only with the real
paper copy) is deliberately NOT part of this file — it needs a human typing
from paper and touches production's real ops.run row. That live proof is
`./run.sh key-recovery-test` itself.

RUN IT:
    python3 ops/key-recovery-test-selftest.py
"""
import glob
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


def drive(env_extra: dict, timeout: int = 60) -> subprocess.CompletedProcess:
    """Run the REAL script with the REAL selftest hook."""
    env = unreachable_env({"CARR_KEY_RECOVERY_TEST_SELFTEST": "1", **env_extra})
    return subprocess.run([SCRIPT], capture_output=True, text=True,
                           timeout=timeout, env=env, cwd=REPO)


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


def wait_until_paused(proc: subprocess.Popen, before: set, tmp_glob: str,
                      secret: str, budget_s: float = 240.0) -> Optional[str]:
    """Block until the script has WRITTEN ITS IDENTITY FILE, then return its
    temp dir — the load-proof replacement for `time.sleep(1.5)`.

    bin/key-recovery-test.sh writes the identity file on the statement right
    before its selftest pause, so this directory appearing is the script
    saying "I am at the pause now". Waiting on that instead of a fixed delay is
    what keeps the SIGINT landing INSIDE the pause on a thrashing host, where
    the interpreter may not even have started at 1.5s and the signal would
    otherwise kill zsh before its trap exists.

    The wait itself asserts nothing about how long it took — that number is the
    host's, not the script's. It only refuses to wait forever, and it gives up
    early if the child dies. Candidate dirs are matched by CONTENT against this
    run's own throwaway secret, so a concurrent CI run's temp dir under the
    shared TMPDIR can never be mistaken for this one.
    """
    want = secret.strip()
    deadline = time.monotonic() + budget_s
    while time.monotonic() < deadline:
        for d in set(glob.glob(tmp_glob)) - before:
            idfile = os.path.join(d, "identity.txt")
            try:
                if open(idfile, encoding="utf-8").read().strip() == want:
                    return d
            except OSError:
                continue
        if proc.poll() is not None:
            return None
        time.sleep(0.02)
    return None


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
    # NOTHING HERE IS A WALL-CLOCK CONSTANT, and that is a correction rather
    # than a convenience — the same one ops/hook-meter-selftest.py took on
    # 2026-08-23 ("a threshold that measured the laptop"). Two constants used
    # to sit in this block and both of them measured the host, not the script:
    #
    #   * the signal was sent a fixed 1.5s after spawn, ASSUMING the script had
    #     reached its pause by then. On a thrashing 16GB Mac the interpreter
    #     may not have started yet, and a SIGINT that lands before the trap is
    #     installed kills zsh outright — no cleanup, no evidence row, and a red
    #     suite that says "the interrupt path is broken" about a script that
    #     was never given the chance to run it.
    #   * the result was asserted as `elapsed < 6`, spawn to exit. Measured
    #     2026-08-23 in the CANONICAL checkout, which no in-flight branch had
    #     touched: 7.9s at load average 110 and again at 340 — exit code 130,
    #     cleanup fired exactly once, temp dir gone, every other check green.
    #     The script was right; the laptop was slow, and the gates class blocked
    #     unrelated branches on it. A bigger constant measures the host too,
    #     just less often, so the constant is gone instead of widened.
    #
    # WHAT REPLACES THEM. The signal is now gated on OBSERVED READINESS — the
    # suite waits for this run's own identity file to appear on disk, written
    # on the statement immediately before the pause — so the SIGINT lands
    # inside the pause at any load. And promptness is asserted against a
    # CONTROL run of the same script, on the same machine, moments later, under
    # whatever load exists: an uninterrupted mismatch run does strictly MORE
    # work than the interrupted run's remaining teardown (the same zsh start,
    # the same shred, the same ops-record write, plus a real age-keygen -y
    # derivation the interrupted run never reaches), so load moves both terms
    # together. The one term load cannot move is the `sleep` itself, and a
    # quarter of it is handed over as slack for run-to-run variance in the
    # ops-record write both runs pay.
    #
    # THE PAUSE IS LONG ON PURPOSE, and it is free. Nothing waits it out any
    # more — the signal goes the moment readiness is observed — so its length
    # costs a healthy run nothing at all, and is paid only by a script that
    # ignores the signal, which is a red suite either way. Length is what buys
    # the detection margin, and the margin needed measuring rather than
    # guessing. Three samples taken while building this, all on a thrashing Mac
    # — control against teardown: 9.2s/5.3s at load ~250, 10.2s/9.9s at load
    # ~323, and 7.6s/9.3s at load ~232.
    #
    # READ THAT LAST ONE. The control does strictly more WORK than the teardown,
    # but it is not a guaranteed upper bound on its TIME: load swings between
    # the two measurements, and the ops-record write they share is the dominant
    # term in both, so the teardown can and does come out the slower of the two.
    # Anyone tempted to shave the slack because "the control already covers it"
    # should re-read this sample. The slack is not decoration; it is what makes
    # the inversion harmless, and 7.5s against a largest-observed inversion of
    # 1.7s is the ratio being kept.
    #
    # At the 8s pause this block first used, the mutation test below was caught
    # by 0.1s: caught, but one bad sample from a false pass, and a false pass
    # here is a check that no longer checks anything. At 30s it is caught by
    # ~19s, and detection would not begin to fail until a control run outran
    # its own teardown by more than 22s.
    PAUSE_S = 30
    tmp_glob = os.path.join(os.environ.get("TMPDIR", "/tmp"), "carr-key-recovery.*")
    before = set(glob.glob(tmp_glob))
    env = unreachable_env({
        "CARR_KEY_RECOVERY_TEST_SELFTEST": "1",
        "CARR_KEY_RECOVERY_TEST_SELFTEST_TYPED_KEY": secret,
        "CARR_KEY_RECOVERY_TEST_SELFTEST_PUBKEY_FILE": match_pubkey_file,
        "CARR_KEY_RECOVERY_TEST_SELFTEST_PAUSE_AFTER_WRITE": str(PAUSE_S),
    })
    t_spawn = time.monotonic()
    proc2 = subprocess.Popen([SCRIPT], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True, env=env, cwd=REPO, start_new_session=True)
    paused_dir = wait_until_paused(proc2, before, tmp_glob, secret)
    ready_s = time.monotonic() - t_spawn
    check("interrupted path: the script was observed reaching the pause (this "
          "run's identity file appeared on disk), so the SIGINT below lands "
          "inside the pause at any load — never a fixed delay a loaded host "
          "can outrun", paused_dir is not None,
          "no carr-key-recovery temp dir holding this run's identity ever "
          "appeared; the script exited early or never started")
    # A short settle so the signal lands in `sleep` itself rather than in the
    # two statements between the write and it. Not an assertion: if a loaded
    # host is still short of the sleep, the trap fires a moment earlier and
    # everything below still holds — a script that ignored the signal would go
    # on to sleep the full pause and blow the budget exactly the same way.
    time.sleep(0.3)
    t_sig = time.monotonic()
    os.killpg(os.getpgid(proc2.pid), signal.SIGINT)
    try:
        # Generous, because a timeout here is a KILL and a killed child fails
        # every check below for the wrong reason. Lateness is caught by the
        # calibrated budget, not by this number.
        out, err = proc2.communicate(timeout=300)
    except subprocess.TimeoutExpired:
        proc2.kill()
        out, err = proc2.communicate()
    post_signal = time.monotonic() - t_sig

    # THE CONTROL, timed immediately after the interrupted run so it sees the
    # same load: the same script, the same hook, no pause, dying at the phase-1
    # mismatch — an uninterrupted run whose work is a superset of the teardown
    # the interrupted run had left to do.
    t_ctl = time.monotonic()
    ctl = drive({
        "CARR_KEY_RECOVERY_TEST_SELFTEST_TYPED_KEY": secret,
        "CARR_KEY_RECOVERY_TEST_SELFTEST_PUBKEY_FILE": mismatch_pubkey_file,
    }, timeout=300)
    control_s = time.monotonic() - t_ctl
    check("interrupted path: the control run is a usable yardstick (it ran the "
          "same prologue, shred and ops.run write through to the end)",
          ctl.returncode != 0 and "evidence: state=failed" in ctl.stdout,
          f"control rc={ctl.returncode}\n{ctl.stdout[-400:]}")
    budget = control_s + PAUSE_S / 4.0
    check("interrupted path: the script died inside the pause instead of "
          "sleeping it out — teardown after SIGINT finished inside one whole "
          "uninterrupted run of the same script on this same machine, plus a "
          "quarter of the pause",
          post_signal < budget,
          f"teardown took {post_signal:.1f}s; budget {budget:.1f}s "
          f"(control run {control_s:.1f}s + {PAUSE_S / 4.0:.1f}s slack). "
          f"A script that slept the whole {PAUSE_S}s pause lands here, "
          f"~{PAUSE_S * 0.75:.0f}s past the budget, at any load.")
    # Printed on the green path too, deliberately. These four numbers are how a
    # human reading a CI log tells "the interrupt path is healthy" from "this
    # host is thrashing and everything here is slow" — the distinction the old
    # constant could not draw, and the reason it went red on a working script.
    print(f"        timings on this host: startup-to-pause {ready_s:.1f}s, "
          f"teardown-after-SIGINT {post_signal:.1f}s, control run "
          f"{control_s:.1f}s, budget {budget:.1f}s")
    check("interrupted path: the script never reached phase 1 — the pause was "
          "cut short, not run out (this one holds at any load, no clock "
          "involved)", "phase 1:" not in out, out)
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
    # SCOPED TO THIS RUN'S OWN DIRECTORY, because TMPDIR is shared by every
    # session on this Mac. This was a glob diff — "no carr-key-recovery.* dir
    # exists that did not exist before" — and it went red at 21:46 on
    # 2026-08-23 on a directory ANOTHER session created while this suite was
    # mid-run. Same class of false red as the wall-clock bound above (a check
    # reporting on the machine rather than on the script under test) and the
    # same cure. wait_until_paused() already handed back the exact directory
    # THIS run wrote its identity into, so that is the one that must be gone.
    # The glob diff stays only as the fallback for a run that never got a
    # directory to name, where it is all there is.
    if paused_dir is not None:
        survived = os.path.exists(paused_dir)
        leftover_detail = f"still on disk: {paused_dir}"
    else:
        leftover = set(glob.glob(tmp_glob)) - before
        survived = bool(leftover)
        leftover_detail = f"leftover: {leftover}"
    check("interrupted path: no carr-key-recovery temp dir survives — the "
          "identity file and its directory were both shredded and removed",
          not survived, leftover_detail)
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
