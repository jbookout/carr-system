#!/bin/zsh
# restore-rehearse.sh — prove the encrypted backups can actually come BACK.
#
# WHY THIS EXISTS (2026-08-02 cold-session audit). bin/backup-dump.sh has been
# writing encrypted dumps since 2026-07-30 and NOTHING has ever read one. A grep
# across the whole repo for `age -d`, `age --decrypt`, `pg_restore` and
# `psql ... backups/` returned zero hits: no code existed that could restore
# these files, so "we have backups" was an assumption, not a fact. Three things
# make that assumption expensive to keep:
#   1. Neon's point-in-time-restore window is SIX HOURS. Past that, these dumps
#      are the only history that exists. There is no vendor safety net behind
#      them.
#   2. backup-dump.sh runs inside the nightly chain, which is skipped whenever
#      the Mac sleeps through 2am. The gap is visible in backups/ right now:
#      2026-07-30, 2026-07-31, then 2026-08-02. No 08-01. A backup you cannot
#      restore AND cannot count on being there is two unknowns stacked.
#   3. An unrestorable dump is indistinguishable from a good one until the day
#      you need it, which is the worst possible day to find out.
#
# WHAT IT DOES. Creates a throwaway Neon branch, restores the newest
# backups/*.sql.age into a brand-new database ON that branch, compares row
# counts table-by-table against production, reports PASS/FAIL loudly, and
# deletes the branch. Re-runnable: the branch name carries a UTC timestamp, so
# two runs never collide, and teardown runs from a trap so it happens even on
# a failure or a Ctrl-C.
#
#   ./run.sh restore-rehearse                  # the full rehearsal
#   ./run.sh restore-rehearse --preflight      # checks only; creates nothing
#   ./run.sh restore-rehearse --identity path  # where the age private key is
#   ./run.sh restore-rehearse --keep-branch    # leave the branch for debugging
#
# THE PRIVATE KEY IS NOT OPTIONAL AND NOT STORED HERE. backups-public-key.txt in
# the repo can only ENCRYPT. Decryption needs ~/.config/carr/age-key.txt (600,
# local, plus the offline copy Joe owes per secrets-inventory.md). Override with
# --identity or CARR_AGE_IDENTITY. The key check runs FIRST, before anything is
# created or charged for, so a keyless run costs nothing and says exactly what
# is missing.
#
# ── PRODUCTION IS NEVER WRITTEN. Four independent guards, because one is a
#    promise and four is a design:
#   1. The ONLY statement this script ever sends to production is a read, and
#      that session is opened with default_transaction_read_only=on via
#      PGOPTIONS. A stray write errors at the server, not at our good intentions.
#   2. The restore target DSN is asserted to resolve to a DIFFERENT HOST than
#      production. Every Neon branch gets its own compute endpoint hostname
#      (verified 2026-08-02: production is ep-restless-resonance-…, a branch is
#      ep-damp-star-…), so a same-host restore target means something is wrong
#      and we stop rather than guess.
#   3. The restore goes into a NEW DATABASE created for this run, never into the
#      branch's inherited copy of neondb. Nothing is dropped, anywhere, ever —
#      this script contains no DROP and no TRUNCATE. A restore rehearsal that
#      needs to destroy something first is one typo away from destroying the
#      wrong thing.
#   4. Teardown deletes a branch BY THE ID neonctl handed back when we created
#      it this run, never by name and never by search. The default branch id is
#      also checked explicitly, so the delete cannot reach production even if
#      the create step returned something unexpected.
#
# ── THE DECRYPTED DUMP IS PRODUCTION DATA IN CLEARTEXT. It lands in a mktemp -d
#    (mode 700) outside the repo, never in backups/ and never anywhere git can
#    see, and the same trap that deletes the branch removes it. --keep-branch
#    keeps the BRANCH, never the plaintext; there is no flag that keeps the
#    plaintext, on purpose.
set -u

REPO="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="/usr/local/opt/node@22/bin:/opt/homebrew/opt/node@22/bin:/opt/homebrew/bin:/opt/homebrew/opt/libpq/bin:/usr/local/opt/libpq/bin:/usr/local/bin:/usr/bin:/bin"

PROJECT_ID="steep-field-48688294"
PROD_BRANCH="production"
NEONCTL="$REPO/mcp-server/node_modules/.bin/neonctl"
IDENTITY="${CARR_AGE_IDENTITY:-$HOME/.config/carr/age-key.txt}"
RESTORE_DB="restore_rehearse"

