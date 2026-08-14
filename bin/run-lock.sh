#!/bin/zsh
# run-lock.sh — one unattended chain at a time. Sourced, not executed.
#
# WHY. On 2026-08-14 the nightly chain reported 30 tampered vault files and
# nothing had been tampered with: the scheduled 02:05 run and a manual run
# overlapped, and the second run's vault-drift check read the files the first
# had just re-exported. Every quarantined diff was a single line, the export
# timestamp. That is the cheap symptom. Two chains against one database also
# race on the export ledger, the encrypted backup, the drift baseline, and the
# consumer rebuilds — and the chain had no mutual exclusion of any kind, so
# running ./bin/nightly.sh while the scheduled one was mid-flight was always
# going to produce two writers and a false alarm.
#
# Manual and scheduled runs take the SAME lock through the SAME code, per rule
# a8c55a47: a manual path and an automated path that do the same job must be the
# same code, or they drift and only one of them is ever tested.
#
# Usage — all three lines, in this order:
#   source "$(dirname "$0")/run-lock.sh"
#   carr_take_lock nightly || exit 0            # loser exits 0: a duplicate is a no-op
#   trap 'carr_release_lock; exit 143' INT TERM HUP
#   trap 'carr_release_lock' EXIT
#
# THE TRAPS BELONG AT THE CALLER'S TOP LEVEL, NOT IN THIS FUNCTION, and that is
# not a style preference. zsh runs an EXIT trap set inside a function when the
# FUNCTION returns, not when the shell exits — the first cut of this helper set
# its own trap, released the lock on the way out of carr_take_lock, and both
# racers in the test won. Measured, not reasoned about.
#
# CORRECTNESS DOES NOT DEPEND ON THE TRAPS FIRING, and it cannot: SIGKILL runs
# no trap at all, and a SIGTERM that arrives while a long step is in flight is
# not handled until that step returns — this chain's steps take minutes, so a
# machine going down can and will take the lock's holder with it while the lock
# still sits there. A hard kill, a panic, or a reboot mid-chain all leave the
# lock directory behind with nothing to release it.
# So the holder is identified by pid and checked for liveness: a lock whose pid
# is gone is reclaimed by the next run. Traps keep the common case tidy; pid
# liveness is what makes it safe to leave unattended. Without the reclaim, one
# crash would silently stop every nightly run from that day forward — quieter,
# and worse, than the failure this helper exists to prevent.
#
# ATOMICITY. mkdir is the atomic primitive, not a test-then-write: it either
# creates the directory or fails, in one syscall, on every filesystem this repo
# runs on. `[ -e lock ] || touch lock` has a window between the test and the
# touch that two chains firing on the same wake will eventually land in.
#
# Tested by tools/test-run-lock.py, written before this file (rule e65efc68) and
# run by ops/ci.sh's suite. That test also asserts bin/nightly.sh wires all four
# lines above, because a helper nobody wires is not mutual exclusion.

CARR_LOCK_DIR="${CARR_LOCK_DIR:-$HOME/carr-system/out/locks}"
CARR_LOCK_PATH=""

carr_take_lock() {              # carr_take_lock <name> -> 0 took it, 1 held elsewhere
  local name="$1"
  local lock="$CARR_LOCK_DIR/carr-$name.lock"
  mkdir -p "$CARR_LOCK_DIR" 2>/dev/null

  if ! mkdir "$lock" 2>/dev/null; then
    local holder
    holder="$(cat "$lock/pid" 2>/dev/null)"
    # A pid we can signal with 0 is alive and holds this lock. A pid we cannot
    # is gone, so the lock is a leftover rather than a live claim.
    if [ -n "$holder" ] && kill -0 "$holder" 2>/dev/null; then
      print -r -- "LOCKED  another '$name' run is in progress (held by pid $holder since $(cat "$lock/since" 2>/dev/null || print -r -- 'unknown')) — this run is a no-op"
      return 1
    fi
    print -r -- "STALE   '$name' lock left behind by pid ${holder:-unknown}, which is no longer running — reclaiming it"
    rm -rf "$lock" 2>/dev/null
    if ! mkdir "$lock" 2>/dev/null; then
      # Lost the race to reclaim: somebody else got there first, and they are live.
      print -r -- "LOCKED  another '$name' run reclaimed the stale lock first (held by pid $(cat "$lock/pid" 2>/dev/null || print -r -- '?')) — this run is a no-op"
      return 1
    fi
  fi

  print -r -- "$$" > "$lock/pid"
  date -u '+%Y-%m-%dT%H:%M:%SZ' > "$lock/since"
  CARR_LOCK_PATH="$lock"
  return 0
}

carr_release_lock() {
  [ -n "$CARR_LOCK_PATH" ] && rm -rf "$CARR_LOCK_PATH" 2>/dev/null
  CARR_LOCK_PATH=""
  return 0
}
