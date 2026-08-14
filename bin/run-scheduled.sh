#!/bin/zsh
# run-scheduled.sh — make a launchd job's outcome durable, without touching the
# job. Program 4's first slice.
#
#   usage: bin/run-scheduled.sh [--heartbeat-interval SECONDS]
#                                [--also-heartbeat SERVICE]
#                                <service-key> <run-key> <command> [args...]
#
# WHY THIS EXISTS, measured 2026-08-14. `tools/ops-record.py health` read 21 of
# its 25 registered service/environment rows at "last seen never" — only
# nightly-record-layer and social-batch-weekly were healthy at all. Almost none
# of those 21 was down. They run on schedule and always have; nothing was
# listening. Only bin/nightly.sh and bin/smoke-and-record.sh ever called
# `ops-record run`, so rules-refresh, partner-ping, capture-poll, local-briefs,
# notes-sweep, recordings-purge and cc-version-sentinel reported nothing, ever.
# A failure in any of them was durable NOWHERE: it lived in a launchd log on one
# Mac and nothing turned it into an incident. Program 4's gate is "forced job
# failure is durable and actionable", and for those seven it was neither.
#
# THIS WRAPPER CLOSES THE LAUNCHD SEVEN. The rest of the 21 are the ~13
# Claude Code scheduled tasks, which are prompts rather than scripts and need a
# Stop hook instead of a wrapper — lib/scheduled_run.py and
# bin/record-scheduled-run.py are that path, already merged; only their hook
# wiring is still loose (team loop T75) — plus two staging rows nothing observes
# yet. Naming what this does NOT cover is the point: 7 of 21 fixed is the honest
# claim, and a wrapper that quietly implied 21 would be worse than none.
#
# WHY A WRAPPER AND NOT SEVEN EDITS. The seven are zsh, sh and python, written
# by different hands over months. Teaching each to record would make seven
# copies of one decision and guarantee they drift — rule a8c55a47 pointed the
# other way. One implementation; the plist is the only per-job change.
#
# ── THE PROPERTY THIS FILE MUST HOLD ─────────────────────────────────────────
# THE WRAPPER IS TRANSPARENT. It never changes what the job does, what the job
# prints, or what the job's exit code says. An observer that can turn a passing
# job red is worse than no observation, because it puts itself in the failure
# path of the thing it watches. Concretely, and each one is a check in
# ops/run-scheduled-selftest.py:
#   * the child's exit code is returned verbatim, signals included;
#   * the child's stdout and stderr are never captured, filtered or reordered —
#     they go straight to whatever launchd's StandardOutPath already pointed at;
#   * the child keeps the caller's cwd, because none of the seven plists sets
#     WorkingDirectory and every relative path in those scripts resolves against
#     it today;
#   * the child's arguments are passed through unsplit (notes-sweep's
#     `--scheduled` flag is what confines it to weekday business hours; drop it
#     and the job runs at 3am);
#   * the recorder's own output and the recorder's own failures never reach the
#     job's log or the job's exit code.
#
# RECORDING NEVER FAILS A JOB, and is never hidden either. If the recorder
# cannot reach the database the line still lands in out/run-scheduled.log with a
# non-zero recorder_exit, and the service then reads `unknown` at the next
# health look rather than staying green — ops.v_service_environment_health
# derives health from the latest observation and its freshness and stores no
# health anywhere. Silence is visible by design; that is Program 3's load-
# bearing decision and this file leans on it rather than working around it.
#
# THE PROVENANCE LINE IS THE TESTED SURFACE. Every run appends exactly one line
# to out/run-scheduled.log carrying the state this script derived and the exact
# recorder argv it built. ops/run-scheduled-selftest.py asserts against that
# line and nothing else — no injectable recorder, no dry-run flag, no mock. On
# 2026-08-14 the settings-change gate shipped two defects and both were the same
# shape: a test that exercised a path production never takes (team loop T75).
# The line the suite reads is the line the 02:05 run writes.
#
# ── THROTTLING FOR HIGH-FREQUENCY JOBS (Program 4 follow-up) ─────────────────
# partner-ping wakes every 2 minutes and capture-poll every 5; recording every
# fire would be ~1000 rows/day of noise from two channels that are usually
# fine. Both flags below are entirely optional and inert unless a caller
# passes them, so every existing invocation — all seven current plists — is
# untouched byte-for-byte.
#
#   --heartbeat-interval SECONDS   Record a SUCCEEDED row for this job's own
#                                  key at most once per this many seconds (a
#                                  state file under out/run-scheduled-state/).
#                                  Every non-succeeded outcome — failed,
#                                  skipped, timed_out, cancelled — is still
#                                  recorded immediately and CLEARS the
#                                  throttle, so a recovery posts on the very
#                                  next fire instead of waiting out a stale
#                                  interval. Omitted (the default): record
#                                  every fire, which is every existing job's
#                                  actual behavior today.
#   --also-heartbeat SERVICE       On the SAME wake, also record an
#                                  independent SUCCEEDED row for a second,
#                                  unrelated service — riding this job's cron
#                                  slot as the cheapest true signal that the
#                                  Mac is awake and launchd is actually firing
#                                  agents (PROP-010's local edge node).
#                                  Subject to the SAME --heartbeat-interval,
#                                  tracked independently of the primary job.
#                                  Always its own SECOND provenance line
#                                  (key=launchd.heartbeat), so it is asserted
#                                  on exactly the way the primary line is —
#                                  the "provenance line is the tested surface"
#                                  property above extends to it unchanged.
#
# Neither flag touches the job itself: the child still runs exactly once,
# unmodified, and nothing here can change what it does, prints, or returns —
# only what this script decides to WRITE afterward.

