#!/bin/sh
# Install the loopback-only Deal Room companion for Quill meeting mode.
#
# Risk color RED, human initiated: a click starts a client-visible recording
# announcement and capture. Nothing starts on a timer or without that click.
# The LaunchAgent only keeps the idle local control bridge available.
set -eu

TOOL_DIR="$(cd "$(dirname "$0")/.." && pwd)"
REPO="$(cd "$TOOL_DIR/../.." && pwd)"
LABEL="com.carr.call-mode"
SOURCE="$TOOL_DIR/launchd/$LABEL.plist"
DEST="$HOME/Library/LaunchAgents/$LABEL.plist"
TMP="$(mktemp -t carr-call-mode-plist)"
trap 'rm -f "$TMP"' EXIT

[ -f "$SOURCE" ] || { echo "missing $SOURCE" >&2; exit 1; }
[ -x "$TOOL_DIR/bin/call-mode.py" ] || chmod +x "$TOOL_DIR/bin/call-mode.py"

# A launchd agent loaded from ~/Library/LaunchAgents gets its Accessibility
# (TCC) responsibility attributed to ProgramArguments[0]. /usr/bin/python3 is
# the system xcrun shim, not a real interpreter, and macOS will not let a
# human usefully grant it Accessibility access (System Events clicks then
# fail with -25211 even after enabling python3, Python.app and osascript).
# Resolve the REAL interpreter that shim forwards to (the Command Line Tools
# python3) and bake that concrete, grantable path into the plist instead.
PYTHON_BIN="$(/usr/bin/python3 -c 'import sys; print(sys.executable)' 2>/dev/null || true)"
if [ -z "$PYTHON_BIN" ] || [ ! -x "$PYTHON_BIN" ]; then
    echo "Could not resolve a real python3 interpreter via /usr/bin/python3 -c 'import sys; print(sys.executable)'" >&2
    echo "(got: '${PYTHON_BIN:-<empty>}'). Install the Xcode Command Line Tools and retry." >&2
    exit 1
fi

mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
sed -e "s|{{REPO}}|$REPO|g" -e "s|{{HOME}}|$HOME|g" -e "s|{{PYTHON}}|$PYTHON_BIN|g" "$SOURCE" > "$TMP"
cp "$TMP" "$DEST"

echo "Call Mode will run under: $PYTHON_BIN"
echo "Grant that exact path Accessibility access: System Settings > Privacy & Security > Accessibility."

launchctl bootout "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$(id -u)" "$DEST"
launchctl print "gui/$(id -u)/$LABEL" >/dev/null

tries=0
while [ "$tries" -lt 20 ]; do
    if /usr/bin/curl -fsS --max-time 1 http://127.0.0.1:4682/api/state >/dev/null 2>&1; then
        echo "Call Mode ready at http://127.0.0.1:4682"
        exit 0
    fi
    tries=$((tries + 1))
    sleep 0.2
done

echo "Call Mode agent loaded but the health check did not answer; see $HOME/Library/Logs/carr-call-mode.log" >&2
exit 1
