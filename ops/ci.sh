#!/bin/bash
# ci.sh — the ONE check script. Program 2's spine.
#
# RUN UNDER BASH OR NOT AT ALL. The class loop is `for c in $CLASS_ORDER`, which
# depends on bash word-splitting an unquoted string. zsh does not split there,
# so under zsh the loop runs ONCE with c set to the whole list, `check_$c`
# becomes one unknown command, every check is skipped -- and the script still
# reaches its own success line and prints "CI passed - every class green".
# Observed exactly that on 2026-08-13 from `zsh ops/ci.sh`: eight classes
# reported green, zero of them executed. A green CI that ran nothing is the
# worst possible failure for a promotion gate, and the shebang alone does not
# prevent it, because a shebang binds `./ops/ci.sh` and not `zsh ops/ci.sh` or
# `sh ops/ci.sh`. Re-exec rather than error: the caller's intent is always to
# run the checks, so silently doing the right thing beats refusing.
if [ -z "${BASH_VERSION:-}" ]; then
  exec /bin/bash "$0" "$@"
fi
#
# WHY ONE SCRIPT. Program 2's gate is "seeded failures in tests, migration, auth,
# binding, secret and dependency checks each block promotion." That needs the
# same checks to run in two places: a GitHub runner on every push, and Joe's Mac
# at pre-push time. Rule a8c55a47 says a manual path and an automated path doing
# the same job must be the same code. So neither the workflow file nor the git
# hook contains any check logic — both call this, and this is the only place a
# check is defined. A check added here appears in both callers for free, and
# cannot be present in one and missing from the other.
#
# WHY BOTH CALLERS EXIST AT ALL. For most of this file's life the answer was that
# GitHub could not enforce anything: both the branch-protection API and the newer
# rulesets API refused with "Upgrade to GitHub Pro or make this repository
# public" on a private free-plan repo, verified live against both endpoints.
# Joe enabled Pro on 2026-08-13 and ruleset 20824501, "main: CI must be green",
# is now active on main — required status check "ops/ci.sh --strict", plus no
# force-push and no branch deletion. NOTHING in this file changed to make that
# work, which was the point of writing it this way.
#
# THE PRE-PUSH HOOK STILL EARNS ITS PLACE, and is not made redundant by the
# ruleset. It fails in SECONDS on the machine that wrote the change, before a
# runner is ever asked for; the ruleset fails minutes later after a round trip.
# It is bypassable with --no-verify and the ruleset is not, so they cover
# different halves: the hook is the fast accident-stopper, the ruleset is the
# one that cannot be talked out of it.
#
# SKIPPED IS NOT PASSED. Rule 88e9b5eb: "not authorized" and "not possible" are
# different findings and must never be reported as the same one. A check that
# cannot run here (no Postgres on Joe's laptop, no network for an audit) reports
# SKIP, never OK. Under --strict, which CI sets, a SKIP is a FAILURE — because in
# CI everything is available, so a skip there means a check silently stopped
# running. That is the exact shape of the runaway-job finding from 2026-08-13: a
# clean report that was clean because nothing was looking.
#
# Usage:
#   ops/ci.sh                  # every class, local tolerances
#   ops/ci.sh --strict         # a SKIP is a failure (what CI runs)
#   ops/ci.sh --only secret    # one class, by name
#   ops/ci.sh --list           # the classes and what each one is the gate for

set -uo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"

PY="$REPO/.venv/bin/python"
[ -x "$PY" ] || PY=python3

STRICT=0
ONLY=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --strict) STRICT=1 ;;
    --only)   ONLY="${2:-}"; shift ;;
    --list)   LIST=1 ;;
    -h|--help) sed -n '1,40p' "$0"; exit 0 ;;
    *) echo "ci.sh: unknown argument: $1" >&2; exit 64 ;;
  esac
  shift
done

# Class -> the doctrine seeded-failure class it is the gate for.
# These names are the vocabulary the acceptance test uses, so a seeded defect can
# be checked against the class that SHOULD have caught it. A defect failing the
# wrong class is a finding, not a pass.
#
# NO ASSOCIATIVE ARRAYS ANYWHERE IN THIS FILE, deliberately. macOS ships bash
# 3.2.57 (2007, the last GPLv2 release) and Joe's Mac has no newer bash; a GitHub
# ubuntu runner has bash 5. `declare -A` is a bash 4 feature, so using it would
# mean this script behaved differently in its two callers — which is the exact
# failure this one-script design exists to prevent. Plain arrays and a case
# statement run identically on both.
CLASS_ORDER="unit types contract gates secret dependency migration binding artifact freshness"

class_desc() {
  case "$1" in
    unit)       echo "seeded failing unit test" ;;
    freshness)  echo "seeded stale rewrite of a generated config file" ;;
    types)      echo "seeded shape mistake in a data hand-off" ;;
    contract)   echo "seeded auth/schema contract break" ;;
    gates)      echo "seeded enforcement-layer regression" ;;
    secret)     echo "seeded credential in the tree" ;;
    dependency) echo "seeded unpinned or vulnerable dependency" ;;
    migration)  echo "seeded bad migration / trigger permission" ;;
    binding)    echo "seeded binding or config drift" ;;
    artifact)   echo "seeded artifact mismatch (verb loss)" ;;
    *)          echo "" ;;
  esac
}

if [ "${LIST:-0}" = "1" ]; then
  echo "ci.sh check classes — each is the gate for one doctrine seeded-failure class:"
  for c in $CLASS_ORDER; do printf '  %-11s %s\n' "$c" "$(class_desc "$c")"; done
  exit 0
fi

FAILED=0
FAILED_CLASSES=""
# A HARD failure is one no known gap may ever suppress. A gap excuses "this
# class is red pending a design ruling"; it must never excuse a SAFETY refusal,
# because those are categorically not the thing anyone is waiting on a ruling
# about. Caught by ops/ci-selftest.py the moment known gaps were added: the
# first version suppressed the loopback guard too, which would have let a DSN
# pointed at Neon pass silently.
HARD_FAILED=0
SKIPPED=0
SKIPPED_CLASSES=""
RAN=0
# Per-class wall-clock, written by the run loop. The 2026-08-23 council's first
# recommendation was a failure taxonomy and class timings, because neither
# existed: the suite crept 249s -> 317s across a week and nobody could say
# which class grew, and 40 CI failures in three days could not be split into
# "real defect" vs "environment skew" without opening forty logs. Plain string,
# not an associative array — see the bash 3.2 note above CLASS_ORDER.
CLASS_TIMINGS=""

