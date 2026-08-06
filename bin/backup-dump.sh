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
set -eu
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
pg_dump --no-owner --no-acl "$URL" | age -r "$PUBKEY" > "$OUT.tmp"
[ -s "$OUT.tmp" ] || { echo "EMPTY DUMP — aborting, previous backups untouched" >&2; rm -f "$OUT.tmp"; exit 1; }
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
R2_KEY="$(print -r -- "$ARCHIVE_JSON" | grep -m1 '"key"' | sed -E 's/.*"key": *"([^"]+)".*/\1/')"
if [ -n "${R2_KEY:-}" ]; then
  echo "backup ok -> $OUT ($(du -h "$OUT" | cut -f1)) -> R2 archive: $R2_KEY"
else
  echo "backup ok -> $OUT ($(du -h "$OUT" | cut -f1)) -> R2 archive: see stderr above"
fi
