#!/bin/bash
# ci-runner-guard.sh — refuse to run CI on a self-hosted runner that can reach
# CARR production.
#
# WHY THIS IS A SCRIPT AND NOT A PARAGRAPH IN A PR. A GitHub-hosted runner is a
# fresh VM that is destroyed afterwards, so "the job cannot see production" was
# free and nobody had to state it. A self-hosted runner is a long-lived process
# on a machine that DOES have production credentials sitting on its disk:
# ~/.config/carr/db.env is mode 600 and holds the live Neon DSNs and the Neon API
# key. If the runner is registered under Joe's own account, then every workflow
# run — including one from a branch pushed by any agent session — can read that
# file, and CI stops being a sandbox. The boundary is entirely a function of
# WHICH UNIX USER the runner service runs as, which is invisible in the workflow
# file and easy to get wrong once and never notice.
#
# So the boundary is asserted here, on every run, and a violation stops the job
# rather than producing a green tick from a runner that could have exfiltrated
# the database. Rule 88e9b5eb applies: this reports what is actually wrong, and
# it never downgrades a violation to a warning.
#
# THIS IS NOT A REPO CHECK. Rule a8c55a47 is about a manual path and an automated
# path doing the same job in two places; this has no manual counterpart, because
# Joe's own pre-push hook is SUPPOSED to run as Joe with his credentials present.
# Nothing here inspects the repository; it inspects the machine, and only the
# workflow calls it.

set -uo pipefail

fail=0
note() { printf '  %s\n' "$*" >&2; }

printf 'runner guard: user=%s home=%s\n' "$(id -un)" "${HOME:-<unset>}" >&2

# 1. THE SECRET STORE MUST BE UNREADABLE. Not "absent" — unreadable. Absent is
# also fine and produces the same pass, but the question that matters is whether
# THIS process can open it, which is the only thing an attacker in a workflow
# step would care about. Every operator secret lives in this one directory, so
# testing the directory rather than one file inside it is what keeps this honest
# when a new .env lands next week.
# The operator home directories are listed explicitly as well as the runner's
# own, because the interesting failure is precisely the one where the runner IS
# an operator account and $HOME already points at the secrets. Duplicates are
# collapsed so one misconfiguration is reported once.
CARR_SECRETS="${CARR_SECRET_DIR:-$HOME/.config/carr}"
seen=""
for d in "$CARR_SECRETS" /Users/booko/.config/carr /Users/dell/.config/carr; do
  [ -d "$d" ] || continue
  d="$(cd "$d" && pwd -P)" || continue
  case " $seen " in *" $d "*) continue ;; esac
  seen="$seen $d"
  # -r on the directory is not enough on its own: a readable directory with
  # unreadable files still leaks nothing. Test an actual open of each file.
  while IFS= read -r f; do
    if [ -r "$f" ]; then
      note "READABLE PRODUCTION SECRET: $f"
      fail=1
    fi
  done < <(find "$d" -maxdepth 1 -type f 2>/dev/null)
done

# 2. NO PRODUCTION CONNECTION STRINGS IN THE ENVIRONMENT. The runner service
# inherits the environment of whatever launched it, so a runner started by hand
# from a logged-in shell that had sourced db.env carries those values into every
# job forever. CARR_CI_* is the deliberate exception: those are set BY the
# workflow and point at the throwaway loopback cluster.
while IFS='=' read -r k _; do
  case "$k" in
    CARR_CI_*) continue ;;
    CARR_*|DATABASE_URL|*NEON*|PGHOST|PGUSER|PGPASSWORD|PGDATABASE)
      note "PRODUCTION-SHAPED VARIABLE IN THE RUNNER ENVIRONMENT: $k"
      fail=1 ;;
  esac
done < <(env)

# 3. NOT AS AN ADMIN. A runner in the admin group can sudo, and a workflow step
# that can sudo can read anything on the machine regardless of file modes, which
# would make check 1 decorative.
if id -Gn 2>/dev/null | tr ' ' '\n' | grep -qx admin; then
  note "RUNNER USER IS IN THE admin GROUP — it can sudo, so file modes do not bound it"
  fail=1
fi

if [ "$fail" -ne 0 ]; then
  cat >&2 <<'EOF'

REFUSING TO RUN. This self-hosted runner can reach CARR production, so a green
result from it would not mean what CI is supposed to mean.

The runner must run as its own unprivileged macOS user — not Joe's account, and
not any account in the admin group. ops/install-ci-runner.sh creates that user
and installs the service under it. See the SECURITY BOUNDARY section in that
file for what the runner is allowed to reach and why.
EOF
  exit 1
fi

echo "runner guard: no production credential reachable from this runner" >&2