set -u

EX_USAGE=64

HEARTBEAT_INTERVAL=0
ALSO_HEARTBEAT=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --heartbeat-interval)
      if [ "$#" -lt 2 ]; then
        print -ru2 -- "usage: --heartbeat-interval requires a value"
        exit $EX_USAGE
      fi
      HEARTBEAT_INTERVAL="$2"; shift 2 ;;
    --also-heartbeat)
      if [ "$#" -lt 2 ]; then
        print -ru2 -- "usage: --also-heartbeat requires a value"
        exit $EX_USAGE
      fi
      ALSO_HEARTBEAT="$2"; shift 2 ;;
    --) shift; break ;;
    *) break ;;
  esac
done

if [ $# -lt 3 ]; then
  print -ru2 -- "usage: bin/run-scheduled.sh [--heartbeat-interval SECONDS] [--also-heartbeat SERVICE] <service-key> <run-key> <command> [args...]"
  print -ru2 -- "  e.g. bin/run-scheduled.sh nightly-record-layer rules.refresh /bin/zsh {{REPO}}/bin/refresh-rules.sh"
  exit $EX_USAGE
fi

SERVICE="$1"; shift
RUN_KEY="$1"; shift

REPO="${0:A:h:h}"
LOG="$REPO/out/run-scheduled.log"
PY="$REPO/.venv/bin/python"
[ -x "$PY" ] || PY=python3

STARTED="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"

# ── the job itself ───────────────────────────────────────────────────────────
# No redirection of any kind. Whatever the caller gave this process for stdout
# and stderr is what the child gets, which is how the existing per-job launchd
# logs keep working unchanged.
"$@"
rc=$?

# ── what that exit code MEANS ────────────────────────────────────────────────
# 78 = EX_CONFIG: the job ran, found a credential or setting it needs is absent,
# wrote nothing and said so. That is a SKIP, not a failed night — the same
# convention bin/nightly.sh and bin/smoke-and-record.sh already hold, and it
# exists because an alarm that fires every night until someone pastes a token
# trains both partners to stop reading alarms. That is precisely how the smoke
# suite was lost the first time.
#
# 137 (SIGKILL) and 143 (SIGTERM) are the machine's doing, not the job's. The
# documented 2026-08-14 failure is this Mac sleeping through a scheduled window;
# recording "failed" for a job the OS killed sends whoever reads it hunting for
# a bug in a job that does not have one.
case $rc in
  0)         state=succeeded ;;
  78)        state=skipped   ;;
  124|137)   state=timed_out ;;
  143)       state=cancelled ;;
  *)         state=failed    ;;
esac

# ops.run's own constraint `a_failure_names_its_class` (migration 0115) requires
# a failure_class for exactly 'failed' and 'timed_out' and permits none
# elsewhere. Deriving it here rather than letting the database refuse the insert
# is what keeps a recording failure from ever being the job's problem.
fclass=()
case $state in
  failed|timed_out) fclass=(--failure-class "exit_$rc") ;;
esac

# Inherited when a chain or a deploy exported one, so a job a nightly chain
# launched traces WITH that chain instead of starting a lone journey — the same
# reason bin/smoke-and-record.sh reads this variable.
corr=()
[ -n "${CARR_CORRELATION_ID:-}" ] && corr=(--correlation "$CARR_CORRELATION_ID")

mkdir -p "$REPO/out" 2>/dev/null

# ── throttle a high-frequency SUCCEEDED row ──────────────────────────────────
# Inert when HEARTBEAT_INTERVAL is 0 (the default, and every existing job's
# real invocation today): should_record is always 1 below, so this is a no-op
# for the six jobs that never pass the flag.
STATE_DIR="$REPO/out/run-scheduled-state"
STATE_FILE="$STATE_DIR/$SERVICE.$RUN_KEY.last-success"
should_record=1
if [ "$state" = "succeeded" ] && [ "${HEARTBEAT_INTERVAL:-0}" -gt 0 ] 2>/dev/null; then
  mkdir -p "$STATE_DIR" 2>/dev/null
  if [ -f "$STATE_FILE" ]; then
    last="$(cat "$STATE_FILE" 2>/dev/null || print -r -- 0)"
    now_epoch="$(date -u +%s)"
    elapsed=$(( now_epoch - last ))
    [ "$elapsed" -lt "$HEARTBEAT_INTERVAL" ] && should_record=0
  fi
