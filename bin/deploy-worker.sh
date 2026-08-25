#!/bin/sh
# deploy-worker.sh — the ONE sanctioned way the CARR MCP Worker ships.
#
# WHY THIS EXISTS (loop #276, 2026-08-09). Production went from 75 verbs to 66
# in the middle of a working session, because `wrangler deploy` was run from a
# checkout sitting on `dictation-phase-b-loop-243` — a branch 49 commits behind
# main whose tools.js simply does not contain nine of them. The entire Deal Room
# read/write surface disappeared (get-deal-room, deal-room-board,
# patch-deal-field, add-deal-note, set-next-step) along with presence-lease, the
# primitive two-writer discipline depends on, plus capture-queue,
# resolve-candidate and resolve-conflict.
#
# NOTHING OBJECTED. wrangler ships whatever tree happens to be checked out, and
# a verb vanishing from production is INVISIBLE until some session happens to
# call it and gets `unknown_tool`. The loss was caught only because an unrelated
# audit had recorded a "75" reading an hour earlier to compare against. That is
# luck, not a control.
#
# TWO GUARDS, because the cause and the class are different problems:
#   PREFLIGHT catches the cause — refuse to ship anything that is not exactly
#   origin/main (or an explicitly approved immutable ancestor), and refuse to
#   ship dirty mcp-server/ or dealroom/ trees (this repo
#   regularly holds another live session's uncommitted work; see rule 308ef1de).
#   COUNT CHECK catches the class — compare the verb count about to ship against
#   the count recorded by the last successful deploy, and refuse on a DROP.
#   The sole exception is an already-prepared typed `prior` recovery leg: its
#   completed/read-back Production prior is what authorises the temporary drop,
#   never a caller flag.
#
# The count is EXACT, not parsed: it imports the module and reads
# Object.keys(TOOLS).length. A regex over the source undercounts (61 vs the real
# 66 on the branch that caused this), and a guard that reports the wrong number
# is worse than no guard.
#
# Usage:
#   bin/deploy-worker.sh              # preflight, deploy, postflight
#   bin/deploy-worker.sh --check      # preflight only, ship nothing
#   bin/deploy-worker.sh --release-sha <full-40-char-sha>
#       # an approved immutable release when main moves after approval
#   bin/deploy-worker.sh --upload-version
#       # upload a Production candidate without changing traffic
#   bin/deploy-worker.sh --promote-version <cloudflare-version-id>
#       # promote that exact approved version to 100% of Production traffic
#   # Both Production modes also require the approval preimage inputs:
#       --performance-budget-ref <immutable-ref> --performance-budget-ms <ms>
#       --recovery-strategy <rollback|forward_fix>
#       --rollback-plan-ref <immutable-runbook-ref>
#
# Per rule a8c55a47, this is the same code the manual path and any automated
# path both run. There is no second way to deploy.
#
# DEPLOY PROVENANCE (Phase 1, 2026-08-13). The deployed Worker could not say
# what code it was running: no Git SHA, no schema range, no policy
# generation, in its responses or its deploy metadata. The only local signal
# was mcp-server/.last-deployed-verb-count, and on 2026-08-13 it sat un-
# bumped for ~2h after a real deploy (a Worker deploy happened ~15:21 while
# the marker's last write was ~13:23) — a verification pass very nearly
# concluded, wrongly, that the code was unshipped. A signal that can go
# silently stale is worse than none.
#
# TWO HALVES to closing it:
#   1. THE SHA IS STAMPED HERE, at deploy time, via `--var GIT_SHA:<sha>` —
#      not a [vars] line in wrangler.toml. wrangler.toml is a tracked file;
#      writing a commit's SHA into it would mean either committing a new
#      value on every deploy (noise, and a race with whatever else is
#      touching main) or leaving it perpetually stale between deploys. A CLI
#      --var is scoped to exactly this invocation, requires no commit, and
#      --keep-vars defaults to false — so a deploy that does NOT pass it
#      (i.e. `wrangler deploy` run directly, bypassing this script) drops
#      GIT_SHA from that version. That is a feature, not a gap: THE VAR
#      BEING ABSENT IS THE HONEST SIGNAL THAT THIS SCRIPT WAS BYPASSED,
#      which is exactly what /release reports (see mcp-server/src/release.js
#      — null value, "not stamped: deployed outside bin/deploy-worker.sh").
#   2. THE MARKER WRITE IS PART OF THIS SAME STEP (see postflight below) —
#      it always was, but that only protects a deploy that goes THROUGH this
#      script. Grep the repo: `wrangler deploy` is also called directly
#      (ops/completion-evidence-gate-selftest.py's own test fixture models
#      exactly that as a plausible session action), and a local file cannot
#      detect being bypassed — there is no hook a bypassed script can run.
#      So the marker is necessary-but-not-sufficient, and this script does
#      not pretend otherwise: /release, read live off the deployed Worker
#      (mcp-server/src/index.js), is the AUTHORITATIVE signal now.
#      tools/health-check.py's release check reads /release, not this file,
#      for exactly that reason — this marker remains only as a fast local
#      sanity check for a session that already trusts its own history.
set -eu

REPO="$(cd "$(dirname "$0")/.." && pwd)"
# Policy, writers and runtime dependencies always resolve from this current
# wrapper root. A typed recovery can select only a detached, exact source root
# for Worker/assets; it never gets to execute that revision's wrapper or tools.
SOURCE_ROOT="$REPO"
WORKER_DIR="$SOURCE_ROOT/mcp-server"
WRANGLER="$REPO/mcp-server/node_modules/.bin/wrangler"
# Same resolution ops/ci.sh and bin/worktree.sh use: prefer the repo venv, fall
# back to whatever python3 is on PATH. The release preflight below needs it.
PY="$REPO/.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3 || true)"

CHECK_ONLY=0
TARGET_ENV="production"
PINNED_RELEASE=""
VERSION_MODE="ordinary"
PROVIDER="cloudflare-workers"
PROVIDER_VERSION_ID=""
PERFORMANCE_BUDGET_REF=""
PERFORMANCE_BUDGET_MS=""
RECOVERY_STRATEGY=""
ROLLBACK_PLAN_REF=""
REQUESTED_RELEASE_KEY=""
RECOVERY_ATTEMPT_ID=""
RECOVERY_STEP="standalone"
RECOVERY_PRIOR_RELEASE_KEY=""
STAGING_RECEIPT_KEY=""
EXACT_SOURCE_ROOT=""
# Filled only from the exact immutable release manifest after preflight.
EXPECTED_PROGRAM6_ACTIONS=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --check)         CHECK_ONLY=1 ;;
    --env)
      [ "$#" -ge 2 ] || { echo "deploy-worker: --env needs a name" >&2; exit 64; }
      TARGET_ENV="$2"
      shift
      ;;
    --performance-budget-ref)
      [ "$#" -ge 2 ] || { echo "deploy-worker: --performance-budget-ref needs a reference" >&2; exit 64; }
      PERFORMANCE_BUDGET_REF="$2"; shift ;;
    --performance-budget-ms)
      [ "$#" -ge 2 ] || { echo "deploy-worker: --performance-budget-ms needs milliseconds" >&2; exit 64; }
      PERFORMANCE_BUDGET_MS="$2"; shift ;;
    --recovery-strategy)
      [ "$#" -ge 2 ] || { echo "deploy-worker: --recovery-strategy needs an approved strategy" >&2; exit 64; }
      RECOVERY_STRATEGY="$2"; shift ;;
    --rollback-plan-ref)
      [ "$#" -ge 2 ] || { echo "deploy-worker: --rollback-plan-ref needs an immutable runbook reference" >&2; exit 64; }
      ROLLBACK_PLAN_REF="$2"; shift ;;
    --release-sha)
      [ "$#" -ge 2 ] || { echo "deploy-worker: --release-sha needs a full commit SHA" >&2; exit 64; }
      PINNED_RELEASE="$2"
      shift
      ;;
    --release-key)
      [ "$#" -ge 2 ] || { echo "deploy-worker: --release-key needs a canonical key" >&2; exit 64; }
      REQUESTED_RELEASE_KEY="$2"; shift ;;
    --recovery-attempt-id)
      [ "$#" -ge 2 ] || { echo "deploy-worker: --recovery-attempt-id needs a UUID" >&2; exit 64; }
      RECOVERY_ATTEMPT_ID="$2"; shift ;;
    --recovery-step)
      [ "$#" -ge 2 ] || { echo "deploy-worker: --recovery-step needs current_before|prior|current_after|restore_only" >&2; exit 64; }
      RECOVERY_STEP="$2"; shift ;;
    --recovery-prior-release-key)
      [ "$#" -ge 2 ] || { echo "deploy-worker: --recovery-prior-release-key needs a key" >&2; exit 64; }
      RECOVERY_PRIOR_RELEASE_KEY="$2"; shift ;;
    --staging-receipt-idempotency-key)
      [ "$#" -ge 2 ] || { echo "deploy-worker: --staging-receipt-idempotency-key needs a UUID" >&2; exit 64; }
      STAGING_RECEIPT_KEY="$2"; shift ;;
    --internal-exact-source-root)
      [ "$#" -ge 2 ] || { echo "deploy-worker: --internal-exact-source-root needs a path" >&2; exit 64; }
      [ -z "$EXACT_SOURCE_ROOT" ] || { echo "deploy-worker: --internal-exact-source-root may appear once" >&2; exit 64; }
      EXACT_SOURCE_ROOT="$2"; shift ;;
    --upload-version)
      [ "$VERSION_MODE" = "ordinary" ] \
        || { echo "deploy-worker: --upload-version and --promote-version are mutually exclusive" >&2; exit 64; }
      VERSION_MODE="upload"
      ;;
    --promote-version)
      [ "$#" -ge 2 ] || { echo "deploy-worker: --promote-version needs an immutable provider version id" >&2; exit 64; }
      [ "$VERSION_MODE" = "ordinary" ] \
        || { echo "deploy-worker: --upload-version and --promote-version are mutually exclusive" >&2; exit 64; }
      VERSION_MODE="promote"
      PROVIDER_VERSION_ID="$2"
      shift
      ;;
    *) echo "deploy-worker: unknown argument '$1'" >&2; exit 64 ;;
  esac
  shift
