#!/usr/bin/env python3
"""executor-tier-gate.py — make the executor an explicit choice, not a default.

WHY THIS EXISTS. Three active rules already say the executor must be named and
must be the cheapest one still qualified: the subagent cost gate (2dbb0ad8),
the executor-choice rule whose audit signal is a stated "executor: <tier>" line
(185013c6), and the rule reserving the frontier model for its best use cases
(fb110a39). All three are PROSE. Nothing enforced them, and on 2026-08-13 a
live test showed exactly what that costs: a `general-purpose` subagent spawned
with no `model` parameter reported back "You are powered by the model named
Opus 5 (1M context)". It inherited the parent tier silently, and nothing in the
transcript said so.

That is the whole leak. The fifteen custom CARR agents pin a model in their own
frontmatter, so they are safe by construction. The GENERIC types — the ones a
session reaches for when it wants a cheap mechanical sweep, `general-purpose`,
`Explore`, `Plan` — define no model at all, so per the Agent tool's own contract
they "inherit from the parent". The parent here is pinned to Opus. A session
intending a throwaway grep and forgetting one parameter buys the whole sweep at
top tier, and the report still reads clean.

WHAT THIS IS NOT. It is not the Claude Code 2.1.223 "restricted subagent model"
warning. That warning was assessed on 2026-08-13 and CANNOT fire here: nothing
on this machine restricts any model (no managed settings, no MDM profile, no
restricting key or env var), and none of CARR's model-request sites fall into
the four categories it covers. It was checked before this was built, so nobody
re-proposes it later as the fix for this.

WHAT IT CHECKS. One question, at the one detectable moment: an Agent spawn is
about to happen and NOTHING has named the tier it will run on.

  · `model` passed on the call            -> allow. The executor is named.
  · `subagent_type: fork`                 -> allow. The Agent tool documents
                                             model as ignored for forks; they
                                             always inherit the parent, so
                                             denying would demand the
                                             impossible.
  · a definition file pinning `model:`    -> allow. The job description names
                                             its own tier, which is the point
                                             of pinning it there.
  · none of the above                     -> DENY, naming what it would have
                                             cost and how to fix it.

IT DENIES RATHER THAN WARNS, which is the opposite of its neighbour
rule-shape-gate.py, and the difference is deliberate. That gate warns because
blocking would make `teach` refuse a partner's own words, which it must never
do. Here the cost of a false stop is one round trip and one extra parameter,
while the cost of a warning is that it gets clicked past — and prose that gets
clicked past is precisely the failure being fixed. A gate that only warns would
be the same aspiration in a new costume.

FAILS OPEN on any error, like every other hook here. A gate that crashes must
never be able to stop work. Logged to out/hook-guard.log.
"""

import json
import os
import re
import sys
from datetime import datetime, timezone

LOG = os.path.expanduser("~/carr-system/out/hook-guard.log")

# Where a subagent definition may live. Project scope first: that is where the
# fifteen CARR agents are, and a project definition wins over a user-level one
# of the same name.
AGENT_DIRS = [
    os.path.expanduser("~/My Drive/.claude/agents"),
    os.path.expanduser("~/My Drive/CARR AI/.claude/agents"),
    os.path.expanduser("~/.claude/agents"),
]

# Forks are exempt by the tool's own contract, not by preference.
ALWAYS_INHERITS = {"fork"}

MODEL_FRONTMATTER = re.compile(r"^model\s*:\s*\S+", re.M)


def log(msg):
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        ts = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        with open(LOG, "a") as fh:
            fh.write(f"{ts} executor-tier-gate {msg.rstrip()}\n")
    except Exception:
        pass


def definition_pins_model(subagent_type):
    """True when this subagent type has a definition file naming its own model.

    Only the frontmatter block is inspected — the first fenced `---` section —
    so the word "model:" appearing in an agent's prose body cannot be mistaken
    for a pin. Reads at most a few KB per call.
    """
    if not subagent_type:
        return False
    safe = os.path.basename(subagent_type)
    if safe != subagent_type:
        return False
    for d in AGENT_DIRS:
        path = os.path.join(d, f"{safe}.md")
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                head = fh.read(4096)
        except Exception:
            continue
        if not head.startswith("---"):
            continue
        end = head.find("\n---", 3)
        frontmatter = head[3:end] if end != -1 else head[3:]
        if MODEL_FRONTMATTER.search(frontmatter):
            return True
    return False


def deny(reason):
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }))
    sys.exit(0)


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception as exc:
        log(f"ALLOW(parse-error) {exc}")
        sys.exit(0)

    try:
        tool = payload.get("tool_name") or payload.get("toolName") or ""
        if tool not in ("Agent", "Task"):
            sys.exit(0)

        ti = payload.get("tool_input") or payload.get("toolInput") or {}
        if not isinstance(ti, dict):
            sys.exit(0)

        model = ti.get("model")
        subagent_type = ti.get("subagent_type") or ti.get("subagentType") or ""
        desc = ti.get("description") or ""

        # The executor is named on the call. Nothing to do.
        if isinstance(model, str) and model.strip():
            sys.exit(0)

        if subagent_type in ALWAYS_INHERITS:
            sys.exit(0)

        if definition_pins_model(subagent_type):
            sys.exit(0)

        log(f"DENY subagent_type={subagent_type or '(none)'} desc={desc[:80]}")
        deny(
            "EXECUTOR NOT NAMED. This Agent call passes no `model`, and "
            f"`{subagent_type or 'the default type'}` has no model pinned in a definition file, "
            "so it will INHERIT THE PARENT TIER. On this machine the parent is pinned to Opus, "
            "which was confirmed live on 2026-08-13: a general-purpose subagent spawned without "
            "a model reported back that it was running on Opus 5. A mechanical sweep dispatched "
            "this way costs top-tier rates and nothing in the transcript says so.\n\n"
            "FIX: pass `model` explicitly on this call. Pick the CHEAPEST tier still qualified "
            "to do the job correctly:\n"
            "  haiku  — lookups, retrieval, mechanical extraction, single-file reads\n"
            "  sonnet — sweeps, code reading, research, most delegated implementation\n"
            "  opus   — judgment, verification of load-bearing findings, client-facing work\n"
            "  fable  — reserved for deep design and doctrine work; not a default\n\n"
            "If you genuinely want the parent tier, say so by passing it explicitly. The point "
            "is that the tier is a decision someone made, not one nobody noticed. Custom CARR "
            "agents that pin a model in their own frontmatter are exempt and need no parameter."
        )
    except Exception as exc:
        log(f"ALLOW(internal-error) {exc}")
        sys.exit(0)


if __name__ == "__main__":
    main()
