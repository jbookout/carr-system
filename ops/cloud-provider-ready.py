#!/usr/bin/env python3
"""Wait until OneDrive's File Provider can actually serve the export targets.

WHY THIS EXISTS, measured 2026-09-17. The six business exports failed 2 nights
running, and the chain as a whole has failed 26 of its last 27 launchd runs. The
error is always the same: `OSError: [Errno 11] Resource deadlock avoided`, raised
out of keep_generation() while it reads the PREVIOUS OneDrive copy to keep the
dated rollback generation. run_exports.py already names the shape of it —
"`curriculum` is the first key in TARGETS and its OneDrive file locks
intermittently around the 02:05 window" — and exporters/common.py already retries
EAGAIN/EDEADLK/ETIMEDOUT with a 23.5s budget. That budget is not the problem.

THE MECHANISM, which the retry budget cannot reach. Every file in the CARR
OneDrive tree is `dataless`: 687 of 861 files on the morning this was written,
for a tree whose entire logical size is 1.4 GiB. OneDrive's Files On-Demand has
evicted them, because the Data volume sits at 97% full. A dataless file has no
local bytes, so reading one is not a disk read — it is a synchronous request to
the OneDrive File Provider extension to fetch the content back. `pmset repeat
wakeorpoweron` wakes this Mac at 01:55 and launchd fires the chain at 02:05, so
that request lands ten minutes into a scheduled dark wake, while the provider is
still coming up. It answers EDEADLK, and it keeps answering EDEADLK for minutes
at a time — measured at roughly 8 minutes on 2026-09-16 and past 10 on 09-17. A
23.5-second per-target retry cannot outlast that, so all six targets burn their
budget and the step fails.

Proof that nothing else is wrong: re-running the identical export by hand at
06:08 the same morning, against the same 97%-full disk and the same dataless
files, published all six targets clean. The code is fine. The provider was not
awake yet.

WHAT THIS DOES. It blocks at the front of the chain until the provider proves it
can serve a real read, then lets the cloud-touching steps run. It is a GATE, not
a repair: it fixes nothing about OneDrive, it just refuses to start work that
depends on a service that is not up yet, which is the difference between a chain
that waits four minutes and a chain that fails for 26 nights.

THE PROBE SET IS THE REAL WORK, NOT A PROXY. It reads the actual export target
files — the exact bytes keep_generation() is about to read — so a pass here means
the step that follows can do its job, rather than meaning some unrelated file
happened to be local. Reading them also materializes them, so the gate warms the
cache it just tested.

WHY IT FAILS LOUD RATHER THAN SKIPPING. An exit 78 here would read as "not
configured" and mark the night healthy while six receipts went stale, which is
precisely the failure this file is named after. A provider that never comes up
inside the budget is a real finding and exits 1.

Exit codes:  0 ready (or nothing cloud-backed to wait for) · 1 provider never
served a read inside the budget · 78 no export home configured on this machine.
"""

from __future__ import annotations

import errno
import os
import sys
import time
from pathlib import Path

# The same default bin/nightly.sh carries, and for the same reason: the path is
# stated once by CARR_EXPORT_HOME and this is the fallback when it is unset.
DEFAULT_EXPORT_HOME = Path(
    "/Users/booko/Library/CloudStorage/OneDrive-CARR,Inc/Joe's Folder/CARR AI"
)

# The three errnos a File Provider returns for "I cannot serve this right now".
# Identical to exporters/common.py's GENERATION_COPY_RETRY_ERRNOS on purpose:
# one definition of "transient cloud", two places that must agree about it.
TRANSIENT_ERRNOS = frozenset({errno.EAGAIN, errno.EDEADLK, errno.ETIMEDOUT})

# 20 minutes, against a measured outage of 8-10 minutes. Long enough to cover
# roughly double the worst observed wait, short enough that a genuinely dead
# provider is reported the same night instead of hanging until morning. The
# chain's own step timeout is the backstop above this one.
BUDGET_SECONDS = float(os.environ.get("CARR_CLOUD_READY_BUDGET_SECONDS", 1200))

# 20s between sweeps. The thing being waited on takes minutes, so polling faster
# buys nothing and just multiplies the log.
POLL_SECONDS = float(os.environ.get("CARR_CLOUD_READY_POLL_SECONDS", 20))

# A floor, so a zero or negative poll cannot turn the wait into a busy loop. The
# selftest caught this at 20,863 attempts against a 0.05s budget: the deadline
# was still honoured, but a gate that pins a core while it waits is a gate that
# competes with the provider it is waiting for.
MIN_POLL_SECONDS = 0.01