done

fail() { echo ""; echo "REFUSED: $1" >&2; echo "" >&2; exit 1; }

if [ "$VERSION_MODE" != "ordinary" ] && [ "$TARGET_ENV" != "production" ]; then
  fail "provider-version operations are Production-only; staging is a source rehearsal and receives its own build."
fi
case "$RECOVERY_STEP" in
  standalone)
    [ -z "$RECOVERY_ATTEMPT_ID$RECOVERY_PRIOR_RELEASE_KEY" ] \
      || fail "standalone staging deploy cannot carry recovery attempt/prior fields."
    ;;
  current_before|prior|current_after|restore_only)
    [ "$TARGET_ENV" = "staging" ] && [ -n "$RECOVERY_ATTEMPT_ID" ] \
      && [ -n "$RECOVERY_PRIOR_RELEASE_KEY" ] && [ -n "$REQUESTED_RELEASE_KEY" ] \
      || fail "recovery deploy requires staging plus --release-key, --recovery-attempt-id, --recovery-prior-release-key and --recovery-step."
    printf '%s\n' "$RECOVERY_ATTEMPT_ID" | grep -Eq \
      '^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$' \
      || fail "--recovery-attempt-id must be an exact UUID."
    RECOVERY_ATTEMPT_ID="$(printf '%s' "$RECOVERY_ATTEMPT_ID" | tr 'A-F' 'a-f')"
    ;;
  *) fail "--recovery-step must be standalone|current_before|prior|current_after|restore_only." ;;
esac
if [ -n "$EXACT_SOURCE_ROOT" ]; then
  [ "$VERSION_MODE" = "ordinary" ] && [ "$TARGET_ENV" = "staging" ] \
    && [ "$RECOVERY_STEP" != "standalone" ] && [ -n "$PINNED_RELEASE" ] \
    || fail "an exact source root is internal to a typed staging recovery step."
  SOURCE_ROOT="$("$PY" "$REPO/tools/validate-exact-recovery-source.py" \
    --root "$EXACT_SOURCE_ROOT" --sha "$PINNED_RELEASE")" \
    || fail "the internal exact source root is not the bound clean detached source."
  WORKER_DIR="$SOURCE_ROOT/mcp-server"
fi
if [ "$VERSION_MODE" = "ordinary" ] && [ "$TARGET_ENV" = "production" ]; then
  fail "Production source deploy is disabled. Upload an immutable candidate with
  --upload-version, bind that provider version to an approved release, then use
  --promote-version <id>."
fi
if [ "$VERSION_MODE" = "promote" ]; then
  printf '%s\n' "$PROVIDER_VERSION_ID" | grep -Eq \
    '^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$' \
    || fail "--promote-version must be the exact immutable Cloudflare UUID."
  PROVIDER_VERSION_ID="$(printf '%s' "$PROVIDER_VERSION_ID" | tr 'A-F' 'a-f')"
fi

cd "$REPO"
[ -x "$WRANGLER" ] || fail "wrangler not found at $WRANGLER (run npm install in mcp-server/)."
[ -x "$PY" ] || fail "python not found; release truth cannot be checked."
if [ "$TARGET_ENV" = "production" ]; then
  [ -n "$PERFORMANCE_BUDGET_REF" ] && [ -n "$PERFORMANCE_BUDGET_MS" ] \
    && [ -n "$RECOVERY_STRATEGY" ] && [ -n "$ROLLBACK_PLAN_REF" ] \
    || fail "Production performance budget/ref, recovery strategy, and rollback plan ref are required; they are approval inputs, not deploy defaults."
fi

HEAD_SHA=""
SHIPPING=""

# ---------- preflight 1: exactly origin/main ----------
echo "== preflight =="
if [ "$VERSION_MODE" = "promote" ]; then
  echo "  OK  immutable provider promotion — source checkout and build preflights are skipped"
  echo "      release truth below supplies the SHA bound to $PROVIDER_VERSION_ID"
else
git -C "$REPO" fetch origin main --quiet 2>/dev/null || fail "could not reach origin to verify main."

HEAD_SHA="$(git -C "$SOURCE_ROOT" rev-parse HEAD)"
MAIN_SHA="$(git -C "$SOURCE_ROOT" rev-parse origin/main)"
BRANCH="$(git -C "$SOURCE_ROOT" rev-parse --abbrev-ref HEAD)"

if [ -n "$PINNED_RELEASE" ]; then
  [ "${#PINNED_RELEASE}" -eq 40 ] \
    || fail "--release-sha must be the full immutable 40-character commit SHA."
  PINNED_SHA="$(git -C "$SOURCE_ROOT" rev-parse --verify "${PINNED_RELEASE}^{commit}" 2>/dev/null)" \
    || fail "approved release SHA does not resolve to a commit."
  [ "$PINNED_RELEASE" = "$PINNED_SHA" ] \
    || fail "--release-sha must be the exact canonical full SHA, not an abbreviation or tag."
  [ "$HEAD_SHA" = "$PINNED_SHA" ] \
    || fail "checkout HEAD does not equal the approved release SHA."
  git -C "$SOURCE_ROOT" merge-base --is-ancestor "$PINNED_SHA" origin/main \
    || fail "approved release SHA is not an ancestor of fetched origin/main."
  echo "  OK  pinned approved release: $PINNED_SHA (ancestor of origin/main $MAIN_SHA)"
elif [ "$TARGET_ENV" != "production" ]; then
  # STAGING IS FOR CODE THAT IS NOT ON MAIN YET — that is the entire point of
  # having it. Requiring origin/main here would mean the only way to rehearse a
  # change is to merge it first, which inverts the purpose of a rehearsal
  # environment. The clean-tree check below still applies, so what ships is
  # always a commit someone can name and return to.
  echo "  OK  env=$TARGET_ENV — branch check skipped on purpose (staging exists to run unmerged code)"
  echo "      shipping $BRANCH @ $HEAD_SHA"
elif [ "$HEAD_SHA" != "$MAIN_SHA" ]; then
  BEHIND="$(git -C "$SOURCE_ROOT" rev-list --count "HEAD..origin/main")"
  AHEAD="$(git -C "$SOURCE_ROOT" rev-list --count "origin/main..HEAD")"
  fail "this checkout is not origin/main.
  branch:  $BRANCH
  HEAD:    $HEAD_SHA
  main:    $MAIN_SHA
  $BEHIND commit(s) behind main, $AHEAD ahead.

  Deploying from here would ship main's absence, not your presence: any verb
  that exists on main but not here is REMOVED from production silently.
  That is loop #276, exactly. Deploy from a clean main checkout or a worktree:
      git worktree add /tmp/deploy-main main"
else
  echo "  OK  HEAD is origin/main ($MAIN_SHA)"
fi

# ---------- preflight 2: nothing uncommitted in the Worker or its assets ----------
# node_modules is excluded BY NAME, not left to .gitignore. mcp-server/.gitignore
# ignores `node_modules/` with a trailing slash, which matches a directory and
# NOT a symlink — and symlinking node_modules from a primary checkout is exactly
# how you deploy from a throwaway worktree without a second npm install. Without
# this exclusion the guard refuses every worktree deploy, and a guard that always
# refuses is a guard everyone learns to skip.
# .last-deployed-verb-count is excluded for a different reason: postflight WRITES
# it, so leaving it in the dirty check means every deploy poisons the next one
# until the file is committed. It is a guard artifact and never ships to the
# Worker. The postflight still tells you to commit it, because an uncommitted
# baseline protects only this checkout.
DIRTY_LIST="$(git -C "$SOURCE_ROOT" status --porcelain -- mcp-server/ dealroom/ \
  | grep -v 'mcp-server/node_modules' \
  | grep -v 'mcp-server/.last-deployed-verb-count' || true)"
DIRTY="$(printf '%s' "$DIRTY_LIST" | grep -c . || true)"
if [ "$DIRTY" != "0" ]; then
  printf '%s\n' "$DIRTY_LIST" >&2
  fail "mcp-server/ or dealroom/ has $DIRTY uncommitted change(s) above.
  Shipping them would put code or assets in production that are in no commit, and this
  repo regularly holds ANOTHER live session's work (rule 308ef1de). Commit
  them deliberately, or deploy from a clean worktree."
fi
echo "  OK  mcp-server/ and dealroom/ are clean"

# ---------- preflight 2b: what this deploy will ATTACH to ----------
# Every other preflight is about the ARTIFACT — right commit, clean tree, no verb
# loss. All three passed on 2026-08-13 while a staging deploy took over all three
# production custom domains, because nothing here looked at ATTACHMENT. This is
# that check. Production is exempt: it is allowed to claim its own hostnames.
if [ "$TARGET_ENV" != "production" ]; then
  PYBIN="$REPO/.venv/bin/python"
  [ -x "$PYBIN" ] || PYBIN=python3
  if ! "$PYBIN" "$REPO/ops/deploy-attachment-check.py" "$WORKER_DIR/wrangler.toml" "$TARGET_ENV"; then
    fail "refusing to deploy env=$TARGET_ENV — see the attachment check above."
  fi
fi

# ---------- preflight 3: verb count did not shrink ----------

# The count moved to ops/verb-count.sh on 2026-08-13 so that CI can run this same
# guard at MERGE time, not only here at DEPLOY time. Same code, two callers
# (rule a8c55a47) — deliberately NOT a second copy that can drift from this one.
SHIPPING="$(sh "$REPO/ops/verb-count.sh" "$WORKER_DIR" 2>/dev/null || true)"

