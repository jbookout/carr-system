#!/bin/zsh
# nightly.sh — the record layer's unattended chain (ORDER 2).
#
# Until this existed, every generated file was only as fresh as the last time a
# human remembered to run the export by hand. Seven steps, in order:
#   1. cadence      (spawn the next_action rows the rules are owed)   [ORDER 14]
#   2. matcher      (availability x open space searches -> digest)    [ORDER 14]
#   3. exports      (all seven targets, LIVE -> the vault)
#   4. corpus push  (doctrine tier: git -> the Drive/vault render copies) [CORPUS FLIP, 2026-08-06]
#   5. consumers    (renewal-feed, lead-board, deal-room — the boards built FROM
#                    those files; they must follow the export that feeds them)
#   6. graph        (derived from the freshly exported files, so it follows too)
#   7. backup       (encrypted pg_dump -> git)
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
# LAST_STEP_RC carries the outcome of the step that just ran (0 = OK, 78 = SKIP,
# anything else = FAIL). Added 2026-08-07 so the dead-man pings can report on the
# steps they are named after instead of on the mere fact that the chain reached
# the end. See the ping block at the bottom.
LAST_STEP_RC=0
step() {                        # step <label> <command...>
  local label="$1"; shift
  say "START $label"
  LAST_STEP_RC=0
  if "$@" >> "$LOG" 2>&1; then
    say "OK    $label"
  else
    local rc=$?
    LAST_STEP_RC=$rc
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
EXPORTS_RC=$LAST_STEP_RC

# CORPUS FLIP (2026-08-06): the doctrine tier is git-canonical now — corpus/ under this
# repo, not the Drive, is the source of truth. This step pushes whatever changed in git
# out to the Drive/vault/home render copies. It refuses to clobber a source-side hand-edit
# (a CONFLICT, printed by name) rather than silently overwriting it, and exits 78 — SKIP,
# not FAIL, same contract as the credential-gated steps above — when the Drive mount isn't
# up, since that is not a broken night, just an unreachable render target.
step "corpus push (git-canonical doctrine -> vault)" ./.venv/bin/python tools/corpus-sync.py --push

step "consumers (renewal-feed, lead-board, deal-room)" ./run.sh all

# Added 2026-08-07 (loop #204, Joe's ruling): the promotion gate runs nightly so
# the renewal T1 review shortlist is fresh each morning. READ-ONLY plus one
# artifact: it prints the shortlist and writes out/lead-promote/
# renewal-t1-shortlist.json (gitignored; the brief pack's renewal-shortlist
# section reads it). It writes NO lead, NO registry row, and nothing to the
# database — T1 candidates queue for Joe's review, and only his claim at the
# board creates a lead. Runs after the consumers because the renewal feed it
# reads is rebuilt by the step above.
step "lead promote (review shortlist, writes no leads)" ./run.sh lead-promote

step "graph (derived from the exported files)"       ./run.sh graph
step "encrypted backup -> R2"                        ./bin/backup-dump.sh
BACKUP_RC=$LAST_STEP_RC

# Added 2026-08-06 (loop #180): the published Outlook feeds are a ROLLING window
# (~1 month back on current publish settings), so history that scrolls out is
# gone forever and any "look back N months" backfill silently under-covers.
# This step snapshots both partners' feeds into out/calendar-archive/ (dedup by
# content, gitignored — PII stays out of git). It fetches independently of the
# Shortcuts drop files, so archiving survives a dead Shortcut. Exit 78 = SKIP
# when ~/.config/carr/calendar.env is absent, same contract as the other steps.
step "calendar archive (both partners' feeds)"       ./bin/archive-calendar.sh

# Added 2026-08-06 (Joe's go, the Python-native answer to the Rust question,
# loop #218): mypy over the whole repo, lenient config in mypy.ini, 19 legacy
# files grandfathered with self-removing headers. Catches shape mistakes in
# data hand-offs the night they land. A red here is a NEW regression, never
# legacy noise — the baseline was green the day it was wired.
step "type-check tripwire (mypy)"                    ./bin/type-check.sh

# Added 2026-08-07 (Joe's pick: "build the one-page view first"): the combined
# open-items dashboard — every open loop, team row, idea, and action-required
# item on one filterable page, derived read-only from v_export_loops. Runs after
# the exports so it reflects the same night's truth. Output:
# 00_Context/open-items.html (GENERATED — never hand-edited).
step "open-items dashboard (one-page view)"          ./.venv/bin/python generators/build-open-items-dashboard.py

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
#
# CHANGED 2026-08-07, and the reason is the same defect the backup guard had one
# layer down. This step used to run unconditionally, so the exports check and the
# backup check were pinged OK on the strength of the chain REACHING them, never
# on what they did. On 2026-08-07 a 200-byte corrupt backup pinged the backup
# check as healthy. A success signal that does not look at the thing it reports
# on is worse than no signal, because it is believed.
#
# Each check now gets the exit code of the step it is named after. hc-ping.sh
# pings /fail on anything non-zero, so a bad night ALARMS IMMEDIATELY instead of
# waiting out the dead-man grace period — the same treatment the Worker health
# check has had since ORDER 5. Exported rather than prefixed onto the call for
# the zsh scoping reason stated at CARR_EXPORT_LIVE above.
export HC_EXPORTS_RC="$EXPORTS_RC"
export HC_BACKUP_RC="$BACKUP_RC"
step "healthchecks dead-man pings"               ./bin/hc-ping.sh

if [ "$rc_total" -eq 0 ]; then
  say "===== nightly chain OK ====="
else
  say "===== nightly chain FINISHED WITH FAILURES — see above ====="
fi

# Keep the log from growing without bound: last 2000 lines is several months.
tail -n 2000 "$LOG" > "$LOG.trim" && mv "$LOG.trim" "$LOG"
exit "$rc_total"
