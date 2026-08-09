#!/bin/zsh
# harden-gates.sh — THE ACTUAL FIX for "the gates live in a repo I can edit".
#
# Joe, 2026-08-09: "the gates live in a repo i can edit — so what are you
# suggesting is the solution to that problem?"
#
# THE ANSWER: take write access away from the account the sessions run as.
# Everything else — hash baselines, git traces, drift detectors — is friction
# and paper trail. This is the only step that turns "the session agreed not to
# edit the gate" into "the session cannot edit the gate."
#
# WHY IT NEEDS JOE. It uses sudo, which is one of exactly three entries on this
# machine's Bash deny list (sudo, diskutil, dd) and needs a password only he
# holds. That is not an inconvenience, it IS the mechanism: if a session could
# run this, a session could run the undo, and the boundary would be decorative.
# Rule e313a3ca names sudo as one of the real hand-overs for precisely this
# reason.
#
# WHAT IT DOES
#   1. chown root:wheel + chmod 644 every gate script in hooks/.
#      The session keeps READ (the hook still runs — Claude Code executes it via
#      `python3 <path>`, which needs read, not write). It loses WRITE.
#   2. chown root:wheel + chmod 644 ~/.claude/settings.json.
#      Hardening the scripts alone is not enough: a session that cannot edit
#      conduct-stop-gate.py can still edit settings.json to stop CALLING it.
#      Both halves or neither.
#   3. Leaves ops/config/hooks.json (the repo source) WRITABLE on purpose.
#      Gate changes should still be authorable and reviewable in git; what
#      changes is that DEPLOYING one now requires Joe. Propose in the repo,
#      deploy with a password.
#
# THE COST, STATED HONESTLY. After this runs, a session can no longer install a
# gate change on its own — including a fix. `config-as-code.py install --apply`
# will fail with EACCES until you re-run this script, which is the correct
# trade: deploying enforcement is a boundary change, and rule aa411351's
# constitutional carve-out already says widening the system's authority is
# yours. Narrowing it stays free.
#
# WHAT THIS DOES NOT PROTECT AGAINST. A session can still ask you to run
# something that undoes it. That is why the escalation gate treats "weaken a
# gate / widen permissions / edit hooks" as a class that must reach you rather
# than being decided internally. The OS stops the silent path; the gate stops
# the quiet-request path; nothing stops you deciding to undo it deliberately,
# and nothing should.
#
#   sudo ops/harden-gates.sh          # apply
#   sudo ops/harden-gates.sh --undo   # give write back (for a gate rebuild)
#        ops/harden-gates.sh --status # no sudo needed; just reports
set -u

REPO="$(cd "$(dirname "$0")/.." && pwd)"
HOOKS="$REPO/hooks"
SETTINGS="${HOME}/.claude/settings.json"
[ -n "${SUDO_USER:-}" ] && SETTINGS="$(eval echo ~$SUDO_USER)/.claude/settings.json"

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
  "$SETTINGS"
  # THE OTHER TWO SETTINGS FILES. Found 2026-08-09, after hardening only the
  # global one: Claude Code reads FOUR settings files, and the two project-level
  # ones under My Drive carry the SessionStart hook that boots the standing
  # context (session-brief.py) and now the gate-integrity alarm. Both were still
  # session-writable, so the boot directive and the alarm could be unwired even
  # while every hook SCRIPT was immutable. Hooks from multiple files COMBINE
  # rather than override, so these cannot switch off the global gates — but a
  # session could still silence its own startup brief, which is how a session
  # stops knowing what binds it.
  # settings.local.json is deliberately NOT hardened: Claude Code writes
  # per-project permission grants there, and freezing it breaks ordinary work
  # for no enforcement gain (it carries no hooks).
  "/Users/booko/My Drive/CARR AI/.claude/settings.json"
  "/Users/booko/My Drive/.claude/settings.json"
)