# The tables whose absence means the restore is worthless rather than merely
# odd. These are the record layer's spine: a dump that comes back without them
# is not a backup of this system. Kept short on purpose — the all-tables sweep
# below covers the other ~60 without a list anyone has to maintain.
CORE_TABLES="party lead client vendor deal activity next_action event"

# Aggregate floor. See the comparison block for why this is the truncation gate
# and per-table deltas are not.
FLOOR_PCT="${CARR_RESTORE_FLOOR_PCT:-90}"

# Freshness warning threshold for the newest dump, in hours. 26 matches the
# cadence health-check.py uses for every nightly-chain output (24h plus two
# hours of slack), so this script and the façade check agree on what "the chain
# ran last night" means.
STALE_HOURS="${CARR_BACKUP_STALE_HOURS:-26}"

# Minimum size for a dump to be worth spending a branch on. Mirrors the floor in
# backup-dump.sh, which learned on 2026-08-07 that "more than zero bytes" is not
# a check. This script had the same weak `[ -s ]` test below and would have
# created a Neon branch, restored a 200-byte file into it and only then
# discovered the problem — the expensive way to learn something free.
MIN_DUMP_BYTES="${CARR_MIN_DUMP_BYTES:-1048576}"

PREFLIGHT_ONLY=0
KEEP_BRANCH=0
VERIFY_ONLY=0
WANT_DATE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --preflight)   PREFLIGHT_ONLY=1; shift ;;
    --keep-branch) KEEP_BRANCH=1; shift ;;
    # --verify-only ADDED 2026-08-07. The missing middle rung. Until now the only
    # two things this system could say about a dump were "the file exists" (free,
    # and worth nothing — a 200-byte file exists) and "it restored into a real
    # database" (conclusive, but costs a Neon branch and several minutes). So in
    # practice nobody asked. This decrypts the dump and reads its structure: a
    # pg_dump ends with an explicit completion footer, so a truncated dump is
    # provable in seconds, with no branch created and nothing charged for.
    --verify-only) VERIFY_ONLY=1; shift ;;
    # --date ADDED the same day. `ls -t` picks the newest, which is right for the
    # nightly drill and useless for the actual question that came up: are the
    # 2026-07-30 and 2026-07-31 dumps (138K and 1.2M, against 15-17M from 08-02
    # onward) genuine early-buildout snapshots or truncated ones.
    --date)        [ $# -ge 2 ] || { echo "FAIL: --date needs YYYYMMDD" >&2; exit 2; }
                   WANT_DATE="$2"; shift 2 ;;
    --identity)    [ $# -ge 2 ] || { echo "FAIL: --identity needs a path" >&2; exit 2; }
                   IDENTITY="$2"; shift 2 ;;
    -h|--help)     sed -n '2,60p' "$0"; exit 0 ;;
    *)             echo "FAIL: unknown argument '$1'" >&2; exit 2 ;;
  esac
done

say()  { print -r -- "$*"; }
step() { print -r -- ""; print -r -- "== $*"; }
die()  { print -r -- ""; print -r -- "REHEARSAL FAILED: $*" >&2; exit 1; }

BRANCH_ID=""
WORKDIR=""
cleanup() {
  # Runs on every exit path, including a failure or a Ctrl-C. Plaintext first:
  # if only one of the two teardowns can happen, it must be the one holding
  # production data in the clear.
  if [ -n "$WORKDIR" ] && [ -d "$WORKDIR" ]; then
    rm -rf "$WORKDIR"
    say "  teardown: decrypted dump removed"
  fi
  if [ -n "$BRANCH_ID" ]; then
    if [ "$KEEP_BRANCH" -eq 1 ]; then
      say "  teardown: KEEPING branch $BRANCH_ID (--keep-branch)."
      say "            It holds a copy of production. Delete it when you are done:"
      say "            $NEONCTL branches delete $BRANCH_ID --project-id $PROJECT_ID"
    elif "$NEONCTL" branches delete "$BRANCH_ID" --project-id "$PROJECT_ID" >/dev/null 2>&1; then
      say "  teardown: branch $BRANCH_ID deleted"
    else
      # Loud, and deliberately not a silent success: an orphaned branch is a
      # copy of production sitting in the vendor account, and it is also a bill.
      say "  teardown: COULD NOT DELETE branch $BRANCH_ID — delete it by hand:" >&2
      say "            $NEONCTL branches delete $BRANCH_ID --project-id $PROJECT_ID" >&2
    fi
  fi
}
trap cleanup EXIT INT TERM

# ── PHASE 0: preflight. Nothing is created and nothing is charged for until
#    every one of these passes. Ordered cheapest-and-most-likely-missing first.
step "phase 0: preflight"

