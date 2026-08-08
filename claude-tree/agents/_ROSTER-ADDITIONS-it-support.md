# Roster additions: IT Support, a two-tier org (merge into README.md)

*Written 2026-08-02 by the build session that created `it-support.md` and `it-lane-worker.md`.
This is staging, not a second roster. Merge the sections below into `README.md` and delete this
file. Nothing here edits README directly, because another session was writing it at the same
moment, and the same reason produced `_ROSTER-ADDITIONS-sop-specialists.md` earlier tonight.*

---

## Section to add to README.md, as PART THREE, after the marketing lane

# PART THREE: IT Support

## What this is

The **Automation & system health** COO seat has existed in the COO seat roster (now in `.claude/agents/README.md`; formerly ai-operating-notes.md)
since Jul 22 2026 with nothing instantiating it. Every other seat on that roster either went LIVE
with a doctrine file behind it or got an agent tonight. This one sat as four lines of text, which
is why the system accumulated the defects the 2026-08-02 audit found in a single sitting: a
credential that had been silently unparseable for two days, a nightly chain running six hours
late, an export monitor that could not see, and three registers nobody had read since they were
written.

**Joe named the seat "IT Support" and the name is his ruling.** Agents in this folder are named by
human job title, not by file-name convention, because a person can tell what a job title does at a
glance.

## The org, in Joe's words (2026-08-02)

> IT Support has an IT manager who delegates to subagent IT workers for each lane of the system.
> They report back to the manager. The manager verifies claims with the context of the entire
> system in their mind. They come to an agreement on solutions and suggestions, the IT manager
> comes to you with findings, you handle obvious ones and report to me with ones that need
> escalation.

The chain: **Joe → the main session (CEO) → IT Manager → IT lane workers.**

| File | Role | Grant | Denied |
|---|---|---|---|
| `it-support.md` | IT Manager. Runs the `automation` lane itself, delegates the other five, re-runs the evidence behind every material finding, returns one merged report. | `Read, Grep, Glob, Bash, Agent` | `Write, Edit, NotebookEdit`, and every record verb, by the spawn-or-write constraint |
| `it-lane-worker.md` | IT lane worker. One lane per invocation, named on the first line of the prompt. Checks that lane's records, jobs, artifacts and pointers. | `Read, Grep, Glob, Bash` | `Agent, Write, Edit, NotebookEdit` |

The allowlist already excludes the file-writing tools; they are named in `disallowedTools` as well,
matching `costar-operator`'s belt-and-braces pattern, so read-and-report is structural rather than
a promise in prose. Neither agent holds a record verb. The manager's spawn bit is the reason it
holds no write access at all: **an agent that can spawn does not also carry write access** is the
standing constraint set on 2026-08-02, and this seat wanted the read-only posture anyway.

The six lane arguments map to the six COO seats: `marketing`, `pipeline`, `vendors`, `leads`,
`automation`, `sysdev`.

## Why the workers are ONE parameterised file and not six

This was a live design call and the reasoning belongs in the roster.

- About ninety percent of a worker's content is lane-invariant: the six interpretation rules, the
  provenance rail, the severity tiers, the report shape, the degradation contract. Six copies of
  that is six places to fix a rail and six chances to fix five of them. The forking problem
  `DNA/Team/skills-rule.md` exists to prevent applies here even though agents are a separate
  category from skills, because the failure mode is identical.
- The lane-variant part is a table of which artifacts, which tables, which SOP and which scheduled
  tasks. That is data. Data belongs in one table.
- Census discipline. Sixteen agent files where eleven do the job is exactly the roster inflation
  `skills-rule.md` guards against.
- **The counter-argument, because it is Joe's own thesis:** *"An agent who has one job would never
  forget their simple list of rules and procedures."* A parameterised worker risks reading all six
  rows and blurring them. The mitigation is written into the worker file: the manager names the
  lane on line one, and the worker is told to read its own row and ignore the other five. The
  thesis is about context load at runtime, not file count, and a worker invoked with `lane=leads`
  has exactly one job for that run.
- One mechanical fact settles it either way: a subagent must be a registered agent file to be
  spawnable by `subagent_type`. The worker needs its own file regardless. The only real question
  was one file or six.

## The rule that matters most in the manager's file

