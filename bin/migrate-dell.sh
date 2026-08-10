#!/bin/zsh
# migrate-dell.sh — ONE COMMAND. Brings Dell's Mac onto the new system.
#
# Joe, 2026-08-09: "i want to get on his laptop this week (maybe tuesday) and
# just say 'do the migration' and it just happens. no human steps, no permission
# needed. it just works"
#
# So the design constraints are absolute, and every one of them is checked by
# ops/migrate-dell-selftest.py rather than trusted:
#   NO sudo.            The gate hardening was removed 2026-08-09 precisely so
#                       machine setup would never need a password again.
#   NO credentials.     No token is read, written, or prompted for.
#   NO prompts.         Nothing waits on a human. stdin is closed on every child.
#   IDEMPOTENT.         Safe to run twice, or to re-run after a partial failure.
#   FAIL LOUD.          Any step that fails aborts with a non-zero exit and says
#                       what to do. A half-migrated machine that reports success
#                       is the worst possible outcome.
#
# WHAT IT DOES NOT DO, on purpose: it does not touch the record layer. Marking
# Dell's inbound items acknowledged is a VERB call, and verbs belong to the
# session driving this, not to a shell script. The script prints exactly which
# ones to close and the session does it — see THE SESSION'S HALF at the bottom.
#
# WHY A SCRIPT AND NOT A CHECKLIST. Every step below is currently a hand
# instruction sitting in Dell's inbound queue, where it has aged since 2026-08-06
# without being done. A checklist that has not been actioned in three days is not
# a plan. Rule a8c55a47 — a manual path and an automated path that do the same
# job must be the same code — so this script IS the migration, and the queue item
# becomes a pointer to it.
#
#   bin/migrate-dell.sh            # dry run: report what would change
#   bin/migrate-dell.sh --apply    # do it
set -u

REPO="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="/opt/homebrew/opt/node@22/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
APPLY=0
[ "${1:-}" = "--apply" ] && APPLY=1

PY="python3"
[ -x "$REPO/.venv/bin/python" ] && PY="$REPO/.venv/bin/python"

ok=0; warn=0; fail=0
say()  { print -r -- "$@"; }
good() { print -r -- "  ok    $*"; ok=$((ok+1)); }
note() { print -r -- "  note  $*"; warn=$((warn+1)); }
bad()  { print -r -- "  FAIL  $*"; fail=$((fail+1)); }
step() { print -r -- ""; print -r -- "── $* ──────────────────────────────"; }

say "CARR migration — Dell's machine"
say "repo: $REPO"
[ $APPLY -eq 1 ] && say "mode: APPLY" || say "mode: DRY RUN (re-run with --apply to execute)"

# ── 1. PRECONDITIONS ────────────────────────────────────────────────────────
step "1. preconditions"
[ -d "$REPO/.git" ] && good "repo present" || { bad "no git repo at $REPO — clone carr-system first"; exit 1; }
command -v git >/dev/null && good "git" || bad "git missing"
command -v $PY  >/dev/null && good "python ($PY)" || bad "python3 missing"
[ -f "$HOME/.claude/settings.json" ] && good "Claude settings present" \
  || note "no ~/.claude/settings.json yet — install will create the hooks block"

# A dirty tree can silently lose the other writer's work when we pull — the
# 2026-08-09 incident, and the reason for the two-writer rule on the git tree.
# But refusing on ANY dirty file is too blunt: this machine had 9 unrelated
# modified files from a concurrent session and the script aborted, which on
# Dell's Mac would mean "it just works" doesn't. So refuse only on a REAL
# conflict — a file that is both locally modified AND changed upstream. Anything
# else is another lane's work that a fast-forward will not touch.
git -C "$REPO" fetch --quiet </dev/null 2>/dev/null || true
DIRTY_FILES=$(git -C "$REPO" status --porcelain --untracked-files=no 2>/dev/null | awk '{print $2}')
UPSTREAM=$(git -C "$REPO" rev-parse --abbrev-ref '@{u}' 2>/dev/null || echo "")
CONFLICTS=""

# INCOMING means "changed upstream SINCE WE DIVERGED", which is a merge-base
# diff. A plain two-dot `git diff HEAD upstream` is the symmetric difference of
# two trees, so a file this machine ADDED locally shows up as "changed upstream"
# even though nothing is arriving for it — which is exactly how this check
# flagged migrate-dell.sh against itself and aborted its own test run. If there
# is nothing to pull, no conflict is possible.
if [ -n "$UPSTREAM" ] && [ -n "$DIRTY_FILES" ]; then
  BASE=$(git -C "$REPO" merge-base HEAD "$UPSTREAM" 2>/dev/null)
  if [ -n "$BASE" ]; then
    INCOMING=$(git -C "$REPO" diff --name-only "$BASE" "$UPSTREAM" 2>/dev/null)
    if [ -n "$INCOMING" ]; then
      CONFLICTS=$(print -r -- "$DIRTY_FILES" | while read -r f; do
        [ -n "$f" ] && print -r -- "$INCOMING" | grep -qxF "$f" && print -r -- "$f"
      done)
    fi
  fi
