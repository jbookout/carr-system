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
CLASS_ORDER="unit types contract gates secret dependency migration binding artifact"

class_desc() {
  case "$1" in
    unit)       echo "seeded failing unit test" ;;
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
RAN=0

ok()   { RAN=$((RAN+1)); printf '  \033[32mOK\033[0m    %-11s %s\n' "$1" "${2:-}"; }
bad()  { RAN=$((RAN+1)); printf '  \033[31mFAIL\033[0m  %-11s %s\n' "$1" "${2:-}"; FAILED=$((FAILED+1)); FAILED_CLASSES="$FAILED_CLASSES $1"; }
hard() { bad "$1" "${2:-}"; HARD_FAILED=$((HARD_FAILED+1)); }
skip() { RAN=$((RAN+1)); printf '  \033[33mSKIP\033[0m  %-11s %s\n' "$1" "${2:-}"; SKIPPED=$((SKIPPED+1)); }

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
    skip types "mypy not installed (pinned in requirements.lock, so CI always has it)"
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
    run_quiet "$LOGDIR/gate-$base.log" "$PY" "$t" \
      || { failures="$failures $base"; tail -12 "$LOGDIR/gate-$base.log" >&2; }
  done
  # gate-integrity is the baseline check itself: a gate edited without a
  # re-bless in the same commit (rule c0b38d80) fails here.
  run_quiet "$LOGDIR/gate-integrity.log" "$PY" hooks/gate-integrity.py \
    || { failures="$failures gate-integrity"; tail -12 "$LOGDIR/gate-integrity.log" >&2; }
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
    bad migration "db/schema.sql did not load — the committed structure is not loadable"
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
    ok migration "committed schema loads; ${n:-0} pending migration(s) apply; app-role grants verified live"
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
  local problems=""
  if [ -f mcp-server/wrangler.toml ]; then
    run_quiet "$LOGDIR/binding-wrangler.log" node -e '
      const fs=require("fs"), t=fs.readFileSync("mcp-server/wrangler.toml","utf8");
      const need=["name","main","compatibility_date"];
      const missing=need.filter(k=>!new RegExp("^\\s*"+k+"\\s*=","m").test(t));
      if(missing.length){console.error("wrangler.toml missing: "+missing.join(", "));process.exit(1);}
      if(/DATABASE_URL\s*=/.test(t)){console.error("wrangler.toml declares a DATABASE_URL inline; it belongs in a secret");process.exit(1);}
    ' || { problems="$problems wrangler.toml"; cat "$LOGDIR/binding-wrangler.log" >&2; }
  fi
  if [ -f "$HOME/.claude/settings.json" ]; then
    run_quiet "$LOGDIR/binding-config.log" "$PY" ops/config-as-code.py \
      || { problems="$problems config-as-code"; tail -15 "$LOGDIR/binding-config.log" >&2; }
  fi
  if [ -n "$problems" ]; then
    bad binding "drift:$problems"
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
  marker="$(tr -dc '0-9' < mcp-server/.last-deployed-verb-count 2>/dev/null)"
  if [ -n "$marker" ] && [ "$shipping" -lt "$marker" ]; then
    bad artifact "would REMOVE $((marker - shipping)) verb(s): deployed $marker, tree has $shipping"
  else
    ok artifact "$shipping verbs, no loss against deployed ${marker:-none}"
  fi
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
  "check_$c"
done

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