for tool in age psql neonctl; do
  case "$tool" in
    neonctl) [ -x "$NEONCTL" ] || die "neonctl not found at $NEONCTL (run npm install in mcp-server/)" ;;
    *) command -v "$tool" >/dev/null 2>&1 || die "$tool is not on PATH. brew install $tool" ;;
  esac
  say "  ok    $tool present"
done

# THE KEY CHECK, FIRST AND LOUD. This is the step that cannot be worked around,
# and the failure message has to tell a human who is holding a paper key at 2am
# exactly what to do, not just that something is missing.
if [ ! -f "$IDENTITY" ]; then
  print -r -- "" >&2
  print -r -- "REHEARSAL CANNOT RUN: no age private key at $IDENTITY" >&2
  print -r -- "" >&2
  print -r -- "  backups-public-key.txt in this repo ENCRYPTS ONLY. It cannot decrypt anything." >&2
  print -r -- "  The private key normally lives at ~/.config/carr/age-key.txt (mode 600), with" >&2
  print -r -- "  an offline copy per secrets-inventory.md." >&2
  print -r -- "" >&2
  print -r -- "  Supply it one of two ways:" >&2
  print -r -- "    ./run.sh restore-rehearse --identity /path/to/age-key.txt" >&2
  print -r -- "    CARR_AGE_IDENTITY=/path/to/age-key.txt ./run.sh restore-rehearse" >&2
  print -r -- "" >&2
  print -r -- "  If NO copy of that key can be found, the dumps in backups/ are already" >&2
  print -r -- "  unrecoverable and that is the emergency, not this message." >&2
  exit 1
fi
say "  ok    age identity present at $IDENTITY"

# Permission check on the identity, advisory. A 644 private key is not a reason
# to refuse a restore rehearsal, but it is a reason to say something out loud.
IDENT_MODE="$(stat -f '%Lp' "$IDENTITY" 2>/dev/null || echo '???')"
case "$IDENT_MODE" in
  600|400) : ;;
  *) say "  WARN  identity file mode is $IDENT_MODE, expected 600 — chmod 600 $IDENTITY" ;;
esac

# Newest dump. `ls -t` matches how backup-dump.sh prunes, so "newest" means the
# same thing in both scripts.
if [ -n "$WANT_DATE" ]; then
  DUMP="$REPO/backups/carr-$WANT_DATE.sql.age"
  [ -f "$DUMP" ] || die "no dump for $WANT_DATE. Present: $(ls "$REPO"/backups/carr-*.sql.age 2>/dev/null | xargs -n1 basename 2>/dev/null | tr '\n' ' ')"
  say "  ok    selected dump: $(basename "$DUMP") (--date $WANT_DATE)"
else
  DUMP="$(ls -t "$REPO"/backups/carr-*.sql.age 2>/dev/null | head -1)"
  [ -n "$DUMP" ] || die "no backups/carr-*.sql.age files exist. Run ./bin/backup-dump.sh first."
  say "  ok    newest dump: $(basename "$DUMP")"
fi

# Size, in exact bytes. `du -h` used to report this and floors at the 4K block
# size, so it printed "4.0K" for the 200-byte corrupt backup of 2026-08-07 — the
# one number that would have exposed the failure, rounded up out of visibility.
DUMP_BYTES="$(stat -f%z "$DUMP")"
say "  ok    size: $DUMP_BYTES bytes"
if [ "$DUMP_BYTES" -lt "$MIN_DUMP_BYTES" ]; then
  # The floor gates the EXPENSIVE path only. --verify-only costs nothing and is
  # precisely how you examine a suspiciously small dump, so refusing to look at
  # one would be the check preventing its own use. It warns and proceeds; the
  # structure test below is what actually rules.
  if [ "$VERIFY_ONLY" -eq 1 ]; then
    say "  WARN  $DUMP_BYTES bytes is under the $MIN_DUMP_BYTES-byte floor — verifying anyway"
  else
    die "$(basename "$DUMP") is $DUMP_BYTES bytes, under the $MIN_DUMP_BYTES-byte floor. This is not a usable dump; nothing was created."
  fi
fi

# Is it actually an age file? The header is a plain ASCII line, so this is a
# free check that catches a truncated or half-written dump before we spend a
# branch on it.
if ! head -c 21 "$DUMP" | grep -q 'age-encryption.org/v1'; then
  die "$DUMP does not start with the age header — it is not an age file (truncated write?)"
fi
say "  ok    age header intact"

