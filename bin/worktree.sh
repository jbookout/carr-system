#!/bin/zsh
# worktree.sh — one command for an isolated checkout, because the ritual was
# three steps and nobody performed all three.
#
# WHY THIS EXISTS. On 2026-08-10 two sessions collided in ~/carr-system twice in
# one morning: each could only commit safely by hand-naming its paths, and one
# stretch of work sat loose for thirteen hours because committing it would have
# swept the other writer's files. git-writer-gate.py blocks that sweep, and its
# own deny message already tells you the answer: "to change branch, use a
# WORKTREE so no other session's tree moves."
#
# THE MECHANISM WAS NEVER MISSING. Seven worktrees were already live when this
# was written. What was missing is that a usable one takes three steps, two of
# which are invisible until something breaks:
#
#   1. git worktree add                     — the obvious one
#   2. symlink .venv back to the canonical  — .venv/ is gitignored, so a fresh
#      worktree has none, and ten-plus scripts hard-code ./.venv/bin. Without
#      it type-check, export, migrate and the verb probe all fail on a path
#      that simply is not there.
#   3. symlink out/                         — also gitignored. The hooks already
#      write to the ABSOLUTE ~/carr-system/out/hook-guard.log, so leaving out/
#      absent splits some logs from the rest for no benefit. One place to look.
#
# Both long-lived worktrees on this machine had step 2 applied BY HAND, the same
# way, on different days. Doing a thing by hand twice is the system asking for
# the command (rule 9873a0d2, promotion watch).
#
# WHAT THIS DELIBERATELY DOES NOT DO. It does not commit, merge, push, or delete
# a branch. Isolation is about giving a session its own tree, not about taking
# decisions off the human. Removal is explicit and refuses to discard work.
#
# THE NIGHTLY CHAIN STAYS IN THE CANONICAL TREE. bin/nightly.sh exports to the
# vault and rebuilds boards from whatever code is checked out; running it from a
# feature worktree would publish that branch's output as if it were live. Nothing
# here prevents that — it is a convention, stated because an unstated one is how
# this morning's failures happened.
#
# Risk colour GREEN: creates a checkout and two symlinks, writes nothing outside
# the repo, sends nothing.
set -u

REPO="$(cd "$(dirname "$0")/.." && pwd)"
# The canonical tree is where .venv and out/ actually live. When this script is
# itself run FROM a worktree, dirname gives that worktree — so resolve the real
# one through git rather than assuming, or the symlinks would point at a
# worktree that may be removed tomorrow.
CANON="$(git -C "$REPO" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)"
CANON="${CANON:h}"                      # strip the trailing /.git
[ -d "$CANON" ] || CANON="$REPO"
HOME_DIR="$CANON/.claude/worktrees"     # the convention already in use here

# NEVER NAME A VARIABLE `path` IN ZSH. It is tied to the PATH array, so a scalar
# assignment silently destroys the command search path — the first cut of this
# script did exactly that and died on "command not found: mkdir" three lines
# later, with nothing in the error pointing at the cause. Hence `wt` throughout.

usage() {
  print -r -- "usage: run.sh worktree <name> [--from <base>]   create (branch = <name>)"
  print -r -- "       run.sh worktree --list                   show every worktree"
  print -r -- "       run.sh worktree --remove <name>          remove one (refuses if dirty)"
  exit 2
}

link() {                                # link <target> <linkname>
  local target="$1" name="$2"
  if [ -e "$name" ] && [ ! -L "$name" ]; then
    print -r -- "  !! $name exists and is NOT a symlink — left alone"
    return 0
  fi
  ln -sfn "$target" "$name"
  print -r -- "  ok  $(basename "$name") -> $target"
}

