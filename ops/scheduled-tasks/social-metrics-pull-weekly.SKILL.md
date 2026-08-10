---
name: social-metrics-pull-weekly
description: Weekly social analytics pull (Chrome): reads FB/IG/LinkedIn/X post metrics from Joe's logged-in browser and fills post-performance-log.md rows so the monthly review steers from data
---

STORE-FIRST (added 2026-08-09, loop #289): the doctrine STORE is the source of truth for every governing doc named below. Before reading any `.md` path in the vault, try `read-doctrine` with that file's stem as the document slug; if a store doc exists, IT WINS and the vault file may be a stale duplicate. Two such duplicates were found on 2026-08-09 and this routine's sibling had been reading a three-week-old SOP because its pointer named the file instead of the slug. Do not edit the vault copy either way: hand-authored vault markdown is closed by record-home-gate.py (rule 14181e60).

You are running Joe Bookout's weekly social-media metrics pull (CARR AI system). This is a MEASUREMENT-ONLY task: you read analytics and update one log file, plus (since 2026-07-31) you kick off the record layer's own metrics chain with one command. You never post, publish, schedule, or reply to anything.

STEP 0 — THE RECORD-LAYER CHAIN, FIRST, BEFORE ANY BROWSER WORK (added 2026-07-31, ORDER 15). Execute EXACTLY this via Bash, VERBATIM, character for character — do not paraphrase it, do not add flags, do not substitute paths, do not re-quote it. Permission approval matches the exact command string, and any rewording can hit a permission prompt in an unattended run with nobody there to answer it. That is not hypothetical: on 2026-07-31 a scheduled run sat 5 hours 58 minutes on exactly that prompt and produced nothing.

cd ~/carr-system && ./bin/learning-weekly.sh; echo "learning-weekly exit=$?"

Do NOT invoke it as `bash ./bin/learning-weekly.sh`: the script is `#!/bin/zsh` and its logging helper uses zsh's `print` builtin, so under bash every timestamped log line dies with `print: command not found` and a broken step leaves no FAIL line to find.

That one command does three things: pulls Joe's published posts and their metrics from the Blotato API into the record layer (`content_piece` / `placement` / `placement_metric`), then runs the weekly learning job, then runs the correction miner. It writes its reports to CARR AI/Automation/Learning/ and logs to ~/carr-system/out/learning.log.

THEN VERIFY BY OUTPUT, NOT BY THE EXIT CODE (protocol rule 28 — an automation is verified by what it produced, never by its schedule existing and never by its own claim of success). Read the first bold line of each of these three files and quote them in your final summary:
- CARR AI/Automation/Learning/placement-pull-latest.md
- CARR AI/Automation/Learning/weekly-learning-latest.md
- CARR AI/Automation/Learning/correction-miner-latest.md

A report saying "no conclusions yet, threshold is 30" or "the placement records could not be read under this credential" is a SUCCESSFUL run — those jobs are built to speak honestly below their evidence floor, and a below-threshold report is the correct output, not a fault. The only real failures are a FAIL line in out/learning.log or a report file that did not update. Do NOT edit any file under Automation/Learning/ — they are generated. Do NOT try to fix a credential yourself; if a step reports one missing, say which one in your summary and move on to the browser work below.

CONTEXT TO READ FIRST (all under {{VAULT}}/):
1. Marketing/Social Media/post-performance-log.md — the log you are filling. Read its "How to use" header; it defines the columns and the measurement routes. You are executing route (2): Claude in Chrome reads each post's native analytics.
2. Marketing/Social Media/published-log.md — background on what's published and each post's live URL, for the CARR-branded platforms (FB/IG/LinkedIn) and for content/topic labels. For X specifically, treat this file as INCOMPLETE by default (see step 2 below) — it has a history of drifting out of sync with what's actually live.
3. Marketing/Social Media/x-posting-spec.md — X runs as a separate lane from the CARR-branded batch (own cadence, standalone posts, own topics). Don't assume an X post exists only because a same-day FB/LinkedIn/IG post does, or vice versa.

THE JOB:
1. Identify published posts that are ~5+ days old and have no metrics row (or an unfilled row) in post-performance-log.md.
   - **Facebook/Instagram/LinkedIn:** cross-reference published-log.md as before, it's reliable for these three.
   - **X:** do NOT rely on published-log.md's post list alone, it has been found incomplete (a 2026-07-22 audit found a 1.2K-impression post and 3 others live on X with zero record in published-log.md). Instead, pull the actual list of recent posts straight from X's own Analytics → Content tab (see step 2 below) and treat THAT as the source of truth for "what's live." Cross-reference published-log.md only for topic/pillar labels, and if a live post has no matching published-log.md entry, backfill one (see step 4).
2. Use the claude-in-chrome tools (load via ToolSearch if deferred; call tabs_context_mcp first, create a tab) to read each post's analytics from Joe's logged-in Chrome:
   - Facebook + Instagram: Meta Business Suite (business.facebook.com) — Views/Reach + engagements per post. IG reels: views/reach/likes/comments/shares.
   - LinkedIn: open each post's analytics (impressions, profile views/followers gained if shown).
   - **X: use the full Analytics dashboard, NOT the per-tweet "Post Analytics" popup.** Navigate to `x.com/i/account_analytics/content?type=posts&sort=date&dir=desc&days=28` (Content tab, Premium+ feature this account has) and read Impressions/Likes/Replies/Reposts straight off that table, matching posts by exact text + date. The per-tweet popup (opened via the bar-chart icon under a tweet, or a post's `/analytics` URL) has a broken Impressions figure that can read 0 even when the post has real likes/replies — confirmed wrong on 8 posts on 2026-07-22. Never use it for Impressions again (its likes/replies/reposts counts are fine, just not Impressions).
   If Chrome is not connected or a platform is logged out, SKIP that platform gracefully and note it — never try to log in, never handle credentials.
3. Fill the log: one row per post in the existing table format (Date | Platform | Pillar | Job | Hook | Format | Link | Impressions/Reach | Engagements | Replies/DMs | Learning). Write a one-line Learning per post — that column is the whole point. Keep Learnings comparative and concrete ("reel 6x the statics around it", "COI hook beat data posts again").
4. If step 1 turns up an X post that's live but missing from published-log.md, add a row for it there too (Date Published | Platform=X | Pillar | Topic | Post Excerpt | Live URL | Calendar Row="standalone lane, backfilled <date>") before logging its metrics in post-performance-log.md. This is the one exception to "don't edit any file other than post-performance-log.md" below, it exists specifically to stop this gap from recurring. Describe the post plainly from what you read; don't guess at intent you can't see in the text.
5. If a full batch is now measured, append a short dated readout section (like the existing "Batch 1 readout") summarizing per-platform signal. Follow DNA/writing-rules.md style rules in anything you write (no em-dashes etc.).
6. Data quality: never invent a number. If an analytics page won't load or a number is ambiguous, leave the cell as "n/c" with a note. Numbers you didn't read do not go in the log.

GUARDRAILS: read-only in the browser (navigate + read; no clicking Post/Boost/Promote/any action button). Do not touch Blotato BY HAND — no posting, scheduling, editing or cancelling, in the app or through its API. (Amended 2026-07-31: step 0's script READS the Blotato API for published posts and their analytics. That read is the exception and it is the only one; everything else about Blotato stays hands-off.) Do not edit any file other than post-performance-log.md, except the narrow published-log.md backfill in step 4 — and never hand-edit anything under Automation/Learning/, which is generated by step 0's script. Weekends-off rule does not apply (this is a Wednesday task) but if the run lands on a holiday or Chrome is unavailable, just skip and note it — Monday's brief absorbs gaps.

OUTPUT: end with a short summary — how many rows filled, per-platform highlights (best/worst), anything skipped and why, and one sentence on what the data suggests for the next batch (informational only; the monthly review makes the actual steering decision).