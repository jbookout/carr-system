# dictation-rig — local meeting capture + transcription on our own whisper

The CARR dictation rig. Phase A: meeting mode. Phase B: desk dictation
(quill-dictate). Spec and rulings:
`CARR AI/00_Context/idea-inbox/2026-07-30-local-dictation-tool-note.md` (five addenda);
build plan: `specs/dictation-rig-build-plan-2026-08-07.md`; Phase B ruled design:
loop #243 / decision f799fd49.

## Two modes, one hard boundary

- **Meeting mode** (quill, Phase A): a counterparty exists, so the consent
  announcement is structural and non-negotiable.
- **Desk dictation** (quill-dictate, Phase B): Joe speaking to his own machine,
  no counterparty, so there is NO consent announcement — and the two modes stay
  visibly distinct (separate binaries, separate menu-bar icons, separate
  launchd labels) precisely so that boundary stays true. The privacy property
  of dictation mode is structural too: the mic is live only while a key is
  physically held.

Both modes share ONE transcription engine: `/opt/homebrew/bin/whisper-cli`,
ggml-large-v3-turbo, `vocab-prompt.txt`. Never a second engine.

## Phase B — quill-dictate (system-wide dictation to the active text box)

`dictate/` — own Swift package (menu-bar agent, no dependency on vendor/quill).
One gesture set, both keyboards (decision f799fd49; trigger emissions
live-corrected 2026-08-07):

- **HOLD the trigger key** = push-to-talk: speak while held, release, text
  lands at the cursor of whatever app has focus. Trigger keys: **right-cmd
  (54)** on the MacBook, **right-ctrl (62)** on the Logitech — live capture
  showed the Logitech emits NO true right-cmd (its right side sends
  rctrl/ropt/left-cmd), so the ruling's "one key" became one *position* (the
  key right of space), absorbed by the config exactly as the ruling intended.
- **DOUBLE-TAP the trigger** = toggle conversation mode; inside it, **hold
  space** speaks, release disengages. A QUICK space tap types a normal space
  (replayed synthetically), so typing keeps working inside the mode; **Esc
  always exits it**. The menu-bar icon changes and an audible cue marks
  entry/exit. Space with modifiers passes through.
- Trigger flagsChanged events are CONSUMED — the agent owns those keys.
  Required because Siri's "press either cmd twice" shortcut kept firing on
  the double-tap regardless of the Settings dropdown (live, 2026-08-07).
  Trigger+key chords still reach apps (keyDowns carry their own flags), and
  any other key pressed during a hold aborts capture into an ordinary
  shortcut. Normal typing and left-hand shortcuts are untouched.

While a capture is held, a **live preview panel** shows words as they are
heard (resident whisper-server on small.en, ~1s cadence), anchored at the
text caret of the focused app — or at the focused element's frame when the
app reports no usable caret (Electron apps report degenerate bounds; the
locator flips their AX tree on via AXManualAccessibility first). The final
insert always comes from the full-accuracy large-v3-turbo pass; the preview
is strictly additive and any preview failure is silent. Tune or disable via
`live_preview`, `preview_interval_ms`, `preview_window_seconds` in config.

Trigger keys live in `~/.config/quill-dictate/config.json`
(`trigger_key_codes`, a list — one entry per keyboard; a future keyboard is a
one-line change).
Insertion is clipboard-paste (saved string restored 2s later; a non-string
clipboard — image, file — is not resurrected, known v1 limit) or `"type"` mode
for synthetic keystrokes. A silence gate (`min_peak_level`, min 0.35s) stops
whisper hallucinating text onto an empty press. Apple's built-in dictation was
verified never-enabled on this Mac (no `Dictation Enabled` key), so nothing
collides with the trigger.

Build: `bin/build-dictate.sh` (same toolchain guard as quill — see Build).
Deploy: `bin/install-dictate.sh` (config + launchd agent
`com.carr.quill-dictate`; `--config-only` skips launchd). Diagnostics:
`quill-dictate doctor` (headless health check) and
`quill-dictate transcribe <wav>` (engine test without keyboard or permissions).
Permissions, one-time, per binary identity: Accessibility (event tap + paste)
and Microphone — a rebuild changes the code hash and macOS may re-ask; that is
TCC working, not a defect. Logs: `~/Library/Logs/quill-dictate.log`.

## Shape (Phase A: meeting mode)

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