[ $# -ge 1 ] || usage

case "$1" in
  --list|-l)
    git -C "$CANON" worktree list
    exit 0
    ;;
  --remove|-r)
    [ $# -ge 2 ] || usage
    name="$2"
    wt="$HOME_DIR/$name"
    [ -d "$wt" ] || { print -r -- "no worktree at $wt"; exit 1; }
    # Drop the symlinks THIS script created, before judging the tree dirty.
    # They are plumbing, not work, and leaving them in made the dirty-check
    # refuse its own handiwork — caught on the first live removal.
    #
    # UNTRACKED IS THE TEST, NOT -L. The original test was `[ -L ]` alone, on
    # the reasoning that only a symlink is ever unlinked so a genuine .venv is
    # kept and correctly reported dirty. A TRACKED symlink is still a symlink,
    # so that reasoning has a hole: dropping one deletes a tracked file, which
    # makes the tree dirty and fires the refusal below on dirt this script has
    # just created. Branch dealroom-chrome-tidy tracks .venv and hit exactly
    # that on 2026-08-13 — refused, and left without its .venv either way.
    # Asking git whether the path is tracked closes it: plumbing this script
    # made is untracked by definition, so anything tracked is somebody's work
    # and is never touched.
    typeset -a dropped; dropped=()
    for l in .venv out mcp-server/node_modules; do
      if [ -L "$wt/$l" ] && ! git -C "$wt" ls-files --error-unmatch "$l" >/dev/null 2>&1; then
        rm "$wt/$l" && dropped+=("$l")
      fi
    done
    # RESTORE ON ANY PATH THAT DOES NOT REMOVE. A refusal must leave the tree
    # exactly as it was found: the first version returned the tree minus its
    # .venv, so the next command run in there failed on a path that was simply
    # not there — the same invisible breakage the create path exists to prevent.
    restore_dropped() {
      local l
      for l in $dropped; do ln -sfn "$CANON/$l" "$wt/$l"; done
    }
    # REFUSE ON DIRTY, always. A worktree exists to hold work in progress, so
    # removing one is the single most likely way to lose some. The check is
    # cheap and the failure it prevents is not recoverable from git.
    if [ -n "$(git -C "$wt" status --porcelain 2>/dev/null)" ]; then
      # RESTORE BEFORE PRINTING, never after. Putting it after the messages made
      # the tree's repair depend on surviving output: a caller piping this to
      # `head` closes the pipe after two lines, the third print takes SIGPIPE,
      # and the script dies before it can put the links back — the very damage
      # this restore exists to prevent, reappearing only under a pipe. Found
      # 2026-08-13 when a verification run using `| head -2` reported the links
      # MISSING while the identical unpiped command left them intact.
      restore_dropped
      print -r -- "REFUSED — $name has uncommitted work:"
      git -C "$wt" status --porcelain | sed 's/^/    /'
      print -r -- "Commit it there (naming paths), or remove the directory yourself if it is truly scrap."
      exit 1
    fi
    # The other non-removing path: git itself can refuse (a submodule, a locked
    # worktree, a file it cannot delete). Same rule as the dirty refusal — a
    # tree that survives keeps its plumbing.
    if git -C "$CANON" worktree remove "$wt"; then
      print -r -- "removed $name (branch kept)"
      exit 0
    fi
    restore_dropped
    exit 1
    ;;
esac

name="$1"; shift
base="main"
while [ $# -gt 0 ]; do
  case "$1" in
    --from) base="${2:-main}"; shift 2 ;;
    *) usage ;;
  esac
done

case "$name" in
  -*|*/*|*' '*) print -r -- "name must be a plain branch-safe word: $name"; exit 2 ;;
esac

wt="$HOME_DIR/$name"
if [ -d "$wt" ]; then
  print -r -- "already exists: $wt"
  print -r -- "  cd $wt"
  exit 0
fi

mkdir -p "$HOME_DIR" || exit 1

# Reuse the branch when it already exists; create it from <base> when it does
# not. Guessing wrong either way is a confusing error rather than a lost branch.
if git -C "$CANON" show-ref --verify --quiet "refs/heads/$name"; then
  git -C "$CANON" worktree add "$wt" "$name" || exit 1
  print -r -- "attached existing branch $name"
else
  git -C "$CANON" worktree add -b "$name" "$wt" "$base" || exit 1
  print -r -- "branched $name from $base"
fi

link "$CANON/.venv" "$wt/.venv"
link "$CANON/out"   "$wt/out"
# The THIRD gitignored dependency, and it hides the same way the first two did.
# `npm test` in a fresh worktree dies on ERR_MODULE_NOT_FOUND for
# @neondatabase/serverless — which reads as a code failure, not a missing
# checkout artifact, so a session can spend real time chasing a bug that is not
# there. Hit live 2026-08-11 deploying the vendor-stage fix: the suite reported
# 1 fail on a test that had nothing to do with the change. Linked, not
# installed, because a per-worktree npm install would drift from the canonical
# lockfile — the same reasoning as .venv.
[ -d "$CANON/mcp-server/node_modules" ] && link "$CANON/mcp-server/node_modules" "$wt/mcp-server/node_modules"

print -r -- ""
print -r -- "worktree ready — your own tree, nobody else's files in it:"
print -r -- "  cd $wt"
print -r -- ""
print -r -- "Commit normally in there; the shared-tree sweep problem does not apply,"
print -r -- "because this tree holds only your work. Land it with a merge onto main"
print -r -- "from the canonical checkout when you are done, then:"
print -r -- "  ./run.sh worktree --remove $name"
print -r -- ""
print -r -- "Keep the nightly chain in $CANON — it publishes to the vault, so running"
print -r -- "it here would ship this branch's output as if it were live."