status() {
  print -r -- "gate hardening status:"
  local hard=0 soft=0 gone=0
  for f in "${FILES[@]}"; do
    if [ ! -e "$f" ]; then
      printf "  %-24s MISSING\n" "$(basename "$f")"; gone=$((gone+1)); continue
    fi
    # NOT `local o m` then assign: under zsh that echoes the assignments into
    # the report, so the status output printed "o=booko / m=755" after every
    # line. A status command whose own output is noisy is a status command
    # people stop reading.
    o="$(stat -f '%Su' "$f")"
    m="$(stat -f '%Lp' "$f")"
    fl="$(stat -f '%Sf' "$f")"
    case "$fl" in (*uchg*) imm=yes ;; (*) imm=no ;; esac
    if [ "$o" = "root" ] && [ $(( 8#$m & 8#022 )) -eq 0 ] && [ "$imm" = yes ]; then
      printf "  %-24s HARDENED   (root, %s, uchg)\n" "$(basename "$f")" "$m"; hard=$((hard+1))
    elif [ "$o" = "root" ] && [ "$imm" = no ]; then
      printf "  %-24s PARTIAL    (root, %s, NO uchg — can be renamed over)\n" "$(basename "$f")" "$m"; soft=$((soft+1))
    else
      printf "  %-24s writable   (%s, %s)\n" "$(basename "$f")" "$o" "$m"; soft=$((soft+1))
    fi
  done
  print -r -- ""
  print -r -- "  $hard hardened · $soft still session-writable · $gone missing"
  [ $soft -gt 0 ] && print -r -- "  Until soft==0, gate tampering is DETECTABLE (gate-integrity.py) but NOT PREVENTED."
  [ $soft -eq 0 ] && [ $gone -eq 0 ] && print -r -- "  Gates are OS-enforced. A session cannot edit them without your password."
}

if [ "${1:-}" = "--status" ]; then status; exit 0; fi

if [ "$(id -u)" -ne 0 ]; then
  print -r -- "harden-gates: must run under sudo (that is the point — see the header)." >&2
  print -r -- "Current state:" >&2
  status
  exit 2
fi

if [ "${1:-}" = "--undo" ]; then
  me="${SUDO_USER:-$USER}"
  for f in "${FILES[@]}"; do
    [ -e "$f" ] || continue
    # nouchg FIRST — with uchg set, chown and chmod both fail with EPERM even
    # as root, so clearing the flag has to lead.
    chflags nouchg "$f" 2>/dev/null
    chown "$me":staff "$f" && chmod 644 "$f" && print -r -- "  unhardened $(basename "$f")"
  done
  print -r -- "harden-gates: write access RETURNED to $me. Re-run without --undo when the rebuild is done."
  exit 0
fi

changed=0
for f in "${FILES[@]}"; do
  if [ ! -e "$f" ]; then
    print -r -- "  SKIP $(basename "$f") — not present"; continue
  fi
  chown root:wheel "$f" || { print -r -- "  FAILED chown $f" >&2; continue; }
  chmod 644 "$f"        || { print -r -- "  FAILED chmod $f" >&2; continue; }
  # chown ALONE IS NOT ENOUGH, and this was proved the hard way on 2026-08-09.
  # A root-owned 644 file in a USER-WRITABLE DIRECTORY can still be replaced:
  # rename(2) and unlink(2) check the DIRECTORY's permission bits, not the
  # file's. A test that did exactly that (`shutil.move(tmp, settings.json)`)
  # succeeded and truncated the live settings to 0 bytes while the file was
  # root-owned — the hardening looked correct and stopped nothing.
  #
  # `chflags uchg` is the fix: the immutable flag blocks write, rename AND
  # unlink on the file itself regardless of directory permissions. Clearing it
  # needs `chflags nouchg`, which only the file's owner (now root) can do — so
  # a session running as booko can neither edit, replace, nor unlock it.
  #
  # Deliberately uchg and NOT schg: system-immutable survives even root outside
  # single-user mode, which would make --undo impossible and strand Joe.
  chflags uchg "$f"     || { print -r -- "  WARN  chflags failed on $f (replaceable!)" >&2; }
  print -r -- "  hardened $(basename "$f")"
  changed=$((changed+1))
done

print -r -- ""
print -r -- "harden-gates: $changed file(s) now root-owned and session-read-only."
print -r -- "The session can still RUN every gate; it can no longer REWRITE one."
print -r -- ""
status
