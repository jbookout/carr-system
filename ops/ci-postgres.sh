#!/bin/bash
# ci-postgres.sh — the throwaway Postgres the migration class needs, on a machine
# that has no Docker.
#
# WHY THIS FILE EXISTS. ci.yml used to get its database from a `services:` block:
# a postgres:17 container GitHub started, on loopback, that died with the job.
# Service containers are a Linux-runner feature — they are Docker, and a
# self-hosted macOS runner has no Docker daemon. Moving the checks job onto Joe's
# Mac therefore had to replace the container with something that keeps the two
# properties the container was chosen for:
#
#   1. THROWAWAY. A whole cluster is initdb'd into the runner's temp directory,
#      used once, and deleted. Nothing survives the job, so no state can leak
#      from one run into the next the way a shared long-lived server would let it.
#   2. LOOPBACK ONLY. listen_addresses is 127.0.0.1 and nothing else, which is
#      what lets ops/ci.sh keep enforcing its "refuse any DSN that is not
#      loopback" rule with no override: production is not reachable from here
#      even if someone tried. That rule is the reason the migration class is
#      allowed to apply every pending migration without asking.
#
# THIS IS PROVISIONING, NOT CHECK LOGIC. Rule a8c55a47 says a manual path and an
# automated path doing the same job must be the same code, and it is why no check
# is written in ci.yml — every check lives in ops/ci.sh. Nothing here asserts
# anything about the repo; it hands ops/ci.sh a database and gets out of the way,
# exactly as the `services:` block it replaces did.
#
# A PORT, NOT A SOCKET. ops/ci.sh matches the DSN host against localhost and
# 127.0.0.1, so the DSN has to be TCP. The port is chosen free at start time
# rather than fixed at 5432, because more than one runner can be registered on
# the same Mac and two concurrent jobs must not land on the same port — and
# because Joe's own Postgres may already hold 5432.
#
# 127.0.0.1, NEVER THE NAME "localhost", in the DSN this prints. On macOS
# localhost resolves to ::1 first; a server told to listen on 127.0.0.1 is not
# there, and the connection is refused with a message that reads like the server
# failed to start. Both spellings satisfy ops/ci.sh, so the one that works is the
# only one worth emitting.
#
# TWO OUTPUT SHAPES, BECAUSE THE TWO CALLERS PARSE DIFFERENTLY AND ONE OF THEM
# PARSES SILENTLY. GitHub reads $GITHUB_ENV as bare KEY=value lines; hand it
# `export KEY='value'` and it does not error, it creates a variable actually
# named "export KEY" holding a value that still has the quotes around it — so the
# job would sail past this step and fail later in ops/ci.sh with an empty DSN and
# no hint as to why. So the default output is the bare form GitHub wants, and
# --export is the shell-eval form a human at a terminal wants.
#
# Usage:
#   ops/ci-postgres.sh start >> "$GITHUB_ENV"        # in the workflow
#   eval "$(ops/ci-postgres.sh start --export)"      # at a terminal
#   ops/ci-postgres.sh stop                          # reads CARR_CI_PGDATA, always succeeds

set -uo pipefail

# Homebrew does not link postgresql@17 into /opt/homebrew/bin, because it is a
# versioned formula and linking it would fight whatever other major version is
# installed. Both Apple Silicon and Intel prefixes are searched so this does not
# quietly depend on which Mac is running it.
for d in \
  /opt/homebrew/opt/postgresql@17/bin \
  /usr/local/opt/postgresql@17/bin \
  /opt/homebrew/bin \
  /usr/local/bin
do
  [ -x "$d/initdb" ] && PATH="$d:$PATH" && break
done
export PATH

die() { echo "ci-postgres: $*" >&2; exit 1; }

# LC_ALL=C, OR THE SERVER DOES NOT START ON MACOS. Observed 2026-08-18 on
# Postgres 17.11 (Homebrew, arm64): postmaster dies during startup with "FATAL:
# postmaster became multithreaded during startup / HINT: Set the LC_ALL
# environment variable to a valid locale." macOS's locale lookup spawns a thread
# when the environment leaves the locale unresolved, and the postmaster refuses
# to fork a backend from a multithreaded process. The GitHub-hosted Linux
# container never hit this, so it is new surface that came with the move to the
# Mac and not something the old workflow was silently relying on. C is also what
# initdb builds the cluster with below, so the two agree.
export LC_ALL=C LANG=C

