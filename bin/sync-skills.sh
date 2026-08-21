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
#
# THE REPORT NAMES DELETIONS SEPARATELY, AND THAT IS NOT COSMETIC (2026-08-21).
# It used to build one list from `rsync -rcn --delete --out-format='%n'`, which
# emits the paths the apply would ADD and is silent about the paths it would
# DELETE. Since the apply is `rsync -rc --delete`, an exact mirror, a file that
# existed only on the Drive side got removed without ever appearing in the
# report that exists to warn you first. That is not hypothetical: the
# `surface-review` skill was hand-authored on Drive on 2026-08-13, sat
# unmentioned in every drift listing for eight days, and was one --apply from
# being destroyed. A warning surface that is blind in the destructive direction
# is worse than none, because it gets consulted and believed. Deletions now get
# their own block, their own wording, and the same exit code.
# Regression suite: ops/sync-skills-drift-selftest.py.

set -eu
REPO="${0:A:h:h}"
# Both sides are overridable so the regression suite can exercise the REAL
# script against throwaway trees. Nothing but a test ever sets these, and the
# defaults are the only paths production uses.
SRC="${CARR_SKILLS_SRC:-$REPO/claude-tree}"
DST="${CARR_SKILLS_DST:-/Users/booko/My Drive/.claude}"
LOG="$REPO/out/sync-skills.log"
mkdir -p "$REPO/out"
stamp() { print -r -- "$(date -u +%FT%TZ) sync-skills $*" >> "$LOG" }

# ADDED OR CHANGED: what the apply would write. No --delete here on purpose, so
# this list carries only the non-destructive half.
incoming=$(rsync -rcn --out-format='%n' "$SRC/skills/" "$DST/skills/" 2>/dev/null; \
           rsync -rcn --out-format='%n' "$SRC/agents/" "$DST/agents/" 2>/dev/null)

# DELETED: what the apply would destroy. --itemize-changes is the only rsync
# output that names these; --out-format='%n' does not.
removing=$(rsync -rcn --delete --itemize-changes "$SRC/skills/" "$DST/skills/" 2>/dev/null; \
           rsync -rcn --delete --itemize-changes "$SRC/agents/" "$DST/agents/" 2>/dev/null)
removing=$(print -r -- "$removing" | grep '^\*deleting ' | sed 's/^\*deleting //' || true)

mirrored=$(find "$SRC/skills" "$SRC/agents" -type f 2>/dev/null | wc -l | tr -d ' ')

if [[ -z "$incoming" && -z "$removing" ]]; then
  print "sync-skills: repo and Drive trees identical (${mirrored}-file mirror clean)"
  stamp "OK no drift"
  exit 0
fi

print "sync-skills: DRIFT between repo canon and Drive projection:"
if [[ -n "$incoming" ]]; then
  print "  ADDED or CHANGED on Drive by an --apply:"
  print -r -- "$incoming" | sed 's/^/    /'
fi
if [[ -n "$removing" ]]; then
  print "  DELETED from Drive by an --apply — these exist ONLY in the projection"
  print "  and are in no commit anywhere. An --apply DESTROYS them:"
  print -r -- "$removing" | sed 's/^/    /'
fi

if [[ "${1:-}" == "--apply" ]]; then
  rsync -rc --delete "$SRC/skills/" "$DST/skills/"
  rsync -rc --delete "$SRC/agents/" "$DST/agents/"
  stamp "APPLIED ${#${(f)incoming}} written, ${#${(f)removing}} deleted"
  print "applied: repo → Drive, exact mirror"
else
  stamp "DRIFT reported, not applied: ${#${(f)incoming}} incoming, ${#${(f)removing}} to delete"
  if [[ -n "$removing" ]]; then
    print "\nSOMETHING WOULD BE DESTROYED. Rescue it into canon FIRST: copy it into"
    print "$SRC, commit, then --apply — never let the projection lead."
    print "Only once the deletion list above is empty is --apply a safe mirror."
  else
    print "\nIf the repo side is right (it should be — it is canon): ./bin/sync-skills.sh --apply"
    print "If the DRIFT is a hand edit on Drive that must be kept: copy it into"
    print "$SRC first, commit, then --apply — never let the projection lead."
  fi
  exit 1
fi
