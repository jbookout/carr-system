---
name: playbook-review-monthly
description: Monthly playbook review (fires daily 9:00am inside the 15th-21st window, after the sweep; ledger gate runs it once per month)
---

You are the monthly playbook review for Joe Bookout's CARR AI system (migrated from the app scheduler 2026-07-25). Read and execute EXACTLY the live instruction set at "{{VAULT}}/Automation/local-tasks/review-task.md" — its STEP 0 MONTHLY GATE decides whether this firing does the work or exits immediately. All behavior lives in that file (thin-prompt law).