#!/usr/bin/env python3
"""Regression fixtures for the narrow terminal completion-evidence gate."""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location(
    "completion_evidence", os.path.join(REPO, "hooks", "completion-evidence-gate.py")
)
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def user(text):
    return {"type": "user", "message": {"role": "user", "content": text}}


def assistant(text):
    return {"type": "assistant", "message": {"role": "assistant", "content": text}}


def tool(name, value=None):
    return {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": name, "input": value or {}}
    ]}}


def codex_user(value):
    return {"type": "response_item", "payload": {
        "type": "message", "role": "user", "content": [
            {"type": "input_text", "text": value},
        ],
    }}


def codex_wrapper(value):
    return {"type": "response_item", "payload": {
        "type": "message", "role": "user", "content": [
            {"type": "input_text", "text": "The following is the Codex agent history added since your last approval"},
            {"type": "input_text", "text": value},
        ],
    }}


def codex_tool(name, value):
    return {"type": "response_item", "payload": {
        "type": "custom_tool_call", "name": name, "input": value,
    }}


def codex_assistant(value):
    return {"type": "response_item", "payload": {
        "type": "message", "role": "assistant", "content": [
            {"type": "output_text", "text": value},
        ],
    }}


CASES = [
    ("ordinary answer", [user("status"), assistant("The board is current.")], False),
    ("one file patch is friction-free", [user("fix it"), tool("apply_patch", {"command": "*** Update File: a.py\n+x"}), assistant("Done.")], False),
    ("unsupported completion after multi-file patch", [user("fix it"), tool("apply_patch", {"command": "*** Update File: a.py\n+x\n*** Update File: b.py\n+y"}), assistant("Done.")], True),
    ("fresh read permits completion", [user("fix it"), tool("apply_patch", {"command": "*** Update File: a.py\n+x\n*** Update File: b.py\n+y"}), tool("Read"), assistant("Done and verified.")], False),
    ("explicit disclosure permits close", [user("fix it"), tool("apply_patch", {"command": "*** Update File: a.py\n+x\n*** Update File: b.py\n+y"}), assistant("Done, but unverified because the server is unavailable.")], False),
    ("no tests failed is not a disclosure", [user("fix it"), tool("apply_patch", {"command": "*** Update File: a.py\n+x\n*** Update File: b.py\n+y"}), assistant("Done; no tests failed.")], True),
    ("skipped none is not a disclosure", [user("fix it"), tool("apply_patch", {"command": "*** Update File: a.py\n+x\n*** Update File: b.py\n+y"}), assistant("Done; skipped none.")], True),
    ("verification failed needs a reason", [user("fix it"), tool("apply_patch", {"command": "*** Update File: a.py\n+x\n*** Update File: b.py\n+y"}), assistant("Done, but verification failed because the test server is unavailable.")], False),
    ("record write requires evidence", [user("reconcile"), tool("mcp__carr__update-deal"), assistant("Done.")], True),
    ("generic call-verb write requires evidence", [user("reconcile"), tool("mcp__carr__call-verb", {"verb": "update-deal", "args": {}}), assistant("Done.")], True),
    ("recipient required for delivery", [user("update it"), tool("mcp__carr__update-deal"), assistant("Delivered after the update.")], True),
    ("named recipient permits delivery", [user("update it"), tool("mcp__carr__update-deal"), tool("Read"), assistant("Delivered to Dell after a fresh read.")], False),
    ("deploy gets checked", [user("release"), tool("Bash", {"command": "npx wrangler deploy"}), assistant("Deployed.")], True),
    ("unrelated historical tool does not matter", [user("status"), tool("Read"), assistant("Done with the explanation.")], False),
    ("Codex nested CARR write requires evidence", [
        codex_user("reconcile Musicologie"),
        codex_tool("exec", "const row = await tools.mcp__carr__update_deal({ id: 'd1', stage: 'LOI' });"),
        codex_assistant("Completed."),
    ], True),
    ("Codex nested generic call verb requires evidence", [
        codex_user("reconcile Musicologie"),
        codex_tool("exec", "await tools.mcp__carr__call_verb({ verb: 'update-deal', args: { id: 'd1' } });"),
        codex_assistant("Done."),
    ], True),
    ("Codex nested CARR read is fresh evidence", [
        codex_user("reconcile Musicologie"),
        codex_tool("exec", "await tools.mcp__carr__update_deal({ id: 'd1' });"),
        codex_tool("exec", "const fresh = await tools.mcp__carr__get_deal({ id: 'd1' });"),
        codex_assistant("Done and verified."),
    ], False),
    ("Codex history wrapper cannot reset the mutation window", [
        codex_user("reconcile Musicologie"),
        codex_tool("exec", "await tools.mcp__carr__update_deal({ id: 'd1' });"),
        codex_wrapper("Earlier work and environment context."),
        codex_assistant("Done."),
    ], True),
    ("Codex create national market deal requires evidence", [
        codex_user("add market"),
        codex_tool("exec", "await tools.mcp__carr__create_national_market_deal({ account: 'Musicologie' });"),
        codex_assistant("Done."),
    ], True),
    ("Codex patch deal field requires evidence", [
        codex_user("update market"),
        codex_tool("exec", "await tools.mcp__carr__patch_deal_field({ id: 'd1', field: 'phase' });"),
        codex_assistant("Done."),
    ], True),
    ("Codex doctrine write requires evidence", [
        codex_user("write doctrine"),
        codex_tool("exec", "await tools.mcp__carr__write_doctrine_section({ doctrine_id: 'x' });"),
        codex_assistant("Completed."),
    ], True),
    ("Codex workflow acceptance requires evidence", [
        codex_user("accept the canary"),
        codex_tool("exec", "await tools.mcp__carr__accept_workflow({ workflow_key: 'fixture' });"),
        codex_assistant("Completed."),
    ], True),
    ("Codex legacy schedule disable requires evidence", [
        codex_user("retire the old schedule"),
        codex_tool("exec", "await tools.mcp__carr__disable_legacy_schedule({ workflow_key: 'fixture' });"),
        codex_assistant("Completed."),
    ], True),
    ("Codex sourced problem capture requires evidence", [
        codex_user("capture this operating problem"),
        codex_tool("exec", "await tools.mcp__carr__report_problem({ idempotency_key: 'fixture' });"),
        codex_assistant("Completed."),
    ], True),
    ("CARR read action permits completion", [
        codex_user("reconcile Musicologie"),
        codex_tool("exec", "await tools.mcp__carr__patch_deal_field({ id: 'd1' });"),
        codex_tool("exec", "await tools.mcp__carr__review_queue({});"),
        codex_assistant("Done and verified."),
    ], False),
]


