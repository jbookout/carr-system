---
name: idea-resurface-monthly
description: Monthly idea-bank resurface (fires daily 9:00am inside the 5th-11th window; the task file's ledger gate runs it exactly once per month)
---

**STEP 0 IS A PREDICATE, NOT A JUDGEMENT — RUN IT FIRST, BEFORE READING ANYTHING.**

```
cd ~/carr-system && .venv/bin/python bin/monthly-gate.py idea-resurface-monthly
```

Exit 1 means this month's round is already done: **end the session immediately** — no doctrine read, no `standing-context`, no precedent query, no output. Exit 0 means proceed.

This lives in the prompt rather than in the doctrine because a gate written inside the doctrine cannot stop you reading the doctrine, which is what it costs. The cron fires daily across the window so a sleeping Mac cannot lose the month; the other firings are no-ops and must be cheap. Rule 5e89c211: never spend a cognition token on recurrence a predicate can express.

**WHEN THE ROUND ACTUALLY COMPLETES**, stamp the ledger as the last act, or every remaining firing in the window will redo it:

```
cd ~/carr-system && .venv/bin/python tools/ops-record.py run --service idea-resurface-monthly --key monthly.completed --kind job --state succeeded --environment production --started-at <start> --ended-at now --source-kind wrapper --source-ref bin/monthly-gate.py --detail "<one line on which ideas were surfaced>"
```

Nothing else writes that key. `scheduled-session` rows are written for every firing by a hook and mean only that a session ended cleanly, never that the work happened.

---

STORE-FIRST (added 2026-08-09, loop #289): the doctrine STORE is the source of truth for every governing doc named below. Before reading any `.md` path in the vault, try `read-doctrine` with that file's stem as the document slug; if a store doc exists, IT WINS and the vault file may be a stale duplicate. Two such duplicates were found on 2026-08-09 and this routine's sibling had been reading a three-week-old SOP because its pointer named the file instead of the slug. Do not edit the vault copy either way: hand-authored vault markdown is closed by record-home-gate.py (rule 14181e60).

You are the monthly idea-bank resurface for Joe Bookout's CARR AI system (migrated from the app scheduler 2026-07-25). Read and execute EXACTLY the live instruction set at "{{VAULT}}/Automation/local-tasks/resurface-task.md" — its STEP 0 MONTHLY GATE decides whether this firing does the work or exits immediately. All behavior lives in that file (thin-prompt law).