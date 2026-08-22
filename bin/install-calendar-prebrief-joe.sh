#!/bin/zsh
# Explicit Joe-only installer. It refuses absent evidence and never loads Dell.
set -eu
REPO="$(cd "$(dirname "$0")/.." && pwd)"
CANONICAL_REPO="$HOME/carr-system"
PROFILE="$HOME/.config/carr/calendar-prebrief-joe.env"
APP="$HOME/Applications/CARR Calendar Access.app"
BACKUP="$HOME/Applications/CARR Calendar Access.app.previous"
STAGED_ROOT=""
INSTALLED=0
SUCCESS=0
recover() {
  code=$?
  if [ "$SUCCESS" -ne 1 ] && [ "$INSTALLED" -eq 1 ] && [ -e "$BACKUP" ]; then
    # Preserve the failed candidate for inspection; never delete an app bundle
    # while recovering the known prior installation.
    if [ -e "$APP" ]; then
      mv "$APP" "$HOME/Applications/CARR Calendar Access.app.failed.$$.app" 2>/dev/null || true
    fi
    mv "$BACKUP" "$APP" 2>/dev/null || true
  fi
  [ -z "$STAGED_ROOT" ] || [ ! -d "$STAGED_ROOT" ] || rmdir "$STAGED_ROOT" 2>/dev/null || true
  exit "$code"
}
trap recover EXIT HUP INT TERM
[ "${1:-}" = "--install" ] || { print -u2 'usage: install-calendar-prebrief-joe.sh --install'; exit 64; }
[ -f "$PROFILE" ] || { print -u2 'calendar prebrief: missing Joe-only 0600 profile'; exit 78; }
[ "$(stat -f '%Lp' "$PROFILE" 2>/dev/null || stat -c '%a' "$PROFILE")" = 600 ] || { print -u2 'calendar prebrief: profile must be 0600'; exit 78; }
[ -x "$CANONICAL_REPO/.venv/bin/python" ] && [ -f "$CANONICAL_REPO/tools/calendar-prebrief-joe-runtime.py" ] || {
  print -u2 'calendar prebrief: permanent carr-system runtime is unavailable'; exit 78; }
CARR_REPO="$REPO" "$REPO/bin/build-calendar-access.sh" || exit $?
mkdir -p "$HOME/Applications"
STAGED_ROOT="$(mktemp -d "$HOME/Applications/.carr-calendar-access.XXXXXX")"
STAGED="$STAGED_ROOT/CARR Calendar Access.app"
ditto "$REPO/tools/CARR Calendar Access.app" "$STAGED"
xattr -cr "$STAGED"
codesign --force --sign - "$STAGED" >/dev/null 2>&1 || exit 78
[ ! -e "$BACKUP" ] || { print -u2 'calendar prebrief: recoverable backup already exists'; exit 78; }
[ ! -e "$APP" ] || mv "$APP" "$BACKUP"
mv "$STAGED" "$APP"
INSTALLED=1
xattr -cr "$APP"
codesign -v "$APP" >/dev/null 2>&1 || { print -u2 'calendar prebrief: app signature refused'; exit 78; }
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$APP" >/dev/null 2>&1 || true
mkdir -p "$HOME/Library/LaunchAgents" "$REPO/out"
sed -e "s|{{REPO}}|$CANONICAL_REPO|g" -e "s|{{HOME}}|$HOME|g" "$REPO/ops/launchd/com.carr.calendar-prebrief-joe.plist" > "$HOME/Library/LaunchAgents/com.carr.calendar-prebrief-joe.plist"
plutil -lint "$HOME/Library/LaunchAgents/com.carr.calendar-prebrief-joe.plist" >/dev/null
SUCCESS=1
print 'calendar prebrief: staged recoverably; sealed authority activation/bootstrap is still required'
