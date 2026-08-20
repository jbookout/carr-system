#!/bin/zsh
# make-animated-static.sh — turn a static you already run into a stop-motion build.
# Video for the feed, GIF for email and landing pages.
#
#   make-animated-static.sh <layers-folder> [outname] [--concept KEY] [--sfx NAME]
#                           [--avoid N] [--dry-run]
#   make-animated-static.sh --list          # concepts + landing sounds
#
# The choreography is NOT fixed. plan-animated-static.py picks the build order,
# entrance directions, cadence, duration and landing sound by reading the
# choreography log and avoiding whatever ran recently. Joe's rule, 2026-07-25:
# the format's advantage is novelty, and a recognizable house template spends
# that novelty by about the third post. Defaults are what produce sameness, so
# there are none — every run makes a choice and records it.
#
# The layers folder holds one full-canvas PNG per element, named in STACKING
# order (bottom first):
#   01_background.png  02_kicker.png  03_head1.png  04_rule.png  05_logo.png
# All PNGs must be the same pixel size, with transparency around the element.
# That is the whole input contract: because you AUTHORED the static, the layers
# already exist. Nothing here has to segment a flat JPG.
#
# Stacking order is always filename order. The BEAT order is the concept's
# business. A filename suffix pins one layer's entrance if you need it:
#   03_head1_left.png  05_logo_scale.png   (below | above | left | right | scale)
#
# Audio: ONE sound for the whole piece, quiet, airy (the CRITIQUE-LOG v3 rule).
# The sound varies BETWEEN pieces, never inside one. Music bed is opt-in:
#   MUSIC="$AUD/music/close-up.mp3" make-animated-static.sh ...

set -e
PIPE="${CARR_VIDEO_PIPE:-$HOME/Movies/CARR Video Pipeline}"
REPO="$(cd "$(dirname "$0")" && pwd)"
LOG="$PIPE/choreography-log.tsv"
FF=/opt/homebrew/bin/ffmpeg
PLANNER="$REPO/plan-animated-static.py"

if [ "${1:-}" = "--list" ]; then exec python3 "$PLANNER" --list; fi

LAYERDIR="${1:?usage: make-animated-static.sh <layers-folder> [outname] [--concept K] [--sfx N] [--dry-run]}"
shift
NAME="animated_static"
case "${1:-}" in -*|"") ;; *) NAME="$1"; shift ;; esac

PLANARGS=(); RECOVERY_ARGS=(); DRY=0; EMAIL=0
while [ $# -gt 0 ]; do
  case "$1" in
    --concept|--sfx|--avoid) PLANARGS+=("$1" "$2"); shift 2 ;;
    --reason|--vault) RECOVERY_ARGS+=("$1" "$2"); shift 2 ;;
    --recovery) RECOVERY_ARGS+=("$1"); shift ;;
    --dry-run) DRY=1; shift ;;
    --email) EMAIL=1; shift ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

[ -d "$LAYERDIR" ] || { echo "no such layers folder: $LAYERDIR" >&2; exit 1; }

RAW="$PIPE/03_Output/${NAME}_raw.mov"
FINAL="$PIPE/03_Output/${NAME}.mp4"
GIF="$PIPE/03_Output/${NAME}.gif"
JOB="$PIPE/Scripts/animstatic-job.json"
PLAN="$PIPE/Scripts/animstatic-plan.env"

# --- plan the choreography ----------------------------------------------------
python3 "$PLANNER" "$LAYERDIR" "$LOG" --name "$NAME" --out-path "$RAW" \
  --json-out "$JOB" --plan-out "$PLAN" "${PLANARGS[@]}" "${RECOVERY_ARGS[@]}"
source "$PLAN"
GIF_WIDTH="${GIF_WIDTH:-640}"
echo "canvas $CANVAS · hero '$HERO'"

if [ "$DRY" -eq 1 ]; then
  echo "(dry run — nothing rendered, nothing logged)"
  exit 0
fi
[ -f "$SFX_FILE" ] || { echo "landing SFX missing: $SFX_FILE" >&2; exit 1; }

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

# --- audio: one quiet landing sound, fired on the frame each element stops ----
typeset -a IN_ARGS FILTERS
MIXIN=""; k=0
for t in ${=LANDINGS}; do
  k=$((k+1))
  IN_ARGS+=(-i "$SFX_FILE")
  ms=$(echo "$t" | awk '{printf "%d", $1*1000}')
  FILTERS+=("[${k}:a]adelay=${ms}|${ms},volume=${SFX_VOL}[t${k}]")
  MIXIN+="[t${k}]"
done
NMIX=$k

