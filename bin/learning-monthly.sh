#!/bin/zsh
# learning-monthly.sh — ORDER 15's monthly pair, ONE byte-stable command.
#
# Rides the EXISTING monthly review (no new scheduler — the order's stop rule).
# Two jobs, both reading the rule store, neither writing anything anywhere:
#   1. promotion review   — active rules vs promotion.min_repeat_violations
#   2. conflict surfacing — rules that contradict
#
# "N active rules, 0 repeat violations, nothing to promote" is a PASS, not an
# empty result. Neither job changes a rule's enforcement, retires a rule, or
# resolves a conflict; promotion and resolution are human rulings and stay that
# way. These two produce reading material for the monthly review, nothing more.
#
# Appends to out/learning.log. Verified by OUTPUT: the two report files under
# Automation/Learning/ and their first lines.
#
# Run by hand any time: ./bin/learning-monthly.sh
set -u

REPO="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="/opt/homebrew/opt/node@22/bin:/opt/homebrew/bin:/opt/homebrew/opt/libpq/bin:/usr/local/bin:/usr/bin:/bin"
LOG="$REPO/out/learning.log"
VAULT="${CARR_VAULT:-/Users/booko/Library/CloudStorage/GoogleDrive-joe.bookout.carr.us@gmail.com/My Drive/CARR AI}"
LEARN_DIR="$VAULT/Automation/Learning"
mkdir -p "$REPO/out" "$LEARN_DIR"

[ -f "$HOME/.config/carr/db.env" ] && { set -a; . "$HOME/.config/carr/db.env"; set +a; }
: "${DATABASE_URL:=${CARR_DB_WRITER_URL:-${CARR_DB_CADENCE_URL:-}}}"
[ -n "$DATABASE_URL" ] && export DATABASE_URL || unset DATABASE_URL

say() { print -r -- "$(date -u '+%Y-%m-%dT%H:%M:%SZ')  $*" >> "$LOG"; }

say "===== learning monthly pair begin ====="
cd "$REPO" || { say "FATAL cannot cd $REPO"; exit 2; }

rc=0
./.venv/bin/python pipelines/learning_jobs.py monthly-chain \
  --report-dir "$LEARN_DIR" --report-dir "$REPO/out/Learning" >> "$LOG" 2>&1 || rc=$?

# 3 = at least one clause read a tier that could not answer it and SAID SO in
# its report. That is the honest-degradation path, not a failure.
if [ "$rc" -eq 0 ]; then
  say "===== learning monthly pair OK ====="
elif [ "$rc" -eq 3 ]; then
  say "===== learning monthly pair OK (one or more clauses UNAVAILABLE under the read tier — see the reports) ====="
  rc=0
else
  say "===== learning monthly pair FAILED (exit $rc) ====="
fi

tail -n 2000 "$LOG" > "$LOG.trim" && mv "$LOG.trim" "$LOG"
exit "$rc"
