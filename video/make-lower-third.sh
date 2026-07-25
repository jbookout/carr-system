#!/bin/zsh
# make-lower-third.sh — generate a CARR branded lower-third overlay with alpha
# Usage: ./make-lower-third.sh "Name" "Title line" [horizontal|vertical] [duration]
# Output lands in 03_Output as <Name>_lowerthird_<orientation>.mov

set -e
PIPE="$HOME/Movies/CARR Video Pipeline"
NAME="${1:?Usage: make-lower-third.sh \"Name\" \"Title\" [horizontal|vertical] [duration]}"
TITLE="${2:?Missing title argument}"
ORIENT="${3:-horizontal}"
DUR="${4:-6}"
SAFE=$(echo "$NAME" | tr ' /' '__')
OUT="$PIPE/03_Output/${SAFE}_lowerthird_${ORIENT}.mov"

# escape for JSON
esc() { echo "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'; }

cat > "$PIPE/Scripts/lowerthird-job.json" <<EOF
{
  "name": "$(esc "$NAME")",
  "title": "$(esc "$TITLE")",
  "orientation": "$ORIENT",
  "duration": $DUR,
  "outPath": "$(esc "$OUT")",
  "logo": true
}
EOF

: > "$PIPE/Scripts/lowerthird-log.txt"

echo "Launching After Effects (first launch can take ~30s)..."
osascript <<APPLESCRIPT
with timeout of 600 seconds
    tell application "Adobe After Effects 2026"
        activate
        DoScript "\$.evalFile(File('$PIPE/Scripts/carr_lower_third.jsx'))"
    end tell
end timeout
APPLESCRIPT

echo "--- AE log:"
cat "$PIPE/Scripts/lowerthird-log.txt"
if [ -f "$OUT" ]; then
    echo "OK: $OUT"
else
    echo "Render did not produce output — check log above." >&2
    exit 1
fi