fi
if [ -n "$CONFLICTS" ]; then
  bad "these files are modified locally AND changed upstream — commit or stash them first:"
  print -r -- "$CONFLICTS" | sed 's/^/          /'
elif [ -n "$DIRTY_FILES" ]; then
  note "$(print -r -- "$DIRTY_FILES" | wc -l | tr -d ' ') unrelated modified file(s) — untouched by a fast-forward, proceeding"
else
  good "working tree clean"
fi
[ $fail -gt 0 ] && { say ""; say "ABORTED — $fail precondition(s) failed."; exit 1; }

# ── 2. PULL ─────────────────────────────────────────────────────────────────
step "2. pull latest"
BRANCH=$(git -C "$REPO" rev-parse --abbrev-ref HEAD 2>/dev/null)
[ "$BRANCH" = "main" ] && good "on main" || note "on '$BRANCH', not main — pulling that branch"
# THREE STATES, NOT TWO — found by running --apply on a machine that had its own
# unpushed commits. A bare `git pull --ff-only` aborts with "Not possible to
# fast-forward" whenever the local repo is AHEAD, which is not a failure at all;
# it just means there is nothing to pull. The first version reported that as
# MIGRATION INCOMPLETE and would have done the same on Dell's Mac the moment he
# had one local commit.
if [ -z "$UPSTREAM" ]; then
  note "no upstream tracking branch — skipping pull"
else
  BEHIND=$(git -C "$REPO" rev-list --count HEAD.."$UPSTREAM" 2>/dev/null || echo 0)
  AHEAD=$(git -C "$REPO" rev-list --count "$UPSTREAM"..HEAD 2>/dev/null || echo 0)
  if [ "$BEHIND" -eq 0 ] && [ "$AHEAD" -eq 0 ]; then
    good "already up to date"
  elif [ "$BEHIND" -eq 0 ]; then
    good "$AHEAD local commit(s) not yet pushed, nothing to pull — fine"
  elif [ "$AHEAD" -eq 0 ]; then
    if [ $APPLY -eq 1 ]; then
      if git -C "$REPO" merge --ff-only "$UPSTREAM" </dev/null >/tmp/mig-pull.log 2>&1; then
        good "fast-forwarded $BEHIND commit(s) to $(git -C "$REPO" rev-parse --short HEAD)"
      else
        bad "fast-forward failed — $(tail -1 /tmp/mig-pull.log)"
      fi
    else
      good "would fast-forward $BEHIND commit(s)"
    fi
  else
    # Diverged. Never auto-merge or rebase another writer's history.
    bad "branch has diverged ($AHEAD local, $BEHIND remote) — reconcile by hand before migrating"
  fi
fi

# ── 3. HOOKS ────────────────────────────────────────────────────────────────
# This is the whole of the old hand-instruction "change the guard-unattended
# matcher from Bash to Bash|WebFetch". The repo's ops/config/hooks.json already
# carries the correct matcher plus every gate built since, so installing it
# replaces that manual edit entirely — and brings the conduct gates, the
# escalation gate, the git-writer gate and the integrity check with it.
step "3. python environment"
# THE FRESH-MACHINE BLOCKER, found in the 2026-08-10 audit. bin/nightly.sh runs
# ./.venv/bin/python explicitly on eight of its steps, two launchd jobs name that
# interpreter in their ProgramArguments, and NOTHING in this repo has ever
# created it — it exists on the primary machine only because it was made by hand
# long ago. On a fresh clone every one of those fails silently at its first fire.
# The gate selftests themselves are stdlib-only and would have passed, so the
# migration would have reported success on a machine whose scheduled work could
# not run. That is precisely the "reports success while half-migrated" outcome
# this script was written to prevent.
if [ -x "$REPO/.venv/bin/python" ]; then
  good "venv present"
elif [ $APPLY -eq 1 ]; then
  if python3 -m venv "$REPO/.venv" </dev/null >/tmp/mig-venv.log 2>&1; then
    good "venv created"
  else
    bad "venv creation failed — see /tmp/mig-venv.log"
  fi
else
  good "would create .venv"
fi

if [ -x "$REPO/.venv/bin/python" ]; then
  PY="$REPO/.venv/bin/python"
  if $PY -c "import psycopg, openpyxl, PIL" >/dev/null 2>&1; then
    good "python deps present"
  elif [ $APPLY -eq 1 ]; then
    # The only step that touches the network. Slowest part of the migration.
    if $PY -m pip install -q --disable-pip-version-check \
         -r "$REPO/requirements.txt" </dev/null >/tmp/mig-pip.log 2>&1; then
      good "requirements installed"
    else
      bad "pip install failed — see /tmp/mig-pip.log"
    fi
  else
    note "would install requirements.txt (needs network, ~1 min)"
  fi
