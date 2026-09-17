#!/usr/bin/env python3
"""Prove the cloud-readiness gate on the four outcomes that matter.

A gate is only worth having if its NO is as trustworthy as its YES, so the two
failure paths are tested as hard as the success path: a transient provider that
recovers must pass, and a transient provider that never recovers must exit 1
rather than hang or pass. The hard-error case is separate because waiting out a
permission failure would delay the report by the whole budget and change nothing.

Runs with no database, no network and no OneDrive: the probe read is monkeypatched
so the clock and the provider are both under the test's control.
"""

from __future__ import annotations

import errno
import importlib.util
import sys
import tempfile
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location(
    "cloud_provider_ready", HERE / "cloud-provider-ready.py"
)
assert SPEC and SPEC.loader
# Deliberately Any: this test monkeypatches the subject's probe and clock, and a
# module object loaded from a hyphenated path has no static shape for mypy to
# check those assignments against.
gate: Any = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)

FAILURES: list[str] = []


def check(name: str, got, want) -> None:
    if got == want:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name}: got {got!r}, want {want!r}")
        FAILURES.append(name)


def with_home(tmp: Path, targets: list[str]):
    """Point the gate at a scratch export home holding the named target files."""
    for rel in targets:
        path = tmp / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * 128)
    gate.export_home = lambda: tmp
    gate.probe_paths = lambda home: [home / rel for rel in targets]


def main() -> int:
    print("cloud-provider-ready selftest")

    # 1. A provider serving every read passes on the first attempt.
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        with_home(tmp, ["DNA/Leads/lead-registry.xlsx", "DNA/Network/vendors.xlsx"])
        gate.read_probe = lambda path: None
        check("ready provider exits 0", gate.main(), 0)

    # 2. A provider that is down and then recovers must PASS, not fail early.
    #    This is the whole point of the gate: the 02:05 outage is transient.
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        with_home(tmp, ["DNA/Leads/lead-registry.xlsx"])
        state = {"calls": 0}

        def flaky(path):
            state["calls"] += 1
            if state["calls"] <= 2:
                return OSError(errno.EDEADLK, "Resource deadlock avoided")
            return None

        gate.read_probe = flaky
        gate.POLL_SECONDS = 0
        gate.BUDGET_SECONDS = 60
        check("transient provider that recovers exits 0", gate.main(), 0)
        check("it waited rather than passing blind", state["calls"] >= 3, True)

    # 3. A provider that never recovers must exit 1 inside the budget, not hang.
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        with_home(tmp, ["DNA/Leads/lead-registry.xlsx"])
        state = {"calls": 0}

        def never(path):
            state["calls"] += 1
            return OSError(errno.EDEADLK, "Resource deadlock avoided")

        gate.read_probe = never
        gate.POLL_SECONDS = 0
        gate.BUDGET_SECONDS = 0.05
        check("exhausted budget exits 1", gate.main(), 1)
        # The floor is what stops a zero poll becoming a busy loop. Without it
        # this same case spun 20,863 times; with it the budget divided by the
        # floor is the ceiling, and that is a bound rather than a hope.
        check(
            "a zero poll cannot busy-spin",
            state["calls"] <= int(0.05 / gate.MIN_POLL_SECONDS) + 2,
            True,
        )

    # 4. A non-transient error is reported immediately, not waited out.
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        with_home(tmp, ["DNA/Leads/lead-registry.xlsx"])
        state = {"calls": 0}

        def denied(path):
            state["calls"] += 1
            return OSError(errno.EACCES, "Permission denied")

        gate.read_probe = denied
        gate.POLL_SECONDS = 0
        gate.BUDGET_SECONDS = 600
        check("hard error exits 1", gate.main(), 1)
        check("hard error did not wait out the budget", state["calls"], 1)

    # 5. No export home at all is a SKIP (78), not a failed night.
    gate.export_home = lambda: Path("/nonexistent/carr/export/home")
    check("absent export home exits 78", gate.main(), 78)

    if FAILURES:
        print(f"\nFAILED: {len(FAILURES)} check(s): {', '.join(FAILURES)}")
        return 1
    print("\nall checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