Joe described the manager as verifying "with the context of the entire system in their mind." The
manager's file carries a titled correction to that, because a manager agent starts cold every run.
It holds the system map, which is durable, but not what changed this afternoon, which is the part
that matters: sixteen migrations landed on 2026-08-02 alone.

> **THE MANAGER VERIFIES BY RE-RUNNING THE EVIDENCE, NEVER BY RE-READING THE REPORT.**

Concretely: for every worker finding rated RED or AMBER, the manager re-executes the exact command
the worker cited and reports the comparison. If a worker reports a number with no command
attached, the manager does not escalate it, it sends it back, which enforces the provenance rail
one level down instead of relying on goodwill. Every wrong claim caught on 2026-08-02 was caught by
running the query again. A manager that reads reports harder catches nothing and adds a layer of
confident compression.

## The escalation contract, three tiers

1. **The manager fixes nothing.** It reports. Every finding carries the remedy command pre-written.
2. **Fully evidenced findings with an obvious remedy go to the main session with the command
   pre-written, and the main session executes without going to Joe.** "Fully evidenced" means the
   manager re-ran it. "Obvious remedy" means one command, reversible or read-only, no judgement
   about priorities, no money, nothing outbound.
3. **Findings needing a judgement call, money, anything outbound, an identity edit, or a genuine
   fork go to Joe with the options laid out.** Anything touching production data, a migration, a
   scheduled task's existence, a credential, or a baseline threshold is automatically this tier.

Tiers 2 and 3 are separate sections of the report, because the reader's next action differs.

## Depth-3 caveat, and it is unproven

Nesting from the main session to a subagent is confirmed working and was executed on 2026-08-02. A
subagent spawning its OWN child was confirmed only as tool-available: the child reported holding
the `Agent` tool, but nothing was actually run at that depth. The manager-spawns-workers layer is a
reasonable bet, not a proven path.

The manager's file requires it to degrade: if a spawn fails, times out, or returns nothing, it runs
that lane's checks itself and says in the report that it did so and why. If more than two spawns
fail it stops delegating, runs the remainder inline, and flags the nesting layer itself as an AMBER
finding. **Reporting nothing because a delegation failed is the worst outcome available.**

This is the first real test of depth 3 in this system. The first live run should be watched.

## The eight named checks, from real failures

Both files carry these in full rather than by reference, per the SOP-specialist tier's thesis that
a one-job agent should never have to go look something up. Every one is a real 2026-08-02 error.

1. **A credential that exists but does not parse.** `CARR_DB_JOBS_URL` sat in `db.env` working
   since Jul 31 with an unquoted value containing an `&`, so `set -a; . db.env` died parsing it and
   two pipelines reported "NOT CONFIGURED" for two days over one missing quote.
2. **Three states for a gated job:** GATED (credential genuinely absent, not red) versus CREDENTIAL
   PRESENT BUT UNREACHABLE (red and actionable) versus MISSING (real absence).
3. **Never-run is not broken.** Four tasks with no `lastRunAt` were all new tasks whose first cron
   window had not arrived.
4. **Weekend-scoped tasks are not stale on a weekend.** Evaluate the real cron, not output age.
5. **Stale is not wrong.** Check `schema_migrations.applied_at` and `git log` before calling a
   prior claim wrong. A verdict that blames an auditor for a defect we then repaired is worse than
   no verdict.
6. **Absence in a partial search is not absence.** Four readers, one day, both directions: 17
   "duplicate" orgs that were tombstones, and six files "proved" gone by a `-maxdepth 2` scan when
   they sat at depth 3.
7. **A guardrail firing is not a failure.** `record-layer-dictionary.md` refused to export at 640
   rows against a 511 baseline because the schema legitimately grew. The guard worked.
8. **Verify by OUTPUT, never by the schedule existing or a job's own claim of success** (protocol
   rule 28).

Plus the two rails the whole design rests on: **provenance inline** (every number carries the
command that produced it; this caught four wrong claims in one day) and **severity that means
something** (graph-health returned 95 findings at zero HIGH, and an agent that reports everything
at one level trains people to read nothing).

## Triggers: which scheduled tasks should invoke this agent

All three already exist. **List before create; never recreate one.** The point of naming them here
is that each currently re-derives its own idea of what to check, and each should call `it-support`
instead.

