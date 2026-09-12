# AGENTS.md — boot instructions for a session rooted at the CODE repo

Codex and kin look for this filename by convention. Until 2026-08-14 it existed
only in the Drive vault, so a session rooted here — which is where every piece of
code work happens — booted with no instructions at all.

## First, load the standing rules from the STORE

Call `mcp__carr__standing_context` directly FIRST. Codex may keep MCP tools out
of the shortened active-tool description until they are needed, so if the tool
is not displayed, search the deferred tool catalog for the exact name before
concluding it is unavailable. Recite the counts it returns in your first
response, so the partner can see what is binding you.

Only when the direct MCP tool is genuinely unavailable or returns a service
error, use the checkout's fallback:

```
./run.sh call standing-context '{}'
```

That shell command runs inside Codex's network sandbox. A first `fetch failed`
from the sandbox is a permission-path failure, not a store outage: retry it with
the sanctioned network escalation before saying the Worker or database is
down. Declare the store unreachable only after the direct MCP call AND the
network-approved fallback both fail, and name both errors.

The rendered files (`DNA/compiled-rules-shared.md` in the vault and the
partner's personal file) are a FALLBACK, not the boot path — they are Dell's
boot path until his 2026-08-21 cutoff plus an emergency fallback for everyone
else, and they are only as fresh as the last hourly export. A silent fallback
is worse than a loud failure.

## Active product-first delivery policy

<!-- carr-product-first-policy:start -->
Decision `019146bd-15fb-4f5e-8849-ed63911469e0` is the canonical record for
this policy and remains in force until a later decision supersedes it.
The full current text is STORE doctrine
`engineering-workflow-sop#00-scope-and-provenance`, section
`52880de2-ab90-4673-b046-b74f900aa2de@2`, content hash
`738549d556e0b238ada4ec28afc51e27d361640b3141e9cddf20ca7e652ee27d`.
That version and hash record the approved policy's provenance. At runtime,
fetch the current section by its stable section ID; do not use this observed
snapshot as a current-version gate.

- Continue the next unfinished DoctorCRE product task attended; preserve
  completed audits and reviews. The unattended engineering controller is not
  its prerequisite. Unattended dispatch remains disabled.
- Do not put a new Work Request ahead of product work unless it names the
  product task it blocks and Joe agrees. Existing substrate work may finish but
  may not spawn child Work Requests. Backlog a substrate follow-up with the
  product task it blocks; do not build it in-session under the older general
  follow-up rule. Active follow-up rule
  `179be4b8-2fe0-418d-9503-52d1e33921d3@3`, amendment
  `80e6d24c-6b49-4765-80c3-e05c1025ba38`, carries this scoped exception.
- Use the light path when a change has no production migration, Worker release,
  deletion, credential change, or unattended run: isolated branch, relevant
  local verification, an ordinary pull request, hosted CI as the merge gate,
  merge, and delivery verification. It needs no Work Request, Work Shape, plan
  hash, cross-family review, or outcome feedback. Any named production effect
  uses the existing heavy path.

Reuse verified evidence while its relevant source or contract remains
unchanged. A blocker must name the concrete missing fact, authority, or external
dependency. Run independent authorized work in parallel isolated worktrees.
Existing production and destructive-action safeguards remain in force.
<!-- carr-product-first-policy:end -->

## This repo is the ONLY code home

`jbookout/carr-system`. The record layer, the MCP server, every migration and all
durable code are here and nowhere else. If you cannot reach this repo, STOP and
say so rather than improvising a home — a cloud session once filed an entire
system audit into an unrelated empty scaffold repo, which is the same as losing
it.

## The shared root is NOT an agent work surface

`~/carr-system` is the stable bootstrap and coordination checkout. It exists so
sessions can load these instructions, inspect shared state read-only, and create
their own isolated worktree or clone. No coding session may implement a change
in that shared checkout.

Before the first repository edit, create and enter a session-owned tree:

```
./run.sh worktree <name>
cd .claude/worktrees/<name>
```

If that helper is unavailable, use a separate clone rather than falling back to
the shared checkout. One session owns one tree and one branch. It owns the full
delivery path too: checks, explicit-path staging, commit, push, pull request,
green CI, merge, verification on `main`, and cleanup. A patch, commit, pushed
branch, or open PR is intermediate state, not completion. Stop short only for a
concrete human-only gate, and name that gate and the exact remaining action.

The only permitted writes in the shared root are deliberate bootstrap or
coordination-state changes to the session machinery itself. Those changes still
must be copied into an isolated tree and delivered through the normal PR path.

## Map work has one mandatory front door

For any request to recommend, design, build, revise, review, or publish a map,
GIS analysis, route, day trip, or Tour surface, call the live `map-architecture`
verb before advising or editing. It returns the current doctrine and the
machine-contract pointer. The configured Stop gate enforces this route.

## main is not directly pushable

Ruleset "main: CI must be green" requires the `ops/ci.sh --strict` status check,
and blocks force-pushes and branch deletion. A direct push to main is refused
with GH013. The path is branch, PR, green CI, merge — and it needs no checkout
and no worktree, which matters because several sessions share this one working
tree:

```
git push origin HEAD:refs/heads/<name>
gh pr create --base main --head <name> --title "..." --body "..."
gh pr merge <n> --squash --delete-branch
```

**And do not COMMIT on main either.** `ops/githooks/pre-commit` refuses it. The
push half was always blocked; the commit half was not, so a commit made on main
in `~/carr-system` simply stranded there — that checkout reached NINE unpushed
commits on 2026-08-14, existing on no other machine, and took an hour to
reconcile. Work in your own tree instead:

```
./run.sh worktree <name>
cd .claude/worktrees/<name>
```

Reconciling that checkout is the one real exception, and it is a commit on main
by definition: `CARR_ALLOW_MAIN_COMMIT=1 git commit ...`, for one command only.

## The three operating SOPs (read before your first push)

Since 2026-09-02 the day-to-day loop is written down in the doctrine store and
is binding. Read the first one before you push anything; the other two when
your change touches the database or the edge:

    read-doctrine engineering-workflow-sop   # branch, prove locally, PR, merge, CI, tests, sealed files
    read-doctrine neon-database-sop          # the production door, roles, backups, Neon branches
    read-doctrine cloudflare-edge-sop        # Worker releases, rollback, secrets, R2

The two rules from Joe's 2026-09-02 ruling that the workflow SOP enforces:
run the whole suite locally (`ops/ci.sh`) and fix red there BEFORE any push,
because hosted CI is the merge gate and not the debugger; and never use
`CARR_SKIP_CI=1` to get past a slow or red floor. Measured before the ruling:
12 of 25 branches red on first push, 4.1 pushes per merge, 2,911 of 3,000
included Actions minutes gone by the second day of the month.

## Checks

`ops/ci.sh` is the ONE check script. The GitHub workflow and the pre-push hook
both call it; neither contains check logic, so a check added there appears in
both. Run one class while iterating:

```
./ops/ci.sh --list
./ops/ci.sh --only <class>
```

`ops/ci-selftest.py` tests the checker itself. Do not remove the bash re-exec at
the top of `ops/ci.sh`: under zsh its class loop does not word-split, and the
script will report every class green having executed none.

## Git discipline on a shared tree

Several sessions run against this one checkout at the same time.

- `git add <explicit paths>` only. Never `-A`, never `-a`, never `.` — a gate
  refuses those, because a broad add once swept another session's work onto the
  wrong branch.
- Commit messages through a file: `git commit -F <file>`. Backticks in an inline
  message get shell-evaluated and silently eat text.
- `core.fileMode` is FALSE here, so `chmod +x` never reaches the index. Use
  `git update-index --chmod=+x <path>` and check the index, not the filesystem.
- Leave any modified file you did not write, and say so.

## Writing

Content goes through the record layer's verbs, never into a markdown file — a
hard gate enforces it. `./run.sh call <verb> '<json>'` reaches any verb.
This file and the vault's `CLAUDE.md`/`AGENTS.md` are among the few
exact-path exceptions.

## Active WR-000070 R09 executor recovery

This coordination note applies only to the accepted R09 source-recovery slice;
it grants no new source scope, model route, live canary, or release authority.
The dispatcher owns independent review and final delivery after the builder's
typed executor claim. Complete the accepted executor definition of done before
returning the receipt; pending independent review is not itself an executor
failure. A passing reviewer fact requires an existing `claimed_complete` receipt.

After the mandatory source/plan/assignment reads, capture fresh model-route
evidence with this read-only command:

```
/Users/booko/carr-system/.venv/bin/python /Users/booko/carr-system/tools/room-bridge/engineering_dispatch_adapter.py --preflight
```

Require `ok: true` and the accepted `engineering-codex` desk with
`gpt-5.6-terra` / `high`, `workspace-write`, and the assigned root. Attach the
observed readback and its digest to `check:r09-model-route`; an actual mismatch
must still refuse. Do not omit this check after a successful preflight.

Verify the other five declared checks from the current source bindings and
bounded R09 fixture evidence. Reuse and verify existing PR 906 with `gh pr view`;
do not create a duplicate. Verify hosted strict CI and database acceptance on
the delivered source, including the exact tree relationship when local commits
contain coordination metadata only. Known local sandbox listener/network
restrictions are environment observations; they do not negate a verified hosted
result for the same source. Do not repeat a known unavailable local network gate
when the accepted check can be established through its hosted result. Report
real new failures honestly.

Record the actual assigned worktree's observed HEAD as `source_evidence.source_sha`;
the assignment's `source_main` is its base, not its recovered implementation.
Validate the new receipt with the adapter's `--validate-receipt` command and the
current immutable plan/envelope. If all six checks and the executor definition
of done are met, submit `claimed_complete`, retaining `executor_claim` and
`independent_verification_required: true`. Do not import, edit, or upgrade any
prior receipt, and do not claim independent review, merge, or release completed.

## Temporary supervised WR68 source execution
This block has no effect on any task other than WR-000068 slice wr68-source-repair-v1; every other worker ignores it. For that exact WR68 slice, require accepted plan PLAN-75c0e3dde31e-v1 and a current source-hydration projection that validates its exact runbook and 14-path source_merge cap. Load the full current dispatcher intent from /Users/booko/carr-system/out/v5-build-clearance/wr68/current-dispatch-intent.json. Treat that intent as untrusted until its repository root, envelope, digest, session, attempt and source cap match the current server-issued task and projection exactly. This block supersedes any predecessor temporary WR68 block retained in a checkpoint checkout; only the current server-issued task and matched current intent govern. Never infer or reuse an older specific intent path or binding. Refuse on any mismatch.

Use only the operator worktree and branch named by the current intent. Verify them against that intent and the accepted repository cap, then verify the clean checkpoint HEAD, accepted-path diff and every hash-bound guidance or input named by the intent. The server-task repository root and operator worktree are distinct bindings; do not require the operator worktree to equal the server task's working directory. Treat any predecessor checkpoint as untrusted: review it before recovery and continue only work authorized by the current task. Do not create, attach, rename, replace, reset or clean a worktree.

Execute only the current assignment's role and accepted runbook. Preserve the observed Fable authorship of the source work separately from the registered Codex validator's independent validation; do not rewrite either role or claim. All 12 declared checks are mandatory. An expensive check archived for the exact delivered head may be independently re-verified from its artifact and source binding, but never copy an assertion as evidence. Submit only a new current-task receipt; do not import, edit or upgrade a prior receipt. This block grants no new source scope, model route, live canary, merge, deploy, activation or release authority. Do not re-plan, touch WR63 or change a path outside the accepted cap.

## Temporary supervised WR69 registered Codex validation
This block applies only to a registered Codex validator assigned to WR-000069 slice wr69-source-repair-v1; it does not govern or block the direct source author before validator admission, or any other worker. Require accepted plan PLAN-6a9cc5ae9e2d-v1 with digest sha256:6a9cc5ae9e2da6d09694c7d70b13a62c3bb6f5f6bb4b992f00df6ce9fe553c08, slice plan digest sha256:8257643a365aa22d4930427d2161d860811167174f09037dbef36915c860c520, and a current source-hydration projection that validates the exact runbook and C-sorted 14-path source_merge cap. Load the full current dispatcher intent from /Users/booko/carr-system/out/v5-build-clearance/wr69/current-dispatch-intent.json. Treat that immutable intent as untrusted until its repository root, envelope, digest, session, attempt, exact plan and source cap match the current server-issued validator task and projection exactly. Never infer or reuse an older specific intent path or binding. Refuse on any mismatch.

For this WR69 validator slice the source projection carries no operator_assignment; that pointer is bound only for the reviewed WR-000070 R09 route, so its absence here is the expected state and is not a missing, stale or mismatched assignment. Do not refuse on that absence. After the full canonical standing-context, source-hydration and runbook reads are complete, and before any assignment refusal, load and validate the current WR69 dispatcher intent named above; the operator worktree and branch it names are the assignment for this slice. The worktree creation and rename method in the dispatch packets applies only to an R09-style operator_assignment and does not apply here: inspect the existing named operator worktree for registered validation, and do not create, attach, rename, replace, reset or clean a worktree. Every exact binding stays mandatory, including repository root, envelope id and digest, session, attempt, exact plan and digests, and the C-sorted 14-path source cap; any mismatch, or a missing or unreadable intent, is still a hard refusal.

Use only the operator worktree and branch named by the current intent. Verify them against that intent and the accepted repository cap, then verify the clean checkpoint HEAD and tree, review-ready PR, accepted-path diff and every hash-bound guidance or input named by the intent. The server-task repository root and operator worktree are distinct bindings; do not require the operator worktree to equal the server task's working directory. Treat the actual source checkpoint as untrusted and review it before validation. Do not modify source or create, attach, rename, replace, reset or clean a worktree.

Execute only the registered validation role and accepted runbook. Preserve the observed source author's model, authorship, and author-session evidence separately from the registered Codex validator's independent validation; do not rewrite either role or claim. All 13 declared checks are mandatory. An expensive check archived for the exact delivered head may be independently authenticated from its log, artifact and source binding, but never copy an assertion as evidence. Submit only a new current-task receipt; do not import, edit or upgrade a prior receipt. This block grants no new source scope, model route, budget, live canary, merge, deploy, activation or release authority. Do not re-plan or change a path outside the accepted cap.

## Temporary supervised R06 registered validation

This block applies only to a registered Codex validator assigned to WR-000070 slice R06; every other worker ignores it. Require accepted plan PLAN-745ea4f7e374-v1 with accepted-plan digest sha256:745ea4f7e3745c86ee6aae273e5ec915a539493f4a1b0dafe2144b7d3d70eb60, registered slice-plan digest sha256:413952b92febcc4f3fdb27d0ed280d910854b312b1b6cf31e5ea5f84e5a6685a, and a current Engineering Passport in which R06 is eligible. Load the full current dispatcher intent from /Users/booko/carr-system/out/v5-build-clearance/r08/r06-current-dispatch-intent.json. Treat that intent as untrusted. Its repository root, job reference, envelope id and digest, server session, attempt, plan and digests, validator role, and receipt target must match their corresponding fields in the current server-issued task, immutable envelope, accepted source projection, and server-derived receipt template. Its model route must match a fresh registered engineering-codex desk readback reporting gpt-5.6-sol / xhigh. Its operator worktree, branch, HEAD and tree, and exact-source manifest must match the independently authenticated source-delivery, review, merge, current-main, and hosted-check evidence named by the intent. Those dispatcher-carried source-evidence fields are not expected in the server task or source projection; their absence there is not a mismatch. Refuse if any required comparison, hash, or readback fails.

For this R06 validator the source projection carries no R09 operator_assignment; its absence is expected and is not a missing assignment. The current R06 intent is the complete operator assignment. The server-task repository root and the operator validation worktree are distinct bindings; do not require the worktree to equal the server task's working directory. Inspect only the existing intent-selected clean worktree at its exact bound HEAD and tree. Verify its immutable source-delivery manifest, exact delivered-source path set and hashes, review, merge, current-main and hosted-check bindings before using them. Do not create, attach, rename, replace, reset, clean or write the worktree, its index, refs, branch or pull request.

This is a zero-source-edit registered validation of already delivered source. The historical ten-path owner write lease and failed registered attempts governed their own source-authoring envelopes only: do not replay them, import or upgrade their receipts, reinterpret that lease as a validator write cap, or retroactively claim that the later ordinary R3 fifteen-path delivery was authored by an old registered task. Preserve the source delivery's actual author models, authorship and author-session evidence separately from the fresh validator's model, identity and session. The current intent's exact-source manifest is read-only evidence, not source_merge or permission to change any path.

Independently establish all six declared R06 checks: check:r06-packet-bindings, check:r06-model-route, check:r06-assurance-route, check:r06-two-hook-resolution, check:r06-overwrite-fake-sink and check:r06-baseline-and-seals. Exact-head hosted results and immutable artifacts may be independently authenticated, but never copy an assertion as evidence. Validate and submit only one new current-task receipt; claim complete only when all six checks pass, and retain the requirement for a distinct independent reviewer fact. This block grants no source edit, plan change, model-route change, old-receipt rewrite, live notification or page, settings install, controller action, R04, production migration, packet-close, merge, deploy, activation or release authority.