# Freshness. NOT a failure: an old dump still deserves to be rehearsed, and in
# fact an old dump is when you most want to know it works. But the gap in
# backups/ (no 2026-08-01) is exactly the silent-skip this warns about.
#
# FRESHNESS IS READ FROM THE FILENAME, NOT THE MTIME (changed 2026-08-07).
# mtime answers "when was this file last written to this disk", which is a
# different question and stopped being a usable proxy on 2026-08-06: the ORDER
# 42b history purge rewrote backups/ and stamped every dump from 07-30 through
# 08-06 with the same mtime, 02:05 that morning. This check then reported the
# 2026-07-31 dump as "28h old". An instrument that measures the last git
# operation instead of the backup is worse than no instrument, because it still
# prints a confident number.
#
# backup-dump.sh names every dump from `date -u +%Y%m%d` at the moment it runs,
# so the STAMP IN THE NAME is the taken-on date, and it survives a copy, a
# restore, or a history rewrite. Age is measured from midnight UTC of that date,
# which reads up to a couple of hours young against a 2:33am run — immaterial
# against a 26h threshold on a daily cadence, and written down here rather than
# left for someone to rediscover.
DUMP_STAMP="${${DUMP:t}#carr-}"; DUMP_STAMP="${DUMP_STAMP%%.*}"
DUMP_EPOCH="$(date -j -u -f '%Y%m%d %H%M%S' "$DUMP_STAMP 000000" +%s 2>/dev/null || true)"
if [ -z "${DUMP_EPOCH:-}" ]; then
  say "  WARN  no date stamp readable from $(basename "$DUMP") — falling back to mtime"
  DUMP_EPOCH="$(stat -f %m "$DUMP")"
else
  # The disagreement is worth one line: it is the tell that a dump was rewritten
  # after it was taken, and it is what made this check lie in the first place.
  MTIME_EPOCH="$(stat -f %m "$DUMP")"
  DRIFT_H=$(( (MTIME_EPOCH - DUMP_EPOCH) / 3600 ))
  [ "$DRIFT_H" -lt 0 ] && DRIFT_H=$(( 0 - DRIFT_H ))
  if [ "$DRIFT_H" -gt 48 ]; then
    say "  note  mtime sits ${DRIFT_H}h off the name's date — this file was rewritten after"
    say "        it was taken (a checkout, a restore, or the ORDER 42b purge). Harmless;"
    say "        noted because mtime is no longer what freshness reads."
  fi
fi
DUMP_AGE_H=$(( ( $(date +%s) - DUMP_EPOCH ) / 3600 ))
if [ -n "$WANT_DATE" ]; then
  say "  ok    dump is ${DUMP_AGE_H}h old (freshness not gated: --date asked for this one)"
elif [ "$DUMP_AGE_H" -gt "$STALE_HOURS" ]; then
  say "  WARN  newest dump is ${DUMP_AGE_H}h old (>${STALE_HOURS}h). The nightly chain is"
  say "        skipped whenever the Mac sleeps through 2am — check out/nightly.log."
else
  say "  ok    dump is ${DUMP_AGE_H}h old"
fi

# ── VERIFY-ONLY: structure, not a restore. Nothing on Neon is touched, so this
#    exits before the reachability checks below.
#
#    WHAT IT PROVES AND WHAT IT DOES NOT. A pg_dump writes a header line and, as
#    its last line, an explicit completion footer. The footer is only reached if
#    pg_dump ran to the end, so its ABSENCE is proof of truncation. Its presence
#    proves the file is a complete pg_dump, which is not the same as proving the
#    dump will load — only phase 3 proves that. This rung is cheap and
#    conclusive in one direction, which is exactly what a triage check should be.
if [ "$VERIFY_ONLY" -eq 1 ]; then
  step "verify: decrypt and read the dump's structure (nothing created)"
  WORKDIR="$(mktemp -d "${TMPDIR:-/tmp}/carr-verify.XXXXXX")"
  chmod 700 "$WORKDIR"
  PLAIN="$WORKDIR/dump.sql"
  if ! age --decrypt -i "$IDENTITY" "$DUMP" > "$PLAIN" 2>"$WORKDIR/verify.err"; then
    say "$(cat "$WORKDIR/verify.err")" >&2
    die "$(basename "$DUMP") did not decrypt. THIS IS THE FINDING."
  fi
  PLAIN_BYTES="$(stat -f%z "$PLAIN")"
  say "  ok    decrypted: $PLAIN_BYTES bytes of SQL"

  HEAD_OK=0; FOOT_OK=0
  head -5 "$PLAIN" | grep -q 'PostgreSQL database dump' && HEAD_OK=1
  tail -5 "$PLAIN" | grep -q 'PostgreSQL database dump complete' && FOOT_OK=1
  N_TABLES="$(grep -c '^CREATE TABLE ' "$PLAIN" || true)"
  N_COPY="$(grep -c '^COPY ' "$PLAIN" || true)"
  say "  ok    CREATE TABLE statements: $N_TABLES"
  say "  ok    COPY (data) blocks:      $N_COPY"
  [ "$HEAD_OK" -eq 1 ] && say "  ok    pg_dump header present" \
                       || say "  FAIL  pg_dump header MISSING"
  [ "$FOOT_OK" -eq 1 ] && say "  ok    completion footer present — pg_dump ran to the end" \
                       || say "  FAIL  completion footer MISSING — this dump is TRUNCATED"

  say ""
  if [ "$HEAD_OK" -eq 1 ] && [ "$FOOT_OK" -eq 1 ] && [ "$N_TABLES" -gt 0 ]; then
    say "VERIFY PASS — $(basename "$DUMP") is a structurally complete pg_dump."
    say "Complete is not the same as loadable: drop --verify-only for the real rehearsal."
    exit 0
  fi
  die "$(basename "$DUMP") is NOT a complete pg_dump. Treat it as no backup at all."
