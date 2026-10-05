# pstack platform port

Lauren Tan's MIT pstack comes from `cursor/plugins`, commit `e43c7ee2`, directory `pstack`.
The supplied source inventory contains 161 files, including hidden manifests and binary assets.
`UPSTREAM.json` binds each original file by SHA-256. LICENSE and the Cursor manifest remain unchanged. The repository inventory declares the seven exact upstream raster paths as binary so its text scanner can inspect the remaining source; its checker stays unchanged. The commit path checker preserves directory depth only for paths declared by this staged upstream manifest. Unlisted paths and filename checks keep their existing rules.

This port changes platform references only. Principles remain byte-identical.
Playbook steps, order, thresholds, review contracts, and prose remain upstream apart from literal tool and path substitutions.
Each operational skill reads this file and `pstack-models.md` before its upstream body.
Original platform-specific statements below those pointers use the mappings here.
The setup result is committed. It overrides older model defaults without rewriting Lauren's defaults.

## Platform mappings

| ID | Upstream reference | Resolution |
| --- | --- | --- |
| M1 | Cursor `Task`, `generalPurpose`, `Comment Sicko`, and `is_background` | Claude Code `Agent`, `general-purpose`, `comment-sicko`, and `run_in_background: true`. Use the registered `poteto-agent` for playbook workers and `comment-sicko` for comment review. Installed plugin agent names can carry the `pstack:` namespace. On Codex, use its native `spawn_agent` for the same roles, with the registered agent file as the role brief; model-specific headless delegates use the routes below. Model routes preserve family diversity. |
| M2 | `AskQuestion` and `allow_multiple` | Claude `AskUserQuestion` and `multiSelect`. Keep the original questions, choices, and meanings. Split a prompt if native option limits require it. On Codex, use its structured user-input tool with the same choices. |
| M3 | `~/.cursor/rules/pstack-models.mdc` and model picker | Read `plugins/pstack/pstack-models.md`, resolved from this installed plugin root. `/setup-pstack` updates that file through the ordinary source path. Available native models and CLI readbacks replace Cursor's entitlement list. Do not invent a model or silently change its family. |
| M4 | `cursor-team-kit` `deslop`, `control-ui`, `control-cli`, and built-in `create-skill` | These dependencies were absent from installed Claude plugins and the Claude, Codex, and agent skill trees. This port supplies minimal equivalents in `skills/`. They preserve scoped cleanup, real CLI control, Playwright UI proof, and skill draft/test/iterate. They are port additions, not Lauren's original implementations. |
| M5 | Cursor cloud agent, `environment: "cloud"`, `cloud_base_branch`, cloud URL/dashboard | A headless agent in its own worktree or clone at the exact base revision, using `codex exec` or `claude -p`. Track process/session, owned branch, head SHA, and output. This is local headless isolation, not a hosted VM. A workflow that specifically needs a separate machine remains unsupported until that environment exists. |
| M6 | Origin forge and `origin pr` | Resolve to `gh`, the existing upstream fallback. Preserve create/edit/view/check/thread/merge semantics and all independent-review and stack rules. Retained Origin examples describe the upstream alternative; do not execute them here. Graphite `gt` is a separate upstream dependency, not Origin. |
| M7 | Cursor `agent-transcripts`, `.cursor/projects`, message schema, pinned chat/sidebar, readonly stripping MCP | Use only the current workspace and named run. Claude JSONL is under `~/.claude/projects/<encoded-workspace>/<session>.jsonl`; resolve the actual directory and session, never glob unrelated workspaces. Codex uses `ops/codex-history.py` with validated session, cwd, and transcript path. Parse the native record types; do not reuse Cursor's schema. For no-write readers use native read-only controls and read-only connector tools. Claude Agent has no Cursor `readonly` field; do not pass it or claim it strips MCP. Pinned/sidebar checks need observed native session state or the operator's evidence. |
| M8 | `.cursor/skills`, `.cursor/worktrees`, install `/add-plugin` | Project skills use `.claude/skills`; Codex discovery uses links under `~/.codex/skills`. Agent branches use owned git worktrees. `scripts/install.sh` registers this repository's local marketplace and installs pstack through Claude's CLI. No hand-written settings. |
| M9 | Cursor routines, `update_state`, `SendToUser` secret requests, `api2.cursor.sh` webhooks | `make-bot-ui` remains dormant for these operations. No equivalent routine state or secure-request contract was proven. The URL and procedure stay as upstream reference; no guessed endpoint, credential flow, or substitute backend. |
| M10 | Cursor `/loop` and built-in babysit | Claude's native `/loop` when available. For Codex/headless work, a coordinator supervises event wakeups and heartbeats, rechecks the unchanged exit predicate, and keeps the same decision trail. A single CLI invocation is not a persistent wake loop. Use the pstack Babysit playbook, not a client's similarly named built-in skill. |
| M11 | Cursor mentions in unchanged scripts, tests, lockfile, or package identity | Keep byte-identical. `cursor` pagination variables are data, not platform calls. GitHub reviewer identity `cursor` and `CURSOR_AUTOMATION_ID` remain necessary to recognize existing bot reviews. The package name is upstream identity. `worktree-audit.sh` still has Cursor transcript discovery; its transcript/pinned-chat advice is unsupported here and is not deletion authority. |
| M12 | Cursor documentation, provenance, generic UI terms, or other client-only claims | Keep attribution and historical names. Cursor mode/icon/reminder metadata has no proven native auto-load equivalent; the short AGENTS.md/CLAUDE.md pointer supplies explicit routing. Capability claims apply only after native readback. Native-only operations with no equivalent remain dormant rather than being silently omitted. |

