#!/bin/zsh
# smoke-and-record.sh — run the MCP probe suite, then write its verdict to the
# dead-man freshness table so `smoke` can never again read "stale" while passing.
#
# The 2026-08-02 cold-session audit found `smoke` reading last-ok 2026-07-30 while the
# suite itself passed 17/17 on demand: nothing wrote its heartbeat. The heartbeat is
# recorded HERE, from the probe's real exit code, so a green row can only mean the
# probes actually ran and actually passed.
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO" || exit 1

./mcp-server/smoke-reads.sh
probe_exit=$?

./.venv/bin/python tools/record-smoke.py "$probe_exit"
exit $probe_exit