fi

# Production reachability + the default branch id, which teardown checks against.
PROD_DEFAULT_ID="$("$NEONCTL" branches list --project-id "$PROJECT_ID" --output json 2>/dev/null \
  | python3 -c 'import json,sys; print(next((b["id"] for b in json.load(sys.stdin) if b.get("default")), ""))' 2>/dev/null)"
[ -n "$PROD_DEFAULT_ID" ] || die "cannot list Neon branches — is neonctl authenticated? ($NEONCTL auth)"
say "  ok    Neon reachable; default branch is $PROD_DEFAULT_ID"

# The production DSN, derived INSIDE this process. Same pattern as
# backup-dump.sh and tools/db-tap.py, and for the same two reasons: the harness
# classifier refuses a Bash command containing $(...), and a DSN that never
# reaches a shell argument never reaches a shell history either. Never printed.
PROD_URL="$("$NEONCTL" connection-string "$PROD_BRANCH" --project-id "$PROJECT_ID" \
            --role-name neondb_owner 2>/dev/null)"
[ -n "$PROD_URL" ] || die "could not obtain the production connection string"
PROD_HOST="${${PROD_URL#*@}%%/*}"; PROD_HOST="${PROD_HOST%%\?*}"
say "  ok    production endpoint resolved (${PROD_HOST%%.*}…)"

if [ "$PREFLIGHT_ONLY" -eq 1 ]; then
  say ""
  say "PREFLIGHT OK — every check that runs before anything is created has passed."
  say "Nothing was created, decrypted or charged for. Drop --preflight for the real rehearsal."
  exit 0
fi

# ── PHASE 1: production baseline, READ ONLY.
# Taken BEFORE the branch exists so the numbers are not measured against a
# database we just influenced, and taken through a read-only session so the
# server enforces what the comment promises.
step "phase 1: production row counts (read-only session)"

# Exact counts for every base table in one round trip. query_to_xml is the
# standard way to run count(*) per table without generating SQL in the shell —
# no table name ever crosses a shell boundary, so nothing here is injectable by
# a table someone names creatively.
COUNT_SQL="select table_name || '|' || (xpath('/row/c/text()',
             query_to_xml(format('select count(*) as c from %I.%I', table_schema, table_name),
                          false, true, '')))[1]::text
           from information_schema.tables
          where table_schema = 'public' and table_type = 'BASE TABLE'
          order by table_name"

WORKDIR="$(mktemp -d "${TMPDIR:-/tmp}/carr-restore-rehearse.XXXXXX")"
chmod 700 "$WORKDIR"
PROD_COUNTS="$WORKDIR/prod-counts.txt"

# default_transaction_read_only=on is the guard, not the comment above it. Any
# write against production in this session fails at the server.
if ! PGOPTIONS="-c default_transaction_read_only=on" \
     psql "$PROD_URL" -v ON_ERROR_STOP=1 -At -c "$COUNT_SQL" > "$PROD_COUNTS" 2>"$WORKDIR/prod.err"; then
  say "$(cat "$WORKDIR/prod.err")" >&2
  die "could not read production row counts"
fi
PROD_TABLES=$(wc -l < "$PROD_COUNTS" | tr -d ' ')
PROD_ROWS=$(awk -F'|' '{s+=$2} END {print s+0}' "$PROD_COUNTS")
[ "$PROD_TABLES" -gt 0 ] || die "production reported zero tables — refusing to rehearse against nothing"
say "  ok    $PROD_TABLES tables, $PROD_ROWS rows in production right now"

