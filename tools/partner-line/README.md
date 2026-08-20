# partner-line watcher (open idea #78, item 1)

A per-Mac daemon that watches the shared partner room (the `read-room` /
`add-room-turn` verbs behind the CARR MCP Worker — see
`mcp-server/src/partner-room.js`) and drops the OTHER partner's turns into a
live local Claude Code session, so a human does not have to go read the room
by hand to notice their partner said something.

It is symmetric: one script, both Macs run it, each watches whichever partner
is NOT the one running it — same stance as `pipelines/partner_ping.py`.

## What it does, in one paragraph

Every few seconds it calls `read-room` (through `tools/call-verb.py`, i.e. the
deployed Worker over HTTPS — it never opens a direct database connection) for
turns after a locally-stored watermark. For each new turn: if it was written
by the OTHER partner and its `kind` is `"turn"`, the watcher injects it into a
registered local Claude Code session's socket and fires a macOS notification
naming who it's from. Anything else (your own turns, `system`/`receipt` rows)
is skipped — the watermark still advances past it. Then it moves on.

## Install / config

Three small files under `~/.config/carr/`, none of which this repo can set
for you (they are per-Mac facts):

- `~/.config/carr/partner` — `joe` or `dell`. Same file `partner_ping.py`
  already reads; if it's already set for that script, the watcher reads the
  same one. `CARR_PARTNER` env var overrides it if you'd rather not write the
  file.
- `~/.config/carr/partner-line-target` — the **registered label** of the
  Claude Code session to inject into (see "Injection safety" below for why
  this can't just be inferred). `--target <label>` overrides it per run.
- `~/.config/carr/mcp-tokens.env` — already required for `run.sh call` /
  `tools/call-verb.py` to work at all; nothing new here, just noting the
  watcher's read path depends on it being present (see "Honest limits").

Run it:

```
.venv/bin/python tools/partner-line/watch.py --target <label>
```

That runs forever, polling every 5 seconds (`--interval` to change it) until
you Ctrl-C it. Useful flags:

- `--once` — poll exactly once and exit (for a cron-style call, or manual
  testing).
- `--dry-run` — print what WOULD be injected; inject nothing; never advance
  the stored watermark. Safe to run repeatedly with no side effects.
- `--since <seq>` — read starting after this seq instead of the stored
  watermark, for this run's first poll only (a one-off catch-up, mirroring
  `partner_ping.py`'s own `--since`). Every later poll in the same run reads
  from wherever that first poll left the watermark.
- `--room <name>` — default `partner-line`.

launchd auto-start is **deliberately not wired up yet** — this PR is the
script and its tests; scheduling it to start at login is a separate,
follow-up change.

## Consent posture — decision 351b9995

Incoming turns from the other partner **auto-inject**, but the design is
required to make that **visible** and **abortable**, never silent and never a
per-turn approval prompt:

- **Visible**: every injection fires a macOS notification (`osascript`)
  naming who the turn is from (sponsor + seat) before/as it lands. There is
  no code path that injects without one.
- **Abortable — the kill switch**: create the file
  `~/.config/carr/partner-line-paused` (any content, even empty) and the
  watcher keeps polling and keeps advancing its watermark, but injects
  **nothing** — it logs that it would have, and why it didn't. Delete the
  file to resume. Advancing the watermark while paused is deliberate: turns
  that arrive during a pause are not queued up to dump on you all at once the
  moment you resume — pausing means "I don't want to see these," not "hold
  them for later."

## Injection safety — the one non-negotiable rule

Claude Code sessions listen on `/tmp/cc-socks/<label>.sock`. A session with no
explicitly registered label binds its own **pid** as that label — e.g.
`/tmp/cc-socks/52188.sock`. This watcher **never writes to a pid-shaped
socket name**. It only injects to a socket path built from a **registered
label** — `--target <label>` or the `partner-line-target` file — and
`validate_target_label()` / `socket_path_for_label()` in `watch.py` refuse
anything matching a bare-digits `.sock` filename before a path is ever built.
There is no flag or code path in this script that reaches an unlabeled/pid
socket; see the pid-socket-refusal tests in `tests/test_watch.py` for the
enforced cases.

The injected message matches the wire format proven by the earlier spike
(`spikes/partner-line-78/`, receipts 1–2) exactly:

```json
{"type":"user","message":{"role":"user","content":"[dell · claude] <body>"},"origin":{"kind":"peer"}}
```

`origin.kind: "peer"` is what makes the target session render it as a
`<cross-session-message>` rather than an ordinary user turn. The `[sponsor ·
seat]` prefix is so the human watching the session can see at a glance who
it's from, without opening the room itself.

## Design decisions made while building this

- **`poll_once()` takes `fetch` / `injector` / `notifier` as injectable
  parameters.** The acceptance bar requires unit-testing the pure logic with
  no live socket and no live network. Rather than mocking `subprocess`/
  `socket` at the module boundary, the three I/O edges are ordinary
  parameters with real defaults (`fetch_turns`, `inject_to_socket`, `notify`)
  that the tests replace with plain recording fakes. This also means the same
  function is what really runs in production — there is no separate "test
  path" through the logic.
- **The watermark advances past *every* processed turn, including skipped
  ones** (your own turns, `system`/`receipt` rows) — not just injected ones.
  Otherwise a skipped row would be re-fetched and re-classified forever.
- **Advancing the watermark while paused** (see kill switch above) was a
  judgment call: the spec says "keeps polling and advancing state" for the
  kill switch, which reads as intentional, and it's also the only choice that
  keeps a long pause from turning into a flood of injections on resume.
- **`--since` only steers the first poll of a given run**, not every poll in
  a long-running loop. After that first poll (possibly) advances the real
  watermark, later polls in the same run read from it normally — mirrors how
  `partner_ping.py`'s one-shot `--since` behaves, extended sensibly to a
  script that can now also run as a loop.
- **Label validation doubles as a light path-traversal guard.** `LABEL_RE`
  requires a plain slug-ish string (no `/`), which incidentally also keeps a
  label like `../../etc/passwd` from ever reaching `os.path.join`, not just
  pid-shaped names.

## Honest limits / follow-ups

- **No live end-to-end test against a real Claude Code session socket.**
  Everything here is tested with fakes standing in for the socket, the
  `read-room` subprocess call, and `osascript`. Proving actual injection
  against a live labeled session (the way `spikes/partner-line-78`'s receipts
  did) is an explicit, separate follow-up — this PR is the daemon and its
  offline test suite, not that live proof.
- **No launchd wiring.** Running this continuously on login/boot is left for
  a follow-up change, once the live injection path above has been proven.
- **Depends on `~/.config/carr/mcp-tokens.env` and `~/.config/carr/partner`
  being set up on a given Mac** (the same files `partner_ping.py` and
  `tools/call-verb.py` already depend on). The tests do not depend on either
  file existing — they pass `whoami_path`/`target_path` explicitly and never
  touch the real `~/.config/carr/` files or the network.
- **How a session registers a label in the first place** (so it has something
  other than its pid to be targeted by) is out of scope here — that's the
  Claude Code harness's own labeling mechanism, referenced but not built by
  this change.

## Running the tests

```
python3 -m unittest tools/partner-line/tests/test_watch.py -v
```

No repo dependencies beyond the Python standard library; also runs under
`pytest` if you have it installed. 32 cases, covering: watermark advance
across polls, `--since` override, `--dry-run` never advancing state,
self-vs-other partner filtering, non-`turn` kind filtering, already-seen-seq
skipping, pid-socket refusal, the kill switch (including resume), and the
refuse-to-guess self/target resolution.