## Delegate routes

Role names, models, efforts, and panel sizes come from the current `pstack-models.md`. Derive each native model and effort argument from that role on every dispatch, including after `/setup-pstack` changes the budget. The commands below show the committed initial setup, not fixed effort requirements. A Claude Agent cannot select a GPT or Grok slug.
For a Claude role, use Agent with the requested registered subagent type and the configured Claude model. The initial setup uses Claude Opus 5.5.
Pass the effort through the native supported control. For headless Claude, use `--model claude-opus-5-5 --effort max`.
If a native Agent cannot set the required model or effort, use that explicit headless route in an owned worktree.

Sol roles use `codex exec -m gpt-6.1-sol -c model_reasoning_effort="xhigh"`.
Grok roles use `grok -p --model grok-4.7 --reasoning-effort xhigh --permission-mode auto`.
The installed Grok CLI has no separate fast flag. `fast` remains the role's selection label for Grok 4.7.
The explicit permission mode keeps print-mode shell tools active. It does not expand the brief's scope.
Use the native sandbox for no-write roles and owned worktrees for writing roles.
Keep briefs in files, pass them as data, and preserve background execution, output ownership, and independent review.
For `auto` and `inherit-parent`, omit explicit model and effort selectors as upstream specifies. Never replace a different-family seat with another Claude seat.
No route authorizes new credentials, data deletion, production effects, or paid API work.

## Remaining platform limits

Benny is preserved outside the registered slash-skill directory, as upstream requires.
Its two workflows, trusted thread marker, immutable source coordinates, coordinator-only Slack writes, fail-closed capability checks, repeated UI proof, verify-only existing-fix route, and draft-only delivery remain intact.
Cursor's `/automate` discovery and draft editor, Slack action names, operation editing, model picker, and store paths have no proven equivalent here.
Creation and activation remain dormant. This port does not install a scheduler or substitute a CARR backend.

`orch frontier set` still requires Graphite, as its upstream code and tests specify.
Its other TSV/JSON bookkeeping and the GitHub `watch-pr` tool run on Bun unchanged.
This task neither installs Graphite nor changes that dependency. A `gh` forge mapping does not claim to replace Graphite's stack graph.
UI control uses the existing target project's Playwright harness. Native simulators, Cursor IDE control, and hosted-VM provisioning need separate proven adapters.
Skills with Cursor-only `paths`, `mode`, `icon`, `color`, or `reminder` metadata keep that source metadata; this port does not claim native autoload from it.

