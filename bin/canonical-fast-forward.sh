#!/bin/zsh
# canonical-fast-forward.sh — keep the canonical checkout within one day of
# origin/main, and never at the cost of anything sitting in it.
#
# WR-000040's AC-FRESH: "A scheduled fast-forward keeps canonical within one day
# of origin/main and pages visibly when the tree is dirty or the fast-forward
# fails."
#
# WHY THIS EXISTS, measured 2026-09-11. The canonical checkout at
# ~/carr-system sat at d75a4ab6 — 111 commits behind origin/main, 0 ahead — and
# had done for nine days. Twenty of its twenty-four "modified" paths held bytes
# that are already on origin/main, in commits canonical has never pulled: the
# tree was not dirty with anyone's work, it was dirty with the past. Every
# derived thing — dependency caches, repo-vs-production checks, the Dell
# migration precondition — inherited that.
#
# ── THE PROPERTY THIS FILE MUST HOLD ─────────────────────────────────────────
#
# IT NEVER DESTROYS ANYTHING. The only git command that moves the branch is
# `git merge --ff-only`, which fails rather than rewriting history, and it is
# not reached at all unless the tree is clean of TRACKED modifications. No
# reset, no checkout of a path, no clean, no stash — a fast-forward that had to
# discard something to succeed would be the exact failure this job is supposed
# to page about.
#
# TRACKED DIRT ONLY, which is the accepted plan's wording and not a softening.
# Untracked paths are a separate settlement question: WR-000040's AC-CLEAN
# routes each one to Joe for a discard-or-land ruling, and a freshness job that
# refused to run until that ruling arrived would simply never run. So untracked
# paths are REPORTED in the receipt and do not block the fast-forward; tracked
# modifications block it absolutely, because those are somebody's edit.
#
# IT IS NOT THE WATCHDOG. bin/canonical-dirty-watchdog.sh is a separate job,
# separately scheduled, and it pages when this one has not run. A freshness
# machine whose only alarm lives inside the job it watches is silent in exactly
# the case that matters — the job never fired at all.
#
#   usage: bin/canonical-fast-forward.sh [--repository PATH] [--dry-run]
#
# Exit codes, and each is a distinct thing a reader needs to tell apart:
#   0  already current, or fast-forwarded cleanly
#   3  refused: tracked modifications present (somebody's edit is in the way)
#   4  refused: canonical is AHEAD of origin/main (a fast-forward would lose it)
#   5  failed: fetch or merge failed
set -u

REPO="${CARR_CANONICAL_REPO:-$HOME/carr-system}"
DRY_RUN=0
while [ $# -gt 0 ]; do
  case "$1" in
    --repository) REPO="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    *) print -u2 "canonical-fast-forward: unknown argument $1"; exit 64 ;;
  esac
done

if [ ! -d "$REPO/.git" ]; then
  print -u2 "canonical-fast-forward: $REPO is not a git checkout"
  exit 64
fi

git -C "$REPO" rev-parse --git-dir >/dev/null 2>&1 || { print -u2 "canonical-fast-forward: $REPO unreadable"; exit 64; }

branch=$(git -C "$REPO" rev-parse --abbrev-ref HEAD 2>/dev/null)
print "canonical-fast-forward: repository=$REPO branch=$branch"

# Tracked dirt first, BEFORE the fetch: a job that refuses should refuse
# cheaply, and a fetch on a dirty tree invites the temptation to "just tidy".
tracked_dirty=$(git -C "$REPO" status --porcelain --untracked-files=no | wc -l | tr -d ' ')
untracked=$(git -C "$REPO" status --porcelain --untracked-files=normal | grep -c '^??' || true)
print "canonical-fast-forward: tracked_modified=$tracked_dirty untracked=$untracked"

if [ "$tracked_dirty" -ne 0 ]; then
  print -u2 "canonical-fast-forward: REFUSED — $tracked_dirty tracked path(s) modified in canonical."
  print -u2 "canonical-fast-forward: those are somebody's edit. Land or discard them; this job will not."
  git -C "$REPO" status --porcelain --untracked-files=no >&2
  exit 3
fi

if ! git -C "$REPO" fetch --quiet origin main 2>&1; then
  print -u2 "canonical-fast-forward: FAILED — could not fetch origin/main"
  exit 5
fi

behind=$(git -C "$REPO" rev-list --count HEAD..origin/main)
ahead=$(git -C "$REPO" rev-list --count origin/main..HEAD)
print "canonical-fast-forward: behind=$behind ahead=$ahead"

if [ "$ahead" -ne 0 ]; then
  print -u2 "canonical-fast-forward: REFUSED — canonical is $ahead commit(s) AHEAD of origin/main."
  print -u2 "canonical-fast-forward: a fast-forward cannot represent that, and this job never rewrites."
  exit 4
fi

if [ "$behind" -eq 0 ]; then
  print "canonical-fast-forward: already current with origin/main"
  exit 0
fi

if [ "$DRY_RUN" -eq 1 ]; then
  print "canonical-fast-forward: --dry-run, would fast-forward $behind commit(s)"
  exit 0
fi

if ! git -C "$REPO" merge --ff-only origin/main; then
  print -u2 "canonical-fast-forward: FAILED — ff-only merge refused"
  exit 5
fi

print "canonical-fast-forward: fast-forwarded $behind commit(s) to $(git -C "$REPO" rev-parse --short HEAD)"
exit 0
