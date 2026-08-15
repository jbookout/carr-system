#!/bin/zsh
# harden-gates.sh — the OS-level answer to "the gates live in a repo I can edit".
#
# Joe, 2026-08-09: "the gates live in a repo i can edit — so what are you
# suggesting is the solution to that problem?"
#
# Both council chairs named this the decisive hole, unprompted. Grok: "without
# it every gate is temporary cosplay." Codex: "the agent may be the operator,
# but it cannot also be the root authority that decides whether its own actions
# were allowed." Hash baselines and git traces make tampering DETECTABLE; only
# this makes it IMPOSSIBLE.
#
# ─────────────────────────────────────────────────────────────────────────────
# THIS FILE WAS DELETED ONCE ALREADY, 2026-08-09, AND THAT IS THE PROOF IT WORKS
# ─────────────────────────────────────────────────────────────────────────────
# Within an hour of the first hardening run, a CONCURRENT SESSION wiped every
# unhardened file this build had produced — this script, bin/council*.sh,
# bin/precheck.sh, both selftests, ops/vault-duplicate-sweep.py. Every file that
# had been hardened (all of hooks/, plus the four settings.json files) SURVIVED
# UNTOUCHED. Same disk, same user, same moment: the protected set lived and the
# unprotected set did not. Rule 308ef1de already warned that ~/carr-system holds
# other live sessions' work; this is that hazard with teeth.
# TWO LESSONS, both now enforced rather than remembered:
#   1. COMMIT AS YOU BUILD. Nothing deleted here had ever been committed, so
#      there was nothing to restore from. Hours of work, no undo.
#   2. Anything that ENFORCES belongs in the hardened set, not merely in git.
#
# ─────────────────────────────────────────────────────────────────────────────
# WHY chown ALONE IS NOT ENOUGH — measured, not theorised
# ─────────────────────────────────────────────────────────────────────────────
# The first version chowned to root:wheel 644 and reported HARDENED. A test then
# replaced the live settings.json with `shutil.move(tmp, settings.json)` and it
# SUCCEEDED, truncating it to 0 bytes while root-owned. rename(2) and unlink(2)
# check the DIRECTORY's permission bits, not the file's, and ~/.claude is
# user-writable. The status output said HARDENED throughout: a false green.
#
# `chflags uchg` closes it. The immutable flag blocks write, rename AND unlink on
# the file itself regardless of directory permissions, and clearing it requires
# being the file's owner — now root. Verified by re-running the full attack set:
# append, truncate, rename-over, unlink and chflags-nouchg all refused.
# Deliberately uchg and NOT schg: system-immutable survives even root outside
# single-user mode, which would make --undo impossible and strand Joe.
#
# WHY IT NEEDS JOE. sudo is one of exactly three entries on this machine's deny
# list (sudo, diskutil, dd) and needs a password only he holds. That is not
# friction, it IS the mechanism: a session that could run this could run --undo,
# and the boundary would be decorative. Rule e313a3ca names sudo as a real
# hand-over for exactly this reason. In practice Joe authenticates a macOS admin
# prompt (osascript "with administrator privileges"), so the password stays his
# physical act and no session ever holds sudo.
#
# THE COST, STATED PLAINLY. After this runs, "always allow" permission grants,
# theme changes and plugin installs all FAIL, because Claude Code writes
# settings.json to do them. config-as-code.py install --apply fails EACCES too.
# The fix each time is --undo, change, re-harden. Judged worth it: those are
# rare, and the alternative is leaving the wiring editable by the thing it
# constrains.
#
#   sudo ops/harden-gates.sh          # apply
#   sudo ops/harden-gates.sh --undo   # return write access for a rebuild
#        ops/harden-gates.sh --status # no sudo needed; just reports
set -u

REPO="$(cd "$(dirname "$0")/.." && pwd)"
HOOKS="$REPO/hooks"
HOME_DIR="${HOME}"
[ -n "${SUDO_USER:-}" ] && HOME_DIR="$(eval echo ~$SUDO_USER)"

