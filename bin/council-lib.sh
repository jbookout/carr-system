#!/bin/zsh
# council-lib.sh — THE ONE HOME FOR OUTSIDE-MODEL PINS AND INVOCATIONS.
#
# Sourced by bin/council.sh (the panel) and bin/precheck.sh (the single-seat
# independent reviewer). Both call outside models; both must call them the same
# way. Two copies of a model id and an effort flag is exactly the drift that let
# a council run at DEFAULT effort on 2026-08-09 while everyone assumed otherwise
# (rule a8c55a47: a manual path and an automated path that do the same job must
# be the same code; rule 0f38532e: one home per fact).
#
# Change a pin HERE and nowhere else.
#
# ── WHAT "VERIFIED" MEANS FOR THESE PINS, AND WHAT IT CANNOT MEAN ────────────
# ASKING A MODEL ITS OWN NAME OR EFFORT IS NOT EVIDENCE. Measured live
# 2026-08-09, all on this machine, all in one sitting:
#   - Codex, invoked with model=gpt-5.6-sol and effort=high, self-reported
#     "GPT-5.4 effort=xhigh". Neither value was passed; xhigh is not even a
#     value this script can send. An earlier probe self-reported "GPT-5.4"
#     while running on the Sol config, and that wrong answer was briefly
#     reported to Joe as fact before the config was read.
#   - Grok, invoked with --reasoning-effort low and then high, returned
#     "effort=low" BOTH TIMES — byte-identical answers for opposite flags.
# So a self-report is confabulation, not telemetry. What IS verifiable:
#   (a) the CLI ACCEPTS the flag (a wrong value errors out — `expert` does),
#   (b) the model id appears in `grok models` / the codex config,
#   (c) behavioural proxies such as latency and output size under a
#       reasoning-heavy prompt.
# Rule 97326357 says a claim about a surface becomes doctrine only after a live
# test FROM that surface. The live test here proves the flags are accepted and
# the ids are real. It does NOT prove the remote side honours the effort, and
# this file does not claim it does.

# ── CODEX / "Sol 5.6" ────────────────────────────────────────────────────────
# `gpt-5.6-sol` is the id in ~/.codex/config.toml on Joe's Mac. `gpt-5.6` and
# `sol-5.6` are NOT valid and 400 on a ChatGPT account. Flags are passed
# explicitly even though config.toml already sets them, because config.toml is
# machine-local and untracked — Dell's machine has none, and a council that
# silently downgrades on another machine is the failure this file prevents.
CODEX_MODEL="gpt-5.6-sol"
CODEX_EFFORT="high"

# ── GROK / "SuperGrok 4.5 Expert" ────────────────────────────────────────────
# The CLI accepts ONLY high | medium | low; `expert` is rejected outright.
# "SuperGrok Expert" is the grok.com ACCOUNT TIER that entitles the session, not
# a per-call setting, and `grok models` lists exactly one model. So `high` is
# the top of the CLI's scale and is what Joe's instruction resolves to. If xAI
# later exposes an expert effort or model id, change it here.
GROK_MODEL="grok-4.5"
GROK_EFFORT="high"

# run_codex <brief-file> <out-file>   — stderr goes to <out>.err
run_codex() {
  local brief="$1" out="$2"
  codex exec \
    --sandbox read-only \
    --skip-git-repo-check \
    -c model="\"${CODEX_MODEL}\"" \
    -c model_reasoning_effort="\"${CODEX_EFFORT}\"" \
    "$(cat "$brief")" > "$out" 2>"${out:r}.err"
}

# run_grok <brief-file> <out-file>    — stderr goes to <out>.err
run_grok() {
  local brief="$1" out="$2"
  grok \
    --sandbox read-only \
    --model "$GROK_MODEL" \
    --reasoning-effort "$GROK_EFFORT" \
    --print \
    "$(cat "$brief")" > "$out" 2>"${out:r}.err"
}