def registry_prefix_coverage():
    """Keep the family classifier honest against the local live registry when present."""
    registry = os.path.join(REPO, "mcp-server", "src", "tools.js")
    if not os.path.exists(registry):
        print("SKIP  live registry unavailable")
        return True
    script = (
        'import { TOOLS } from "./src/tools.js"; '
        'console.log(JSON.stringify(Object.keys(TOOLS).filter((n) => TOOLS[n].write).sort()))'
    )
    result = subprocess.run(["node", "--input-type=module", "-e", script],
                            cwd=os.path.join(REPO, "mcp-server"), text=True,
                            capture_output=True, timeout=30)
    if result.returncode:
        print("SKIP  live registry could not load")
        return True
    writes = json.loads(result.stdout)
    missing = [name for name in writes if not mod.is_write_action(name)]
    reads = ["review-queue", "get-deal", "list-verbs", "catch-me-up", "deal-board", "find"]
    false_writes = [name for name in reads if mod.is_write_action(name)]
    ok = not missing and not false_writes
    print(f"{'PASS' if ok else 'FAIL'}  live registry write coverage: "
          f"{len(writes) - len(missing)}/{len(writes)} writes classified"
          + (f"; missing={','.join(missing)}" if missing else "")
          + (f"; read false positives={','.join(false_writes)}" if false_writes else ""))
    return ok


def authority_family_coverage():
    """Human-only acceptance/retirement and future proposal/approval writes stay gated."""
    actions = [
        "accept-workflow", "disable-legacy-schedule", "approve-work-request",
        "propose-cognition-job", "decide-guidance-import-batch",
        "deactivate-guidance-registry",
    ]
    missing = [action for action in actions if not mod.is_write_action(action)]
    ok = not missing
    print(f"{'PASS' if ok else 'FAIL'}  authority workflow family coverage"
          + (f"; missing={','.join(missing)}" if missing else ""))
    return ok


def real_hook_case(kind, non_carr=False):
    if kind == "codex":
        records = [
            codex_user("reconcile"),
            codex_tool("exec", "await tools.mcp__carr__update_deal({ id: 'd1' });"),
            codex_assistant("Done."),
        ]
    else:
        records = [user("reconcile"), tool("mcp__carr__update-deal"), assistant("Done.")]
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as fh:
        for row in records:
            fh.write(json.dumps(row) + "\n")
        path = fh.name
    try:
        payload = {"transcript_path": path, "session_id": "selftest", "stop_hook_active": False}
        if kind == "codex":
            payload = {"transcriptPath": path, "sessionId": "selftest", "stop_hook_active": False,
                       "hook_event_name": "Stop"}
        if non_carr:
            payload["cwd"] = "/private/tmp/non-carr-app"
        hook = os.path.join(REPO, "hooks", "completion-evidence-gate.py")
        result = subprocess.run([os.sys.executable, hook], input=json.dumps(payload), text=True,
                                capture_output=True, timeout=20)
        body = json.loads(result.stdout or "{}")
        return body.get("decision") == "block"
    finally:
        os.unlink(path)


def main():
    outcomes = []
    for name, recs, expected in CASES:
        got, reason = mod.evaluate(recs)
        ok = got == expected
        outcomes.append(ok)
        print(f"{'PASS' if ok else 'FAIL'}  {name}: {got} ({reason})")
    for kind in ("claude", "codex"):
        got = real_hook_case(kind)
        outcomes.append(got)
        print(f"{'PASS' if got else 'FAIL'}  {kind} structured Stop block")
    non_carr = not real_hook_case("claude", non_carr=True)
    outcomes.append(non_carr)
    print(f"{'PASS' if non_carr else 'FAIL'}  non-CARR cwd is out of scope")
    outcomes.append(registry_prefix_coverage())
    outcomes.append(authority_family_coverage())
    print(f"completion-evidence-gate-selftest: {sum(outcomes)}/{len(outcomes)} passed")
    return 0 if all(outcomes) else 1


if __name__ == "__main__":
    raise SystemExit(main())
