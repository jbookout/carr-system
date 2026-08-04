# CLAUDE.md — you are in the CODE repo, not the standing context

**This is `jbookout/carr-system`, the one and only home for CARR's code.** The
record layer, the MCP server, every migration, the exporters, the pipelines, the
hooks and all durable code live here and nowhere else.

**This file is a POINTER, not the standing context.** It exists because a session
rooted here would otherwise boot with no identity, no binding rules and no
reading router — and would not know that any of those exist. Same fail-soft
pattern as the Cowork project stub: the pointer never changes, the thing it
points at does.

## Read this first, before anything else

```
/Users/booko/Library/CloudStorage/GoogleDrive-joe.bookout.carr.us@gmail.com/My Drive/CARR AI/CLAUDE.md
```

That is the single live source of project instructions. It carries who you are
working with, the standing rules, and the session-start read set. Read it, then
follow its reading order — the root `INDEX.md`, `00_Context/ai-operating-notes.md`,
`DNA/Clients/clients-active.md`, `00_Context/open-loops.md`, and both compiled-rules
files. **Recite the loaded rule counts in your first response**, exactly as it says.

If that path is unreachable, say so plainly and stop rather than improvising a
substitute. A session working CARR code without the taught rules will break
conventions it cannot see.

## Why the project root normally is NOT here

Joe asked this directly on 2026-08-04 — now that the system runs on the database,
should the project connect to this repo instead of the Drive vault? No, and the
reasoning is worth keeping so it is not relitigated:

- **The database replaced the RECORD half of the vault, not the KNOWLEDGE half.**
  Leads, clients, vendors, deals, loops, rules and decisions are records and live
  in the DB. Doctrine does not: `writing-rules.md`, `brand-voice.md`,
  `templates.md`, `carr-profile.md`, `ux-doctrine.md`, every per-platform
  marketing strategy file, `DNA/Reference/` build-out specs, every SOP and all
  prospect narrative are markdown in the vault with no DB equivalent.
- **The session-start read set is entirely vault files**, including
  `compiled-rules-shared.md`, which is GENERATED from the DB and READ from Drive.
  That export exists precisely so sessions load rules from a file.
- **Cowork can only see Drive.** Rooting the project here would leave Dell's side
  and every cloud session with no standing context at all.
- **Permissions and hooks are user-level** (`~/.claude/settings.json`), not
  project-scoped, so the root choice neither grants nor removes any of them.

Working in this repo from a vault-rooted session costs nothing — absolute paths
and `cd ~/carr-system` work normally, which is how every build lands.

## The rules that bite hardest in here

These are the full text's, not substitutes for reading it:

- **`run.sh export` writes to `out/exports/` STAGING by default.** Only
  `CARR_EXPORT_LIVE=1` reaches the vault. After teaching a rule, refresh with
  **`bin/refresh-rules.sh`** — that script, never a raw export. `run.sh health`
  carries a `rules live` row that catches the gap.
- **Findings and record updates go into the DATABASE, never a markdown report.**
  A `PreToolUse` hook denies writes that violate this; it is not advisory.
- **Verify by output, never by the artifact existing.** A schedule that ran, a
  verb that returned `ok:true`, and a file that is fresh each prove less than they
  appear to. Re-read the row, re-run the query, check the log.
- **A manual path and an automated path that do the same job must be the same
  code.** If a script already does it, call the script.

*Created 2026-08-04. Edit in place; no version-numbered copies.*
