#!/bin/zsh
# partner-ping-run.sh — one launchd wake, two recorded facts.
#
# pipelines/partner_ping.py answers "did the buzz fire" — its own service,
# partner-ping. The SAME wake is also the cheapest true signal that this Mac is
# awake, Joe is logged in, and launchd is actually running its agents, which is
# exactly what the carr-local-edge-node service (Program 4, PROP-010) needs to
# stay honest: a sleeping, logged-out, or unreachable Mac must read stale or
# unknown, never healthy. Riding partner-ping's already-scheduled 2-minute wake
# means the edge node needs no LaunchAgent of its own.
#
# The edge-node heartbeat is deliberately RECORDED INDEPENDENTLY of whether
# partner_ping.py itself succeeds — a broken partner-ping query says nothing
# about whether the Mac is awake, and conflating the two would make the edge
# node's presence signal only as reliable as one unrelated script.
#
# Both calls go through bin/with-run-record.sh, so the throttle, correlation
# threading, and "recording can never block or fail the wrapped job" contract
# are the SAME code as every other wrapped job in this system (rule a8c55a47).
set -u
REPO="$(cd "$(dirname "$0")/.." && pwd)"

"$REPO/bin/with-run-record.sh" partner-ping --heartbeat-interval 1800 -- \
  "$REPO/.venv/bin/python" "$REPO/tools/db-tap.py" run pipelines/partner_ping.py
ping_rc=$?

"$REPO/bin/with-run-record.sh" carr-local-edge-node --heartbeat-interval 1800 -- \
  /usr/bin/true

# partner_ping.py's own outcome is what launchd should see for THIS job — the
# edge-node heartbeat above is a side signal, not this script's purpose.
exit $ping_rc
