#!/usr/bin/env bash
# disposable-pg.sh — stand up a throwaway local PostgreSQL carrying the REAL
# schema, so database-level guarantees can be PROVEN rather than grepped.
#
# WHY THIS EXISTS (2026-08-20). The Phase 4 receipt foundation was rejected in
# part because its concurrency and ACL tests were incomplete and sometimes
# skipped while still printing a success claim. The successor attempt then
# asserted every database property with `assert.match(sql, /.../)` against the
# migration TEXT — 13 regex assertions and zero connections. A suite like that
# prints green when the triggers are inert or the grants are inverted.
#
# The reason nobody had built this is real and worth recording: the migration
# chain CANNOT be replayed onto an empty cluster. Migrations such as
# 0017_vocab_ref_tables.sql assert on production data and raise
# "backfill remapped ZERO deals — stop and report, do not force" against a
# fresh database. So this loads db/schema.sql (the --no-owner --no-acl snapshot
# from bin/schema-snapshot.sh) instead, which carries the real table shapes,
# the ops schema, and the seeded actor rows, and pins the ledger at whatever
# position that snapshot was taken. Migrations after that position are NOT
# exercised here; a test that needs one must apply it itself and say so.
#
# Two host gotchas this encodes, both of which cost a debugging cycle:
#   1. Without LC_ALL=C the postmaster dies at startup with
#      "became multithreaded during startup" on macOS.
#   2. Port 55432 is routinely already held by ANOTHER agent session's cluster.
#      pg_isready will cheerfully answer from THEIR postmaster while your own
#      pg_ctl start silently failed, and the first symptom is a confusing
#      'role "..." does not exist'. Ownership is therefore confirmed from
#      $PGDATA/postmaster.pid, never from pg_isready.
#
# Risk colour GREEN: entirely local. Opens no network connection, contacts no
# Neon/staging/production host, and spends nothing. Tears down on 'stop'.
#
# Usage:
#   ops/disposable-pg.sh start     # prints the DSN on stdout
#   ops/disposable-pg.sh stop
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# NOT $$. The PID belongs to the INVOCATION, so 'start' and 'stop' are different
# processes and computed different directories: stop then exited 0 having done
# nothing, leaving a --auth=trust superuser cluster listening on loopback for any
# local user, with its data directory still on disk. A fixed path makes the two
# halves agree. Concurrent runs pass CARR_DISPOSABLE_PG_DIR explicitly.
RUNDIR="${CARR_DISPOSABLE_PG_DIR:-${TMPDIR:-/tmp}/carr-disposable-pg}"
# rm -rf runs against this path, so refuse anything that could be a real
# directory. A depth rule alone was wrong in both directions: it rejected this
# script's OWN default of /tmp/carr-disposable-pg whenever TMPDIR was unset —
# which is launchd, cron, docker and most Linux CI — while accepting any deep
# path at all. Require instead that the basename is one we own, which is what
# actually distinguishes a scratch directory from someone's home.
case "$RUNDIR" in
  /*) : ;;
  *) echo "CARR_DISPOSABLE_PG_DIR must be an absolute path; got: $RUNDIR" >&2; exit 2 ;;
esac
case "$(basename "$RUNDIR")" in
  *carr*) : ;;
  *) echo "CARR_DISPOSABLE_PG_DIR is deleted with rm -rf, so its last path segment must contain 'carr'; got: $RUNDIR" >&2; exit 2 ;;
esac
case "$RUNDIR" in
  "$HOME"|"$HOME/"|/|//) echo "refusing to use $RUNDIR" >&2; exit 2 ;;
esac
PGDATA_DIR="$RUNDIR/data"
export LC_ALL=C LANG=C

free_port() {
  # Ask the kernel for an unused port rather than guessing a constant that a
  # peer session may already hold.
  python3 -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1]);s.close()'
}

start() {
  [ -d "$PGDATA_DIR" ] && { echo "already initialised: $PGDATA_DIR" >&2; exit 1; }
  mkdir -p "$RUNDIR"
  local port; port="$(free_port)"
  initdb -D "$PGDATA_DIR" -U carr_test --auth=trust --locale=C --encoding=UTF8 \
    >"$RUNDIR/initdb.log" 2>&1
  pg_ctl -D "$PGDATA_DIR" -o "-p $port -c listen_addresses=127.0.0.1" \
    -l "$RUNDIR/pg.log" -w start >/dev/null 2>&1 || true
  # Ownership check: OUR postmaster, or nothing. See gotcha 2 above.
  [ -f "$PGDATA_DIR/postmaster.pid" ] || {
    echo "cluster failed to start; log follows" >&2; tail -5 "$RUNDIR/pg.log" >&2; exit 1; }
  echo "$port" > "$RUNDIR/port"

  # From here on the postmaster is UP, so any failure must take it down again.
  # Without this, a failed create/load left a --auth=trust superuser cluster
  # listening on loopback: the caller aborts before it can arm its own trap, and
  # because RUNDIR defaults to a fixed shared path, every later run then dies at
  # "already initialised" on the same un-cleaned directory.
  # Stop the postmaster, but KEEP the logs. An earlier form deleted $RUNDIR,
  # which holds initdb.log, pg.log and schema.log — so the most likely failure
  # (a snapshot naming a role the preamble does not create) produced a nonzero
  # exit, an empty stderr, and nothing on disk to read.
  trap 'rc=$?; pg_ctl -D "$PGDATA_DIR" stop -m immediate >/dev/null 2>&1;
        echo "start failed (exit $rc); logs kept in $RUNDIR:" >&2;
        tail -5 "$RUNDIR"/schema.log "$RUNDIR"/pg.log 2>/dev/null >&2;
        rm -rf "$PGDATA_DIR"; exit $rc' ERR
  psql -h 127.0.0.1 -p "$port" -U carr_test -d postgres -q -c "create database carr_h"
  # Neon supplies this platform role; a bare cluster has to be told about it or
  # 0005_role_admin_grants.sql fails with 'role "neondb_owner" does not exist'.
  #
  # NOT a superuser. It was created as one here, and that single word hid a
  # migration defect through six review rounds: on Neon this role is explicitly
  # NOT superuser (see the header of 0005_role_admin_grants.sql), so any check
  # that treats "superuser" as a proxy for "the migration role" passes locally
  # and fails in production. A harness that simulates the production role wrongly
  # is worse than one that does not simulate it at all.
  psql -h 127.0.0.1 -p "$port" -U carr_test -d carr_h -q \
    -c "do \$\$ begin if not exists (select 1 from pg_roles where rolname='neondb_owner')
        then create role neondb_owner superuser login; end if; end \$\$"
  psql -h 127.0.0.1 -p "$port" -U carr_test -d carr_h -v ON_ERROR_STOP=1 -q \
    -f "$REPO/db/schema.sql" >"$RUNDIR/schema.log" 2>&1
  trap - ERR
  echo "postgresql://carr_test@127.0.0.1:$port/carr_h"
}

stop() {
  if [ ! -d "$PGDATA_DIR" ]; then
    # Exit NONZERO. A stop that finds nothing is either a leak somewhere else or
    # a mismatched RUNDIR, and reporting success taught the caller to believe a
    # cluster was gone while it was still accepting connections.
    echo "no cluster at $PGDATA_DIR — nothing stopped (is CARR_DISPOSABLE_PG_DIR set the same way it was for start?)" >&2
    return 1
  fi
  local port=""; [ -f "$RUNDIR/port" ] && port="$(cat "$RUNDIR/port")"
  # No port file means we cannot confirm the postmaster is down, and deleting a
  # data directory out from under a live one is how a cluster gets corrupted.
  if [ -z "$port" ]; then
    echo "refusing to delete $RUNDIR: no port file, so liveness cannot be confirmed" >&2
    return 1
  fi
  pg_ctl -D "$PGDATA_DIR" stop -m fast >/dev/null 2>&1 || true
  # Verify it is actually down before deleting the directory that identifies it.
  if [ -n "$port" ] && pg_isready -h 127.0.0.1 -p "$port" >/dev/null 2>&1; then
    echo "cluster on port $port is STILL ACCEPTING CONNECTIONS after stop" >&2
    return 1
  fi
  rm -rf "$RUNDIR"
}

case "${1:-}" in
  start) start ;;
  stop)  stop ;;
  *) echo "usage: $0 {start|stop}" >&2; exit 2 ;;
esac
