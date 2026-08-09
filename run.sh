#!/bin/zsh
# carr-system run entry point (phase 2, 2026-07-24).
# The ONE command SOPs call on Joe's Mac. Runs the repo generators against the vault.
#   ./run.sh deal-room     — rebuild the Deal Room HTML from the deal record (--files for the json)
#   ./run.sh lead-board    — rebuild the Lead Board HTML from the registry + feeds
#   ./run.sh renewal-feed  — rebuild renewal-radar.json from the newest radar xlsx
#   ./run.sh all           — all three, renewal-feed before lead-board (feed order matters)
#   ./run.sh review-queue  — rebuild the ONE review queue (out/review-queue/), a surface only
#   ./run.sh brief-pack    — the four brief sections as callable units (out/brief-pack/)
#   ./run.sh restore-rehearse — prove the encrypted backups actually restore (needs the age key)
# CARR_VAULT overrides the vault path (default: Joe's Drive mount).

set -eu
REPO="$(cd "$(dirname "$0")" && pwd)"
VAULT="${CARR_VAULT:-/Users/booko/Library/CloudStorage/GoogleDrive-joe.bookout.carr.us@gmail.com/My Drive/CARR AI}"

# ORDER 29a: the Deal Room and the graph pipeline read the RECORD by default
# (v_export_* through lib/record_sources.py), so they run on the repo venv — that
# is the interpreter with psycopg. Both still accept the file path and both fall
# back to the generated files, loudly, on any machine without the credential.
# Pass --files to either one to force the old read path.
PY="$REPO/.venv/bin/python"
[ -x "$PY" ] || PY=python3

deal_room()    { "$PY" "$REPO/generators/build-deal-room.py" \
                   "$VAULT/DNA/Deal Management/panhandle-team-deals.json" \
                   "$VAULT/DNA/Team/live-boards/deal-room-panhandle.html" "$@"; }
# ORDER 26(b): the board gained --files/--records. The DEFAULT IS STILL FILES and
# the chain still calls this with no arguments, so `run.sh all` is byte-for-byte
# what it was. Records mode is reachable by hand — and needs the repo venv (it
# speaks to Neon through psycopg) plus a DSN that can read candidate_pool, which
# `tools/db-tap.py run` supplies. Parity passed 2026-07-31; the default flip waits
# on a pool read surface the exporter credential can reach (parked, see the
# ORDER 26 execution log).
lead_board()   { "$PY" "$REPO/generators/build-lead-board.py" "$VAULT" "$@"; }
lead_promote() { shift; "$PY" "$REPO/pipelines/lead-promote.py" "$VAULT" "$@"; }  # ORDER 29b flip: venv, records-mode reads (parity byte-identical 2026-08-05)
renewal_feed() { "$PY" "$REPO/generators/build-renewal-feed.py" "$VAULT"; }  # ORDER 29b flip: venv, records-mode suppressor (parity byte-identical 2026-08-05)
corroborate()  { python3 "$REPO/pipelines/radar/corroborate.py" "$VAULT"; }
space_search() { python3 "$REPO/pipelines/build-space-search.py" "$2"; }
graph()        { "$PY" "$REPO/pipelines/build-graph-notes.py" "$VAULT" "$@" \
                 && "$PY" "$REPO/pipelines/build-graph-structure.py" "$VAULT" "$@"; }
