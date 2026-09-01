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
# TWO LIFECYCLE GAPS, closed 2026-08-14 with nine sessions running concurrently:
#
#   CREATE PATH FRESHNESS. A new worktree branched from LOCAL main, which
#   nothing on a busy machine keeps fresh — a fresh worktree's diff landed
#   against a stale base and misdiagnosed what had actually changed. The
#   create path now fetches origin before deciding a base, and defaults NEW
#   branches to origin/main when it exists. `--from` still overrides outright;
#   attaching an EXISTING branch is unchanged (there is no base to pick).
#
#   --sweep. Nothing ever reaped a finished worktree — 22 existed live when
#   this was written, 10 with branches already merged into origin/main, one
#   443 commits behind. `--sweep [--dry-run]` reaps a worktree only when ALL
#   THREE hold: its branch is merged (see below), its tree is clean
#   (the identical test --remove uses), and no file in it was touched in the
#   last 48 hours (the guard against reaping a live session's tree — a session
#   touches its worktree by editing files, which is the one signal that
#   cannot be faked by git metadata living outside the tree). It removes
#   through the SAME code --remove uses (rule a8c55a47: a manual path and an
#   automated path doing the same job must be the same code), so an automatic
#   reap carries the identical dirty-refusal and restore-on-refusal safety as
#   a removal Joe types by hand.
#
#   MERGED means ancestor-of-origin/main OR cherry-equivalent (added
#   2026-08-14, same day, once the ancestry test alone proved to under-report
#   under squash/rebase merges: a branch whose content fully landed on
#   origin/main under a different sha still reads unmerged by ancestry
#   alone, and the sweep quietly stops reaping it forever). `git cherry
#   origin/main <branch>` compares PATCH-IDs, not shas — if every line it
#   prints starts with `-`, every commit on the branch is already upstream
#   in substance. A kept tree whose cherry output mixes `-` and `+` is
#   reported "partially landed"; a zero-reap run alongside an idle,
#   behind-origin/main, still-unmerged tree prints a caveat pointing at
#   `git cherry origin/main <branch>` as the by-hand check. The failure
#   direction stays safe either way: anything this predicate still misses
#   simply stays KEPT, same as before it existed.
#
# Risk colour GREEN: creates a checkout and two symlinks, writes nothing outside
# the repo, sends nothing. --sweep additionally REMOVES worktrees, but only
# ones already proven merged, clean, and idle — the same removal --remove
# already performs on request.
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

# Same resolution ops/ci.sh uses: prefer the venv, fall back to whatever
# python3 is on PATH. Only used for the 48h idle scan (newest_mtime_age_h
# below) — everything else in this script is plain git/POSIX.
PY="$CANON/.venv/bin/python"
[ -x "$PY" ] || PY=python3

# NEVER NAME A VARIABLE `path` IN ZSH. It is tied to the PATH array, so a scalar
# assignment silently destroys the command search path — the first cut of this
# script did exactly that and died on "command not found: mkdir" three lines
# later, with nothing in the error pointing at the cause. Hence `wt` throughout.

