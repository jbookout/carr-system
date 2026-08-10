#!/usr/bin/env python3
"""Executable regressions for hooks/delegation-gate.py."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(REPO, "hooks", "delegation-gate.py")


def load_hook():
    spec = importlib.util.spec_from_file_location("delegation_gate", HOOK)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


GATE = load_hook()


def user(text):
    return {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": text}]}}


def assistant_text(text):
    return {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}


def tool(name):
    return {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "tool_use", "name": name, "input": {}}]}}


def codex_user(text, include_generated_context=False):
    content = [{"type": "input_text", "text": text}]
    if include_generated_context:
        content.append({
            "type": "input_text",
            "text": "<environment_context> delegation latch documentation </environment_context>",
        })
    return {"type": "response_item", "payload": {
        "type": "message", "role": "user", "content": content,
    }}


def codex_history_wrapper(quoted):
    return {"type": "response_item", "payload": {
        "type": "message", "role": "user", "content": [
            {"type": "input_text", "text": "The following is the Codex agent history added since your last approval"},
            {"type": "input_text", "text": quoted},
        ],
    }}


def codex_tool(name):
    return {"type": "response_item", "payload": {
        "type": "custom_tool_call", "name": name,
    }}


def run_case(name, recs, want, state, session, cwd=REPO, subagent=False):
    with tempfile.TemporaryDirectory(prefix="delegation-gate-") as td:
        base = os.path.join(td, "subagents") if subagent else td
        os.makedirs(base, exist_ok=True)
        transcript = os.path.join(base, "session.jsonl")
        with open(transcript, "w") as fh:
            for rec in recs:
                fh.write(json.dumps(rec) + "\n")
        payload = {
            "session_id": session,
            "cwd": cwd,
            "transcript_path": transcript,
            "tool_name": "Bash",
            "tool_input": {"command": "true"},
        }
        env = dict(os.environ, DELEGATION_GATE_STATE=state)
        got = subprocess.run(
            [sys.executable, HOOK],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            env=env,
        )
        ok = got.returncode == want
        print(f"{'PASS' if ok else 'FAIL'}  {name}: got {got.returncode}, want {want}")
        if not ok and got.stderr:
            print(got.stderr.strip())
        return ok


def run_codex_case(name, recs, should_block, reason_prefix=None, agent_type=None):
    """Exercise the actual Codex PreToolUse input/output contract."""
    with tempfile.TemporaryDirectory(prefix="delegation-gate-codex-") as td:
        transcript = os.path.join(td, "session.jsonl")
        with open(transcript, "w") as fh:
            for rec in recs:
                fh.write(json.dumps(rec) + "\n")
        payload = {
            "hook_event_name": "PreToolUse",
            "cwd": REPO,
            "model": "gpt-5.6-terra",
            "permission_mode": "default",
            "session_id": "codex-fixture",
            "tool_name": "functions.exec",
            "tool_input": {"cmd": "true"},
            "tool_use_id": "call-1",
            "transcript_path": transcript,
            "turn_id": "turn-1",
        }
        if agent_type:
            payload["agent_type"] = agent_type
        got = subprocess.run(
            [sys.executable, HOOK], input=json.dumps(payload), text=True,
            capture_output=True,
            env=dict(os.environ, DELEGATION_GATE_STATE=os.path.join(td, "state.json")),
        )
        try:
            response = json.loads(got.stdout) if got.stdout else None
        except json.JSONDecodeError:
            response = None
        blocked = isinstance(response, dict) and response.get("decision") == "block"
        reason_ok = not reason_prefix or (
            isinstance(response, dict)
            and isinstance(response.get("reason"), str)
            and response["reason"].startswith(reason_prefix)
        )
        ok = got.returncode == 0 and blocked == should_block and reason_ok
        print(f"{'PASS' if ok else 'FAIL'}  {name}: got {got.returncode} {response!r}")
        return ok


def task_id(session, index, instruction):
    return GATE.task_id_for(session, index, instruction)


def main():
    with tempfile.TemporaryDirectory(prefix="delegation-gate-state-") as state_dir:
        state = os.path.join(state_dir, "state.json")
        cases = [
            ("first mechanical lookup is allowed", [user("audit this")], 0, "ordinary-first", REPO, False),
            ("second call without executor blocks", [user("audit this"), tool("Bash")], 2, "ordinary-second", REPO, False),
            ("explicit delegation is sticky and blocks inline", [user("use the cheapest model that can do this correctly"), tool("Bash")], 2, "sticky-basic", REPO, False),
            ("Agent spawn satisfies the ordinary tripwire", [user("audit this"), tool("Bash"), tool("Agent")], 0, "ordinary-agent", REPO, False),
            ("Terra peer rationale satisfies ordinary tripwire", [user("audit this"), assistant_text("executor: Terra peer — because this is final judgment verification"), tool("Bash")], 0, "ordinary-terra", REPO, False),
            ("inline executor cannot override explicit delegation", [user("use the cheapest qualified model"), assistant_text("executor: Terra peer — because I prefer it"), tool("Bash")], 2, "sticky-inline", REPO, False),
            ("delegation survives a later phase message", [user("delegate this to the cheapest qualified model"), tool("Agent"), user("I'm in Salesforce now"), tool("Bash")], 2, "sticky-phase", REPO, False),
            ("same-message revocation wins over delegation", [user("delegate this, but do not delegate it; keep this inline"), assistant_text("executor: Terra peer — because this is a final verification"), tool("Bash")], 0, "revoke-wins", REPO, False),
            ("later revocation prevents recreation after state loss", [user("delegate this to the cheapest qualified model"), user("do not delegate; keep this inline"), user("continue with the final verification"), assistant_text("executor: Terra peer — because this is a final verification"), tool("Bash")], 0, "fresh-revocation", REPO, False),
            ("generic completion cannot release a task", [user("delegate this to the cheapest qualified model"), tool("Agent"), assistant_text("delegation complete: Salesforce extraction"), user("check one more thing"), assistant_text("executor: Terra peer — because this is one judgment verification"), tool("Bash")], 2, "generic-complete", REPO, False),
            ("full transcript keeps delegation beyond 500 records", [user("delegate this to the cheapest qualified model")] + [assistant_text("filler") for _ in range(501)] + [user("new data source is ready"), tool("Bash")], 2, "long-transcript", REPO, False),
            ("subagent transcripts are exempt", [user("do the assigned sweep"), tool("Bash")], 0, "subagent", REPO, True),
            ("non-CARR sessions are exempt", [user("audit this"), tool("Bash")], 0, "non-carr", "/private/tmp", False),
        ]
        oks = [run_case(name, recs, want, state, session, cwd, subagent)
               for name, recs, want, session, cwd, subagent in cases]

        origin_instruction = "delegate this to the cheapest qualified model"
        origin = "resume-origin"
        resume_id = task_id(origin, 0, origin_instruction)
        oks.append(run_case(
            "resume origin establishes task", [user(origin_instruction), tool("Bash")],
            2, state, origin,
        ))
        complete_session = "exact-complete"
        complete_instruction = "delegate this to the cheapest qualified model"
        complete_id = task_id(complete_session, 0, complete_instruction)
        oks.append(run_case(
            "exact completion task establishes", [user(complete_instruction), tool("Bash")],
            2, state, complete_session,
        ))
        oks.append(run_case(
            "exact task-id completion releases the latch",
            [assistant_text("delegation complete: " + complete_id), user("check one more thing"), assistant_text("executor: Terra peer — because this is one judgment verification"), tool("Bash")],
            0, state, complete_session,
        ))
        oks.append(run_case(
            "only exact resume binds a continuation",
            [user("delegation resume: " + resume_id), assistant_text("executor: Terra peer — because I prefer it"), tool("Bash")],
            2, state, "resume-continuation",
        ))
        oks.append(run_case(
            "malformed resume does not bind an unrelated session",
            [user("delegation resume: " + resume_id + " please"), assistant_text("executor: Terra peer — because this is final verification"), tool("Bash")],
            0, state, "resume-malformed",
        ))
        oks.append(run_case(
            "rebinding removes the origin session's latch",
            [user("new phase"), assistant_text("executor: Terra peer — because this is final verification"), tool("Bash")],
            0, state, origin,
        ))

        task_a, task_b = "revoke-a", "revoke-b"
        instr = "delegate this to the cheapest qualified model"
        oks.append(run_case("task A establishes", [user(instr), tool("Bash")], 2, state, task_a))
        oks.append(run_case("task B establishes", [user(instr), tool("Bash")], 2, state, task_b))
        oks.append(run_case(
            "revocation releases only its current bound task",
            [user("do not delegate; keep this inline"), assistant_text("executor: Terra peer — because this is final verification"), tool("Bash")],
            0, state, task_a,
        ))
        oks.append(run_case(
            "other session's bound task remains sticky",
            [user("new phase"), assistant_text("executor: Terra peer — because this is final verification"), tool("Bash")],
            2, state, task_b,
        ))

        oks.append(run_codex_case(
            "Codex explicit delegation returns a structured block",
            [codex_user("delegate this to the cheapest qualified model", True), codex_tool("exec")],
            True,
            "DELEGATION GATE",
        ))
        oks.append(run_codex_case(
            "Codex injected environment text cannot create a delegation latch",
            [codex_user("audit this", True), codex_tool("exec")],
            True,
            "DELEGATION TRIPWIRE",
        ))
        oks.append(run_codex_case(
            "Codex approval wrapper ignores quoted historical delegation",
            [codex_user("audit this"), codex_history_wrapper("delegate this to a cheaper model"), codex_tool("exec")],
            True,
            "DELEGATION TRIPWIRE",
        ))
        oks.append(run_codex_case(
            "later synthetic context does not erase a real delegation",
            [{"type": "response_item", "payload": {"type": "message", "role": "user", "content": [
                {"type": "input_text", "text": "delegate this to the cheapest qualified model"},
                {"type": "input_text", "text": "<environment_context> generated rail </environment_context>"},
            ]}}, codex_tool("exec")],
            True,
            "DELEGATION GATE",
        ))
        oks.append(run_codex_case(
            "Codex subagent payload is exempt from the main-seat tripwire",
            [codex_user("delegate this to the cheapest qualified model"), codex_tool("exec")],
            False,
            agent_type="subagent",
        ))
        # CARR record tools are classified by their own namespace when a
        # desktop session has no meaningful working directory.
        with tempfile.TemporaryDirectory(prefix="delegation-gate-mcp-") as td:
            transcript = os.path.join(td, "session.jsonl")
            with open(transcript, "w") as fh:
                for rec in [
                    codex_user("delegate this to the cheapest qualified model"),
                    {"type": "assistant", "message": {"content": [{
                        "type": "tool_use", "name": "mcp__carr__update-deal", "input": {}
                    }]}},
                ]:
                    fh.write(json.dumps(rec) + "\n")
            payload = {"hook_event_name": "PreToolUse", "cwd": "/private/tmp", "session_id": "mcp-fixture",
                       "tool_name": "mcp__carr__update-deal", "tool_input": {}, "transcript_path": transcript}
            result = subprocess.run([sys.executable, HOOK], input=json.dumps(payload), text=True, capture_output=True,
                                    env=dict(os.environ, DELEGATION_GATE_STATE=os.path.join(td, "state.json")))
            try:
                response = json.loads(result.stdout or "{}")
            except json.JSONDecodeError:
                response = {}
            ok = response.get("decision") == "block"
            print(f"{'PASS' if ok else 'FAIL'}  CARR MCP is scoped without cwd: {response!r}")
            oks.append(ok)

    print(f"\n{sum(oks)}/{len(oks)} delegation-gate cases passed")
    return 0 if all(oks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
