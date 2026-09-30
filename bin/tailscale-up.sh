#!/bin/zsh
# Bring Tailscale up at login if, and only if, it is stopped (WR-000178).
#
# After the 2026-09-30 macOS reboot the Mac Studio's Tailscale stayed "stopped"
# for about six hours and SSH to the MacBook was cut off. launchd runs this once
# at login through com.carr.tailscale-up (installed on the primary by
# ops/config-as-code.py). It is idempotent:
#
#   running    -> nothing to do, exit 0
#   stopped    -> `Tailscale up --timeout=60s`, exit with its status
#   logged out -> exit 3 WITHOUT running `up`: that path waits on a browser
#                 sign-in nobody is at (rule 847f9995). The health row reports it.
#   no app     -> exit 0; nothing to start on this Mac
#
# The stored node key is the only credential used; nothing is read or written here.
set -u

TS="${TAILSCALE_BIN:-/Applications/Tailscale.app/Contents/MacOS/Tailscale}"
stamp() { date -u +%Y-%m-%dT%H:%M:%SZ; }

if [[ ! -x "$TS" ]]; then
  echo "$(stamp) tailscale-up: $TS not installed; nothing to do"
  exit 0
fi

# The daemon can lag the login session by a few seconds after a reboot.
out=""
for attempt in 1 2 3 4 5 6; do
  out="$("$TS" status 2>&1)"
  rc=$?
  case "$out" in
    *"failed to connect"*|*"not running"*) sleep 5 ;;
    *) break ;;
  esac
done

case "$out" in
  *"Tailscale is stopped"*)
    echo "$(stamp) tailscale-up: stopped; running up"
    "$TS" up --timeout=60s
    rc=$?
    echo "$(stamp) tailscale-up: up exited $rc"
    exit $rc
    ;;
  *"Logged out"*|*"NeedsLogin"*)
    echo "$(stamp) tailscale-up: node is logged out; not running up (needs an interactive sign-in)"
    exit 3
    ;;
esac

if [[ $rc -eq 0 ]]; then
  echo "$(stamp) tailscale-up: already running; nothing to do"
  exit 0
fi
echo "$(stamp) tailscale-up: status unreadable (exit $rc): ${out%%$'\n'*}"
exit $rc