# ── PHASE 2: the throwaway branch.
step "phase 2: throwaway branch"
BRANCH_NAME="restore-rehearse-$(date -u +%Y%m%dT%H%M%SZ)"
BRANCH_JSON="$("$NEONCTL" branches create --project-id "$PROJECT_ID" --name "$BRANCH_NAME" \
               --output json 2>"$WORKDIR/branch.err")"
BRANCH_ID="$(print -r -- "$BRANCH_JSON" | python3 -c 'import json,sys; d=json.load(sys.stdin); b=d.get("branch",d); print(b.get("id",""))' 2>/dev/null)"
if [ -z "$BRANCH_ID" ]; then
  say "$(cat "$WORKDIR/branch.err")" >&2
  die "could not create the rehearsal branch"
fi
# Guard 4. If this ever trips, the create step returned something that is not a
# new branch, and the trap is about to delete whatever it names.
[ "$BRANCH_ID" != "$PROD_DEFAULT_ID" ] || { BRANCH_ID=""; die "branch create returned the DEFAULT branch id — refusing to continue or delete"; }
say "  ok    created $BRANCH_NAME ($BRANCH_ID)"

# ── PHASE 3: restore into a NEW database on that branch.
# The branch inherits production's copy of neondb, so restoring there would
# collide with existing objects and prove nothing. A fresh database is a genuine
# empty target and costs one statement.
step "phase 3: restore"
BRANCH_ADMIN_URL="$("$NEONCTL" connection-string "$BRANCH_ID" --project-id "$PROJECT_ID" \
                    --role-name neondb_owner 2>/dev/null)"
[ -n "$BRANCH_ADMIN_URL" ] || die "could not obtain the branch connection string"
BRANCH_HOST="${${BRANCH_ADMIN_URL#*@}%%/*}"; BRANCH_HOST="${BRANCH_HOST%%\?*}"

# Guard 2, the one that matters most. Different branch, different compute
# endpoint, different hostname. Same host means we are about to write to
# production and we stop instead.
if [ "$BRANCH_HOST" = "$PROD_HOST" ]; then
  die "the branch DSN resolves to the SAME HOST as production — refusing to restore anywhere near it"
fi
say "  ok    branch endpoint is a different host from production"

if ! psql "$BRANCH_ADMIN_URL" -v ON_ERROR_STOP=1 -q -c "create database $RESTORE_DB" >/dev/null 2>"$WORKDIR/createdb.err"; then
  say "$(cat "$WORKDIR/createdb.err")" >&2
  die "could not create the $RESTORE_DB database on the branch"
fi
RESTORE_URL="$("$NEONCTL" connection-string "$BRANCH_ID" --project-id "$PROJECT_ID" \
               --role-name neondb_owner --database-name "$RESTORE_DB" 2>/dev/null)"
[ -n "$RESTORE_URL" ] || die "could not obtain the $RESTORE_DB connection string"
say "  ok    empty database $RESTORE_DB created on the branch"

# THE DECRYPT. Piped straight into psql: the plaintext dump exists only in a
# pipe, never as a file, so there is nothing to forget to delete.
#
# BOTH sides of the pipe append to the SAME stderr file, and that is not tidiness.
# The first run of this script (2026-08-02, with a deliberately wrong identity)
# captured psql's stderr only, so age's "unknown identity type" scrolled past
# while the diagnostic tail printed an empty file — the script reported a failure
# and then showed nothing about it. The single most useful line this can ever
# print is age's "no identity matched any of the recipients", so it goes where
# the tail will find it.
#
# pipefail matters just as much: without it the pipeline's status is psql's, and
# psql exits 0 on an empty stdin. A failed decrypt would have loaded nothing and
# passed.
# THE ACL FILTER, and why it is a filter rather than ON_ERROR_STOP=0.
#
# FOUND BY THE FIRST REAL RUN, 2026-08-02: the restore died on
#   ERROR: permission denied to change default privileges
# `bin/backup-dump.sh` calls plain `pg_dump` with no --no-owner/--no-acl, so
# every dump carries ALTER DEFAULT PRIVILEGES / GRANT / REVOKE / OWNER TO
# statements naming the role that owned the source database. Restoring into a
# fresh database on a branch, those roles are not ours to act for, so the first
# one errors and ON_ERROR_STOP=1 abandons the whole load. The DATA was never the
# problem; the permission grammar wrapped around it was.
#
# backup-dump.sh is fixed so future dumps carry none of this. That does NOT help
# the dumps we already hold, and those are the only backups that exist — which is
# exactly the situation a restore has to survive. So the stream is filtered here.
#
# ON_ERROR_STOP STAYS 1. Dropping it would have been one word and would have made
# every future failure silent — a restore that skips a broken COPY and reports
# success is worse than one that fails loudly, and is the same false-comfort
# defect this whole drill was built to expose. Filtering removes exactly the
# statement classes we know cannot apply and leaves every other error fatal.
#
# The count assertion in phase 4 is what proves the filter did not eat data: ACL
# statements move no rows, so if stripping them changed a single count, the
# comparison fails.
ACL_FILTER='/^(ALTER DEFAULT PRIVILEGES|GRANT |REVOKE |ALTER .* OWNER TO |COMMENT ON EXTENSION )/d'

