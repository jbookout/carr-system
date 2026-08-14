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
#   the count recorded by the last successful deploy, and refuse on a DROP. A
#   deliberate retirement is still allowed, it just has to say so out loud with
#   --allow-shrink, which is the whole point: shrinking becomes a decision
#   instead of an accident.
#
# The count is EXACT, not parsed: it imports the module and reads
# Object.keys(TOOLS).length. A regex over the source undercounts (61 vs the real
# 66 on the branch that caused this), and a guard that reports the wrong number
# is worse than no guard.
#
# Usage:
#   bin/deploy-worker.sh              # preflight, deploy, postflight
#   bin/deploy-worker.sh --check      # preflight only, ship nothing
#   bin/deploy-worker.sh --allow-shrink   # deliberate verb retirement
#   bin/deploy-worker.sh --release-sha <full-40-char-sha>
#       # an approved immutable release when main moves after approval
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
WORKER_DIR="$REPO/mcp-server"
COUNT_FILE="$WORKER_DIR/.last-deployed-verb-count"
# Set again after argument parsing when a non-production env is chosen, so a
# staging deploy can never overwrite the baseline production is measured against.
WRANGLER="$WORKER_DIR/node_modules/.bin/wrangler"

CHECK_ONLY=0
ALLOW_SHRINK=0
TARGET_ENV="production"
PINNED_RELEASE=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --check)         CHECK_ONLY=1 ;;
    --env)
      [ "$#" -ge 2 ] || { echo "deploy-worker: --env needs a name" >&2; exit 64; }
      TARGET_ENV="$2"
      shift
      ;;
    --allow-shrink)  ALLOW_SHRINK=1 ;;
    --release-sha)
      [ "$#" -ge 2 ] || { echo "deploy-worker: --release-sha needs a full commit SHA" >&2; exit 64; }
      PINNED_RELEASE="$2"
      shift
      ;;
    *) echo "deploy-worker: unknown argument '$1'" >&2; exit 64 ;;
  esac
  shift
done

if [ "$TARGET_ENV" != "production" ]; then
  COUNT_FILE="$WORKER_DIR/.last-deployed-verb-count.$TARGET_ENV"
fi

fail() { echo ""; echo "REFUSED: $1" >&2; echo "" >&2; exit 1; }

cd "$REPO"

# ---------- preflight 1: exactly origin/main ----------
echo "== preflight =="
git fetch origin main --quiet 2>/dev/null || fail "could not reach origin to verify main."

HEAD_SHA="$(git rev-parse HEAD)"
MAIN_SHA="$(git rev-parse origin/main)"
BRANCH="$(git rev-parse --abbrev-ref HEAD)"

if [ -n "$PINNED_RELEASE" ]; then
  [ "${#PINNED_RELEASE}" -eq 40 ] \
    || fail "--release-sha must be the full immutable 40-character commit SHA."
  PINNED_SHA="$(git rev-parse --verify "${PINNED_RELEASE}^{commit}" 2>/dev/null)" \
    || fail "approved release SHA does not resolve to a commit."
  [ "$PINNED_RELEASE" = "$PINNED_SHA" ] \
    || fail "--release-sha must be the exact canonical full SHA, not an abbreviation or tag."
  [ "$HEAD_SHA" = "$PINNED_SHA" ] \
    || fail "checkout HEAD does not equal the approved release SHA."
  git merge-base --is-ancestor "$PINNED_SHA" origin/main \
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
  BEHIND="$(git rev-list --count "HEAD..origin/main")"
  AHEAD="$(git rev-list --count "origin/main..HEAD")"
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
DIRTY_LIST="$(git status --porcelain -- mcp-server/ dealroom/ \
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
[ -x "$WRANGLER" ] || fail "wrangler not found at $WRANGLER (run npm install in mcp-server/)."

# The count moved to ops/verb-count.sh on 2026-08-13 so that CI can run this same
# guard at MERGE time, not only here at DEPLOY time. Same code, two callers
# (rule a8c55a47) — deliberately NOT a second copy that can drift from this one.
SHIPPING="$(sh "$REPO/ops/verb-count.sh" "$WORKER_DIR" 2>/dev/null || true)"

