#!/bin/zsh
# council-lib.sh — the ONE home for outside-model pins and invocations.
#
# Sourced by bin/council.sh (the panel) and bin/precheck.sh (the single-seat
# reviewer). Two copies of a model id and an effort flag is exactly the drift
# that let a council run at DEFAULT effort on 2026-08-09 while everyone assumed
# otherwise. Rule a8c55a47: a manual path and an automated path that do the same
# job must be the same code. Change a pin HERE and nowhere else.
#
# ── WHAT "VERIFIED" CAN AND CANNOT MEAN FOR THESE PINS ───────────────────────
# ASKING A MODEL ITS OWN NAME OR EFFORT IS CONFABULATION, NOT TELEMETRY.
# Measured live 2026-08-09, one sitting, this machine:
#   - Codex invoked with model=gpt-5.6-sol, effort=high self-reported
#     "GPT-5.4 effort=xhigh". Neither value was sent; xhigh cannot even be sent.
#     An earlier probe self-reported "GPT-5.4" while running on the Sol config,
#     and that wrong answer was briefly reported to Joe as fact.
#   - Grok invoked with --reasoning-effort low, then high, returned "effort=low"
#     BOTH TIMES — byte-identical answers for opposite flags. A latency probe
#     showed 83s vs 91s on one sample, inside noise.
# So: the flags are VERIFIED ACCEPTED (a wrong value like `expert` hard-errors)
# and the ids are VERIFIED REAL (`grok models`, ~/.codex/config.toml). Whether
# the remote side HONOURS the effort is NOT verified, and nothing here claims it
# is. Manifests say "requested", never "used".
#
# ── BILLING, checked 2026-08-09 ──────────────────────────────────────────────
# Both CLIs run on SUBSCRIPTION auth, not metered API keys. ~/.codex/auth.json
# is auth_mode=chatgpt with OPENAI_API_KEY null; grok reports "logged in with
# grok.com". No OPENAI_API_KEY or XAI_API_KEY in the environment. If an API
# charge ever appears, something changed and it should be investigated, not
# assumed normal.

# ── COUNCIL TIER — a panel reasoning about an open design question ───────────
# Joe, 2026-08-09: "council meetings are always held with the Sol 5.6 model on
# 'high' effort. And Supergrok 4.5 Expert."
#
# CODEX / "Sol 5.6" -> gpt-5.6-sol. `gpt-5.6` and `sol-5.6` both 400 on a
# ChatGPT account. Flags passed explicitly even though ~/.codex/config.toml sets
# them, because that file is machine-local and untracked — Dell's machine has
# none, and a council that silently downgrades elsewhere is the failure this
# file exists to prevent.
CODEX_MODEL="gpt-5.6-sol"
CODEX_EFFORT="high"

# GROK / "SuperGrok 4.5 Expert" -> grok-4.5 at high. IMPORTANT: the CLI accepts
# ONLY high | medium | low; `expert` is rejected outright. "SuperGrok Expert" is
# the grok.com ACCOUNT TIER that entitles the session, not a per-call setting,
# and `grok models` lists exactly one model. So `high` is the top of the CLI's
# scale and is what Joe's instruction resolves to. If xAI ever exposes an expert
# effort or model id, change it here.
GROK_MODEL="grok-4.5"
GROK_EFFORT="high"

# ── PRECHECK TIER — deliberately NOT the council tier ────────────────────────
# Joe, 2026-08-09: "i don't think sol 5.6 at high effort is necessary for all
# tasks, only for council stuff. its the equivalent of fable 5 and youre wasting
# it on something that could be handled by a lower model."
#
# He is right, and the measurement is brutal: the first precheck run — ONE
# one-page LOI, asked to find defects and judge a single classification — ran
# 33 MINUTES 57 SECONDS at council tier and was killed without returning. That
# is document triage against an explicit rubric, not open-ended reasoning. A
# pre-clear step meant to SHRINK Joe's queue cannot take half an hour; at that
# speed it makes the queue worse.
#
# Mirrors rule fb110a39 (Fable reserved for its best use cases) and the cost-tier
# half of 185013c6 (outside models are a delegation pool; tier is chosen per
# job). Model stays gpt-5.6-sol because it is what this account supports; EFFORT
# is the dial.
PRECHECK_MODEL="gpt-5.6-sol"
PRECHECK_EFFORT="low"

# run_codex <brief> <out>          — council tier
run_codex() {
  codex exec --sandbox read-only --skip-git-repo-check \
    -c model="\"${CODEX_MODEL}\"" \
    -c model_reasoning_effort="\"${CODEX_EFFORT}\"" \
    "$(cat "$1")" > "$2" 2>"${2:r}.err"
}

# run_grok <brief> <out>           — council tier
run_grok() {
  grok --sandbox read-only \
    --model "$GROK_MODEL" --reasoning-effort "$GROK_EFFORT" --print \
    "$(cat "$1")" > "$2" 2>"${2:r}.err"
}

# run_precheck <brief> <out>       — triage tier, NOT the council tier
run_precheck() {
  codex exec --sandbox read-only --skip-git-repo-check \
    -c model="\"${PRECHECK_MODEL}\"" \
    -c model_reasoning_effort="\"${PRECHECK_EFFORT}\"" \
    "$(cat "$1")" > "$2" 2>"${2:r}.err"
}