ok()   { RAN=$((RAN+1)); printf '  \033[32mOK\033[0m    %-11s %s\n' "$1" "${2:-}"; }
bad()  { RAN=$((RAN+1)); printf '  \033[31mFAIL\033[0m  %-11s %s\n' "$1" "${2:-}"; FAILED=$((FAILED+1)); FAILED_CLASSES="$FAILED_CLASSES $1"; }
hard() { bad "$1" "${2:-}"; HARD_FAILED=$((HARD_FAILED+1)); }
skip() { RAN=$((RAN+1)); printf '  \033[33mSKIP\033[0m  %-11s %s\n' "$1" "${2:-}"; SKIPPED=$((SKIPPED+1)); SKIPPED_CLASSES="$SKIPPED_CLASSES $1"; }

run_quiet() {  # run_quiet <logfile> <cmd...>  — capture output, return status
  local log="$1"; shift
  "$@" >"$log" 2>&1
}

LOGDIR="$(mktemp -d)"
trap 'rm -rf "$LOGDIR"' EXIT

# ---------------------------------------------------------------- unit
check_unit() {
  local failed_pkgs=""
  for pkg in mcp-server control-room workspace; do
    [ -f "$pkg/package.json" ] || continue
    if ! run_quiet "$LOGDIR/unit-$pkg.log" npm --prefix "$pkg" test; then
      failed_pkgs="$failed_pkgs $pkg"
      echo "--- $pkg ---" >&2
      tail -25 "$LOGDIR/unit-$pkg.log" >&2
    fi
  done
  if [ -n "$failed_pkgs" ]; then
    bad unit "node suites failed:$failed_pkgs"
  else
    ok unit "mcp-server, control-room, workspace suites pass"
  fi
}

# ---------------------------------------------------------------- types
# THE TRIPWIRE MOVES TO THE DOOR (2026-08-14). bin/type-check.sh existed since
# 2026-08-06 and ran in exactly one place: the 2am chain, AFTER the code had
# already merged. So the only way a shape mistake was ever reported was as a red
# nightly the next morning, and only if somebody read past the seven rows the
# nightly report was told to read. It sat red from 08-08 to 08-10 with nobody
# told, and on 08-14 — inside two hours of being cleared to zero — three
# separate sessions merged files that put it back to 12, then 1. Every one was
# the same two idioms, and every one was invisible until the chain ran.
#
# A check that only runs after merge cannot keep main green; it can only report
# that main is not. Running it here means a shape mistake blocks its own PR, and
# the nightly step passes by construction rather than by anyone remembering.
#
# SAME SCRIPT as the manual and nightly paths (rule a8c55a47) — bin/type-check.sh
# holds the file list and the lenient mypy.ini, so there is nothing here to drift
# out of step with them.
# ---------------------------------------------------------------- freshness
# THE LOST UPDATE, which review cannot catch and a merge conflict never reports.
# Two branches that both rewrite a GENERATED file do not conflict — the second
# silently replaces the first. It happened twice on 2026-08-14: an older copy of
# the type-check class reverted the newer one, and a stale gate hash was restored
# over a fresh bless, leaving main shipping a gate whose recorded hash did not
# match its own file until somebody re-blessed by hand. Each branch passed CI
# alone; the damage only existed once combined.
#
# LAST in CLASS_ORDER deliberately. It is the only class that reports on the
# branch's RELATIONSHIP to main rather than on the tree's contents, so a real
# defect in the code should surface before a "you are behind" message.
check_freshness() {
  if [ ! -f ops/stale-config-check.py ]; then
    skip freshness "ops/stale-config-check.py not present"
    return
  fi
  if run_quiet "$LOGDIR/freshness.log" "$PY" ops/stale-config-check.py; then
    ok freshness "no generated config rewritten from a stale base"
  else
    cat "$LOGDIR/freshness.log" >&2
    bad freshness "this branch would overwrite a generated file it never saw"
  fi
}

check_types() {
  if [ ! -x bin/type-check.sh ]; then
    skip types "bin/type-check.sh not present"
    return
  fi
  local rc
  run_quiet "$LOGDIR/types.log" ./bin/type-check.sh
  rc=$?
  if [ "$rc" -eq 0 ]; then
    ok types "mypy clean across the repo's Python"
  elif [ "$rc" -eq 78 ]; then
    # 78 = EX_CONFIG, and bin/type-check.sh's own header names the contract:
    # "Exit 78 (EX_CONFIG) when mypy is absent anywhere: the nightly chain reads
    # 78". This branch was lost when two sessions built this class in parallel
    # on 2026-08-14 and the one WITHOUT it merged second (#65 over #60), so a
    # machine with no mypy read `bad types — mypy found shape mistakes`, which
    # is both a false failure and a false explanation. It matters beyond tidiness
    # because ops/githooks/pre-push runs this script: on a fresh clone, or on
    # Dell's Mac before the venv exists, every push would be refused and the
    # stated reason would send the reader hunting for type errors that are not
    # there. CI on the runner is unaffected — mypy is pinned in requirements.lock.
    # NAME THE ACCEPTED STATE. requirements.txt pins mypy to python_version
    # >= "3.10", so on an older interpreter mypy is not missing by accident, it
    # is excluded by the pin. Printing the version keeps that visible instead of
    # letting a standing machine state read as a vague absence — and keeps the
    # DIFFERENT case (a 3.10+ machine that simply has not installed mypy) legible
    # as the real problem it is. ops/ci-selftest.py holds the same boundary as
    # MYPY_PIN_MIN_PYTHON and fails if the two drift apart.
    local tcpy
    tcpy="$( { [ -x "$REPO/.venv/bin/python" ] && "$REPO/.venv/bin/python" -c 'import sys;print("%d.%d"%sys.version_info[:2])'; } 2>/dev/null || python3 -c 'import sys;print("%d.%d"%sys.version_info[:2])' 2>/dev/null || echo "unknown" )"
    case "$tcpy" in
      3.[0-9]|unknown)
        skip types "mypy excluded by the requirements pin on Python $tcpy (pin needs >= 3.10) — accepted machine state" ;;
      *)
        skip types "mypy not installed on Python $tcpy (pinned in requirements.lock, so CI always has it)" ;;
    esac
  else
    tail -25 "$LOGDIR/types.log" >&2
    bad types "mypy found shape mistakes — see the errors above"
  fi
}

