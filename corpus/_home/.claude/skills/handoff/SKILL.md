---
name: handoff
description: >
  Prepares a complete, self-contained handoff packet so a brand-new session — one with zero memory
  of this conversation — can fully take over the work started here and continue exactly where it
  left off. Project-agnostic: works in any repo, folder, or project. Use whenever the user types
  /handoff (with or without a topic after it) or says things like "hand this off," "continue this
  in a fresh session," "spin up a new session on this," "prep a new session," "create a
  continuation packet," "get a new chat up to speed," or "pick this up somewhere else." Two modes:
  with NO topic after /handoff, hand off the ENTIRE current session — every active thread, each as
  its own block. With a topic given (a feature, bug, file, idea, or deliverable name), scope the
  packet to just that one thread. Output is a single drop-in markdown packet written AS A BRIEFING
  TO THE NEXT SESSION, not a recap to the user, that the new session reads and immediately acts on.
  After producing it, offer — but never assume — to save it into the project's conventional home
  for such notes (or a sensible default). NOT for parking a raw idea for later (that is capture,
  not handoff), and NOT a status summary written for the user to read.
---

# handoff — session takeover packet builder (portable)

> **Doctrine ownership: single writer.** One seat edits this file, the Fable design seat. Every other session, either brain, proposes changes through the `teach` verb or a team-board row, and the seat lands them. Set 2026-08-01 per ORDER 38 (two-writer endgame D3).

## What this skill is for

A new session starts blank. It does not remember this conversation, the decisions made in it, the files touched, or the dead ends already ruled out. `/handoff` closes that gap: it packages everything a fresh session needs to resume the work without the user re-explaining anything.

The output is **one markdown packet**, written **as a briefing addressed to the next session** ("You are picking up..."), not as a recap addressed to the user. The user pastes or attaches it into a fresh session, and that session reads it and continues.

This is **build-and-deliver**. It never sends anything, never opens the new session itself, and never saves a file without the user's go-ahead.

## Adapt to the project first

This skill runs in any project. Before writing, take ten seconds to fit the local conventions:

- **Read the project's own context** if present — `CLAUDE.md`, `AGENTS.md`, `README`, or a docs index — to learn the project's operating rules, vocabulary, and where notes live.
- **Find the handoff home.** If the project already has an obvious place for continuation notes, decisions, or open items (a `handoffs/` or `docs/` folder, a decision log, an open-loops or TODO file), plan to offer that. If nothing exists, the default is a `handoffs/` folder at the project root with a dated file.
- **Carry the project's guardrails.** If the project's context defines standing rules — an approval gate before outbound actions, confidentiality boundaries, formatting defaults, a house style — restate the ones the successor could trip over in the packet. If there is no such context, skip this section rather than inventing rules.

## Two modes — decide first

Read what came after `/handoff`:

- **No topic** → hand off the **entire session**. Identify every distinct active thread of work in the conversation. Produce **one packet** with **one self-contained block per thread**. If threads share context, state it once in a short preamble, then give each thread its own block.
- **A topic given** → **scope to that one thread only**. Pull just the material relevant to it; ignore unrelated threads. If the named topic was never actually worked on this session, say so and ask which thread is meant rather than guessing.

## The packet — required structure

Wrap the whole thing in a fenced code block so it copies clean. Open with a one-line header naming the source session and date. Then, for **each thread**, include every section below. Omit one only if it genuinely does not apply, and say so in a line rather than dropping it silently.

1. **The ask (in the user's words).** What the user set out to do, phrased the way they framed it, not sanitized into a summary. One or two sentences.
2. **Why it matters.** The reason this thread exists — the goal, the deadline, the thing it unblocks. Only what changes the next session's priorities.
3. **Current state.** What is done, what is in progress, what is untouched. Be honest about half-finished work; do not round "started" up to "done."
4. **Decisions made, with rationale.** Every settled decision AND why it was settled, so the next session does not reopen it. This is the most important section — relitigating decided questions is the main failure a handoff prevents.
5. **Exact paths and references.** Full paths to every file, artifact, or document created or edited, and any that must be read first. Real paths, not descriptions. Note anything the new session may not be able to reach.
6. **Open questions.** What is genuinely undecided. Separate "waiting on the user" from "next session should work it out."
7. **Next actions — ordered, starting at step 1.** A numbered list the next session executes top-down. The first item is immediately actionable, not "get oriented."
8. **Load first.** Which files or context the new session should read before acting — the project's context file(s) plus the session-specific paths from section 5. Keep it lean; only what this thread needs.
9. **Guardrails the successor must honor.** The project's standing rules the successor could violate (see "Adapt to the project" above). Omit entirely if the project defines none.
10. **Watch-outs.** Mistakes already made and corrected this session, dead ends already explored, assumptions that turned out wrong — so the successor does not repeat them. This is what plain scrollback never captures and what makes a handoff worth more than "read the history."

## How to build it

1. **Reconstruct from the actual conversation**, not from how these tasks usually go. Pull the real decisions, real paths, real corrections from THIS session.
2. **Do not fabricate continuity.** If a section has nothing real, write "none this session" rather than inventing plausible content. A blank next session acts on whatever the packet says, so a made-up next-action is worse than an honest gap.
3. **Write for a reader with zero context.** Spell out names, acronyms, and locations this session takes for granted. Test: could a competent stranger execute step 1 with only this packet and the files it points to?
4. **Keep it dense, not padded.** Every line should change what the next session does.
5. **Verify paths exist** before listing them in section 5 when the tools are available. A wrong path sends the successor in a circle.

## After the packet exists

Deliver it in-chat first. Then offer, as a yes/no question — never assume:

- **Save it** into the project's handoff home (from "Adapt to the project"), as a dated markdown file, creating the folder if needed.
- **Log it** in the project's open-items / TODO / decision file if one exists, so the thread does not fall off the radar.

Do either only on a yes. If the user just wants the packet to paste and move on, stop after delivering it.

## Boundaries

- **Not** idea capture. Parking a raw idea for later is a different move; this packages live, in-progress work for takeover.
- **Not** a status report for the user. If they want to know where things stand for their own reading, tell them plainly in chat — do not build a to-the-next-session packet they do not need.
- Handoff **builds** the packet. It never opens the new session, never sends anything, and never saves without a go-ahead.