case "$SHIPPING" in
  ''|*[!0-9]*) fail "could not count verbs in $WORKER_DIR/src/tools.js — the import failed.
  Refusing rather than shipping an unmeasured registry." ;;
esac
echo "  OK  registry imports cleanly: $SHIPPING verbs about to ship"

if [ -f "$COUNT_FILE" ]; then
  PREVIOUS="$(tr -dc '0-9' < "$COUNT_FILE")"
  if [ -n "$PREVIOUS" ] && [ "$SHIPPING" -lt "$PREVIOUS" ]; then
    LOST=$((PREVIOUS - SHIPPING))
    if [ "$ALLOW_SHRINK" = "0" ]; then
      fail "this deploy would REMOVE $LOST verb(s) from production.
  last deployed: $PREVIOUS
  about to ship: $SHIPPING

  If verbs were deliberately retired, say so: re-run with --allow-shrink.
  If not, you are about to reproduce loop #276."
    fi
    echo "  !!  shrinking by $LOST verb(s), allowed explicitly via --allow-shrink"
  else
    echo "  OK  no verb loss (last deployed: $PREVIOUS)"
  fi
else
  echo "  --  no previous count recorded; this run establishes the baseline"
fi

if [ "$CHECK_ONLY" = "1" ]; then
  echo ""
  echo "check only — nothing deployed."
  exit 0
fi

# ---------- deploy ----------
echo ""
echo "== deploy =="
cd "$WORKER_DIR"
# RELEASE_SHA: whichever of the two preflight branches ran above, HEAD_SHA is
# already proven equal to it (the pinned branch fails otherwise), so it is
# always the right value to stamp — no separate variable to keep in sync.
if [ "$TARGET_ENV" = "production" ]; then
  "$WRANGLER" deploy --var "GIT_SHA:$HEAD_SHA"
else
  "$WRANGLER" deploy --env "$TARGET_ENV" --var "GIT_SHA:$HEAD_SHA"
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
printf '%s\n' "$SHIPPING" > "$COUNT_FILE"
echo "  recorded $SHIPPING verbs in $(basename "$COUNT_FILE")"
echo "  stamped GIT_SHA=$HEAD_SHA into this deploy (see /release)"
echo "  COMMIT THAT FILE — it is the baseline the next deploy is measured against."
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
if [ "$TARGET_ENV" = "production" ] && [ -x "$REPO/bin/smoke-and-record.sh" ]; then
  echo ""
  echo "== postflight: golden workflow suite =="
  CARR_CORRELATION_ID="${CARR_CORRELATION_ID:-$(uuidgen | tr 'A-Z' 'a-z')}"
  export CARR_CORRELATION_ID
  CARR_ENV="$TARGET_ENV"
  export CARR_ENV
  echo "  correlation $CARR_CORRELATION_ID"
  if "$REPO/bin/smoke-and-record.sh"; then
    echo "  golden workflow suite PASSED against the deploy you just shipped."
  else
    smoke_rc=$?
    if [ "$smoke_rc" -eq 78 ]; then
      echo "  golden workflow suite SKIPPED — no probe token configured on this machine."
      echo "  This deploy is UNVERIFIED by the suite. See the provisioning runbook in"
      echo "  mcp-server/smoke-reads.sh. Not treated as a deploy failure."
    else
      echo ""
      echo "  ***  GOLDEN WORKFLOW SUITE FAILED (exit $smoke_rc) AFTER THIS DEPLOY.  ***"
      echo "  The Worker shipped and is answering wrongly, or the probe credential is"
      echo "  refused. Read the output above, and read the failed check together with"
      echo "  this deploy as ONE journey rather than two unrelated facts:"
      echo "      .venv/bin/python tools/ops-record.py trace $CARR_CORRELATION_ID"
      echo "  Rolling back is bin/deploy-worker.sh --pinned-release <sha>."
      exit 1
    fi
  fi
fi
