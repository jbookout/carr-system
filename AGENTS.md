# AGENTS.md — boot instructions for a session rooted at the CODE repo

Codex and kin look for this filename by convention. Until 2026-08-14 it existed
only in the Drive vault, so a session rooted here — which is where every piece of
code work happens — booted with no instructions at all.

## First, load the standing rules from the STORE

```
./run.sh call standing-context '{}'
```

Recite the counts it returns in your first response, so the partner can see what
is binding you. That command authenticates as the `joe-local` machine actor
through the deployed Worker; it needs no MCP server and works from a plain shell.

If it fails, say so plainly and name the error before doing anything else. The
rendered files (`DNA/compiled-rules-shared.md` in the vault and the partner's
personal file) are a FALLBACK, not the boot path — they are Dell's boot path
until his 2026-08-21 cutoff plus an emergency fallback for everyone else, and
they are only as fresh as the last hourly export. A silent fallback is worse
than a loud failure.

## This repo is the ONLY code home

`jbookout/carr-system`. The record layer, the MCP server, every migration and all
durable code are here and nowhere else. If you cannot reach this repo, STOP and
say so rather than improvising a home — a cloud session once filed an entire
system audit into an unrelated empty scaffold repo, which is the same as losing
it.

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
