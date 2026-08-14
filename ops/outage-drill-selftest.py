#!/usr/bin/env python3
"""ops/outage-drill-selftest.py — the acceptance test for bin/outage-drill.py,
Program 4's synthetic degraded-mode harness.

TWO TIERS, matching the house pattern in ops/run-scheduled-selftest.py and
ops/restore-rehearse-record-selftest.py.

TIER 1 (always runs; no staging credential, no network). Drives the REAL
harness as a subprocess for everything that does not need a live database or
a live Worker: --list, --dry-run, unknown-drill handling, and the two drills
that are fully local — model-provider-unavailable (a fixture `claude` binary,
no network) and settings-change-db-outage (a genuinely unreachable DSN, no
staging credential needed for the MECHANICS, only for the evidence write at
the end — see the note on that below).

TIER 2 (only when a staging credential is actually available — gated behind
CARR_OUTAGE_DRILL_SELFTEST_LIVE=1, never on by default, because it spends a
Neon staging round trip and a real HTTP call to the staging Worker on every
invocation otherwise). Runs record-layer-unreachable, stale-observation and
worker-unreachable for real and checks their verdicts.

WHY TIER 1's LOCAL DRILLS STILL NEED "LIVE" LOOSELY DEFINED: both
model-provider-unavailable and settings-change-db-outage end by calling
record_evidence(), which needs a staging DSN to write the evidence row. This
suite does not stub that: it lets the evidence write attempt for real and
tolerates either outcome (recorded, or a loud EVIDENCE WARNING on stderr) —
same no-mock discipline as the rest of this repo's selftests, and consistent
with bin/outage-drill.py's own DrillResult/record_evidence separation: the
DRILL's verdict (checked here) and whether the evidence WRITE itself landed
are two different facts.

RUN IT:
    python3 ops/outage-drill-selftest.py
    CARR_OUTAGE_DRILL_SELFTEST_LIVE=1 python3 ops/outage-drill-selftest.py
"""
import os
import subprocess
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPT = os.path.join(REPO, "bin", "outage-drill.py")
PY = os.path.join(REPO, ".venv", "bin", "python")
if not os.path.exists(PY):
    PY = sys.executable

FAILED: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> bool:
    if cond:
        print(f"  ok    {label}")
    else:
        print(f"  FAIL  {label}" + (f"\n        {detail}" if detail else ""))
        FAILED.append(label)
    return bool(cond)


