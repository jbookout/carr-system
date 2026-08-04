---
name: restore-rehearse-weekly
description: Weekly proof that the encrypted backups actually restore: decrypts the newest dump into a throwaway Neon branch, reconciles row counts against production, reports PASS/FAIL, deletes the branch
---

Weekly backup restore rehearsal for Joe Bookout's CARR record layer. LOCAL Claude Code session on Joe's Mac.

WHY THIS EXISTS. Until 2026-08-02 nothing in the repo could restore a backup — a grep for `age -d`, `age --decrypt`, `pg_restore` and `psql ... backups/` returned zero hits across every language and every doc. The chain had been running nightly since 7/30 and had never been exercised. The first real run found the dumps would NOT restore: plain `pg_dump` embedded `ALTER DEFAULT PRIVILEGES` / `GRANT` / `OWNER TO` statements naming the source role, and the first one aborted the load. Three dumps existed and not one would have come back. An untested backup is a hope, and this job is what converts it to a fact every week.

RUN EXACTLY THIS, verbatim, one Bash call:

cd ~/carr-system && ./run.sh restore-rehearse

That is the whole job. Behaviour lives in `bin/restore-rehearse.sh`, never in this prompt — if something needs to change, change the script (thin-prompt law). Do NOT rewrite, paraphrase or "improve" the command; do not add flags.

DO NOT ADD THE KEY PATH BACK. This prompt used to prescribe `CARR_AGE_IDENTITY=~/.config/carr/age-key.txt ./run.sh restore-rehearse`, and that command could never run: `hooks/guard-unattended.py` rule 3 matches `age-key` as private key material and denies the Bash call before it starts. The env var was redundant anyway — `bin/restore-rehearse.sh` line 72 defaults the identity to that exact same path. The script resolves its own secret; the command names no key. Caught 2026-08-04, the first session that tried to run it. The `--identity` and `CARR_AGE_IDENTITY` overrides stay in the script for a human whose key lives somewhere else; they do not belong in automation.

WHAT IT DOES, so you can read the output rather than trust it: preflight (tools, key, dump age, Neon reachability), reads production row counts in a `default_transaction_read_only=on` session, creates a timestamped throwaway branch, asserts the branch endpoint is a DIFFERENT host from production, creates a fresh database on it, decrypts the newest `backups/*.sql.age` straight into psql, reconciles all ~67 tables against production, then deletes the branch from a trap. It contains no DROP and no TRUNCATE. It never touches production with anything but a read.

VERIFY BY OUTPUT, NEVER BY EXIT CODE (protocol rule 28). The last block prints either `RESTORE REHEARSAL: PASS` or `RESTORE REHEARSAL: FAIL (n assertion(s) failed)`. Read it.

HOW TO READ A RESULT — this matters, because the first run cried wolf:
- `expected: table postdates this dump` and `expected: rows postdate this dump` are NOT failures. The dump is a snapshot; migrations applied after it created tables it cannot contain. The script prints `NOTE: n migration(s) were applied AFTER this dump` when this is in play.
- `(drift — the dump is a snapshot; production moved)` is normal and expected on active tables.
- A FAIL means one of three things and all three are real: a table present in production is missing while the schema is CURRENT, a table with rows restored empty while the schema is CURRENT, or the restored total fell below the 90% floor (a truncated or half-written dump).

REPORT in chat, short:
- PASS: one line with the dump name, the restored-vs-production percentage, and the branch it used and deleted. Nothing else.
- FAIL: quote the failing rows verbatim, say plainly that the backups are not currently provable, and stop. Do NOT try to fix the dump, do not re-run more than once, do not edit the script. Raise it to Joe as the highest-priority item in the system — a backup that will not restore is the one failure that cannot be recovered from later.
- Also flag it if the newest dump is more than ~26h old: that means the nightly chain skipped, which happens when the Mac sleeps through the 2am run. A 08-01 dump is missing for exactly that reason.

If the age key is absent or the branch cannot be created, say so plainly and stop. Never fake a pass, and never report success without the PASS line in the output.