usage() {
  print -r -- "usage: run.sh worktree <name> [--from <base>]   create (branch = <name>;"
  print -r -- "                                                base defaults to origin/main)"
  print -r -- "       run.sh worktree --list                   show every worktree"
  print -r -- "       run.sh worktree --remove <name|path>     remove one (refuses if dirty)"
  print -r -- "                                                a name resolves under $HOME_DIR;"
  print -r -- "                                                a path reaches trees anywhere"
  print -r -- "       run.sh worktree --sweep [--dry-run]      reap merged+clean+idle(48h) trees"
  print -r -- "                                                through the same code as --remove"
  print -r -- "       run.sh worktree --plumb [path]           link .venv/out/node_modules into a"
  print -r -- "                                                worktree THIS script did not create"
  print -r -- "                                                (default: the worktree at \$PWD)"
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

# runtime_matches_lock <worker-dir> <node_modules-dir>
#
# A canonical checkout can be cleanly *present* yet many commits behind the
# worktree consuming its shared npm cache. Directory existence therefore proves
# nothing. npm writes node_modules/.package-lock.json from the exact resolution
# it installed; compare that installed resolution with the consuming
# worktree's lock before linking. Platform-inapplicable optional packages are
# legitimately absent, but every required package must be present and every
# installed entry must be byte-for-byte the lock entry the worktree expects.
runtime_matches_lock() {
  "$PY" - "$1/package-lock.json" "$2/.package-lock.json" <<'PYEOF'
import json, sys
try:
    expected = json.load(open(sys.argv[1]))["packages"]
    installed = json.load(open(sys.argv[2]))["packages"]
except Exception:
    raise SystemExit(1)
expected.pop("", None)
if any(key not in expected or installed[key] != expected[key] for key in installed):
    raise SystemExit(1)
if any(key not in installed and not row.get("optional")
       for key, row in expected.items()):
    raise SystemExit(1)
PYEOF
}

# link_node_runtime <worktree> — one shared decision for create-time plumbing
# and SessionStart --plumb. A stale shared cache is worse than no cache: the
# latter prints a missing-dependency error, while the former runs an arbitrary
# older dependency graph and can make unrelated current tests fail. On a
# mismatch, remove only this script's untracked symlink and leave the worktree
# dependency-empty; ordinary STORE calls still boot zero-install, and code work
# can run `npm --prefix mcp-server ci` locally from its own lock.
link_node_runtime() {
  local wt="$1"
  local runtime="$CANON/mcp-server/node_modules"
  local name="$wt/mcp-server/node_modules"
  [ -d "$runtime" ] || return 0
  if runtime_matches_lock "$wt/mcp-server" "$runtime"; then
    link "$runtime" "$name"
    return 0
  fi
  if [ -L "$name" ] && ! git -C "$wt" ls-files --error-unmatch \
       "mcp-server/node_modules" >/dev/null 2>&1; then
    rm "$name"
  fi
  print -r -- "  !! shared node_modules does not match this worktree's package-lock.json — left unlinked"
  print -r -- "     run npm --prefix mcp-server ci in the worktree when local Node tooling is needed"
}

# ---------------------------------------------------------------------------
# REMOVAL PLUMBING — shared by --remove and --sweep, so a worktree reaped
# automatically goes through the EXACT code path a worktree removed by hand
# goes through (rule a8c55a47). PLUMBING_DROPPED is a single scratch global
# rather than a returned/parsed value on purpose: this script is strictly
# sequential (one removal happens at a time, start to finish, before the next
# begins), so a scratch global is simpler and safer than threading an array
# through a command substitution — `${(@f)$(...)}` on TRULY empty output is a
# known zsh footgun (it can yield one empty-string element instead of zero),
# which would hand restore_plumbing a bogus name and `ln -sfn` a bare
# "$CANON/" target. A global array populated by a plain for-loop cannot do
# that: unset, it is simply empty.
typeset -ga PLUMBING_DROPPED

# drop_plumbing <wt> — drop THIS SCRIPT's own untracked symlinks (.venv, out,
# mcp-server/node_modules) so the dirty-check below judges the session's own
# work, not this script's handiwork. Sets PLUMBING_DROPPED to what it actually
# dropped (may be empty).
#
# UNTRACKED IS THE TEST, NOT -L. The original test was `[ -L ]` alone, on the
# reasoning that only a symlink is ever unlinked so a genuine .venv is kept
# and correctly reported dirty. A TRACKED symlink is still a symlink, so that
# reasoning has a hole: dropping one deletes a tracked file, which makes the
# tree dirty and fires the refusal on dirt this script just created. Branch
# dealroom-chrome-tidy tracks .venv and hit exactly that on 2026-08-13 —
# refused, and left without its .venv either way. Asking git whether the path
# is tracked closes it: plumbing this script made is untracked by definition,
# so anything tracked is somebody's work and is never touched.
drop_plumbing() {
  local wt="$1" l
  PLUMBING_DROPPED=()
  for l in .venv out mcp-server/node_modules; do
    if [ -L "$wt/$l" ] && ! git -C "$wt" ls-files --error-unmatch "$l" >/dev/null 2>&1; then
      rm "$wt/$l" && PLUMBING_DROPPED+=("$l")
    fi
  done
}

# restore_plumbing <wt> — put back exactly what the last drop_plumbing took.
# RESTORE ON ANY PATH THAT DOES NOT REMOVE, always: the first version of this
# script returned the tree minus its .venv on a refusal, so the next command
# run in there failed on a path that was simply not there — the same
# invisible breakage the create path exists to prevent.
restore_plumbing() {
  local wt="$1" l
  for l in "${PLUMBING_DROPPED[@]}"; do ln -sfn "$CANON/$l" "$wt/$l"; done
}

# is_dirty <wt> — exit 0 if the tree has uncommitted work. Call only AFTER
# drop_plumbing, or this script's own symlinks read as somebody's work.
is_dirty() { [ -n "$(git -C "$1" status --porcelain 2>/dev/null)" ]; }

# dirty_count <wt> — number of dirty-status lines, for --sweep's report only.
dirty_count() { git -C "$1" status --porcelain 2>/dev/null | wc -l | tr -d ' '; }

# newest_mtime_age_h <wt> — hours since the newest file mtime in <wt>,
# excluding the three symlinked plumbing dirs. This IS the 48h guard against
# reaping a live session's tree: a session interacts with its worktree by
# editing files, and edits land inside <wt> itself — the shared gitdir under
# $CANON/.git/worktrees/<name>/ does NOT live in <wt>, so `git status`,
# `git fetch`, even a commit's ref update, do not move this number; only a
# real file edit does. Piped through Python for portability: ops/ci.sh runs
# this repo's suites on a GitHub ubuntu runner too, and BSD `stat -f` (used
# elsewhere in this repo, e.g. bin/nightly.sh) is macOS-only.
newest_mtime_age_h() {
  "$PY" - "$1" <<'PYEOF'
import os, sys, time
root = sys.argv[1]
skip = {os.path.join(root, "out"), os.path.join(root, ".venv"),
        os.path.join(root, "mcp-server", "node_modules")}
newest = 0.0
for dirpath, dirnames, filenames in os.walk(root):
    if dirpath in skip:
        dirnames[:] = []
        continue
    dirnames[:] = [d for d in dirnames if os.path.join(dirpath, d) not in skip]
    for f in filenames:
        p = os.path.join(dirpath, f)
        try:
            mt = os.path.getmtime(p)
        except OSError:
            continue
        if mt > newest:
            newest = mt
print(f"{(time.time() - newest) / 3600:.2f}" if newest else "999999")
PYEOF
}

# remove_one <wt> <name> <dry:0|1> — THE removal code. --remove calls this
# directly; --sweep calls it too. REFUSE ON DIRTY, always, and restore the
# plumbing BEFORE printing, never after: putting it after made the tree's
# repair depend on surviving output — a caller piping this to `head` closes
# the pipe after two lines, the third print takes SIGPIPE, and the script
# dies before it can put the links back (found 2026-08-13, only reproduced
# under a pipe). Returns 0 on removed-or-would-remove, 1 on refused/failed.
remove_one() {
  local wt="$1" name="$2" dry="${3:-0}"
  drop_plumbing "$wt"
  if is_dirty "$wt"; then
    restore_plumbing "$wt"
    print -r -- "REFUSED — $name has uncommitted work:"
    git -C "$wt" status --porcelain | sed 's/^/    /'
    print -r -- "Commit it there (naming paths), or remove the directory yourself if it is truly scrap."
    return 1
  fi
  if [ "$dry" = "1" ]; then
    restore_plumbing "$wt"
    print -r -- "would remove $name (branch kept)"
    return 0
  fi
  # The other non-removing path: git itself can refuse (a submodule, a locked
  # worktree, a file it cannot delete). Same rule as the dirty refusal — a
  # tree that survives keeps its plumbing.
  if git -C "$CANON" worktree remove "$wt"; then
    print -r -- "removed $name (branch kept)"
    return 0
  fi
  restore_plumbing "$wt"
  return 1
}

[ $# -ge 1 ] || usage

case "$1" in
  --list|-l)
    git -C "$CANON" worktree list
    exit 0
    ;;
  --remove|-r)
    [ $# -ge 2 ] || usage
    target="$2"
    # A NAME resolves under HOME_DIR; anything containing a slash or a ~ is
    # taken as a PATH. Name-only was the whole vocabulary until 2026-08-13, and
    # it could not reach a single one of the eleven trees living in /private/tmp
    # or a session scratchpad — so that day's cleanup was done by hand with raw
    # `git worktree remove`, which skips the symlink handling and the dirty
    # refusal that make this command safe, and hit the node_modules refusal
    # twice. Doing a thing by hand twice is the system asking for the command
    # (rule 9873a0d2), the same reasoning that created this script.
    case "$target" in
      */*|'~'*) wt="${target:A}" ;;
      *)        wt="$HOME_DIR/$target" ;;
    esac
    name="${wt:t}"
    [ -d "$wt" ] || { print -r -- "no worktree at $wt"; exit 1; }
    # IT MUST BE A REGISTERED WORKTREE OF THIS REPO, and never the canonical
    # checkout. Accepting a path widens what this command can be pointed at, so
    # the guard has to widen with it: without this, one mistyped path hands an
    # arbitrary directory to `git worktree remove`, and the canonical tree is
    # itself in that list — removing it would take the repo with it.
    if [ "${wt:A}" = "${CANON:A}" ]; then
      print -r -- "refusing to remove the canonical tree: $CANON"
      exit 2
    fi
    if ! git -C "$CANON" worktree list --porcelain | awk '/^worktree /{print $2}' \
         | grep -qx -e "${wt:A}" -e "$wt"; then
      print -r -- "not a registered worktree of this repo: $wt"
      print -r -- "  ./run.sh worktree --list   shows the ones that are"
      exit 2
    fi
    # The drop-symlinks / dirty-refusal / remove-or-restore sequence used to
    # live inline here. It is now remove_one (defined above), shared with
    # --sweep, so a worktree reaped automatically goes through the identical
    # code a worktree removed by hand goes through (rule a8c55a47).
    remove_one "$wt" "$name" 0
    exit $?
    ;;
  --plumb)
    shift
    # TWO DOORS, ONE PLUMBING JOB (2026-08-14). `run.sh worktree <name>` links
    # .venv/out/mcp-server-node_modules at CREATE time (see `link` calls at the
    # bottom of this file). That covers worktrees THIS script creates. It does
    # nothing for a worktree created by any other door — Claude Code's own
    # native isolation (a bare `git worktree add` under .claude/worktrees/,
    # with no call into this script at all), Codex, or a bare `git worktree
    # add` typed by hand. A session that lands in one of those finds no
    # mcp-server/node_modules, cannot run its own tooling, and the failure
    # mode observed live is worse than an error: it falls back to running from
    # the shared CANONICAL checkout instead — the exact multi-writer collision
    # `run.sh worktree` exists to prevent (see the file header above).
    #
    # `--plumb [path]` is the same three links, callable against a worktree
    # this script did not create. It reuses `link()` and `$CANON` verbatim —
    # no second copy of the linking decision (rule a8c55a47: a manual path and
    # an automated path doing the same job must be the same code). The boot
    # hook (hooks/worktree-self-plumb.py) calls this with an explicit path;
    # a human runs it bare from inside the worktree and gets the default below.
    if [ $# -ge 1 ]; then
      target="$1"
      case "$target" in
        */*|'~'*) wt="${target:A}" ;;
        *)        wt="$HOME_DIR/$target" ;;
      esac
    else
      # DEFAULT: the worktree containing the CURRENT directory, not $REPO —
      # $REPO can itself be the canonical tree when this script is invoked by
      # absolute path (the boot hook always does), so a plain path-based
      # default would silently resolve to the wrong tree. `git rev-parse` with
      # no `-C` reads the process's actual $PWD, which is always right for
      # "the worktree I am standing in right now".
      wt="$(git rev-parse --show-toplevel 2>/dev/null)"
      if [ -z "$wt" ]; then
        print -r -- "not inside a git worktree — pass a path: run.sh worktree --plumb <path>"
        exit 2
      fi
      wt="${wt:A}"
    fi
    [ -d "$wt" ] || { print -r -- "no worktree at $wt"; exit 1; }
    # Same two guards --remove uses, same reasons: never the canonical tree
    # (it holds the real dirs, not links to them — there is nothing to plumb),
    # and never a path this repo does not actually recognize as a worktree.
    if [ "${wt:A}" = "${CANON:A}" ]; then
      print -r -- "refusing to plumb the canonical tree: $CANON (nothing to plumb — it holds the real dirs)"
      exit 2
    fi
    if ! git -C "$CANON" worktree list --porcelain | awk '/^worktree /{print $2}' \
         | grep -qx -e "${wt:A}" -e "$wt"; then
      print -r -- "not a registered worktree of this repo: $wt"
      print -r -- "  ./run.sh worktree --list   shows the ones that are"
      exit 2
    fi
    link "$CANON/.venv" "$wt/.venv"
    link "$CANON/out"   "$wt/out"
    link_node_runtime "$wt"
    exit 0
    ;;
  --sweep)
    shift
    dry=0
    case "${1:-}" in
      --dry-run) dry=1; shift ;;
    esac
    [ $# -eq 0 ] || usage

    # STEP 1: prune. Clears registered entries whose directory is already gone
    # from disk — there is nothing left to lose, so this runs even under
    # --dry-run; a dangling administrative entry is bookkeeping, not work.
    git -C "$CANON" worktree prune

    # STEP 2: fetch, so "merged into origin/main" means TODAY's origin/main,
    # not whatever this machine last happened to see — same warn-and-continue
    # as the create path below. An offline sweep reports against the last
    # known origin/main rather than refuse outright.
    if ! git -C "$CANON" fetch -q origin; then
      print -r -- "  !! fetch origin failed (offline?) — sweeping against last-known origin/main"
    fi
    have_origin_main=0
    git -C "$CANON" show-ref --verify --quiet "refs/remotes/origin/main" && have_origin_main=1

    total=0
    reaped=0
    kept=0
    # Set when a KEPT tree is unmerged, idle >48h, and behind origin/main —
    # exactly the shape a squash/rebase merge produces when the cherry
    # predicate above also fails to catch it (a genuinely rewritten diff,
    # not a clean patch-equivalent). Drives the point-3 caveat below.
    stale_unmerged_kept=0

    # Parse `git worktree list --porcelain` by hand: a block is
    #   worktree <path>  [bare]  HEAD <sha>  [branch refs/heads/<name>]  <blank>
    # flush_entry handles one finished block. The loop calls it right before
    # starting the NEXT block; one more call after the loop handles the last
    # block, since there is no trailing blank `worktree` line to trigger it.
    cur_path=""
    cur_head=""
    cur_branch=""
    cur_bare=0

    flush_entry() {
      [ -n "$cur_path" ] || return 0
      local p="$cur_path" head="$cur_head" branch="$cur_branch" bare="$cur_bare"
      cur_path=""; cur_head=""; cur_branch=""; cur_bare=0

      [ "${p:A}" = "${CANON:A}" ] && return 0   # never the canonical tree
      [ "$bare" = "1" ] && return 0              # no working tree to judge
      [ -d "$p" ] || return 0                    # prune (above) should have caught this

      total=$((total+1))
      local nm="${p:t}"

      # condition (i): branch tip is an ancestor of origin/main, OR every
      # commit on the branch is patch-equivalent to one already on
      # origin/main (2026-08-14, cherry predicate). Ancestry alone
      # under-reports here: a squash or rebase merge lands the branch's
      # content on origin/main under a DIFFERENT sha (rebase) or one
      # combined sha (squash), so `merge-base --is-ancestor` says no even
      # though nothing on the branch is actually missing upstream.
      # `git cherry <upstream> <branch>` compares PATCH-IDs instead of
      # ancestry: each line is prefixed `-` when a patch-equivalent commit
      # is already upstream, `+` when it is not. Non-empty output where
      # EVERY line is `-` means the branch's content is fully landed. The
      # failure direction stays safe either way — a branch this predicate
      # still misses (e.g. a genuinely rewritten diff) simply stays KEPT,
      # same as before this existed.
      local merged=0 ancestry_merged=0 ahead="?" behind="?"
      local cherry_landed=0 cherry_partial=0
      if [ -n "$branch" ] && [ "$have_origin_main" = "1" ]; then
        git -C "$CANON" merge-base --is-ancestor "refs/heads/$branch" "refs/remotes/origin/main" \
          2>/dev/null && ancestry_merged=1
        local ab
        ab="$(git -C "$CANON" rev-list --left-right --count \
              "refs/heads/$branch...refs/remotes/origin/main" 2>/dev/null)"
        ahead="${ab%%$'\t'*}"; behind="${ab##*$'\t'}"

        local cherry_out
        cherry_out="$(git -C "$CANON" cherry "refs/remotes/origin/main" "refs/heads/$branch" 2>/dev/null)"
        if [ -n "$cherry_out" ]; then
          if ! print -r -- "$cherry_out" | grep -qv '^-'; then
            cherry_landed=1
          elif print -r -- "$cherry_out" | grep -q '^-' \
               && print -r -- "$cherry_out" | grep -q '^+'; then
            cherry_partial=1
          fi
        fi

        merged=$ancestry_merged
        [ "$merged" != "1" ] && [ "$cherry_landed" = "1" ] && merged=1
      fi

      # condition (ii): clean, by the SAME test --remove uses (rule a8c55a47)
      drop_plumbing "$p"
      local dcount; dcount="$(dirty_count "$p")"
      restore_plumbing "$p"
      local dirty=0; [ "${dcount:-0}" != "0" ] && dirty=1

      # condition (iii): idle 48h+
      local age_h; age_h="$(newest_mtime_age_h "$p")"

      # last-commit age, for the report line only
      local ref="${branch:+refs/heads/$branch}"
      [ -z "$ref" ] && ref="$head"
      local lc_epoch lc_age
      lc_epoch="$(git -C "$CANON" log -1 --format=%ct "$ref" 2>/dev/null)"
      if [ -n "$lc_epoch" ]; then
        lc_age="$(( ( $(date +%s) - lc_epoch ) / 3600 ))h"
      else
        lc_age="?"
      fi

      local branch_label="${branch:-<detached:${head[1,8]}>}"
      local stat_line
      stat_line="$(printf '%-24s branch=%-28s last-commit=%-6s %s/%s ahead/behind  dirty=%s' \
                   "$nm" "$branch_label" "$lc_age" "$ahead" "$behind" "$dcount")"

      if [ -z "$branch" ] || [ "$have_origin_main" != "1" ]; then
        print -r -- "  KEEP  $stat_line  — cannot verify merge state (detached HEAD or no origin/main)"
        kept=$((kept+1))
      elif [ "$merged" != "1" ]; then
        local reason="unmerged"
        [ "$cherry_partial" = "1" ] && reason="unmerged (partially landed)"
        print -r -- "  KEEP  $stat_line  — $reason"
        kept=$((kept+1))
        if [ "${age_h%%.*}" -gt 48 ] 2>/dev/null \
           && [ "${behind:-0}" != "0" ] && [ "${behind:-0}" != "?" ]; then
          stale_unmerged_kept=1
        fi
      elif [ "$dirty" = "1" ]; then
        print -r -- "  KEEP  $stat_line  — dirty"
        kept=$((kept+1))
      elif [ "${age_h%%.*}" -le 48 ]; then
        print -r -- "  KEEP  $stat_line  — active, touched ${age_h}h ago (<48h guard)"
        kept=$((kept+1))
      else
        local via=""
        [ "$ancestry_merged" != "1" ] && [ "$cherry_landed" = "1" ] && via=" (cherry-detected)"
        print -r -- "  REAP  $stat_line  — merged, clean, idle ${age_h}h$via"
        if remove_one "$p" "$nm" "$dry"; then
          reaped=$((reaped+1))
        else
          kept=$((kept+1))
        fi
      fi
    }

    while IFS= read -r ln; do
      case "$ln" in
        worktree\ *) flush_entry; cur_path="${ln#worktree }" ;;
        HEAD\ *)     cur_head="${ln#HEAD }" ;;
        branch\ *)   cur_branch="${ln#branch refs/heads/}" ;;
        bare)        cur_bare=1 ;;
      esac
    done < <(git -C "$CANON" worktree list --porcelain)
    flush_entry

    print -r -- ""
    # point 3: a zero-reap run alongside a kept tree that is unmerged,
    # behind origin/main, and idle 48h+ is exactly the shape a squash
    # merge produces when the cherry predicate above still could not prove
    # it landed — flag it instead of reporting a silent clean sweep.
    if [ "$reaped" = "0" ] && [ "$stale_unmerged_kept" = "1" ]; then
      print -r -- "  caveat: ancestry can under-report merged content under squash merges — check a specific tree by hand with: git cherry origin/main <branch>"
    fi
    print -r -- "$total total, $reaped reaped, $kept kept"
    exit 0
    ;;
esac

name="$1"; shift
base=""
base_override=0
while [ $# -gt 0 ]; do
  case "$1" in
    --from) base="${2:-main}"; base_override=1; shift 2 ;;
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
  # CREATE PATH FRESHNESS (2026-08-14). Local main is chronically stale on a
  # machine running this many concurrent sessions — a NEW branch off it diffs
  # and diagnoses against the wrong base. Fetch before deciding the base, and
  # default to origin/main when it exists. --from still wins outright: an
  # explicit base is never second-guessed by a fetch.
  if ! git -C "$CANON" fetch -q origin; then
    print -r -- "  !! fetch origin failed (offline?) — branching from local state"
  fi
  if [ "$base_override" != "1" ]; then
    if git -C "$CANON" show-ref --verify --quiet "refs/remotes/origin/main"; then
      base="origin/main"
    else
      base="main"
    fi
  fi
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
link_node_runtime "$wt"

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
