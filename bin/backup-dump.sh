#!/bin/zsh
# Nightly encrypted pg_dump -> git (A9: encrypted from the FIRST dump; git,
# not the vendor, is the permanent record). Scheduled at cutover; runnable
# any time: ./bin/backup-dump.sh
# Private key: ~/.config/carr/age-key.txt (local, 600) — Joe owes an OFFLINE
# copy (paper/sealed); tracked in secrets-inventory.md.
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
# keep 14 dailies in git working tree; git history keeps everything forever
ls -t "$REPO/backups"/carr-*.sql.age 2>/dev/null | tail -n +15 | xargs rm -f 2>/dev/null || true
cd "$REPO" && git add backups/ && git commit -q -m "backup: encrypted dump $STAMP" && git push -q
echo "backup ok -> $OUT ($(du -h "$OUT" | cut -f1))"