# Claude Code reads FOUR settings files. Hardening only the global one left the
# two under My Drive — which carry the SessionStart hooks that boot the standing
# context and the gate-integrity alarm — still session-writable, so a session
# could silence its own startup brief while every gate script sat immutable.
# settings.local.json is deliberately EXCLUDED: Claude Code writes per-project
# permission grants there and it carries no hooks, so freezing it breaks
# ordinary work for no enforcement gain.
FILES=(
  "$HOOKS/guard-unattended.py"
  "$HOOKS/record-home-gate.py"
  "$HOOKS/rule-shape-gate.py"
  "$HOOKS/lint-gate.py"
  "$HOOKS/ledger-sweep.py"
  "$HOOKS/conduct-stop-gate.py"
  "$HOOKS/escalation-gate.py"
  "$HOOKS/conduct_patterns.py"
  "$HOOKS/gate-integrity.py"
  "$HOOKS/session-brief.py"
  "$HOOKS/delegation-gate.py"
  "$HOOKS/completion-evidence-gate.py"
  "$HOOKS/map-architecture-gate.py"
  "$HOME_DIR/.claude/settings.json"
  "$HOME_DIR/My Drive/CARR AI/.claude/settings.json"
  "$HOME_DIR/My Drive/.claude/settings.json"
  "$REPO/ops/config/delegation-gate-hook.json"
  "$REPO/ops/config/codex-hooks.json"
  "$REPO/ops/config/rule-enforcement-map.json"
)

status() {
  print -r -- "gate hardening status:"
  hard=0; soft=0; gone=0
  for f in "${FILES[@]}"; do
    if [ ! -e "$f" ]; then
      printf "  %-24s MISSING\n" "$(basename "$f")"; gone=$((gone+1)); continue
    fi
    # NOT `local o m` then assign — under zsh that echoes the assignments into
    # the report, which made an earlier version print "o=booko / m=755" after
    # every line. A status command with noisy output stops being read.
    o="$(stat -f '%Su' "$f")"
    m="$(stat -f '%Lp' "$f")"
    fl="$(stat -f '%Sf' "$f")"
    case "$fl" in (*uchg*) imm=yes ;; (*) imm=no ;; esac
    if [ "$o" = "root" ] && [ $(( 8#$m & 8#022 )) -eq 0 ] && [ "$imm" = yes ]; then
      printf "  %-24s HARDENED   (root, %s, uchg)\n" "$(basename "$f")" "$m"; hard=$((hard+1))
    elif [ "$o" = "root" ] && [ "$imm" = no ]; then
      # The false green that hid the hole. Never report this as hardened.
      printf "  %-24s PARTIAL    (root, %s, NO uchg — can be renamed over)\n" "$(basename "$f")" "$m"; soft=$((soft+1))
    else
      printf "  %-24s writable   (%s, %s)\n" "$(basename "$f")" "$o" "$m"; soft=$((soft+1))
    fi
  done
  print -r -- ""
  print -r -- "  $hard hardened · $soft unprotected · $gone missing"
  [ $soft -gt 0 ] && print -r -- "  Until unprotected==0, tampering is DETECTABLE (gate-integrity.py) but NOT PREVENTED."
  [ $soft -eq 0 ] && [ $gone -eq 0 ] && print -r -- "  Gates are OS-enforced. A session cannot edit them without your password."
}

if [ "${1:-}" = "--status" ]; then status; exit 0; fi

if [ "$(id -u)" -ne 0 ]; then
  print -r -- "harden-gates: must run under sudo (that is the point — see the header)." >&2
  status >&2
  exit 2
fi

if [ "${1:-}" = "--undo" ]; then
  me="${SUDO_USER:-$USER}"
  for f in "${FILES[@]}"; do
    [ -e "$f" ] || continue
    # nouchg FIRST — with uchg set, chown and chmod both fail EPERM even as root.
    chflags nouchg "$f" 2>/dev/null
    chown "$me":staff "$f" && chmod 644 "$f" && print -r -- "  unhardened $(basename "$f")"
  done
  print -r -- "harden-gates: write access RETURNED to $me. Re-run without --undo when done."
  exit 0
fi

changed=0
for f in "${FILES[@]}"; do
  if [ ! -e "$f" ]; then print -r -- "  SKIP $(basename "$f") — not present"; continue; fi
  chflags nouchg "$f" 2>/dev/null          # idempotent re-apply
  chown root:wheel "$f" || { print -r -- "  FAILED chown $f" >&2; continue; }
  chmod 644 "$f"        || { print -r -- "  FAILED chmod $f" >&2; continue; }
  chflags uchg "$f"     || print -r -- "  WARN  chflags failed on $f (still replaceable!)" >&2
  print -r -- "  hardened $(basename "$f")"
  changed=$((changed+1))
done

print -r -- ""
print -r -- "harden-gates: $changed file(s) root-owned + immutable."
print -r -- "The session can still RUN every gate; it can no longer REWRITE or REPLACE one."
print -r -- ""
status
