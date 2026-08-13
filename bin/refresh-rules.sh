#!/bin/zsh
# refresh-rules.sh — close the same-day taught-rules gap (Joe, 2026-07-31:
# "i dont love that it lags over night").
#
# The compiled-rules files are the BINDING auto-loaded surface; a rule taught
# from Cowork/phone mid-day sat in the DB until the 2am chain re-rendered them.
# This job re-renders JUST those two files HOURLY, 7:00-20:00 local (launchd
# StartCalendarInterval) — business hours only, on Joe's cost ruling 2026-07-31:
# Neon free tier = 100 CU-h/month, every wake burns ~5 min of compute, and
# capping out SUSPENDS the DB until the next cycle. Overnight wakes would spend
# real budget re-rendering rules nobody teaches at 3am; the 2am chain stays the
# night's only DB visitor. Pure code, no model (T0 per model-tiering).
#
# SKIP-not-FAIL: missing env = exit 0 with a SKIP line, per house convention.
REPO="$HOME/carr-system"
LOG="$REPO/out/rules-refresh.log"
[ -f "$HOME/.config/carr/db.env" ] || { echo "$(date -u +%FT%TZ) SKIP no db.env" >> "$LOG"; exit 0; }
cd "$REPO" || exit 1
if CARR_EXPORT_LIVE=1 ./run.sh export --only compiled-rules >> "$LOG" 2>&1; then
  echo "$(date -u +%FT%TZ) OK rules refreshed" >> "$LOG"
  # Activating a rule updates the renders above but NOT the enforcement map,
  # whose inventory must match the render order exactly. That gap fired twice
  # in two days (2026-08-12, 2026-08-13) and each time every session booting
  # afterwards was told the gates were not in force. The map inventory is
  # derived data, so it is synced by code here, right where the renders move.
  # It re-stamps only the map's own contract hash — never the gate-script
  # hashes, so tampering with a gate is still detected on the next boot.
  python3 "$REPO/bin/sync-enforcement-map.py" >> "$LOG" 2>&1 || true
else
  rc=$?  # capture BEFORE the date subshell resets $? — the old line always logged rc=0
  echo "$(date -u +%FT%TZ) FAIL rules refresh rc=$rc" >> "$LOG"
fi
# keep the log from growing forever: trim to the last 500 lines
tail -n 500 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
