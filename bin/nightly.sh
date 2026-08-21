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

# Drive projection is never part of the ordinary unattended path.  Recovery is
# an operator act, not an environment default: it must name why noncanonical
# Drive state is needed before any child can receive that route.
RECOVERY=0
RECOVERY_REASON=""

# Control-plane shadow path. Validate the exact versioned chain's executable
# surfaces without loading credentials, taking locks, writing logs, touching
# Drive, or calling production. A canary uses the normal path under the same
# singleton lock as the legacy schedule.
if [ "${1:-}" = "--preflight" ]; then
  required=(
    ops/vault-drift-watch.py bin/schema-snapshot.sh ops/p1-environment-gate.py
    ops/p1-rebuild-gate.py ops/p1-integration-gate.py pipelines/cadence_engine.py
    pipelines/availability_matcher.py ops/fetch-allowlist.py
    ops/cutover-readiness.py ops/codex-hook-smoke.sh tools/corpus-sync.py
    ops/vendor-level-drift-check.py bin/backup-dump.sh bin/archive-calendar.sh
    bin/sync-settings.sh bin/type-check.sh ops/store-markup-scan.py
    generators/build-open-items-dashboard.py ops/nightly-verb-probe.py
    bin/smoke-and-record.sh tools/ops-record.py bin/routine-canonical-seam-refusal.sh
  )
  missing=0
  for path in "${required[@]}"; do
    if [ ! -f "$REPO/$path" ]; then
      print -ru2 -- "nightly preflight missing: $path"
      missing=$((missing+1))
    fi
  done
  [ "$missing" -eq 0 ] || exit 1
  /bin/zsh -n "$REPO/bin/nightly.sh" || exit 1
  print -r -- "nightly preflight: ${#required[@]} chain surfaces present; writes=0"
  exit 0
fi
if [ "$#" -eq 0 ]; then
  unset CARR_VAULT CARR_EXPORT_LIVE
elif [ "$#" -eq 3 ] && [ "$1" = "--recovery" ] && [ "$2" = "--reason" ] && [ -n "$3" ]; then
  RECOVERY=1
  RECOVERY_REASON="$3"
else
  print -ru2 -- "usage: bin/nightly.sh [--preflight|--recovery --reason WHY]"
  exit 64
fi
LOG="$REPO/out/nightly.log"
mkdir -p "$REPO/out"

# Routine work receives only named least-privilege database capabilities.  Do
# not source db.env: it also holds owner/admin credentials and shell syntax.
source "$REPO/bin/routine-credential-env.sh"
carr_clear_routine_db_env
carr_load_routine_db_env CARR_DB_JOBS_URL CARR_DB_EXPORTER_URL CARR_DB_BACKUP_URL || exit $?

# A CAPABILITY THIS MACHINE DOES NOT HOLD IS A SKIP, NEVER A SILENT DEATH.
# The loader exports only the names it FINDS in db.env, so an unprovisioned
# capability leaves its variable unset — and under `set -u` (line 33) the first
# "$CARR_DB_BACKUP_URL" below then kills the whole chain mid-run: no FAIL line,
# no `chain FINISHED` line, nothing in out/nightly.log to find it by. Every step
# after that point simply never happens.
#
# That is not hypothetical. It began on 2026-08-17, when the hardening that
# replaced backup-dump.sh's neonctl owner-credential path with a dedicated
# carr_backup DSN landed in the code while this Mac had no such DSN. By 08-19
# the local backup had aged past the catch-up threshold, which moved the first
# dereference to the TOP of the chain, and three consecutive nights then ran
# zero steps — no cadence, no matcher, no exports, no boards, no graph.
#
# ONE DEFAULT, HERE, rather than a `:-` at each call site. #380 did it inline at
# four sites, which works but leaves nothing to point at when asking "is every
# use of this variable safe?" — the answer has to be re-derived by reading all
# four. Defaulting once makes that a single fact, and lets
# ops/nightly-capability-skip-selftest.py check "every use comes AFTER the
# default" as a property of the file instead of a habit.
#
# bin/backup-dump.sh ALREADY handles the missing credential correctly — it
# exits 78, which this chain reports as SKIP and steps past. Defaulting the
# variable here is what lets that graceful path be reached at all: the callee's
# fallback cannot run if dereferencing the variable kills the caller first.
CARR_DB_BACKUP_URL="${CARR_DB_BACKUP_URL:-}"

say() { print -r -- "$(date -u '+%Y-%m-%dT%H:%M:%SZ')  $*" >> "$LOG"; }

rc_total=0
# LAST_STEP_RC carries the outcome of the step that just ran (0 = OK, 78 = SKIP,
# anything else = FAIL). Added 2026-08-07 so the dead-man pings can report on the
# steps they are named after instead of on the mere fact that the chain reached
# the end. See the ping block at the bottom.
LAST_STEP_RC=0

