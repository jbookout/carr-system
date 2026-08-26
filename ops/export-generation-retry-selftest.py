#!/usr/bin/env python3
"""Assert the generation-copy retry actually outlasts a OneDrive hydration stall.

The retry path was added on 2026-08-25 after an EDEADLK from the FileProvider
killed the curriculum export, and it shipped with no test.  On 2026-08-26 the
same export died the same way, because the budget it shipped with was three
attempts over 0.05s + 0.10s: a sixth of a second against a cloud-file hydration
that takes seconds.  The step began at 07:05:13Z and failed at 07:05:15Z; the
same file read by hand two minutes later succeeded.

So the number that matters is not "does it retry" but "for how long".  These
tests pin the wall-clock budget and the errno set, because those are the two
things that silently regress to a value that looks like a retry and is not one.
"""
from __future__ import annotations

import errno
import importlib.util
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("carr_exporters_common", ROOT / "exporters" / "common.py")
if spec is None or spec.loader is None:  # pragma: no cover - the file is in-tree
    raise SystemExit("cannot load exporters/common.py")
common = importlib.util.module_from_spec(spec)
spec.loader.exec_module(common)

FAILURES: list[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        FAILURES.append(message)


def total_backoff() -> float:
    return sum(common.GENERATION_COPY_BACKOFF_SECONDS)


# 1. The budget must outlast a real hydration stall, not a scheduler hiccup.
#    OneDrive materialising a dehydrated file is a seconds-scale operation, so a
#    sub-second budget is indistinguishable from no retry at all.
check(
    total_backoff() >= 20.0,
    f"retry budget is {total_backoff():.2f}s of backoff; a OneDrive hydration stall "
    f"outlasts anything under 20s, which is how the 2026-08-26 failure got through",
)

# 2. Attempts and backoff steps must agree.  The loop indexes
#    BACKOFF_SECONDS[attempt] for every attempt but the last, so a mismatch is an
#    IndexError raised from inside the error handler — the retry turning into a
#    different crash.
check(
    len(common.GENERATION_COPY_BACKOFF_SECONDS) == common.GENERATION_COPY_ATTEMPTS - 1,
    f"{common.GENERATION_COPY_ATTEMPTS} attempts needs "
    f"{common.GENERATION_COPY_ATTEMPTS - 1} backoff steps, found "
    f"{len(common.GENERATION_COPY_BACKOFF_SECONDS)}",
)

# 3. The two FileProvider transients stay retryable.  EDEADLK is errno 11 on
#    macOS (where EAGAIN is 35); on Linux those numbers swap.  Naming the
#    constants rather than the integers is what keeps this true on both.
for name in ("EAGAIN", "EDEADLK"):
    check(
        getattr(errno, name) in common.GENERATION_COPY_RETRY_ERRNOS,
        f"{name} must stay in GENERATION_COPY_RETRY_ERRNOS",
    )

# 4. Nothing else may be retried.  A permission, path or storage failure has to
#    escape to the exporter rather than burn the budget and then surface late.
check(
    common.GENERATION_COPY_RETRY_ERRNOS == frozenset({errno.EAGAIN, errno.EDEADLK}),
    f"only EAGAIN/EDEADLK may be retried, found {sorted(common.GENERATION_COPY_RETRY_ERRNOS)}",
)

# 5. A transient that clears mid-budget must produce a real archived generation,
#    not just a swallowed error.  This exercises the actual retry loop.
with tempfile.TemporaryDirectory() as tmp:
    live = Path(tmp) / "dashboard.html"
    live.write_bytes(b"<html>live</html>")

    real_read_bytes = getattr(Path, "read_bytes")
    state = {"calls": 0}

    def flaky_read_bytes(self):
        if self == live:
            state["calls"] += 1
            if state["calls"] <= 2:
                raise OSError(errno.EDEADLK, "Resource deadlock avoided", str(self))
        return real_read_bytes(self)

    real_sleep = common.time.sleep
    common.time.sleep = lambda _seconds: None  # keep the selftest fast
    setattr(Path, "read_bytes", flaky_read_bytes)
    try:
        common.keep_generation(live)
    finally:
        setattr(Path, "read_bytes", real_read_bytes)
        common.time.sleep = real_sleep

    gen_dir = live.parent / (live.name + ".generations")
    archived = sorted(p for p in gen_dir.iterdir() if not p.name.startswith("."))
    check(state["calls"] >= 3, f"expected the read to be retried past two stalls, saw {state['calls']} call(s)")
    check(len(archived) == 1, f"expected exactly one archived generation, found {[p.name for p in archived]}")
    if archived:
        check(
            archived[0].read_bytes() == b"<html>live</html>",
            "the archived generation must be byte-for-byte the file it replaced",
        )
        # The 2026-08-25 failure left a 0-byte archive behind.  A generation that
        # exists but is empty is worse than none: it looks like a rollback point.
        check(archived[0].stat().st_size > 0, "a 0-byte generation is not a rollback point")

if FAILURES:
    print("export-generation-retry: FAIL")
    for line in FAILURES:
        print(f"  - {line}")
    sys.exit(1)

print(
    f"export-generation-retry: OK — {common.GENERATION_COPY_ATTEMPTS} attempts over "
    f"{total_backoff():.0f}s, EAGAIN/EDEADLK only, retried read archives a real generation"
)