case "$SHIPPING" in
  ''|*[!0-9]*) fail "could not count verbs in $WORKER_DIR/src/tools.js — the import failed.
  Refusing rather than shipping an unmeasured registry." ;;
esac
echo "  OK  registry imports cleanly: $SHIPPING verbs about to ship"

# THE BASELINE COMES FROM THE LEDGER, NOT A FILE (defect d737c09c, 2026-08-16).
# It used to live in mcp-server/.last-deployed-verb-count.<env>, which the
# postflight told you to commit — and committing it fails
# ops/release-manifest-selftest.py, because manifest artifact_paths are
# ['mcp-server','dealroom'] so the file sits inside the digested artifact while
# the digest skips dotfiles. It moved the deployed TREE without moving its
# DIGEST, the exact condition that test guards. Two halves of one control giving
# opposite instructions.
#
# The file was always a DUPLICATE: every deploy already records --verb-count
# into ops.deployment, and since the ledger fix earlier today it does so for
# staging too. Write law 14181e60 settles which copy survives.
PREVIOUS=""
LEDGER_RC=0
# STDERR IS KEPT, and that is the point. This read used to send stderr to
# /dev/null, so ops/last-deployed-verb-count.py could tell us the baseline was
# stale and nobody would ever see it — the same "printed into a log nobody
# opens" shape as the nightly steps that reported skipped for five days
# (defect 3b21767e). stdout is still the number and nothing else.
BASELINE_STDERR="$(mktemp)"
PREVIOUS="$("$PY" "$REPO/ops/last-deployed-verb-count.py" carr-mcp "$TARGET_ENV" 2>"$BASELINE_STDERR")" \
  || LEDGER_RC=$?
case "$LEDGER_RC" in
  0)
    if [ -n "$PREVIOUS" ] && [ "$SHIPPING" -lt "$PREVIOUS" ]; then
      LOST=$((PREVIOUS - SHIPPING))
      if [ "$RECOVERY_STEP" != "prior" ] || [ "$TARGET_ENV" != "staging" ]; then
        fail "this deploy would REMOVE $LOST verb(s) from $TARGET_ENV.
  last deployed: $PREVIOUS
  about to ship: $SHIPPING

  Source and standalone deploys cannot waive the verb-loss guard. Only the
  exact prepared `prior` leg of a typed staging recovery may temporarily shrink."
      fi
      [ -n "$STAGING_RECEIPT_KEY" ] \
        || fail "typed recovery prior shrink needs a staging receipt idempotency key."
      printf '%s\n' "$STAGING_RECEIPT_KEY" | grep -Eq \
        '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' \
        || fail "--staging-receipt-idempotency-key must be a lowercase canonical UUID."
      # The existing sole writer validates the current candidate, distinct
      # completed prior, shared service, exact prior SHA and Production
      # readback BEFORE returning a tag.  This prepare is replayed later in
      # the staging deploy path; it is not a caller-controlled override.
      TYPED_PRIOR_TAG="$("$PY" "$REPO/tools/ops-record.py" staging-attempt prepare \
        --idempotency-key "$STAGING_RECEIPT_KEY" --release-key "$REQUESTED_RELEASE_KEY" \
        --prior-release-key "$RECOVERY_PRIOR_RELEASE_KEY" \
        --recovery-attempt-id "$RECOVERY_ATTEMPT_ID" --recovery-step prior \
        --git-sha "$HEAD_SHA" --correlation "$RECOVERY_ATTEMPT_ID" \
        --field expected_provider_tag)" \
        || fail "the typed recovery prior was not durably prepared; verb shrink is refused."
      printf '%s\n' "$TYPED_PRIOR_TAG" | grep -Eq '^carr-staging-[0-9a-f]{32}$' \
        || fail "the typed recovery prior returned no exact provider tag; verb shrink is refused."
      echo "  !!  shrinking by $LOST verb(s), allowed only by the exact prepared recovery prior"
    else
      echo "  OK  no verb loss (last deployed to $TARGET_ENV: $PREVIOUS)"
    fi
    if grep -q 'STALE BASELINE' "$BASELINE_STDERR" 2>/dev/null; then
      echo "  !!  the number above is STALE and the guard above is weaker than it looks:" >&2
      sed 's/^/      /' "$BASELINE_STDERR" >&2
    fi
    ;;
  3)
    echo "  --  no previous deployment recorded for $TARGET_ENV; this run establishes the baseline"
    ;;
  *)
    # FAIL CLOSED, and no escape flag. Not caution: the Worker being deployed
    # needs Postgres for every verb it serves, so a deploy attempted while the
    # ledger is unreachable ships something that cannot work anyway. Refusing
    # costs nothing real, and a loss guard that cannot check must never wave a
    # deploy through — that is exactly how loop #276 happened.
    fail "could not read the previous verb count from the ledger (exit $LEDGER_RC).
  The verb-loss guard cannot run, so this deploy is refused rather than shipped blind.
  Diagnose with: .venv/bin/python ops/last-deployed-verb-count.py carr-mcp $TARGET_ENV
  If Postgres is down, the Worker you are about to ship cannot serve a verb anyway."
    ;;
esac
rm -f "$BASELINE_STDERR"
fi

# record_deployment <state> <correlation> [identity-readback] — attach what just
# shipped to the release that authorised it.
#
# ONE FUNCTION, THREE CALL SITES, because the three outcomes of a deploy are
# genuinely different facts and the ledger has to hold whichever one happened.
# complete carries the final read-back and means the suite proved the Worker;
# one explicit verifying receipt carries the earlier machine identity read-back,
# while every other verifying state means the suite could not prove completion.
# failed means a check ran and the Worker answered wrongly. Recording all three
# as one state, or recording only the happy one, is how a deploy history starts
# reading greener than the deploys were.
#
# A routine/non-Production ledger miss remains loud but non-fatal: the code has
# already shipped. Program 5 Production assurance is stricter. The durable live
# identity receipt and the final complete release are acceptance outcomes, so
# their absence must return non-zero even though the traffic change already
# happened. The recovery instructions below say exactly what remains to record.
record_deployment() {
  rd_state="$1"
  rd_corr="${2:-}"
  rd_readback_kind="${3:-}"
  rd_must_record=0
  if [ "$TARGET_ENV" = "production" ] \
      && { [ "$rd_state" = "complete" ] \
           || [ "$rd_readback_kind" = "identity-readback" ]; }; then
    rd_must_record=1
  fi
  if [ ! -x "$PY" ] || [ ! -f "$REPO/tools/ops-record.py" ]; then
    echo "  !! deployment assurance recorder is unavailable after traffic changed."
    [ "$rd_must_record" = "0" ] || return 1
    return 0
  fi

  set +e
  # The first read-back is the machine-verified serving identity. Complete adds
  # a later receipt after golden/performance pass. Both are real observations;
  # ordinary verifying states must not manufacture a timestamp.
  if [ "$rd_state" = "complete" ] \
      || [ "$rd_readback_kind" = "identity-readback" ]; then
    rd_read_back="--read-back-at now"
  else
    rd_read_back=""
  fi
  rd_provider_args=""
  if [ "$TARGET_ENV" = "production" ]; then
    rd_provider_args="--provider $PROVIDER --provider-version-id $PROVIDER_VERSION_ID"
  fi
  rd_verb_args=""
  [ -n "$SHIPPING" ] && rd_verb_args="--verb-count $SHIPPING"
  rd_evidence_ref="${DEPLOYMENT_EVIDENCE_REF:-bin/smoke-and-record.sh#${rd_corr:-unknown}}"
  rd_failure_args=""
  if [ "$rd_state" = "failed" ]; then
    rd_failure_args="--failure-class ${DEPLOYMENT_FAILURE_CLASS:-golden_workflow_failed}"
  fi
  # shellcheck disable=SC2086
  "$PY" "$REPO/tools/ops-record.py" deployment \
    --service carr-mcp --environment "$TARGET_ENV" --state "$rd_state" \
    --git-sha "$HEAD_SHA" $rd_provider_args $rd_verb_args \
    ${rd_corr:+--correlation "$rd_corr"} \
    ${RELEASE_KEY:+--release-key "$RELEASE_KEY"} \
    ${rd_state:+--source-kind wrapper} --source-ref bin/deploy-worker.sh \
    $rd_read_back \
    --verification-evidence-ref "$rd_evidence_ref" \
    $rd_failure_args \
    >/dev/null 2>&1
  rd_rc=$?
  set -e
  if [ "$rd_rc" -ne 0 ]; then
    echo "  !! the deploy shipped but its ledger row did NOT record (exit $rd_rc)."
    echo "     Record it by hand so the release keeps its deployment:"
    echo "       .venv/bin/python tools/ops-record.py deployment --service carr-mcp \\"
    echo "         --environment $TARGET_ENV --state $rd_state --git-sha $HEAD_SHA \\"
    [ "$TARGET_ENV" != "production" ] || \
      echo "         --provider $PROVIDER --provider-version-id $PROVIDER_VERSION_ID \\"
    echo "         --release-key ${RELEASE_KEY:-<key>} --source-kind wrapper \\"
    echo "         --source-ref bin/deploy-worker.sh \\"
    [ -z "$rd_read_back" ] || echo "         $rd_read_back \\"
    echo "         --verification-evidence-ref $rd_evidence_ref"
    [ "$rd_must_record" = "0" ] || return 1
  else
    echo "  recorded this deploy as $rd_state against release ${RELEASE_KEY:-<none>}"
  fi

  # AND CLOSE THE RELEASE, when the deploy actually landed and was verified.
  # Without this the release sits at `approved` while its own deployment reads
  # `complete` — observed on the first real release, 2026-08-16, where the
  # manifest view showed a landed deploy against a release still waiting to
  # ship. Only the complete path closes it: `verifying` means nothing was
  # proven and `failed` means the opposite, and neither is a finished release.
  if [ "$rd_state" = "complete" ] && [ -n "$RELEASE_KEY" ]; then
    set +e
    # Approval owns the independent verifier pair immutably.  Completion adds
    # deployment/performance evidence through its own gates; it must not try to
    # replace that approval-bound verifier with the golden-suite runner.
    "$PY" "$REPO/tools/ops-record.py" release complete --key "$RELEASE_KEY" >/dev/null 2>&1
    rc_rel=$?
    set -e
    if [ "$rc_rel" -eq 0 ]; then
      echo "  release $RELEASE_KEY is complete"
    else
      echo "  !! the deploy landed but release $RELEASE_KEY did not close (exit $rc_rel)."
      echo "     Close it by hand so the release and its deployment stop disagreeing:"
      echo "       .venv/bin/python tools/ops-record.py release complete --key $RELEASE_KEY"
      [ "$TARGET_ENV" != "production" ] || return 1
    fi
  fi
  return 0
}

