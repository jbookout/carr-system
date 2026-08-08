#!/bin/zsh
# sync-skills.sh — skills are CODE (council verdict F4, decision 82a2fb62):
# canonical in the repo (carr-system/claude-tree/), synced to the Drive tree
# that local Claude Code and Cowork sessions actually load from. The Drive
# copy becomes a GENERATED projection — same law as every render: edit in the
# repo, sync, never hand-edit the projection.
#
#   ./bin/sync-skills.sh            # drift report only (hash compare)
#   ./bin/sync-skills.sh --apply    # repo → Drive, exact mirror
#
# Drift in the REPO direction (Drive file newer/different than repo) is
# REPORTED AND NEVER auto-pulled: a hand edit on the Drive side after canon
# moved is the failure this script exists to catch, not to absorb silently.
# (Bootstrap exception: until the first --apply, Drive IS the source; the
# initial repo copy was taken 2026-08-08.)

set -eu
REPO="${0:A:h:h}"
SRC="$REPO/claude-tree"
DST="/Users/booko/My Drive/.claude"
LOG="$REPO/out/sync-skills.log"
mkdir -p "$REPO/out"
stamp() { print -r -- "$(date -u +%FT%TZ) sync-skills $*" >> "$LOG" }

drift=$(rsync -rcn --delete --out-format='%n' \
        "$SRC/skills/" "$DST/skills/" 2>/dev/null; \
        rsync -rcn --delete --out-format='%n' \
        "$SRC/agents/" "$DST/agents/" 2>/dev/null)

if [[ -z "$drift" ]]; then
  print "sync-skills: repo and Drive trees identical (34-file mirror clean)"
  stamp "OK no drift"
  exit 0
fi

print "sync-skills: DRIFT between repo canon and Drive projection:"
print -r -- "$drift" | sed 's/^/  /'

if [[ "${1:-}" == "--apply" ]]; then
  rsync -rc --delete "$SRC/skills/" "$DST/skills/"
  rsync -rc --delete "$SRC/agents/" "$DST/agents/"
  stamp "APPLIED ${#${(f)drift}} path(s)"
  print "applied: repo → Drive, exact mirror"
else
  stamp "DRIFT reported, not applied: ${#${(f)drift}} path(s)"
  print "\nIf the repo side is right (it should be — it is canon): ./bin/sync-skills.sh --apply"
  print "If the DRIFT is a hand edit on Drive that must be kept: copy it into"
  print "$SRC first, commit, then --apply — never let the projection lead."
  exit 1
fi
