#!/bin/sh
# calendar-eventkit-capture.sh — the unattended calendar capture, replacing the
# published-feed fetch Joe turned off on 2026-08-14.
#
# WHY THIS EXISTS. calendar-fetch-daily read a PUBLISHED calendar feed, and
# Microsoft strips ATTENDEE and ORGANIZER from that feed by design: a backfill
# over it matched 6 events out of 81. Reading the LOCAL calendar instead finds
# attendee emails on roughly half of all events — 386 of 936 on the 2026-08-13
# dump. Same meetings, the half that names who was in the room.
#
# THE ONE MECHANIC THAT MATTERS, and the reason this is a script rather than a
# line in a plist: the read MUST be launched with `open -a`, never by executing
# the bundle's inner binary. macOS attributes calendar permission to the
# RESPONSIBLE PROCESS. Launched properly, the bundle is responsible and holds the
# grant. Exec the inner binary and the responsible process is the shell, which has
# no usage description and no grant — macOS then answers DENIED without ever
# prompting. Both were measured on 2026-08-14 within two minutes of each other:
#
#     direct exec of Contents/MacOS/carr-calendar-access -> "DENIED", exit 3
#     open -a "CARR Calendar Access.app"                 -> real events, exit 0
#
# A DENIAL AND AN EMPTY CALENDAR MUST NEVER LOOK THE SAME. That confusion is the
# defect that started this whole thread: a verb answered emptily instead of
# refusing, and the empty answer was read as truth. So this script treats DENIED
# as a hard failure with its own exit code, and never reports "0 touches" when
# what actually happened was "not allowed to look".
#
# WHAT IT WRITES, and what it deliberately does not:
#   EXACT email matches  -> logged as touches. An exact address is evidence a
#                           NAMED person was in the room.
#   DOMAIN-only matches  -> reported, never logged. "Someone from that org" is
#                           not a dated touch on an individual.
#   Unknown externals    -> reported as research candidates, never auto-created.
#                           A new name is an intake trigger for a person to weigh,
#                           not a record this job invents.
#
# RISK: YELLOW. Reads the calendar read-only, writes only internal touch records,
# sends nothing outside and publishes nothing.
#
#   bin/calendar-eventkit-capture.sh            # capture, log exact touches
#   bin/calendar-eventkit-capture.sh --dry-run  # report only, write nothing
#   bin/calendar-eventkit-capture.sh --days 14  # widen the window
set -u

REPO="${CARR_REPO:-$HOME/carr-system}"
cd "$REPO" || { echo "calendar-capture: FAIL cannot reach $REPO" >&2; exit 1; }

APP="$REPO/tools/CARR Calendar Access.app"
ACCESS_LOG="$REPO/out/calendar-access.log"
PY="$REPO/.venv/bin/python"
[ -x "$PY" ] || PY=python3

DAYS=7
DRY=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --days) DAYS="${2:-7}"; shift ;;
    --dry-run) DRY=1 ;;
    -h|--help) sed -n '1,40p' "$0"; exit 0 ;;
    *) echo "calendar-capture: unknown argument: $1" >&2; exit 64 ;;
  esac
  shift
done

mkdir -p "$REPO/out"

# ---------------------------------------------------------------- 1. the read
# Mark the log so we judge THIS run's lines and not a previous run's success —
# the failure mode a naive `tail` would hide.
MARK="$(date -u +%FT%TZ)-$$"
printf '=== capture-run %s ===\n' "$MARK" >> "$ACCESS_LOG"

if [ ! -d "$APP" ]; then
  echo "calendar-capture: FAIL the access bundle is missing at $APP" >&2
  echo "  Without it macOS cannot prompt for calendar permission at all." >&2
  exit 1
fi

open -a "$APP" --args dump || {
  echo "calendar-capture: FAIL could not launch the access bundle" >&2; exit 1; }

