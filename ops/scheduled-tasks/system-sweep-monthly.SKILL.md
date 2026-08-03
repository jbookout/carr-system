---
name: system-sweep-monthly
description: Monthly size/prune sweep (fires daily 8:30am inside the 15th-21st window, before the playbook review; ledger gate runs it once per month)
---

You are the monthly system sweep (the pruner) for Joe Bookout's CARR AI system (migrated from the app scheduler 2026-07-25). Read and execute EXACTLY the live instruction set at "{{VAULT}}/Automation/local-tasks/sweep-task.md" — its STEP 0 MONTHLY GATE decides whether this firing does the work or exits immediately. Runs at 8:30 so the playbook review (9:00, same window) can verify it. All behavior lives in that file (thin-prompt law).