cmd_start() {
  local export_form=0
  [ "${1:-}" = "--export" ] && export_form=1
  command -v initdb >/dev/null 2>&1 || die "no initdb on PATH — brew install postgresql@17"

  local base="${RUNNER_TEMP:-${TMPDIR:-/tmp}}"
  local pgdata; pgdata="$(mktemp -d "$base/carr-ci-pg.XXXXXXXX")" || die "cannot make a data directory"

  # A FREE PORT FROM THE KERNEL, not a guess. Binding port 0 and reading back
  # what was assigned is the only way to ask "which port is free" that does not
  # race against every other process on the machine for a fixed number.
  local port
  port="$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()')" \
    || die "cannot find a free port"

  # scram, not trust. The cluster listens on loopback, but every other account on
  # this Mac is also on loopback — trust auth would make this database writable by
  # any local process for as long as a CI job is running. The password is the same
  # throwaway string the postgres:17 service container used, and it dies with the
  # cluster.
  local pwfile="$pgdata.pw"
  printf 'carr_ci\n' > "$pwfile"
  chmod 600 "$pwfile"

  if ! initdb -D "$pgdata" -U carr_ci --auth=scram-sha-256 --pwfile="$pwfile" \
       --encoding=UTF8 --locale=C >"$pgdata.initdb.log" 2>&1; then
    tail -20 "$pgdata.initdb.log" >&2
    rm -f "$pwfile"
    rm -rf "$pgdata"
    die "initdb failed"
  fi
  rm -f "$pwfile"

  # timezone=UTC IS LOAD-BEARING, not tidiness. The postgres:17 service container
  # ran UTC; a Homebrew cluster inherits the Mac's zone, which is US/Central.
  # tools/ops-record.py's _next_incident_ref counts today's incidents with
  # to_char(now(), 'YYYYMMDD') — evaluated by the SERVER — and then formats the
  # ref it writes from datetime.now(timezone.utc) — evaluated by the CLIENT. Those
  # two agree only where the server is UTC. On a CDT cluster, between 19:00 and
  # midnight local the counter counts the previous UTC day (finding none, so 01)
  # while the ref written is tomorrow's, so every incident in that window is
  # numbered 01 and the second one dies on incident_ref_key. Observed exactly that
  # on 2026-08-18 at 19:22 CDT, and it is why program3-incident-gate.py failed on
  # the Mac and passes on the hosted runner. Pinning the cluster to UTC reproduces
  # the container rather than papering over it; the underlying split between server
  # clock and client clock is a real defect in ops-record.py and is tracked
  # separately, because changing incident numbering is not this change's business.
  if ! pg_ctl -D "$pgdata" -l "$pgdata.server.log" -w -t 60 \
       -o "-p $port -c listen_addresses=127.0.0.1 -c unix_socket_directories=$pgdata -c timezone=UTC -c fsync=off -c full_page_writes=off" \
       start >/dev/null 2>&1; then
    tail -20 "$pgdata.server.log" >&2
    rm -rf "$pgdata"
    die "postgres did not start on port $port"
  fi

  # THE DATABASE ITSELF. initdb only leaves postgres/template0/template1 behind;
  # the name carr_ci came from the service container's POSTGRES_DB, and ops/ci.sh
  # loads db/schema.sql into whatever the DSN names. Created empty, because the
  # migration class's whole question is whether the committed structure loads and
  # the pending migrations apply on top of it.
  if ! PGPASSWORD=carr_ci createdb -h 127.0.0.1 -p "$port" -U carr_ci carr_ci >>"$pgdata.server.log" 2>&1; then
    tail -20 "$pgdata.server.log" >&2
    pg_ctl -D "$pgdata" -m immediate -w -t 30 stop >/dev/null 2>&1
    rm -rf "$pgdata"
    die "could not create the carr_ci database"
  fi

  # THE NEON ADMIN ROLE. Migration 0005 grants privilege bundles to
  # neondb_owner, which exists on every Neon project and on no vanilla Postgres.
  # Without it the migration set is not rehearsable anywhere except production,
  # which defeats the point of rehearsing. This is the same statement ci.yml ran
  # as its own step against the service container, moved here so the whole
  # "database the migration class expects" shape is defined in one place.
  if ! PGPASSWORD=carr_ci psql -h 127.0.0.1 -p "$port" -U carr_ci -d postgres -v ON_ERROR_STOP=1 -q -c \
       "do \$\$ begin
          if not exists (select 1 from pg_roles where rolname = 'neondb_owner') then
            create role neondb_owner;
          end if;
        end \$\$;" >>"$pgdata.server.log" 2>&1; then
    tail -20 "$pgdata.server.log" >&2
    pg_ctl -D "$pgdata" -m immediate -w -t 30 stop >/dev/null 2>&1
    rm -rf "$pgdata"
    die "could not create neondb_owner"
  fi

  # The marker below has to sit on the SAME line as the value — the scanner reads
  # the line it flagged, not the comment above it. This is the throwaway cluster's
  # own credential: it authenticates to a database that was created ninety lines
  # ago and is deleted when the job ends, and it is the same string the postgres:17
  # service container used before this file existed.
  local dsn="postgres://carr_ci:carr_ci@127.0.0.1:$port/carr_ci" # ci-secret-scan: allow — throwaway cluster credential, loopback only, dies with the job
  if [ "$export_form" = "1" ]; then
    echo "export CARR_CI_DATABASE_URL='$dsn'"
    echo "export CARR_CI_PGDATA='$pgdata'"
  else
    echo "CARR_CI_DATABASE_URL=$dsn"
    echo "CARR_CI_PGDATA=$pgdata"
  fi
}

# STOP NEVER FAILS THE JOB. This runs from an `if: always()` step whose entire
# purpose is cleanup; a nonzero exit here would turn a green CI run red for the
# sake of a temp directory. Anything it could not remove is reported and left.
cmd_stop() {
  local pgdata="${CARR_CI_PGDATA:-}"
  [ -n "$pgdata" ] || { echo "ci-postgres: no CARR_CI_PGDATA — nothing to stop" >&2; exit 0; }
  if [ -d "$pgdata" ]; then
    pg_ctl -D "$pgdata" -m immediate -w -t 30 stop >/dev/null 2>&1 \
      || echo "ci-postgres: pg_ctl stop did not report success for $pgdata" >&2
  fi
  rm -rf "$pgdata" "$pgdata.server.log" "$pgdata.initdb.log" 2>/dev/null \
    || echo "ci-postgres: could not fully remove $pgdata" >&2
  exit 0
}

case "${1:-}" in
  start) shift; cmd_start "$@" ;;
  stop)  cmd_stop ;;
  *) die "usage: ci-postgres.sh start [--export] | stop" ;;
esac
