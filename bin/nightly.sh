#!/bin/zsh
# nightly.sh — the record layer's unattended chain (ORDER 2).
#
# Until this existed, every generated file was only as fresh as the last time a
# human remembered to run the export by hand. Six steps, in order:
#   1. cadence   (spawn the next_action rows the rules are owed)   [ORDER 14]
#   2. matcher   (availability x open space searches -> digest)    [ORDER 14]
#   3. exports   (all seven targets, LIVE -> the vault)
#   4. consumers (renewal-feed, lead-board, deal-room — the boards built FROM
#                 those files; they must follow the export that feeds them)
#   5. graph     (derived from the freshly exported files, so it follows too)
#   6. backup    (encrypted pg_dump -> git)
#
# Steps 1 and 2 run BEFORE the exports (ORDER 14d) for one reason: they WRITE,
# and a write that lands after the export it belongs in sits invisible for a
# whole day. Same night, same file.
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
    # 78 = EX_CONFIG: the step ran, found a credential or setting it needs is
    # absent, wrote nothing and said so. That is NOT a failed night. Treating it
    # as one would fire the alarm every single night until the credential lands,
    # which is precisely the "an alarm that fires every day trains people to stop
    # reading alarms" problem the ORDER 2 addendum fixed for the boards. The line
    # still appears in the log, so it is visible without being a failure.
    if [ "$rc" -eq 78 ]; then
      say "SKIP  $label (exit 78 — not configured; see the step's own message above)"
    else
      say "FAIL  $label (exit $rc)"
      rc_total=1
    fi
  fi
}

say "===== nightly chain begin ====="
cd "$REPO" || { say "FATAL cannot cd $REPO"; exit 2; }

# Exported explicitly, NOT as a `VAR=1 step ...` prefix: a var-prefix on a
# function call is not reliably scoped in zsh, and if it failed to propagate the
# export would quietly write to staging and the vault would never update — the
# precise silent failure this chain exists to prevent.
export CARR_EXPORT_LIVE=1

# ── ORDER 14: the two writing steps, BEFORE the exports ──────────────────────
# The cadence engine WRITES (next_action + event), so the read-only exporter
# credential above cannot run it. Both steps look for CARR_DB_JOBS_URL first
# (ORDER 19a settled the name: ONE nightly-jobs role, `carr_jobs`, not one
# credential per pipeline; the older CARR_DB_CADENCE_URL / CARR_DB_MATCHER_URL
# names stay accepted) and exit 78 — SKIP, not FAIL — when there is none. Until
# that row exists in ~/.config/carr/db.env both are armed and silent, which is
# honest. THE COMMAND LINES BELOW DO NOT CHANGE when the credential lands: the
# scripts read the environment, so the night the row appears both steps simply
# start doing work, with no new approval surface and no task-file edit.
step "cadence engine (spawn owed next actions)"      ./.venv/bin/python pipelines/cadence_engine.py --apply
step "availability matcher (digest, never sent)"     ./.venv/bin/python pipelines/availability_matcher.py

step "exports (7 targets -> vault)"                  ./run.sh export
step "consumers (renewal-feed, lead-board, deal-room)" ./run.sh all
step "graph (derived from the exported files)"       ./run.sh graph
step "encrypted backup -> git"                       ./bin/backup-dump.sh

# Added 2026-08-02 (cold-session audit): the smoke canary runs IN the chain and records
# its own heartbeat. Before this it sat in the dead-man freshness list with NOTHING
# writing to it, so it read stale from 7/30 onward while passing 17/17 every time anyone
# ran it by hand — a canary whose silence was indistinguishable from its health.
#
# REPLACED 2026-08-04 (loop #178, Joe's ruling). smoke-and-record.sh runs
# mcp-server/smoke-reads.sh, which authenticates to /mcp with the legacy
# PARTNER_TOKENS bearer. That auth path was retired on purpose (5b13ed7,
# 2026-08-03), so the suite has returned 23 failed / 0 passed every night since —
# a canary that is not merely silent but structurally dead, and whose red had
# already become background noise in one day.
#
# Joe ruled against minting a machine credential to revive it, since that would
# partly undo a deliberate retirement. So the chain now runs what it CAN verify
# without a partner identity: the Worker is deployed and still refuses anonymous
# callers, and all 40 v_* views the read verbs sit on are queryable — which is
# the failure that actually bites, because migrations break views and sixteen
# landed in one day on 2026-08-02.
#
# THE FULL VERB SUITE IS NOT DEAD, IT MOVED: run ./mcp-server/smoke-reads.sh
# AFTER EVERY WORKER DEPLOY from an interactive session holding a real grant. It
# still covers transport, dispatch and answer correctness, and this probe says so
# in its own output rather than letting a green row imply more than it proves.
step "verb probe (worker gate + view sweep)"         ./.venv/bin/python ops/nightly-verb-probe.py

# ── ORDER 5: dead-man pings LAST — a ping means the whole chain above ran ────
step "healthchecks dead-man pings"               ./bin/hc-ping.sh

if [ "$rc_total" -eq 0 ]; then
  say "===== nightly chain OK ====="
else
  say "===== nightly chain FINISHED WITH FAILURES — see above ====="
fi

# Keep the log from growing without bound: last 2000 lines is several months.
tail -n 2000 "$LOG" > "$LOG.trim" && mv "$LOG.trim" "$LOG"
exit "$rc_total"