fi

step "4. install gates"
if [ $APPLY -eq 1 ]; then
  if $PY "$REPO/ops/config-as-code.py" install --apply </dev/null >/tmp/mig-hooks.log 2>&1; then
    good "hooks installed (all other settings keys preserved)"
  else
    bad "hook install failed — see /tmp/mig-hooks.log"
  fi
else
  $PY "$REPO/ops/config-as-code.py" install </dev/null 2>&1 | sed 's/^/        /' | head -6
  good "would install the repo's hooks block"
fi

# ── 4. DERIVED ALLOWLIST ────────────────────────────────────────────────────
# Machine-local and gitignored, so it does not exist on a fresh clone. Without
# it the egress guard falls back to its code list — safe, but client practice
# sites stay unreachable until the nightly runs. Generate it now instead.
step "5. derived fetch allowlist"
if [ -f "$REPO/ops/fetch-allowlist.py" ]; then
  if [ $APPLY -eq 1 ]; then
    if $PY "$REPO/ops/fetch-allowlist.py" </dev/null >/tmp/mig-allow.log 2>&1; then
      good "allowlist generated"
    else
      note "allowlist generation failed (guard falls back to its code list — safe) — /tmp/mig-allow.log"
    fi
  else
    good "would generate the record-derived host allowlist"
  fi
else
  note "ops/fetch-allowlist.py not present — skipping"
fi

# ── 5. VERIFY ───────────────────────────────────────────────────────────────
# Every gate is exercised by spawning the REAL hook, never by importing a
# function (rule a9ecd5b4: a check compares the artifact against what it should
# be). A migration that installs gates without proving they fire has proved
# nothing.
step "6. verify"
run_check() {
  local label="$1" file="$2"
  [ -f "$REPO/$file" ] || { note "$label — not present, skipped"; return; }
  if $PY "$REPO/$file" </dev/null >/tmp/mig-check.log 2>&1; then
    good "$label — $(tail -1 /tmp/mig-check.log | tr -s ' ')"
  else
    bad "$label FAILED — $(tail -1 /tmp/mig-check.log | tr -s ' ')"
  fi
}
run_check "egress guard"     "ops/guard-selftest.py"
run_check "conduct gate"     "ops/conduct-gate-selftest.py"
run_check "escalation gate"  "ops/escalation-gate-selftest.py"
run_check "gate-edit gate"   "ops/gate-edit-gate-selftest.py"
run_check "git-writer gate"  "ops/git-writer-gate-selftest.py"
# Added after the two gates below shipped (2026-08-10), later than this script.
# Step 3 installs every gate in the repo's hooks block, so a verify list that
# lags the hooks block certifies a machine it has not fully checked.
run_check "context-handoff"  "ops/context-handoff-gate-selftest.py"
run_check "corpus-render"    "ops/corpus-render-gate-selftest.py"

if $PY "$REPO/ops/config-as-code.py" check </dev/null >/tmp/mig-drift.log 2>&1; then
  good "config matches the repo (no drift)"
else
  if [ $APPLY -eq 1 ]; then
    bad "config drift remains after install — see /tmp/mig-drift.log"
  else
    note "config drift (expected before --apply)"
  fi
fi

# ── 6. REPORT ───────────────────────────────────────────────────────────────
step "result"
say "  $ok ok · $warn note · $fail fail"
say ""
if [ $fail -gt 0 ]; then
  say "MIGRATION INCOMPLETE — $fail step(s) failed. Nothing is half-applied that"
  say "cannot be fixed by re-running; this script is idempotent."
  exit 1
fi
if [ $APPLY -eq 0 ]; then
  say "Dry run clean. Re-run with --apply to migrate."
  exit 0
fi

cat <<'NEXT'
MACHINE IS MIGRATED. The gates are live immediately — Claude Code re-reads its
hooks per tool call, so every session already running on this Mac is covered
without a restart (verified 2026-08-09).

THE SESSION'S HALF — the driving session does these, not this script, because
they are record writes and records go through verbs:

  1. Call standing-context. It should return the shared rules and Dell's
     inbound queue. If it answers, the doctrine store is live for him and the
     old markdown boot path is no longer needed.

  2. Close these inbound items for Dell, since this script just did them:
       - the repo-pull item (guard protections, host allowlist, egress rebuild)
       - the doctrine-store item (his sessions now boot from the store)
     Leave the three protocol-adoption items open — those need Dell to actually
     read and agree, which no script can do for him.

  3. Tell Dell what changed on his machine, in plain words, not item numbers.
NEXT
exit 0