# ---------- preflight 4: release truth (P0-1) ----------
# THE DEPLOY IS THE MOMENT THE MANIFEST HAS TO EXIST, not a step somebody
# performs beside it. Every earlier preflight asks whether the ARTIFACT is
# right; this one asks whether anyone APPROVED it, and it is the wrapper half
# of P0-1. The database half (migration 0130) refuses a production deployment
# naming an unapproved or expired release, so this check exists to fail EARLY
# and legibly rather than half way through a wrangler run.
#
# It builds the manifest from the SHA about to ship — the same tools/
# release-manifest.py any human or CI run would use, never a second copy of the
# digest decision (rule a8c55a47) — and hands the plan hash to the require
# check, so an approval given to a DIFFERENT plan is caught here by name.
#
# NOT ENFORCED IS SAID OUT LOUD. Where DATABASE_URL is absent, or 0130 has not
# been applied, the require check prints exactly that and returns success. A
# wrapper that refused on the database's behalf would be claiming a protection
# the database is not providing, and any other deploy path would bypass it
# anyway. Silence is the only outcome ruled out.
RELEASE_KEY="$REQUESTED_RELEASE_KEY"
RELEASE_PLAN_HASH=""
RELEASE_MANIFEST=""
if [ "$VERSION_MODE" = "promote" ]; then
  echo ""
  echo "== preflight: immutable release truth =="
  set +e
  RELEASE_BINDING="$("$PY" "$REPO/tools/ops-record.py" release require \
    --environment production --provider "$PROVIDER" \
    --provider-version-id "$PROVIDER_VERSION_ID")"
  REQUIRE_RC=$?
  set -e
  [ "$REQUIRE_RC" -eq 0 ] \
    || fail "no live approval binds Production to $PROVIDER:$PROVIDER_VERSION_ID."
  # Production provider lookup returns exactly `<release-key> <git-sha>` so
  # promotion provenance comes from the approved immutable object, not HEAD.
  set -- $RELEASE_BINDING
  [ "$#" -eq 2 ] \
    || fail "release truth returned no exact release/SHA binding for $PROVIDER_VERSION_ID."
  RELEASE_KEY="$1"
  HEAD_SHA="$2"
  printf '%s\n' "$HEAD_SHA" | grep -Eq '^[0-9A-Fa-f]{40}$' \
    || fail "approved release $RELEASE_KEY has no canonical git SHA."
  echo "  approved release: $RELEASE_KEY"
  echo "  provider version: $PROVIDER_VERSION_ID"
  echo "  recorded git SHA: $HEAD_SHA"

  # The first exact UUID lookup reveals the SHA the approver signed. Recompute
  # the evidence from that git object without uploading or building a Worker,
  # bind the same canonical provider UUID, then ask release truth a second time
  # with every immutable dimension and the freshly computed plan hash.
  PROMOTION_SOURCE_MANIFEST="$(mktemp "${TMPDIR:-/tmp}/carr-promotion-source-manifest.XXXXXX")"
  PROMOTION_BOUND_MANIFEST="$(mktemp "${TMPDIR:-/tmp}/carr-promotion-bound-manifest.XXXXXX")"
  if ! "$PY" "$REPO/tools/release-manifest.py" build --sha "$HEAD_SHA" \
      --environment production --performance-budget-ref "$PERFORMANCE_BUDGET_REF" \
      --performance-budget-ms "$PERFORMANCE_BUDGET_MS" \
      --recovery-strategy "$RECOVERY_STRATEGY" \
      --rollback-plan-ref "$ROLLBACK_PLAN_REF" > "$PROMOTION_SOURCE_MANIFEST"; then
    fail "approved release evidence cannot be rebuilt from git SHA $HEAD_SHA."
  fi
  if ! "$PY" "$REPO/tools/release-manifest.py" bind-provider \
      --manifest "$PROMOTION_SOURCE_MANIFEST" --provider "$PROVIDER" \
      --provider-version-id "$PROVIDER_VERSION_ID" > "$PROMOTION_BOUND_MANIFEST"; then
    fail "approved provider identity cannot be rebound to recomputed evidence."
  fi
  RELEASE_MANIFEST="$PROMOTION_BOUND_MANIFEST"
  RELEASE_PLAN_HASH="$("$PY" "$REPO/tools/release-manifest.py" plan-hash \
    --manifest "$RELEASE_MANIFEST")"
  [ -n "$RELEASE_PLAN_HASH" ] \
    || fail "recomputed provider-bound evidence produced no plan hash."
  set +e
  RECONFIRMED_BINDING="$("$PY" "$REPO/tools/ops-record.py" release require \
    --sha "$HEAD_SHA" --environment production --provider "$PROVIDER" \
    --provider-version-id "$PROVIDER_VERSION_ID" \
    --plan-hash "$RELEASE_PLAN_HASH")"
  REQUIRE_RC=$?
  set -e
  [ "$REQUIRE_RC" -eq 0 ] \
    || fail "approval no longer matches the recomputed SHA/provider/version plan."
  [ "$RECONFIRMED_BINDING" = "$RELEASE_BINDING" ] \
    || fail "release binding changed between UUID resolution and final approval check."
  echo "  recomputed plan: $RELEASE_PLAN_HASH"
elif [ "$RECOVERY_STEP" != "standalone" ]; then
  echo ""
  echo "== preflight: typed recovery release truth =="
  echo "  candidate release: $RELEASE_KEY"
  echo "  prior release: $RECOVERY_PRIOR_RELEASE_KEY"
  echo "  recovery attempt/step: $RECOVERY_ATTEMPT_ID / $RECOVERY_STEP"
  RELEASE_MANIFEST="$(mktemp "${TMPDIR:-/tmp}/carr-recovery-release-manifest.XXXXXX")"
  "$PY" "$REPO/tools/release-manifest.py" build --sha "$HEAD_SHA" \
    --environment staging > "$RELEASE_MANIFEST" \
    || fail "recovery release evidence cannot be rebuilt from git SHA $HEAD_SHA."
elif [ -f "$REPO/tools/release-manifest.py" ]; then
  echo ""
  echo "== preflight: release truth =="
  RELEASE_MANIFEST="$(mktemp "${TMPDIR:-/tmp}/carr-release-manifest.XXXXXX")"
  if [ "$TARGET_ENV" = "production" ]; then
    manifest_build_args="--performance-budget-ref $PERFORMANCE_BUDGET_REF --performance-budget-ms $PERFORMANCE_BUDGET_MS --recovery-strategy $RECOVERY_STRATEGY --rollback-plan-ref $ROLLBACK_PLAN_REF"
  else
    manifest_build_args=""
  fi
  # shellcheck disable=SC2086
  if "$PY" "$REPO/tools/release-manifest.py" build --sha "$HEAD_SHA" \
       --environment "$TARGET_ENV" $manifest_build_args > "$RELEASE_MANIFEST" 2>/dev/null; then
    RELEASE_PLAN_HASH="$("$PY" "$REPO/tools/release-manifest.py" plan-hash \
                          --manifest "$RELEASE_MANIFEST" 2>/dev/null)"
    echo "  manifest built for ${HEAD_SHA} — plan ${RELEASE_PLAN_HASH:-unknown}"
  else
    echo "  !! could not build the release manifest for $HEAD_SHA"
  fi

  if [ "$VERSION_MODE" = "upload" ]; then
    [ -n "$RELEASE_PLAN_HASH" ] \
      || fail "the release manifest did not produce a plan hash; version upload refused."
    echo "  upload may proceed; approval happens only after Cloudflare returns the immutable version id"
  else
    set +e
    RELEASE_KEY="$("$PY" "$REPO/tools/ops-record.py" release require \
                     --sha "$HEAD_SHA" --environment "$TARGET_ENV" \
                     --plan-hash "$RELEASE_PLAN_HASH")"
    REQUIRE_RC=$?
    set -e
    if [ "$REQUIRE_RC" -eq 3 ]; then
      fail "no live approval for $HEAD_SHA in $TARGET_ENV. The reason and the exact
commands are printed above. This is P0-1: a production deploy names an approved
release or it does not happen."
    fi
    [ -n "$RELEASE_KEY" ] && echo "  approved release: $RELEASE_KEY"
  fi
fi

# Read the expected Program 6 posture from the manifest reconstructed from the
# exact release SHA, never from this checkout or a hard-coded activation bit.
# Its source config fingerprint is in the approval preimage; this turns the
# serving readback into a comparison against the reviewed immutable plan.
[ -n "${RELEASE_MANIFEST:-}" ] && [ -s "$RELEASE_MANIFEST" ] \
  || fail "release manifest is required to bind the Program 6 serving posture."
EXPECTED_PROGRAM6_ACTIONS="$("$PY" "$REPO/tools/release-manifest.py" \
  program6-posture --manifest "$RELEASE_MANIFEST")" \
  || fail "release manifest has no valid Program 6 posture."
case "$EXPECTED_PROGRAM6_ACTIONS" in
  enabled|disabled) ;;
  *) fail "release manifest returned an invalid Program 6 posture." ;;
esac

if [ "$CHECK_ONLY" = "1" ]; then
  echo ""
  echo "check only — nothing deployed."
  exit 0
