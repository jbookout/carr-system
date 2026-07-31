<!-- CANONICAL COPY, versioned 2026-07-31 (ORDER 12 lane c added step 2 to a pre-existing task). RUNTIME lives at ~/.claude/scheduled-tasks/calendar-fetch-daily/SKILL.md — edit there, then sync this copy in the same commit. Same convention as nightly-record-layer.SKILL.md. -->
---
name: calendar-fetch-daily
description: Weekday calendar feed fetch, then push into the record layer: runs ~/carr-local/fetch-calendar.sh (replaces the launchd job us.carr.fetchcalendar) and then posts the normalized events to the ingest socket (ORDER 12 lane c)
---

Run the CARR calendar feed fetch, then push the events into the record layer. Two steps, in that order — the push reads the file the fetch just wrote.

## Step 1 — fetch the feed

Execute exactly this via Bash:

bash "/Users/booko/carr-local/fetch-calendar.sh"

Then verify success: the last line of "/Users/booko/Library/CloudStorage/GoogleDrive-joe.bookout.carr.us@gmail.com/My Drive/CARR AI/DNA/Team/calendar-fetch.log" must be a fresh timestamp with "OK". If it shows an error or the file was not touched in this run, report the failure plainly (name the error line) — do not retry more than once, do not modify the script, do not create any other task.

## Step 2 — push the events to the ingest socket

Execute EXACTLY this via Bash, VERBATIM, character for character — do not paraphrase it, do not add flags, do not substitute paths, do not re-quote it. Permission approval matches the exact command string, and a reworded command is an unapproved command (2026-07-31: an unapproved command in a scheduled session sat unexecuted for nearly six hours while its schedule record claimed the run had happened):

cd ~/carr-system && ./bin/pull-gmail-calendar.py

**Run it even if step 1 failed.** The push reads whatever .ics files are on disk; a stale feed still carries real meetings, and skipping the push because the fetch broke loses a day of capture for no gain.

Verify by OUTPUT, not by the exit code and not by the fact that the task ran (protocol rule 28). The script prints one line per feed plus a summary:

`calendar-pull: source=calendar window=<from>..<to> posted=N duplicate=M failed=K unparseable=S`

- `failed=0` → success. Report the summary line as-is. **A high `duplicate` count is the healthy steady state**, not a problem: the socket is unique on (source, external_id), so every unchanged meeting in the window dedups on every run. `posted` is only ever new or newly-moved meetings.
- `failed` above zero → quote the FAIL lines from `~/carr-system/out/capture-lanes.log` and report them. Do not retry more than once.
- Exit code **78** means the ingest token is not configured yet (`~/.config/carr/ingest.env` missing, or no `CARR_INGEST_TOKEN_CALENDAR`). That is Joe's `wrangler secret put` step, documented in `DNA/Deal Management/record-layer/ingest-tokens-setup.md`. Report it as a pending human step; it is not a failure of this task, and nothing is lost — the next run after the token lands posts the same window.

Never hand-write a curl for this (the token must not enter a transcript or `ps` output — that is why the script exists), never edit the script, and never create another scheduled task.

## Context (for you, not to be re-litigated)

Step 1 replaced the launchd job us.carr.fetchcalendar on 2026-07-22 because launchd background processes get "Operation not permitted" writing to the Google Drive CloudStorage path; the Claude harness has that access. Weekdays-only matches the system's weekend stand-down. The script writes calendar-latest.ics (JOE ONLY) plus the log line.

Dell's feed is NOT this task's job and never has been. calendar-latest-dell.ics is written by Dell's own Mac ("CARR Calendar Fetch - Dell" Shortcuts automation, daily 7:55am, live since 2026-07-28) and reaches Joe's Mac by Google Drive sync. Since this task fires at ~7:09, a Dell file still dated yesterday is EXPECTED at run time — do not report it as a failure or as a fault in fetch-calendar.sh. Only flag Dell's feed if it is still older than ~26 hours well after 8:00am. Both feeds are read by the daily heartbeat (8:06) and the Monday brief.

Step 2 was added 2026-07-31 under ORDER 12 lane (c), binding design `DNA/Deal Management/record-layer/wave2-design-2026-07-31.md` §2b. It posts BOTH partners' feeds, and it reads Dell's file at whatever age it happens to be — at 7:09 that is normally yesterday's copy, which is fine: the window runs a day back and two weeks forward, so a one-day-old Dell feed still covers nearly all of it, and the next morning's run picks up anything that changed. The script never fetches a feed itself; one fetcher, one job, and a network failure stays in the fetcher's log where it already gets read.
