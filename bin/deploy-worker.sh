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
#   origin/main, and refuse to ship a dirty mcp-server/ tree (this repo
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
#
# Per rule a8c55a47, this is the same code the manual path and any automated
# path both run. There is no second way to deploy.
set -eu

REPO="$(cd "$(dirname "$0")/.." && pwd)"
WORKER_DIR="$REPO/mcp-server"
COUNT_FILE="$WORKER_DIR/.last-deployed-verb-count"
WRANGLER="$WORKER_DIR/node_modules/.bin/wrangler"

CHECK_ONLY=0
ALLOW_SHRINK=0
for arg in "$@"; do
  case "$arg" in
    --check)         CHECK_ONLY=1 ;;
    --allow-shrink)  ALLOW_SHRINK=1 ;;
    *) echo "deploy-worker: unknown argument '$arg'" >&2; exit 64 ;;
  esac
done

fail() { echo ""; echo "REFUSED: $1" >&2; echo "" >&2; exit 1; }

cd "$REPO"

# ---------- preflight 1: exactly origin/main ----------
echo "== preflight =="
git fetch origin main --quiet 2>/dev/null || fail "could not reach origin to verify main."

HEAD_SHA="$(git rev-parse HEAD)"
MAIN_SHA="$(git rev-parse origin/main)"
BRANCH="$(git rev-parse --abbrev-ref HEAD)"

if [ "$HEAD_SHA" != "$MAIN_SHA" ]; then
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
fi
echo "  OK  HEAD is origin/main ($MAIN_SHA)"

# ---------- preflight 2: nothing uncommitted in the worker ----------
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
DIRTY_LIST="$(git status --porcelain -- mcp-server/ \
  | grep -v 'mcp-server/node_modules' \
  | grep -v 'mcp-server/.last-deployed-verb-count' || true)"
DIRTY="$(printf '%s' "$DIRTY_LIST" | grep -c . || true)"
if [ "$DIRTY" != "0" ]; then
  printf '%s\n' "$DIRTY_LIST" >&2
  fail "mcp-server/ has $DIRTY uncommitted change(s) above.
  Shipping them would put code in production that is in no commit, and this
  repo regularly holds ANOTHER live session's work (rule 308ef1de). Commit
  them deliberately, or deploy from a clean worktree."
fi
echo "  OK  mcp-server/ is clean"

# ---------- preflight 3: verb count did not shrink ----------
[ -x "$WRANGLER" ] || fail "wrangler not found at $WRANGLER (run npm install in mcp-server/)."

SHIPPING="$(cd "$WORKER_DIR" && node --input-type=module -e '
import("'"$WORKER_DIR"'/src/tools.js")
  .then(m => console.log(Object.keys(m.TOOLS).length))
  .catch(e => { console.error(e.message); process.exit(1); });' 2>/dev/null)"

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
"$WRANGLER" deploy

# ---------- postflight ----------
echo ""
echo "== postflight =="
printf '%s\n' "$SHIPPING" > "$COUNT_FILE"
echo "  recorded $SHIPPING verbs in $(basename "$COUNT_FILE")"
echo "  COMMIT THAT FILE — it is the baseline the next deploy is measured against."
echo ""
echo "  Verify live before you walk away: call list-verbs from a session and"
echo "  confirm it reports $SHIPPING. A deploy that returns success and a"
echo "  registry that answers are two different claims (rule c53beeaa)."
