#!/bin/zsh
# run-scheduled.sh — make a launchd job's outcome durable, without touching the
# job. Program 4's first slice.
#
#   usage: bin/run-scheduled.sh <service-key> <run-key> <command> [args...]
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

set -u

EX_USAGE=64

if [ $# -lt 3 ]; then
  print -ru2 -- "usage: bin/run-scheduled.sh <service-key> <run-key> <command> [args...]"
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

set -A argv "$PY" "$REPO/tools/ops-record.py" run \
  --service "$SERVICE" --key "$RUN_KEY" --state "$state" \
  --exit-code "$rc" --started-at "$STARTED" \
  --source-kind wrapper --source-ref bin/run-scheduled.sh \
  --detail "$RUN_KEY exited $rc" "${fclass[@]}" "${corr[@]}"

mkdir -p "$REPO/out" 2>/dev/null
"${argv[@]}" >> "$LOG" 2>&1
recorder_exit=$?

# One line, always, whatever happened. The run key and service are the job's own
# identifiers, the detail is a key and a number: nothing here is a secret and
# nothing is client content.
print -r -- "$(date -u '+%Y-%m-%dT%H:%M:%SZ') run-scheduled key=$RUN_KEY service=$SERVICE child_exit=$rc state=$state recorder_exit=$recorder_exit argv=${argv[*]}" >> "$LOG"

# The job's answer, never this script's.
exit $rc