# ---------------------------------------------------------------- contract
# Contract validators run as their own class even though the node suites also
# execute some of them. Overlap is deliberate: doctrine wants an auth/schema
# contract break to block INDEPENDENTLY, so it gets a gate that names it.
check_contract() {
  local failures=""
  if [ -f workspace/contracts/validate.mjs ]; then
    run_quiet "$LOGDIR/contract-workspace.log" node workspace/contracts/validate.mjs \
      || { failures="$failures workspace/contracts"; tail -20 "$LOGDIR/contract-workspace.log" >&2; }
  fi
  for t in control-room/test/contracts.test.mjs control-room/test/work-request-projection.test.mjs; do
    [ -f "$t" ] || continue
    run_quiet "$LOGDIR/contract-$(basename "$t").log" node --test "$t" \
      || { failures="$failures $t"; tail -20 "$LOGDIR/contract-$(basename "$t").log" >&2; }
  done
  if [ -n "$failures" ]; then
    bad contract "contract checks failed:$failures"
  else
    ok contract "workspace + control-room contracts validate"
  fi
}

# ---------------------------------------------------------------- gates
# The enforcement layer's own regression suites. These are the checks that caught
# the ledger-sweep scope gate swallowing its own headline trigger, so they are a
# promotion gate in their own right rather than developer convenience.
check_gates() {
  local failures="" count=0 skiplist=""

  # Exceptions come from ops/config/ci-check-scope.json and are ANNOUNCED, never
  # applied silently. A quarantined check is skipped everywhere; a local_only one
  # is skipped only where its dependency genuinely cannot exist (a runner has no
  # Google Drive vault). Both print their reason on every single run, so the
  # coverage this class actually delivers is visible in the output rather than
  # buried in a config file nobody opens.
  local scope="ops/config/ci-check-scope.json"
  excluded_reason() {  # excluded_reason <basename> -> reason, or empty
    [ -f "$scope" ] || return 0
    "$PY" - "$scope" "$1" "${CARR_CI_PORTABLE_ONLY:-0}" <<'PYEOF'
import json, sys
scope, name, portable = sys.argv[1], sys.argv[2], sys.argv[3] == "1"
d = json.load(open(scope))
for e in d.get("quarantined", []):
    if e["check"] == name:
        print("QUARANTINED: " + e["reason"]); sys.exit(0)
if portable:
    for e in d.get("local_only", []):
        if e["check"] == name:
            print("LOCAL-ONLY: " + e["reason"]); sys.exit(0)
PYEOF
  }

  for t in ops/*-selftest.py tools/test-*.py; do
    [ -f "$t" ] || continue
    local base; base="$(basename "$t")"
    local why; why="$(excluded_reason "$base")"
    if [ -n "$why" ]; then
      skiplist="$skiplist $base"
      printf '        \033[33mnot run\033[0m  %s — %s\n' "$base" "$why" >&2
      continue
    fi
    count=$((count+1))
    local grc
    run_quiet "$LOGDIR/gate-$base.log" "$PY" "$t"
    grc=$?
    # EXIT 78 IS "NOT CONFIGURED HERE", NOT A FAILURE. It is EX_CONFIG, and it is
    # already the repo's convention: bin/type-check.sh's header states it and the
    # types class above honours it. This loop counted every nonzero the same, so
    # a selftest that correctly DECLINED to run for want of a local dependency
    # read as a red gate on any machine lacking it, and the only way
    # past it was CARR_SKIP_CI on every push. Deliberately narrow: ONLY 78 skips,
    # the reason is printed every run, and every other nonzero still fails.
    # ops/ci-selftest.py seeds both cases and fails if this widens.
    if [ "$grc" -eq 78 ]; then
      skiplist="$skiplist $base"
      printf '        \033[33mnot run\033[0m  %s — NOT CONFIGURED (exit 78): %s\n' \
        "$base" "$(tail -1 "$LOGDIR/gate-$base.log" 2>/dev/null)" >&2
    elif [ "$grc" -ne 0 ]; then
      failures="$failures $base"; tail -12 "$LOGDIR/gate-$base.log" >&2
    fi
  done

  # SHELL TESTS, run under their own shebang rather than "$PY". The loop above
  # globs only *.py and hands every match to the Python interpreter, so a shell
  # test dropped into tools/ was collected by nobody and executed by nothing —
  # it would sit in the tree looking like coverage while never running once.
  # Some things under test here ARE shell (mcp-server/smoke-reads.sh), and a
  # Python wrapper around them would only shell out to the same script.
  # Everything else is identical to the loop above: same exclusion scope, same
  # counting, same captured log and same 12-line tail on failure.
  for t in tools/test-*.sh; do
    [ -f "$t" ] || continue
    local sbase; sbase="$(basename "$t")"
    local swhy; swhy="$(excluded_reason "$sbase")"
    if [ -n "$swhy" ]; then
      skiplist="$skiplist $sbase"
      printf '        \033[33mnot run\033[0m  %s — %s\n' "$sbase" "$swhy" >&2
      continue
    fi
    count=$((count+1))
    run_quiet "$LOGDIR/gate-$sbase.log" "$t" \
      || { failures="$failures $sbase"; tail -12 "$LOGDIR/gate-$sbase.log" >&2; }
  done
  # gate-integrity is the baseline check itself: a gate edited without a
  # re-bless in the same commit (rule c0b38d80) fails here.
  #
  # --strict IS LOAD-BEARING AND WAS MISSING UNTIL 2026-08-14. Without it this
  # call could never fail: gate-integrity returns 0 on every path including its
  # failure path, deliberately, because it is also a SessionStart hook and a
  # boot check that wedges a session is worse than the drift it reports. So
  # this line announced a baseline check it never performed. Pull request #102,
  # cut before #99 merged, then overwrote main's baseline and dropped two gates
  # from it with CI green the whole way. --strict fails on repository-content
  # findings only (a hash that does not match, a blessed gate gone, a gate
  # nothing blessed) and never on machine state a runner legitimately lacks.
  #
  # THIS IS THE ONLY LAYER THAT CAN CATCH THAT OVERWRITE. The pre-commit
  # co-change check cannot: the clobber happens inside GitHub's merge, where no
  # local hook runs.
  run_quiet "$LOGDIR/gate-integrity.log" "$PY" hooks/gate-integrity.py --strict \
    || { failures="$failures gate-integrity"; tail -12 "$LOGDIR/gate-integrity.log" >&2; }

  # THE THREE INVENTORY CHECKS, run against THIS REPO rather than a synthetic
  # tree. The selftest loop above runs their *-selftest.py, and every one of
  # those builds its own fixture tree on purpose — that measures the CHECK and
  # says nothing whatever about the actual inventory. So all three could pass
  # their suites while the real files contradicted each other, and that is not
  # hypothetical: on 2026-08-15 ops/audit-queue-freshness-check.py FAILED on
  # main's own tree while CI reported green, because nothing here ever ran it.
  #
  # Same shape as gate-integrity --strict directly above: repository content
  # only, no machine state, no network, no database. Each carries its own
  # escape hatch for a genuinely mid-flight tree and names its own remedy in
  # its own output, so none is repeated here.
  #
  # mechanism-doctrine-gate is in this list rather than a class of its own
  # because it is the same kind of check: repository content only, no machine
  # state and no database. It is the compiled half of "knowledge ships with the
  # mechanism" (open loop 504) — a newly ADDED gate, hook, launchd job or
  # scheduled task must name the doctrine section explaining it. It reads the
  # change against origin/main, so it is a no-op on a branch that adds none of
  # those, which is nearly every branch. Editing an existing mechanism is
  # deliberately out of scope.
  # rule-enforcement-map-check JOINED THIS LIST 2026-08-23, and the reason is a
  # merged mistake. hooks/gate-integrity.py runs it too, but classifies it an
  # ENVIRONMENT problem that --strict deliberately does not fail CI on, because
  # it was once "the vault-backed rule map" and a bare runner had no vault. The
  # markdown-render cutoff on 2026-08-19 retired that dependency: the check now
  # reads one repository file and passes under `env -i`, which is the same
  # standard every other check in this loop meets. Left where it was, it caught
  # a real defect — rule 3fa422b7 categorized session_task_rail without the
  # matching override — only at the NEXT session's boot, after the change had
  # merged with CI green. Here it catches it before the merge.
  # scheduler-cutover-coverage-gate JOINED 2026-08-23. Same kind again:
  # repository content only, no machine state and no database — it reads the
  # launchd declarations and the cutover registry. It is a RATCHET rather than a
  # threshold, because coverage stands at three of twenty-three and a check that
  # fails every run is one people learn to scroll past. It fails only when a job
  # that HAD a workflow binding loses it.
  # rule-load-layer-check JOINED 2026-08-23, alongside the delivery tags it
  # validates. Same kind again: one repository file, no machine state, no
  # database. It is the check that stops the scoping change from becoming the
  # thing the council warned about — a wildcard tag, an untagged rule, or a
  # `control` claim on a rule nothing can actually refuse would all ship a
  # smaller boot payload that silently drops law, and the failure is invisible
  # afterwards because a session that boots with fewer rules looks exactly like
  # one that boots correctly.
  for inv in enforcement-coverage-check audit-queue-freshness-check map-row-evidence-check \
             rule-enforcement-map-check rule-load-layer-check \
             drive-dependency-inventory drive-retirement-readiness-gate \
             mechanism-doctrine-gate scheduler-cutover-coverage-gate; do
    [ -f "ops/$inv.py" ] || continue
    run_quiet "$LOGDIR/gate-$inv.log" "$PY" "ops/$inv.py" \
      || { failures="$failures $inv"; tail -12 "$LOGDIR/gate-$inv.log" >&2; }
  done
  if [ -n "$failures" ]; then
    bad gates "failed:$failures"
  elif [ -n "$skiplist" ]; then
    # Deliberately NOT a plain OK. The class ran with reduced coverage, and the
    # summary line says so — an exception that reads as a clean pass is how a
    # bounded check gets mistaken for a complete one.
    ok gates "$count suites + baseline integrity · NOT RUN:$skiplist"
  else
    ok gates "$count selftest suites + baseline integrity"
  fi
}

# ---------------------------------------------------------------- secret
check_secret() {
  if run_quiet "$LOGDIR/secret.log" "$PY" ops/ci-secret-scan.py; then
    ok secret "no shaped credential in tracked files"
  else
    cat "$LOGDIR/secret.log" >&2
    bad secret "credential-shaped content found"
  fi
}

# ---------------------------------------------------------------- dependency
check_dependency() {
  # NOT ${STRICT:+--strict}: STRICT is "0" or "1", and :+ expands on any non-empty
  # value, so "0" would have passed --strict too. Caught while testing the skip path.
  local strictflag=""
  [ "$STRICT" = "1" ] && strictflag="--strict"
  if run_quiet "$LOGDIR/dep.log" "$PY" ops/ci-dep-check.py $strictflag; then
    ok dependency "$(tail -1 "$LOGDIR/dep.log")"
  else
    cat "$LOGDIR/dep.log" >&2
    bad dependency "see above"
  fi
}

# ---------------------------------------------------------------- migration
# HARD SAFETY PROPERTY: this refuses any DSN whose host is not loopback. The
# check applies 130 forward migrations to a throwaway database; pointed at Neon
# it would apply them to the live record layer. Making localhost a structural
# requirement means no environment variable, typo or copied DSN can aim it at
# production. There is no override flag on purpose.
check_migration() {
  local dsn="${CARR_CI_DATABASE_URL:-}"
  if [ -z "$dsn" ]; then
    skip migration "no CARR_CI_DATABASE_URL (CI provides a throwaway Postgres)"
    return
  fi
  case "$dsn" in
    *@localhost:*|*@localhost/*|*@127.0.0.1:*|*@127.0.0.1/*) ;;
    *) hard migration "REFUSED: CARR_CI_DATABASE_URL is not loopback. This applies every migration; it runs against a throwaway only."
       return ;;
  esac
  # WHAT THIS ASKS, changed 2026-08-13. It used to replay all 121 migrations
  # against an empty database, which CANNOT WORK and is also the wrong question.
  # Several migrations are data backfills carrying guards like "remapped ZERO
  # deals — stop and report, do not force"; those guards are correct, they catch
  # a backfill that silently did nothing to production, and they assert on
  # business data an empty database legitimately does not have.
  #
  # It now loads db/schema.sql — production's committed structure plus its
  # applied-migration ledger — and applies whatever is PENDING on top. That is
  # the question worth gating a change on: does the migration in this diff apply
  # cleanly to the database we actually have? Replaying history tests history.
  local psql_bin=""
  for c in psql /opt/homebrew/opt/libpq/bin/psql /usr/local/opt/libpq/bin/psql; do
    if command -v "$c" >/dev/null 2>&1; then psql_bin="$c"; break; fi
  done
  if [ -z "$psql_bin" ]; then
    skip migration "no psql on PATH to load db/schema.sql"
    return
  fi
  if [ ! -f db/schema.sql ]; then
    skip migration "db/schema.sql absent — regenerate with bin/schema-snapshot.sh"
    return
  fi

  if ! PGOPTIONS='--client-min-messages=warning' run_quiet "$LOGDIR/migration-load.log" \
       "$psql_bin" -v ON_ERROR_STOP=1 -q -d "$dsn" -f db/schema.sql; then
    tail -25 "$LOGDIR/migration-load.log" >&2
    # SAY WHICH FAILURE THIS IS. "The committed structure is not loadable" is a
    # serious claim and was, until 2026-08-22, also what a session saw for the
    # entirely ordinary case of pointing CARR_CI_DATABASE_URL at a database that
    # ALREADY HAS the schema. This class loads db/schema.sql from scratch, so a
    # second run against the same database always fails on "already exists" —
    # and the message accused the repository instead. A session chasing that on
    # its own hand-built cluster hit it repeatedly in one night.
    if grep -qE 'already exists' "$LOGDIR/migration-load.log"; then
      bad migration "this database already carries the schema — it is not a fresh one. \
This class loads db/schema.sql from scratch, so it needs a database nobody has loaded yet. \
The supported lane builds and removes one for you: ./run.sh local-db-ci --class migration \
(add --port N if the default is taken by another session)."
    else
      bad migration "db/schema.sql did not load — the committed structure is not loadable"
    fi
    return
  fi

  if ! DATABASE_URL="$dsn" run_quiet "$LOGDIR/migration.log" "$PY" tools/migrate.py --apply --yes; then
    tail -30 "$LOGDIR/migration.log" >&2
    bad migration "a pending migration did not apply to the committed schema"
    return
  fi

  # THE GRANTS CANARY, added 2026-08-14. The snapshot is pg_dump --no-acl, so
  # for months this class built a database where the app roles existed and held
  # NOTHING — has_table_privilege() false for every table, every role — and ran
  # green anyway, because nothing ever asked. PR #75 paid for that: verifying
  # carr_writer's insert on lead meant replaying 0001-0004 by hand. The CARR
  # GRANTS section in db/schema.sql now carries the privileges; this asks the
  # LIVE database whether they actually attached, which no text check on the
  # snapshot can prove. One positive per grant shape, plus 0117's negative —
  # the interlock where the ABSENT column is the point. A flattening bug that
  # turned column grants into table grants would pass every positive and be
  # caught only there.
  if run_quiet "$LOGDIR/migration-grants.log" "$psql_bin" -X -v ON_ERROR_STOP=1 -Atq -d "$dsn" -c "
    do \$\$ begin
      if not has_table_privilege('carr_writer', 'public.lead', 'insert') then
        raise exception 'carr_writer cannot insert into lead (the PR #75 case)'; end if;
      if not has_schema_privilege('carr_jobs', 'ops', 'usage') then
        raise exception 'carr_jobs lacks usage on schema ops (0115)'; end if;
      if not has_column_privilege('carr_jobs', 'ops.incident', 'state', 'update') then
        raise exception 'carr_jobs cannot update incident.state (0117)'; end if;
      if has_column_privilege('carr_jobs', 'ops.incident', 'resolved_at', 'update') then
        raise exception 'carr_jobs can update incident.resolved_at — 0117 interlock broken'; end if;
      if not has_function_privilege('carr_reader', 'public.state_as_of(text, uuid, timestamptz)', 'execute') then
        raise exception 'carr_reader cannot execute state_as_of (0106)'; end if;
      if not pg_has_role('carr_exporter', 'carr_reader', 'member') then
        raise exception 'carr_exporter lost its carr_reader bundle (0006)'; end if;
    end \$\$;"; then
    local n
    # grep -c prints "0" AND exits nonzero on no match, so `|| echo 0` printed
    # a second zero. `|| :` keeps the count grep already printed.
    n="$(grep -c '^applying ' "$LOGDIR/migration.log" 2>/dev/null || :)"

    # THE TRIGGER-READ CHECK (rule 5409731b). The canary above proves the
    # declared grants attached; this asks the question that broke set-lead for
    # five days in production: can the role that WRITES a table actually SELECT
    # every table the trigger on it READS? An invoker-rights trigger runs as the
    # caller, and grants never fire for the owner, so no rehearsal as owner can
    # see it — which is why this lives here, against the built database, rather
    # than in the gates class against a synthetic tree.
    if ! CARR_CI_DATABASE_URL="$dsn" run_quiet "$LOGDIR/migration-triggers.log" \
         "$PY" ops/trigger-grant-check.py; then
      tail -25 "$LOGDIR/migration-triggers.log" >&2
      bad migration "a trigger reads a table its firing role cannot select"
      return
    fi

    # Migration 0176 deliberately creates its FK-bound scheduler surface
    # registry empty.  The real authority sync validates the complete
    # checked-in inventory and writes it only after job definitions exist.
    # Do that real sync on this disposable database before acceptance gates;
    # copied seed SQL would bypass exactly the ordering this class must prove.
    if ! DATABASE_URL="$dsn" run_quiet "$LOGDIR/migration-control-plane-sync.log" \
         "$PY" tools/control-plane.py sync; then
      tail -30 "$LOGDIR/migration-control-plane-sync.log" >&2
      bad migration "control-plane registry sync failed after migrations"
      return
    fi

    # ── THE DATABASE ACCEPTANCE GATES ────────────────────────────────────────
    # Three of these existed before this loop did and NOTHING RAN ANY OF THEM.
    # ops/program3-trace-gate.py opens by calling itself "the acceptance test
    # for Program 3", written before the thing it tests — and its only stated
    # runner was a db-tap command a human types. A gate nobody runs is a
    # document with assertions in it (rule ab814a26: a rule ships with its
    # enforcement, and recitation is not enforcement).
    #
    # This class already stands up the one thing they need: a throwaway
    # database with the committed schema loaded and every pending migration
    # applied. So they run here, on every proposed change, against real
    # Postgres. MOST gates roll back everything they write, which is why
    # running them repeatedly costs nothing.
    #
    # ONE DOES NOT, AND THE BLANKET CLAIM THAT USED TO STAND HERE WAS FALSE
    # (2026-08-22, found while building the snapshot authority-membership
    # gate). ops/calendar-prebrief-projection-local-pg-gate.py creates twelve
    # login roles — including carr_authority_joe and carr_authority_dell — and
    # COMMITS. That is deliberate and it is not a leak: the gate proves a
    # concurrency property with two real peer connections authenticating as
    # different roles and racing in threads, which cannot be done inside one
    # transaction, and it hard-refuses to run unless the database is a
    # dedicated disposable carr_ci superuser database.
    #
    # THE CONSEQUENCE IS WHAT MATTERS TO ANYONE WRITING A GATE. The glob below
    # is alphabetical, so every gate ordered after `calendar-prebrief-p...`
    # runs on a substrate where both human authority login roles already exist
    # and already hold the carr_authority bundle. A gate that asserts an
    # authority privilege there is asserting it under conditions it did not
    # establish and cannot see — the same shape as a proof that passes because
    # it happens to run as the owner. Establish the role state you depend on
    # inside your own rolled-back transaction rather than inheriting whatever
    # the gate before you left; ops/snapshot-authority-membership-gate.py
    # manufactures absence that way for exactly this reason, after an earlier
    # revision of it reported differently depending on its position.
    #
    # SELF-REGISTERING, by the marker `# ci: db-gate` in the file itself. A new
    # gate is wired by writing it, not by remembering to edit this list.
    #
    # WHAT COUNTS AS A DB-GATE, and why the old test was wrong (open loop 503,
    # item 1, 2026-08-22). This used to flag any ops/*-gate.py MENTIONING the
    # string DATABASE_URL. Two files mention it without ever consuming it:
    # ops/p1-rebuild-gate.py and ops/p1-integration-gate.py only SET it in the
    # environment of subprocesses they point at a Neon branch they created
    # themselves. So both were named "not run" on every single push — a true
    # sentence reached by a wrong test, for gates that could never take CI's
    # throwaway DSN in the first place.
    #
    # THE COST OF GETTING IT WRONG THIS WAY. A yellow line that prints on every
    # push and names something CI genuinely cannot fix is invisible within a
    # week, and it is invisible in exactly the way the comment above warns
    # about. The heuristic was importing a gate's real problem into the wrong
    # lane and then shrugging at it in colour.
    #
    # So the test is now an actual READ of a DSN from the environment. Checked
    # against all 32 gates when this was written, it agrees with the `# ci:
    # db-gate` marker exactly: 25 marked, 25 reading, no gate on either side
    # alone. That agreement is what makes the two rules below safe.
    local dsn_read='environ\.get\("(CARR_CI_)?DATABASE_URL"|environ\["(CARR_CI_)?DATABASE_URL"\]|getenv\("(CARR_CI_)?DATABASE_URL"'
    local db_gate_failures="" db_gate_count=0 db_gate_unmarked="" db_gate_declared=""
    for g in ops/*-gate.py; do
      [ -f "$g" ] || continue
      if grep -q '^# ci: db-gate' "$g"; then
        db_gate_count=$((db_gate_count+1))
        if ! DATABASE_URL="$dsn" run_quiet "$LOGDIR/db-gate-$(basename "$g").log" \
             "$PY" "$g"; then
          db_gate_failures="$db_gate_failures $(basename "$g")"
          tail -20 "$LOGDIR/db-gate-$(basename "$g").log" >&2
        fi
      elif grep -qE "$dsn_read" "$g"; then
        # A gate that reads a DSN and carries no marker really is unrun, and
        # this is now a FAILURE rather than a line in the margin. The whole
        # lesson of the three gates that prompted this loop is that nobody
        # reads a warning; the only reliable way to make an unrun gate visible
        # is to stop the push that added it.
        db_gate_unmarked="$db_gate_unmarked $(basename "$g")"
      elif grep -q '^# ci: runs-outside-ci' "$g"; then
        # Declared, not forgotten. Printed with its stated reason so the state
        # is legible rather than merely quiet — a gate that runs nowhere should
        # say so out loud every time, in its own words.
        db_gate_declared="$db_gate_declared\n            $(basename "$g"): $(sed -n 's/^# ci: runs-outside-ci *— *//p' "$g" | head -1)"
      fi
    done
    if [ -n "$db_gate_declared" ]; then
      printf '        \033[33moutside CI\033[0m  gate(s) declared to run elsewhere:%b\n' \
        "$db_gate_declared" >&2
    fi
    if [ -n "$db_gate_unmarked" ]; then
      bad migration "gate(s) read a database URL but carry no \`# ci: db-gate\` marker, so nothing runs them:$db_gate_unmarked (add the marker, or declare \`# ci: runs-outside-ci — why\`)"
      return
    fi
    if [ -n "$db_gate_failures" ]; then
      bad migration "database acceptance gate(s) failed:$db_gate_failures"
      return
    fi

    ok migration "committed schema loads; ${n:-0} pending migration(s) apply; app-role grants verified live; trigger reads granted; $db_gate_count db acceptance gate(s) pass"
  else
    tail -15 "$LOGDIR/migration-grants.log" >&2
    bad migration "the app roles' grants did not survive into the built database"
  fi
}

