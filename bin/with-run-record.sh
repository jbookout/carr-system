#!/bin/zsh
# with-run-record.sh — the ONE wrapper between a recurring launchd job and the
# job-run ledger (Program 4, first work package). Doctrine, quoted verbatim:
#
#   "Job failure becomes a durable failed run, incident, or Work Request. It
#   cannot exist only in stdout or a local log."
#   "Manual Run Now and scheduled execution invoke the same implementation."
#
# Until this existed, eight recurring LaunchAgents (rules-refresh, local-briefs,
# capture-poll, partner-ping, notes-sweep, recordings-purge, cc-version-sentinel,
# calendar-eventkit) wrote only to /tmp or out/*.log — exactly the "stdout or a
# local log" the doctrine line above refuses. bin/nightly.sh already wraps its
# OWN seven steps with the same record_run()/step() pattern this file
# generalizes; this is that pattern, factored out so any launchd job can adopt
# it with one line in its plist, rather than every job re-implementing it.
#
# USAGE
#   with-run-record.sh <service-key> [options] -- <command...>
#
# OPTIONS
#   --heartbeat-interval SECONDS   Record at most one succeeded row per this
#                                  many seconds (state file under out/). Every
#                                  FAILURE is still recorded immediately,
#                                  regardless of the interval — throttling
#                                  exists to cut noise from a healthy job that
#                                  fires every 2-5 minutes, never to hide a
#                                  broken one. Omit for "record every fire"
#                                  (the right default for anything hourly or
#                                  slower).
#   --run-key KEY                  Override the derived run_key. Default:
#                                  launchd.<service-key>.
#   --kind job|check               Default: job.
#   --environment ENV              Default: production.
#
# CONTRACT
#   1. The wrapped command's exit code is ALWAYS this script's own exit code.
#      Recording happens strictly after the command finishes and can never
#      change what gets returned to launchd.
#   2. Recording itself can never hang the wrapped job past a bounded wait
#      (CARR_WITH_RUN_RECORD_TIMEOUT seconds, default 15): if ops-record.py
#      has not returned by then, it is killed and the miss is logged. A ledger
#      write that can stall forever would turn "never block the wrapped job"
#      into a lie the first time the database is unreachable over a slow link.
#   3. A missing or bad DB credential is EX_CONFIG (ops-record.py's own 78),
#      logged loudly to out/with-run-record.log and to this script's own
#      stderr (so it lands in the plist's StandardErrorPath too) — and is
#      never surfaced as a failure of the wrapped job.
#   4. CARR_CORRELATION_ID threads through: an outer caller's exported value
#      wins (so a chain that already has one keeps it); otherwise this
#      invocation mints its own and exports it for the wrapped command, so a
#      wrapped script that itself calls ops-record.py (e.g. partner_ping.py
#      via db-tap.py) joins the same journey rather than starting a second one.
#
# Same code, whether launchd fires it at 2am or a human runs it by hand as
# "Run Now" from the terminal — rule a8c55a47.
set -u

REPO="$(cd "$(dirname "$0")/.." && pwd)"
LOG="$REPO/out/with-run-record.log"
mkdir -p "$REPO/out" 2>/dev/null

usage() {
  print -u2 -- "usage: with-run-record.sh <service-key> [--heartbeat-interval SECONDS] [--run-key KEY] [--kind job|check] [--environment ENV] -- <command...>"
  exit 64
}

[ "$#" -ge 1 ] || usage
case "$1" in -h|--help) usage ;; esac
SERVICE="$1"; shift

HEARTBEAT_INTERVAL=0
RUN_KEY=""
KIND="job"
ENVIRONMENT="production"

while [ "$#" -gt 0 ]; do
  case "$1" in
    --heartbeat-interval) HEARTBEAT_INTERVAL="${2:-0}"; shift 2 ;;
    --run-key)            RUN_KEY="${2:-}"; shift 2 ;;
    --kind)                KIND="${2:-job}"; shift 2 ;;
    --environment)         ENVIRONMENT="${2:-production}"; shift 2 ;;
    --) shift; break ;;
    -h|--help) usage ;;
    *) print -u2 -- "with-run-record.sh: unknown option: $1 (missing --?)"; usage ;;
  esac
done

[ "$#" -ge 1 ] || { print -u2 -- "with-run-record.sh: no command given after --"; usage; }
[ -n "$RUN_KEY" ] || RUN_KEY="launchd.$SERVICE"

STATE_DIR="$REPO/out/with-run-record-state"
mkdir -p "$STATE_DIR" 2>/dev/null
STATE_FILE="$STATE_DIR/$SERVICE.last-success"

# ── correlation: inherit or mint ─────────────────────────────────────────────
if [ -z "${CARR_CORRELATION_ID:-}" ]; then
  export CARR_CORRELATION_ID="$(uuidgen | tr 'A-Z' 'a-z')"
