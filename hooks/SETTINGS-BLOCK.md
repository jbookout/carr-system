# SETTINGS-BLOCK.md — RETIRED 2026-08-23. The wiring lives in config, not here.

This file used to be the hand-maintained record of the hooks block to paste into
`~/.claude/settings.json`. It is retired, and it is **not** being regenerated as
prose. Read the config instead:

| What you want | Where it actually lives |
| --- | --- |
| The CARR-owned Claude hooks block | `ops/config/hooks.json` |
| The Codex hooks contract | `ops/config/codex-hooks.json` |
| The CARR-project delegation matcher | `ops/config/delegation-gate-hook.json` |
| The vault-rooted session settings | `claude-tree/settings/*.settings.json` |
| Which gate hashes are blessed | `ops/config/gate-baseline.json` |
| Install / compare live vs repo | `./.venv/bin/python ops/config-as-code.py check` |
| Attest the whole layer right now | `python3 hooks/gate-integrity.py` |

## Why a tombstone rather than a rewrite

Because this file's entire history is the argument against itself. It drifted
twice, in the same way, and both times the drift was found by something that
COMPARED config rather than something that DESCRIBED it:

- **2026-08-03** — it documented two hooks while four were live. That finding is
  what caused `ops/config-as-code.py` and the `tools/health-check.py` machine-config
  check to be built at all; both still carry it in their headers as the reason
  they compare instead of narrate.
- **2026-08-06** (the #214 audit) — it was the only place claiming
  `ledger-boundary-sweep.py` was a hook. No settings file named it and
  `out/hook-guard.log` showed zero firings against 75 for `ledger-sweep.py`. It
  blocked nothing for seventeen days while this document said it did.
- **2026-08-23** — the same drift a third time: it described an older, smaller
  hook set than the fifty gates `gate-baseline.json` tracks.

Rewriting it would restore the exact mechanism that failed three times. Rule
14181e60, the database-first write law, is the general form: content lives in
verbs and config, and prose files drift because nothing can fail when they are
wrong. `ops/config/hooks.json` cannot silently drift — `hooks/gate-integrity.py`
compares it against live settings at every SessionStart and names the mismatch.

## What was retired alongside it

`hooks/ledger-boundary-sweep.py` and `hooks/install-record-home-gate.py` were
deleted in the same change (the 2026-08-23 process-audit council's dead-weight
sweep). The first was registered nowhere and blocked nothing since it was
written. The second was a one-shot 2026-08-03 installer that merged
`record-home-gate.py` into `settings.json` by hand; `ops/config-as-code.py install`
has owned that job since, and the record-home gate itself is untouched and still
wired.

`hooks/session-brief.py` was ALSO proposed for deletion in that sweep and was
kept, because the premise was wrong: it is wired as a SessionStart hook in both
`claude-tree/settings/my-drive-root.settings.json` and
`claude-tree/settings/carr-ai-project.settings.json`, and in both deployed
copies under `~/My Drive`. It is vault-rooted, never carr-system-rooted, which
is why a repo-side grep for it reads as unwired — the same trap
`hooks/worktree-self-plumb.py` documents in its own header.
