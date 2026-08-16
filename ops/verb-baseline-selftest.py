#!/usr/bin/env python3
"""verb-baseline-selftest.py — the verb-loss guard reads the LEDGER, not a file.
Fixtures written before the change (rule e65efc68).

THE PROBLEM, defect d737c09c. bin/deploy-worker.sh's postflight says of
mcp-server/.last-deployed-verb-count.<env>: "COMMIT THAT FILE — it is the
baseline the next deploy is measured against." Committing it fails
ops/release-manifest-selftest.py, so pre-push CI refuses and the file cannot be
committed at all. Isolated on 2026-08-16: clean main passes, the same tree plus
that one file fails.

WHY IT COLLIDES. Manifest artifact_paths are ['mcp-server','dealroom'], so the
baseline sits INSIDE the digested artifact, while the digest covers 73 files and
skips dotfiles. Changing it moves the deployed TREE without moving the DIGEST —
exactly the "tree differs, digest same" condition the manifest test guards. Both
halves work as designed and they instruct opposite actions.

THE RESOLUTION IS NOT A TIEBREAK, IT IS A DUPLICATE REMOVED. Every deploy
ALREADY records its verb count into ops.deployment (`--verb-count "$SHIPPING"`),
and since the ledger fix landed it does so for staging too. The file duplicates
data the system already holds, and the duplicate is the half causing the
collision. Write law 14181e60 — database first, a file only where a machine
requires one — settles which copy goes.

FAIL CLOSED WHEN THE LEDGER CANNOT BE READ, and no escape flag is added. The
argument is not caution: the Worker being deployed needs Postgres for every verb
it serves, so a deploy attempted while the ledger is unreachable ships something
that cannot work anyway. Refusing costs nothing real and keeps a guard that
cannot check from waving a deploy through — which is how the 2026-08-09 verb loss
happened in the first place.

Run: .venv/bin/python ops/verb-baseline-selftest.py
"""
import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEPLOY = REPO / "bin" / "deploy-worker.sh"
HELPER = REPO / "ops" / "last-deployed-verb-count.py"

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


def main() -> int:
    check("the ledger reader exists", HELPER.exists(), f"{HELPER} not found")
    if not HELPER.exists():
        print(f"\nverb-baseline-selftest: {PASSED}/{PASSED + len(FAILED)} passed")
        return 1

    text = DEPLOY.read_text()

    # ── the duplicate is gone ────────────────────────────────────────────────
    check("the deploy no longer WRITES a verb-count file",
          not re.search(r">\s*\"?\$COUNT_FILE", text),
          "the postflight still writes the baseline file that cannot be committed")
    check("the deploy no longer READS a verb-count file",
          "tr -dc '0-9' < \"$COUNT_FILE\"" not in text,
          "the preflight still reads the file rather than the ledger")
    check("no verb-count file is tracked in the repository",
          not list((REPO / "mcp-server").glob(".last-deployed-verb-count*"))
          or not _tracked(".last-deployed-verb-count"),
          "a baseline file is still tracked and will keep colliding with the manifest digest")

    # ── the ledger is the source ─────────────────────────────────────────────
    check("the preflight asks the ledger for the previous count",
          "last-deployed-verb-count.py" in text,
          "nothing in the deploy calls the ledger reader")

    # ── fail closed, and say so ──────────────────────────────────────────────
    check("an unreadable ledger REFUSES the deploy",
          re.search(r"fail \".{0,400}?(ledger|previous verb count)",
                    text, re.DOTALL) is not None,
          "no refusal path when the ledger cannot be read — the guard would wave the deploy through")

    # ── a genuine first deploy is still allowed ──────────────────────────────
    check("a first-ever deploy with no prior row still establishes a baseline",
          "establishes the baseline" in text,
          "the no-prior-deployment path was lost, so a first deploy to a new env would refuse forever")

    # ── the helper's own contract ────────────────────────────────────────────
    htext = HELPER.read_text()
    for code, meaning in ((" 3", "no prior deployment"), ("78", "no credential")):
        check(f"the reader documents exit {code.strip()} ({meaning})",
              code.strip() in htext and meaning.split()[0] in htext.lower(),
              "exit-code contract undocumented; the shell caller cannot distinguish causes")

    # Runs at all, and does not explode without a database.
    proc = subprocess.run([sys.executable, str(HELPER), "carr-mcp", "staging"],
                          capture_output=True, text=True, timeout=120,
                          env={k: v for k, v in os.environ.items()
                               if k not in ("DATABASE_URL", "CARR_DB_JOBS_URL",
                                            "CARR_DB_EXPORTER_URL")})
    check("the reader exits cleanly rather than crashing when it cannot connect",
          proc.returncode in (0, 3, 78, 1) and "Traceback" not in (proc.stderr or ""),
          f"rc={proc.returncode} stderr={(proc.stderr or '')[:160]}")

    print(f"\nverb-baseline-selftest: {PASSED}/{PASSED + len(FAILED)} passed")
    if FAILED:
        print("FAILURES: " + ", ".join(FAILED))
        return 1
    return 0


def _tracked(fragment: str) -> bool:
    out = subprocess.run(["git", "-C", str(REPO), "ls-files"],
                         capture_output=True, text=True, timeout=60)
    return any(fragment in l for l in out.stdout.splitlines())


if __name__ == "__main__":
    sys.exit(main())
