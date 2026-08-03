# Hooks: the block to paste, and why each line is there

*Built 2026-08-02 against idea-bank #32. Claude cannot edit `settings.json` (classifier-blocked, same gate as `migrate.py --apply`), so this is the write-it-and-hand-it-over pattern: the scripts are on disk and tested, the config is yours to paste.*

## Where it goes

`~/.claude/settings.json` — the GLOBAL file, not the project one.

Reason: the scheduled tasks that most need the guard do not all run with the CARR vault as their working directory, and a project-scoped hook would miss them. The lint gate is safe globally because it checks the vault path itself and exits silently on anything else.

That file already has `skipWorkflowUsageWarning`, `agentPushNotifEnabled`, `inputNeededNotifEnabled` and `permissions`. **Merge the `"hooks"` key in beside them; do not replace the file.**

**REVISION 2026-08-03:** this block had drifted from what is actually installed — `rule-shape-gate.py` and `ledger-sweep.py` were added to `settings.json` without being written down here. The block below is the full current set INCLUDING the new `record-home-gate.py`. The only line you need to add by hand is the `Write|Edit|MultiEdit` entry under `PreToolUse`; everything else already matches your file.

```json
"hooks": {
  "PreToolUse": [
    {
      "matcher": "Bash",
      "hooks": [
        {
          "type": "command",
          "command": "/usr/bin/env python3 /Users/booko/carr-system/hooks/guard-unattended.py",
          "timeout": 15
        }
      ]
    },
    {
      "matcher": "mcp__.*__teach",
      "hooks": [
        {
          "type": "command",
          "command": "/usr/bin/env python3 /Users/booko/carr-system/hooks/rule-shape-gate.py",
          "timeout": 10
        }
      ]
    },
    {
      "matcher": "Write|Edit|MultiEdit",
      "hooks": [
        {
          "type": "command",
          "command": "/usr/bin/env python3 /Users/booko/carr-system/hooks/record-home-gate.py",
          "timeout": 10
        }
      ]
    }
  ],
  "PostToolUse": [
    {
      "matcher": "Write|Edit|MultiEdit",
      "hooks": [
        {
          "type": "command",
          "command": "/usr/bin/env python3 /Users/booko/carr-system/hooks/lint-gate.py",
          "timeout": 40
        }
      ]
    }
  ],
  "Stop": [
    {
      "hooks": [
        {
          "type": "command",
          "command": "/usr/bin/env python3 /Users/booko/carr-system/hooks/ledger-sweep.py",
          "timeout": 15
        }
      ]
    }
  ]
}
```

## What each hook does

**`guard-unattended.py`** blocks five classes on every Bash call: recursive or forced deletes outside the scratch zones, git history rewrites and force pushes, private key reads, network sends to hosts outside the known set, and destructive SQL. It exits 2, which blocks on every build and hands the reason back as feedback.

**`record-home-gate.py`** (NEW, 2026-08-03) **denies** writing records into markdown — Joe's words: *"you cant write a .md file you have to write to the database... it is one of the worst mistakes that can be made in this database system. writing a .md file creates a big issue. its undetectable to the system."* Four path-structural rules: any write to a **generated render**; **creating a new `.md`** under `00_Context/`, the vault root or `out/`; **anything under `00_Context/handoffs/`**; and content carrying a **rule uuid next to a rule verb**. It deliberately does NOT sniff prose, because a content heuristic on writing this varied would misfire and burn the gate's credibility in a day. Ordinary narrative editing — doctrine under `DNA/`, `CLAUDE.md`, `INDEX.md`, skills, agents — stays unblocked, which is the half that keeps the gate alive.

**`rule-shape-gate.py`** checks the shape of a `teach` call before it lands.

**`lint-gate.py`** runs `run.sh lint` after a write to a plausibly client-facing vault file and injects the result back into the session. It never blocks. It skips generated renders, repo code and scratch files.

**`ledger-sweep.py`** is a Stop hook: it reads the last human turn against the five ledger triggers and, when one matched and no `log-decision` or `teach` followed, names the trigger and quotes the line.

