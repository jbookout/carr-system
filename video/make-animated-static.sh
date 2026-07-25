#!/bin/zsh
# make-animated-static.sh — turn a static you already run into a 6-second
# stop-motion build. Video for the feed, GIF for email and landing pages.
#
#   make-animated-static.sh <layers-folder> [outname] [duration]
#
# The layers folder holds one full-canvas PNG per element, named in BUILD ORDER:
#   01_background.png  02_headline.png  03_rule.png  04_body.png  05_logo.png
# Every PNG must be the same pixel size (the canvas) with transparency around its
# element. That is the whole input contract: because you AUTHORED the static, you
# already have the layers. Nothing here has to guess at segmenting a flat JPG.
#
# Optional per-layer direction, encoded in the filename after the number:
#   02_headline_below.png   02_headline_left.png   05_logo_scale.png
# Directions: below (default), above, left, right, scale.
#
# Audio follows the standing rule from CRITIQUE-LOG (v3): ONE sound for the whole
# piece, quiet, airy. A soft UI tick on each landing at 0.3, nothing else. Not the
# pops/snaps/clicks/thuds grab-bag — that was the v3 failure.
# Music bed is opt-in:  MUSIC="$AUD/music/close-up.mp3" make-animated-static.sh ...

set -e
PIPE="$HOME/Movies/CARR Video Pipeline"
BRAND="/Users/booko/Library/CloudStorage/GoogleDrive-joe.bookout.carr.us@gmail.com/My Drive/CARR AI/Marketing/Brand Assets"
AUD="$BRAND/Audio/library"
TICK="$AUD/sfx/tick-ui-soft_modern-tech-select.wav"
FF=/opt/homebrew/bin/ffmpeg

LAYERDIR="${1:?usage: make-animated-static.sh <layers-folder> [outname] [duration]}"
NAME="${2:-animated_static}"
DUR="${3:-6}"
FPS=30
STEP_FRAMES="${STEP_FRAMES:-3}"     # 3 frames per pose = 10 poses/sec stop-motion cadence
HOLD_TAIL="${HOLD_TAIL:-1.6}"       # seconds the finished static holds at the end
GIF_WIDTH="${GIF_WIDTH:-640}"

RAW="$PIPE/03_Output/${NAME}_raw.mov"
FINAL="$PIPE/03_Output/${NAME}.mp4"
GIF="$PIPE/03_Output/${NAME}.gif"

[ -d "$LAYERDIR" ] || { echo "no such layers folder: $LAYERDIR" >&2; exit 1; }
[ -f "$TICK" ] || { echo "landing SFX missing: $TICK" >&2; exit 1; }