fi

# Cost admission binds the release wrapper before any Wrangler operation.  The
# preflights above are the evidence; a direct Wrangler call remains outside the
# sanctioned path and is independently refused by the command gate.
"$PY" "$REPO/ops/platform-metering-gate.py" --gate cloudflare-worker-release \
  --release-preflight-green \
  --performance-budget-ref "${PERFORMANCE_BUDGET_REF:-${RELEASE_PLAN_HASH:-staging-release-preflight}}" \
  --release-candidate-count 1 >/dev/null \
  || fail "cloudflare-worker-release metering admission refused."

# ---------- deploy ----------
echo ""
echo "== deploy =="
cd "$WORKER_DIR"
# -- provider-version upload --
if [ "$VERSION_MODE" = "upload" ]; then
  set +e
  VERSION_UPLOAD_OUTPUT="$("$WRANGLER" versions upload --var "GIT_SHA:$HEAD_SHA" 2>&1)"
  VERSION_UPLOAD_RC=$?
  set -e
  printf '%s\n' "$VERSION_UPLOAD_OUTPUT"
  [ "$VERSION_UPLOAD_RC" -eq 0 ] || fail "Cloudflare version upload failed."
  PROVIDER_VERSION_ID="$(printf '%s\n' "$VERSION_UPLOAD_OUTPUT" \
    | sed -nE 's/^.*Worker Version ID:[[:space:]]*([0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}).*$/\1/p' \
    | tail -n 1 | tr 'A-F' 'a-f')"
  [ -n "$PROVIDER_VERSION_ID" ] \
    || fail "Cloudflare uploaded a version but returned no parseable immutable version id; traffic was not changed."
  BOUND_RELEASE_MANIFEST="$(mktemp "${TMPDIR:-/tmp}/carr-bound-release-manifest.XXXXXX")"
  if ! "$PY" "$REPO/tools/release-manifest.py" bind-provider \
      --manifest "$RELEASE_MANIFEST" --provider "$PROVIDER" \
      --provider-version-id "$PROVIDER_VERSION_ID" > "$BOUND_RELEASE_MANIFEST"; then
    fail "the uploaded version could not be bound into its release manifest; traffic was not changed."
  fi
  RELEASE_MANIFEST="$BOUND_RELEASE_MANIFEST"
  RELEASE_PLAN_HASH="$("$PY" "$REPO/tools/release-manifest.py" plan-hash \
    --manifest "$RELEASE_MANIFEST")"
  [ -n "$RELEASE_PLAN_HASH" ] \
    || fail "the provider-bound release manifest has no approval plan hash; traffic was not changed."
  echo ""
  echo "uploaded only — Production traffic was not changed"
  echo "  provider: $PROVIDER"
  echo "  provider version: $PROVIDER_VERSION_ID"
  echo "  git SHA: $HEAD_SHA"
  echo "  plan hash: $RELEASE_PLAN_HASH"
  echo ""
  echo "Record this exact candidate before Joe approves its plan hash:"
  echo "  .venv/bin/python tools/ops-record.py release candidate --key <key> \\"
  echo "    --environment production --provider $PROVIDER \\"
  echo "    --provider-version-id $PROVIDER_VERSION_ID --manifest $RELEASE_MANIFEST ..."
  echo "Then record the actual release-linked recovery rehearsal before Joe approves:"
  echo "  .venv/bin/python tools/ops-record.py run --kind check --service carr-mcp \\"
  echo "    --key recovery.rehearsal.worker --state succeeded --environment staging \\"
  echo "    --release-key <key> --source-ref <rehearsal-run> --evidence-ref <receipt>"
  exit 0
fi

# -- provider-version promotion --
if [ "$VERSION_MODE" = "promote" ]; then
  "$WRANGLER" versions deploy "${PROVIDER_VERSION_ID}@100" --yes
else
# -- ordinary source deploy --
  # Ordinary source deployment is staging/rehearsal only. Production reaches
  # this file solely through upload followed by exact immutable promotion.
  [ "$TARGET_ENV" != "production" ] \
    || fail "Production cannot rebuild source during promotion."
  if [ "$TARGET_ENV" = "staging" ]; then
    STAGING_TARGET_HOST="$("$PY" "$REPO/tools/ops-record.py" staging-target --field host)" \
      || fail "checked-in staging target config is not exact."
    [ -n "$STAGING_RECEIPT_KEY" ] \
      || STAGING_RECEIPT_KEY="$(uuidgen | tr 'A-Z' 'a-z')"
    printf '%s\n' "$STAGING_RECEIPT_KEY" | grep -Eq \
      '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' \
      || fail "--staging-receipt-idempotency-key must be a lowercase canonical UUID."
    if [ "$RECOVERY_STEP" = "standalone" ]; then
      CARR_CORRELATION_ID="${CARR_CORRELATION_ID:-$(uuidgen | tr 'A-Z' 'a-z')}"
    else
      [ -z "${CARR_CORRELATION_ID:-}" ] || [ "$CARR_CORRELATION_ID" = "$RECOVERY_ATTEMPT_ID" ] \
        || fail "ambient CARR_CORRELATION_ID differs from the recovery attempt."
      CARR_CORRELATION_ID="$RECOVERY_ATTEMPT_ID"
    fi
    export CARR_CORRELATION_ID

    staging_attempt() {
      attempt_action="$1"; shift
      if [ "$RECOVERY_STEP" = "restore_only" ]; then
        "$PY" "$REPO/tools/ops-record.py" staging-restore-only "$attempt_action" \
          --idempotency-key "$STAGING_RECEIPT_KEY" --release-key "$RELEASE_KEY" \
          --prior-release-key "$RECOVERY_PRIOR_RELEASE_KEY" \
          --recovery-attempt-id "$RECOVERY_ATTEMPT_ID" --git-sha "$HEAD_SHA" \
          --correlation "$CARR_CORRELATION_ID" "$@"
      elif [ "$RECOVERY_STEP" = "standalone" ]; then
        "$PY" "$REPO/tools/ops-record.py" staging-attempt "$attempt_action" \
          --idempotency-key "$STAGING_RECEIPT_KEY" --release-key "$RELEASE_KEY" \
          --recovery-step standalone --git-sha "$HEAD_SHA" \
          --correlation "$CARR_CORRELATION_ID" "$@"
      else
        "$PY" "$REPO/tools/ops-record.py" staging-attempt "$attempt_action" \
          --idempotency-key "$STAGING_RECEIPT_KEY" --release-key "$RELEASE_KEY" \
          --prior-release-key "$RECOVERY_PRIOR_RELEASE_KEY" \
          --recovery-attempt-id "$RECOVERY_ATTEMPT_ID" \
          --recovery-step "$RECOVERY_STEP" --git-sha "$HEAD_SHA" \
          --correlation "$CARR_CORRELATION_ID" "$@"
      fi
    }
    DEPLOY_TAG="$(staging_attempt prepare --field expected_provider_tag)" \
      || fail "the exact staging deployment attempt was not durably prepared."
    [ -n "$DEPLOY_TAG" ] || fail "the prepared staging attempt returned no provider tag."
    ATTEMPT_CLAIM_FIELD="deploy_claimed"
    [ "$RECOVERY_STEP" != "restore_only" ] || ATTEMPT_CLAIM_FIELD="mutation_claimed"
    ATTEMPT_DEPLOY_CLAIMED="$(staging_attempt prepare --field "$ATTEMPT_CLAIM_FIELD")" \
      || fail "the prepared staging attempt claim state could not be read."
    if [ "$RECOVERY_STEP" = "restore_only" ]; then
      # The controller may record an `unknown` repair result only after this
      # append-only attempt exists.  A preflight refusal has no repair row and
      # must remain only the failed source-step evidence.
      echo "  restore-only attempt prepared"
    fi

    verify_staging_receipt_file() {
      receipt_file="$1"
      live_provider_version="$("$PY" "$REPO/tools/ops-record.py" \
        staging-readback-verify --file "$receipt_file" --git-sha "$HEAD_SHA" \
        --provider-tag "$DEPLOY_TAG" --expected-program6-actions "$EXPECTED_PROGRAM6_ACTIONS" \
        --field provider_version_id)" || return 1
      provider_versions="$(mktemp "${TMPDIR:-/tmp}/carr-staging-versions.XXXXXX")" \
        || return 1
      chmod 600 "$provider_versions"
      if ! "$WRANGLER" versions list --env "$TARGET_ENV" --json > "$provider_versions" 2>/dev/null; then
        rm -f "$provider_versions"
        return 1
      fi
      if ! "$PY" "$REPO/tools/ops-record.py" staging-provider-version \
          --file "$provider_versions" --provider-tag "$DEPLOY_TAG" \
          --live-version-id "$live_provider_version" >/dev/null; then
        rm -f "$provider_versions"
        return 1
      fi
      rm -f "$provider_versions"
    }

    record_staging_receipt_file() {
      receipt_file="$1"
      verify_staging_receipt_file "$receipt_file" || return 1
      if [ "$RECOVERY_STEP" = "restore_only" ]; then
        "$PY" "$REPO/tools/ops-record.py" staging-restore-only result \
          --idempotency-key "$STAGING_RECEIPT_KEY" --status succeeded \
          --git-sha "$HEAD_SHA" --expected-provider-tag "$DEPLOY_TAG" \
          --expected-program6-actions "$EXPECTED_PROGRAM6_ACTIONS" \
          --staging-readback-file "$receipt_file"
      elif [ "$RECOVERY_STEP" = "standalone" ]; then
        "$PY" "$REPO/tools/ops-record.py" deployment --service carr-mcp \
          --environment staging --state complete --git-sha "$HEAD_SHA" \
          --correlation "$CARR_CORRELATION_ID" --source-kind wrapper \
          --source-ref bin/deploy-worker.sh --read-back-at now \
          --release-key "$RELEASE_KEY" --idempotency-key "$STAGING_RECEIPT_KEY" \
          --expected-provider-tag "$DEPLOY_TAG" --recovery-step standalone \
          --expected-program6-actions "$EXPECTED_PROGRAM6_ACTIONS" \
          --staging-readback-file "$receipt_file"
      else
        "$PY" "$REPO/tools/ops-record.py" deployment --service carr-mcp \
          --environment staging --state complete --git-sha "$HEAD_SHA" \
          --correlation "$CARR_CORRELATION_ID" --source-kind wrapper \
          --source-ref bin/deploy-worker.sh --read-back-at now \
          --release-key "$RELEASE_KEY" --idempotency-key "$STAGING_RECEIPT_KEY" \
          --expected-provider-tag "$DEPLOY_TAG" --recovery-step "$RECOVERY_STEP" \
          --expected-program6-actions "$EXPECTED_PROGRAM6_ACTIONS" \
          --recovery-attempt-id "$RECOVERY_ATTEMPT_ID" \
          --prior-release-key "$RECOVERY_PRIOR_RELEASE_KEY" \
          --staging-readback-file "$receipt_file"
      fi
    }

    # Resume before provider mutation. If a prior process reached Cloudflare but
    # lost its client/DB response, the exact serving tag+UUID is recorded/replayed
    # and Wrangler is never called again. A claimed attempt whose tag is not
    # serving is deliberately stuck for operator recovery; automatic redeploy
    # would turn an ambiguous crash into a second provider mutation.
    STAGING_DEPLOY_RECOVERED=0
    RECOVERY_READBACK="$(mktemp "${TMPDIR:-/tmp}/carr-staging-resume.XXXXXX")"
    chmod 600 "$RECOVERY_READBACK"
    if curl --fail --silent --show-error --max-time 30 --max-filesize 65536 \
         "https://$STAGING_TARGET_HOST/release" > "$RECOVERY_READBACK" 2>/dev/null; then
      if [ "$ATTEMPT_DEPLOY_CLAIMED" = "true" ] && \
         record_staging_receipt_file "$RECOVERY_READBACK" >/dev/null 2>&1; then
        STAGING_DEPLOY_RECOVERED=1
      elif [ "$ATTEMPT_DEPLOY_CLAIMED" = "false" ] && \
           verify_staging_receipt_file "$RECOVERY_READBACK" >/dev/null 2>&1; then
        rm -f "$RECOVERY_READBACK"
        fail "the unclaimed deterministic provider tag already exists; refusing tag recreation"
      fi
    fi
    rm -f "$RECOVERY_READBACK"
    if [ "$STAGING_DEPLOY_RECOVERED" = 1 ]; then
      echo "  recovered exact serving staging tag; provider deploy skipped"
    else
      DEPLOY_CLAIM_FIELD="deploy_allowed"
      [ "$RECOVERY_STEP" != "restore_only" ] || DEPLOY_CLAIM_FIELD="mutation_allowed"
      DEPLOY_ALLOWED="$(staging_attempt claim --field "$DEPLOY_CLAIM_FIELD")" \
        || fail "the prepared staging attempt could not be claimed."
      [ "$DEPLOY_ALLOWED" = "true" ] \
        || fail "deployment already claimed but its exact tag is not serving; refusing redeploy"
      "$WRANGLER" deploy --env "$TARGET_ENV" --var "GIT_SHA:$HEAD_SHA" --tag "$DEPLOY_TAG"
    fi
  else
    "$WRANGLER" deploy --env "$TARGET_ENV" --var "GIT_SHA:$HEAD_SHA"
  fi
