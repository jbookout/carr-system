#!/bin/zsh
# sync-settings.sh — retired live-settings mirror.
#
# The repository now owns CARR's hook configuration.  Normal operation is
# repo-only: config-as-code checks/renders the managed hooks block without
# treating a synced settings file as a source of truth.  The old import remains
# available only as an explicit, reasoned NONCANONICAL recovery action.

set -eu
REPO="${0:A:h:h}"
DST="$REPO/claude-tree/settings"
LOG="$REPO/out/sync-settings.log"
mkdir -p "$REPO/out"
stamp() { print -r -- "$(date -u +%FT%TZ) sync-settings $*" >> "$LOG" }

if [[ "${1:-}" != "--noncanonical-recovery" ]]; then
  print "sync-settings: normal Drive mirror retired; repository config is canonical."
  print "sync-settings: use ops/config-as-code.py check for managed hook drift."
  stamp "NORMAL repo-only; no synced settings source consulted"
  exit 0
fi

shift
reason=""
vault=""
while (( $# )); do
  case "$1" in
    --reason) reason="${2:-}"; shift 2 ;;
    --vault) vault="${2:-}"; shift 2 ;;
    *) print -ru2 -- "sync-settings: unknown recovery argument: $1"; exit 2 ;;
  esac
done
if [[ -z "$reason" || -z "$vault" ]]; then
  print -ru2 -- "sync-settings: NONCANONICAL recovery requires --reason and explicit --vault"
  exit 2
fi
if [[ ! -d "$vault" ]]; then
  print -ru2 -- "sync-settings: NONCANONICAL recovery vault is not a directory: $vault"
  exit 2
fi
mkdir -p "$DST"

# The vault is an operator-selected recovery source, never an ambient default.
typeset -a sources
sources=(
  "carr-ai-project:$vault/.claude/settings.json"
  "my-drive-root:${vault:h}/.claude/settings.json"
)
typeset -a recovered
for entry in $sources; do
  label="${entry%%:*}"
  src="${entry#*:}"
  if [[ -f "$src" ]]; then
    cp "$src" "$DST/$label.settings.json"
    recovered+=("$label")
  fi
done
stamp "NONCANONICAL recovery reason=$reason recovered=${recovered:-none}"
print "sync-settings: NONCANONICAL recovery mirrored ${#recovered} file(s); review and commit deliberately."
