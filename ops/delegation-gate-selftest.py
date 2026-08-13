#!/usr/bin/env python3
"""Executable regressions for hooks/delegation-gate.py."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import threading
import time


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(REPO, "hooks", "delegation-gate.py")


def hook_env(extra=None, child_session=False):
    """Base env for a hook subprocess, with CLAUDE_CODE_CHILD_SESSION pinned.

    This selftest runner is itself frequently a leaf worker (spawned via the
    Agent tool), so os.environ here can carry CLAUDE_CODE_CHILD_SESSION=1 of
    its OWN. Every case below simulates a specific seat -- main or subagent --
    and must not silently inherit whichever seat happens to be running the
    test. child_session=False (the default, i.e. every pre-existing case in
    this file, which all assume main-seat behaviour) always deletes the key
    so a contaminated environment can never turn a main-seat case into a
    false-pass; child_session=True sets it to "1" to simulate the leaf-worker
    payload the gate must exempt.
    """
    env = dict(os.environ)
    env.pop("CLAUDE_CODE_CHILD_SESSION", None)
    if child_session:
        env["CLAUDE_CODE_CHILD_SESSION"] = "1"
    if extra:
        env.update(extra)
    return env


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


def tool_with_input(name, tool_input):
    return {"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "tool_use", "name": name, "input": tool_input}
    ]}}


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


def run_case(name, recs, want, state, session, cwd=REPO, subagent=False,
             tool_name="Bash", tool_input=None, child_session=False):
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
            "tool_name": tool_name,
            "tool_input": tool_input if tool_input is not None else {"command": "true"},
        }
        env = hook_env({"DELEGATION_GATE_STATE": state}, child_session=child_session)
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
            env=hook_env({"DELEGATION_GATE_STATE": os.path.join(td, "state.json")}),
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


def run_race_case(name, recs, appended, state, session, delay=0.45):
    """The declaration lands on disk only after the hook has already started.

    This is the 2026-08-11 failure made deterministic.  The harness writes the
    assistant text block and the tool_use it accompanies as separate records, so
    a hook that decided on one synchronous read could miss a declaration that
    had in fact been made, and deny a call its own logic would have allowed.
    The delay must land AFTER the hook's first read (roughly one interpreter
    startup, ~0.1s) and inside the retry window (~0.77s), or the case proves
    nothing: at 0.18s the pre-fix hook also passed, because the append had
    already happened before it ever read.  Verified to split at 0.45s.
    Before the retry fix this case returns 2; after it, 0.
    """
    with tempfile.TemporaryDirectory(prefix="delegation-gate-race-") as td:
        transcript = os.path.join(td, "session.jsonl")
        with open(transcript, "w") as fh:
            for rec in recs:
                fh.write(json.dumps(rec) + "\n")
        payload = {
            "session_id": session,
            "cwd": REPO,
            "transcript_path": transcript,
            "tool_name": "Bash",
            "tool_input": {"command": "true"},
        }

        def append_later():
            time.sleep(delay)
            with open(transcript, "a") as fh:
                fh.write(json.dumps(appended) + "\n")
                fh.flush()
                os.fsync(fh.fileno())

        writer = threading.Thread(target=append_later)
        proc = subprocess.Popen(
            [sys.executable, HOOK],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, env=hook_env({"DELEGATION_GATE_STATE": state}),
        )
        writer.start()
        _, err = proc.communicate(json.dumps(payload))
        writer.join()
        ok = proc.returncode == 0
        print(f"{'PASS' if ok else 'FAIL'}  {name}: got {proc.returncode}, want 0")
        if not ok and err:
            print(err.strip())
        return ok


def run_tool_input_case(name, recs, tool_input, want, state, session):
    """A declaration carried on the call itself, immune to transcript timing."""
    with tempfile.TemporaryDirectory(prefix="delegation-gate-ti-") as td:
        transcript = os.path.join(td, "session.jsonl")
        with open(transcript, "w") as fh:
            for rec in recs:
                fh.write(json.dumps(rec) + "\n")
        payload = {
            "session_id": session,
            "cwd": REPO,
            "transcript_path": transcript,
            "tool_name": "Bash",
            "tool_input": tool_input,
        }
        got = subprocess.run(
            [sys.executable, HOOK], input=json.dumps(payload), text=True,
            capture_output=True, env=hook_env({"DELEGATION_GATE_STATE": state}),
        )
        ok = got.returncode == want
        print(f"{'PASS' if ok else 'FAIL'}  {name}: got {got.returncode}, want {want}")
        return ok


def run_message_case(name, recs, state, session, must_contain):
    """The deny message must name every label the regex accepts."""
    with tempfile.TemporaryDirectory(prefix="delegation-gate-msg-") as td:
        transcript = os.path.join(td, "session.jsonl")
        with open(transcript, "w") as fh:
            for rec in recs:
                fh.write(json.dumps(rec) + "\n")
        payload = {
            "session_id": session,
            "cwd": REPO,
            "transcript_path": transcript,
            "tool_name": "Bash",
            "tool_input": {"command": "true"},
        }
        got = subprocess.run(
            [sys.executable, HOOK], input=json.dumps(payload), text=True,
            capture_output=True, env=hook_env({"DELEGATION_GATE_STATE": state}),
        )
        message = got.stderr or ""
        ok = got.returncode == 2 and must_contain in message
        print(f"{'PASS' if ok else 'FAIL'}  {name}: labels present={must_contain in message}")
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
            ("main seat rationale satisfies ordinary tripwire", [user("audit this"), assistant_text("executor: main seat — because this is the verification step the main seat must own"), tool("Bash")], 0, "ordinary-main-seat", REPO, False),
            ("an unlisted executor label is still rejected", [user("audit this"), assistant_text("executor: the vibes — because reasons"), tool("Bash")], 2, "ordinary-bogus-label", REPO, False),
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

        oks.append(run_race_case(
            "a declaration written while the hook runs is still honoured",
            [user("audit this"), tool("Bash")],
            assistant_text("executor: main seat — because verification belongs to the main seat"),
            state, "race-late-write",
        ))
        oks.append(run_message_case(
            "the deny message names every accepted label",
            [user("audit this"), tool("Bash")],
            state, "message-labels", GATE.EXECUTOR_LABELS,
        ))
        blocked_window = [user("audit this"), tool("Bash")]
        oks.append(run_tool_input_case(
            "a declaration in the Bash description satisfies the tripwire",
            blocked_window,
            {"command": "true",
             "description": "executor: main seat — because this is the seat's own verification"},
            0, state, "tool-input-description",
        ))
        oks.append(run_tool_input_case(
            "a leading executor comment on the command satisfies the tripwire",
            blocked_window,
            {"command": "# executor: main seat — because this is the seat's own verification\ntrue"},
            0, state, "tool-input-comment",
        ))
        oks.append(run_tool_input_case(
            "an unlisted label on the call is still rejected",
            blocked_window,
            {"command": "true", "description": "executor: the vibes — because reasons"},
            2, state, "tool-input-bogus",
        ))
        oks.append(run_tool_input_case(
            "a declaration on the call cannot release a sticky delegation",
            [user("delegate this to the cheapest qualified model"), tool("Bash")],
            {"command": "true",
             "description": "executor: main seat — because I would rather do it myself"},
            2, state, "tool-input-sticky",
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
                                    env=hook_env({"DELEGATION_GATE_STATE": os.path.join(td, "state.json")}))
            try:
                response = json.loads(result.stdout or "{}")
            except json.JSONDecodeError:
                response = {}
            ok = response.get("decision") == "block"
            print(f"{'PASS' if ok else 'FAIL'}  CARR MCP is scoped without cwd: {response!r}")
            oks.append(ok)

        # --- 2026-08-13 fix regressions -------------------------------
        # The defect: the gate fired inside subagents four times today at
        # real cost, blocking a leaf worker that has no ability to delegate
        # further, and it counted a plain echo and a self-write re-read as
        # "mechanical sweep" work. These cases pin the fix: main-seat
        # behaviour is unchanged, a subagent payload is fully exempt
        # regardless of how much real sweep work it does, and the counter
        # ignores trivial/self-referential calls while still catching real
        # repeated sweeps.

        # Main-seat: a real multi-file sweep (two different Read targets,
        # no executor declared) must still trip the tripwire exactly as
        # before. This is the behaviour the whole gate exists to keep.
        oks.append(run_case(
            "main seat: repeated real sweep (Read, Read) still fires",
            [user("audit this"),
             tool_with_input("Read", {"file_path": "/tmp/one.txt"}),
             tool_with_input("Read", {"file_path": "/tmp/two.txt"})],
            2, state, "main-seat-sweep-fires",
            tool_name="Read", tool_input={"file_path": "/tmp/three.txt"},
        ))
        oks.append(run_case(
            "main seat: repeated real sweep (Grep, Bash) still fires",
            [user("audit this"), tool("Grep"),
             tool_with_input("Bash", {"command": "grep -rl foo ."})],
            2, state, "main-seat-sweep-fires-2",
        ))

        # Subagent: CLAUDE_CODE_CHILD_SESSION=1 must exempt the call
        # entirely, even for a payload that would otherwise be a slam-dunk
        # denial (an explicit sticky delegation, repeated real sweep calls,
        # no executor declared, no Agent spawn in the window). A leaf worker
        # cannot delegate further -- exempting it is not a loophole, it is
        # the fix.
        oks.append(run_case(
            "subagent env: sticky delegation + repeated sweep is still exempt",
            [user("delegate this to the cheapest qualified model"),
             tool_with_input("Read", {"file_path": "/tmp/a.txt"}),
             tool_with_input("Grep", {"pattern": "x"})],
            0, state, "subagent-env-exempt",
            child_session=True,
        ))
        oks.append(run_case(
            "subagent env: a plain echo is not blocked (the live 2026-08-13 case)",
            [user("do the assigned sweep"),
             tool_with_input("Bash", {"command": "echo done"})],
            0, state, "subagent-env-echo",
            tool_name="Bash", tool_input={"command": "echo done"},
            child_session=True,
        ))

        # Counting refinement: a trivial echo does not consume the one free
        # lookup, so the very next real mechanical call is still the
        # session's first one and is allowed.
        oks.append(run_case(
            "counting: a leading trivial echo does not consume the free lookup",
            [user("audit this"), tool_with_input("Bash", {"command": "echo starting"})],
            0, state, "counting-echo-free",
        ))
        # Control: the same echo WITH a shell metacharacter is not trivial
        # and counts normally -- the allowance must stay narrow.
        oks.append(run_case(
            "counting: echo with a metacharacter is not exempted",
            [user("audit this"),
             tool_with_input("Bash", {"command": "echo done && rm -rf /tmp/x"})],
            2, state, "counting-echo-not-trivial",
        ))
        # A Read of a path this same turn already Wrote is a self-check, not
        # a sweep -- it must not consume the free lookup either.
        oks.append(run_case(
            "counting: a Read of a file this turn just wrote is not a sweep hit",
            [user("build this"),
             tool_with_input("Write", {"file_path": "/tmp/dg-selftest-out.txt"}),
             tool_with_input("Read", {"file_path": "/tmp/dg-selftest-out.txt"})],
            0, state, "counting-self-write-read",
        ))
        # Control: a Read of a DIFFERENT path than the one just written is an
        # ordinary sweep read and must still count -- the exemption is
        # targeted at the exact path, not a blanket post-write allowance.
        oks.append(run_case(
            "counting: a Read of an unrelated path still counts",
            [user("build this"),
             tool_with_input("Write", {"file_path": "/tmp/dg-selftest-out.txt"}),
             tool_with_input("Read", {"file_path": "/tmp/dg-selftest-other.txt"})],
            2, state, "counting-unrelated-read-counts",
        ))

    print(f"\n{sum(oks)}/{len(oks)} delegation-gate cases passed")
    return 0 if all(oks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
