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
# WHY BOTH CALLERS EXIST AT ALL. This repo is private on a free GitHub plan, and
# both the branch-protection API and the newer rulesets API refuse:
# "Upgrade to GitHub Pro or make this repository public." Verified live
# 2026-08-13 against both endpoints. So GitHub Actions can RUN these checks and
# report, but it cannot REQUIRE them before a merge. The pre-push hook is what
# gives the checks teeth today, at no cost: a red tree does not leave the
# machine. It is bypassable with --no-verify, and that is stated plainly rather
# than papered over — it is an accident-stopper, the same honest framing
# ops/githooks/pre-push already uses about itself. When GitHub Pro is enabled,
# the workflow becomes a required status check and NOTHING here changes.
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
CLASS_ORDER="unit contract gates secret dependency migration binding artifact"

class_desc() {
  case "$1" in
    unit)       echo "seeded failing unit test" ;;
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
SKIPPED=0
RAN=0

ok()   { RAN=$((RAN+1)); printf '  \033[32mOK\033[0m    %-11s %s\n' "$1" "${2:-}"; }
bad()  { RAN=$((RAN+1)); printf '  \033[31mFAIL\033[0m  %-11s %s\n' "$1" "${2:-}"; FAILED=$((FAILED+1)); }
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
    *) bad migration "REFUSED: CARR_CI_DATABASE_URL is not loopback. This applies every migration; it runs against a throwaway only."
       return ;;
  esac
  if DATABASE_URL="$dsn" run_quiet "$LOGDIR/migration.log" "$PY" tools/migrate.py --apply --yes; then
    ok migration "$(grep -c . migrations/*.sql >/dev/null 2>&1; ls migrations/*.sql | wc -l | tr -d ' ') migrations apply to an empty database"
  else
    tail -30 "$LOGDIR/migration.log" >&2
    bad migration "migrations did not apply cleanly"
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