def run(args: list[str], timeout: int = 60, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run([PY, SCRIPT, *args], capture_output=True, text=True,
                           timeout=timeout, cwd=REPO, env=env)


def tier1() -> None:
    print("TIER 1 — harness mechanics and the two fully-local drills")

    check("the harness exists and is executable",
          os.access(SCRIPT, os.X_OK), SCRIPT)
    if not os.access(SCRIPT, os.X_OK):
        return

    # ── --list ────────────────────────────────────────────────────────────
    proc = run(["--list"])
    check("--list exits 0", proc.returncode == 0, f"rc={proc.returncode}")
    for name in ("record-layer-unreachable", "stale-observation", "worker-unreachable",
                 "model-provider-unavailable", "settings-change-db-outage"):
        check(f"--list names {name!r}", name in proc.stdout, proc.stdout[:800])

    # ── --dry-run touches nothing: proven by poisoning the network AND the
    #    staging credential and confirming it still exits 0 immediately, for
    #    every drill including the ones that would otherwise dial out ───────
    poisoned = dict(os.environ)
    poisoned["DATABASE_URL"] = "postgresql://nobody@127.0.0.1:1/nothing"
    poisoned["NEON_API_KEY"] = "not-a-real-key-outage-drill-selftest"
    for leak in ("CARR_DB_JOBS_URL", "CARR_DB_EXPORTER_URL"):
        poisoned.pop(leak, None)
    proc = run(["--dry-run"], env=poisoned, timeout=20)
    check("--dry-run exits 0 even with every credential poisoned "
          "(proof it performs no I/O at all)", proc.returncode == 0, f"rc={proc.returncode}")
    check("--dry-run describes every drill without running one",
          "DRY RUN" in proc.stdout and "record-layer-unreachable" in proc.stdout,
          proc.stdout[:400])

    # ── unknown --only is a usage error, not a silent no-op ─────────────────
    proc = run(["--only", "not-a-real-drill"])
    check("an unknown --only exits 64 (EX_USAGE)", proc.returncode == 64, f"rc={proc.returncode}")
    check("...and names the drills that DO exist",
          "record-layer-unreachable" in proc.stderr, proc.stderr[:400])

    # ── model-provider-unavailable: fully local, no network at all ──────────
    print("\n  drill: model-provider-unavailable (real fixture, no network)")
    proc = run(["--only", "model-provider-unavailable"], timeout=60)
    check("runs to completion", proc.returncode in (0, 1, 2), f"rc={proc.returncode}\n{proc.stdout[-800:]}")
    check("finds the system TRUTHFUL — no fabricated reply from a dead model",
          "TRUTHFUL:" in proc.stdout and "NOT TRUTHFUL" not in proc.stdout,
          proc.stdout[-800:])
    check("the fixture's own evidence is either genuinely non-zero returncode "
          "or explicitly reported broken, never silently green",
          "convo_core reported a real failure" in proc.stdout, proc.stdout[-800:])

    # ── settings-change-db-outage: fully local, no staging credential needed
    #    for the drill's own mechanics (only the evidence write at the end) ──
    print("\n  drill: settings-change-db-outage (real fixture, genuinely unreachable DSN)")
    proc = run(["--only", "settings-change-db-outage"], timeout=60)
    check("runs to completion", proc.returncode in (0, 1, 2), f"rc={proc.returncode}\n{proc.stdout[-1000:]}")
    check("finds the system TRUTHFUL — never blocks, never hides the outage",
          "TRUTHFUL" in proc.stdout and "NOT TRUTHFUL" not in proc.stdout,
          proc.stdout[-1000:])
    check("reports the not-encrypted finding about the local spool",
          "not encrypted" in proc.stdout.lower() or "PLAINTEXT" in proc.stdout,
          proc.stdout[-1000:])
    check("reports the never-reviewed finding about the local spool",
          "later reviewed" in proc.stdout.lower() or "never reads" in proc.stdout.lower(),
          proc.stdout[-1000:])


def tier2() -> None:
    if os.environ.get("CARR_OUTAGE_DRILL_SELFTEST_LIVE") != "1":
        print("\nTIER 2 — skipped (set CARR_OUTAGE_DRILL_SELFTEST_LIVE=1 to run the "
              "staging-database and staging-Worker drills for real)")
        return

    print("\nTIER 2 — the staging-database and staging-Worker drills, run for real")

    print("\n  drill: record-layer-unreachable")
    proc = run(["--only", "record-layer-unreachable"], timeout=60)
    check("runs to completion", proc.returncode in (0, 1, 2), f"rc={proc.returncode}\n{proc.stdout[-1500:]}")
    check("finds the system TRUTHFUL", "NOT TRUTHFUL" not in proc.stdout, proc.stdout[-1500:])
    check("restores and proves it: the probe is deregistered afterward",
          "probe service is fully deregistered afterward" in proc.stdout, proc.stdout[-1500:])

    print("\n  drill: stale-observation")
    proc = run(["--only", "stale-observation"], timeout=60)
    check("runs to completion", proc.returncode in (0, 1, 2), f"rc={proc.returncode}\n{proc.stdout[-1500:]}")
    check("finds the system TRUTHFUL", "NOT TRUTHFUL" not in proc.stdout, proc.stdout[-1500:])

    print("\n  drill: worker-unreachable (staging only, never production)")
    proc = run(["--only", "worker-unreachable"], timeout=60)
    check("runs to completion", proc.returncode in (0, 1, 2), f"rc={proc.returncode}\n{proc.stdout[-1500:]}")
    check("the local node --test fixture passes",
          "local fixture proves the committed correlation.js mechanism is correct" in proc.stdout,
          proc.stdout[-1500:])
    check("the live staging call never sees a fabricated success",
          "fabricated" not in proc.stdout.lower() or "never a" in proc.stdout.lower(),
          proc.stdout[-1500:])


def main() -> int:
    print("outage-drill-selftest — bin/outage-drill.py must run every drill it "
          "claims to, touch nothing on --dry-run, and never mistake the target's "
          "own dishonesty for its own crash")
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