# `open` returns as soon as the app is launched, so wait for the bundle's own
# exit line to appear after our marker rather than assuming it finished.
i=0
while [ "$i" -lt 60 ]; do
  if sed -n "/=== capture-run $MARK ===/,\$p" "$ACCESS_LOG" 2>/dev/null | grep -q '^exit='; then
    break
  fi
  sleep 1
  i=$((i + 1))
done

RUN_LOG="$(sed -n "/=== capture-run $MARK ===/,\$p" "$ACCESS_LOG" 2>/dev/null)"
if [ -z "$RUN_LOG" ] || ! printf '%s' "$RUN_LOG" | grep -q '^exit='; then
  echo "calendar-capture: FAIL the read did not finish within 60s" >&2
  exit 1
fi

if printf '%s' "$RUN_LOG" | grep -qi 'DENIED'; then
  echo "calendar-capture: FAIL calendar access DENIED — nothing was read." >&2
  echo "  This is a PERMISSION answer, not an empty calendar, and it must never" >&2
  echo "  be reported as zero touches. Grant Calendars to \"CARR Calendar Access\"" >&2
  echo "  in System Settings > Privacy & Security > Calendars, then re-run." >&2
  exit 3
fi

SCANNED="$(printf '%s' "$RUN_LOG" | sed -n 's/.*events scanned: \([0-9]*\).*/\1/p' | head -1)"
echo "calendar-capture: read OK — ${SCANNED:-?} events scanned"

# ---------------------------------------------------------------- 2. the match
MATCH_JSON="$REPO/out/calendar-touch-proposals.json"
if ! "$PY" "$REPO/tools/calendar-touch-matcher.py" "$DAYS" --json > "$MATCH_JSON" 2>/dev/null; then
  echo "calendar-capture: FAIL the matcher did not complete" >&2; exit 1
fi

"$PY" - "$MATCH_JSON" "$DRY" "$DAYS" <<'PYEOF'
import json, subprocess, sys, pathlib
path, dry, days = sys.argv[1], sys.argv[2] == "1", sys.argv[3]
d = json.load(open(path))
c = d["counts"]
print(f"calendar-capture: window {days}d — {c['emails']} attendee address(es): "
      f"{c['exact']} exact, {c['domain']} domain-only, {c['unknown']} unknown")

for u in d["unknown"]:
    print(f"  research candidate  {u['email']}  (last seen {u['last_seen']})")
for m in d["domain"]:
    print(f"  domain-only, NOT logged  {m['email']} -> {m['org'][:50]}")

if not d["exact"]:
    print("calendar-capture: no exact matches in this window — nothing to log")
    sys.exit(0)

if dry:
    for e in d["exact"]:
        print(f"  would log touch  {e['ref']}  via {e['email']}  ({e['last_seen']})")
    sys.exit(0)

repo = pathlib.Path(__file__).resolve().parent if False else pathlib.Path.cwd()
failed = 0
for e in d["exact"]:
    ev = (e["events"] or [{}])[0]
    args = json.dumps({
        "idempotency_key": f"calcap-{e['email']}-{e['last_seen']}",
        "ref": e["ref"],
        "happened_on": e["last_seen"],
        "channel": "meeting",
        "note": (f"Calendar evidence: {e['email']} attended "
                 f"\"{ev.get('title','(untitled)')}\" on {ev.get('day', e['last_seen'])}. "
                 f"Inferred from the local calendar by an exact email match; "
                 f"not self-reported."),
    })
    r = subprocess.run(["./run.sh", "call", "stamp-touch", args],
                       capture_output=True, text=True)
    ok = '"ok": true' in r.stdout or '"ok":true' in r.stdout
    print(f"  {'logged touch  ' if ok else 'FAILED to log '} {e['ref']}  via {e['email']}")
    if not ok:
        failed += 1
        print("    " + (r.stdout or r.stderr).strip().replace("\n", "\n    ")[:400])
sys.exit(1 if failed else 0)
PYEOF
