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
  --out)
    if [ ! -f "$REPO/baselines/$2" ]; then
      echo "local baseline copy missing (gitignored, hash-only tracked): baselines/$2"
      echo "regenerate: cp \"${OUT[$2]}\" \"$REPO/baselines/$2\"  [only after confirming today's output is correct]"
      exit 1
    fi
    exec diff -u "$REPO/baselines/$2" "${OUT[$2]}"
    ;;
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

echo "== Video pipeline drift (repo vs ~/Movies runtime, per manifest-video.tsv) =="
VIDEO="${CARR_VIDEO_PIPE:-$HOME/Movies/CARR Video Pipeline}"
if [ -d "$VIDEO" ]; then
  grep -v '^#' "$REPO/manifest-video.tsv" | while IFS=$'\t' read -r rp vp; do
    [ -z "$rp" ] && continue
    if [ ! -f "$VIDEO/$vp" ]; then
      echo "  MISSING $rp   (runtime copy not found: $vp)"; rc=1
    elif diff -q "$REPO/$rp" "$VIDEO/$vp" >/dev/null 2>&1; then
      echo "  OK      $rp"
    else
      echo "  DRIFT   $rp   (diff: diff -u \"$VIDEO/$vp\" \"$REPO/$rp\")"; rc=1
    fi
  done
else
  echo "  SKIP    video pipeline not present at $VIDEO"
fi

echo "== Output drift (vault output vs committed baseline) =="
# PII-bearing baselines (lead-board.html, deal-room-panhandle.html) moved to hash-only
# tracking under ORDER 42b (2026-08-06): the full HTML stays LOCAL and untracked
# (.gitignore); only its sha256 is committed, in baselines/SHA256SUMS. Everything else
# (renewal-radar.json) has no PII and still tracks the full file, diffed as before.
typeset -A BASEHASH
if [ -f "$REPO/baselines/SHA256SUMS" ]; then
  while IFS= read -r line; do
    case "$line" in
      \#*|'') continue ;;
    esac
    BASEHASH[${line##*  }]="${line%%  *}"
  done < "$REPO/baselines/SHA256SUMS"
fi
for f in ${(k)OUT}; do
  if [ -n "${BASEHASH[$f]:-}" ]; then
    if [ ! -f "${OUT[$f]}" ]; then
      echo "  MISSING $f   (vault output not found: ${OUT[$f]})"; rc=1
    else
      livehash="$(shasum -a 256 "${OUT[$f]}" | cut -d' ' -f1)"
      if [ "$livehash" = "${BASEHASH[$f]}" ]; then
        echo "  OK      $f"
      elif [ -f "$REPO/baselines/$f" ]; then
        echo "  CHANGED $f   (hash mismatch vs baselines/SHA256SUMS; diff: tools/check.sh --out $f)"; rc=1
      else
        echo "  CHANGED $f   (hash mismatch vs baselines/SHA256SUMS; local baseline copy missing —"
        echo "          regenerate: cp \"${OUT[$f]}\" \"$REPO/baselines/$f\"  [only after confirming the new output is correct])"
        rc=1
      fi
    fi
  else
    if diff -q "$REPO/baselines/$f" "${OUT[$f]}" >/dev/null 2>&1; then
      echo "  OK      $f"
    else
      echo "  CHANGED $f   (diff: tools/check.sh --out $f)"; rc=1
    fi
  fi
done

# Twin-copy identity (orchestrator-lane corrective #3, 2026-07-25): Joe's generator and
# Dell's shared template are the SAME logic and were reconciled to identical on 2026-07-25.
# A fix applied to one side only used to ship silently — now it flags here.
echo "== Twin-copy identity (generator vs shared template) =="
if diff -q "$REPO/generators/build-lead-board.py" "$REPO/shared/build-lead-board-template.py" >/dev/null 2>&1; then
  echo "  OK      build-lead-board (both copies identical)"
else
  echo "  SPLIT   build-lead-board — the two copies have diverged; port the change to both or say why in manifest.tsv"; rc=1
fi

# writing-lint fixtures (2026-07-25). The linter encodes DNA/writing-rules.md, so a
# rule edit that quietly starts flagging good CRE prose (or stops catching a tell)
# is the failure mode that would get it ignored. The two fixtures are the contract.
echo "== writing-lint fixtures (false-positive + detection guard) =="
if [ -f "$REPO/baselines/writing-lint.txt" ]; then
  tmp="$(mktemp)"
  { echo "# writing-lint baseline. Regenerate: tools/writing-lint-baseline.sh"
    echo "## clean-email.txt --surface email  (must stay 0 HARD)"
    python3 "$REPO/tools/writing-lint.py" "$REPO/tools/fixtures/clean-email.txt" --surface email | tail -n +2
    echo
    echo "## dirty-social.txt --surface social"
    python3 "$REPO/tools/writing-lint.py" "$REPO/tools/fixtures/dirty-social.txt" --surface social | tail -n +2
  } > "$tmp" 2>/dev/null
  if diff -q "$REPO/baselines/writing-lint.txt" "$tmp" >/dev/null 2>&1; then
    echo "  OK      writing-lint (fixtures match baseline)"
  else
    echo "  CHANGED writing-lint — findings moved. Review, then tools/writing-lint-baseline.sh"
    echo "          (diff: diff -u baselines/writing-lint.txt <(tools/writing-lint-baseline.sh >/dev/null; cat baselines/writing-lint.txt))"
    rc=1
  fi
  rm -f "$tmp"
else
  echo "  MISSING writing-lint baseline — run tools/writing-lint-baseline.sh"; rc=1
fi

# Registry integrity (2026-07-27). Both of that day's defects — lane 3's columns dropped
# (#100) and L-164/165/166 reassigned on top of live rows (#101) — ran for weeks behind a
# clean-looking weekly digest because nothing resolved a pointer end to end. This does.
echo "== Registry integrity (pointer rot, occupant drift, schema, ledger) =="
if CARR_VAULT="$VAULT" python3 "$REPO/tools/registry-audit.py" >/dev/null 2>&1; then
  echo "  OK      lead-registry (every pointer resolves)"
else
  echo "  BROKEN  lead-registry — run: ./run.sh registry-audit"; rc=1
fi
exit $rc
