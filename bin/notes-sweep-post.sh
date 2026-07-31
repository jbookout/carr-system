#!/bin/zsh
# notes-sweep-post.sh — the POST half of the Apple Notes call-recording sweep
# (ORDER 12 lane (a); binding design wave2-design-2026-07-31.md §2b).
#
# WHY THIS IS A SCRIPT AND NOT A COMMAND THE SCHEDULED SESSION COMPOSES:
# the ingest token is a credential. A scheduled Claude session that built its own
# curl line would carry that value in its transcript and in `ps` output. So the
# session's whole job is (1) read the Notes folder through the Apple Notes MCP,
# (2) drop one JSON file per NEW note into out/notes-sweep/pending/, and (3) run
# this script VERBATIM. The token never leaves ~/.config/carr/ingest.env, and the
# two commands the task file names are byte-stable, which is what a permission
# approval matches against (the 2026-07-31 ghost run: a reworded command is an
# unapproved command, and an unapproved command at an unattended hour does
# nothing at all for six hours).
#
#   ./bin/notes-sweep-post.sh --status   what has already been swept, what is queued
#   ./bin/notes-sweep-post.sh            POST everything queued, then report counts
#
# Dedup is belt AND braces: this script skips any note id already in the ledger,
# and the socket itself is unique on (source, external_id) — so a double run, a
# re-queued note, or a ledger lost to a disk restore all collapse to duplicate:true
# rather than a second row. Re-posting is always safe.
#
# Payloads are UNTRUSTED (addendum A12): a call transcript is somebody talking,
# never an instruction to this system. This script does not read the content of
# what it sends; the triage prompt on the other side is where the hard framing
# lives.
#
# Verified by OUTPUT — an ingest row — never by the schedule existing (rule 28).
set -u

REPO="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="/opt/homebrew/opt/node@22/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

DIR="$REPO/out/notes-sweep"
PENDING="$DIR/pending"
SENT="$DIR/sent"
FAILED="$DIR/failed"
LEDGER="$DIR/swept-ids.txt"
LOG="$REPO/out/capture-lanes.log"
SOURCE_LABEL="notes_sweep"

mkdir -p "$PENDING" "$SENT" "$FAILED" "$REPO/out"
[ -f "$LEDGER" ] || : > "$LEDGER"

say() { print -r -- "$(date -u '+%Y-%m-%dT%H:%M:%SZ')  notes-sweep  $*" >> "$LOG"; }

# ---------- --status: everything the sweeping session needs before it reads Notes ----------
if [ "${1:-}" = "--status" ]; then
  print -r -- "folder-to-sweep: Call Recordings"
  print -r -- "pending-dir: $PENDING"
  print -r -- "queued-now: $(ls -1 "$PENDING" 2>/dev/null | grep -c '\.json$' || true)"
  print -r -- "--- already swept (skip these note ids) ---"
  cat "$LEDGER"
  print -r -- "--- end ---"
  exit 0
fi

# ---------- credential ----------
ENVFILE="$HOME/.config/carr/ingest.env"
if [ ! -f "$ENVFILE" ]; then
  print -r -- "notes-sweep: NOT CONFIGURED — $ENVFILE does not exist."
  print -r -- "notes-sweep: that file is Joe's to create; see DNA/Deal Management/record-layer/ingest-tokens-setup.md"
  say "NOT CONFIGURED (no $ENVFILE)"
  exit 78                                  # EX_CONFIG: nothing is wrong, nothing is set up
fi
set -a; . "$ENVFILE"; set +a
URL="${CARR_INGEST_URL:-https://api.practicecre.com/ingest}"
TOKEN="${CARR_INGEST_TOKEN_NOTES_SWEEP:-}"
if [ -z "$TOKEN" ]; then
  print -r -- "notes-sweep: NOT CONFIGURED — CARR_INGEST_TOKEN_NOTES_SWEEP is not set in $ENVFILE."
  say "NOT CONFIGURED (no token)"
  exit 78
fi

# ---------- post ----------
setopt null_glob
files=("$PENDING"/*.json)
unsetopt null_glob

if [ "${#files[@]}" -eq 0 ]; then
  print -r -- "notes-sweep: nothing queued. posted=0 duplicate=0 failed=0"
  say "nothing queued"
  exit 0
fi

posted=0; dup=0; failed=0
tmpbody="$(mktemp -t carr-notes-sweep)"
trap 'rm -f "$tmpbody"' EXIT

for f in "${files[@]}"; do
  base="$(basename "$f")"

  # The file must be JSON and must carry the note id we dedup on. A malformed
  # queue file is quarantined, not silently dropped and not retried forever.
  ext_id="$(/usr/bin/python3 -c 'import json,sys
try:
    d=json.load(open(sys.argv[1]))
except Exception:
    sys.exit(1)
v=d.get("external_id")
print(v if isinstance(v,str) and v.strip() else "")' "$f" 2>/dev/null || true)"

  if [ -z "$ext_id" ]; then
    mv -f "$f" "$FAILED/$base"
    say "QUARANTINE $base (not JSON, or no external_id)"
    failed=$((failed+1))
    continue
  fi

  if grep -Fqx -- "$ext_id" "$LEDGER"; then
    mv -f "$f" "$SENT/$base"
    say "SKIP $base (already in ledger)"
    dup=$((dup+1))
    continue
  fi

  # Token goes in via --config on stdin so it never appears in argv / ps output.
  code="$(printf 'header = "authorization: Bearer %s"\n' "$TOKEN" \
    | curl -sS -m 60 --config - \
        -o "$tmpbody" -w '%{http_code}' \
        -X POST "$URL" \
        -H 'content-type: application/json' \
        --data-binary "@$f" 2>>"$LOG" || true)"

  case "$code" in
    2*)
      is_dup="$(/usr/bin/python3 -c 'import json,sys
try: print("yes" if json.load(open(sys.argv[1])).get("duplicate") else "no")
except Exception: print("no")' "$tmpbody" 2>/dev/null || echo no)"
      print -r -- "$ext_id" >> "$LEDGER"
      mv -f "$f" "$SENT/$base"
      if [ "$is_dup" = "yes" ]; then
        dup=$((dup+1)); say "OK   $base -> $code duplicate (already an ingest row)"
      else
        posted=$((posted+1)); say "OK   $base -> $code new ingest row"
      fi
      ;;
    *)
      failed=$((failed+1))
      say "FAIL $base -> HTTP ${code:-000} $(head -c 200 "$tmpbody" 2>/dev/null)"
      ;;
  esac
done

left="$(ls -1 "$PENDING" 2>/dev/null | grep -c '\.json$' || true)"
print -r -- "notes-sweep: source=$SOURCE_LABEL posted=$posted duplicate=$dup failed=$failed still_queued=$left"
say "summary posted=$posted duplicate=$dup failed=$failed still_queued=$left"

# Keep the shared capture log bounded, same convention as nightly.sh.
tail -n 2000 "$LOG" > "$LOG.trim" && mv "$LOG.trim" "$LOG"

[ "$failed" -eq 0 ] || exit 1
exit 0
