#!/bin/zsh
# The sole Production caller for ops.set_rule_delivery_mode.  Dry-run by
# default; --apply still cannot cross the exact human-curation or seven-day
# scoped-shadow gates enforced by ops/rule-delivery-cutover.py.
set -eu
REPO="${0:A:h:h}"
APPLY=0
MODE=""
CHANGED_BY=""
REASON=""
while (( $# )); do
  case "$1" in
    --apply) APPLY=1; shift ;;
    --mode) MODE="$2"; shift 2 ;;
    --changed-by) CHANGED_BY="$2"; shift 2 ;;
    --reason) REASON="$2"; shift 2 ;;
    *) print -u2 "unknown argument: $1"; exit 2 ;;
  esac
done
[[ "$MODE" == "shadow" || "$MODE" == "enforced" ]] || {
  print -u2 "--mode must be shadow or enforced"; exit 2; }
[[ -n "$CHANGED_BY" && -n "$REASON" ]] || {
  print -u2 "--changed-by and --reason are required"; exit 2; }

if (( APPLY )); then
  dirty=$(cd "$REPO" && git status --porcelain -- \
    ops/config/rule-delivery-activation-overlay.v1.json \
    ops/rule-delivery-cutover.py bin/rule-delivery-cutover-prod.sh \
    migrations/0317_atomic_rule_delivery_cutover.sql)
  [[ -z "$dirty" ]] || { print -u2 "REFUSED: cutover source is uncommitted"; exit 1; }
fi

if [[ -z "${NEON_API_KEY:-}" && -f "$HOME/.config/carr/db.env" ]]; then
  set -a; . "$HOME/.config/carr/db.env"; set +a
fi
NEONCTL="$REPO/mcp-server/node_modules/.bin/neonctl"
[[ -x "$NEONCTL" ]] || NEONCTL="neonctl"
DSN="$("$NEONCTL" connection-string production --project-id steep-field-48688294 \
       --role-name neondb_owner 2>/tmp/rule-delivery-cutover-neonctl.err)"
if [[ -z "$DSN" ]]; then
  print -u2 "could not derive the pinned Production owner credential"
  rm -f /tmp/rule-delivery-cutover-neonctl.err
  exit 1
fi
rm -f /tmp/rule-delivery-cutover-neonctl.err
args=(--mode "$MODE" --changed-by "$CHANGED_BY" --reason "$REASON")
(( APPLY )) && args+=(--apply)
DATABASE_URL="$DSN" "$REPO/.venv/bin/python" "$REPO/ops/rule-delivery-cutover.py" "${args[@]}"
