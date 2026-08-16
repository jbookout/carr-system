#!/usr/bin/env python3
"""
deploy-release-wiring-selftest.py — the deploy path names an approved release,
or it does not ship.

WHAT THIS GUARDS. P0-1 put the release object in the database and gave migration
0131 the teeth to refuse a production deployment that names no approved release.
None of that reaches a real deploy unless bin/deploy-worker.sh actually asks. So
this proves the wrapper asks, that a refusal happens BEFORE wrangler is invoked,
and that all three outcomes of a deploy reach the ledger.

WHY SOURCE ASSERTIONS AND NOT A FULL RUN. deploy-worker.sh refuses to run at all
unless the tree is exactly origin/main with a clean Worker directory and a real
wrangler binary — by design, and none of it is reproducible inside a test. The
house pattern for that shape is ops/backup-dump-selftest.py: assert the exact
command form in the source, and execute the parts that CAN run hermetically.
Here the executable part is the release preflight's own logic, exercised through
tools/ops-record.py with no database at all.

  1. THE REFUSAL PRECEDES THE DEPLOY. The require check appears before the
     wrangler invocation. A gate that runs after the ship is a report.

  2. THE REFUSAL IS A REFUSAL. Any non-zero result from the require check
     reaches `fail`; a ledger outage cannot become permission to ship.

  3. ALL THREE OUTCOMES REACH THE LEDGER. complete, verifying and failed each
     have a record_deployment call, and `complete` is the only one that claims a
     read-back.

  4. THE FAILED PATH RECORDS BEFORE IT EXITS. Recording after `exit 1` records
     nothing at all.

  5. THE MANIFEST IS BUILT BY THE ONE TOOL. The wrapper shells out to
     tools/release-manifest.py rather than carrying a second copy of the digest
     decision (rule a8c55a47).

  6. A source rehearsal's `release require` DEMANDS ITS SHA and refuses without
     one, so a staging wrapper that loses the variable gets an error rather
     than a pass. Production instead requires provider + immutable version.

  7. THE PRINTED ROLLBACK COMMAND USES THE PRODUCTION PROMOTION FLAG. Production
     no longer rebuilds a SHA; rollback approves and promotes a known immutable
     provider version through `--promote-version`.
"""

import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "bin" / "deploy-worker.sh"
RECORD = REPO / "tools" / "ops-record.py"

FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  ok    {name}")
    else:
        FAILURES.append(name)
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))


def main() -> int:
    print("deploy-release-wiring-selftest: the deploy names its release")
    source = SCRIPT.read_text(encoding="utf-8")

    # 1. the gate precedes the ship
    require_at = source.find("release require")
    deploy_at = source.find('"$WRANGLER" deploy')
    check("1. the require check runs BEFORE wrangler deploy",
          require_at != -1 and deploy_at != -1 and require_at < deploy_at,
          f"require at {require_at}, wrangler at {deploy_at}")

    # 2. Provider promotion is strict, while routine source rehearsal preserves
    # main's credential boundary: only the explicit not-approved result blocks.
    check("2. provider promotion refuses every non-zero approval result",
          re.search(r'\[ "\$REQUIRE_RC" -eq 0 \]\s*\\\s*\n\s*\|\| fail '
                    r'"no live approval binds Production', source) is not None,
          "provider-version promotion can proceed without exact release truth")
    check("2b. source rehearsal treats exit 3 as the approval refusal",
          re.search(r'REQUIRE_RC["}]*\s*-eq\s*3', source) is not None
          and re.search(r'-eq 3 \]; then\s*\n\s*fail ', source) is not None,
          "routine staging/rehearsal lost main's scoped credential boundary")

    # 3. all three outcomes are recorded
    for state in ("complete", "verifying", "failed"):
        check(f"3. the {state} outcome records a deployment",
              f"record_deployment {state}" in source)

    check("3b. only `complete` claims a read-back",
          '"$rd_state" = "complete"' in source and "--read-back-at now" in source,
          "the read-back is not conditioned on the state")

    # 3c. a verified deploy CLOSES its release
    close_at = source.find("release complete --key")
    check("3c. a complete deploy closes the release, and only a complete one",
          close_at != -1 and '"$rd_state" = "complete" ] && [ -n "$RELEASE_KEY"' in source,
          "nothing advances the release past approved, so it and its deployment disagree")

    # 4. the failed path records before it exits
    failed_at = source.find("record_deployment failed")
    exit_at = source.find("exit 1", failed_at if failed_at != -1 else 0)
    check("4. the failed path records BEFORE exit 1",
          failed_at != -1 and exit_at > failed_at,
          "recording after the exit records nothing")

    # 5. one digest decision, not two
    check("5. the manifest comes from tools/release-manifest.py",
          "tools/release-manifest.py" in source
          and "sha256" not in source,
          "the wrapper appears to compute a digest of its own")

    # 6. a source rehearsal still refuses without a SHA, with no DB in sight
    out = subprocess.run(
        [sys.executable, str(RECORD), "release", "require",
         "--environment", "staging"],
        capture_output=True, text=True, cwd=REPO)
    check("6. staging `release require` refuses with no --sha",
          out.returncode == 2 and "needs --sha" in (out.stderr + out.stdout),
          f"rc={out.returncode} err={out.stderr.strip()[:120]}")

    check("7. rollback instruction uses immutable provider promotion",
          "bin/deploy-worker.sh --promote-version "
          "<approved-prior-version-id>." in source
          and "Rolling back is bin/deploy-worker.sh --release-sha <sha>." not in source,
          "failure output tells Production to rebuild a SHA instead of promoting "
          "an approved provider version")

    print()
    if FAILURES:
        print(f"deploy-release-wiring-selftest: {len(FAILURES)} FAILED")
        for f in FAILURES:
            print(f"  {f}")
        return 1
    print("deploy-release-wiring-selftest: the deploy path is wired to release truth")
    return 0


if __name__ == "__main__":
    sys.exit(main())
