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
This block has no effect on any task other than WR-000068 slice wr68-source-repair-v1; every other worker ignores it. For that exact WR68 slice, require accepted plan PLAN-75c0e3dde31e-v1 and a native source-hydration projection that validates its exact runbook and 14-path source_merge cap. If any of those WR68 bindings mismatch, refuse.

After the mandatory standing-context and source-hydration checks, read these exact coordinator inputs:
- /Users/booko/carr-system/out/v5-build-clearance/wr68/wr68-next-source-assignment-intent.json
- /Users/booko/carr-system/out/v5-build-clearance/wr68/wr68-next-execution-recovery-guidance.v1.json (sha256 7decd60b30fba58d5f5d4d2eb9feb1a10dcbfb4f04ce0496c132169827480c65)
- /Users/booko/carr-system/out/v5-build-clearance/wr68/wr68-first-0492-implementation-boundary.v1.json (sha256 d1c0025f5f4cee4e62705c870e597c508cdb63c8923766988170761d459e1958)

Verify their work request, slice, plan, envelope, path cap, and hashes match the server packet. Independently verify source_main against fresh origin/main with the sanctioned per-command GitHub authentication route. From the canonical repository, execute the intent's exact worktree_helper_command; it invokes ./run.sh worktree through the sanctioned per-command authenticated Git alias and creates the helper-named branch from source_main. Then execute the intent's exact branch_rename_command inside that new tree and verify its absolute root, renamed branch, clean HEAD, and source_main ancestry before editing. Never invoke plain ./run.sh worktree. Use no other worktree or branch. These coordinator facts narrow the server's accepted source authority and never widen it.

Then begin implementation immediately by creating reserved migration 0492 from the named current predecessor witnesses. Do not re-plan, repeat the general source study, inspect a nonexistent 0492 precursor, wait for hosted CI, merge, deploy, activate, touch WR63, or change a path outside the accepted cap. Use the repository-owned ops/ci.sh classes and one final strict run.