# ---------------------------------------------------------------- binding
# Worker config and installed-hook wiring. config-as-code compares the live
# Claude Code settings against the repo's declared baseline; on a runner there is
# no live settings file, so that half skips and the wrangler half still runs.
check_binding() {
  local problems="" machine_drift=""
  run_quiet "$LOGDIR/binding-agent-boot.log" "$PY" ops/agent-boot-contract.py \
    || { problems="$problems agent-boot"; cat "$LOGDIR/binding-agent-boot.log" >&2; }
  if [ -f mcp-server/wrangler.toml ]; then
    run_quiet "$LOGDIR/binding-wrangler.log" node -e '
      const fs=require("fs"), t=fs.readFileSync("mcp-server/wrangler.toml","utf8");
      const need=["name","main","compatibility_date"];
      const missing=need.filter(k=>!new RegExp("^\\s*"+k+"\\s*=","m").test(t));
      if(missing.length){console.error("wrangler.toml missing: "+missing.join(", "));process.exit(1);}
      if(/DATABASE_URL\s*=/.test(t)){console.error("wrangler.toml declares a DATABASE_URL inline; it belongs in a secret");process.exit(1);}
    ' || { problems="$problems wrangler.toml"; cat "$LOGDIR/binding-wrangler.log" >&2; }
  fi
  # CONFIG-AS-CODE IS SCOPED TO BRANCHES THAT ARE ACTUALLY IN THAT BUSINESS.
  #
  # This check compares the LIVE MACHINE — ~/.claude/settings.json and the
  # installed launchd plists — against the repo's declarations. That makes it
  # the one check here whose subject is not the branch. Any session installing
  # a job on this Mac puts every OTHER session's push into drift, and the
  # drifting branch cannot fix it: capturing the machine into an unrelated pull
  # request means committing somebody else's in-flight work, which rule
  # 308ef1de forbids and the git-writer gate blocks outright. The only remaining
  # move was CARR_SKIP_CI=1, which skips the nine checks that WERE working.
  #
  # Observed twice on 2026-08-22, hours apart, on two different jobs, neither
  # belonging to the branch being pushed. Same class the council ruled that day:
  # a machine-global condition may open a loop, never veto unrelated work — the
  # same reasoning that scoped tools/next-migration.py to the caller's own tree.
  #
  # So: drift still RUNS and is still printed in full on every push, because a
  # silent drift is how five gates were off for a day in August. It fails the
  # class only when this branch touches the declarations it is about. A branch
  # that does change them is squarely in this business and must reconcile.
  if [ -f "$HOME/.claude/settings.json" ]; then
    if ! run_quiet "$LOGDIR/binding-config.log" "$PY" ops/config-as-code.py; then
      # OWNED, NOT MERELY ADJACENT. This used to ask whether the branch had
      # touched ANY declaration path and, if so, make every drifting item on the
      # machine fatal — including items in the other family that nobody on this
      # branch had seen. On 2026-08-22 a one-word comment repair to a launchd
      # file (a double hyphen inside an XML comment, which strict parsers refuse
      # and plutil accepts) made two unrelated scheduled tasks another session
      # had installed into that branch's problem, and it could not fix them
      # without committing somebody else's in-flight work. The matching rule
      # lives in ops/config_drift_ownership.py so this shell and its selftest ask
      # the same question (rule a8c55a47).
      local cfg_changed cfg_owned
      cfg_changed="$(git diff --name-only origin/main...HEAD 2>/dev/null || true)"
      tail -15 "$LOGDIR/binding-config.log" >&2
      # shellcheck disable=SC2086
      cfg_owned="$("$PY" ops/config_drift_ownership.py $cfg_changed \
        < "$LOGDIR/binding-config.log" 2>/dev/null || true)"
      if [ -n "$cfg_owned" ]; then
        problems="$problems config-as-code"
        printf '        this branch touches the drifting declaration(s), so they are yours:\n%s\n' "$cfg_owned" >&2
      else
        machine_drift="yes"
        # SAY WHAT IS ACTUALLY TRUE. This line used to read "this branch changes
        # no declaration", which was true only while the test was directory-wide.
        # A branch CAN now touch a declaration and still own none of the drift,
        # and printing the old sentence there would be a comment left above a
        # changed line — the exact shape that let five nightly steps report
        # "skipped" for days while their explanatory text said they ran.
        printf '        \033[33mnot fatal here\033[0m  config-as-code drift is on the MACHINE and this branch touches\n' >&2
        printf '        none of the drifting declarations above. Reported, not vetoed —\n' >&2
        printf '        run `ops/config-as-code.py pull` on a branch that owns the change.\n' >&2
      fi
    fi
  fi
  if [ -n "$problems" ]; then
    bad binding "drift:$problems"
  elif [ -n "$machine_drift" ]; then
    # NEVER say the wiring matches when it does not. A class that reports a
    # drift and then prints "installed wiring matches repo" is the false-green
    # shape this repo has been bitten by repeatedly; the scoping decides whether
    # drift BLOCKS THIS BRANCH, never whether it is true.
    ok binding "worker config declared; MACHINE config-as-code drift reported above, not owned by this branch"
  else
    ok binding "worker config declared; installed wiring matches repo"
  fi
}

