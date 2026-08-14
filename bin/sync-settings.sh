#!/bin/zsh
# sync-settings.sh — the Claude Code PERMISSION SURFACE under version control.
#
# WHY THIS EXISTS. On 2026-08-08 a plugin install silently deleted the entire
# hooks block from a settings.json and all five gates of the day were off until
# somebody noticed by accident. Nothing would have shown a diff, because these
# files live in Google Drive and were tracked nowhere. This script mirrors them
# into the repo so a silent change has a diff, a history, and a blame.
#
#   ./bin/sync-settings.sh          # drift report only (exit 1 on drift)
#   ./bin/sync-settings.sh --apply  # Drive -> repo, then commit the mirror
#
# DIRECTION IS DELIBERATE AND IT IS THE OPPOSITE OF sync-skills.sh. There, the
# repo is canon and Drive is a generated projection. HERE THE VAULT IS CANON and
# the repo holds a mirror, because Claude Code itself writes to these files —
# an interactive permission approval lands in the vault copy with no session in
# the loop. A repo-canonical settings file would let an --apply clobber an
# approval Joe just granted. The mirror is for history and tamper-detection; it
# is never pushed back over the live file.
#
# So: drift here is NORMAL (it means the live file changed) and the response is
# to LOOK at the diff before mirroring it, not to assume the vault is wrong.

set -eu
REPO="${0:A:h:h}"
DST="$REPO/claude-tree/settings"
LOG="$REPO/out/sync-settings.log"
mkdir -p "$DST" "$REPO/out"
stamp() { print -r -- "$(date -u +%FT%TZ) sync-settings $*" >> "$LOG" }

# label:path — label becomes the mirrored filename, so two settings.json files
# from different trees never collide in one flat folder.
SOURCES=(
  "carr-ai-project:/Users/booko/My Drive/CARR AI/.claude/settings.json"
  "my-drive-root:/Users/booko/My Drive/.claude/settings.json"
  "user:/Users/booko/.claude/settings.json"
)

drift=()
missing=()
for entry in $SOURCES; do
  label="${entry%%:*}"
  src="${entry#*:}"
  mirror="$DST/$label.settings.json"
  if [[ ! -f "$src" ]]; then
    missing+=("$label ($src)")
    continue
  fi
  if [[ ! -f "$mirror" ]] || ! cmp -s "$src" "$mirror"; then
    drift+=("$label")
  fi
done

(( ${#missing} )) && for m in $missing; do print "sync-settings: NOT PRESENT — $m"; done

if (( ${#drift} == 0 )); then
  print "sync-settings: all ${#SOURCES} settings file(s) match their repo mirror"
  stamp "OK no drift"
  exit 0
fi

print "sync-settings: DRIFT — the live file differs from the repo mirror:"
for d in $drift; do
  print "  $d"
  src=""
  for entry in $SOURCES; do [[ "${entry%%:*}" == "$d" ]] && src="${entry#*:}"; done
  diff -u "$DST/$d.settings.json" "$src" 2>/dev/null | sed 's/^/    /' | head -40
done

if [[ "${1:-}" == "--apply" ]]; then
  for d in $drift; do
    for entry in $SOURCES; do
      [[ "${entry%%:*}" == "$d" ]] && cp "${entry#*:}" "$DST/$d.settings.json"
    done
  done
  stamp "APPLIED ${#drift} file(s): ${drift}"
  print "\nmirrored: Drive -> repo. Commit claude-tree/settings/ to keep the history."
else
  stamp "DRIFT reported, not applied: ${#drift} file(s): ${drift}"
  print "\nREAD THE DIFF FIRST. A permission or hook that vanished is the thing this"
  print "script was built to catch — mirroring it would file the loss as history"
  print "rather than flag it. If the change is legitimate: ./bin/sync-settings.sh --apply"
  exit 1
fi
