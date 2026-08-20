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
# out/Learning/ and their first lines.
#
# Run by hand any time: ./bin/learning-monthly.sh
set -u

REPO="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="/opt/homebrew/opt/node@22/bin:/opt/homebrew/bin:/opt/homebrew/opt/libpq/bin:/usr/local/bin:/usr/bin:/bin"
LOG="$REPO/out/learning.log"
# THE CUTOFF, 2026-08-19 — same change and same reasoning as bin/learning-weekly.sh:
# these reports are renderings of database content, not a home for it, so the
# vault copy retired with the 37 doctrine renders. The repo copy stays.
LEARN_DIR="$REPO/out/Learning"
mkdir -p "$REPO/out" "$LEARN_DIR"

[ -f "$HOME/.config/carr/db.env" ] && { set -a; . "$HOME/.config/carr/db.env"; set +a; }
jobs_url="${CARR_DB_JOBS_URL:-}"
unset DATABASE_URL CARR_DB_WRITER_URL CARR_DB_OWNER_URL CARR_DB_CADENCE_URL CARR_IMPORT_DB_URL
if [ -z "$jobs_url" ]; then
  print -ru2 -- "learning-monthly: CARR_DB_JOBS_URL is required; refusing writer/owner fallback"
  exit 78
fi
export CARR_DB_JOBS_URL="$jobs_url"

say() { print -r -- "$(date -u '+%Y-%m-%dT%H:%M:%SZ')  $*" >> "$LOG"; }

say "===== learning monthly pair begin ====="
cd "$REPO" || { say "FATAL cannot cd $REPO"; exit 2; }

rc=0
./.venv/bin/python pipelines/learning_jobs.py monthly-chain \
  --report-dir "$LEARN_DIR" >> "$LOG" 2>&1 || rc=$?

# CORRECTIONS SWEEP (loop #113, added 2026-08-13). The rule store had a rich output
# path and no INPUT path except a session noticing in the moment and remembering to
# call `teach`. This looks for corrections Joe has had to make MORE THAN ONCE, which
# is the clearest possible signal that a rule is missing.
#
# IT LIVES HERE RATHER THAN IN A SCHEDULED SESSION on the cost model loop #177
# settled: a script costs nothing and a session costs tokens, so anything mechanical
# belongs in a chain like this one. It PROPOSES only — it never calls `teach`, and a
# proposal binds nobody until Joe says yes.
#
# Its exit code is deliberately not checked: a sweep that finds nothing is a normal
# month, and a broken sweep must not take the learning chain down with it.
./.venv/bin/python ops/corrections-sweep.py \
  > "$REPO/out/Learning/corrections-sweep-$(date -u +%Y%m).md" 2>> "$LOG" \
  && say "corrections sweep written to out/Learning/corrections-sweep-$(date -u +%Y%m).md" \
  || say "corrections sweep did not complete — see $LOG (chain continues)"

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