# ---------------------------------------------------------------- artifact
# The loop #276 guard, moved earlier in the pipeline. At deploy time this same
# helper refuses a shrink; here it refuses a MERGE that would cause one, so the
# verb loss is caught before it is a release.
check_artifact() {
  local shipping marker
  shipping="$(sh ops/verb-count.sh "$REPO/mcp-server" 2>"$LOGDIR/artifact.log")"
  if [ -z "$shipping" ]; then
    cat "$LOGDIR/artifact.log" >&2
    bad artifact "registry did not import — refusing to call it unmeasured-but-fine"
    return
  fi
  # THE BASELINE COMES FROM THE LEDGER, NOT A FILE — defect d737c09c, 2026-08-16.
  # This line read mcp-server/.last-deployed-verb-count until 2026-08-21, five
  # days after the deploy stopped writing it. The file is gitignored and nothing
  # creates it any more, so `marker` was always empty, the shrink comparison was
  # never once performed, and the class still printed OK: "no loss against
  # deployed none" — a loss guard asserting no loss having compared against
  # nothing. The shell also reported the failed redirect on every run, because
  # `2>/dev/null` silences tr and not the `<` that fails before tr starts.
  #
  # ops/last-deployed-verb-count.py is the one query, and its header names this
  # caller: "the deploy asks this, and so can CI or a human. A second copy of the
  # query in shell would be free to drift." This is that drift, closed.
  local rc=0
  marker="$("$PY" ops/last-deployed-verb-count.py carr-mcp production 2>/dev/null)" || rc=$?
  case "$rc" in
    0)
      if [ -n "$marker" ] && [ "$shipping" -lt "$marker" ]; then
        bad artifact "would REMOVE $((marker - shipping)) verb(s): deployed $marker, tree has $shipping"
      else
        ok artifact "$shipping verbs, no loss against deployed $marker"
      fi
      ;;
    3)
      ok artifact "$shipping verbs; no prior deployment recorded, this would set the baseline"
      ;;
    78)
      # NO LEDGER CREDENTIAL. In the GitHub runner that is BY DESIGN — ci.yml
      # grants a throwaway loopback Postgres and sets CARR_CI_PORTABLE_ONLY=1,
      # and no production credential belongs on a PR runner. Saying SKIP there
      # would fail every pull request under --strict, so the portable runner gets
      # a named exclusion instead. Anywhere else a missing credential is a real
      # gap in coverage and reports SKIP, which --strict escalates. Either way
      # this NEVER claims "no loss" — bin/deploy-worker.sh runs the same guard
      # against the same ledger and fails closed, so the deploy is where a shrink
      # is actually stopped.
      if [ "${CARR_CI_PORTABLE_ONLY:-0}" = "1" ]; then
        ok artifact "$shipping verbs; shrink guard not run here (portable runner has no ledger credential — the deploy enforces it)"
      else
        skip artifact "$shipping verbs counted, but no ledger credential — the shrink comparison did not run"
      fi
      ;;
    *)
      bad artifact "could not read the deployed verb count from the ledger (exit $rc) — refusing to call it unmeasured-but-fine"
      ;;
  esac
}

