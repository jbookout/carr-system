#!/bin/zsh
# nightly-exports-retry.sh — daytime safety net for the nightly "exports (6
# targets -> OneDrive)" step.
#
# DEFECT THIS ANSWERS. The nightly export failed 9/22, 9/23 and 9/24 around
# 07:05 UTC (~2am local); on 9/24 it hung the full 1800s step wall-clock (see
# out/nightly-runs/nightly-20260924T080221Z.log). A by-hand run the same day
# at ~4pm (`env CARR_EXPORT_LIVE=1 ./run.sh export`) published all 6 targets
# in 18s. Jev at 0.71: the OneDrive File Provider idling overnight. The
# nightly chain now also runs the exports step under `caffeinate -i -s` and
# ahead of a bounded pre-publish wake (ops/onedrive-prepublish-wake.py) — see
# bin/nightly.sh — but the 2am window is inherently the worst time to reach a
# provider that only wakes on real activity. Working hours are the proven
# fallback: re-run the export once, during the day, ONLY if the night's own
# export did not land clean.
#
# THIS IS A RETRY, NOT A SECOND SCHEDULE. It changes nothing about what gets
# built or how a target is validated/published/read back — exporters/common.py
# owns that, unchanged. It only decides WHETHER to call `./run.sh export`
# again and records the truth of what happened when it does.
#
# WHY A launchd StartCalendarInterval JOB, NOT StartInterval. StartInterval
# jobs have been observed to never fire on this Mac (see CLAUDE.md /
# ops/launchd's other jobs); a fixed daily time is the reliable primitive.
#
# WHY NOT A SECOND IMPLEMENTATION OF THE NIGHTLY CHAIN (rule a8c55a47: a
# manual path and an automated path doing the same job must be the same
# code). This calls the exact same `./run.sh export` the nightly chain calls
# and the exact same credential loader, lock and step-timeout machinery —
# just once, gated on the night's own recorded outcome, rather than seven
# other steps around it.
#
# Run by hand any time: ./bin/nightly-exports-retry.sh
set -u

REPO="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="/opt/homebrew/opt/node@22/bin:/opt/homebrew/bin:/opt/homebrew/opt/libpq/bin:/usr/local/bin:/usr/bin:/bin"
cd "$REPO" || { print -ru2 -- "nightly-exports-retry: cannot cd $REPO"; exit 2; }

LOG="$REPO/out/nightly.log"
mkdir -p "$REPO/out"
say() { print -r -- "$(date -u '+%Y-%m-%dT%H:%M:%SZ')  RETRY  $*" >> "$LOG"; }

TODAY_UTC="$(date -u '+%Y%m%d')"
MARKER="$REPO/out/.nightly-exports-retry-$TODAY_UTC.done"

# ── LOAD THE LEDGER CREDENTIAL EARLY, BEFORE ANY SKIP BRANCH ─────────────────
# CARR_DB_EXPORTER_URL (loaded further down, gating the retry attempt itself)
# is a different credential from the one tools/ops-record.py's `run` needs —
# that write authenticates as carr_jobs, via CARR_DB_JOBS_URL (see its own
# CREDENTIALS note). Loading it here, unconditionally and non-fatally, is what
# lets EVERY exit path below record a heartbeat, including the common ones
# that return before the exporter credential is ever touched. An absent
# CARR_DB_JOBS_URL does not gate anything here: record() below already never
# fails its caller (same contract as bin/nightly.sh's own record_run()).
source "$REPO/bin/routine-credential-env.sh"
carr_clear_routine_db_env
carr_load_routine_db_env CARR_DB_JOBS_URL || true

