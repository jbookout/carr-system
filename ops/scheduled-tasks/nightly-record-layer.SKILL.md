---
name: nightly-record-layer
description: Nightly record-layer chain (7 days, ~2am CT): exports all 7 generated files to the vault, rebuilds the Graph, then takes the encrypted backup. Verified by OUTPUT freshness, never by the schedule existing.
---

RULE-DELIVERY WORKFLOW: nightly-record-layer
RULE-DELIVERY PACKS: scheduled-automation

Before any other read, command, or report, call `standing-context` with exactly
`{"packs":["scheduled-automation"]}`. Recite the returned shared and personal
rule counts plus `rule_delivery.mode`, `rule_delivery.declared_packs`, and
`rule_delivery.would_omit_count`. Refuse the run before executing the chain if
the selector result is absent, if `packs_not_found` is non-empty, or if the
declared pack is anything other than the exact canonical name
`scheduled-automation`. The alias `automation` is unknown and loads nothing.

STORE-FIRST (added 2026-08-09, loop #289): the doctrine STORE is the source of truth for every governing doc named below. Before reading any `.md` path in the vault, try `read-doctrine` with that file's stem as the document slug; if a store doc exists, IT WINS and the vault file may be a stale duplicate. Two such duplicates were found on 2026-08-09 and this routine's sibling had been reading a three-week-old SOP because its pointer named the file instead of the slug. Do not edit the vault copy either way: hand-authored vault markdown is closed by record-home-gate.py (rule 14181e60).

Run the CARR record-layer nightly chain. Execute EXACTLY this via Bash, VERBATIM, character for character — do not paraphrase it, do not add flags, do not substitute paths, do not re-quote it. Permission approval matches the exact command string, and the string below is the one carrying a persisted approval (Joe, 2026-07-31); any rewording can hit a permission prompt at 2am with nobody awake to answer it, which is exactly how the first scheduled run produced nothing:

cd ~/carr-system && ./bin/nightly.sh >/dev/null 2>&1; echo "direct script exit=$?"

Do NOT invoke it with `bash <script>`: the script is `#!/bin/zsh` (its logging helper uses zsh's `print` builtin; under bash every log line dies with `print: command not found` — the steps still run but the timestamped `chain begin` / `START` / `OK` / `FAIL` lines never reach out/nightly.log, and a broken step leaves no FAIL line to find). Running it as `./bin/nightly.sh` uses the shebang and is correct. Fixed 2026-07-31 after the first scheduled run proved it.

That script does six things in order: (1) runs the cadence engine, which spawns the next_action rows the cadence rules are owed, (2) runs the availability matcher, which writes a digest of availability-vs-space-search matches and sends nothing to anyone, (3) exports all seven generated files LIVE to the vault, (4) rebuilds the consumer boards (`./run.sh all`: renewal-feed, lead-board, deal-room), (5) rebuilds the Graph from the exported files, (6) takes the encrypted pg_dump backup and commits it to git. Steps 1 and 2 come first because they WRITE, and a write that lands after the export it belongs in sits invisible for a whole day. It logs to {{REPO}}/out/nightly.log and exits non-zero if any step failed.

THE COMMAND ABOVE DID NOT CHANGE WHEN THOSE TWO STEPS WERE ADDED (2026-07-31, ORDER 14), AND IT MUST NOT. The script grows steps; the command string stays byte-identical, because the persisted permission approval matches the string and a new string is a new approval prompt at 2am. Anything the chain gains in future goes inside bin/nightly.sh, never into this file's command.

A step logging `SKIP … (exit 78 — not configured)` is NOT a failure and does not need reporting as one: it means that step ran, found a credential or setting it needs is absent, wrote nothing, and said which one in the line above it. The cadence engine is expected to SKIP until a write-capable database credential exists in ~/.config/carr/db.env; the matcher likewise. Report a SKIP as one line of context, never as a 🔔.

THEN VERIFY THE OUTPUT, NOT THE EXIT CODE (protocol rule 28: an automation is verified by output freshness, never by its schedule existing, and never by its own claim of success). Run EXACTLY, same verbatim rule:

cd ~/carr-system && ./run.sh health

Every row beginning "GEN " must read OK. Those seven rows are the generated files on a 26-hour cadence; a STALE one means the export step did not actually reach the vault regardless of what the exit code said.

ALSO REPORT THE AMBER ROWS, not only the GEN ones (added 2026-08-10). Until today this routine ran the full health check and was told to read seven rows out of forty-odd, so every other finding was printed and discarded. That is how the mypy tripwire stayed red from 08-08 to 08-10 with nobody told, and how nine finished scheduled-task edits sat uncommitted for thirteen hours until a human happened to read a row carefully. A check nobody reports on is a check nobody is running.

Report every `⚠︎` row EXCEPT the Deprecation register block, which is permanently amber by design (it lists shims kept alive on purpose and prompts rather than enforces). Reporting those nightly would train the reader to skip the whole section, which is the disease, not the cure. Two rows deserve naming in particular because they are new and they are about work in flight:

  * `nightly chain result` — whether the LAST run exited clean, and how many runs in a row have been red. A first failure and a week-long failure need different responses and the row says which it is.
  * `uncommitted work` — tracked files sitting outside git, with the oldest one's age. Past twelve hours it means work crossed a night unlanded; name the paths so whoever wrote them can land them by NAMING PATHS, never a sweep, because this machine runs several sessions against one working tree.

Keep it to one line per amber row. If nothing is amber outside the deprecation register, say so in four words and stop.

REPORT:
- If the chain exited 0 and all seven GEN rows are OK: report success in one line with the row counts from the log, then the amber rows per the section above. Nothing else needed.
- If anything failed: quote the actual FAIL line from out/nightly.log and the STALE/BEHIND rows from the health check. Do not retry more than once. Do NOT modify the script, the exporters, or any generated file, and do NOT hand-edit a vault file to make a check pass — the seven generated files are never hand-edited, ever. If the Worker or Neon is unreachable, say so plainly and stop; the runbook is DNA/Deal Management/record-layer/runbook.md.

(Historical note, resolved: the chain originally omitted the consumer rebuilds, leaving Lead Board / Deal Room reading BEHIND each morning. The ORDER 2 ADDENDUM added `./run.sh all` to the chain on 2026-07-31, so ALL 20 health rows — GEN files and boards alike — should now read OK after a good run. A BEHIND board row is therefore a real finding again, not an expected artifact.)

Context, not to be re-litigated: this replaced manual-only exports on 2026-07-31. It runs 7 days a week because the record layer has no weekend stand-down — the files must be true whenever either partner opens them, and every Cowork and phone session still reads them. (Corrected 2026-08-04: this sentence used to end "and Dell works off the generated files rather than the MCP verbs." Dell is on the connector now, per Joe. The line outlived the fact by days and was still being cited as live evidence for Dell's side in ORDER 28's inventory — where it was the whole basis for classifying his row UNKNOWN. The chain's 7-day rationale does not depend on it.) If the Mac was asleep at 2am the task fires on wake; that is normal for this system and not a failure.
