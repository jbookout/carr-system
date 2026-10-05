#!/usr/bin/env python3
# doctrine: rule-delivery-load-layers
"""rule-boot-gate.py — no tool runs until this context has read the rules.

Joe asked for 100% recall on relevant rules; Jev chose this design (p=1.00).
Every session and every subagent must fetch every page of the gated rule boot
(standing-context `detail: "boot"`: the full text of the always-on rules plus
a one-line index of every active rule, served live from the store) before any
other tool call. hooks/gate-integrity.py arms the gate at SessionStart
(startup, resume, clear, compact) and tells the model which calls to make.

Registered on PreToolUse with matcher ".*" so it sees every tool, which is why
it is deliberately tiny: no network, no model, one small state read. Also
registered on PostToolUse and PostToolUseFailure for the fetch (Bash and the
MCP standing-context), where it reads what the fetch returned: a real page
(recorded; a new digest or page count re-arms the session), a rejection of
detail=boot (the Worker is not deployed yet) or an error (store unreachable).
See lib/rule_boot_gate.py for the state layout, the fetch-call grammar and
why it can never lock a context out.

  · a boot page fetch (CARR MCP standing-context, or a Bash command that runs
    this repo's run.sh `call standing-context '<json>'` by any path, after an
    absolute `cd ... &&`, and through harmless output filters (the emitted
    repair uses literal `python3 -m json.tool`) — the grammar is
    in lib/rule_boot_gate.py)                 -> allow, and record the attempt;
    a page counts as READ only when PostToolUse finds that page's rule_boot
    (matching page, digest and text) in the result, and the page lengths add
    up to the boot's total_chars
  · other standing-context calls, the read-only rule verbs, ToolSearch -> allow
  · every page of the armed digest confirmed in this context          -> allow
  · a fetch in this context failed, or the store was unreachable at arming
    and the context has attempted once          -> allow + RULES UNAVAILABLE
  · the Worker does not serve detail=boot yet   -> allow + NOT DEPLOYED (once)
  · the hold could not be recorded (state unwritable) -> allow + notice
  · held DENY_CAP times without progress        -> allow + RULES UNREAD
  · otherwise                                   -> DENY, naming the exact calls

FAILS OPEN on any internal error, like every other hook here: a gate that
crashes must never be able to stop work. Logged to out/hook-guard.log.

Fixtures: ops/rule-boot-gate-selftest.py
"""
import json
import os
import sys
from datetime import datetime, timezone

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)
LOG = os.environ.get("CARR_HOOK_GUARD_LOG") or os.path.join(REPO, "out", "hook-guard.log")


def log(msg):
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        ts = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(f"{ts} rule-boot-gate {msg.rstrip()}\n")
    except OSError:
        pass


def emit(decision, text, event="PreToolUse"):
    out = {"hookEventName": event}
    if decision == "deny":
        out.update(permissionDecision="deny", permissionDecisionReason=text)
    elif text:
        out["additionalContext"] = text
    else:
        return
    print(json.dumps({"hookSpecificOutput": out}))


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception as exc:
        log(f"ALLOW(parse-error) {exc}")
        return 0
    if not isinstance(payload, dict):
        return 0
    event = payload.get("hook_event_name") or "PreToolUse"
    try:
        if event in ("PostToolUse", "PostToolUseFailure"):
            from lib.rule_boot_gate import observe
            emit("allow", observe(payload), event)
            return 0
        from lib.rule_boot_gate import verdict
        decision, text = verdict(payload)
        if decision == "deny":
            log(f"DENY tool={payload.get('tool_name')} agent={payload.get('agent_id') or 'main'}")
        emit(decision, text)
    except Exception as exc:
        log(f"ALLOW(internal-error) {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
