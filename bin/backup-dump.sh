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
#
# CHANGED 2026-08-14 (PROGRAM 4, THE MAC-INDEPENDENT COPY): this script now
# serves BOTH the local nightly (Joe's Mac) and the GitHub Actions nightly
# workflow — rule a8c55a47, a manual path and an automated path doing the
# same job must be the same code, never a second implementation. Three env
# vars, all optional and all unset on Joe's Mac, so local behavior is
# byte-identical to before this change:
#   BACKUP_DATABASE_URL — if set, used AS-IS instead of resolving the
#     production owner DSN through neonctl. Actions has no neonctl login and
#     should never need one: it connects as the dedicated read-only
#     carr_backup role (migrations/0119_backup_role.sql) via a GitHub secret.
#   BACKUP_SKIP_R2=1 — skips the R2 archive step below. A GitHub runner's
#     disk dies with the job, so there is no local copy to keep and no
#     reason to spend the R2 quota a second time on the same night's dump;
#     the encrypted file goes to the workflow artifact (90-day retention)
#     instead. See .github/workflows/backup-nightly.yml.
#   BACKUP_OUTPUT_DIR — overrides where the dump (and the size-floor's
#     previous-dump lookup) lives. Defaults to $REPO/backups, exactly as
#     before.
#
# SCHEMA SCOPE (2026-08-14). The application owns public and ops. Neon Auth
# owns neon_auth, which is provider-managed identity data and is outside the
# CARR record-layer backup contract. The dedicated carr_backup role therefore
# has SELECT only in public+ops, and pg_dump is scoped to the same two schemas.
# This is both the least-privilege boundary and the restore boundary: Neon
# recreates its managed services; this artifact restores CARR's schema+data.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="/usr/local/opt/node@22/bin:/opt/homebrew/opt/libpq/bin:/usr/local/opt/libpq/bin:$PATH"
PG_DUMP_BIN="${PG_DUMP_BIN:-pg_dump}"
PUBKEY="$(cat "$REPO/backups-public-key.txt")"
if [ -n "${BACKUP_DATABASE_URL:-}" ]; then
  URL="$BACKUP_DATABASE_URL"
else
  URL="$("$REPO/mcp-server/node_modules/.bin/neonctl" connection-string production \
        --project-id steep-field-48688294 --role-name neondb_owner 2>/dev/null)"
fi

# KEEPALIVES + CONNECT TIMEOUT, ADDED 2026-08-16, and the reason is a five and
# a half hour outage rather than tidiness.
#
# WHAT HAPPENED. The 02:00 chain began, took its catch-up backup as step 0, and
# Neon dropped the connection from its side. pg_dump never noticed. libpq's
# default is to wait forever on a silent peer, so it sat on a half-open socket
# having written ZERO bytes, and it held the nightly lock the entire time. Every
# invocation after it printed "another 'nightly' run is in progress" and exited
# 0 — a green exit code from a lock skip, not from a chain. Nothing past step 0
# ran for two days: no cadence engine, no matcher, no export, no boards, no
# Graph, no backup. Found only because the export register still read 28 hours
# stale while the health check's own row said the chain was fine.
#
# WHY THIS FIX AND NOT A SERVER-SIDE ONE. pg_stat_activity on Neon showed NO
# backend for the dump at all — the server side was already gone. There was
# nothing to pg_terminate_backend. When the peer has vanished, only the client
# can end the wait, so this has to be a libpq setting.
#
# WHAT THESE DO. keepalives makes libpq probe an idle connection; after
# roughly idle + (interval x count) of silence, about four minutes here, the
# socket errors out and the step FAILS instead of wedging. A failed backup
# leaves the previous one untouched (see the guard below) and, far more
# importantly, releases the lock so the rest of the chain runs. connect_timeout
# bounds the handshake the same way. Overridable for a slow link, never
# unbounded.
#
# Appended with the right separator: these connection strings already carry
# sslmode and channel_binding, so a blind "?" would corrupt them.
_KEEPALIVE_PARAMS="keepalives=1"
_KEEPALIVE_PARAMS="$_KEEPALIVE_PARAMS&keepalives_idle=${BACKUP_KEEPALIVE_IDLE:-60}"
_KEEPALIVE_PARAMS="$_KEEPALIVE_PARAMS&keepalives_interval=${BACKUP_KEEPALIVE_INTERVAL:-15}"
_KEEPALIVE_PARAMS="$_KEEPALIVE_PARAMS&keepalives_count=${BACKUP_KEEPALIVE_COUNT:-12}"
_KEEPALIVE_PARAMS="$_KEEPALIVE_PARAMS&connect_timeout=${BACKUP_CONNECT_TIMEOUT:-30}"
case "$URL" in
  *keepalives=*) : ;;                       # already carries them; leave it alone
  *\?*) URL="$URL&$_KEEPALIVE_PARAMS" ;;    # has a query string; append
  *)    URL="$URL?$_KEEPALIVE_PARAMS" ;;    # no query string; start one
esac

STAMP="$(date -u +%Y%m%d)"
OUTDIR="${BACKUP_OUTPUT_DIR:-$REPO/backups}"
mkdir -p "$OUTDIR"
OUT="$OUTDIR/carr-$STAMP.sql.age"
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
if ! "$PG_DUMP_BIN" --no-owner --no-acl --schema=public --schema=ops "$URL" \
     | age -r "$PUBKEY" > "$OUT.tmp"; then
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
#
# size_bytes() ADDED 2026-08-14 (PROGRAM 4): this used to be inline `stat
# -f%z`, which is BSD stat and Mac-only. GNU stat (the GitHub Actions
# runner) takes -f to mean something else entirely ("file system status",
# not "format") and this would silently do the wrong thing there rather than
# fail loud. bin/worktree.sh hit the identical BSD-vs-GNU split for a
# different stat call and settled on shelling out to Python for the one
# piece of stdlib both platforms carry unmodified; same fix, same reason,
# here. Bare `python3` rather than "$REPO/.venv/bin/python": the Actions
# runner never provisions this repo's venv (it only needs postgresql-client
# and age — see .github/workflows/backup-nightly.yml), and os.path.getsize
# needs nothing beyond the standard library. Byte counts are identical to
# the old `stat -f%z` on macOS, so local behavior is unchanged.
size_bytes() { python3 -c 'import os,sys; print(os.path.getsize(sys.argv[1]))' "$1"; }
SIZE="$(size_bytes "$OUT.tmp")"
FLOOR=1048576
PREV="$(ls -t "$OUTDIR"/carr-*.sql.age 2>/dev/null | grep -vxF "$OUT" | head -1 || true)"
PREV_SIZE=0
if [ -n "${PREV:-}" ]; then
  PREV_SIZE="$(size_bytes "$PREV")"
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
ls -t "$OUTDIR"/carr-*.sql.age 2>/dev/null | tail -n +15 | xargs rm -f 2>/dev/null || true

# BACKUP_SKIP_R2=1 ADDED 2026-08-14 (PROGRAM 4): the GitHub Actions runner's
# disk does not survive past the job, so there is no local copy to keep and
# no reason to spend the R2 quota archiving a dump that already goes to the
# workflow artifact (90-day retention — see .github/workflows/backup-nightly.yml).
# Unset on Joe's Mac, so the local nightly still archives to R2 exactly as
# before.
if [ -n "${BACKUP_SKIP_R2:-}" ]; then
  echo "backup ok -> $OUT ($SIZE bytes, floor was $FLOOR) -> R2 archive: skipped (BACKUP_SKIP_R2)"
else
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
fi
