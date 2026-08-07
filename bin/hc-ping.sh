#!/bin/zsh
# ORDER 5: Healthchecks.io dead-man pings, fired at the END of the nightly chain.
# Reads ~/.config/carr/healthchecks.env (written by JOE at signup — ping URLs are
# semi-secrets: a leaked URL lets someone fake a heartbeat, so values live only
# in that 600-mode file, names-only in secrets-inventory.md). Absent file or
# vars => exit 78 (SKIP, not FAIL) per the standing chain convention.
# Pings: exports -> HC_PING_EXPORTS · backup -> HC_PING_BACKUP · and the live
# Worker health endpoint result -> HC_PING_MCP.
#
# CHANGED 2026-08-07. Every ping used to fire unconditionally, so two of the
# three checks reported on whether the chain REACHED this script, not on whether
# the work succeeded. On 2026-08-07 a 200-byte corrupt backup — pg_dump died
# mid-dump and a weak guard promoted the empty file — pinged the backup check
# OK, uploaded to R2, and exited 0. Every signal Joe had said the backup
# succeeded. None of them looked at it.
#
# The caller now hands each check the exit code of the step it is named after:
#   HC_EXPORTS_RC · HC_BACKUP_RC   (0 = OK, anything else = failed or skipped)
# Non-zero pings the /fail endpoint, so the check ALARMS NOW rather than waiting
# out the dead-man grace period. That is the treatment HC_PING_MCP has had since
# ORDER 5; it is simply applied to all three now.
#
# UNSET is deliberately NOT treated as OK and deliberately NOT treated as a
# failure: it means nobody told us, which is the state of a human running this
# script by hand. An unknown outcome reports nothing at all and says so. Silence
# costs a grace period; a false OK costs the backup.
ENV_FILE="$HOME/.config/carr/healthchecks.env"
[ -f "$ENV_FILE" ] || { echo "hc-ping: $ENV_FILE not found — Healthchecks not configured yet"; exit 78; }
source "$ENV_FILE"
[ -n "$HC_PING_EXPORTS" ] && [ -n "$HC_PING_BACKUP" ] && [ -n "$HC_PING_MCP" ] || { echo "hc-ping: one or more HC_PING_* vars missing"; exit 78; }

rc=0

# ping_outcome <label> <base-url> <rc-or-empty>
# rc is the exit code of the step this check reports on. Empty means unknown.
ping_outcome() {
  local label="$1" url="$2" step_rc="$3"
  if [ -z "$step_rc" ]; then
    echo "hc-ping: $label outcome not supplied — NOT pinging. The check will go"
    echo "         late on its own, which is the honest signal when nobody knows."
    return 0
  fi
  if [ "$step_rc" -eq 0 ]; then
    curl -fsS -m 15 --retry 3 "$url" > /dev/null || rc=1
    echo "hc-ping: $label OK -> pinged"
  else
    curl -fsS -m 15 --retry 3 "$url/fail" > /dev/null || rc=1
    echo "hc-ping: $label exited $step_rc -> pinged /fail (alarming now, not after the grace period)"
  fi
}

ping_outcome "exports" "$HC_PING_EXPORTS" "${HC_EXPORTS_RC:-}"
ping_outcome "backup"  "$HC_PING_BACKUP"  "${HC_BACKUP_RC:-}"

if curl -fsS -m 15 "https://api.practicecre.com/health" | grep -q '"ok"'; then
  curl -fsS -m 15 --retry 3 "$HC_PING_MCP" > /dev/null || rc=1
  echo "hc-ping: worker health ok -> pinged"
else
  curl -fsS -m 15 --retry 3 "$HC_PING_MCP/fail" > /dev/null || rc=1
  echo "hc-ping: worker health BAD -> pinged /fail"
fi

# rc reflects whether the PINGS got through, never whether the night was good.
# A night that failed still wants its /fail delivered successfully.
exit $rc
