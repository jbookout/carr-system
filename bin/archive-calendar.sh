#!/bin/bash
# archive-calendar.sh — daily snapshot of both partners' published Outlook iCal feeds.
#
# WHY THIS EXISTS (loop #180, 2026-08-06): the published feed is a ROLLING window.
# On current publish settings it reaches only ~1 month into the past (observed:
# events before July 1 were simply absent on an Aug 4 fetch), and Exchange's hard
# ceiling is one year (Set-MailboxCalendarFolder -PublishDateRangeFrom OneYear).
# Anything that scrolls out of the window is gone from the feed forever, so a
# "look back N months" backfill silently under-covers. This archive is the durable
# history: a later backfill reconstructs the real window from the union of
# snapshots instead of being capped by the live feed's retention.
#
# It fetches the feeds ITSELF rather than copying DNA/Team/calendar-latest.ics,
# for two reasons: (1) unattended processes cannot reliably read the Google Drive
# File Provider mount (the 2026-07-17 lesson in calendar-feeds.md), and (2) an
# independent fetch keeps archiving alive even when a partner's Shortcuts fetcher
# dies — which it does (both drop files sat stale from Aug 4 to Aug 6).
#
# Runs as a step in bin/nightly.sh; the manual path is the SAME script by hand:
#   ./bin/archive-calendar.sh
# Writes ONLY to out/calendar-archive/ (gitignored — meeting data is PII-adjacent
# and PII never lives in this repo, ORDER 42). Risk color GREEN: reads the feeds,
# writes its own local archive, touches no vault file and no record.
#
# Feed URLs are bearer tokens and live in ~/.config/carr/calendar.env, never here:
#   CARR_CAL_URL_JOE=...   CARR_CAL_URL_DELL=...
# Missing config -> exit 78 (EX_CONFIG): the chain logs SKIP, not FAIL.
#
# Snapshots are deduplicated by content: a day whose feed is byte-identical to the
# newest existing snapshot stores nothing. Same-day re-runs overwrite that day's
# file (last write wins). ~80-300 KB per changed day; a year is tens of MB.

set -u

REPO="$(cd "$(dirname "$0")/.." && pwd)"
ARCHIVE="$REPO/out/calendar-archive"
ENV_FILE="$HOME/.config/carr/calendar.env"

if [ ! -f "$ENV_FILE" ]; then
  echo "archive-calendar: $ENV_FILE not found — feed URLs not configured"
  exit 78
fi
# shellcheck disable=SC1090
. "$ENV_FILE"

mkdir -p "$ARCHIVE"
today="$(date +%Y%m%d)"
rc_total=0
did_any_config=0

archive_one() {                 # archive_one <owner> <url>
  local owner="$1" url="$2"
  [ -z "$url" ] && return 0
  did_any_config=1

  local tmp="$ARCHIVE/.fetch-$owner.$$"
  # -A Mozilla/5.0 is load-bearing: Office 365 silently drops bare clients
  # (THE FIX, calendar-feeds.md 2026-07-17).
  if ! curl -sS -f -m 60 -A "Mozilla/5.0" -o "$tmp" "$url"; then
    echo "archive-calendar: $owner FETCH FAILED (curl exit $?)"
    rm -f "$tmp"; rc_total=1; return 1
  fi
  # An empty or event-less body is the known silent-failure shape; keep nothing.
  if ! grep -q "^BEGIN:VEVENT" "$tmp"; then
    echo "archive-calendar: $owner fetch returned no VEVENTs — discarded, not archived"
    rm -f "$tmp"; rc_total=1; return 1
  fi

  # Dedupe ignores DTSTAMP: Office 365 stamps every VEVENT with the FETCH time,
  # so two fetches of an unchanged calendar differ on every DTSTAMP line and
  # nothing else (measured 2026-08-06). Byte-level cmp would archive every night.
  local latest
  latest="$(ls "$ARCHIVE"/calendar-"$owner"-*.ics 2>/dev/null | sort | tail -1)"
  if [ -n "$latest" ] && cmp -s <(grep -v '^DTSTAMP:' "$tmp") <(grep -v '^DTSTAMP:' "$latest"); then
    echo "archive-calendar: $owner unchanged since $(basename "$latest") — no new snapshot"
    rm -f "$tmp"; return 0
  fi

  local dest="$ARCHIVE/calendar-$owner-$today.ics"
  mv "$tmp" "$dest"
  local events
  events="$(grep -c "^BEGIN:VEVENT" "$dest")"
  echo "archive-calendar: $owner -> $(basename "$dest") ($events events)"
}

archive_one joe  "${CARR_CAL_URL_JOE:-}"
archive_one dell "${CARR_CAL_URL_DELL:-}"

if [ "$did_any_config" -eq 0 ]; then
  echo "archive-calendar: $ENV_FILE exists but defines no CARR_CAL_URL_* — not configured"
  exit 78
fi
exit $rc_total
