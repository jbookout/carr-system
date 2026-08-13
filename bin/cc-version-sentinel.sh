#!/bin/zsh
# cc-version-sentinel.sh — the FREE half of the Claude Code update audit (loop #177).
#
# WHY THIS EXISTS. The cc-update-audit scheduled task fired five times a week and,
# by its own description, "exits in seconds on no-change days" — which is most days.
# But a session that exits in seconds still cost a full session boot: the rule store,
# the standing context, the whole opening act, five times a week, to compare two
# strings. The cost model this system runs on (loop #177) is that a script costs
# nothing and a session costs tokens, so anything mechanical belongs in a script and
# sessions are reserved for judgment.
#
# THE SPLIT. The GATE is mechanical — read two version strings, compare to a marker.
# That is this file, run by launchd for free. The AUDIT is genuine judgment — read the
# real changelog, work out what each release changes for CARR, delegate to the IT
# Support lane — and that stays a session.
#
# WHAT THIS DOES WHEN THE VERSION MOVES. It notifies Joe within the hour (the same
# macOS notification shape pipelines/partner_ping.py uses — the worked example loop
# #177 cites by name), and it writes a marker the audit session reads. It deliberately
# does NOT advance last-audited-version.txt: only a completed audit may do that, or a
# notification would silently consume the very change it was reporting.
#
# TWO BINARIES, BOTH WATCHED, and this is not optional. The PATH binary
# (/opt/homebrew/bin/claude, npm-global, self-updating) is what `claude --version`
# reports. The desktop app ships its OWN runtime under
# ~/Library/Application Support/Claude/claude-code/<version>/ and THAT is what
# actually executes Joe's sessions; on 2026-08-09 they sat at 2.1.226 and 2.1.222.
# Watching only the PATH binary blinds the audit to the runtime that matters. The
# string built below is byte-identical in shape to the one the audit task's own STEP 0
# builds, because a gate and its script disagreeing about what "changed" means is
# worse than having no gate (rule a8c55a47: one job, one implementation).
#
# RISK COLOR: GREEN. It reads two version strings, writes two files under the task's
# own directory and one log line, and raises a local notification. It calls no verb,
# touches no record, reaches no network, and sends nothing off this Mac.
#
# Usage: ./bin/cc-version-sentinel.sh [--dry-run]

set -eu

REPO="$(cd "$(dirname "$0")/.." && pwd)"
TASK_DIR="$HOME/.claude/scheduled-tasks/cc-update-audit"
SENTINEL="$TASK_DIR/last-audited-version.txt"
PENDING="$TASK_DIR/pending-version.txt"
LOG="$REPO/out/cc-version-sentinel.log"
DRY=0
[ "${1:-}" = "--dry-run" ] && DRY=1

mkdir -p "$REPO/out"
say() { print -r -- "$(date -u +%FT%TZ) cc-version-sentinel $*" >> "$LOG" }

# The audit task's directory is the marker's home on purpose: the file that says
# "an audit is owed" belongs beside the file that says "an audit was done", so a
# reader never has to know two places to answer one question.
if [ ! -d "$TASK_DIR" ]; then
  say "SKIP task dir missing: $TASK_DIR (audit task removed?)"
  exit 0
fi

# ABSOLUTE PATH, NOT `claude`. launchd does not run a login shell, so its PATH does
# not contain /opt/homebrew/bin. Found the only way it could be — by installing this
# agent and reading its first real run, which reported `cli=none`, called that a
# version CHANGE, and notified Joe about an update that had not happened. A sentinel
# whose failure mode is a false alarm is worse than no sentinel: it trains the person
# receiving it to ignore it.
CLAUDE_BIN=""
for c in /opt/homebrew/bin/claude /usr/local/bin/claude "$HOME/.local/bin/claude"; do
  [ -x "$c" ] && { CLAUDE_BIN="$c"; break; }
done
[ -z "$CLAUDE_BIN" ] && CLAUDE_BIN="$(command -v claude 2>/dev/null || true)"
CLI=""
[ -n "$CLAUDE_BIN" ] && CLI="$("$CLAUDE_BIN" --version 2>/dev/null | awk '{print $1}')"
APP="$(ls -1 "$HOME/Library/Application Support/Claude/claude-code/" 2>/dev/null | sort -V | tail -1)"
CUR="cli=${CLI:-none} app=${APP:-none}"
LAST="$(cat "$SENTINEL" 2>/dev/null || echo "none")"

# A READ FAILURE IS NOT A VERSION CHANGE, and conflating the two is what produced the
# false alarm above. If a component reads `none` now but was a real version at the last
# audit, the binary did not vanish — this script failed to see it. Report that, change
# nothing, and let the next hour retry. A binary that reads `none` on BOTH sides is a
# genuine not-installed state and compares normally.
if [ "${CLI:-none}" = "none" ] && [ -n "${LAST##*cli=none*}" ] && [ "$LAST" != "none" ]; then
  say "FAIL cli unreadable (PATH?) while last_audited names a version — no comparison, marker untouched"
  exit 1
fi
if [ "${APP:-none}" = "none" ] && [ -n "${LAST##*app=none*}" ] && [ "$LAST" != "none" ]; then
  say "FAIL app runtime unreadable while last_audited names a version — no comparison, marker untouched"
  exit 1
fi

if [ "$CUR" = "$LAST" ]; then
  say "OK no change ($CUR)"
  exit 0
fi

say "CHANGE current[$CUR] last_audited[$LAST]"

if [ "$DRY" -eq 1 ]; then
  print -r -- "would notify: Claude Code changed — $CUR (last audited: $LAST)"
  exit 0
fi

# The marker carries BOTH strings, because the audit's scope is the range between
# them and re-deriving `last_audited` after the fact is how a gap gets missed.
print -r -- "current: $CUR
last_audited: $LAST
detected_at: $(date -u +%FT%TZ)" > "$PENDING"

TITLE="Claude Code updated"
SUBTITLE="$CUR"
MESSAGE="Last audited: $LAST — the update audit is owed. It runs Monday, or ask for it now."
/usr/bin/osascript -e "display notification \"$MESSAGE\" with title \"$TITLE\" subtitle \"$SUBTITLE\"" \
  >/dev/null 2>&1 || say "WARN notification failed (osascript)"

say "OK marker written, Joe notified"
