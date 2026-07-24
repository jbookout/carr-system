#!/bin/zsh
# carr-system drift + output check (phase 1).
# Reports: (1) code drift between repo generators and their live vault copies,
#          (2) output drift between the vault's current pipeline outputs and the committed baselines.
# Read-only: runs nothing, changes nothing.

set -u
REPO="$(cd "$(dirname "$0")/.." && pwd)"
VAULT="/Users/booko/Library/CloudStorage/GoogleDrive-joe.bookout.carr.us@gmail.com/My Drive/CARR AI"

typeset -A CODE OUT
CODE[build-deal-room.py]="$VAULT/DNA/Team/live-boards/build-deal-room.py"
CODE[build-lead-board.py]="$VAULT/Automation/build-lead-board.py"
CODE[build-renewal-feed.py]="$VAULT/Automation/build-renewal-feed.py"
OUT[deal-room-panhandle.html]="$VAULT/DNA/Team/live-boards/deal-room-panhandle.html"
OUT[lead-board.html]="$VAULT/Automation/lead-board.html"
OUT[renewal-radar.json]="$VAULT/Automation/renewal-radar.json"

rc=0
echo "== Code drift (repo vs live vault copy) =="
for f in ${(k)CODE}; do
  if diff -q "$REPO/generators/$f" "${CODE[$f]}" >/dev/null 2>&1; then
    echo "  OK      $f"
  else
    echo "  DRIFT   $f   (diff: tools/check.sh --code $f)"
    rc=1
  fi
done

echo "== Output drift (vault output vs committed baseline) =="
for f in ${(k)OUT}; do
  if diff -q "$REPO/baselines/$f" "${OUT[$f]}" >/dev/null 2>&1; then
    echo "  OK      $f"
  else
    echo "  CHANGED $f   (diff: tools/check.sh --out $f)"
    rc=1
  fi
done

case "${1:-}" in
  --code) diff -u "${CODE[$2]}" "$REPO/generators/$2" ;;
  --out)  diff -u "$REPO/baselines/$2" "${OUT[$2]}" ;;
esac
exit $rc
