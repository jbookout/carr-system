# Doc's voice — FROZEN 2026-08-07 (Joe's freeze, same night as the bake-off)

Doc's voice is a designed synthetic voice with NO real-person referent (the legal
line, dr-cre-voice-doctrine §3). It was chosen by Joe's ear through ~30 iterations
on 2026-08-07 and is permanent: a new voice is a new being. Target ruled by Joe:
a soothing, natural ROBOT — JARVIS-class machine intelligence, never a human
impersonation. Architecture ruled by Joe ("we want 3. full jarvis"): directed
delivery on a locked identity.

## The frozen assets (reference/)

- `doc-identity-reference.wav` — THE identity anchor. Qwen3-TTS VoiceDesign render
  ("take 2"), from the v8 character brief (preserved in
  `CARR AI/DNA/Deal Management/record-layer/fable-order-voice-bakeoff-2026-08-01.md`
  execution log). Every future render clones from this file. Never regenerate it.
- `doc-master-intro-read.wav` — the approved master read of the self-introduction
  (Chatterbox clone of the identity, exaggeration 0.40, cfg_weight 0.60, corrected
  pronunciation text). Raw, pre-mastering.
- `doc-master-intro-FINAL.wav` — the same read through the frozen mastering chain:
  what Doc actually sounds like.

## The mastering chain (Joe's dialed settings, final)

ffmpeg -af "asetrate=24000*0.97,aresample=24000,atempo=0.965, highpass=f=90,
lowpass=f=7400, aresample=16000, aresample=24000, acrusher=bits=14:mode=log:mix=0.08,
equalizer=f=185:t=q:w=1.4:g=6.5, equalizer=f=4400:t=q:w=1.2:g=3,
acompressor=threshold=-16dB:ratio=2.4:attack=8:release=115:makeup=2dB,
aecho=0.75:0.23:21|36:0.10|0.07, alimiter=limit=0.9"

In words: pitch down 3%, pace eased ~3.5%, clean-machine texture (light 16k
round-trip + trace bit-crush), warm chest (+6.5dB @ 185Hz) with bright presence
(+3dB @ 4.4kHz), balanced punch (2.4:1), moderate room. Punch is a PER-SURFACE
dial: desk can run softer, truck/phone punchier — the chain runs at render time.

## Speakable forms (grows with every correction from Joe's ear)

- "real estate" → "realestate" (one word, stress on real)
- "CARR" → "car"

## Delivery screening (mandatory on every rendered phrase)

Roll N takes per sentence, score terminal f0 slope (librosa pyin, last ~25 voiced
frames per phrase; see bin/screen-endings-reference-implementation.py), reject
rising finals. Falling, settled endings are Doc's signature (and doctrine law —
no upspeak on statements).

## Render paths

- TODAY (proven): Chatterbox clone from the identity reference
  (audio_prompt_path), exaggeration ~0.40 / cfg 0.60, then screening, then the
  mastering chain.
- TARGET ("full jarvis", Joe 2026-08-07): base Qwen3-TTS ICL — identity from
  ref_audio + per-line delivery instructions ("with quiet concern") for the four
  doctrine registers. Test result lands in the bake-off order's execution log;
  adopt as primary render path if identity holds.

## Iteration rules (from decision 0e192fcb — do not relearn these)

Character briefs are dead: never re-describe Doc from words (that re-rolls the
actor). Identity comes ONLY from the reference wav. Delivery varies per render —
roll and screen. Mastering dials are deterministic and always safe. Every render
Joe approves gets SAVED before it is played.