say "  decrypting and loading (this is the slow step) ..."
say "  note: stripping ownership/ACL statements the source dump carries (see comment)"
set -o pipefail
if ! age --decrypt -i "$IDENTITY" "$DUMP" 2>>"$WORKDIR/restore.err" \
     | sed -E "$ACL_FILTER" \
     | psql "$RESTORE_URL" -v ON_ERROR_STOP=1 -q >"$WORKDIR/restore.out" 2>>"$WORKDIR/restore.err"; then
  set +o pipefail
  say ""
  say "  --- decrypt/restore output (last 40 lines) ---" >&2
  tail -40 "$WORKDIR/restore.err" >&2
  die "the dump did not decrypt and load. THIS IS THE FINDING — a backup that will not restore is not a backup."
fi
set +o pipefail
say "  ok    dump decrypted and loaded"

# ── PHASE 4: the assertion. This is the whole point; everything above is setup.
step "phase 4: row counts, restored vs production"
REST_COUNTS="$WORKDIR/restored-counts.txt"
if ! psql "$RESTORE_URL" -v ON_ERROR_STOP=1 -At -c "$COUNT_SQL" > "$REST_COUNTS" 2>"$WORKDIR/rest.err"; then
  say "$(cat "$WORKDIR/rest.err")" >&2
  die "could not read row counts back out of the restored database"
fi

