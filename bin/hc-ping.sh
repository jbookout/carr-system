#!/bin/zsh
# ORDER 5: Healthchecks.io dead-man pings, fired at the END of the nightly chain.
# Reads ~/.config/carr/healthchecks.env (written by JOE at signup — ping URLs are
# semi-secrets: a leaked URL lets someone fake a heartbeat, so values live only
# in that 600-mode file, names-only in secrets-inventory.md). Absent file or
# vars => exit 78 (SKIP, not FAIL) per the standing chain convention.
# Pings: exports OK -> HC_PING_EXPORTS · backup OK -> HC_PING_BACKUP · and the
# live Worker health endpoint result -> HC_PING_MCP (with /fail suffix on a bad
# response, so the check alarms on a DOWN worker, not just a silent one).
ENV_FILE="$HOME/.config/carr/healthchecks.env"
[ -f "$ENV_FILE" ] || { echo "hc-ping: $ENV_FILE not found — Healthchecks not configured yet"; exit 78; }
source "$ENV_FILE"
[ -n "$HC_PING_EXPORTS" ] && [ -n "$HC_PING_BACKUP" ] && [ -n "$HC_PING_MCP" ] || { echo "hc-ping: one or more HC_PING_* vars missing"; exit 78; }
rc=0
curl -fsS -m 15 --retry 3 "$HC_PING_EXPORTS" > /dev/null || rc=1
curl -fsS -m 15 --retry 3 "$HC_PING_BACKUP" > /dev/null || rc=1
if curl -fsS -m 15 "https://api.practicecre.com/health" | grep -q '"ok"'; then
  curl -fsS -m 15 --retry 3 "$HC_PING_MCP" > /dev/null || rc=1
else
  curl -fsS -m 15 --retry 3 "$HC_PING_MCP/fail" > /dev/null || rc=1
fi
exit $rc