# ---------------------------------------------------------------- run
echo "carr-system CI — $(git rev-parse --short HEAD) on $(git rev-parse --abbrev-ref HEAD)"
[ "$STRICT" = "1" ] && echo "  strict: a SKIP counts as a failure"
echo

if [ -n "$ONLY" ] && [ -z "$(class_desc "$ONLY")" ]; then
  echo "ci.sh: no such class: $ONLY (try --list)" >&2
  exit 64
fi

for c in $CLASS_ORDER; do
  if [ -n "$ONLY" ] && [ "$ONLY" != "$c" ]; then continue; fi
  # Snapshot the class name: check functions reuse the global `c` as their own
  # loop variable (check_migration's psql probe at least), so after "check_$c"
  # returns, $c may name whatever that inner loop ended on. The very first CI
  # run of the timing line proved it, printing `psql=32s` where migration
  # belonged. Restoring `c` also keeps anything after this loop honest.
  _class_name="$c"
  _class_t0="$(date +%s)"
  "check_$c"
  CLASS_TIMINGS="$CLASS_TIMINGS $_class_name=$(( $(date +%s) - _class_t0 ))s"
  c="$_class_name"
done

# One greppable line each, every run, pass or fail — this is the raw material
# for the failure taxonomy and the duration budget. `ci-timing` answers "which
# class grew" across runs without opening a single job log; the two class lists
# split every red run into FAIL (a defect, or local/CI skew if the same tree
# passed at pre-push) vs SKIP (environment, promoted to failure under --strict)
# at grep speed. FAILED_CLASSES is printed here, before the known-gaps block
# below rewrites FAILED, so the line always names the raw result.
echo
echo "ci-timing:${CLASS_TIMINGS}"
[ -n "$FAILED_CLASSES" ]  && echo "ci-failed-classes:${FAILED_CLASSES}"
[ -n "$SKIPPED_CLASSES" ] && echo "ci-skipped-classes:${SKIPPED_CLASSES}"