| Task | Cadence | How it should use IT Support |
|---|---|---|
| `health-audit-monthly` | daily 9:00 inside the 4th to 10th, ledger-gated to one run | Its STEP 0 gate and its report-card grading stay as they are. `it-support` replaces the ad-hoc measurement pass: run it first, and feed its report into the 12-category re-grade instead of each firing inventing its own checks. This is the primary trigger. |
| `system-sweep-monthly` | daily 8:30 inside the 15th to 21st, before the review | The sweep prunes; IT Support tells it what is dead before it prunes. Run `it-support` first so the sweep is not pruning something a live pointer still resolves to. |
| `playbook-review-monthly` | daily 9:00 inside the 15th to 21st, after the sweep | Its job includes verifying the sweep actually ran. `it-support`'s report is the evidence for that verification, and its "could not check" section is exactly the input the review needs. |

Two more worth considering, flagged rather than decided: `nightly-record-layer` is where most RED
findings originate, and a weekly IT run on Monday morning would catch a broken nightly four days
earlier than the monthly audit does. And `restore-rehearse-weekly` already produces a PASS/FAIL
line that IT Support reads but does not trigger on. **Both are Joe's call, not this session's, and
neither task should be modified or created without his yes.**

## What needs Joe's ruling

**1. The manager cannot list scheduled tasks, and the seat's first named duty is
list-before-create on scheduled tasks.** `list_scheduled_tasks` lives on the scheduled-tasks MCP
server, which the manager does not hold. It can see task directories on disk (names and birth
times) but not cron expressions, enabled state, or `lastRunAt`, so the register check
(`health-check.py --tasks`) is UNCHECKED unless the invoking session hands the output over in the
pre-brief.

Three options: (a) leave it, and the invoking session always pastes the listing (works, depends on
a human remembering); (b) grant `mcp__scheduled-tasks__list_scheduled_tasks` by name in the
manager's frontmatter, which is read-only and whose server prefix is stable, unlike the
install-specific UUID prefixes on the record-layer server; (c) grant the whole scheduled-tasks
server, which also brings `create` and `delete` and should be declined. **(b) is the recommendation
and it is a one-line frontmatter change.** The risk it carries: a mistyped entry in a tool
allowlist fails in the direction of denial here, not permission, so the failure mode is a check
that stays UNCHECKED rather than an agent that can delete a task.

**2. Two live defects were found while verifying the checklist for this build.** They are real, not
hypothetical, and neither was fixed by this session, which holds no write scope in the repo:

- **The exporter credential's Python fallback path is broken.** `exporters/common.py` line 43 reads
  `db.env` itself when `CARR_DB_EXPORTER_URL` is absent from the environment, and it strips
  whitespace but not surrounding quotes. Both values in `db.env` are now single-quoted (the fix for
  named check 1), so the shell consumer loads them correctly and the Python fallback fails with
  `invalid connection option`. Observed effect: `run.sh health`'s export register reports
  UNREADABLE, so **the check that would tell us if the exports stopped is currently blind.** The
  exports themselves are fine, proven by the generated files sitting at 0.0d. One env file, two
  parsers, opposite requirements. Fix is a one-line strip of quote characters in
  `exporters/common.py`, in the repo, which this session's scope excludes.
- **The migration hash-drift check is unreachable from any credential in `db.env`.** The exporter
  role is denied on both `create table if not exists schema_migrations` and a plain select from it
  (`psycopg.errors.InsufficientPrivilege`, verified 2026-08-02). It needs the owner DSN through
  `tools/db-tap.py`, which derives it from `neonctl` and therefore dies whenever that OAuth token
  expires, as it did mid-session tonight. So rail 4e's clock has a single point of failure. Worth
  a ruling on whether the exporter role should get a select grant on `schema_migrations`, which
  would make the drift check runnable from an unattended job.

**3. Scope note.** The original brief for this build named one new agent file. Joe's mid-build
restructure into a two-tier org required a second, because a subagent must be a registered agent
file to be spawnable. Two files shipped: `it-support.md` and `it-lane-worker.md`. `README.md` was
deliberately not touched.

## Census line to update

The roster is no longer six chairs plus four SOP specialists plus three marketing agents. Add:
**IT Support, two files, one manager and one parameterised lane worker, the first two-tier agent
org in the folder.** It is not a chair, it never sits on a panel, and it is not counted in the
chair census.
