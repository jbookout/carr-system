#!/bin/zsh
# carr-system run entry point (phase 2, 2026-07-24).
# The ONE command SOPs call on Joe's Mac. Runs the repo generators against the vault.
#   ./run.sh deal-room     — rebuild the Deal Room HTML from panhandle-team-deals.json
#   ./run.sh lead-board    — rebuild the Lead Board HTML from the registry + feeds
#   ./run.sh renewal-feed  — rebuild renewal-radar.json from the newest radar xlsx
#   ./run.sh all           — all three, renewal-feed before lead-board (feed order matters)
# CARR_VAULT overrides the vault path (default: Joe's Drive mount).

set -eu
REPO="$(cd "$(dirname "$0")" && pwd)"
VAULT="${CARR_VAULT:-/Users/booko/Library/CloudStorage/GoogleDrive-joe.bookout.carr.us@gmail.com/My Drive/CARR AI}"

deal_room()    { python3 "$REPO/generators/build-deal-room.py" \
                   "$VAULT/DNA/Deal Management/panhandle-team-deals.json" \
                   "$VAULT/DNA/Team/live-boards/deal-room-panhandle.html"; }
lead_board()   { python3 "$REPO/generators/build-lead-board.py" "$VAULT"; }
renewal_feed() { python3 "$REPO/generators/build-renewal-feed.py" "$VAULT"; }
corroborate()  { python3 "$REPO/pipelines/radar/corroborate.py" "$VAULT"; }
space_search() { python3 "$REPO/pipelines/build-space-search.py" "$2"; }
graph()        { python3 "$REPO/pipelines/build-graph-notes.py" "$VAULT"; }
sf_diff()      { shift; python3 "$REPO/pipelines/diff-salesforce-deals.py" "$VAULT" "$@"; }
section_index(){ python3 "$REPO/pipelines/build-section-index.py" "$VAULT"; }

case "${1:-}" in
  deal-room)    deal_room ;;
  lead-board)   lead_board ;;
  renewal-feed) renewal_feed ;;
  all)          renewal_feed; lead_board; deal_room ;;
  corroborate)  corroborate ;;
  space-search) space_search "$@" ;;
  graph)        graph ;;
  salesforce-diff) sf_diff "$@" ;;
  section-index) section_index ;;
  retrieve)     shift; CARR_VAULT="$VAULT" python3 "$REPO/tools/retrieve.py" "$@" ;;
  health)       CARR_VAULT="$VAULT" python3 "$REPO/tools/health-check.py" ;;
  check)        "$REPO/tools/check.sh" ;;
  *) echo "usage: run.sh deal-room|lead-board|renewal-feed|all|corroborate|space-search <folder>|graph|salesforce-diff [--apply]|section-index|retrieve <question>|health|check"; exit 2 ;;
esac
