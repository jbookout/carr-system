#!/usr/bin/env python3
"""record-smoke.py — write the smoke canary's heartbeat into export_run.

WHY THIS EXISTS (2026-08-02, the cold-session audit's sharpest finding): `smoke` sat in
the dead-man freshness list reading "last ok 2026-07-30" while the smoke suite itself was
passing 17/17 on demand. Nothing wrote its heartbeat — the canary was alive and its
monitor could not tell. A canary whose silence is indistinguishable from its health is
worse than no canary, because it trains people to ignore a red line.

Run it from the nightly chain AFTER the probe suite, passing the suite's exit code:
    ./mcp-server/smoke-reads.sh; ./.venv/bin/python tools/record-smoke.py $?
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from exporters.common import connect, record_run

def main() -> int:
    probe_exit = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    status = "ok" if probe_exit == 0 else "failed"
    # row_count is NOT NULL on export_run; the probe count is the honest value here
    # (17 checks as of 2026-08-02). Passing the count rather than a placeholder means a
    # future probe added or lost shows up as a row_count change in the freshness table.
    probe_count = int(sys.argv[2]) if len(sys.argv) > 2 else 17
    # checksum is NOT NULL too. A probe suite has no file to hash, so the honest
    # stand-in is the verdict itself: "<count> probes, <status>". It changes when the
    # suite's shape or result changes, which is exactly what a checksum is for here.
    checksum = f"{probe_count}-probes-{status}"
    with connect() as conn, conn.cursor() as cur:
        record_run(cur, "smoke", probe_count, checksum, status)
        conn.commit()
    print(f"smoke heartbeat recorded: status={status} (probe exit {probe_exit})")
    # Propagate the probe's verdict so the chain step fails when the probes failed.
    return 0 if probe_exit == 0 else 1

if __name__ == "__main__":
    sys.exit(main())
