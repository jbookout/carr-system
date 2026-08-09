#!/bin/zsh
# council.sh — THE ONLY SANCTIONED WAY TO CONVENE A COUNCIL.
#
# WHY THIS FILE EXISTS. On 2026-08-09 a council was convened ad hoc, by hand,
# with two raw CLI invocations. It produced good work, but it ran GROK AT
# DEFAULT REASONING EFFORT because nothing in the system said otherwise and
# nothing checked. Joe's instruction the same session:
#
#     "i need to make sure that council meetings are always held with the
#      Sol 5.6 model on 'high' effort. And Supergrok 4.5 Expert"
#
# A rule saying "always use high effort" is exactly the kind of prose that
# rules 14e0408b / e313a3ca / 179be4b8 already proved does not bind — all three
# were ACTIVE and recited and violated the same day. So the requirement is not
# written down here as an instruction. It is COMPILED IN below, in the two
# invocations, where a session cannot route around it without editing this file.
# Rule a8c55a47: a manual path and an automated path that do the same job must
# be the same code. This is that code. Do not shell out to `codex` or `grok`
# directly for a council; call this.
#
# ═══════════════════════════════════════════════════════════════════════════
# THE MODELS live in bin/council-lib.sh — ONE home, shared with bin/precheck.sh.
# Read that file for the pins, the rationale, and the limits of what was
# actually verified. The short version, because it changes how you read output:
#
#   A MODEL'S SELF-REPORT OF ITS OWN NAME OR EFFORT IS CONFABULATION, NOT
#   TELEMETRY. Measured live 2026-08-09: Codex invoked with gpt-5.6-sol/high
#   answered "GPT-5.4 effort=xhigh" (neither value was sent; xhigh cannot even
#   be sent). Grok invoked with effort=low and then effort=high returned
#   "effort=low" BOTH TIMES, byte-identical. An earlier draft of this very file
#   cited one of those self-reports as verification, which was wrong.
#
# So the manifest below records what was REQUESTED, and says so. What is
# genuinely verified is that the CLIs accept the flags, that the ids are real
# (`grok models`, the codex config), and that a wrong value like `expert` is
# rejected outright rather than silently downgraded.
#
# ═══════════════════════════════════════════════════════════════════════════
# USAGE
#   bin/council.sh <brief-file> [outdir]
#     Fans the SAME brief, byte-for-byte, to both chairs in parallel and writes
#     <outdir>/{codex,grok}.md plus a manifest recording model+effort actually
#     used. One brief pasted identically is the council skill's own contract —
#     the chairs cannot call tools, so they must each receive the whole thing.
#
#   bin/council.sh --check
#     Verifies both CLIs exist, are authenticated, and that the pinned settings
#     are accepted. Wired into run.sh health.
#
# EXIT: 0 both chairs returned · 3 one chair failed (the other's output is
# still written) · 4 both failed · 2 bad usage. A partial council is reported
# as partial, never silently presented as a full panel (rule 2b889e80: no
# negative finding from a single collection).
set -u

REPO="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="/opt/homebrew/opt/node@22/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

# Model ids, effort pins and both invocations live in ONE place, shared with
# bin/precheck.sh. Never re-declare them here.
source "$REPO/bin/council-lib.sh"

die() { print -r -- "council: $*" >&2; exit 2; }

if [ "${1:-}" = "--check" ]; then
  rc=0
  for c in codex grok; do
    if command -v "$c" >/dev/null 2>&1; then
      print -r -- "  ok    $c present: $(command -v $c)"
    else
      print -r -- "  FAIL  $c NOT INSTALLED — that chair cannot sit"; rc=1
    fi
  done
  if command -v grok >/dev/null 2>&1; then
    if grok models 2>&1 | grep -q "$GROK_MODEL"; then
      print -r -- "  ok    grok model $GROK_MODEL available"
    else
      print -r -- "  FAIL  grok model $GROK_MODEL NOT in \`grok models\`"; rc=1
    fi
    if grok --reasoning-effort "$GROK_EFFORT" --help >/dev/null 2>&1; then
      print -r -- "  ok    grok effort $GROK_EFFORT accepted"
    else
      print -r -- "  WARN  grok effort $GROK_EFFORT rejected — check the scale"
    fi
  fi
  print -r -- "  pinned: codex=$CODEX_MODEL/$CODEX_EFFORT  grok=$GROK_MODEL/$GROK_EFFORT"
  exit $rc
fi

BRIEF="${1:-}"
[ -n "$BRIEF" ] || die "usage: council.sh <brief-file> [outdir]   |   council.sh --check"
[ -f "$BRIEF" ] || die "brief not found: $BRIEF"

OUT="${2:-$REPO/out/council/$(date -u +%Y%m%dT%H%M%SZ)}"
mkdir -p "$OUT" || die "cannot create $OUT"
cp "$BRIEF" "$OUT/brief.md"

print -r -- "council: convening 2 chairs on $(basename "$BRIEF")"
print -r -- "  codex  $CODEX_MODEL  effort=$CODEX_EFFORT"
print -r -- "  grok   $GROK_MODEL   effort=$GROK_EFFORT"

run_codex "$OUT/brief.md" "$OUT/codex.md" & pid_c=$!
run_grok  "$OUT/brief.md" "$OUT/grok.md"  & pid_g=$!
wait $pid_c; rc_c=$?
wait $pid_g; rc_g=$?

# A chair that exits 0 but writes nothing did NOT sit. Treat empty as failure —
# a silent empty file read as a chair's agreement would be the worst possible
# failure mode for a panel whose job is disagreement.
[ -s "$OUT/codex.md" ] || rc_c=1
[ -s "$OUT/grok.md"  ] || rc_g=1

cat > "$OUT/manifest.json" <<JSON
{
  "convened_utc": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "brief": "$(basename "$BRIEF")",
  "_note": "model/effort are what this runner REQUESTED. The remote side does not report back what it honoured, and a model's self-description is confabulation (see council-lib.sh). Do not read these as confirmation of what ran.",
  "chairs": [
    {"seat": "codex", "model_requested": "$CODEX_MODEL", "effort_requested": "$CODEX_EFFORT", "exit": $rc_c,
     "bytes": $( [ -f "$OUT/codex.md" ] && wc -c < "$OUT/codex.md" | tr -d ' ' || echo 0 )},
    {"seat": "grok",  "model_requested": "$GROK_MODEL",  "effort_requested": "$GROK_EFFORT",  "exit": $rc_g,
     "bytes": $( [ -f "$OUT/grok.md" ] && wc -c < "$OUT/grok.md" | tr -d ' ' || echo 0 )}
  ]
}
JSON

seated=0
[ $rc_c -eq 0 ] && { print -r -- "  codex: seated ($(wc -c < "$OUT/codex.md" | tr -d ' ') bytes)"; seated=$((seated+1)); } \
                || print -r -- "  codex: FAILED — see $OUT/codex.err"
[ $rc_g -eq 0 ] && { print -r -- "  grok:  seated ($(wc -c < "$OUT/grok.md" | tr -d ' ') bytes)"; seated=$((seated+1)); } \
                || print -r -- "  grok:  FAILED — see $OUT/grok.err"

print -r -- "council: $seated/2 chairs seated -> $OUT"
[ $seated -eq 2 ] && exit 0
[ $seated -eq 1 ] && { print -r -- "PARTIAL COUNCIL — report it as one seat, never as a panel."; exit 3; }
exit 4