# --- collect layers in build order ------------------------------------------
LAYERS=("${(@f)$(find "$LAYERDIR" -maxdepth 1 -name '*.png' | sort)}")
N=${#LAYERS[@]}
[ "$N" -gt 0 ] || { echo "no PNG layers in $LAYERDIR" >&2; exit 1; }

# canvas = size of the first layer; every layer must match it
DIMS=$(/opt/homebrew/bin/ffprobe -v error -select_streams v:0 \
       -show_entries stream=width,height -of csv=p=0:s=x "${LAYERS[1]}")
CW=${DIMS%x*}; CH=${DIMS#*x}
for l in "${LAYERS[@]}"; do
  d=$(/opt/homebrew/bin/ffprobe -v error -select_streams v:0 \
      -show_entries stream=width,height -of csv=p=0:s=x "$l")
  [ "$d" = "$DIMS" ] || { echo "layer size mismatch: $(basename "$l") is $d, canvas is $DIMS" >&2; exit 1; }
done
echo "canvas ${DIMS}, $N layers, ${DUR}s, ${STEP_FRAMES}f per pose"

# --- beats: spread the build across the window before the tail hold ----------
BUILD_WINDOW=$(echo "$DUR $HOLD_TAIL" | awk '{printf "%.4f", $1-$2}')
LEAD=0.25   # a beat of the empty canvas before the first element lands

typeset -a BEATS
for (( i=1; i<=N; i++ )); do
  BEATS[$i]=$(echo "$LEAD $BUILD_WINDOW $N $i" | awk '{printf "%.4f", $1 + ($2-$1)*($4-1)/($3)}')
done

# --- job json ----------------------------------------------------------------
JOB="$PIPE/Scripts/animstatic-job.json"
{
  echo '{'
  echo "  \"compName\": \"CARR_${NAME}\","
  echo "  \"width\": $CW, \"height\": $CH, \"fps\": $FPS,"
  echo "  \"duration\": $DUR, \"stepFrames\": $STEP_FRAMES,"
  echo '  "layers": ['
  for (( i=1; i<=N; i++ )); do
    base=$(basename "${LAYERS[$i]}" .png)
    dir="below"
    case "$base" in
      *_above) dir="above" ;; *_left) dir="left" ;;
      *_right) dir="right" ;; *_scale) dir="scale" ;;
    esac
    # Layer 1 is the background plate: it IS the canvas, so it never animates.
    tilt="1.6"; [ "$i" -eq 1 ] && { dir="plate"; tilt="0"; }
    # Anchor each layer on its own element, not on the canvas center, so scale
    # and rotation pivot where the element actually sits.
    ANCHOR=$(python3 -c "
from PIL import Image; import sys
im = Image.open(sys.argv[1]).convert('RGBA')
bb = im.getbbox() or (0, 0, im.width, im.height)
print(int((bb[0]+bb[2])/2), int((bb[1]+bb[3])/2))
" "${LAYERS[$i]}")
    AX=${ANCHOR%% *}; AY=${ANCHOR##* }
    sep=","; [ "$i" -eq "$N" ] && sep=""
    printf '    { "src": "%s", "name": "%s", "beat": %s, "from": "%s", "tilt": %s, "anchor": [%s, %s] }%s\n' \
      "${LAYERS[$i]}" "$base" "${BEATS[$i]}" "$dir" "$tilt" "$AX" "$AY" "$sep"
  done
  echo '  ],'
  echo "  \"outPath\": \"$RAW\""
  echo '}'
} > "$JOB"

# --- AE build + lossless render ----------------------------------------------
: > "$PIPE/Scripts/animstatic-log.txt"
rm -f "$RAW"
echo "Building comp in After Effects..."
osascript <<APPLESCRIPT
with timeout of 900 seconds
    tell application "Adobe After Effects 2026"
        activate
        DoScript "\$.evalFile(File('$PIPE/Scripts/carr_animated_static.jsx'))"
    end tell
end timeout
APPLESCRIPT

echo "--- AE log:"; cat "$PIPE/Scripts/animstatic-log.txt"
[ -s "$RAW" ] || { echo "AE render missing" >&2; exit 1; }

# --- audio: one quiet tick per landing ---------------------------------------
# The tick fires on the LANDING pose (beat + 3 steps), not on first appearance,
# so the sound matches the frame the element actually stops on.
LAND_OFFSET=$(echo "$STEP_FRAMES $FPS" | awk '{printf "%.4f", ($1*3)/$2}')

typeset -a IN_ARGS FILTERS MIXIN
for (( i=1; i<=N; i++ )); do
  IN_ARGS+=(-i "$TICK")
  ms=$(echo "${BEATS[$i]} $LAND_OFFSET" | awk '{printf "%d", ($1+$2)*1000}')
  FILTERS+=("[${i}:a]adelay=${ms}|${ms},volume=0.30[t${i}]")
  MIXIN+="[t${i}]"
done
NMIX=$N

if [ -n "${MUSIC:-}" ] && [ -f "$MUSIC" ]; then
  IN_ARGS+=(-i "$MUSIC")
  MI=$((N+1))
  FILTERS+=("[${MI}:a]atrim=0:${DUR},loudnorm=I=-26:TP=-3:LRA=7,afade=t=in:d=0.5,afade=t=out:st=$(echo "$DUR"|awk '{printf "%.2f",$1-1.2}'):d=1.2[mus]")
  MIXIN+="[mus]"
  NMIX=$((N+1))
  echo "music bed: $(basename "$MUSIC")"
fi

FC="${(j:;:)FILTERS};${MIXIN}amix=inputs=${NMIX}:normalize=0,alimiter=limit=0.89[aout]"

echo "Finishing: H.264 + landing SFX..."
$FF -y -nostdin -i "$RAW" "${IN_ARGS[@]}" \
  -filter_complex "$FC" \
  -map 0:v -map "[aout]" \
  -c:v libx264 -preset medium -crf 18 -pix_fmt yuv420p -movflags +faststart \
  -c:a aac -b:a 192k -ar 48000 -t "$DUR" \
  "$FINAL" 2>&1 | tail -2

# --- GIF (email, landing pages, muted autoplay placements) -------------------
# Stepped motion is genuinely better suited to a GIF than smooth footage is:
# 10 poses/sec means the palette only has to survive 10 distinct frames a second.
echo "Building GIF..."
PAL="$PIPE/03_Output/.${NAME}_palette.png"
$FF -y -nostdin -i "$FINAL" -vf "fps=15,scale=${GIF_WIDTH}:-1:flags=lanczos,palettegen=stats_mode=diff" "$PAL" 2>/dev/null
$FF -y -nostdin -i "$FINAL" -i "$PAL" \
  -lavfi "fps=15,scale=${GIF_WIDTH}:-1:flags=lanczos[x];[x][1:v]paletteuse=dither=bayer:bayer_scale=3:diff_mode=rectangle" \
  "$GIF" 2>/dev/null
rm -f "$PAL"

echo "OK: $FINAL"
echo "OK: $GIF  ($(du -h "$GIF" | cut -f1))"
