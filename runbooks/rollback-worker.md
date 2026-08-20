# Rolling the CARR MCP Worker back

*Written 2026-08-19, immediately after the procedure below was executed and
verified end to end on staging. Every command here has been run; nothing in it
is proposed from reading the code.*

## Why this file exists at this path

`ops.release` rows written from 2026-08-19 onward carry
`rollback_plan_ref = runbooks/rollback-worker.md`. That reference is checked at
approval time and is recorded permanently against each production release, so
the path is load-bearing: a release approved against a plan that does not exist
is a release whose recovery story was never written down. The first version of
that reference pointed at `RECOVERY.md#worker`, which is about **losing the
Mac** — it tells you how to re-point a Worker at a recovered database, and says
nothing about backing out a bad Worker build. Different failure, different
procedure.

## What "rollback" means here, precisely

The Worker ships as an **immutable provider version**. A rollback does not
rebuild anything and does not touch the database: it moves 100% of traffic back
to a version that was already serving. Schema is deliberately identical across
environments, so a Worker rollback is safe exactly when the previous version's
code is compatible with the *current* schema — which is true for every release
that did not itself apply a migration.

**A release that applied a migration is NOT covered by this runbook.** Rolling
the Worker back under a migrated schema is a different and larger decision;
stop and treat it as one.

## The procedure

### 1. Find what is serving now, and what preceded it

```sh
cd ~/carr-system/mcp-server
npx wrangler deployments list            # production
npx wrangler deployments list --env staging
```

Record the current version id and the one before it. Confirm the current
identity independently rather than trusting the list:

```sh
curl -s https://api.doctorcre.com/release
```

`worker_version.id`, `git_sha.value` and `env.value` are the authoritative
triple. `env` matters because `git_sha` and schema are identical across
environments by design — without it you cannot tell which deployment answered.

### 2. Promote the previous version

Production goes through the sanctioned wrapper, never raw wrangler, because the
wrapper re-checks the approved release and records the deployment:

```sh
cd ~/carr-system
./bin/deploy-worker.sh --promote-version <previous-version-id> \
  --performance-budget-ref ops/performance-budget-gate.py \
  --performance-budget-ms 1000 \
  --recovery-strategy rollback \
  --rollback-plan-ref runbooks/rollback-worker.md
```

### 3. Verify from the Worker, not from the command's exit code

```sh
curl -s https://api.doctorcre.com/release
```

`worker_version.id` must equal the version you promoted, and `verb_count` must
match what that build carried. **A deploy returning success and a registry that
answers are two different claims.**

### 4. Expect the verb-loss guard, and expect it to be right

A rollback usually removes verbs the newer build added, so the preflight
refuses:

```
REFUSED: this deploy would REMOVE 1 verb(s) from staging.
  last deployed: 140
  about to ship: 139
```

That guard exists because production silently went from 75 verbs to 66 in the
middle of a working session. A rollback is the legitimate case for overriding
it, and the override is deliberate rather than automatic:

```sh
./bin/deploy-worker.sh --env staging --allow-shrink
```

Say out loud, in the incident record, which verbs are going away.

## The rehearsal, and why it is required before approval

A production release cannot be approved without a successful
`recovery.rehearsal.worker` receipt bound to it. That is not paperwork: it
means somebody proved the rollback path works *before* needing it.

Rehearse on staging by moving it back one build and forward again. Source
deploys are permitted for staging, so a worktree at the previous commit is the
cleanest way in:

```sh
git worktree add .claude/worktrees/rollback-drill <previous-sha> --detach
./bin/worktree.sh --plumb .claude/worktrees/rollback-drill
cd .claude/worktrees/rollback-drill
./bin/deploy-worker.sh --env staging --allow-shrink
curl -s https://carr-mcp-staging.joe-bookout-carr-us.workers.dev/release
```

Then restore forward from a worktree at the newer commit and verify again.
Each staging deploy needs its own approved release at the exact plan hash the
deploy computes — build the manifest, record a candidate carrying the full
evidence set, approve, then deploy.

Record the receipt only from what actually happened:

```sh
./.venv/bin/python tools/ops-record.py run --kind check --service carr-mcp \
  --key recovery.rehearsal.worker --state succeeded --environment staging \
  --release-key <the production release key> \
  --source-ref bin/deploy-worker.sh --evidence-ref <what you verified>
```

## Verified execution, 2026-08-19

Run against staging while promoting the `export-email-domains` verb:

| Step | Result, read from `/release` |
|---|---|
| Starting state | `7c7e1bd1`, 140 verbs |
| Rolled back to `8e761a0c` | `8e761a0c`, **139 verbs** |
| Restored forward | `7c7e1bd1`, **140 verbs** |

The verb-loss guard fired on the rollback exactly as documented above and was
cleared with `--allow-shrink`. Both directions were confirmed from the Worker's
own `/release` endpoint rather than from the deploy output.

## Two things that will bite you

**The plan hash moves with its inputs.** An approval binds a specific plan
hash, and the promotion recomputes that hash from the git SHA plus the four
approval inputs you pass. Pass different inputs than the ones the approved
manifest was built with and it refuses with `THE PLAN MOVED SINCE APPROVAL`,
which is correct and means: rebuild the manifest with the exact inputs you
intend to promote with, then approve *that* hash.

**Approval needs the full evidence set together.** `an_approved_release_carries_its_evidence`
requires `test_evidence_ref`, `security_evidence_ref` **and**
`maker_verification_ref` — all three, at the moment of approval, not later.
Supply them on the candidate.
