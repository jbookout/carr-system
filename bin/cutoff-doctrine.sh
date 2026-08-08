#!/bin/zsh
# cutoff-doctrine.sh — THE CUTOFF (doctrine-store P5 final act; decisions
# 20dfdfcc + 82a2fb62 + Joe's rulings 14181e60/b42f11a0). One command ends the
# vault's markdown era: the exporter flag flips, every generated .md render
# MOVES to _to_delete staging (never deleted in place — rule faae6748), and
# the store becomes the only doctrine surface.
#
#   ./bin/cutoff-doctrine.sh              # prints the plan + the two gates
#   ./bin/cutoff-doctrine.sh --fire       # executes (ask Joe first)
#
# THE TWO GATES, human-verified before --fire (this script cannot check them):
#   1. Dell acknowledged action-required A17 (his reconnect done, his sessions
#      on standing-context) — the sync contract counts business days.
#   2. One live Monday cycle ran clean on the verb paths (heartbeat + Monday
#      brief with the store-first instructions, 2026-08-10 or later).
# Firing before the gates is Joe's explicit call to make, nobody else's.

set -eu
REPO="${0:A:h:h}"
VAULT="/Users/booko/Library/CloudStorage/GoogleDrive-joe.bookout.carr.us@gmail.com/My Drive/CARR AI"
STAGE="$VAULT/_to_delete/md-renders-cutoff-$(date +%Y%m%d)"
LOG="$REPO/out/cutoff.log"
mkdir -p "$REPO/out"
stamp() { print -r -- "$(date -u +%FT%TZ) cutoff $*" >> "$LOG" }

# The render list comes from the exporter registry via record-home-gate's
# parser — the same single source the write-gate trusts. .md only; xlsx/json/
# html surfaces survive the cutoff untouched.
renders=$("$REPO/.venv/bin/python" - <<'PY'
import importlib.util, os
spec = importlib.util.spec_from_file_location(
    "_rhg", os.path.expanduser("~/carr-system/hooks/record-home-gate.py"))
g = importlib.util.module_from_spec(spec); spec.loader.exec_module(g)
exact, _ = g.generated_paths()
for rel in sorted(exact):
    if rel.lower().endswith(".md"):
        print(rel)
PY
)
n=$(print -r -- "$renders" | grep -c . || true)

if [[ "${1:-}" != "--fire" ]]; then
  print "CUTOFF PLAN (dry — nothing changes):"
  print "  1. system_config doctrine.md_renders_retired = true (exporters skip all .md targets)"
  print "  2. MOVE $n generated .md renders → $STAGE"
  print "  3. run.sh health + a retrieve smoke afterward"
  print ""
  print -r -- "$renders" | sed 's/^/     /'
  print "\nGATES (verify by hand before --fire): Dell A17 acked · one clean Monday cycle on verbs"
  exit 0
fi

stamp "FIRE begin ($n md renders)"
mkdir -p "$STAGE"

printf "insert into system_config (key, value, note) values ('doctrine.md_renders_retired', '\"true\"', 'cutoff fired %s by cutoff-doctrine.sh') on conflict (key) do update set value = '\"true\"';" "$(date +%F)" > "$REPO/pipelines/_cutoff_flag.sql"
"$REPO/.venv/bin/python" "$REPO/tools/db-tap.py" sql "$REPO/pipelines/_cutoff_flag.sql"
rm "$REPO/pipelines/_cutoff_flag.sql"
stamp "flag set"

moved=0
print -r -- "$renders" | while IFS= read -r rel; do
  [[ -z "$rel" ]] && continue
  src="$VAULT/$rel"
  if [[ -f "$src" ]]; then
    mkdir -p "$STAGE/$(dirname "$rel")"
    mv "$src" "$STAGE/$rel"
    moved=$((moved+1))
  fi
done
stamp "renders staged to $STAGE"

# ZERO-FILE ruling (Joe, 2026-08-08): the bootstrap stubs retire too — the
# directive lives in the MCP server instructions, the Cowork project field,
# and the SessionStart hook. No CLAUDE.md, no AGENTS.md.
for stub in "CLAUDE.md" "AGENTS.md"; do
  if [[ -f "$VAULT/$stub" ]]; then
    mv "$VAULT/$stub" "$STAGE/$stub"
  fi
done
stamp "bootstrap stubs staged (zero-file ruling)"
print "REMINDER: commit the manifest cleanup (ALLOW_EXACT empties) + unregister"
print "the compiled-rules-gist-index target in the same sitting."

print "cutoff FIRED: flag set, renders staged. Now run:"
print "  cd ~/carr-system && ./run.sh health   # expect the doctrine rows green, render rows to note retirement"
print "  ./run.sh retrieve \"any question\"      # store-first, no dead file hits"
print "And Joe's 20-second proof: open a FRESH session anywhere and say"
print "  \"call standing-context and recite the rules\""
stamp "FIRE complete"