# WHY THE GATE IS SHAPED THIS WAY, because the obvious shape is wrong. The dump
# is a snapshot taken at 2am and production has been moving all day, so a strict
# per-table equality check would go red every single run on nothing but normal
# churn — the "an alarm that fires every day trains people to stop reading
# alarms" failure bin/nightly.sh already had to fix once. Per-table deltas are
# therefore REPORTED, not gated. What IS gated:
#   FAIL  a table present in production is missing from the restore
#   FAIL  a table with rows in production restored with zero rows
#   FAIL  the restored total is below FLOOR_PCT% of production's total
# Those three catch the failure modes that actually exist — a dependency error
# that skips a table, a truncated dump, a half-written file — without pretending
# a moving database should match a snapshot to the row.
# NO SORT FUNCTION IS USED HERE, deliberately. The first draft called asorti(),
# which is a gawk extension: macOS ships the one-true-awk (version 20200816) and
# died with "calling undefined function asorti" on the first fixture run. Both
# input files already arrive sorted — the counting query ends in `order by
# table_name` — so walking them in file order gives sorted output for free.
# Production's tables print first, then any table the dump carries that
# production no longer has. Row counts are summed with explicit counters rather
# than length(array), for the same portability reason.
awk -F'|' -v floor="$FLOOR_PCT" -v core="$CORE_TABLES" '
  FNR==NR { prod[$1]=$2; ptot+=$2; npro++; ord[++n]=$1; next }
          { rest[$1]=$2; rtot+=$2; nres++; if (!($1 in prod)) ord[++n]=$1 }
  END {
    fails = 0
    split(core, c, " "); for (i in c) is_core[c[i]] = 1
    # HOW FAR BEHIND THE SCHEMA IS, in migrations. Both sides already count
    # schema_migrations as an ordinary table, so this costs no extra query: the
    # dump carries every migration applied before it was taken, production carries
    # every one applied since. The difference is the licence to treat a missing
    # table or an empty one as expected rather than as a failure.
    behind = 0
    if (("schema_migrations" in prod) && ("schema_migrations" in rest))
      behind = prod["schema_migrations"] - rest["schema_migrations"]
    if (behind > 0)
      printf "  NOTE: %d migration(s) were applied AFTER this dump was taken.\n        Tables and rows created by them cannot be in it — reported as expected, not failed.\n\n", behind

    printf "  %-28s %12s %12s %10s\n", "table", "production", "restored", "delta"
    printf "  %-28s %12s %12s %10s\n", "----------------------------", "------------", "------------", "----------"
    for (i = 1; i <= n; i++) {
      t = ord[i]
      p = (t in prod) ? prod[t] : -1
      r = (t in rest) ? rest[t] : -1
      if (p >= 0 && r < 0) {
        # SCHEMA EVOLUTION IS NOT DATA LOSS, and the first real run got this wrong.
        # On 2026-08-02 the drill reported 8 failures against a dump that was
        # perfectly intact: 25 migrations (0032..0056) were applied in the ten
        # hours AFTER the 08:50 dump, so candidate_pool, contact_state,
        # loop_domain, vendor_category, vendor_disposition,
        # vendor_relationship_level and deprecation did not exist yet. The
        # giveaway was in the table itself — prospect_pool carried 9860 rows in
        # the dump and candidate_pool carried 9860 in production, one table
        # renamed by 0048. A restore cannot produce a table its dump predates,
        # and calling that a failure is how a weekly alarm teaches everyone to
        # ignore it. `behind` is production_migrations - restored_migrations,
        # read from the schema_migrations counts both sides already report.
        if (behind > 0) {
          printf "  %-28s %12d %12s %10s   expected: table postdates this dump\n", t, p, "-", "-"
        } else {
          printf "  %-28s %12d %12s %10s   FAIL missing from the restore\n", t, p, "-", "-"
          fails++
        }
        continue
      }
      if (p < 0 && r >= 0) {
        # Expected and harmless: a migration landed AFTER this dump was taken,
        # so production dropped a table the dump still carries. Worth printing,
        # never worth failing.
        printf "  %-28s %12s %12d %10s   note: in the dump, not in production\n", t, "-", r, "-"
        continue
      }
      d = r - p
      if (p > 0 && r == 0) {
        # Same discrimination, one level down: the TABLE existed in the dump but
        # every ROW in it is newer. record_flag is the worked example — created by
        # 0001, still empty at 08:50, and its first 8 rows written that evening.
        # A failed COPY and a table nobody had written to yet look identical from
        # here, so when the dump is known to be schema-behind this is reported and
        # not gated. The aggregate floor below is what still catches a genuinely
        # truncated dump, and it is the right instrument for that.
        if (behind > 0) {
          printf "  %-28s %12d %12d %10d   expected: rows postdate this dump\n", t, p, r, d
        } else {
          printf "  %-28s %12d %12d %10d   FAIL restored EMPTY\n", t, p, r, d
          fails++
        }
        continue
      }
      mark = ""
      if (is_core[t] && r == 0 && p == 0) mark = "   note: core table, empty on both sides"
      else if (d != 0) mark = "   (drift — the dump is a snapshot; production moved)"
      printf "  %-28s %12d %12d %10d%s\n", t, p, r, d, mark
    }

    # The core spine must have restored at all. Separate from the per-table loop
    # because a core table missing entirely from BOTH sides is its own alarm:
    # it means this dump predates the record layer, not that the restore failed.
    for (t in is_core) {
      if (!(t in rest)) { printf "  FAIL core table %s is absent from the restored database\n", t; fails++ }
    }

    printf "\n  totals: production %d rows across %d tables · restored %d rows across %d tables\n",
           ptot, npro, rtot, nres
    if (ptot > 0) {
      pct = 100.0 * rtot / ptot
      printf "  restored total is %.1f%% of production (floor %d%%)\n", pct, floor
      if (pct < floor) { printf "  FAIL restored total is below the %d%% floor — the dump looks truncated\n", floor; fails++ }
    }
    printf "AWKFAILS=%d\n", fails
  }
' "$PROD_COUNTS" "$REST_COUNTS" > "$WORKDIR/compare.txt"

grep -v '^AWKFAILS=' "$WORKDIR/compare.txt"
FAILS="$(sed -n 's/^AWKFAILS=//p' "$WORKDIR/compare.txt")"
: "${FAILS:=1}"

say ""
if [ "$FAILS" -eq 0 ]; then
  say "================================================================"
  say "  RESTORE REHEARSAL: PASS"
  say "  $(basename "$DUMP") decrypted, loaded and reconciled against production."
  say "  The backups are restorable. Proven, not assumed."
  say "================================================================"
  exit 0
else
  say "================================================================" >&2
  say "  RESTORE REHEARSAL: FAIL ($FAILS assertion(s) failed)" >&2
  say "  $(basename "$DUMP") did NOT come back intact. Read the table above." >&2
  say "  Do not treat these dumps as a working backup until this is green." >&2
  say "================================================================" >&2
  exit 1
fi