fi

# Production promotion is not verified by Wrangler accepting the command. Read
# the serving Worker identity back from its fixed Production endpoint and refuse
# to call any later golden success Complete unless every immutable field agrees.
if [ "$TARGET_ENV" = "production" ]; then
  CARR_CORRELATION_ID="${CARR_CORRELATION_ID:-$(uuidgen | tr 'A-Z' 'a-z')}"
  export CARR_CORRELATION_ID
  LIVE_RELEASE_URL="https://api.doctorcre.com/release"
  echo ""
  echo "== postflight: Production release identity =="
  # THE READ-BACK RETRIES, and that is the whole point of this block. It used to
  # read /release exactly once, the instant Wrangler returned, and turn anything
  # it saw into a permanent verdict. A Cloudflare promotion is not globally
  # consistent the moment the upload command exits, so a single immediate read
  # can legitimately observe the PREVIOUS identity — and the old code wrote that
  # observation into the production ledger as state=failed,
  # failure_class=production_readback_mismatch, then exited 1.
  #
  # That is what happened to release.eed.prod.v3 on 2026-08-19 at 23:36:47Z. Joe
  # had approved it eleven seconds earlier. The deploy recorded a hard failure
  # while the Worker it had just promoted was serving correctly: read back later
  # the same night, /release returned the exact identity that deploy expected,
  # git_sha 7c7e1bd12946 and worker version 8f622345-c4df-4469-9676-dc4425c0cf45,
  # with 140 verbs. Nothing was wrong with the deploy. The verifier looked too
  # early, once, and its answer was permanent.
  #
  # The cost was not the wrong row alone. ops/last-deployed-verb-count.py takes
  # its baseline from the newest COMPLETE production row, so a false failure
  # freezes that baseline: it sat at the 130 verbs of 2026-08-16 while production
  # served 140, and a later deploy shipping 131 would have passed the guard while
  # dropping nine live verbs.
  #
  # A STABLE mismatch still fails, which is the property worth keeping. Retrying
  # costs a minute and cannot turn a genuinely wrong identity into a right one —
  # if the Worker is serving another commit, every attempt sees that commit and
  # the failed row is recorded exactly as before. This only removes the race.
  LIVE_READBACK_ATTEMPTS="${CARR_READBACK_ATTEMPTS:-12}"
  LIVE_READBACK_SLEEP="${CARR_READBACK_SLEEP:-5}"
  LIVE_RELEASE_OK=0
  LIVE_RELEASE_ATTEMPT=0
  while [ "$LIVE_RELEASE_ATTEMPT" -lt "$LIVE_READBACK_ATTEMPTS" ]; do
    LIVE_RELEASE_ATTEMPT=$((LIVE_RELEASE_ATTEMPT + 1))
    set +e
    LIVE_RELEASE_JSON="$(curl --fail --silent --show-error --max-time 30 \
      "$LIVE_RELEASE_URL" 2>&1)"
    LIVE_RELEASE_RC=$?
    set -e
    if [ "$LIVE_RELEASE_RC" -eq 0 ]; then
      set +e
      printf '%s' "$LIVE_RELEASE_JSON" | \
        "$PY" "$REPO/ops/verify-worker-release.py" \
          --environment production --sha "$HEAD_SHA" --provider "$PROVIDER" \
          --provider-version-id "$PROVIDER_VERSION_ID" \
          --expected-program6-actions "$EXPECTED_PROGRAM6_ACTIONS" >/dev/null 2>&1
      LIVE_VERIFY_RC=$?
      set -e
      if [ "$LIVE_VERIFY_RC" -eq 0 ]; then
        LIVE_RELEASE_OK=1
        break
      fi
    fi
    if [ "$LIVE_RELEASE_ATTEMPT" -lt "$LIVE_READBACK_ATTEMPTS" ]; then
      echo "  attempt $LIVE_RELEASE_ATTEMPT of $LIVE_READBACK_ATTEMPTS did not yet" \
           "observe the approved identity; waiting ${LIVE_READBACK_SLEEP}s"
      sleep "$LIVE_READBACK_SLEEP"
    fi
  done
  if [ "$LIVE_RELEASE_OK" -eq 1 ] && [ "$LIVE_RELEASE_ATTEMPT" -gt 1 ]; then
    echo "  read-back settled on attempt $LIVE_RELEASE_ATTEMPT of $LIVE_READBACK_ATTEMPTS"
  fi
  if [ "$LIVE_RELEASE_OK" -ne 1 ] && [ "$LIVE_RELEASE_RC" -ne 0 ]; then
    echo "  Production /release could not be read after $LIVE_RELEASE_ATTEMPT attempt(s)" \
         "(curl exit $LIVE_RELEASE_RC)." >&2
    DEPLOYMENT_EVIDENCE_REF="$LIVE_RELEASE_URL#unavailable"
    DEPLOYMENT_FAILURE_CLASS="production_readback_unavailable"
    record_deployment verifying "$CARR_CORRELATION_ID"
    exit 1
  fi
  if [ "$LIVE_RELEASE_OK" -ne 1 ]; then
    # Re-run the verifier once WITHOUT suppressing it, so the operator sees which
    # fields disagreed rather than only that something did. The loop above
    # silenced it because a first attempt that has not settled yet is expected
    # noise, not a finding.
    printf '%s' "$LIVE_RELEASE_JSON" | \
      "$PY" "$REPO/ops/verify-worker-release.py" \
        --environment production --sha "$HEAD_SHA" --provider "$PROVIDER" \
        --provider-version-id "$PROVIDER_VERSION_ID" \
        --expected-program6-actions "$EXPECTED_PROGRAM6_ACTIONS" || true
    echo "  Production /release is malformed or does not match the approved identity," \
         "still, after $LIVE_RELEASE_ATTEMPT attempt(s) over" \
         "$((LIVE_RELEASE_ATTEMPT * LIVE_READBACK_SLEEP))s. This is a settled" \
         "disagreement, not a propagation race." >&2
    DEPLOYMENT_EVIDENCE_REF="$LIVE_RELEASE_URL#identity-mismatch"
    DEPLOYMENT_FAILURE_CLASS="production_readback_mismatch"
    record_deployment failed "$CARR_CORRELATION_ID"
    exit 1
  fi
  LIVE_RELEASE_VERIFIED=1
  echo "  OK  serving Production identity matches $HEAD_SHA / $PROVIDER_VERSION_ID"

  # THE PROMOTED BUILD'S VERB COUNT, taken from the identity read-back that just
  # passed (defect 4077b653). Promote mode skips every source preflight — that is
  # correct, there is no source tree to trust for an immutable provider version —
  # but preflight 3 was the ONLY place SHIPPING was ever set, so every Production
  # promotion recorded verb_count NULL. Since Production source deploys are
  # disabled outright, promotion is the only way Production ships, and the
  # verb-loss baseline could therefore never advance again: it sat at 143 from
  # 2026-08-20 while Production served 146, and a promotion shipping 144 would
  # have passed the guard while dropping two live verbs. That is loop #276 with
  # the guard watching a frozen number.
  #
  # THE LIVE ENDPOINT IS THE RIGHT SOURCE HERE, not a checkout: it is what is
  # actually serving, and this exact JSON has just been verified to carry the
  # expected SHA, provider version, environment and Program 6 posture. Reading a
  # tree would answer a different question.
  #
  # A count that cannot be read does NOT fail the deploy. Production is already
  # serving by this point; refusing would leave a live deployment unrecorded,
  # which is worse than a missing number. It says so out loud instead, naming the
  # consequence rather than passing silently.
  if [ "$VERSION_MODE" = "promote" ]; then
    SHIPPING="$(printf '%s' "$LIVE_RELEASE_JSON" | "$PY" -c '
