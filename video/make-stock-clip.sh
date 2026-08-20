#!/bin/zsh
# make-stock-clip.sh — build + render the CARR stock b-roll proof clip.
# 1) writes stockclip-job.json  2) AE builds/renders lossless  3) ffmpeg: H.264 + audio mix
# Audio: music bed at -23 LUFS (quiet, under the visuals), whoosh per cut, impact on end card.

set -e
PIPE="$HOME/Movies/CARR Video Pipeline"
VIDEO_DIR="$(cd "$(dirname "$0")" && pwd)"
BRAND="$(python3 "$VIDEO_DIR/recovery-asset-root.py" "$@")"
BROLL="$BRAND/Stock/broll"
AUD="$PIPE/Audio_Library"
RAW="$PIPE/03_Output/stockclip_raw.mov"
FINAL="$PIPE/03_Output/carr_conflict_clip_1080sq.mp4"

MUSIC="${MUSIC:-$AUD/music/piano-reflections.mp3}"

cat > "$PIPE/Scripts/stockclip-job.json" <<EOF
{
  "compName": "CARR_Conflict_Proof",
  "width": 1080, "height": 1080, "fps": 29.97,
  "shots": [
    { "src": "$BROLL/lobby-blur-background_AS238299676.mov", "dur": 4.5, "pushIn": 1.07,
      "line": "Touring space for your new practice?" },
    { "src": "$BROLL/glass-tower-facade_AS367005503.mov", "dur": 4.5, "pushIn": 1.08,
      "line": "The listing broker works for the landlord." },
    { "src": "$BROLL/professionals-consult-laptop_AS518646499.mov", "dur": 3.5, "pushIn": 1.06,
      "line": "CARR works only for you." }
  ],
  "endCard": { "dur": 3.5, "name": "Joe Bookout", "title": "HEALTHCARE REAL ESTATE | CARR",
               "tagline": "Tenant and buyer representation only" },
  "outPath": "$RAW"
}
EOF

: > "$PIPE/Scripts/stockclip-log.txt"
echo "Building comp in After Effects..."
osascript <<APPLESCRIPT
with timeout of 900 seconds
    tell application "Adobe After Effects 2026"
        activate
        DoScript "\$.evalFile(File('$PIPE/Scripts/carr_stock_clip.jsx'))"
    end tell
end timeout
APPLESCRIPT

echo "--- AE log:"; cat "$PIPE/Scripts/stockclip-log.txt"
[ -s "$RAW" ] || { echo "AE render missing" >&2; exit 1; }

echo "Finishing: H.264 + audio mix..."
/opt/homebrew/bin/ffmpeg -y -nostdin \
  -i "$RAW" \
  -i "$MUSIC" \
  -i "$AUD/sfx/air-woosh.mp3" \
  -i "$AUD/sfx/cinematic-whoosh-fast-transition.mp3" \
  -i "$AUD/sfx/cinematic-whoosh-deep-impact.mp3" \
  -filter_complex "\
[1:a]atrim=0:16,loudnorm=I=-23:TP=-3:LRA=7,afade=t=in:d=0.7,afade=t=out:st=14.0:d=2.0[mus];\
[2:a]adelay=4350|4350,volume=0.65[w1];\
[3:a]adelay=8850|8850,volume=0.65[w2];\
[4:a]adelay=12350|12350,volume=0.75[imp];\
[mus][w1][w2][imp]amix=inputs=4:normalize=0,alimiter=limit=0.89[aout]" \
  -map 0:v -map "[aout]" \
  -c:v libx264 -preset medium -crf 18 -pix_fmt yuv420p -movflags +faststart \
  -c:a aac -b:a 256k -ar 48000 -t 16 \
  "$FINAL" 2>&1 | tail -3
echo "OK: $FINAL"