# ── THE LEDGER HEARTBEAT, ON EVERY EXIT PATH ─────────────────────────────────
# ops/config/services.json registers THIS job — "nightly-exports-daytime-retry",
# its own service key, not nightly-record-layer's — with a cadence of one fire
# a day. launchd DOES fire this script daily even on the (common) night the
# retry turns out to be unnecessary, but a job that only writes ops.run when it
# actually retries would go quiet on every healthy night and read permanently
# STALE/unknown against that cadence — the exact "an alarm nobody reads because
# it fires when nothing is wrong" shape this codebase has already relearned
# more than once (see the ORDER 2 addendum in bin/nightly.sh). So every exit
# path below records a row, `skipped` for the SKIP branches and
# `succeeded`/`failed` for an actual attempt, all under this job's OWN service
# key so its cadence fields describe something that is really being observed.
# record <state> <rc> <detail> [--started-at TS] [--failure-class X ...]
record() {
  local state="$1" rc="$2" detail="$3"; shift 3
  local started="$(date -u +%FT%TZ)"
  if [ "${1:-}" = "--started-at" ]; then started="$2"; shift 2; fi
  ./.venv/bin/python "$REPO/tools/ops-record.py" run \
      --service nightly-exports-daytime-retry --key nightly.exports-daytime-retry \
      --state "$state" --exit-code "$rc" --started-at "$started" \
      --source-ref bin/nightly-exports-retry.sh --detail "$detail" "$@" >> "$LOG" 2>&1
}

# ── ONE RETRY A DAY, ON PURPOSE ──────────────────────────────────────────────
# The plist fires this at one fixed daytime hour. The marker keeps a second,
# hand-run invocation the same day from spending a second publish for no
# reason once today's retry has already run (success or failure — a retry
# that already failed is a fact for a human to read, not a reason to spin).
if [ -f "$MARKER" ]; then
  say "SKIP  already attempted the daytime retry today ($MARKER exists)"
  record skipped 0 "already attempted today"
  exit 0
fi

# ── DID TONIGHT'S EXPORT ALREADY LAND CLEAN? ─────────────────────────────────
# bin/nightly.sh's own step() already wrote the authoritative line for tonight
# into the per-run archive under out/nightly-runs/. Read that back rather than
# re-deriving the answer: the archive is what "OK", "FAIL", "TIMEOUT",
# "BLOCKED" and "SKIP" already mean there, and re-deciding it here would be a
# second place for that judgment to drift from the chain that made it.
runlog_dir="$REPO/out/nightly-runs"
latest=""
if [ -d "$runlog_dir" ]; then
  # The (N) glob qualifier is NULL_GLOB for this one expansion only: without it
  # zsh's own glob expansion errors "no matches found" on an empty archive dir
  # (a fresh worktree, or a Mac before its first nightly run) BEFORE `ls` ever
  # runs, so redirecting `ls`'s stderr cannot silence it.
  latest="$(ls -1t "$runlog_dir"/nightly-*.log(N) 2>/dev/null | head -1)"
fi

exports_line=""
latest_is_today=0
if [ -n "$latest" ]; then
  case "$(basename "$latest")" in
    nightly-"$TODAY_UTC"T*) latest_is_today=1 ;;
  esac
  # step()'s say() pads each status word differently (OK gets 4 spaces, FAIL/
  # TIMEOUT/BLOCKED/SKIP get 2) so the label columns line up — matching one
  # fixed gap would silently miss every status but the one it was tested
  # against.
  exports_line="$(grep -E '(OK|FAIL|TIMEOUT|BLOCKED|SKIP)[[:space:]]+exports \(6 targets -> OneDrive\)' "$latest" 2>/dev/null | tail -1)"
fi

if [ "$latest_is_today" -eq 0 ]; then
  say "SKIP  no nightly run archived for today ($TODAY_UTC UTC) yet — nothing to retry against"
  record skipped 0 "no nightly run archived for today yet"
  exit 0
fi

if print -r -- "$exports_line" | grep -qE '^\S+[[:space:]]+OK[[:space:]]+exports '; then
  say "SKIP  tonight's exports step already landed OK ($latest) — no retry needed"
  record skipped 0 "tonight's exports step already landed OK"
  exit 0