if [ -n "${MUSIC:-}" ] && [ -f "$MUSIC" ]; then
  IN_ARGS+=(-i "$MUSIC")
  MI=$((k+1))
  FADE=$(echo "$DUR" | awk '{printf "%.2f", $1-1.2}')
  FILTERS+=("[${MI}:a]atrim=0:${DUR},loudnorm=I=-26:TP=-3:LRA=7,afade=t=in:d=0.5,afade=t=out:st=${FADE}:d=1.2[mus]")
  MIXIN+="[mus]"
  NMIX=$((k+1))
  echo "music bed: $(basename "$MUSIC")"
fi

FC="${(j:;:)FILTERS};${MIXIN}amix=inputs=${NMIX}:normalize=0,alimiter=limit=0.89[aout]"

echo "Finishing: H.264 + $SFX_KEY landings..."
$FF -y -nostdin -i "$RAW" "${IN_ARGS[@]}" \
  -filter_complex "$FC" \
  -map 0:v -map "[aout]" \
  -c:v libx264 -preset medium -crf 18 -pix_fmt yuv420p -movflags +faststart \
  -c:a aac -b:a 192k -ar 48000 -t "$DUR" \
  "$FINAL" 2>&1 | tail -2

# --- GIF (email, landing pages, muted autoplay placements) -------------------
# Stepped motion suits a GIF better than smooth footage does: a coarse cadence
# means the palette only has to hold a few distinct frames a second.
echo "Building GIF..."
PAL="$PIPE/03_Output/.${NAME}_palette.png"
$FF -y -nostdin -i "$FINAL" -vf "fps=15,scale=${GIF_WIDTH}:-1:flags=lanczos,palettegen=stats_mode=diff" "$PAL" 2>/dev/null
$FF -y -nostdin -i "$FINAL" -i "$PAL" \
  -lavfi "fps=15,scale=${GIF_WIDTH}:-1:flags=lanczos[x];[x][1:v]paletteuse=dither=bayer:bayer_scale=3:diff_mode=rectangle" \
  "$GIF" 2>/dev/null
rm -f "$PAL"

# --- email variant (--email) -------------------------------------------------
# Legacy Outlook on Windows renders only the FIRST FRAME of a GIF as a static
# image (modern Outlook 365, Outlook Mac, web and mobile all animate). The
# standing email-design rule is that the key information must live on frame one
# — which a build-on animation violates by construction, since frame one is an
# empty canvas. So the email cut leads with the FINISHED card for a single
# frame: legacy Outlook shows the complete, readable card, and everywhere else
# that frame is one 15fps tick before the build starts.
# It also does not loop. A build repeating forever in an inbox is irritating;
# this plays once and rests on the finished card.
if [ "$EMAIL" -eq 1 ]; then
  echo "Building email GIF (Outlook-safe first frame, no loop)..."
  EGIF="$PIPE/03_Output/${NAME}_email.gif"
  EW="${EMAIL_WIDTH:-600}"
  LAST="$PIPE/03_Output/.${NAME}_last.png"
  $FF -y -nostdin -sseof -0.2 -i "$FINAL" -frames:v 1 "$LAST" 2>/dev/null
  $FF -y -nostdin -loop 1 -t 0.07 -i "$LAST" -i "$FINAL" -filter_complex "\
[0:v]fps=15,scale=${EW}:-1:flags=lanczos,setsar=1[a];\
[1:v]fps=15,scale=${EW}:-1:flags=lanczos,setsar=1[b];\
[a][b]concat=n=2:v=1[c];[c]split[s0][s1];\
[s0]palettegen=stats_mode=diff[p];\
[s1][p]paletteuse=dither=bayer:bayer_scale=3:diff_mode=rectangle" \
    -loop -1 "$EGIF" 2>/dev/null
  rm -f "$LAST"
  ESIZE=$(du -k "$EGIF" | cut -f1)
  echo "built pending choreography commit: $EGIF  (${ESIZE}K)"
  [ "$ESIZE" -gt 1024 ] && echo "  WARNING: over 1MB — drop EMAIL_WIDTH or shorten the build before sending."
fi

# Only now does the concept get burned — a failed render must not consume one.
if ! python3 "$PLANNER" "$LAYERDIR" "$LOG" --name "$NAME" --out-path "$RAW" \
  --concept "$CONCEPT" --sfx "$SFX_KEY" --commit "${RECOVERY_ARGS[@]}" >/dev/null; then
  rm -f "$FINAL" "$GIF" "${EGIF:-}"
  echo "animated-static: choreography commit failed; removed uncommitted outputs" >&2
  exit 1
fi

echo "OK: $FINAL"
echo "OK: $GIF  ($(du -h "$GIF" | cut -f1))"
[ "$EMAIL" -eq 0 ] || echo "OK: $EGIF  ($(du -h "$EGIF" | cut -f1))"
echo "logged: $CONCEPT / $SFX_KEY -> $(basename "$LOG")"