Claude Code 2.1.289 rejects an agents directory in `plugin.json` with `agents: Invalid string: must end with ".md"`.
The Claude manifest therefore lists the two files under `./agents` explicitly. The original Cursor manifest still uses its directory.
This follows the [Claude plugin manifest reference](https://code.claude.com/docs/en/plugins-reference).

## Installation and verification

`bash plugins/pstack/scripts/install.sh` installs the user-scoped Claude plugin and Codex skill links.
Keep its source checkout available for the local marketplace and the symlink targets.
The global installation also exposes pstack in doctorcre-app sessions without changing that repository.
Re-run the installer from this same retained checkout to verify installation and refresh Bun dependencies. Claude reports `readFromFolder` for this directory marketplace and fresh sessions read that source folder. Moving to a different checkout requires an explicit marketplace rebind through the CLI and explicit skill-link retargeting; this installer preserves foreign marketplace bindings and occupied skill links.

`python3 plugins/pstack/scripts/port.py <upstream-pstack>` authenticates the upstream file set and compares every vendored byte against the finite port.
`--inventory` regenerates the two reference tables below. `--apply` reproduces only the declared platform substitutions and entrypoint pointers.
The installed table covers every match from `grep -rn "Task\b\|AskQuestion\|cursor" plugins/pstack/skills`, excluding generated dependencies.
Each row points to one source line and the mapping that resolves it. No platform reference is silently dropped.

<!-- reference-inventory -->

## Upstream reference inventory

| Reference | Resolution |
| --- | --- |
| `skills/architect/SKILL.md:31` | M12 |
| `skills/architect/references/design-red-flags.md:41` | M12 |
| `skills/architect/references/design-red-flags.md:43` | M12 |
| `skills/architect/references/runner-prompt.md:3` | M12 |
| `skills/arena/SKILL.md:3` | M12 |
| `skills/arena/SKILL.md:9` | M12 |
| `skills/arena/SKILL.md:27` | M12 |
| `skills/arena/SKILL.md:28` | M1, M3 |
| `skills/arena/SKILL.md:33` | M12 |
| `skills/arena/SKILL.md:41` | M3 |
| `skills/automate-me/SKILL.md:11` | M4 |
| `skills/automate-me/SKILL.md:17` | M2, M8 |
| `skills/automate-me/SKILL.md:29` | M7 |
| `skills/automate-me/SKILL.md:38` | M12 |
| `skills/automate-me/SKILL.md:44` | M2 |
| `skills/automate-me/SKILL.md:57` | M12 |
| `skills/automate-me/SKILL.md:67` | M4 |
| `skills/automate-me/SKILL.md:69` | M8 |
| `skills/automate-me/SKILL.md:102` | M4 |
| `skills/correct/SKILL.md:19` | M12 |
| `skills/create-verification-skill/SKILL.md:9` | M8 |
| `skills/create-verification-skill/SKILL.md:25` | M8 |
| `skills/create-verification-skill/SKILL.md:36` | M8 |
| `skills/figure-it-out/SKILL.md:3` | M12 |
| `skills/figure-it-out/SKILL.md:9` | M12 |
| `skills/how/SKILL.md:11` | M1, M3 |
| `skills/how/SKILL.md:34` | M1 |
| `skills/how/SKILL.md:44` | M1 |
| `skills/interrogate/SKILL.md:36` | M1, M3 |
| `skills/interrogate/SKILL.md:49` | M1 |
| `skills/maintain-verification-skill/SKILL.md:25` | M8 |
| `skills/make-bot-ui/SKILL.md:37` | M9 |
| `skills/no-comments/SKILL.md:19` | M1 |
| `skills/poteto-mode/SKILL.md:8` | M12 |
| `skills/poteto-mode/SKILL.md:20` | M2 |
| `skills/poteto-mode/SKILL.md:26` | M1, M4 |
| `skills/poteto-mode/SKILL.md:28` | M4 |
| `skills/poteto-mode/SKILL.md:30` | M4 |
| `skills/poteto-mode/SKILL.md:32` | M10 |
| `skills/poteto-mode/SKILL.md:35` | M12 |
| `skills/poteto-mode/SKILL.md:36` | M10 |
| `skills/poteto-mode/SKILL.md:66` | M12 |
| `skills/poteto-mode/SKILL.md:95` | M1 |
| `skills/poteto-mode/SKILL.md:121` | M12 |
| `skills/poteto-mode/SKILL.md:123` | M12 |
| `skills/poteto-mode/SKILL.md:139` | M10 |
| `skills/poteto-mode/SKILL.md:140` | M12 |
| `skills/poteto-mode/SKILL.md:144` | M12 |
| `skills/poteto-mode/playbooks/authoring-a-skill.md:5` | M4 |
| `skills/poteto-mode/playbooks/autonomous-run.md:6` | M10 |
| `skills/poteto-mode/playbooks/autonomous-run.md:9` | M2 |
| `skills/poteto-mode/playbooks/autopilot-full.md:6` | M4, M5, M10 |
| `skills/poteto-mode/playbooks/autopilot-full.md:8` | M4 |
| `skills/poteto-mode/playbooks/autopilot-stack.md:5` | M4, M5, M10 |
| `skills/poteto-mode/playbooks/babysit.md:3` | M10 |
| `skills/poteto-mode/playbooks/bug-fix.md:3` | M12 |
| `skills/poteto-mode/playbooks/bug-fix.md:8` | M10 |
| `skills/poteto-mode/playbooks/eval.md:18` | M12 |
| `skills/poteto-mode/playbooks/eval.md:22` | M7 |
| `skills/poteto-mode/playbooks/multi-phase-plan.md:9` | M12 |
| `skills/poteto-mode/playbooks/multi-phase-plan.md:15` | M4 |
| `skills/poteto-mode/playbooks/multi-phase-plan.md:71` | M4, M5 |
| `skills/poteto-mode/playbooks/multi-phase-plan.md:78` | M12 |
| `skills/poteto-mode/playbooks/opening-a-pr.md:5` | M1 |
| `skills/poteto-mode/playbooks/opening-a-pr.md:9` | M4 |
| `skills/poteto-mode/playbooks/orchestrate.md:3` | M12 |
| `skills/poteto-mode/playbooks/orchestrate.md:15` | M1 |
| `skills/poteto-mode/playbooks/orchestrate.md:16` | M1 |
| `skills/poteto-mode/playbooks/orchestrate.md:17` | M4, M5, M7 |
| `skills/poteto-mode/playbooks/orchestrate.md:95` | M5, M7 |
| `skills/poteto-mode/playbooks/orchestrate.md:101` | M5 |
| `skills/poteto-mode/playbooks/session-pickup.md:5` | M5, M7 |
| `skills/poteto-mode/playbooks/shipping.md:7` | M4, M5 |
| `skills/poteto-mode/playbooks/worktree-cleanup.md:5` | M7, M8 |
| `skills/poteto-mode/playbooks/worktree-cleanup.md:10` | M12 |
| `skills/poteto-mode/scripts/bun.lock:6` | M11 |
| `skills/poteto-mode/scripts/check-plan.mjs:90` | M11 |
| `skills/poteto-mode/scripts/check-plan.mjs:92` | M11 |
| `skills/poteto-mode/scripts/check-plan.mjs:94` | M11 |
| `skills/poteto-mode/scripts/package.json:2` | M11 |
| `skills/poteto-mode/scripts/watch-pr/fakes.test-helper.ts:107` | M11 |
| `skills/poteto-mode/scripts/watch-pr/github.test.ts:41` | M11 |
| `skills/poteto-mode/scripts/watch-pr/github.test.ts:42` | M11 |
| `skills/poteto-mode/scripts/watch-pr/github.test.ts:58` | M11 |
| `skills/poteto-mode/scripts/watch-pr/github.test.ts:223` | M11 |
| `skills/poteto-mode/scripts/watch-pr/github.test.ts:227` | M11 |
| `skills/poteto-mode/scripts/watch-pr/github.ts:9` | M11 |
| `skills/poteto-mode/scripts/watch-pr/github.ts:339` | M11 |
| `skills/poteto-mode/scripts/watch-pr/github.ts:342` | M11 |
| `skills/poteto-mode/scripts/watch-pr/github.ts:353` | M11 |
| `skills/poteto-mode/scripts/watch-pr/github.ts:548` | M11 |
| `skills/poteto-mode/scripts/watch-pr/github.ts:554` | M11 |
| `skills/poteto-mode/scripts/watch-pr/github.ts:565` | M11 |
| `skills/poteto-mode/scripts/watch-pr/github.ts:566` | M11 |
| `skills/poteto-mode/scripts/watch-pr/github.ts:567` | M11 |
| `skills/poteto-mode/scripts/watch-pr/github.ts:569` | M11 |
| `skills/poteto-mode/scripts/watch-pr/github.ts:616` | M11 |
| `skills/poteto-mode/scripts/watch-pr/types.ts:380` | M11 |
| `skills/poteto-mode/scripts/worktree-audit.sh:25` | M7, M11 |
| `skills/poteto-mode/scripts/worktree-audit.sh:27` | M7, M11 |
| `skills/principle-build-the-lever/SKILL.md:12` | M12 |
| `skills/principle-explain-the-number/SKILL.md:19` | M12 |
| `skills/principle-laziness-protocol/SKILL.md:15` | M12 |
| `skills/principle-prove-it-works/SKILL.md:3` | M12 |
| `skills/principle-prove-it-works/SKILL.md:9` | M12 |
| `skills/recall/SKILL.md:15` | M7 |
| `skills/recall/SKILL.md:17` | M12 |
| `skills/reflect/SKILL.md:19` | M7 |
| `skills/reflect/SKILL.md:31` | M1, M7 |
| `skills/reflect/SKILL.md:33` | M1, M3 |
| `skills/reflect/SKILL.md:41` | M1, M7 |
| `skills/reflect/SKILL.md:45` | M1 |
| `skills/reflect/SKILL.md:60` | M4 |
| `skills/reflect/references/divergent-reviewer.md:23` | M8 |
| `skills/reflect/references/divergent-reviewer.md:24` | M1 |
| `skills/reflect/references/judgment-reviewer.md:22` | M8 |
| `skills/reflect/references/judgment-reviewer.md:23` | M1 |
| `skills/reflect/references/tooling-reviewer.md:35` | M8 |
| `skills/reflect/references/tooling-reviewer.md:36` | M1 |
| `skills/setup-pstack/SKILL.md:8` | M3 |
| `skills/setup-pstack/SKILL.md:14` | M1 |
| `skills/setup-pstack/SKILL.md:18` | M3 |
| `skills/setup-pstack/SKILL.md:22` | M2 |
| `skills/setup-pstack/SKILL.md:31` | M2 |
| `skills/setup-pstack/SKILL.md:39` | M3 |
| `skills/setup-pstack/SKILL.md:47` | M1 |
| `skills/show-me-your-work/SKILL.md:46` | M12 |
| `skills/show-me-your-work/SKILL.md:57` | M7 |
| `skills/swarm/SKILL.md:25` | M1, M3 |
| `skills/technical-writing/SKILL.md:43` | M12 |
| `skills/technical-writing/SKILL.md:61` | M12 |
| `skills/why/SKILL.md:13` | M1, M3 |
| `skills/why/SKILL.md:23` | M12 |
| `skills/why/SKILL.md:64` | M12 |
| `skills/why/SKILL.md:146` | M12 |

## Installed skills reference inventory

| Reference | Resolution |
| --- | --- |
| `skills/architect/SKILL.md:35` | M12 |
| `skills/architect/references/design-red-flags.md:41` | M12 |
| `skills/architect/references/design-red-flags.md:43` | M12 |
| `skills/architect/references/runner-prompt.md:3` | M12 |
| `skills/arena/SKILL.md:3` | M12 |
| `skills/arena/SKILL.md:13` | M12 |
| `skills/arena/SKILL.md:31` | M12 |
| `skills/arena/SKILL.md:37` | M12 |
| `skills/automate-me/SKILL.md:15` | M4 |
| `skills/automate-me/SKILL.md:33` | M7 |
| `skills/automate-me/SKILL.md:42` | M12 |
| `skills/automate-me/SKILL.md:61` | M12 |
| `skills/automate-me/SKILL.md:71` | M4 |
| `skills/automate-me/SKILL.md:106` | M4 |
| `skills/control-cli/SKILL.md:9` | M4 |
| `skills/control-ui/SKILL.md:9` | M4 |
| `skills/correct/SKILL.md:23` | M12 |
| `skills/create-skill/SKILL.md:9` | M4 |
| `skills/create-skill/SKILL.md:25` | M12 |
| `skills/create-skill/SKILL.md:29` | M12 |
| `skills/create-verification-skill/SKILL.md:13` | M12 |
| `skills/deslop/SKILL.md:9` | M4 |
| `skills/figure-it-out/SKILL.md:3` | M12 |
| `skills/figure-it-out/SKILL.md:13` | M12 |
| `skills/make-bot-ui/SKILL.md:42` | M9 |
| `skills/poteto-mode/SKILL.md:8` | M12 |
| `skills/poteto-mode/SKILL.md:24` | M2 |
| `skills/poteto-mode/SKILL.md:30` | M1, M4 |
| `skills/poteto-mode/SKILL.md:32` | M4 |
| `skills/poteto-mode/SKILL.md:34` | M4 |
| `skills/poteto-mode/SKILL.md:36` | M10 |
| `skills/poteto-mode/SKILL.md:39` | M12 |
| `skills/poteto-mode/SKILL.md:40` | M10 |
| `skills/poteto-mode/SKILL.md:70` | M12 |
| `skills/poteto-mode/SKILL.md:99` | M1 |
| `skills/poteto-mode/SKILL.md:125` | M12 |
| `skills/poteto-mode/SKILL.md:127` | M12 |
| `skills/poteto-mode/SKILL.md:143` | M10 |
| `skills/poteto-mode/SKILL.md:144` | M12 |
| `skills/poteto-mode/SKILL.md:148` | M12 |
| `skills/poteto-mode/playbooks/authoring-a-skill.md:5` | M4 |
| `skills/poteto-mode/playbooks/autonomous-run.md:6` | M10 |
| `skills/poteto-mode/playbooks/autopilot-full.md:6` | M4, M10 |
| `skills/poteto-mode/playbooks/autopilot-full.md:8` | M4 |
| `skills/poteto-mode/playbooks/autopilot-stack.md:5` | M4, M10 |
| `skills/poteto-mode/playbooks/babysit.md:3` | M10 |
| `skills/poteto-mode/playbooks/bug-fix.md:3` | M12 |
| `skills/poteto-mode/playbooks/bug-fix.md:8` | M10 |
| `skills/poteto-mode/playbooks/eval.md:18` | M12 |
| `skills/poteto-mode/playbooks/eval.md:22` | M7 |
| `skills/poteto-mode/playbooks/multi-phase-plan.md:9` | M12 |
| `skills/poteto-mode/playbooks/multi-phase-plan.md:15` | M4 |
| `skills/poteto-mode/playbooks/multi-phase-plan.md:71` | M4, M5 |
| `skills/poteto-mode/playbooks/multi-phase-plan.md:78` | M12 |
| `skills/poteto-mode/playbooks/opening-a-pr.md:9` | M4 |
| `skills/poteto-mode/playbooks/orchestrate.md:3` | M12 |
| `skills/poteto-mode/playbooks/orchestrate.md:17` | M4, M5, M7 |
| `skills/poteto-mode/playbooks/orchestrate.md:95` | M5, M7 |
| `skills/poteto-mode/playbooks/orchestrate.md:101` | M5 |
| `skills/poteto-mode/playbooks/session-pickup.md:5` | M5, M7 |
| `skills/poteto-mode/playbooks/shipping.md:7` | M4 |
| `skills/poteto-mode/playbooks/worktree-cleanup.md:5` | M7, M8 |
| `skills/poteto-mode/playbooks/worktree-cleanup.md:10` | M12 |
| `skills/poteto-mode/scripts/bun.lock:6` | M11 |
| `skills/poteto-mode/scripts/check-plan.mjs:90` | M11 |
| `skills/poteto-mode/scripts/check-plan.mjs:92` | M11 |
| `skills/poteto-mode/scripts/check-plan.mjs:94` | M11 |
| `skills/poteto-mode/scripts/package.json:2` | M11 |
| `skills/poteto-mode/scripts/watch-pr/fakes.test-helper.ts:107` | M11 |
| `skills/poteto-mode/scripts/watch-pr/github.test.ts:41` | M11 |
| `skills/poteto-mode/scripts/watch-pr/github.test.ts:42` | M11 |
| `skills/poteto-mode/scripts/watch-pr/github.test.ts:58` | M11 |
| `skills/poteto-mode/scripts/watch-pr/github.test.ts:223` | M11 |
| `skills/poteto-mode/scripts/watch-pr/github.test.ts:227` | M11 |
| `skills/poteto-mode/scripts/watch-pr/github.ts:9` | M11 |
| `skills/poteto-mode/scripts/watch-pr/github.ts:339` | M11 |
| `skills/poteto-mode/scripts/watch-pr/github.ts:342` | M11 |
| `skills/poteto-mode/scripts/watch-pr/github.ts:353` | M11 |
| `skills/poteto-mode/scripts/watch-pr/github.ts:548` | M11 |
| `skills/poteto-mode/scripts/watch-pr/github.ts:554` | M11 |
| `skills/poteto-mode/scripts/watch-pr/github.ts:565` | M11 |
| `skills/poteto-mode/scripts/watch-pr/github.ts:566` | M11 |
| `skills/poteto-mode/scripts/watch-pr/github.ts:567` | M11 |
| `skills/poteto-mode/scripts/watch-pr/github.ts:569` | M11 |
| `skills/poteto-mode/scripts/watch-pr/github.ts:616` | M11 |
| `skills/poteto-mode/scripts/watch-pr/types.ts:380` | M11 |
| `skills/poteto-mode/scripts/worktree-audit.sh:25` | M7, M11 |
| `skills/poteto-mode/scripts/worktree-audit.sh:27` | M7, M11 |
| `skills/principle-build-the-lever/SKILL.md:12` | M12 |
| `skills/principle-explain-the-number/SKILL.md:19` | M12 |
| `skills/principle-laziness-protocol/SKILL.md:15` | M12 |
| `skills/principle-prove-it-works/SKILL.md:3` | M12 |
| `skills/principle-prove-it-works/SKILL.md:9` | M12 |
| `skills/recall/SKILL.md:19` | M7 |
| `skills/recall/SKILL.md:21` | M12 |
| `skills/reflect/SKILL.md:23` | M7 |
| `skills/reflect/SKILL.md:64` | M4 |
| `skills/reflect/references/divergent-reviewer.md:23` | M12 |
| `skills/reflect/references/judgment-reviewer.md:22` | M12 |
| `skills/reflect/references/tooling-reviewer.md:35` | M12 |
| `skills/setup-pstack/SKILL.md:18` | M1 |
| `skills/show-me-your-work/SKILL.md:50` | M12 |
| `skills/show-me-your-work/SKILL.md:61` | M7 |
| `skills/technical-writing/SKILL.md:47` | M12 |
| `skills/technical-writing/SKILL.md:65` | M12 |
| `skills/why/SKILL.md:27` | M12 |
| `skills/why/SKILL.md:68` | M12 |
| `skills/why/SKILL.md:150` | M12 |