# ── THE JOB-RUN LEDGER (Program 3, 2026-08-14) ───────────────────────────────
# Until this existed, step() computed exactly the right outcome for every step,
# including the SKIP distinction below that nothing else in the system makes,
# and then threw it into a text file on one Mac. A morning question like "which
# step failed, on which code, and what else broke at the same time" could only
# be answered by a human reading out/nightly.log over somebody's shoulder. That
# is the "terminal archaeology" Program 3's gate is named against.
#
# ONE CORRELATION ID PER NIGHT, exported so every step of one chain — and
# anything a step itself records — threads onto the same journey. `ops-record
# trace <id>` then returns the whole night as one chain.
#
# THE RUN KEY IS DERIVED FROM THE LABEL, up to the first parenthesis, so
# "cadence engine (spawn owed next actions)" is always nightly.cadence-engine.
# The parenthetical is where wording actually churns, so keeping it out of the
# key means a reworded step keeps its history. Renaming the leading words DOES
# start a new key, which is the tradeoff taken deliberately rather than changing
# the signature of every call site in a chain that runs unattended tonight.
#
# RECORDING NEVER FAILS A STEP. The recorder's exit code is ignored on purpose.
# It is also not hidden: a step that goes unrecorded makes this service read
# `unknown` on the next health look rather than staying green, because
# ops.v_service_environment_health derives health from the latest observation
# and its freshness and stores no health anywhere. Silence is visible by design.
LEDGER_OFF=0
record_run() {                  # record_run <label> <state> <rc> <started_at>
  [ "$LEDGER_OFF" -eq 1 ] && return 0
  local key
  # The '(' is escaped because zsh treats an unescaped one in a ${..%%..}
  # pattern as globbing syntax and errors with "bad pattern" — caught by running
  # the expression before shipping it, not by reading it.
  key="nightly.$(print -r -- "${1%% \(*}" | tr 'A-Z ' 'a-z-' | tr -cd 'a-z0-9.-')"
  local fclass=()
  [ "$2" = "failed" ] && fclass=(--failure-class "exit_$3")
  ./.venv/bin/python tools/ops-record.py run \
      --service nightly-record-layer --key "$key" --state "$2" \
      --exit-code "$3" --started-at "$4" --source-ref bin/nightly.sh \
      --detail "$1" "${fclass[@]}" >> "$LOG" 2>&1
  # 78 = the ops schema is not there yet (migration 0115 unapplied on this
  # database). Say it once and stop trying, rather than printing the same line
  # for every step — an error that repeats every night trains people to stop
  # reading the log, which is the failure this chain already learned once.
  if [ $? -eq 78 ]; then
    LEDGER_OFF=1
    say "SKIP  job-run ledger not configured on this database (migration 0115 unapplied) — steps will not be recorded tonight"
  fi
  return 0
}

step() {                        # step <label> <command...>
  local label="$1"; shift
  local t0; t0="$(date -u +%FT%TZ)"
  say "START $label"
  LAST_STEP_RC=0
  if carr_routine_exec "$@" >> "$LOG" 2>&1; then
    say "OK    $label"
    record_run "$label" succeeded 0 "$t0"
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
      record_run "$label" skipped "$rc" "$t0"
    else
      say "FAIL  $label (exit $rc)"
      record_run "$label" failed "$rc" "$t0"
      rc_total=1
    fi
  fi
}

# Keep the exact old projection commands available only behind the recovery
# envelope.  In normal scheduled operation each names the concrete canonical
# seam that still has to exist before the work may resume; none falls back to a
# Drive mount just because it happens to be present on this Mac.
drive_projection() {            # drive_projection <label> <missing-seam> <command...>
  local label="$1" seam="$2"; shift 2
  if [ "$RECOVERY" -eq 1 ]; then
    step "RECOVERY NONCANONICAL $label" "$@"
  else
    step "$label (missing canonical seam)" sh ./bin/routine-canonical-seam-refusal.sh \
      "MISSING_CANONICAL_SEAM: $seam"
  fi
}

say "===== nightly chain begin ====="
if [ "$RECOVERY" -eq 1 ]; then
  say "RECOVERY NONCANONICAL: Drive projections enabled; reason=$RECOVERY_REASON"
  print -ru2 -- "RECOVERY NONCANONICAL: Drive projections enabled; reason=$RECOVERY_REASON"
fi
cd "$REPO" || { say "FATAL cannot cd $REPO"; exit 2; }

# ── ONE CHAIN AT A TIME (2026-08-14) ─────────────────────────────────────────
# Until this existed, `./bin/nightly.sh` while the scheduled run was mid-flight
# gave you two chains against one database and one vault. The measured cost, on
# 2026-08-14: the second run's vault-drift check read the 30 files the first run
# had just re-exported and reported all 30 as TAMPER. Nothing had been tampered
# with — every quarantined diff was one line, the export timestamp. A drift
# alarm that fires 30 times for a self-inflicted reason is an alarm nobody will
# read on the night it means something.
#
# The false alarm is only the cheap symptom. Two chains also race on the export
# ledger, the encrypted backup, the drift baseline each of them rewrites at the
# end, and the consumer rebuilds — and the loser of any of those races writes a
# file that no longer matches the database it was derived from.
#
# A duplicate run is a NO-OP, not a failure: exit 0, before the dead-man pings,
# so a second run cannot ping /fail for a night the first run is handling fine.
# The traps are here at top level and not inside carr_take_lock because zsh runs
# an EXIT trap set inside a function when the FUNCTION returns — see the header
# of bin/run-lock.sh, where that cost the first cut of this fix.
source "$REPO/bin/run-lock.sh"
if ! carr_take_lock nightly >> "$LOG" 2>&1; then
  say "SKIP  chain already running — this invocation is a no-op (see the LOCKED line above)"
  exit 0
fi
trap 'carr_release_lock; exit 143' INT TERM HUP
trap 'carr_release_lock' EXIT

# ONE ID FOR THE WHOLE NIGHT. Exported so every step, and anything a step itself
# records, threads onto the same journey — `tools/ops-record.py trace <id>`
# returns the night as one chain instead of a text file to read by hand.
export CARR_CORRELATION_ID="$(uuidgen | tr 'A-Z' 'a-z')"
say "correlation: $CARR_CORRELATION_ID"

# WHICH CODE RAN. Added 2026-08-07. This chain executes whatever is checked out
# in the working tree, and on 2026-08-07 the checked-out branch moved twice in
# one evening with nobody noticing — once leaving a committed fix stranded off
# main, so the tree ran code that main did not have. Nothing anywhere recorded
# which code a given night ran, which means a morning's log could not answer the
# first question you ask when a night goes wrong: what was actually running.
#
# Three cheap facts, logged before any step. The uncommitted count matters as
# much as the sha: a dirty tree means the night ran something that exists in no
# commit and cannot be recovered by checking one out.
GIT_BRANCH="$(git -C "$REPO" rev-parse --abbrev-ref HEAD 2>/dev/null || echo '?')"
GIT_SHA="$(git -C "$REPO" rev-parse --short HEAD 2>/dev/null || echo '?')"
GIT_DIRTY="$(git -C "$REPO" status --porcelain 2>/dev/null | wc -l | tr -d ' ')"
if [ "${GIT_DIRTY:-0}" -gt 0 ] 2>/dev/null; then
  say "code: branch $GIT_BRANCH @ $GIT_SHA — $GIT_DIRTY uncommitted path(s) in the tree"
else
  say "code: branch $GIT_BRANCH @ $GIT_SHA (tree clean)"
fi

# Export live Drive projections only in the explicitly labeled recovery path.
if [ "$RECOVERY" -eq 1 ]; then
  export CARR_EXPORT_LIVE=1
  export CARR_DRIVE_RECOVERY=1
fi

# ── PHASE 1 v2 (2026-08-13): vault drift watch --check, FIRST THING THIS CHAIN
# DOES. "install that vault drift watch into the nightly rewrite. That way, the
# first thing it does is check all the drive files" — the design brief for
# this step. It compares the vault's registered/generated files against the
# LAST --rebaseline (taken after last night's exports, see the step at the
# bottom of this chain) so a file tampered with outside the export path is
# caught BEFORE tonight's export would otherwise silently overwrite the
# evidence — the exact gap ops/vault-drift-watch.py's v1 (plain manifest diff)
# could not close. It also flags any UNEXPECTED add/modify/delete of a non-
# registry, non-corpus-mirror .md file. A finding here is loud (exit 2, marks
# this step FAIL, quarantines the file + a diff, appends an intake payload to
# out/vault-drift-salvage-manifest.jsonl) but NEVER aborts the chain — exports
# still have to run and heal the file either way, same "does not abort"
# contract every other step in this chain already has via the step() wrapper
# below. Live routing to /ingest ENABLED 2026-08-13 (Joe: "Let's go ahead and
# do it"): posts each salvage payload as source vault_drift via the token in
# ~/.config/carr/ingest.env (CARR_INGEST_TOKEN_VAULT_DRIFT, Joe-pasted per the
# secrets policy, held in the additive INGEST_TOKENS_EXTRA Worker secret).
# Until that token is pasted on both ends, the POST 401s loudly and the local
# quarantine + salvage manifest still capture everything — fail-visible.
# RECOVERY-POINT CATCH-UP, ahead of every step that could hang.
#
# The chain already survives a step that FAILS: `step` records the bad exit and
# keeps going, so a broken export cannot skip the backup. It does NOT survive a
# step that HANGS — the backup is the last step, so anything that blocks earlier
# takes the backup down with it, wake schedule or no.
#
# Joe accepted a 24-hour recovery point objective on 2026-08-13. That tolerance is
# a contract, and a contract that only holds when nothing hangs is not a contract.
# So: if the newest backup is already older than the objective, take one NOW,
# before the chain risks stalling. The end-of-chain backup still runs and is still
# the authoritative post-write snapshot; this only fires when we are ALREADY out
# of contract, which on a healthy night is never.
RPO_HOURS=24
newest_backup="$(ls -t "$REPO"/backups/*.sql.age 2>/dev/null | head -1)"
if [ -z "$newest_backup" ]; then
  say "CATCH-UP  no prior backup found — taking one before the chain begins"
  step "recovery-point catch-up (no prior backup)" env CARR_DB_BACKUP_URL="$CARR_DB_BACKUP_URL" ./bin/backup-dump.sh
else
  backup_age_h=$(( ( $(date +%s) - $(stat -f %m "$newest_backup") ) / 3600 ))
  if [ "$backup_age_h" -ge "$RPO_HOURS" ]; then
    say "CATCH-UP  newest backup is ${backup_age_h}h old, objective is ${RPO_HOURS}h — taking one before the chain begins"
    step "recovery-point catch-up (${backup_age_h}h since last backup)" env CARR_DB_BACKUP_URL="$CARR_DB_BACKUP_URL" ./bin/backup-dump.sh
  else
    say "OK    recovery point intact (newest backup ${backup_age_h}h old, objective ${RPO_HOURS}h)"
  fi
fi

drive_projection "vault drift watch (check, first)" \
  "Drive drift-baseline verifier" \
  env CARR_DRIFT_INGEST=1 ./.venv/bin/python ops/vault-drift-watch.py --check

# SCHEMA SNAPSHOT DRIFT, added 2026-08-13 with the snapshot itself. db/schema.sql
# is now what builds staging AND what CI's migration check applies pending
# migrations on top of. If production's structure moves and the committed file
# does not, both of those keep passing against a shape that no longer exists —
# a green check measuring the wrong database, which is the failure mode this
# whole session kept finding. It runs HERE and not in CI because it needs
# production, and CI cannot reach production by construction.
step "schema snapshot drift (admin capability unavailable)" sh ./bin/routine-admin-refusal.sh "schema snapshot requires separately provisioned admin capability"

# ── PROGRAM 1's GATE, ASKED NIGHTLY (2026-08-15) ─────────────────────────────
# "Staging cannot access Production data or credentials" is G1's gate, and until
# this line it was a sentence in a roadmap and an argument in wrangler.toml. The
# isolation was built on 2026-08-13; nothing has ever asked whether it still
# holds, and nothing would have noticed the day it stopped.
#
# WHAT PROVOKED IT is the same shape as the step above. On 2026-08-15 the
# staging database was found FOUR migrations behind production, including the
# one carrying P0-1's release object. It surfaced when a person went to run an
# acceptance test there and got "relation ops.release does not exist" — six days
# after the drift began. The gate's assertions 5 and 6 are that discovery turned
# into a question the machine asks every night, in both directions.
#
# It runs HERE, not in CI, for the same reason: it needs the two REAL Neon
# projects, and CI reaches neither by construction. It writes to neither, and
# opens production read-only at the session level rather than merely intending
# to. step() records the outcome to the operational ledger, so a night it fails
# is a row rather than a line in a log nobody opens (rule 1f3a7372).
step "environment isolation gate (admin capability unavailable)" sh ./bin/routine-admin-refusal.sh "environment isolation gate requires separately provisioned admin capability"

# ── PROGRAM 1's REBUILD CLAUSE, PROVEN NIGHTLY (2026-08-15) ──────────────────
# "A fresh non-production environment can be reconstructed from repository
# declarations and approved secret references." Until this ran, reconstruction
# had never been attempted once, and the only way to discover a missing piece
# would have been to need it.
#
# It branches STAGING, never production — a Neon branch is a copy-on-write child
# of its parent, so branching production would hand a throwaway database every
# production row. Guard 0 refuses on the production project id before anything
# is created, and the branch is destroyed on every exit path.
#
# The nightly cost is one Neon branch created and deleted. The thing it buys is
# that the repository's sufficiency is a measured fact each morning rather than
# an argument nobody has tested.
step "environment rebuild proof (admin capability unavailable)" sh ./bin/routine-admin-refusal.sh "environment rebuild proof requires separately provisioned admin capability"

# Program 1's INTEGRATION clause, the third use of the rehearsal lane. The
# rebuild gate above proves the SCHEMA stands up; this proves the APPLICATION
# works on what it built — the catalog populates, a real entry point writes, the
# value reads back, and the grant surface matches production's. The distinction
# is not academic: on 2026-08-16 a table whose CREATE had succeeded refused every
# write, which `to_regclass` calls perfect.
step "environment integration proof (admin capability unavailable)" sh ./bin/routine-admin-refusal.sh "environment integration proof requires separately provisioned admin capability"

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

# UPSTREAM CORROBORATE (fixed 2026-08-14): writes Automation/pre-entity-watch.json
# and, in the same run, maps it into candidate_pool via map_radar_lanes' writer-
# side hook (ORDER 26b) — both need CARR_DB_JOBS_URL, same as the two writing
# steps above, so it belongs in this group. Runs before the exports and well
# before "consumers" below, because build-lead-board.py reads
# pre-entity-watch.json as one of its segment feeds — a run after that step
# would leave the board a full night stale. This step had silently never run
# nightly before: it existed only as a hand-run command, which is why
# candidate_pool saw no renewal-radar/upstream writes between 2026-08-01 and
# 2026-08-07.
drive_projection "upstream corroborate (pre-entity-watch + pool mapping)" \
  "record-backed pre-entity-watch document destination" \
  ./run.sh corroborate

# FETCH ALLOWLIST (2026-08-09): regenerate the record-derived practice domains
# the egress guard unions with its KNOWN_HOSTS. Runs AFTER the writing steps so a
# client added tonight is fetchable tomorrow, and BEFORE the exports because it
# reads the same views and a stale list silently costs the weekly research slice
# a whole client. Cheap (one query pair); failing it must never stop the chain,
# and it does not — the guard falls back to KNOWN_HOSTS alone when the file is
# missing, which tightens the gate rather than loosening it.
step "fetch allowlist (client domains -> guard)"     ./.venv/bin/python ops/fetch-allowlist.py

drive_projection "exports (7 targets -> vault)" \
  "record/document projection destination for seven legacy exports" \
  ./run.sh export
EXPORTS_RC=$LAST_STEP_RC

# CUTOVER READINESS (Phase 1, 2026-08-13, August 21 cutover). Right after the
# exports because it checks the compiled-rules renders those exports just
# wrote against an independent store query, plus a LIVE standing-context /
# doctrine-index call through this machine's own local-actor identity. Proves
# nightly, for whichever partner this machine belongs to, that the store-first
# boot path agrees with both the store and the fallback file — so drift shows
# up here before the 21st instead of on it. The OTHER partner's live half
# cannot be proven from this machine on purpose (Phase 1 closed the caller-
# supplied-identity hole in local-verb.mjs); this step correctly reports that
# half PARTIAL rather than failing on it — see ops/cutover-readiness.py.
drive_projection "cutover readiness (store-first boot predicate)" \
  "record-native compiled-rule render verifier" \
  ./.venv/bin/python ops/cutover-readiness.py

# CODEX HOOK SMOKE (2026-08-14). Live negative proof that Codex's PreToolUse
# hooks — this repo's OWN gate code, including guard-unattended.py — actually
# fire on an unattended `codex exec` run, not merely that the config file
# matches the tracked contract. Verified live the same day: plain `codex
# exec` SILENTLY SKIPS every hook (no denial, no warning) unless it carries
# --dangerously-bypass-hook-trust, because Codex requires persisted hook
# trust granted only interactively, which an unattended run never has. That
# flag is now on every sanctioned invocation site (bin/council-lib.sh,
# pipelines/run_codex_review.py); this step is the ongoing check that it
# keeps working, so a Codex CLI upgrade or config change that silently
# reopens the gap gets caught here rather than in production.
# tools/health-check.py reads this step's result record
# (out/codex-hook-smoke.json) and goes red if it is ever missing or older
# than 30 days — SKIP (78) on a machine with no Codex CLI installed, not a
# nightly alarm nobody can fix.
step "codex hook smoke (live negative PreToolUse proof)" ./ops/codex-hook-smoke.sh

# CORPUS FLIP (2026-08-06): the doctrine tier is git-canonical now — corpus/ under this
# repo, not the Drive, is the source of truth. This step pushes whatever changed in git
# out to the Drive/vault/home render copies. It refuses to clobber a source-side hand-edit
# (a CONFLICT, printed by name) rather than silently overwriting it, and exits 78 — SKIP,
# not FAIL, same contract as the credential-gated steps above — when the Drive mount isn't
# up, since that is not a broken night, just an unreachable render target.
drive_projection "corpus push (git-canonical doctrine -> vault)" \
  "canonical doctrine document delivery destination" \
  ./.venv/bin/python tools/corpus-sync.py --push

drive_projection "consumers (renewal-feed, lead-board, deal-room)" \
  "record-backed board document destinations" \
  ./run.sh all

# Added 2026-08-07 (loop #204, Joe's ruling): the promotion gate runs nightly so
# the renewal T1 review shortlist is fresh each morning. READ-ONLY plus one
# artifact: it prints the shortlist and writes out/lead-promote/
# renewal-t1-shortlist.json (gitignored; the brief pack's renewal-shortlist
# section reads it). It writes NO lead, NO registry row, and nothing to the
# database — T1 candidates queue for Joe's review, and only his claim at the
# board creates a lead. Runs after the consumers because the renewal feed it
# reads is rebuilt by the step above.
drive_projection "lead promote (review shortlist, writes no leads)" \
  "record-native renewal feed input" \
  ./run.sh lead-promote

drive_projection "graph (derived from the exported files)" \
  "record/document graph input" \
  ./run.sh graph

# Added 2026-08-13 (Phase 1): the retrieval index and the system graph were
# invoked by NOTHING — not this chain, not any launchd plist, not brief_pack.py,
# not local-briefs.sh, not crontab — so `run.sh retrieve` and Graph-System were
# only ever as fresh as the last time a human remembered to run them by hand.
# Both read the freshly EXPORTED vault files (section-index walks the vault
# tree; the system graph's folder-to-folder edges come from file text) AND the
# doctrine STORE (both take the lib/record_sources store pass this Mac's venv
# can reach), so they must run AFTER the exports step and AFTER the corpus push
# above (git-canonical doctrine -> vault) — running earlier would index/graph
# yesterday's vault content. Placed here, late in the chain and right after the
# Obsidian graph render, rather than any earlier: they also want the record
# layer to have finished its own writes and reads for the night (cadence,
# matcher, cutover readiness, consumers), and there is nothing downstream of
# them in-chain that reads their output before the backup, so their exact slot
# among the late steps is not load-bearing — only "after exports+corpus push,
# before backup" is. Same $PY venv convention as every other DB-touching step
# (run.sh's own graph_system/section_index functions already use $PY, not
# plain python3, for the same psycopg reason as ORDER 29a).
drive_projection "section index (retrieval-as-code layer)" \
  "record-native section-index source" \
  ./run.sh section-index
drive_projection "system graph (Graph-System/, derived)" \
  "record-native system-graph source" \
  ./run.sh graph-system

# Added 2026-08-15 (rule faf1b643). Joe defined the vendor relationship levels by
# COUNTABLE EVENTS so they stop being impressions — "you can email fifty people
# and have fifty relationships that do not exist" — and the rule names its own
# mechanism, v_vendor_level_suggestion. That view has existed since migration
# 0052 and NOTHING has ever read it: a finding that only lives in a record has
# not reached the partner (rule d8c9b1f0). This step reads it and prints the
# disagreements. It changes no level: the level stays a human judgment, stored
# and not computed, because a relationship can matter for reasons no event count
# can see. Exits 0 whether or not it finds drift, and 78 = SKIP with no
# credential, same contract as the other steps.
step "vendor level drift (reports, never changes a level)" ./.venv/bin/python ops/vendor-level-drift-check.py

step "encrypted backup -> R2"                        env CARR_DB_BACKUP_URL="$CARR_DB_BACKUP_URL" ./bin/backup-dump.sh
# The portability mirror (Joe's ruling 2026-08-08): the readable escape hatch —
# md per doctrine doc + CSV per table, Drive + local disk, wholesale overwrite.
# NO GUARD HERE. #391 wrapped this in `if [ -n "$CARR_DB_BACKUP_URL" ]` because
# an empty DATABASE_URL used to make doctrine_mirror.py exit 2, which reads as
# FAIL. #387 fixed that in the callee on the same day: the mirror now returns
# 78 on an absent credential, the house SKIP contract, exactly as
# bin/backup-dump.sh already did. Both landed within an hour of each other and
# nobody noticed they overlapped.
#
# The callee is the right home for it. A guard here protects one caller; 78
# protects every caller, including anyone who runs the mirror by hand. Keeping
# both meant two places to read before you could answer what happens when the
# credential is missing, and the shell branch would go stale the moment the
# exit code changed again. ops/nightly-capability-skip-selftest.py asserts the
# behaviour against the mirror itself rather than against this line.
drive_projection "portability mirror (md+csv, 2 locations)" \
  "canonical portability document destination" \
  env DATABASE_URL="$CARR_DB_BACKUP_URL" .venv/bin/python pipelines/doctrine_mirror.py --out "/Users/booko/Library/CloudStorage/GoogleDrive-joe.bookout.carr.us@gmail.com/My Drive/CARR AI/Backups/portability-mirror" --also "$HOME/carr-system/out/mirror"
BACKUP_RC=$LAST_STEP_RC

# Added 2026-08-06 (loop #180): the published Outlook feeds are a ROLLING window
# (~1 month back on current publish settings), so history that scrolls out is
# gone forever and any "look back N months" backfill silently under-covers.
# This step snapshots both partners' feeds into out/calendar-archive/ (dedup by
# content, gitignored — PII stays out of git). It fetches independently of the
# Shortcuts drop files, so archiving survives a dead Shortcut. Exit 78 = SKIP
# when ~/.config/carr/calendar.env is absent, same contract as the other steps.
step "calendar archive (both partners' feeds)"       ./bin/archive-calendar.sh

# Added 2026-08-12 (Joe's go, "put settings in the repo"): mirror the Claude Code
# permission surface — the three settings.json files carrying the allow list, the
# hook wiring, and the autoMode clearances — into claude-tree/settings/. Those
# files live in Google Drive and were tracked NOWHERE, so on 2026-08-08 a plugin
# install could delete an entire hooks block and leave five gates off for a day
# with no diff to find it by. Runs --apply on purpose: the mirror's job is
# HISTORY, and the diff surfaces in this chain's own backup commit rather than by
# failing the chain every time Joe approves a permission interactively. The
# printed diff lands in nightly.log either way. Direction is Drive -> repo (the
# opposite of sync-skills.sh) because Claude Code itself writes these files.
drive_projection "settings mirror (permission surface -> git)" \
  "repo-owned settings authority read route" \
  ./bin/sync-settings.sh --apply

# Added 2026-08-06 (Joe's go, the Python-native answer to the Rust question,
# loop #218): mypy over the whole repo, lenient config in mypy.ini. The legacy
# files that were grandfathered at wiring time carried self-removing headers and
# are all gone (verified 2026-08-14), so the sweep now runs with no exemptions.
# Catches shape mistakes in data hand-offs the night they land. A red here is a
# NEW regression, never legacy noise — the baseline was green the day it was
# wired, and there is no longer any legacy bucket for a red to hide in.
step "type-check tripwire (mypy)"                    ./bin/type-check.sh

# Added 2026-08-13. The rules half of this defect is caught hourly by
# ops/rule-render-markup-check.py on the refresh; this is the records half. The
# same malformed-parameter write path serves every verb that takes prose, and
# rule c53beeaa names loop #159's absorbed `unblocks` as a known instance — so
# the record side was known to be affected and had never once been swept. A
# scanner nobody runs is not a scanner, which is precisely how c53beeaa's own
# prescribed check came to sit unexecuted from 2026-08-03 to 2026-08-13.
# Read-only (exporter role) and reports only; repair needs the absorbed text read
# first and each parameter returned to its own field, per row.
step "tool-call markup sweep (records)"              ./.venv/bin/python ops/store-markup-scan.py

# Added 2026-08-07 (Joe's pick: "build the one-page view first"): the combined
# open-items dashboard — every open loop, team row, idea, and action-required
# item on one filterable page, derived read-only from v_export_loops. Runs after
# the exports so it reflects the same night's truth. Output:
# 00_Context/open-items.html (GENERATED — never hand-edited).
drive_projection "open-items dashboard (one-page view)" \
  "open-items dashboard document destination" \
  ./.venv/bin/python generators/build-open-items-dashboard.py --recovery --reason "$RECOVERY_REASON"

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

# ── THE FULL VERB SUITE IS BACK IN THE CHAIN, 2026-08-14 (Program 3) ──────────
# The paragraph above is now HISTORY rather than current instruction, and is kept
# because the reasoning is the load-bearing part. What changed: the credential
# whose retirement forced the 2026-08-04 removal has been replaced by a narrower
# one (PROBE_TOKENS -> a locked 'probe' profile, loop #192), it is provisioned,
# and the suite was verified honestly green — 33 passed, 0 failed, three
# profile-locked SKIPs — BEFORE this line was added. All three failures found on
# the way there were fixture drift, not regressions; the system answered
# correctly in every case. See bin/smoke-and-record.sh for the whole account.
#
# It runs AFTER the verb probe rather than instead of it. The two cover different
# failures and the cheap one should not be gated behind the expensive one: the
# probe needs no credential and sweeps all 40 views, while this suite needs the
# probe token and covers transport, dispatch and ANSWER CORRECTNESS — the class
# where a verb responds 200 with a wrong answer, which no view sweep can see.
#
# A MISSING TOKEN IS A SKIP, A REFUSED TOKEN IS AN ALARM. smoke-and-record.sh
# maps "no token configured" to 78 so an absent credential cannot alarm every
# night until someone pastes one — that pattern is how this suite was lost the
# first time. A token that EXISTS and is refused stays a failure, because it
# means post-deploy verification has silently stopped happening.
step "golden workflow suite (read verbs, answer correctness)" ./bin/smoke-and-record.sh

# PHASE 1 v2 (2026-08-13): vault drift watch --rebaseline, LAST functional step
# — after every export and every other step that touches a vault or repo file,
# so the snapshot it writes is the true post-chain state. This is what
# tomorrow's first-thing --check (top of this file) compares against: a
# registered file whose hash differs from tonight's rebaseline was touched
# OUTSIDE this chain, which is exactly the "system rewrites every file, so
# tamper would never be caught" gap this watch exists to close. Always exit 0
# (it snapshots, it does not judge) unless the vault root itself is
# unreachable.
drive_projection "vault drift watch (rebaseline, last)" \
  "Drive drift-baseline verifier" \
  ./.venv/bin/python ops/vault-drift-watch.py --rebaseline

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
# The WHOLE chain's outcome, added 2026-08-10. rc_total is final here: every step
# except this one has run. Without it, only the three named steps are watched,
# and any other failing step alarms nowhere — which is how the mypy tripwire
# stayed red from 08-08 to 08-10 with every dead-man check reporting healthy.
export HC_CHAIN_RC="$rc_total"
step "healthchecks dead-man pings"               ./bin/hc-ping.sh

# ── TURN TONIGHT'S FAILURES INTO INCIDENTS (Program 3, 2026-08-14) ───────────
# LAST, and after the pings on purpose. Every step above has now recorded its
# outcome to the job-run ledger, so this sees the whole night rather than a
# prefix of it. The pings stay ahead of it because a dead-man alarm must not wait
# on anything that touches the database.
#
# WHY IT IS A STEP AND NOT A TAIL COMMAND: a step() call records ITSELF to the
# ledger, so an assess that starts failing is visible the same way every other
# step is. An assessment nobody assesses is the blind spot this whole program
# exists to remove.
#
# It opens an incident for each job whose LATEST run failed, appends a fact to
# one that is already open rather than raising a second, and moves an incident to
# monitoring when its job starts passing again. It cannot close one — the grant
# withholds resolved_at, so only a human declares an incident resolved (0117).
step "incident assessment (latest run of every job)"  ./.venv/bin/python tools/ops-record.py assess --environment production

# THE OTHER HALF OF THAT SENTENCE. Every incident assess opens carries the line
# "watch until 24h clear, then close with an outcome", and until 2026-08-14
# nothing performed the close: assess only moves a recovered incident INTO
# monitoring, and no job, agent or service entry called a close path, because
# none existed. The windows expired and the pile stayed, reprinted in full every
# night — a list that can only grow teaches people to stop reading it, the same
# way a check that goes red on normal work does.
#
# WHAT THIS DOES NOT DO. It does not close on one green run, which is the thing
# 0117's grant was written to prevent. It closes only what has nothing left to
# decide: recovered against real evidence, window fully elapsed, and NO failure
# recorded against that same service/environment/run_key for the whole window.
# Anything still flapping, never recovered, or missing evidence stays open and
# keeps its human outcome — that is what `ops-record.py resolve` is for.
#
# WHY BREAK-GLASS. carr_jobs cannot write resolved_at or root_cause at all
# (0117's column-scoped grant), so this needs the owner credential, and db-tap
# holds every write behind default_transaction_read_only unless break-glass is
# set. The reason is stated here rather than typed fresh each night, and every
# run appends to out/break-glass-receipts.log, so the escalation is auditable
# instead of invisible.
step "incident sweep (admin capability unavailable)" sh ./bin/routine-admin-refusal.sh "incident closure requires separately provisioned authority capability"

if [ "$rc_total" -eq 0 ]; then
  say "===== nightly chain OK ====="
  # The dispatcher captures stdout as immutable command evidence.  Keep this
  # concise, emitted only after the chain has completed, and independent of
  # the human-readable log line above.
  print -r -- "nightly result: chain_ok"
else
  say "===== nightly chain FINISHED WITH FAILURES — see above ====="
  print -r -- "nightly result: chain_failed"
fi

# Keep the log from growing without bound: last 2000 lines is several months.
tail -n 2000 "$LOG" > "$LOG.trim" && mv "$LOG.trim" "$LOG"
exit "$rc_total"
