#!/bin/zsh
# Nightly encrypted pg_dump -> R2 (A9: encrypted from the FIRST dump; a durable
# off-Mac copy is the permanent record). Scheduled at cutover; runnable
# any time: ./bin/backup-dump.sh
# Private key: ~/.config/carr/age-key.txt (local, 600) — Joe owes an OFFLINE
# copy (paper/sealed); tracked in secrets-inventory.md.
#
# CHANGED 2026-08-06 (ORDER 42b): dumps used to `git add backups/ && git commit
# && git push` — full encrypted production DB dumps, tracked in git history
# forever. ORDER 42 flagged that as PII exposure. The dump still writes to
# backups/ locally (restore-rehearse.sh and any manual `age -d` still find it
# there — the directory is now gitignored, not deleted), and now also uploads
# to the R2 archive via bin/backup-archive-r2.py, which reuses
# lib/r2_archive.py's quota-guarded uploader (ORDER 20) rather than a second
# implementation. See ops/order42b-history-purge.md for the git-history purge
# of the dumps already committed under the old scheme.
# pipefail ADDED 2026-08-07, and it is half of a fix for a night this script
# reported success on a 200-byte backup. See the guard block below for the whole
# story; the short version is that in `pg_dump | age > f` the pipeline's exit
# status is AGE's, age encrypts an empty stream without complaint, and so a
# pg_dump that died mid-transfer was invisible to `set -e`.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="/usr/local/opt/node@22/bin:/opt/homebrew/opt/libpq/bin:/usr/local/opt/libpq/bin:$PATH"
PUBKEY="$(cat "$REPO/backups-public-key.txt")"
URL="$("$REPO/mcp-server/node_modules/.bin/neonctl" connection-string production \
      --project-id steep-field-48688294 --role-name neondb_owner 2>/dev/null)"
STAMP="$(date -u +%Y%m%d)"
OUT="$REPO/backups/carr-$STAMP.sql.age"
# --no-owner --no-acl ADDED 2026-08-02, and the reason is a real failure, not style.
# The first genuine restore rehearsal ever run against these dumps died with
#   ERROR: permission denied to change default privileges
# A plain pg_dump embeds ALTER DEFAULT PRIVILEGES / GRANT / REVOKE / OWNER TO
# naming the source database's owning role. Restoring into a fresh database —
# which is what any real recovery does — those roles are not the restoring
# session's to act for, the first statement errors, and the load aborts. Nine
# months of nightly backups would have been discovered unrestorable at the worst
# possible moment.
# We restore SCHEMA AND DATA, never the source cluster's permission model: roles
# and grants are rebuilt by the migrations, which are in git. Dropping them from
# the dump costs nothing and is what makes it portable.
if ! pg_dump --no-owner --no-acl "$URL" | age -r "$PUBKEY" > "$OUT.tmp"; then
  echo "DUMP FAILED (pg_dump or age exited non-zero) — aborting, previous backups untouched" >&2
  rm -f "$OUT.tmp"
  exit 1
fi

# SIZE FLOOR ADDED 2026-08-07. The guard here used to be `[ -s "$OUT.tmp" ]`,
# which asks one question: is this file larger than zero bytes. On 2026-08-07
# pg_dump lost its Neon connection mid-dump ("server closed the connection
# unexpectedly"). age wrote its header over the empty stream, producing a
# 200-byte file. -s passed it, mv promoted it to that day's official backup,
# the archiver uploaded it to R2 as the durable off-Mac copy, and the dead-man
# switch was pinged. Every signal said the backup succeeded; none of them
# looked at the size. This is the same failure class as the --no-owner --no-acl
# bug above: a backup that looks taken and is not restorable.
#
# The floor is the larger of 1 MiB and HALF the most recent previous dump. Half
# rather than 90%: the database legitimately grows and shrinks night to night,
# and a guard that cries wolf gets switched off. A truncated dump is not 40%
# short, it is three orders of magnitude short — 200 bytes against 17 MB.
SIZE="$(stat -f%z "$OUT.tmp")"
FLOOR=1048576
PREV="$(ls -t "$REPO/backups"/carr-*.sql.age 2>/dev/null | grep -vxF "$OUT" | head -1 || true)"
PREV_SIZE=0
if [ -n "${PREV:-}" ]; then
  PREV_SIZE="$(stat -f%z "$PREV")"
  if [ "$(( PREV_SIZE / 2 ))" -gt "$FLOOR" ]; then
    FLOOR="$(( PREV_SIZE / 2 ))"
  fi
fi
if [ "$SIZE" -lt "$FLOOR" ]; then
  echo "SHORT DUMP — $SIZE bytes, floor is $FLOOR bytes." >&2
  if [ -n "${PREV:-}" ]; then
    echo "Previous dump for comparison: $PREV ($PREV_SIZE bytes)." >&2
  fi
  echo "Aborting: previous backups untouched, nothing archived to R2." >&2
  rm -f "$OUT.tmp"
  exit 1
fi
mv "$OUT.tmp" "$OUT"
# keep 14 dailies in backups/ (local, gitignored); the R2 archive keeps everything forever
ls -t "$REPO/backups"/carr-*.sql.age 2>/dev/null | tail -n +15 | xargs rm -f 2>/dev/null || true

# Archive to R2 (ORDER 20's quota-guarded uploader, ORDER 42b's replacement for the
# git-commit step). Same production URL already resolved above is reused as
# DATABASE_URL so the real system_config.r2.quota_gb cap is consulted instead of
# the script defaulting blind. A quota refusal is reported, not fatal: the local
# dump in backups/ stands either way (rc=0 from the archiver), same posture the
# document pipeline takes on its OWED path.
ARCHIVE_JSON="$(cd "$REPO" && DATABASE_URL="$URL" .venv/bin/python bin/backup-archive-r2.py "$OUT" 2>&1)" \
  || { echo "R2 archive step failed (backup itself is fine, sitting at $OUT):" >&2
       echo "$ARCHIVE_JSON" >&2; }
# `|| true` is load-bearing under pipefail (added 2026-08-07): grep exits 1 when
# the archiver printed no key, and without it that would abort the script AFTER
# a good backup was already taken and archived.
R2_KEY="$(print -r -- "$ARCHIVE_JSON" | grep -m1 '"key"' | sed -E 's/.*"key": *"([^"]+)".*/\1/' || true)"
# Size reported in EXACT BYTES, not `du -h`. du floors at the 4K block size, so
# on 2026-08-07 the success line read "(4.0K)" for a 200-byte corrupt backup —
# the one number that would have exposed the failure could not physically be
# displayed small enough to look wrong.
if [ -n "${R2_KEY:-}" ]; then
  echo "backup ok -> $OUT ($SIZE bytes, floor was $FLOOR) -> R2 archive: $R2_KEY"
else
  echo "backup ok -> $OUT ($SIZE bytes, floor was $FLOOR) -> R2 archive: see stderr above"
fi
