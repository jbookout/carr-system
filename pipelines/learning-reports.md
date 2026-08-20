# The learning jobs' output surface

*Moved here from the vault's `Automation/Learning/README.md` at the 2026-08-19
doctrine cutoff, and updated for where the reports now land. The five report
files are GENERATED. Never hand-edit them: rerun the job that writes them.*

## What is here

Five fixed filenames under `~/carr-system/out/Learning/`, each overwritten in
place by its job. Reading any one of them tells you what the machinery currently
knows and, more usefully, what it does not.

| file | written by | cadence |
|---|---|---|
| `placement-pull-latest.md` | `pipelines/pull_placement_metrics.py` | weekly, with the metrics pull |
| `weekly-learning-latest.md` | `pipelines/learning_jobs.py weekly` | weekly |
| `correction-miner-latest.md` | `pipelines/learning_jobs.py corrections` | weekly |
| `promotion-review-latest.md` | `pipelines/learning_jobs.py promotion` | monthly, with the playbook review |
| `conflict-surfacing-latest.md` | `pipelines/learning_jobs.py conflicts` | monthly |

Run them by hand any time:

```
cd ~/carr-system && ./bin/learning-weekly.sh
cd ~/carr-system && ./bin/learning-monthly.sh
```

## Why the repo and not the vault any more

The original placement put the output in the vault "because the briefs read the
vault, not the repo," and under `Automation/` rather than `DNA/` so that Joe's
placement counts and feature cells were never in a folder Dell can open.

The cutoff changed the first half and strengthened the second. Every clause here
is a PURE READER — `job_corrections` says so in its own output, "it proposes
nothing and writes nothing" — so each report is a rendering of rows that live in
`event`, `placement`, and `placement_metric`. A rendering is not a home, which is
exactly the test the 37 doctrine renders were retired against. And `out/` is
gitignored, so the personal-tier boundary the vault placement was protecting is
now structural rather than conventional: the reports cannot reach Dell's fork at
all, where `Automation/` merely happened to be a folder he does not open.

Three agent definitions read these paths and were re-pointed in the same commit:
`claude-tree/agents/marketing-coo.md`, `it-lane-worker.md`, and `it-support.md`.

## The lifecycle rule, stated at creation and unchanged

**This folder is not an accumulator and must never become one.** Every job writes
exactly one file, at a fixed name, overwriting the last run. There is no
dated-file series here to prune, no size sweep to schedule, and no growth curve.
History lives where history belongs: the metric snapshots are rows in
`placement_metric`, and the dated run reports sit beside these in the gitignored
`out/`. If a future job wants a dated series, it gets its own folder and its own
retention rule in the same breath, per the standing accumulator doctrine.

## What these reports will and will not do

They report. They do not conclude, propose, promote, retire, notify, or write to
any playbook. Below its evidence floor a job states the shortfall in plain
numbers and stops, which is a successful run and not a gap. Above it, a job lists
what crossed and still leaves the ruling to a human. No playbook change ships
without crossing its threshold, and no threshold crossing ships a change by
itself.

Thresholds are rows in `system_config`, not code:
`learning.min_posts_per_feature_cell`, `promotion.min_repeat_violations`. Tuning
one is a sentence, not a deploy.

## What to read first

Each report's first bold line is the whole summary. If you read nothing else,
read that line, in each file, once a week.