# NOTE: build-graph-notes.py wipes and rebuilds Graph/, which deletes Graph/hubs.
# The hub pass MUST run after it, so `graph` always runs both.
graph_system() { python3 "$REPO/pipelines/build-system-graph.py" "$VAULT"; }
graph_health() { shift; "$PY" "$REPO/pipelines/graph-health.py" "$VAULT" "$@"; }
sf_diff()      { shift; "$PY" "$REPO/pipelines/diff-salesforce-deals.py" "$VAULT" "$@"; }  # ORDER 29b flip: venv, records-mode read (parity byte-identical 2026-08-05)
section_index(){ python3 "$REPO/pipelines/build-section-index.py" "$VAULT"; }
registry_audit(){ shift; CARR_VAULT="$VAULT" python3 "$REPO/tools/registry-audit.py" "$@"; }
# ORDER 16. Both are READ-ONLY surfaces: no database write, no send, no push.
# They run on the repo venv because they speak to Neon through psycopg.
review_queue() { shift; CARR_VAULT="$VAULT" "$REPO/.venv/bin/python" "$REPO/pipelines/review_queue.py" "$@"; }
brief_pack()   { shift; CARR_VAULT="$VAULT" "$REPO/.venv/bin/python" "$REPO/pipelines/brief_pack.py" "$@"; }
verify_emails(){ shift; python3 "$REPO/tools/verify-emails.py" --vault "$VAULT" "$@"; }
# The restore rehearsal (2026-08-02). Proves backups/*.sql.age can come BACK:
# throwaway Neon branch, decrypt, load, reconcile row counts, delete the branch.
# It writes NOTHING to production and needs the age PRIVATE key, which this repo
# does not carry — pass --identity or set CARR_AGE_IDENTITY. --preflight runs the
# checks alone and creates nothing.
restore_rehearse(){ shift; "$REPO/bin/restore-rehearse.sh" "$@"; }
# `call` (2026-08-08): fire ONE record verb from a terminal. Wraps
# mcp-server/local-verb.mjs, which runs the REAL src/tools.js registry, and feeds
# it the DSN from tools/db-tap.py so a human does not have to produce one by
# hand. Production is reachable for reads AND writes (Joe's ruling 2026-08-09,
# loop #258): a production write prints a warning naming the verb and the host,
# and is not blocked. Use --branch to rehearse against a Neon branch instead.
call_verb()    { shift; python3 "$REPO/tools/call-verb.py" "$@"; }

case "${1:-}" in
  deal-room)    shift; deal_room "$@" ;;
  lead-board)   shift; lead_board "$@" ;;
  lead-promote) lead_promote "$@" ;;
  renewal-feed) renewal_feed ;;
  all)          renewal_feed; lead_board; deal_room ;;
  corroborate)  corroborate ;;
  space-search) space_search "$@" ;;
  graph)        shift; graph "$@" ;;
  graph-system) graph_system ;;
  graph-health) graph_health "$@" ;;
  salesforce-diff) sf_diff "$@" ;;
  section-index) section_index ;;
  registry-audit) registry_audit "$@" ;;
  review-queue) review_queue "$@" ;;
  brief-pack)   brief_pack "$@" ;;
  verify-emails) verify_emails "$@" ;;
  restore-rehearse) restore_rehearse "$@" ;;
  call)         call_verb "$@" ;;
  # retrieve runs on the repo venv since the store pass (P4 dual-read) needs psycopg;
  # fails soft to file-index-only on any machine without it.
  retrieve)     shift; CARR_VAULT="$VAULT" "$PY" "$REPO/tools/retrieve.py" "$@" ;;
  health)       CARR_VAULT="$VAULT" python3 "$REPO/tools/health-check.py" ;;
  config)       shift; python3 "$REPO/ops/config-as-code.py" "$@" ;;
  lint)         shift; python3 "$REPO/tools/writing-lint.py" "$@" ;;
  migrate)      shift; "$REPO/.venv/bin/python" "$REPO/tools/migrate.py" "$@" ;;
  export)       shift; "$REPO/.venv/bin/python" -m exporters.run_exports "$@" ;;
  check)        "$REPO/tools/check.sh" ;;
  # The report-card rubric (loop #220, DRAFT). One entry point for both paths:
  # a human runs this, and the monthly audit runs this. Rule a8c55a47 — the
  # manual path and the automated path that do the same job must be the SAME
  # code, which under v1 they were not.
  report-card)  shift; CARR_VAULT="$VAULT" python3 "$REPO/tools/report-card.py" "$@" ;;
  *) echo "usage: run.sh deal-room [--files]|lead-board [--files|--records]|lead-promote [--count N] [--county X] [--segment X]|renewal-feed|all|corroborate|space-search <folder>|graph [--files]|graph-system|graph-health [--files] [--verbose]|salesforce-diff [--apply]|section-index|registry-audit [--verbose]|review-queue [--fixture f.json]|brief-pack [--section all|one-thing|prebriefs|capacity|monday-agenda|renewal-shortlist] [--quiet]|verify-emails [--source registry|vendors|roster] [--segment X] [--out f.csv]|retrieve <question>|health|config [check|pull|install] [--apply]|lint <file> [--surface email|social|proposal|web]|restore-rehearse [--preflight] [--verify-only] [--date YYYYMMDD] [--identity PATH] [--keep-branch]|migrate [--apply] [--yes]|export [--only <target>] [--bootstrap]|call [--branch <name>] <verb> '<json args>' [actor]|check|report-card [--validate|--run] [--skip-evidence]"; exit 2 ;;
esac
