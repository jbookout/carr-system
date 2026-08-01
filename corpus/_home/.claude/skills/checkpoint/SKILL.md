---
name: checkpoint
description: >
  Snapshots the current state of the work to a file mid-session, as insurance before a risky
  operation or before a long context runs out. Use when the user says /checkpoint, "save a
  checkpoint," "snapshot where we are," "save state before we do this," or before an irreversible or
  sweeping change. Writes a compact, dated state file — what's done, what's mid-flight, the key
  decisions, the exact next step, and the files in play — so if the session is lost, crashes, or
  goes sideways, work resumes without loss. Especially valuable in projects that are NOT under
  version control, where there is no commit to fall back on. Lighter than a full handoff: a
  personal save point, not a briefing for a new session.
---

# checkpoint — mid-session state snapshot

> **Doctrine ownership: single writer.** One seat edits this file, the Fable design seat. Every other session, either brain, proposes changes through the `teach` verb or a team-board row, and the seat lands them. Set 2026-08-01 per ORDER 38 (two-writer endgame D3).

## What this is for

Insurance. Before a risky or irreversible step, or when a session is getting long and context might be lost, `/checkpoint` writes enough state to a file that the work can be resumed with minimal loss. It is deliberately lighter than a handoff — a save point for continuity, not a full briefing.

## Procedure

1. **Choose the location.** A `checkpoints/` folder at the project root, or wherever the project keeps working notes. File name dated and timed, e.g. `checkpoints/2025-06-01-1430.md`.
2. **Write a compact snapshot:**
   - **Goal** — what this session is trying to accomplish, one line.
   - **Done so far** — bullet list.
   - **In flight** — what is half-finished right now, precisely enough to resume.
   - **Key decisions** made this session (one line each).
   - **Exact next step** — the single thing to do next.
   - **Files in play** — real paths touched or about-to-be-touched.
   - **About to do** (if checkpointing before a risky op) — what the risky step is, so a resume knows whether it completed.
3. **Confirm** the checkpoint path in one line.

## Notes

- Keep it tight. A checkpoint is not documentation; it is a rope back to the ledge. Speed and accuracy over polish.
- For projects under version control, suggest a commit as the stronger checkpoint and use this for the *uncommittable* state (reasoning, plan, half-formed next steps). For non-version-controlled projects, this file IS the fallback.
- If the user wants a full takeover packet for a *new* session rather than a personal resume point, that's the handoff skill instead.
- Checkpoints are cheap and disposable; old ones can be cleared once the work is safely past them.
