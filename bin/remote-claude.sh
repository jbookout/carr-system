#!/bin/bash
# Run one headless Claude job on another partner's Mac, billed to that Mac's login.
#
#   echo "<task>" | bin/remote-claude.sh dell [claude -p flags...]
#   bin/remote-claude.sh dell --output-format json < brief.md
#
# HOST is an SSH alias (dell, macbook). The prompt goes over stdin, so no task
# text is ever quoted onto a remote command line. The remote side is
# bin/claude-headless.py in that Mac's ~/carr-system checkout; it refuses when
# the Mac has no long-lived login rather than billing some other account.
set -euo pipefail

if [ $# -lt 1 ]; then
  echo "usage: bin/remote-claude.sh HOST [claude -p flags...] < prompt" >&2
  exit 2
fi
host=$1; shift

remote_args=""
for arg in "$@"; do remote_args+=" $(printf '%q' "$arg")"; done

exec ssh -o BatchMode=yes -o ConnectTimeout=10 "$host" \
  "cd ~/carr-system && python3 bin/claude-headless.py$remote_args"
