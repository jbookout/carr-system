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
- "LOI" → "Ell-Oeye" (CHOSEN by Joe's ear, 2026-08-10, A/B/C/D/E on the API).
  Hyphen, capital E on the first half. Joe's target: "it should sound like
  'ell oh eye' except said as 'elloheye' like it rolls off the tongue fluidly
  and smoothly ... similar to how L M N O P in the alphabet song gets pushed
  together LMNOP."
  THE SIX DEAD ENDS, recorded so nobody walks them again:
    "L O I"     spaces  -> the engine drawls the letters apart
    "loy"       word    -> simply wrong, not the term
    "L.O.I."    periods -> says the letters, but too SLOW, and Joe's finding is
                           the important one: the slowness "really exposes that
                           the voice is not real". A correct-but-slow acronym is
                           an uncanny-valley tell, not a cosmetic issue.
    "elloheye"  h       -> intrusive H, lands as "ell oh HIGH"
    "elloeye"   joined  -> "the emphasis is just weird"
    "Ell Oeye"  space   -> right shape, but "the gap is too much between the
                           words"
  THE LESSON UNDER IT: the two halves need BINDING, not JOINING. A space leaves
  a gap, full joining re-stresses the syllables; the hyphen binds them without
  a pause. "ElOeye" (camel, no separator) was also tested and lost.
- "close" in the transaction sense → "closing". Joe, 2026-08-10: "this should be
  the version that is 'close the door' not 'we are close to finishing'". In
  "close by September thirtieth" the engine picks the adjective (nearby) rather
  than the verb. Prefer "closing by"; where a noun is unavoidable, rewrite the
  sentence rather than trusting the engine to guess.

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
- ELEVENLABS (identity confirmed 2026-08-10, Joe: "yea thats him"). Voice "Doc",
  an Instant Voice Clone of 3m07s of the RAW corpus. Professional Voice Cloning
  is permanently closed to Doc — 30-minute floor against our 15.9, and it demands
  the account holder record a verification read matching the samples, which no
  human can do for a synthetic register. Full reasoning: decision 3ccb57c6.
    Model Eleven Multilingual v2 · Stability 65% · Similarity ~87% ·
    Style Exaggeration 0 · Speaker boost ON · Speed just under the midpoint.
  SPEED IS DOC'S OWN NUMBER, NOT THE DOCTRINE'S. The doctrine's medium-slow
  target overshot — Joe: "it dragged just a little bit, maybe somewhere in
  between the two values". Same 837-character passage: 0:56 default, 1:00
  doctrine-slow, 0:58 adopted.
  Background-noise removal OFF at clone time: the source is a synthetic render
  with no noise, so denoising could only smear the timbre being preserved.
  OUTPUT IS MP3 128kbps AND CANNOT BE BETTER ON STARTER — every lossless format
  is Pro+ ($99/mo). This bites when the mastering chain is re-dialled: the chain
  lifts +6.5dB at 185Hz and +3dB at 4.4kHz, which amplifies codec artefacts along
  with Doc. Re-dial by ear against THIS engine's output; never paste the
  Chatterbox chain on unchanged.

  ELEVEN V3 WAS TESTED AND REJECTED, 2026-08-10 — do not retry it blind.
  It is the obvious candidate, because it takes per-line audio tags and that is
  exactly the "full jarvis" architecture (identity locked, delivery directed) and
  the honest fix for the four doctrine registers. Two things killed it:
    PACE. v3 has NO speed control — the panel offers only Stability on a
    Creative/Robust axis. The same passage ran 0:43-0:44 on v3 against 0:56-0:58
    on v2, roughly 25% faster, and Joe's verdict both times was "way too fast".
    A [slowly] tag PARSED (the editor highlighted it) and changed nothing:
    0:44 untagged, 0:43 tagged. The tag layer controls emotion, not tempo.
    Doc is "never in a hurry, and that is audible" — that is character, not
    preference, so an engine that cannot be slowed cannot carry him.
  Two things worth remembering FOR v3, if it ever gains a speed control:
    [serious] and [slowly] both parse, and v3 returns TWO generations per run —
    native take-rolling, which is the screening step this recipe already wants.
  Also: switching model to v3 SILENTLY SWAPPED THE VOICE to a stock "Liam".
  Check the voice after any model change; a session that did not would have
  rendered a stranger and blamed the clone.
  Corroboration, found after the call was made: ElevenLabs' own model picker
  marks Multilingual v2 "Recommended for Doc".
  NOT yet tested: Eleven Flash v2.5, the ultra-low-latency model. That is the
  one that matters for the live path, since the doctrine requires P95 latency
  measured on a real phone before any provider is adopted.

## Iteration rules (from decision 0e192fcb — do not relearn these)

Character briefs are dead: never re-describe Doc from words (that re-rolls the
actor). Identity comes ONLY from the reference wav. Delivery varies per render —
roll and screen. Mastering dials are deterministic and always safe. Every render
Joe approves gets SAVED before it is played.