fi

if [ "$should_record" -eq 1 ]; then
  set -A argv "$PY" "$REPO/tools/ops-record.py" run \
    --service "$SERVICE" --key "$RUN_KEY" --state "$state" \
    --exit-code "$rc" --started-at "$STARTED" \
    --source-kind wrapper --source-ref bin/run-scheduled.sh \
    --detail "$RUN_KEY exited $rc" "${fclass[@]}" "${corr[@]}"
  "${argv[@]}" >> "$LOG" 2>&1
  recorder_exit=$?
  record_action=recorded
  if [ "$state" = "succeeded" ] && [ "${HEARTBEAT_INTERVAL:-0}" -gt 0 ] 2>/dev/null; then
    mkdir -p "$STATE_DIR" 2>/dev/null
    date -u +%s > "$STATE_FILE" 2>/dev/null
  fi
else
  argv=()
  recorder_exit=throttled
  record_action=throttled
fi

# A non-succeeded outcome always clears the throttle, so the recovery — the
# next succeeded fire — posts immediately rather than waiting out a stale
# interval. A silent recovery is exactly the kind of silence this ledger
# exists to make visible.
if [ "$state" != "succeeded" ] && [ "${HEARTBEAT_INTERVAL:-0}" -gt 0 ] 2>/dev/null; then
  rm -f "$STATE_FILE" 2>/dev/null
fi

# One line, always, whatever happened. The run key and service are the job's own
# identifiers, the detail is a key and a number: nothing here is a secret and
# nothing is client content. record_action sits BEFORE recorder_exit and argv
# stays the trailing field exactly as before, so every existing regex-based
# check against this line (name=value, argv= to end of line) is unaffected —
# it only ever gains the new field, never loses or reorders an old one.
print -r -- "$(date -u '+%Y-%m-%dT%H:%M:%SZ') run-scheduled key=$RUN_KEY service=$SERVICE child_exit=$rc state=$state record_action=$record_action recorder_exit=$recorder_exit argv=${argv[*]}" >> "$LOG"

# ── an independent heartbeat riding this same wake (carr-local-edge-node) ────
# Deliberately NOT gated on the primary job's own outcome above: a broken
# partner-ping query says nothing about whether the Mac is awake, and tying
# the edge node's presence signal to one unrelated script's health would make
# it only as reliable as that script.
if [ -n "$ALSO_HEARTBEAT" ]; then
  HB_RUN_KEY="launchd.heartbeat"
  HB_STATE_FILE="$STATE_DIR/$ALSO_HEARTBEAT.$HB_RUN_KEY.last-success"
  hb_should_record=1
  if [ "${HEARTBEAT_INTERVAL:-0}" -gt 0 ] 2>/dev/null && [ -f "$HB_STATE_FILE" ]; then
    hb_last="$(cat "$HB_STATE_FILE" 2>/dev/null || print -r -- 0)"
    hb_now="$(date -u +%s)"
    hb_elapsed=$(( hb_now - hb_last ))
    [ "$hb_elapsed" -lt "$HEARTBEAT_INTERVAL" ] && hb_should_record=0
  fi
  if [ "$hb_should_record" -eq 1 ]; then
    set -A hb_argv "$PY" "$REPO/tools/ops-record.py" run \
      --service "$ALSO_HEARTBEAT" --key "$HB_RUN_KEY" --state succeeded \
      --exit-code 0 --started-at "$STARTED" \
      --source-kind wrapper --source-ref bin/run-scheduled.sh \
      --detail "heartbeat via $SERVICE/$RUN_KEY" "${corr[@]}"
    "${hb_argv[@]}" >> "$LOG" 2>&1
    hb_recorder_exit=$?
    hb_record_action=recorded
    mkdir -p "$STATE_DIR" 2>/dev/null
    date -u +%s > "$HB_STATE_FILE" 2>/dev/null
  else
    hb_argv=()
    hb_recorder_exit=throttled
    hb_record_action=throttled
  fi
  print -r -- "$(date -u '+%Y-%m-%dT%H:%M:%SZ') run-scheduled key=$HB_RUN_KEY service=$ALSO_HEARTBEAT child_exit=0 state=succeeded record_action=$hb_record_action recorder_exit=$hb_recorder_exit argv=${hb_argv[*]}" >> "$LOG"
fi

# The job's answer, never this script's.
exit $rc