import json, sys
try:
    n = json.load(sys.stdin).get("verb_count")
except Exception:
    sys.exit(1)
if isinstance(n, bool) or not isinstance(n, int) or n <= 0:
    sys.exit(1)
print(n)
' 2>/dev/null || true)"
    case "$SHIPPING" in
      ''|*[!0-9]*)
        SHIPPING=""
        echo "  !!  the promoted build's verb count could not be read from the" >&2
        echo "      identity read-back, so this deploy records none. The NEXT" >&2
        echo "      deploy's verb-loss guard will measure against an older row" >&2
        echo "      (defect 4077b653)." >&2 ;;
      *)
        echo "  OK  promoted build serves $SHIPPING verbs — recorded as the next deploy's baseline" ;;
    esac
  fi
  # Persist the exact live identity before any later check can claim it measured
  # this promotion. The performance receipt uses this same correlation, and the
  # database refuses a pre-seeded result with no prior read-back journey.
  DEPLOYMENT_EVIDENCE_REF="$LIVE_RELEASE_URL#identity-ok"
  if ! record_deployment verifying "$CARR_CORRELATION_ID" identity-readback; then
    echo "  Production is serving, but its durable identity receipt is missing." >&2
    exit 1
  fi
fi

# ---------- postflight ----------
# THE MARKER WRITE IS PART OF THE SAME STEP THAT DEPLOYS, deliberately: there
# is no separate "record the deploy" invocation to forget or skip. This is
# necessary but not sufficient (see the DEPLOY PROVENANCE note above) — a
# `wrangler deploy` run outside this script writes nothing here, which is
# exactly why /release, not this file, is the signal a verification pass
# should trust.
echo ""
echo "== postflight =="
# NO BASELINE FILE IS WRITTEN, and nothing is left for a human to commit. The
# baseline is the --verb-count this run records into ops.deployment below, which
# the next deploy reads back through ops/last-deployed-verb-count.py. The file
# this used to write could not be committed at all: it sits inside the digested
# artifact while the digest skips dotfiles, so committing it failed
# ops/release-manifest-selftest.py (defect d737c09c).
if [ -n "$SHIPPING" ]; then
  echo "  shipping $SHIPPING verbs — the baseline for the next deploy is the"
  echo "  ledger row recorded below, not a file"
else
  echo "  promoted immutable $PROVIDER version $PROVIDER_VERSION_ID"
fi
echo "  stamped GIT_SHA=$HEAD_SHA into this deploy (see /release)"
echo ""
echo "  Verify live before you walk away: call list-verbs from a session and"
echo "  confirm it reports $SHIPPING. A deploy that returns success and a"
echo "  registry that answers are two different claims (rule c53beeaa)."
# VERIFY THE ENVIRONMENT YOU JUST SHIPPED, NOT THE ONE YOU DIDN'T. This line
# hardcoded the production URL for every target, so a `--env staging` deploy
# ended by instructing the operator to curl api.doctorcre.com and look for the
# staging commit there. Following that literally means either a false alarm (the
# sha does not match, because production is correctly still production) or, far
# worse, a false all-clear read off the wrong Worker entirely. Pointing a staging
# verification at a production hostname is the exact confusion the 2026-08-13
# routes incident was made of, and it survived in the script that ships the fix.
#
# /release now carries an `env` field, so the check is two-sided: the sha proves
# WHICH BUILD answered and env proves WHICH DEPLOYMENT did. Neither alone is
# enough — during the incident every other field was plausible or identical.
# wrangler prints the deployed trigger URL above and its output is not captured
# here, so the non-production branch NAMES that line rather than reconstructing a
# hostname it would have to keep in sync with wrangler.toml. Being vague about
# which URL is better than being precise about the wrong one.
if [ "$TARGET_ENV" = "production" ]; then
  VERIFY_URL="https://api.doctorcre.com/release"
else
  VERIFY_URL="<the workers.dev URL printed above>/release — NOT api.doctorcre.com, that is production"
fi
echo "  Then curl $VERIFY_URL and confirm BOTH:"
echo "    git_sha.value is $HEAD_SHA   (which build answered)"
echo "    env.value is \"$TARGET_ENV\"                        (which deployment answered)"
if [ "$TARGET_ENV" = "production" ]; then
  echo "    worker_version.id is $PROVIDER_VERSION_ID  (which immutable provider version answered)"
fi
echo "  That pair is the authoritative provenance check, not this file. env matters"
echo "  because git_sha and schema are IDENTICAL across environments by design."

# ── POSTFLIGHT GOLDEN-WORKFLOW RUN, added 2026-08-14 (Phase 1 Program 3) ──────
#
# Everything above this line is an INSTRUCTION TO A HUMAN, and instructions to
# humans are the thing Program 3 exists to stop relying on. smoke-reads.sh has
# carried "RUN THIS AFTER EVERY WORKER DEPLOY" in its header since it was
# written; the Program 0 inventory then found bin/smoke-and-record.sh among the
# scripts with no caller anywhere. A post-deploy check that depends on somebody
# remembering is a check whose coverage is unknown.
#
# PRODUCTION ONLY, AND THAT IS NOT LAZINESS. smoke-reads.sh defaults to the
# production API and a staging deploy prints its workers.dev trigger URL from
# wrangler rather than from anything this script holds — so pointing the suite at
# a staging deploy would mean reconstructing a hostname this file would have to
# keep in sync with wrangler.toml. Aiming post-deploy verification at the wrong
# hostname is precisely what the 2026-08-13 routes incident was made of, and the
# comment block above already refuses to do it for the human. It refuses here too.
# For a staging deploy the printed instruction stands.
#
# The deploy and the check share ONE correlation id, which is the entire point:
# a deploy that breaks a read verb now leaves a deployment and a failed check
# under one id instead of two unrelated facts in two places.
if [ "$TARGET_ENV" = "production" ] && [ ! -x "$REPO/bin/smoke-and-record.sh" ]; then
  [ "${LIVE_RELEASE_VERIFIED:-0}" = "1" ] || {
    DEPLOYMENT_EVIDENCE_REF="https://api.doctorcre.com/release#not-verified"
    DEPLOYMENT_FAILURE_CLASS="production_readback_unavailable"
    record_deployment verifying "$CARR_CORRELATION_ID"
    exit 1
  }
  echo "  golden workflow suite is unavailable after a verified identity read-back." >&2
  DEPLOYMENT_EVIDENCE_REF="$LIVE_RELEASE_URL#identity-ok;golden-workflow-unavailable"
  record_deployment verifying "$CARR_CORRELATION_ID"
  exit 1
fi