fi

RECORD_PY="${CARR_WITH_RUN_RECORD_PYTHON:-$REPO/.venv/bin/python}"
[ -x "$RECORD_PY" ] || RECORD_PY=python3
RECORD_TIMEOUT="${CARR_WITH_RUN_RECORD_TIMEOUT:-15}"

zmodload zsh/datetime 2>/dev/null
t0="$EPOCHREALTIME"
STARTED_AT="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"

# ── run the actual job, unwrapped in every way that matters ─────────────────
"$@"
rc=$?

t1="$EPOCHREALTIME"
# zsh arithmetic is integer-only; do the subtraction and scale in one float
# expression via printf, since (( )) would truncate to whole seconds.
DURATION_MS="$(printf '%.0f' "$(( (t1 - t0) * 1000 ))")"

record() {                       # record <state> [failure_class]
  local state="$1"; shift
  local fclass_args=()
  local fclass_label="-"
  if [ -n "${1:-}" ]; then
    fclass_args=(--failure-class "$1")
    fclass_label="$1"
  fi
  # WRAPPER-OWNED TRUTH, written before the DB call and independent of it: a
  # human (or a selftest) can always answer "what did this job do" from this
  # log alone, even with no database reachable at all.
  print -r -- "$(date -u '+%Y-%m-%dT%H:%M:%SZ') ATTEMPT service=$SERVICE key=$RUN_KEY state=$state exit_code=$rc duration_ms=$DURATION_MS failure_class=$fclass_label correlation=$CARR_CORRELATION_ID" >> "$LOG"

  "$RECORD_PY" "$REPO/tools/ops-record.py" run \
      --service "$SERVICE" --key "$RUN_KEY" --state "$state" \
      --kind "$KIND" --environment "$ENVIRONMENT" \
      --exit-code "$rc" --started-at "$STARTED_AT" \
      --correlation "$CARR_CORRELATION_ID" \
      --source-ref bin/with-run-record.sh \
      --detail "duration_ms=$DURATION_MS" \
      "${fclass_args[@]}" >> "$LOG" 2>&1 &
  local record_pid=$!

  local waited=0
  while kill -0 "$record_pid" 2>/dev/null && [ "$waited" -lt "$RECORD_TIMEOUT" ]; do
    sleep 1
    waited=$((waited + 1))
  done
  if kill -0 "$record_pid" 2>/dev/null; then
    kill -9 "$record_pid" 2>/dev/null
    wait "$record_pid" 2>/dev/null
    print -r -- "$(date -u '+%Y-%m-%dT%H:%M:%SZ') TIMEOUT ops-record.py did not return within ${RECORD_TIMEOUT}s for $SERVICE/$RUN_KEY — killed; the wrapped job's own exit code ($rc) is unaffected" >> "$LOG"
    print -u2 -- "with-run-record.sh: recording timed out for $SERVICE/$RUN_KEY (see $LOG)"
    return
  fi
  wait "$record_pid"
  local record_rc=$?
  # 78 = EX_CONFIG (ops-record.py's own convention: it ran, found a credential
  # or the ops schema missing, wrote nothing, said so). That is a configuration
  # state, not a failure of THIS wrapper or of the wrapped job — stay quiet on
  # the wrapper's own stderr for it, same as bin/nightly.sh's SKIP handling.
  if [ "$record_rc" -ne 0 ] && [ "$record_rc" -ne 78 ]; then
    print -u2 -- "with-run-record.sh: could not record $SERVICE/$RUN_KEY (ops-record.py exit $record_rc) — see $LOG"
  fi
}

if [ "$rc" -eq 0 ]; then
  should_record=1
  if [ "${HEARTBEAT_INTERVAL:-0}" -gt 0 ] 2>/dev/null && [ -f "$STATE_FILE" ]; then
    last="$(cat "$STATE_FILE" 2>/dev/null || print -r -- 0)"
    now_epoch="$(date -u +%s)"
    elapsed=$(( now_epoch - last ))
    if [ "$elapsed" -lt "$HEARTBEAT_INTERVAL" ]; then
      should_record=0
      print -r -- "$(date -u '+%Y-%m-%dT%H:%M:%SZ') THROTTLE service=$SERVICE key=$RUN_KEY elapsed=${elapsed}s < ${HEARTBEAT_INTERVAL}s — skipping the succeeded row" >> "$LOG"
    fi
  fi
  if [ "$should_record" -eq 1 ]; then
    record succeeded
    date -u +%s > "$STATE_FILE" 2>/dev/null
  fi
else
  record failed "exit_$rc"
  # A recorded failure clears the throttle state, so the recovery — the next
  # succeeded fire — is recorded immediately rather than waiting out a stale
  # interval. A silent recovery is exactly the kind of silence this ledger
  # exists to make visible.
  rm -f "$STATE_FILE" 2>/dev/null
fi

exit $rc
