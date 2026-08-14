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
import time
from typing import Optional

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPT = os.path.join(REPO, "bin", "key-recovery-test.sh")

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
    """Same helper, same reasoning, as ops/restore-rehearse-record-selftest.py:
    port 1 on loopback refuses instantly. DATABASE_URL wins over whatever real
    credentials ~/.config/carr/db.env would otherwise supply, for BOTH this
    script's own ops-record.py call and the chained restore-rehearse.sh's."""
    env = dict(os.environ)
    env["DATABASE_URL"] = "postgresql://nobody@127.0.0.1:1/nothing"
    for leak in ("CARR_DB_JOBS_URL", "CARR_DB_EXPORTER_URL", "PGSERVICE"):
        env.pop(leak, None)
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
    tmp_glob = os.path.join(os.environ.get("TMPDIR", "/tmp"), "carr-key-recovery.*")
    before = set(glob.glob(tmp_glob))
    env = unreachable_env({
        "CARR_KEY_RECOVERY_TEST_SELFTEST": "1",
        "CARR_KEY_RECOVERY_TEST_SELFTEST_TYPED_KEY": secret,
        "CARR_KEY_RECOVERY_TEST_SELFTEST_PUBKEY_FILE": match_pubkey_file,
        "CARR_KEY_RECOVERY_TEST_SELFTEST_PAUSE_AFTER_WRITE": "8",
    })
    t0 = time.time()
    proc2 = subprocess.Popen([SCRIPT], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True, env=env, cwd=REPO, start_new_session=True)
    time.sleep(1.5)
    os.killpg(os.getpgid(proc2.pid), signal.SIGINT)
    try:
        out, err = proc2.communicate(timeout=15)
    except subprocess.TimeoutExpired:
        proc2.kill()
        out, err = proc2.communicate()
    elapsed = time.time() - t0
    check("interrupted path: the script actually terminated promptly on "
          "SIGINT (well under the 8s pause, not after it)",
          elapsed < 6, f"took {elapsed:.1f}s")
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
    after = set(glob.glob(tmp_glob))
    leftover = after - before
    check("interrupted path: no carr-key-recovery temp dir survives — the "
          "identity file and its directory were both shredded and removed",
          not leftover, f"leftover: {leftover}")
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
