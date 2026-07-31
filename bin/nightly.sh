#!/bin/zsh
# nightly.sh — the record layer's unattended chain (ORDER 2).
#
# Until this existed, every generated file was only as fresh as the last time a
# human remembered to run the export by hand. Four steps, in order:
#   1. exports   (all seven targets, LIVE -> the vault)
#   2. consumers (renewal-feed, lead-board, deal-room — the boards built FROM
#                 those files; they must follow the export that feeds them)
#   3. graph     (derived from the freshly exported files, so it follows too)
#   4. backup    (encrypted pg_dump -> git)
#
# Step 2 was added by Fable's ORDER 2 addendum. Without it the chain refreshed
# lead-registry.xlsx and panhandle-team-deals.json every night while their boards
# sat unrebuilt, so the façade check reported Lead Board and Deal Room BEHIND
# every single morning — by design, forever. An alarm that fires every day trains
# people to stop reading alarms, which costs more than the two minutes it saves.
#
# Every step runs even if an earlier one failed, and the exit code reports the
# worst outcome. The backup especially must not be skipped because an export
# broke — a bad export is exactly when you want a snapshot of the database.
#
# Appends to out/nightly.log. Verified by OUTPUT freshness (protocol rule 28),
# never by this script existing: tools/health-check.py watches the seven files.
#
# Run by hand any time: ./bin/nightly.sh
set -u

REPO="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="/opt/homebrew/opt/node@22/bin:/opt/homebrew/bin:/opt/homebrew/opt/libpq/bin:/usr/local/bin:/usr/bin:/bin"
LOG="$REPO/out/nightly.log"
mkdir -p "$REPO/out"

# Exporter credential. Same file the manual runs use; never inlined here.
if [ -f "$HOME/.config/carr/db.env" ]; then
  set -a; . "$HOME/.config/carr/db.env"; set +a
fi

say() { print -r -- "$(date -u '+%Y-%m-%dT%H:%M:%SZ')  $*" >> "$LOG"; }

rc_total=0
step() {                        # step <label> <command...>
  local label="$1"; shift
  say "START $label"
  if "$@" >> "$LOG" 2>&1; then
    say "OK    $label"
  else
    local rc=$?
    say "FAIL  $label (exit $rc)"
    rc_total=1
  fi
}

say "===== nightly chain begin ====="
cd "$REPO" || { say "FATAL cannot cd $REPO"; exit 2; }

# Exported explicitly, NOT as a `VAR=1 step ...` prefix: a var-prefix on a
# function call is not reliably scoped in zsh, and if it failed to propagate the
# export would quietly write to staging and the vault would never update — the
# precise silent failure this chain exists to prevent.
export CARR_EXPORT_LIVE=1

step "exports (7 targets -> vault)"                  ./run.sh export
step "consumers (renewal-feed, lead-board, deal-room)" ./run.sh all
step "graph (derived from the exported files)"       ./run.sh graph
step "encrypted backup -> git"                       ./bin/backup-dump.sh

if [ "$rc_total" -eq 0 ]; then
  say "===== nightly chain OK ====="
else
  say "===== nightly chain FINISHED WITH FAILURES — see above ====="
fi

# Keep the log from growing without bound: last 2000 lines is several months.
tail -n 2000 "$LOG" > "$LOG.trim" && mv "$LOG.trim" "$LOG"
exit "$rc_total"
