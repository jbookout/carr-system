#!/bin/zsh
# carr-system drift + output check.
# (1) code drift: every manifest.tsv row, repo file vs live vault copy;
# (2) output drift: vault pipeline outputs vs committed baselines.
# Read-only: runs nothing, changes nothing.
# Diffs: tools/check.sh --code <repo-path>   |   tools/check.sh --out <baseline-name>

set -u
REPO="$(cd "$(dirname "$0")/.." && pwd)"
VAULT="${CARR_VAULT:-/Users/booko/Library/CloudStorage/GoogleDrive-joe.bookout.carr.us@gmail.com/My Drive/CARR AI}"

typeset -A OUT
OUT[deal-room-panhandle.html]="$VAULT/DNA/Team/live-boards/deal-room-panhandle.html"
OUT[lead-board.html]="$VAULT/Automation/lead-board.html"
OUT[renewal-radar.json]="$VAULT/Automation/renewal-radar.json"

case "${1:-}" in
  --code) exec diff -u "$VAULT/$(grep -F "$2	" "$REPO/manifest.tsv" | cut -f2)" "$REPO/$2" ;;
  --out)  exec diff -u "$REPO/baselines/$2" "${OUT[$2]}" ;;
esac

rc=0
echo "== Code drift (repo vs live vault copy, per manifest.tsv) =="
grep -v '^#' "$REPO/manifest.tsv" | while IFS=$'\t' read -r rp vp; do
  [ -z "$rp" ] && continue
  if [ ! -f "$VAULT/$vp" ]; then
    echo "  MISSING $rp   (vault copy not found: $vp)"; rc=1
  elif diff -q "$REPO/$rp" "$VAULT/$vp" >/dev/null 2>&1; then
    echo "  OK      $rp"
  else
    echo "  DRIFT   $rp   (diff: tools/check.sh --code $rp)"; rc=1
  fi
done

echo "== Output drift (vault output vs committed baseline) =="
for f in ${(k)OUT}; do
  if diff -q "$REPO/baselines/$f" "${OUT[$f]}" >/dev/null 2>&1; then
    echo "  OK      $f"
  else
    echo "  CHANGED $f   (diff: tools/check.sh --out $f)"; rc=1
  fi
done
exit $rc
