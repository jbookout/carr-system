#!/usr/bin/env python3
"""Hermetic dispatch contract for ``./run.sh all``.

``all`` is the normal record-backed rebuild: Lead Board first, then Deal Room.
The MLS renewal-feed remains an explicit recovery-only command until it has a
canonical ingress, so a normal aggregate run must never dispatch it.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
POISONED_VAULT = "/definitely-not-a-carr-source"


def run_fixture(*, lead_board_injection: str = "") -> tuple[subprocess.CompletedProcess[str], list[str]]:
    """Run a copied launcher whose interpreter only records its argv.

    ``lead_board_injection`` models a future wrapper change before the all-arm;
    it must be detected from the actual child argv, not inferred from the arm.
    """
    with tempfile.TemporaryDirectory() as raw:
        fixture = Path(raw)
        launcher = fixture / "run.sh"
        source = (REPO / "run.sh").read_text(encoding="utf-8")
        marker = 'lead_board()   { "$PY" "$REPO/generators/build-lead-board.py" "$@"; }'
        replacement = ('lead_board()   { "$PY" "$REPO/generators/build-lead-board.py" '
                       f'"$@"{lead_board_injection}; }}')
        assert marker in source, "lead_board wrapper changed; update fixture marker"
        launcher.write_text(source.replace(marker, replacement, 1), encoding="utf-8")
        launcher.chmod(0o755)
        fake_python = fixture / ".venv" / "bin" / "python"
        fake_python.parent.mkdir(parents=True)
        log = fixture / "calls.log"
        fake_python.write_text(
            "#!/bin/sh\n"
            "printf '%s\\n' \"$*\" >> \"$CARR_SELFTEST_CALL_LOG\"\n",
            encoding="utf-8",
        )
        fake_python.chmod(0o755)
        proc = subprocess.run(
            [str(launcher), "all"], text=True, capture_output=True,
            env={**os.environ, "CARR_SELFTEST_CALL_LOG": str(log),
                 "CARR_VAULT": POISONED_VAULT},
        )
        calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return proc, calls


def canonical_child_arguments(calls: list[str]) -> bool:
    """Whether child argv stays on the canonical normal-mode contract."""
    forbidden = ("--files", "--records", "--recovery", "--reason", "--vault")
    return all(all(control not in call for control in forbidden) for call in calls)


def main() -> int:
    failures: list[str] = []
    checks = 0

    def check(name: str, value: bool, detail: str = "") -> None:
        nonlocal checks
        checks += 1
        print(f"  {'ok  ' if value else 'FAIL'} {name}")
        if not value:
            if detail:
                print(f"        {detail}")
            failures.append(name)

    # The copied launcher resolves REPO to this fixture. Its only interpreter
    # records invocations, so proving the aggregate order cannot read Drive,
    # reach a DB, or write a real board.
    aggregate, calls = run_fixture()

    check("all exits through only fixture commands", aggregate.returncode == 0,
          aggregate.stdout + aggregate.stderr)
    # The exact temporary root is intentionally irrelevant; assert the routed
    # generator names and order rather than the fixture path.
    routed = [Path(call.split()[0]).name for call in calls]
    check("all order is lead_board then deal_room",
          routed == ["build-lead-board.py", "build-deal-room.py"],
          f"observed: {routed}")
    check("all excludes recovery-only renewal_feed",
          "build-renewal-feed.py" not in routed, f"observed: {routed}")
    check("all does not force the legacy files mode",
          canonical_child_arguments(calls), f"observed: {calls}")

    _, injected_calls = run_fixture(
        lead_board_injection=" --recovery --reason selftest --vault /tmp/fixture")
    check("adversarial lead_board recovery injection is rejected",
          not canonical_child_arguments(injected_calls), f"observed: {injected_calls}")

    source = (REPO / "run.sh").read_text(encoding="utf-8")
    all_arm = source.split("  all)", 1)[1].split("\n  ", 1)[0]
    check("all arm adds no Drive environment or recovery fallback",
          "CARR_VAULT" not in all_arm and "--recovery" not in all_arm
          and "--vault" not in all_arm and "--files" not in all_arm,
          all_arm.strip())

    # This drives the real standalone entry point.  Its refusal occurs before
    # opening a recovery root, so the poisoned ambient value is safe and proves
    # normal mode did not grow a Drive fallback while aggregate dispatch moved.
    standalone = subprocess.run([str(REPO / "run.sh"), "renewal-feed"], text=True,
                                capture_output=True,
                                env={**os.environ, "CARR_VAULT": POISONED_VAULT})
    refusal = standalone.stdout + standalone.stderr
    check("standalone renewal-feed still dispatches and fails closed",
          standalone.returncode != 0
          and "canonical external MLS ingress is not implemented" in refusal
          and "normal mode refuses Drive files" in refusal
          and POISONED_VAULT not in refusal, refusal)

    if failures:
        print(f"FAIL {len(failures)}: {', '.join(failures)}")
        return 1
    print(f"run all canonical selftest: {checks}/{checks} passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