if [ "$TARGET_ENV" = "production" ] && [ -x "$REPO/bin/smoke-and-record.sh" ]; then
  [ "${LIVE_RELEASE_VERIFIED:-0}" = "1" ] || {
    DEPLOYMENT_EVIDENCE_REF="https://api.doctorcre.com/release#not-verified"
    DEPLOYMENT_FAILURE_CLASS="production_readback_unavailable"
    record_deployment verifying "$CARR_CORRELATION_ID"
    exit 1
  }
  echo ""
  echo "== postflight: golden workflow suite =="
  CARR_CORRELATION_ID="${CARR_CORRELATION_ID:-$(uuidgen | tr 'A-Z' 'a-z')}"
  export CARR_CORRELATION_ID
  CARR_ENV="$TARGET_ENV"
  export CARR_ENV
  echo "  correlation $CARR_CORRELATION_ID"
  DEPLOYMENT_EVIDENCE_REF="$LIVE_RELEASE_URL#identity-ok;bin/smoke-and-record.sh#$CARR_CORRELATION_ID"
  DEPLOYMENT_FAILURE_CLASS="golden_workflow_failed"
  SUITE_STARTED_MS="$("$PY" -c 'import time; print(time.monotonic_ns() // 1_000_000)')"
  if "$REPO/bin/smoke-and-record.sh"; then
    echo "  golden workflow suite PASSED against the deploy you just shipped."
    SUITE_ELAPSED_MS=$(( $("$PY" -c 'import time; print(time.monotonic_ns() // 1_000_000)') - SUITE_STARTED_MS ))

    # THE BUDGET MEASURES A REQUEST, NOT THE SUITE. This used to clock
    # bin/smoke-and-record.sh end to end — 33 workflow tests against the live
    # Worker over the network — and compare that to PERFORMANCE_BUDGET_MS, whose
    # approved value on every production release is 1000. A remote suite cannot
    # finish inside one second, so the gate failed EVERY promotion after
    # everything real had passed: measured 2026-08-20 promoting claim-card,
    # "33 passed, 0 failed" immediately followed by "FAIL 229615ms exceeds
    # approved 1000ms", and the deploy recorded failed on that alone.
    #
    # That is not a cosmetic complaint. ops.deployment is what the verb-loss
    # guard reads for its baseline, so every good promotion booked as failed
    # left the newest success further behind reality — production was serving
    # 141 verbs while the ledger's newest success still said 130, wide enough
    # for a deploy shipping 131 to pass the guard and drop ten live verbs.
    #
    # So measure what the budget is actually about: how long production takes to
    # answer. Slowest of five samples, not the mean — a budget met on average
    # and blown one call in five is not met. Measured on the same endpoint the
    # identity read-back already uses, so this adds no new dependency. Real
    # readings that morning: 101, 156, 187, 446, 589 ms.
    PERFORMANCE_ELAPSED_MS="$(
      for _ in 1 2 3 4 5; do
        curl -s -o /dev/null -w '%{time_total}\n' --max-time 30 "$LIVE_RELEASE_URL" || echo 999
      done | "$PY" -c 'import sys; print(max(int(float(x) * 1000) for x in sys.stdin.read().split()))'
    )"
    echo "  slowest of 5 live requests: ${PERFORMANCE_ELAPSED_MS}ms (suite took ${SUITE_ELAPSED_MS}ms, not gated)"
    PERFORMANCE_EVIDENCE_REF="$LIVE_RELEASE_URL#performance-$CARR_CORRELATION_ID"
    set +e
    "$PY" "$REPO/ops/performance-budget-gate.py" \
        --elapsed-ms "$PERFORMANCE_ELAPSED_MS" --budget-ms "$PERFORMANCE_BUDGET_MS" \
        --budget-ref "$PERFORMANCE_BUDGET_REF" --evidence-ref "$PERFORMANCE_EVIDENCE_REF"
    PERFORMANCE_GATE_RC=$?
    set -e
    if [ "$PERFORMANCE_GATE_RC" -eq 1 ]; then
      # A measured breach is a real failed release-bound check, not a made-up
      # recovery receipt. Record it before the deployment failure so the
      # immediate-incident path receives both linked facts.
      if ! "$PY" "$REPO/tools/ops-record.py" run --kind check --service carr-mcp \
          --key performance.release --state failed --environment production \
          --release-key "$RELEASE_KEY" --budget-ms "$PERFORMANCE_BUDGET_MS" \
          --duration-ms "$PERFORMANCE_ELAPSED_MS" --correlation "$CARR_CORRELATION_ID" \
          --source-kind wrapper --source-ref bin/deploy-worker.sh \
          --evidence-ref "$PERFORMANCE_EVIDENCE_REF" \
          --failure-class performance_budget_exceeded \
          --detail "slowest of 5 live requests ${PERFORMANCE_ELAPSED_MS}ms exceeds ${PERFORMANCE_BUDGET_REF}; golden suite ${SUITE_ELAPSED_MS}ms, not gated"; then
        DEPLOYMENT_EVIDENCE_REF="$PERFORMANCE_EVIDENCE_REF"
        DEPLOYMENT_FAILURE_CLASS="performance_evidence_unrecorded"
        record_deployment verifying "$CARR_CORRELATION_ID"
        exit 1
      fi
      DEPLOYMENT_EVIDENCE_REF="$PERFORMANCE_EVIDENCE_REF"
      DEPLOYMENT_FAILURE_CLASS="performance_budget_exceeded"
      record_deployment failed "$CARR_CORRELATION_ID"
      exit 1
    fi
    if [ "$PERFORMANCE_GATE_RC" -ne 0 ]; then
      DEPLOYMENT_EVIDENCE_REF="$PERFORMANCE_EVIDENCE_REF"
      DEPLOYMENT_FAILURE_CLASS="performance_gate_unavailable"
      record_deployment verifying "$CARR_CORRELATION_ID"
      exit 1
    fi
    if ! "$PY" "$REPO/tools/ops-record.py" run --kind check --service carr-mcp \
        --key performance.release --state succeeded --environment production \
        --release-key "$RELEASE_KEY" --budget-ms "$PERFORMANCE_BUDGET_MS" \
        --duration-ms "$PERFORMANCE_ELAPSED_MS" --correlation "$CARR_CORRELATION_ID" \
        --source-kind wrapper --source-ref bin/deploy-worker.sh \
        --evidence-ref "$PERFORMANCE_EVIDENCE_REF" \
        --detail "slowest of 5 live requests ${PERFORMANCE_ELAPSED_MS}ms within ${PERFORMANCE_BUDGET_REF}; golden suite ${SUITE_ELAPSED_MS}ms, not gated"; then
      DEPLOYMENT_EVIDENCE_REF="$PERFORMANCE_EVIDENCE_REF"
      DEPLOYMENT_FAILURE_CLASS="performance_evidence_unrecorded"
      record_deployment verifying "$CARR_CORRELATION_ID"
      exit 1
    fi
    DEPLOYMENT_EVIDENCE_REF="$PERFORMANCE_EVIDENCE_REF;golden-workflow-ok"
    if ! record_deployment complete "$CARR_CORRELATION_ID"; then
      echo "  Production shipped, but Program 5 assurance did not complete." >&2
      exit 1
    fi
  else
    smoke_rc=$?
    if [ "$smoke_rc" -eq 78 ]; then
      echo "  golden workflow suite SKIPPED — no probe token configured on this machine."
      echo "  This deploy is UNVERIFIED by the suite. See the provisioning runbook in"
      echo "  mcp-server/smoke-reads.sh. Not treated as a deploy failure."
      # VERIFYING, NOT COMPLETE. The completion bar in 0115 is a read-back, and a
      # skipped suite produced none. Recording this as complete would be the
      # single most expensive lie this script could tell.
      record_deployment verifying "$CARR_CORRELATION_ID"
      exit 1
    else
      echo ""
      echo "  ***  GOLDEN WORKFLOW SUITE FAILED (exit $smoke_rc) AFTER THIS DEPLOY.  ***"
      echo "  The Worker shipped and is answering wrongly, or the probe credential is"
      echo "  refused. Read the output above, and read the failed check together with"
      echo "  this deploy as ONE journey rather than two unrelated facts:"
      echo "      .venv/bin/python tools/ops-record.py trace $CARR_CORRELATION_ID"
      echo "  Roll back by approving the prior immutable version, then running"
      echo "  bin/deploy-worker.sh --promote-version <approved-prior-version-id>."
      record_deployment failed "$CARR_CORRELATION_ID"
      exit 1
    fi
  fi
fi

# ── THE LEDGER IS NOT THE SMOKE SUITE ────────────────────────────────────────
# Defect cb65fc17, 2026-08-16: the first approved release in this system's
# history shipped to staging, read back clean by hand, and wrote NO row to
# ops.deployment. All three record_deployment call sites sit inside the block
# above, so a staging deploy reached none of them.
#
# THE PRODUCTION-ONLY GUARD ABOVE IS CORRECT AND STAYS. smoke-reads.sh defaults
# to the production API, and aiming post-deploy verification at a reconstructed
# staging hostname is what the 2026-08-13 routes incident was made of.
#
# WHAT WAS WRONG IS THE NESTING. Running the golden suite and recording THAT A
# DEPLOY HAPPENED are different facts. The second is the Program 3 job ledger,
# which Program 5's promotion path reads, and a staging deploy leaving no row is
# indistinguishable from a staging deploy that never ran — which is exactly how
# this went unnoticed until someone went looking for something else.
#
# `verifying`, NEVER `complete`, and the word is already defined above for this
# case: the suite could not run, so nothing was proven. Migration 0115 refuses
# `complete` without a read-back and would reject the lie anyway. Automating the
# staging read-back so this could honestly say `complete` is Program 5 work
# (production read-back is one of its bullets) and is deliberately not smuggled
# in here.
if [ "$TARGET_ENV" = "staging" ]; then
  echo ""
  echo "== postflight: deployment ledger =="
  CARR_CORRELATION_ID="${CARR_CORRELATION_ID:-$(uuidgen | tr 'A-Z' 'a-z')}"
  export CARR_CORRELATION_ID
  echo "  correlation $CARR_CORRELATION_ID"
  # The host, account and Worker name are resolved together from checked-in
  # semantic config. The response is capped and only typed scalar fields cross
  # into the database-owned receipt writer.
  STAGING_HOST="${STAGING_TARGET_HOST:-$("$PY" "$REPO/tools/ops-record.py" staging-target --field host)}" \
    || fail "checked-in staging target config is not exact."
  STAGING_RECEIPT="$(mktemp "${TMPDIR:-/tmp}/carr-staging-release.XXXXXX")"
  chmod 600 "$STAGING_RECEIPT"
  trap 'rm -f "$STAGING_RECEIPT"' EXIT INT TERM
  STAGING_OK=0
  record_staging_receipt() {
    record_staging_receipt_file "$STAGING_RECEIPT" >/dev/null
  }
  for _ in 1 2 3; do
    if [ -n "$STAGING_HOST" ] && curl --fail --silent --show-error --max-time 30 --max-filesize 65536 \
         "https://$STAGING_HOST/release" > "$STAGING_RECEIPT" 2>/dev/null && \
       record_staging_receipt; then
      STAGING_OK=1; break
    fi
    sleep 5
  done
  rm -f "$STAGING_RECEIPT"
  trap - EXIT INT TERM
  if [ "$STAGING_OK" = 1 ]; then
    echo "  recorded immutable staging /release receipt for $CARR_CORRELATION_ID"
  else
    echo "  staging /release identity was not durably verified; recording VERIFYING only." >&2
    record_deployment verifying "$CARR_CORRELATION_ID"
    exit 1
  fi
elif [ "$TARGET_ENV" != "production" ]; then
  CARR_CORRELATION_ID="${CARR_CORRELATION_ID:-$(uuidgen | tr 'A-Z' 'a-z')}"
  export CARR_CORRELATION_ID
  record_deployment verifying "$CARR_CORRELATION_ID"
fi
