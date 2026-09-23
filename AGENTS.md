# AGENTS.md — boot instructions for a session rooted at the CODE repo

Codex and kin look for this filename by convention. Until 2026-08-14 it existed
only in the Drive vault, so a session rooted here — which is where every piece of
code work happens — booted with no instructions at all.

## First, load the standing rules from the STORE

Call `mcp__carr__standing_context` directly FIRST. Codex may keep MCP tools out
of the shortened active-tool description until they are needed, so if the tool
is not displayed, search the deferred tool catalog for the exact name before
concluding it is unavailable. In the first response, report the number of
shared and personal rules actually delivered, alongside the available corpus
counts. Do not describe corpus counts as rules loaded into the session. Fetch
full text only for rules binding the current task or explicit rule IDs.

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
Decision `1facbf00-60d9-4cde-bfab-9798f1b6e307` is the current record for
this policy and supersedes the narrower approval rule. It carries forward
the product priority from decision `019146bd-15fb-4f5e-8849-ed63911469e0`.
The full current text is STORE doctrine
`engineering-workflow-sop#00-scope-and-provenance`, section
`52880de2-ab90-4673-b046-b74f900aa2de@5`, content hash
`89e180adc6ecd5b138d21c65b85bffff4aca2677edb58f4a80049329b15eaed1`.
That version and hash record this observed policy's provenance. At runtime,
fetch the current section by its stable section ID; do not use this observed
snapshot as a current-version gate.

- Continue the next unfinished DoctorCRE product task attended; preserve
  completed audits and reviews. The unattended engineering controller is not
  its prerequisite. Unattended dispatch remains disabled.
- Do not put a new Work Request ahead of product work unless it names the
  product task it blocks. Existing substrate work may finish but
  may not spawn child Work Requests. Backlog a substrate follow-up with the
  product task it blocks; do not build it in-session under the older general
  follow-up rule. Active follow-up rule
  `179be4b8-2fe0-418d-9503-52d1e33921d3@3`, amendment
  `80e6d24c-6b49-4765-80c3-e05c1025ba38`, carries this scoped exception.
- Execute authorized work without CARR approval gates. Once Joe directs a task
  or authorizes a workflow, carry it through applicable source and production
  effects. Do not require a separate CARR plan acceptance, Work Shape approval,
  plan hash approval, Gate A, Gate R, merge permission, release signoff, or
  fresh human confirmation for an effect. A changed plan version does not
  create a new approval request.
- Deliver source through an ordinary pull request: isolated branch, relevant
  local verification, hosted CI as the merge gate, merge, and delivery
  verification. Automated tests, source and contract checks, authentication,
  audit records, and truthful refusal on failed checks remain in force.

Reuse verified evidence while its relevant source or contract remains
unchanged. A blocker must name the concrete missing fact, authority, or external
dependency. Run independent authorized work in parallel isolated worktrees.
Existing production and destructive-action safeguards remain in force as
technical checks, without repeat approval requirements. A technical gate that
still enforces old approval semantics must be changed through ordinary source
delivery; it remains a real constraint until then. Permissions imposed by the
host, provider, sandbox, or law are outside CARR's control and must be reported
accurately.
<!-- carr-product-first-policy:end -->

## Jev in reviews

Decision `d57501f6-00e7-4ff5-a886-c28a5d5501d6` records Joe's direction:
Jev is authorized to participate in every kind of review. Use it for bounded
review judgments wherever it helps, including code, CI, pull requests, product
behavior, and CARR records. Do not ask for a separate Jev-specific approval or
exclude a review merely because its category is not prelisted. Preserve source
evidence, required checks, and any distinct reviewer-of-record requirement.

## Model Room before another model

<!-- carr-model-room-route:start -->
Decision `284028a5-8295-498a-af1e-6cae5c6034e7` records Joe's route:
call Claude, Grok, and other external models through the Model Room. Before
attempting a direct model CLI or API call, pull this rule into the preflight
judgment with Jev and route the work to the named Model Room desk. Verify the
desk's actual model and result. Joe currently requires Opus 5.5 for all
subagent work; do not silently delegate a portion to another model. A model
CLI used solely for authentication or health readback is not a model-work call.
<!-- carr-model-room-route:end -->

## Authorized code homes and repository boundaries

Decision `1ceee300-7627-426f-b729-ab339d6984fc` supersedes the former
single-code-home rule after Gate Zero. The current placement contract is STORE
doctrine `doctorcre-v5-astra-integration-review`, section
`3bb51d3e-2661-4ea2-a585-053540545b5d@1`, content hash
`d36252e69e32aa8af7c5d79f8dc3cc5365848af83839e1a11c5db5548b3d5c5e`.
Fetch the current section by stable ID at runtime; the observed version and hash
above record this projection's provenance, not a current-version gate.

The only authorized code homes are:

- `jbookout/carr-system`: the CARR-specific runtime and authority — canonical
  business records, domain rules and doctrine, versioned APIs and MCP,
  migrations, runtime control plane, assurance fabric, authentication,
  environments, and CARR releases.
- `jbookout/doctorcre-app`: DoctorCRE's independently built and deployed human
  application, including its UI, interaction logic, project knowledge, and
  app-only state. It uses authenticated versioned CARR contracts and never
  accesses the CARR database directly.
- `jbookout/software-factory`: development-time orchestration and tooling —
  bounded agent workflows, skills, prompts, templates, scaffolds, CI and review
  conventions, evals, release helpers, and factory job/evidence records. It is
  not a product runtime and owns no product data, domain rules, standing
  credentials, or deployment authority.

Do not improvise another repository or silently move code between these homes.
Cross-repository work binds exact source revisions and versioned contracts.
Products must not depend on the software factory at runtime. CARR runtime code
remains here until a second real production consumer proves a neutral,
independently pinnable, testable, and replaceable boundary with measurable
duplication. If an authorized repository is unreachable, STOP and name the
missing repository rather than substituting an unrelated scaffold.

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