# KNOWN GAPS. A class that is red because a DESIGN RULING is outstanding, not
# because of a bug someone could fix. It still runs, it still prints its failure
# in full, and it is still counted and named below — it just does not take the
# exit code down while the ruling is pending.
#
# WHY THIS EXISTS. On 2026-08-13 seven consecutive pushes each emailed Joe a CI
# failure for the same blocked-on-him migration question. A gate that cries every
# push about something the reader cannot act on is one they learn to filter, and a
# filtered gate has stopped being a gate. The noise, not the redness, is the
# hazard.
#
# EVERY GAP EXPIRES, and that is the whole safety of the mechanism. Past its date
# it becomes a hard failure again whether or not anyone remembers it, so this can
# never quietly become a permanent exemption. ops/ci-selftest.py enforces that
# every entry HAS an expiry and that an expired one is honoured.
if [ "$FAILED" -gt 0 ] && [ "$HARD_FAILED" -eq 0 ] && [ -f ops/config/ci-check-scope.json ]; then
  GAP_REPORT="$("$PY" - ops/config/ci-check-scope.json "$FAILED_CLASSES" <<'PYEOF'
import json, sys, datetime
scope, failed = sys.argv[1], sys.argv[2].split()
gaps = {g["class"]: g for g in json.load(open(scope)).get("known_gaps", [])}
today = datetime.date.today().isoformat()
covered, live = [], []
for c in failed:
    g = gaps.get(c)
    if g and g.get("expires", "") > today:
        covered.append(c)
        print(f"KNOWNGAP\t{c}\t{g['expires']}\t{g.get('loop','')}\t{g['reason']}")
    else:
        live.append(c)
        if g:
            print(f"EXPIRED\t{c}\t{g.get('expires','no expiry')}")