fi

if [ -z "$exports_line" ]; then
  say "RETRY tonight's archive ($latest) has no exports outcome line at all — treating as a failure and retrying"
else
  say "RETRY tonight's exports step did not report OK ($latest): $exports_line"
fi

# ── ONE CHAIN'S WORTH OF MUTUAL EXCLUSION, SAME LOCK AS THE NIGHTLY CHAIN ────
# Reusing the "nightly" lock (not a lock of this script's own) means a retry
# can never run concurrently with the nightly chain itself against the same
# OneDrive files and the same export ledger — the exact race bin/run-lock.sh
# was built to close. If the nightly chain (or a still-running earlier retry)
# holds it, this is a no-op, same as a duplicate nightly invocation.
CHAIN_OUTCOME=incomplete
LOCK_HELD=0
carr_retry_exit() {
  local rc="${1:-0}"
  [ "$LOCK_HELD" -eq 1 ] && carr_release_lock
  return 0
}
trap 'carr_retry_exit $?; exit 143' INT TERM HUP
trap 'carr_retry_exit $?' EXIT

source "$REPO/bin/run-lock.sh"
if ! carr_take_lock nightly >> "$LOG" 2>&1; then
  say "SKIP  nightly lock held elsewhere — this retry is a no-op (see the LOCKED line above)"
  record skipped 0 "nightly lock held elsewhere"
  exit 0
fi
LOCK_HELD=1

# NOT carr_clear_routine_db_env here: that would unset the CARR_DB_JOBS_URL
# already loaded above for record(), and this call only ADDS a name to load,
# it does not need a clean slate. carr_load_routine_db_env's own contract is
# "export whatever of these names the file has"; a name already exported by
# an earlier call is simply exported again with the same value.
carr_load_routine_db_env CARR_DB_JOBS_URL CARR_DB_EXPORTER_URL || true
if [ -z "${CARR_DB_EXPORTER_URL:-}" ]; then
  say "SKIP  no exporter credential provisioned on this machine"
  record skipped 0 "no exporter credential provisioned on this machine"
  exit 0
fi

source "$REPO/bin/step-timeout.zsh"
# carr_step_timeout_prefix returns WORDS (CARR_STEP_TIMEOUT_ARGV), never a
# function call: carr_routine_exec ends in `env -i ... "$@"`, and `env`
# execs a real program, not a zsh function — carr_step_with_timeout itself
# would silently fail to run under env -i. Same caveat bin/nightly.sh's own
# step() already works around; see this function's docstring.
carr_step_timeout_prefix "$(carr_step_timeout_for exports)"
t0="$(date -u +%FT%TZ)"
say "START exports (daytime retry, caffeinate -i -s, exports timeout budget)"
if carr_routine_exec "${CARR_STEP_TIMEOUT_ARGV[@]}" \
    caffeinate -i -s env CARR_EXPORT_LIVE=1 ./run.sh export >> "$LOG" 2>&1; then
  rc=0
  say "OK    exports (daytime retry)"
else
  rc=$?
  say "FAIL  exports (daytime retry) (exit $rc)"
fi

# THE TRUTH LIVES IN THE SAME LEDGER THE NIGHTLY CHAIN WRITES TO (rule
# 1f3a7372), under its own service+run key so a retry is never mistaken for
# the night's own run, and record-run's own honesty rules apply unchanged:
# this reports exactly the exit code ./run.sh export returned, which is
# itself driven by exporters/common.py's real publish + read-back
# verification, not by anything decided here.
state="succeeded"; fclass=()
if [ "$rc" -ne 0 ]; then
  state="failed"; fclass=(--failure-class "exit_$rc")
fi
record "$state" "$rc" "daytime retry of the nightly exports step" --started-at "$t0" "${fclass[@]}"

touch "$MARKER"
exit "$rc"
