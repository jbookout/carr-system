# doc-convo — the Doc conversation loop (v0, desk)

Push-to-talk voice conversation with Doc at the Mac, per the feasibility
council's v1 cut list (bake-off order execution log, 2026-08-07) and the
dialogue-mode Ten Laws. Build loop: #250.

## The pipeline (engine vs front end — the phone fronts this same engine later)

    convo.sh (front end: terminal push-to-talk)
      ├─ mic capture        ffmpeg avfoundation, 16kHz mono
      ├─ ack                assets/earcon-ack.wav, plays at release (<300ms law)
      ├─ ears               resident whisper-server large-v3-turbo + CARR vocab
      │                     (whisper-cli fallback if the server fails)
      ├─ bridge             cached frozen-voice phrase while the brain works
      ├─ brain              claude -p (resumed session) + prompt/preamble.md
      │                     + assets/hot-context.md snapshot — NO live tools in v0;
      │                     reads come from the snapshot (council cut list),
      │                     writes are refused aloud (confirm-gated)
      └─ mouth              bin/speak.py, three tiers:
                              1. phrase cache hit (frozen voice, instant)
                              2. live render via .venv-tts (Chatterbox clone of
                                 tools/doc-voice/reference/doc-identity-reference.wav,
                                 RECIPE settings + mastering chain) — SLOW on the
                                 M1 Pro (~0.25x realtime; a 2-sentence reply
                                 renders in ~20-40s). Honest v0 cadence.
                              3. text-only fallback (reply always prints regardless)

## Setup (one-time)

1. `bin/make-earcon.sh` — generates the ack earcon.
2. `bin/setup-tts-env.sh` — builds `.venv-tts` (python3.12 + chatterbox). ~10 min.
3. `bin/render-bridges.sh` — pre-renders the bridge phrases in Doc's frozen
   voice (needs the venv; uses roll-and-screen per doc-voice/RECIPE.md).
4. `bin/refresh-context.sh` — pulls the hot-context snapshot (probe token,
   reads only, server-side locked). Re-run any time; convo.sh runs it at start
   when the snapshot is older than 30 minutes.

## Run

    bin/convo.sh          # Enter to talk, Enter to stop, Ctrl-C to leave

First run needs mic permission for the terminal app (macOS prompts once).
Headphones-first per the council (desk-speaker echo cancellation is v2).
The panel engine resolves its microphone once at startup. After hot-plugging a
device, re-resolve it while idle with:

    curl -X POST -H 'Content-Type: application/json' \
      -d '{"action":"refresh_mic"}' http://127.0.0.1:4680/talk

## Voice harvest (bin/harvest-voice.py)

Grows the phrase cache from Joe's own real questions instead of hand-picked
bridge phrases. Mines question-shaped human messages out of a claude.ai
account export (plus a curated 40-question bank of Joe's real workflows —
deal status, critical dates, negotiation, clients, vendors, comps, judgment
calls, daily planning), asks Doc each one in character
(`convo_core.ask_brain_streaming`, real preamble + hot-context), splits every
reply into sentences, and classifies each FACT-BEARING or FACT-FREE.

Hard rule: only FACT-FREE sentences ever reach `assets/phrases/` (the live
cache). A sentence with a digit, currency symbol, month, weekday, or a
probable proper noun never gets cached — Doc always says a name or number
fresh, never replays one. `bin/test-harvest.sh` proves this. FACT-BEARING
sentences render only with `--corpus`, into `assets/harvest-corpus/`, a
directory the live engine never reads — future voice-training material, kept
separate on purpose.

    bin/harvest-voice.py --dry-run            mine + generate + classify only
    bin/harvest-voice.py                      + render frames to the cache
    bin/harvest-voice.py --corpus             + also render facts to corpus/
    bin/test-harvest.sh                       classifier safety test

All harvest inputs and outputs (Joe's real questions, Doc's real replies,
sentence splits) are local-only — see `.gitignore` — never committed; only
the pipeline code is tracked.

## Honest limits (v0, by design)

- Push-to-talk only: no wake word, no VAD endpointing, no barge-in.
- Brain answers from the snapshot; a question deeper than the snapshot gets a
  truthful "I'd need the desk for that," not a guess (Ten Laws: no fabrication).
- Live renders skip take-screening (speed); bridges get the full roll-and-screen.
- Two registers specced, neutral only implemented; urgent is a stub.
- Voice-proximity rule: v0 performs NO record writes at all, so a voice near
  the desk cannot alter records (council concern, answered structurally).