print("REMAINING\t" + str(len(live)))
PYEOF
)"
  echo "$GAP_REPORT" | while IFS=$'\t' read -r tag c exp loop reason; do
    case "$tag" in
      KNOWNGAP) printf '  \033[33mKNOWN GAP\033[0m  %s — blocked on a ruling, not a bug. Expires %s (then this fails hard again).\n             Open loop #%s: %s\n' "$c" "$exp" "$loop" "$reason" ;;
      EXPIRED)  printf '  \033[31mEXPIRED GAP\033[0m %s — its exemption ran out on %s. Failing hard, as designed.\n' "$c" "$exp" ;;
    esac
  done
  REMAINING="$(echo "$GAP_REPORT" | awk -F'\t' '$1=="REMAINING"{print $2}')"
  if [ "${REMAINING:-$FAILED}" = "0" ]; then
    echo
    echo "CI: $FAILED class(es) red, all of them known gaps awaiting a ruling. Not counted as failure."
    exit 0
  fi
  FAILED="${REMAINING:-$FAILED}"
fi

echo
if [ "$FAILED" -gt 0 ]; then
  echo "CI FAILED — $FAILED class(es) failed, $SKIPPED skipped"
  exit 1
fi
if [ "$SKIPPED" -gt 0 ] && [ "$STRICT" = "1" ]; then
  echo "CI FAILED — $SKIPPED class(es) SKIPPED under --strict."
  echo "In CI every check is available, so a skip means a check stopped running."
  exit 1
fi
if [ "$SKIPPED" -gt 0 ]; then
  echo "CI passed — $SKIPPED class(es) skipped locally (they run in CI under --strict)"
else
  echo "CI passed — every class green"
fi
exit 0