## Why the record-home gate had to be a hook

Shared rule *"findings and record updates go into the DATABASE, never into a markdown report"* was the last major rule in this system enforced only by a session remembering it. It failed **twice in one sitting** on 2026-08-03, in a session that had loaded the rule and recited it back at session start.

That is the same failure `ledger-sweep.py` was built for a day earlier, and its docstring already carries the finding: *"The only control that worked unassisted all night was a hook."* Doctrine, taught rules and recitation are all compliance-dependent. A hook is not.

Three causes this gate does **not** fix, stated so nobody mistakes it for a complete answer: the rule's own carve-out (*"narrative and doctrine files stay markdown"*) is a trap, because handoffs and status writeups look like narrative and are made of records; both `/handoff` skills actively instruct sessions to produce a markdown packet and file it into CARR; and `Write` is frictionless while a verb needs a schema load, a fresh UUID and a `base_version` read. The gate addresses detection only.

## One deviation from idea #32, and it is deliberate

#32 specified deny rules **"during scheduled sessions."** There is no reliable way for a hook to tell a scheduled run from an interactive one. The documented fields (`permission_mode`, `CLAUDE_CODE_REMOTE`, `CLAUDE_CODE_BRIDGE_SESSION_ID`) all mark something else, and the docs are explicit that no scheduled-session marker exists.

So the gate applies **always**. That is the honest version, and it is also the better one: none of the five blocked classes is something an interactive session should be doing unannounced either. The alternative was inventing a detection signal and trusting it, which is how you end up with a guard that silently does not guard.

## The nightly chain is protected by name

`nightly-record-layer` runs one byte-identical command carrying a persisted permission approval. Reword it and the 2am run hits a permission prompt with nobody awake, which is exactly how the first scheduled run produced nothing.

It matches no deny pattern. It is **also** allowlisted verbatim in `ALLOW_EXACT`, so a pattern added later cannot catch it by accident. Step 1 of that chain (`cadence_engine.py --apply`) writes to the database on purpose, which is why this gate draws its line at destructive-or-exfiltrating rather than at write-versus-read.

## Failure behaviour

Both hooks **fail open**. Any parse error, internal error or timeout allows the action. On a single-operator machine, a gate that wedges the 2am chain costs more than the marginal safety of failing closed. Every allow-on-error is written to `~/carr-system/out/hook-guard.log`, so a silently degraded gate is still discoverable.

Deny events are logged to the same file. Check it after the first week to see whether the patterns are too tight.

## Kill switch

If a hook misfires, add this to the same settings file and restart the session:

```json
"disableAllHooks": true
```

Faster still: `chmod -x` the script. A non-executable hook errors, and both scripts fail open.

## Verify it took

After pasting and restarting, these should print the exit codes shown.

```bash
printf '%s' '{"tool_name":"Bash","tool_input":{"command":"rm -rf ~/carr-system/migrations"}}' | python3 ~/carr-system/hooks/guard-unattended.py; echo "exit=$? (want 2)"
```

```bash
printf '%s' '{"tool_name":"Bash","tool_input":{"command":"cd ~/carr-system && ./bin/nightly.sh >/dev/null 2>&1; echo \"direct script exit=$?\""}}' | python3 ~/carr-system/hooks/guard-unattended.py; echo "exit=$? (want 0)"
```

## Tested before handover

Thirteen guard cases, all correct: the nightly chain, `cadence_engine.py --apply`, a scratch-zone delete, a known-host curl and an ordinary grep all ALLOW; repo delete, vault delete, force push, hard reset, private key read, unknown-host POST, `DROP TABLE` and unqualified `DELETE` all DENY.

Lint gate tested end to end: it correctly flagged the 13 hard-ban hits in `DNA/Clients/intake/README.md`, and correctly skipped a generated render and a repo script.

**Not tested:** the hooks firing inside a live session, because that needs the config pasted first. The scripts are proven against the documented payload shape; the wiring is unproven until you restart.