# Read this much of each probe file. Materialization is per FILE, not per byte:
# the provider must fetch the content before it can answer the first read at all,
# so one chunk proves the same thing a full read proves, at a fraction of the
# cost on a 9320-row workbook.
PROBE_BYTES = 1 << 16


def export_home() -> Path:
    configured = os.environ.get("CARR_EXPORT_HOME", "").strip()
    return Path(configured) if configured else DEFAULT_EXPORT_HOME


def probe_paths(home: Path) -> list[Path]:
    """The live export targets that exist on disk, newest-first by declaration.

    Asking exporters.targets rather than globbing keeps this honest: if a target
    is added, renamed or retired, the gate follows it without a second list to
    maintain. A target that does not exist yet is not a probe — keep_generation
    returns early on a missing file, so there is nothing for the provider to
    serve and nothing to wait for.
    """
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from exporters.targets import TARGETS  # noqa: PLC0415
    except Exception as error:  # pragma: no cover - import shape, not logic
        print(
            f"cloud-ready: cannot read the target registry ({type(error).__name__}: "
            f"{error}); falling back to whatever files the export home holds",
            flush=True,
        )
        return [p for p in sorted(home.rglob("*")) if p.is_file()][:6]

    paths = []
    for _key, (relative, _build) in TARGETS.items():
        if relative.lower().endswith(".md"):
            continue  # retired at the 2026-08-19 cutoff; never published again
        candidate = home / relative
        if candidate.is_file():
            paths.append(candidate)
    return paths


def errno_name(error: OSError) -> str:
    """The symbolic name for an OSError's errno, for a human reading the log.

    OSError.errno is Optional: an OSError raised without one is rare but legal,
    and a bare `errorcode[error.errno]` on that path would replace the real
    failure with a KeyError while reporting it.
    """
    if error.errno is None:
        return "?"
    return errno.errorcode.get(error.errno, "?")


def read_probe(path: Path) -> OSError | None:
    """Return None when the provider served this file, else the OSError it raised."""
    try:
        with path.open("rb") as stream:
            stream.read(PROBE_BYTES)
        return None
    except OSError as error:
        return error


def main() -> int:
    home = export_home()
    if not home.is_dir():
        print(
            f"cloud-ready: SKIP — no export home at {home}; nothing cloud-backed "
            f"to wait for on this machine",
            flush=True,
        )
        return 78

    probes = probe_paths(home)
    if not probes:
        print(
            "cloud-ready: OK — no published export target exists yet, so no "
            "provider read is owed",
            flush=True,
        )
        return 0

    deadline = time.monotonic() + BUDGET_SECONDS
    attempt = 0
    while True:
        attempt += 1
        failures = [(p, e) for p in probes if (e := read_probe(p)) is not None]
        if not failures:
            print(
                f"cloud-ready: OK — all {len(probes)} export target(s) readable "
                f"on attempt {attempt}",
                flush=True,
            )
            return 0

        # A NON-TRANSIENT ERROR IS NOT SOMETHING TO WAIT OUT. A permission or
        # path failure will read exactly the same on the last attempt as on the
        # first, so burning 20 minutes to re-learn it delays the report by 20
        # minutes and changes nothing.
        hard = [(p, e) for p, e in failures if e.errno not in TRANSIENT_ERRNOS]
        if hard:
            for path, error in hard:
                print(
                    f"cloud-ready: FAIL — {path.name}: {type(error).__name__} "
                    f"errno={error.errno} ({errno_name(error)}); "
                    f"not a transient provider state, so not waiting",
                    file=sys.stderr,
                    flush=True,
                )
            return 1

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            print(
                f"cloud-ready: FAIL — the File Provider did not serve "
                f"{len(failures)} of {len(probes)} export target(s) within "
                f"{BUDGET_SECONDS:.0f}s across {attempt} attempt(s). "
                f"Every file in this tree is cloud-only (dataless) because the "
                f"volume is near full, so each read needs OneDrive to fetch it "
                f"back. Fix the cause, not this gate: mark the OneDrive CARR "
                f"folder 'Always Keep on This Device', or free disk so Files "
                f"On-Demand stops evicting it.",
                file=sys.stderr,
                flush=True,
            )
            for path, error in failures:
                print(
                    f"  still unreadable: {path.name} "
                    f"(errno={error.errno} {errno_name(error)})",
                    file=sys.stderr,
                    flush=True,
                )
            return 1

        names = ", ".join(sorted(p.name for p, _ in failures))
        print(
            f"cloud-ready: attempt {attempt} — provider not serving {len(failures)} "
            f"file(s) ({names}); {remaining:.0f}s of budget left",
            flush=True,
        )
        time.sleep(max(MIN_POLL_SECONDS, min(POLL_SECONDS, remaining)))


if __name__ == "__main__":
    sys.exit(main())
