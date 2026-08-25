#!/bin/zsh
# fleet-sync.sh — each Mac keeps its OWN checkout and its OWN installed gates
# current, so neither partner has to carry an update to the other by hand.
#
# WHY THIS EXISTS (2026-08-18). Dell's Mac was migrated clean on 2026-08-11 with
# its adapter wiring exact. Over the next week the repo grew new gates
# (map-architecture, costar-lane, draft-export). Nothing on his machine ever
# fetched them, so his sessions started announcing the whole hook tuple as
# missing — and, running with no gates, had no escalation gate to stop them
# sending every blocker to Joe. He was pulled between two machines twice in one
# day: "i cant keep running back and forth between dells computer and mine."
#
# WHY THE SELF-HEAL IN gate-integrity.py IS NOT ENOUGH ON ITS OWN. That repairs
# an install that lags THE LOCAL REPO. It re-runs the installer from whatever
# ops/config/hooks.json is on disk. If the CHECKOUT is the stale thing, it
# faithfully reinstalls stale gates and reports success. Something has to move
# the checkout, and before this job nothing did: all nineteen scheduled jobs
# were audited on 2026-08-18 and not one touched a git remote.
#
# WHAT IT REFUSES TO DO, which is most of its design:
#   - It NEVER discards local work. A dirty tracked tree exits 78 (skip) with
#     the paths named. Dell's machine deliberately carries two uncommitted edits
#     from his migration; a blind fast-forward would have destroyed them.
#   - It NEVER merges, rebases, resets or force-anythings. Fast-forward only.
#     If the branches have diverged it skips and says so — a diverged checkout
#     is a human question, not a job's decision.
#   - It NEVER runs off main, and never inside a worktree. Worktree-per-session
#     means most sessions run somewhere else entirely; syncing from there would
#     move a branch somebody is mid-edit on.
#
# Exit codes follow bin/run-scheduled.sh's convention: 0 did something or was
# already current, 78 deliberately skipped and said why, anything else failed.

set -u
EX_CONFIG=78

REPO="${0:A:h:h}"
cd "$REPO" || { print -ru2 -- "fleet-sync: cannot enter $REPO"; exit 1 }
PY="$REPO/.venv/bin/python"
[ -x "$PY" ] || PY=python3

# Canonical checkout only. --git-common-dir differs from --git-dir inside a
# worktree, which is the cheapest reliable test.
common="$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null)"
gitdir="$(git rev-parse --path-format=absolute --git-dir 2>/dev/null)"
if [ "$common" != "$gitdir" ]; then
  print -r -- "fleet-sync: SKIP — this is a worktree, not the canonical checkout"
  exit $EX_CONFIG
fi

branch="$(git rev-parse --abbrev-ref HEAD 2>/dev/null)"
if [ "$branch" != "main" ]; then
  print -r -- "fleet-sync: SKIP — checkout is on '$branch', not main; leaving it alone"
  exit $EX_CONFIG
fi

if ! git fetch --quiet origin main 2>/dev/null; then
  print -ru2 -- "fleet-sync: fetch of origin/main failed (offline, or no credential)"
  exit $EX_CONFIG
fi

local_sha="$(git rev-parse HEAD)"
remote_sha="$(git rev-parse origin/main)"

if [ "$local_sha" = "$remote_sha" ]; then
  print -r -- "fleet-sync: checkout already current at ${local_sha:0:8}"
else
  # Tracked changes only. Untracked scratch is a session's business, not this
  # job's, and refusing on it would mean this never runs on a working machine.
  if ! dirt_reason="$("$PY" "$REPO/tools/fleet_sync_safety.py" "$REPO" origin/main)"; then
    print -r -- "fleet-sync: SKIP — local changes present, refusing to fast-forward over them:"
    print -r -- "    $dirt_reason"
    exit $EX_CONFIG
  fi

  # Fast-forward only: HEAD must already be an ancestor of origin/main.
  if ! git merge-base --is-ancestor HEAD origin/main; then
    print -r -- "fleet-sync: SKIP — main has diverged from origin/main; a human decides this one"
    exit $EX_CONFIG
  fi

  if ! git merge --ff-only origin/main >/dev/null 2>&1; then
    print -ru2 -- "fleet-sync: fast-forward failed unexpectedly"
    exit 1
  fi
  print -r -- "fleet-sync: fast-forwarded ${local_sha:0:8} -> ${remote_sha:0:8}"
fi

# Re-render the installed wiring from whatever the checkout now holds. Idempotent
# by design and the same installer bin/migrate-dell.sh runs; on an already-correct
# machine it changes nothing.
if ! CARR_CONFIG_AS_CODE_ACTIVE_LAUNCHD_LABEL=com.carr.fleet-sync \
    "$PY" "$REPO/ops/config-as-code.py" install --apply </dev/null; then
  print -ru2 -- "fleet-sync: config-as-code install --apply failed"
  exit 1
fi

print -r -- "fleet-sync: installed wiring re-rendered from $(git rev-parse --short HEAD)"
exit 0
