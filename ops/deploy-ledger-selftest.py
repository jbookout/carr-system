#!/usr/bin/env python3
"""deploy-ledger-selftest.py — every deploy records that it happened, staging
included. Fixtures written before the fix (rule e65efc68).

THE FAILURE, 2026-08-16, defect cb65fc17. The first approved release in this
system's history shipped to staging, read back clean, and wrote NO row to
ops.deployment. The table held exactly one row and it belonged to a different
session's production release.

THE CAUSE, read out of bin/deploy-worker.sh rather than guessed: all three
`record_deployment` call sites live INSIDE

    if [ "$TARGET_ENV" = "production" ] && [ -x .../smoke-and-record.sh ]; then

so a staging deploy reaches none of them.

WHY THAT BLOCK IS PRODUCTION-ONLY, AND WHY THAT PART IS CORRECT. The script's own
header explains it: smoke-reads.sh defaults to the production API, and a staging
deploy prints its workers.dev trigger from wrangler rather than from anything the
script holds, so aiming the suite at staging would mean reconstructing a hostname
this file would have to keep in sync with wrangler.toml. Pointing post-deploy
verification at the wrong hostname is what the 2026-08-13 routes incident was
made of. That refusal stays.

WHAT WAS WRONG IS THE NESTING, NOT THE REFUSAL. Running the golden suite and
recording that a deploy happened are different facts. The first is genuinely
production-only. The second is the Program 3 job ledger, which Program 5's
promotion path reads, and a staging deploy that leaves no row is indistinguishable
from a staging deploy that never ran.

THE STATE FOR A STAGING DEPLOY IS `verifying`, NOT `complete`, and the script
already defines that word for exactly this case: the suite could not run, so
nothing was proven. Migration 0115 refuses `complete` without a read-back, which
is the constraint doing its job. Automating the staging read-back so it could
honestly claim `complete` is Program 5 work (production read-back is a Program 5
bullet), deliberately not smuggled in here.

These fixtures parse the SHIPPED SCRIPT rather than run a deploy: a real run costs
a Cloudflare deploy and a Neon write. Structure is what regressed, so structure is
what is pinned.

Run: .venv/bin/python ops/deploy-ledger-selftest.py
"""
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "bin" / "deploy-worker.sh"

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


def block_of(text: str, start_pat: str) -> tuple[int, int]:
    """Line span of the shell `if` block whose opening line matches start_pat,
    found by matching if/fi depth. Crude on purpose: the assertion is about
    which lines sit inside which block, so the parse must be literal."""
    lines = text.splitlines()
    start = next((i for i, l in enumerate(lines) if re.search(start_pat, l)), -1)
    if start < 0:
        return (-1, -1)
    depth = 0
    for i in range(start, len(lines)):
        stripped = lines[i].strip()
        if re.match(r"^if\b", stripped):
            depth += 1
        elif stripped == "fi" or stripped.startswith("fi "):
            depth -= 1
            if depth == 0:
                return (start, i)
    return (start, len(lines) - 1)


def main() -> int:
    if not SCRIPT.exists():
        print(f"FAIL: {SCRIPT} not found")
        return 1
    text = SCRIPT.read_text()
    lines = text.splitlines()

    calls = [i for i, l in enumerate(lines) if re.search(r"^\s*record_deployment\s+\w", l)]
    check("the deploy script still records deployments at all", bool(calls),
          "no record_deployment call sites found")
    if not calls:
        print(f"\ndeploy-ledger-selftest: {PASSED}/{PASSED + len(FAILED)} passed")
        return 1

    # ANCHOR ON THE GOLDEN-SUITE BLOCK, not on any production test. The script
    # has more than one `if [ "$TARGET_ENV" = "production" ]`, and the first is a
    # five-line block hundreds of lines earlier. Matching the wrong one made the
    # first run of these fixtures report three failures that were the parser's
    # rather than the script's — caught by tracing the span before trusting it.
    prod_start, prod_end = block_of(
        text, r'^\s*if \[ "\$TARGET_ENV" = "production" \]\s*&&\s*\[ -x .*smoke-and-record')
    check("the production-only golden-suite block is still present",
          prod_start >= 0,
          "the guard that keeps smoke-reads.sh off staging is gone — that guard must stay")

    # THE REGRESSION ITSELF: at least one recording path must sit OUTSIDE the
    # production-only block, or staging deploys are invisible to the ledger.
    outside = [i for i in calls if not (prod_start <= i <= prod_end)]
    check("at least one deployment is recorded OUTSIDE the production-only block",
          bool(outside),
          "every record_deployment call is nested inside the production guard, so a "
          "staging deploy records nothing — this is defect cb65fc17")

    # And it must actually be reachable for a non-production target.
    tail = "\n".join(lines[prod_end + 1:]) if prod_end >= 0 else ""
    check("the non-production path is guarded by a NOT-production test",
          bool(re.search(r'\[\s*"\$TARGET_ENV"\s*!=\s*"production"\s*\]', tail)),
          "nothing after the production block tests for a non-production target")

    # The honest state. `complete` requires a read-back the staging path does not
    # have; migration 0115 refuses it, so claiming it would fail loudly anyway.
    non_prod_states = re.findall(r"record_deployment\s+(\w+)", tail)
    check("the staging deploy records `verifying`, never `complete`",
          bool(non_prod_states) and "complete" not in non_prod_states,
          f"states recorded outside the production block: {non_prod_states}")

    # The refusal that must survive: the golden suite still never aims at staging.
    prod_block = "\n".join(lines[prod_start:prod_end + 1]) if prod_start >= 0 else ""
    check("smoke-and-record.sh is still called ONLY inside the production block",
          "smoke-and-record.sh" in prod_block
          and "smoke-and-record.sh" not in tail,
          "the golden suite escaped its production guard — that is the 2026-08-13 "
          "routes incident waiting to happen again")

    # Recording must never turn a shipped deploy into a failed command.
    fn_start, fn_end = block_of(text, r"^record_deployment\(\)")
    body = text[text.find("record_deployment()"):]
    check("a failed ledger write still never fails the deploy",
          "IT NEVER FAILS THE DEPLOY" in text and "rd_rc" in body,
          "the loud-but-non-fatal contract around the ledger write is gone")

    print(f"\ndeploy-ledger-selftest: {PASSED}/{PASSED + len(FAILED)} passed")
    if FAILED:
        print("FAILURES: " + ", ".join(FAILED))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
