#!/bin/zsh
# CARR Automation Chrome — dedicated flagged instance (created Jul 7, 2026; flags fixed same day)
# Separate Chrome instance (own user-data-dir): the normal browser keeps full protections.
# Flags disable BOTH generations of Chrome's localhost gate (PNA legacy + LNA current)
# so Claude's firefly_receiver (127.0.0.1:8756) can catch generated files.
# Sign into ONLY what automation needs here (Adobe Firefly). No Google account.
exec "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --user-data-dir="$HOME/.chrome-carr-automation" \
  --disable-features=LocalNetworkAccessChecks,LocalNetworkAccessChecksWarn,PrivateNetworkAccessChecks,PrivateNetworkAccessSendPreflights,PrivateNetworkAccessRespectPreflightResults \
  --no-first-run \
  --no-default-browser-check \
  "https://firefly.adobe.com/generate/video" &
# Companion: firefly_receiver.py (same folder) must be running to catch files:
#   python3 "$(dirname "$0")/firefly_receiver.py" &
