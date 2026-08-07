# dictation-rig — local meeting capture + transcription on our own whisper

The CARR dictation rig, Phase A: meeting mode. Spec and rulings:
`CARR AI/00_Context/idea-inbox/2026-07-30-local-dictation-tool-note.md` (five addenda);
build plan: `specs/dictation-rig-build-plan-2026-08-07.md`.

## Shape

- `vendor/quill/` — digimata/quill, pinned submodule (commit 855869e, MIT, evaluated
  2026-08-07). Does the capture: one menu-bar click records mic + all system audio as
  two separate tracks (`mic.caf` = me, `system.caf` = them) into
  `~/Recordings/<yyyy.MM.dd-HHmm>/`. We never modify its source.
- `bin/transcribe-session.sh` — quill's `on_stop` hook. Runs our whisper-cli
  (large-v3-turbo) on both tracks with the CARR vocabulary prompt, shifts each track
  by its `meta.json` offset onto one clock, merges by timestamp into a labeled
  `me`/`them` transcript. Quill's own Parakeet engine is disabled in config; the raw
  audio and the transcript both stay on this Mac.
- `vocab-prompt.txt` — the whisper `--prompt` seed: CARR nouns, platforms, market
  towns, and active client/prospect surnames. PURE PROMPT TEXT, no comments — the
  whole file is fed to the model (~224-token budget). Refresh it from the live deal
  board when the active book changes (source: the `deal-board` verb); a periodic
  refresh job is a Phase B follow-up.
- Consent layer — see below. Non-negotiable tool behavior per Addendum 5.

## Build

`bin/build-quill.sh` — the only sanctioned build path. It carries the toolchain
fix (this Mac's CommandLineTools lacks its own libc++ headers; the script points
`CPLUS_INCLUDE_PATH` at the SDK's). Never run a bare `swift build` from docs.
Never run `swift package update` in vendor/quill without a fresh security review
— Package.resolved is the locked, reviewed dependency set (FluidAudio 0.15.5,
swift-argument-parser 1.8.2, scan of 2026-08-07).

## Config (quill side)

`~/.config/quill/config.json` is deployed by `bin/install-config.sh`:
transcription disabled (we own the engine), `on_stop` pointed at
`transcribe-session.sh`.

## The consent rule (Addendum 5, non-negotiable)

Meeting mode never runs silently. Recording start triggers an audible
"Now recording" announcement; the transcript header records that it fired and when.
The announcement plays on the local output device — in person everyone hears it; on
a call the remote side hears it when audio routes through speakers, and mic pickup
carries it into the recording either way, so the session itself carries proof of
disclosure. Known limit, stated honestly: with headphones on, the remote side does
not hear the local announcement — injecting audio into the outbound call path needs
a virtual-mic loopback device, which is a Phase B item. Until then the ask-first
habit governs headphone calls, exactly as Joe already does on the phone.

Florida is all-party consent. The tool enforces the floor; Joe's ask-first habit is
the craft on top.

## Known limits (Phase A, honest)

- Whisper hallucinates on near-silent tracks (the classic "Sous-titrage Société
  Radio-Canada" on an empty channel). A real call fills the system track so this
  mostly bites test recordings; a no-speech/energy gate on segments is a Phase B
  cleanup.
- The announcement plays on the local output device. With headphones, the remote
  side does not hear it — see the consent section. Ask-first governs there.
- Jot-blend, ingest-socket landing, and calendar-linking are build-plan steps 6-8,
  not yet built.

## No third-party voiceprints — structural

Two-party attribution comes free from the channel split (mic = me, system = them).
There is no speaker-enrollment feature for anyone but Joe/Dell (self-consented,
Phase B if built at all), and no persistent voiceprint of any client or third
party, ever.
