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

# THE WHOLE-CHAIN CHECK, added 2026-08-10. The three checks above each report on
# ONE named step, so a failure anywhere else pings nothing at all — and on
# 2026-08-10 that was not hypothetical: the mypy tripwire had been failing since
# 08-08 and the corpus push failed that morning, the chain exited 1 three nights
# running, and all three checks pinged OK every time. Every alarm Joe had was
# green while the chain was red.
#
# OPTIONAL BY DESIGN. HC_PING_CHAIN is a fourth Healthchecks.io check that only
# Joe can create (it is an account action, not a script's job). Until the URL is
# in healthchecks.env this says so and pings nothing, rather than failing the
# step — the same SKIP-not-FAIL contract the rest of the chain uses. The health
# check's own "nightly chain result" row covers the same ground locally in the
# meantime, so the gap is visible either way.
if [ -n "${HC_PING_CHAIN:-}" ]; then
  ping_outcome "whole chain" "$HC_PING_CHAIN" "${HC_CHAIN_RC:-}"
else
  echo "hc-ping: HC_PING_CHAIN not set — the whole-chain check is not created yet."
  echo "         Until it is, a failing step that is not exports/backup/worker"
  echo "         alarms NOWHERE. Create a check at healthchecks.io and add its"
  echo "         ping URL to ~/.config/carr/healthchecks.env as HC_PING_CHAIN."
fi

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
