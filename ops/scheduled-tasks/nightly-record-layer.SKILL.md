<!-- CANONICAL COPY, versioned 2026-07-31 (Fable seat ruling after ORDER 8: the file the nightly chain depends on was unversioned and invisible to Dell's side). RUNTIME lives at ~/.claude/scheduled-tasks/nightly-record-layer/SKILL.md — edit there, then sync this copy in the same commit. Dell's side authors its own when his nightly lands; this is the reference pattern. -->
---
name: nightly-record-layer
description: Nightly record-layer chain (7 days, ~2am CT): exports all 7 generated files to the vault, rebuilds the Graph, then takes the encrypted backup. Verified by OUTPUT freshness, never by the schedule existing.
---

Run the CARR record-layer nightly chain. Execute EXACTLY this via Bash, VERBATIM, character for character — do not paraphrase it, do not add flags, do not substitute paths, do not re-quote it. Permission approval matches the exact command string, and the string below is the one carrying a persisted approval (Joe, 2026-07-31); any rewording can hit a permission prompt at 2am with nobody awake to answer it, which is exactly how the first scheduled run produced nothing:

cd ~/carr-system && ./bin/nightly.sh >/dev/null 2>&1; echo "direct script exit=$?"

Do NOT invoke it with `bash <script>`: the script is `#!/bin/zsh` (its logging helper uses zsh's `print` builtin; under bash every log line dies with `print: command not found` — the steps still run but the timestamped `chain begin` / `START` / `OK` / `FAIL` lines never reach out/nightly.log, and a broken step leaves no FAIL line to find). Running it as `./bin/nightly.sh` uses the shebang and is correct. Fixed 2026-07-31 after the first scheduled run proved it.

That script does four things in order: (1) exports all seven generated files LIVE to the vault, (2) rebuilds the consumer boards (`./run.sh all`: renewal-feed, lead-board, deal-room), (3) rebuilds the Graph from the exported files, (4) takes the encrypted pg_dump backup and commits it to git. It logs to /Users/booko/carr-system/out/nightly.log and exits non-zero if any step failed.

THEN VERIFY THE OUTPUT, NOT THE EXIT CODE (protocol rule 28: an automation is verified by output freshness, never by its schedule existing, and never by its own claim of success). Run EXACTLY, same verbatim rule:

cd ~/carr-system && ./run.sh health

Every row beginning "GEN " must read OK. Those seven rows are the generated files on a 26-hour cadence; a STALE one means the export step did not actually reach the vault regardless of what the exit code said.

REPORT:
- If the chain exited 0 and all seven GEN rows are OK: report success in one line with the row counts from the log. Nothing else needed.
- If anything failed: quote the actual FAIL line from out/nightly.log and the STALE/BEHIND rows from the health check. Do not retry more than once. Do NOT modify the script, the exporters, or any generated file, and do NOT hand-edit a vault file to make a check pass — the seven generated files are never hand-edited, ever. If the Worker or Neon is unreachable, say so plainly and stop; the runbook is DNA/Deal Management/record-layer/runbook.md.

(Historical note, resolved: the chain originally omitted the consumer rebuilds, leaving Lead Board / Deal Room reading BEHIND each morning. The ORDER 2 ADDENDUM added `./run.sh all` to the chain on 2026-07-31, so ALL 20 health rows — GEN files and boards alike — should now read OK after a good run. A BEHIND board row is therefore a real finding again, not an expected artifact.)

Context, not to be re-litigated: this replaced manual-only exports on 2026-07-31. It runs 7 days a week because the record layer has no weekend stand-down — the files must be true whenever either partner opens them, and Dell works off the generated files rather than the MCP verbs. If the Mac was asleep at 2am the task fires on wake; that is normal for this system and not a failure.