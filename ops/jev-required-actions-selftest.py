#!/usr/bin/env python3
"""Acceptance fixtures for decision 0b11c89b's Stop-side enforcement
(lib/jev_required_actions.py, wired into hooks/completion-evidence-gate.py's
JEV REQUIRED ACTIONS GATE and hooks/executor-tier-gate.py's Agent-prompt
check).

KNOWN-BAD / KNOWN-GOOD, per the receipt this decision requires:

  · KNOWN-BAD: a turn whose build advisory required a facet, with no Jev call
    and no named refusal in the transcript, reopens the Stop.
  · KNOWN-GOOD: the same turn, with either a Jev call (a Bash command naming
    typesafe_client) or a `JEV-REFUSED: <facet> <reason>` line, does not.
  · An advisory that itself could not be read (crashed, malformed, or simply
    absent) fails OPEN and is logged, never invented as a requirement.
  · A readable advisory with an empty required_actions list is "none", not
    "unavailable" — it must not fail open silently; it must also not block.

RUNNING IT. No database, no network, no production access:

    .venv/bin/python ops/jev-required-actions-selftest.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from lib.jev_required_actions import (  # noqa: E402
    evaluate_required_actions, find_build_advisory, missing_facets,
    refused_facets_in_texts, required_facets, typesafe_called_in_commands,
)



def user(text):
    return {"type": "user", "message": {"role": "user", "content": text}}


def assistant(text):
    return {"type": "assistant", "message": {"role": "assistant", "content": text}}


def tool_use(name, value=None):
    return {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": name, "input": value or {}}
    ]}}


def bash(command_text):
    return tool_use("Bash", {"command": command_text})


def hook_attachment(hook_name, hook_event, stdout_obj):
    return {"attachment": {"type": "hook_success", "hookName": hook_name,
                           "hookEvent": hook_event, "stdout": json.dumps(stdout_obj)}}


def build_advisory_attachment(facets, authority="required"):
    advisory = {
        "schema": "jev-build-advisory/v1",
        "partner_request_sha256": "0" * 64,
        "model": "jev-1.13.0",
        "facets": {f: 0.9 for f in
                  ("architecture_or_design", "semantic_creation", "diagnosis",
                   "verification_selection", "evidence_matching", "next_action_priority")},
        "guidance": {},
        "required_actions": [{"facet": f, "instruction": f"do {f}"} for f in facets],
        "usage": {},
        "authority": authority,
        "deterministic_exclusions": [],
    }
    receipt = {
        "schema": "jev-build-turn-receipt/v1",
        "client": "claude",
        "session_id": "s1",
        "turn_id": None,
        "prompt_sha256": "0" * 64,
        "adviser_digest": "sha256:" + "0" * 64,
        "configuration_digest": "sha256:" + "0" * 64,
        "source_digest": "sha256:" + "0" * 64,
        "semantic_rule_delivery": "delivered",
        "advisory": advisory,
    }
    return hook_attachment("UserPromptSubmit", "UserPromptSubmit",
                           {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                                   "additionalContext": json.dumps(receipt)}})


def unavailable_advisory_attachment():
    receipt = {
        "schema": "jev-build-turn-receipt/v1", "client": "claude", "session_id": "s1",
        "turn_id": None, "prompt_sha256": "0" * 64, "adviser_digest": "sha256:" + "0" * 64,
        "configuration_digest": "sha256:" + "0" * 64, "source_digest": "sha256:" + "0" * 64,
        "semantic_rule_delivery": "not_attempted",
        "advisory": {"schema": "jev-build-advisory-unavailable/v1", "status": "unavailable",
                    "effect": "visible_advisory_abstention", "instruction": "Jev was unavailable."},
    }
    return hook_attachment("UserPromptSubmit", "UserPromptSubmit",
                           {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                                   "additionalContext": json.dumps(receipt)}})


def postwrite_attachment(status, tool_use_id="t1"):
    receipt = {"schema": "jev-post-write-review/v2", "receipt_id": "r1", "client": "claude",
              "session_id": "s1", "turn_id": None, "tool_use_id": tool_use_id,
              "tool_name": "Edit", "tool_input_sha256": "0" * 64,
              "configuration_digest": "sha256:" + "0" * 64, "reviewer_digest": "sha256:" + "0" * 64,
              "status": status, "paths": [], "findings": [], "models": [], "reason": None,
              "instruction": None}
    return hook_attachment("PostToolUse:Edit", "PostToolUse",
                           {"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                                   "additionalContext": json.dumps(receipt)}})


# ---------------------------------------------------------------------------
# unit-level checks on the library
# ---------------------------------------------------------------------------

def lib_reads_required_actions():
    recs = [user("build something"), build_advisory_attachment(["architecture_or_design"])]
    receipt = find_build_advisory(recs)
    ok = receipt is not None and required_facets(receipt) == ["architecture_or_design"]
    print(f"{'PASS' if ok else 'FAIL'}  lib: reads required_actions back out of the transcript")
    return ok


def lib_unavailable_is_not_zero_requirements():
    recs = [user("build something"), unavailable_advisory_attachment()]
    receipt = find_build_advisory(recs)
    ok = receipt is not None and required_facets(receipt) is None
    print(f"{'PASS' if ok else 'FAIL'}  lib: an unavailable advisory reads as None, not []")
    return ok


def lib_missing_facets_logic():
    required = ["architecture_or_design", "semantic_creation"]
    a = missing_facets(required, set(), False) == required
    b = missing_facets(required, set(), True) == []
    c = missing_facets(required, {"architecture_or_design"}, False) == ["semantic_creation"]
    ok = a and b and c
    print(f"{'PASS' if ok else 'FAIL'}  lib: missing_facets refusal/call semantics")
    return ok


def lib_refused_facets_parses_named_refusal():
    got = refused_facets_in_texts(["Some text.",
                                   "JEV-REFUSED: architecture_or_design Jev unreachable this turn"])
    ok = got == {"architecture_or_design"}
    print(f"{'PASS' if ok else 'FAIL'}  lib: JEV-REFUSED line names its facet")
    return ok


def lib_typesafe_call_detected():
    ok = (typesafe_called_in_commands(["python3 - <<'EOF'\nimport typesafe_client\nEOF"]) is True
         and typesafe_called_in_commands(["ls -la"]) is False)
    print(f"{'PASS' if ok else 'FAIL'}  lib: a Bash command naming typesafe_client counts as a call")
    return ok


def lib_semantic_creation_receipt_gap():
    recs_missing = [user("x"), tool_use("Edit", {"file_path": "a.py"})]
    recs_unavailable = recs_missing + [postwrite_attachment("unavailable")]
    recs_clear = recs_missing + [postwrite_attachment("skipped")]
    from lib.jev_required_actions import semantic_creation_receipt_missing
    ok = (semantic_creation_receipt_missing(recs_missing, True) is True
         and semantic_creation_receipt_missing(recs_unavailable, True) is True
         and semantic_creation_receipt_missing(recs_clear, True) is False
         and semantic_creation_receipt_missing(recs_missing, False) is False)
    print(f"{'PASS' if ok else 'FAIL'}  lib: an absent or unavailable post-write review is a "
          "gap; a real skipped/clear status is not")
    return ok


def evaluate_required_actions_shapes():
    none_case = evaluate_required_actions(
        [user("x"), build_advisory_attachment([])], [], [], [], False)
    unavailable_case = evaluate_required_actions(
        [user("x"), unavailable_advisory_attachment()], [], [], [], False)
    required_case = evaluate_required_actions(
        [user("x"), build_advisory_attachment(["architecture_or_design"])],
        [], [], [], False)
    ok = (none_case["status"] == "none"
         and unavailable_case["status"] == "unavailable"
         and required_case["status"] == "required"
         and required_case["missing"] == ["architecture_or_design"])
    print(f"{'PASS' if ok else 'FAIL'}  lib: evaluate_required_actions status shapes")
    return ok


# ---------------------------------------------------------------------------
# real-hook checks (completion-evidence-gate.py)
# ---------------------------------------------------------------------------

def run_gate(records, session, state):
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as fh:
        for row in records:
            fh.write(json.dumps(row) + "\n")
        path = fh.name
    try:
        hook = os.path.join(REPO, "hooks", "completion-evidence-gate.py")
        proc = subprocess.run(
            [sys.executable, hook], text=True, capture_output=True, timeout=30,
            input=json.dumps({"transcript_path": path, "session_id": session,
                              "stop_hook_active": False, "cwd": REPO}),
            env={**os.environ, "CARR_STOP_LATCH_STATE": state})
        body = json.loads(proc.stdout or "{}")
        return body.get("decision") == "block", body.get("reason", "")
    finally:
        os.unlink(path)


def known_bad_required_with_no_evidence_reopens():
    with tempfile.TemporaryDirectory(prefix="jev-required-") as state:
        records = [user("design the new seam"),
                  build_advisory_attachment(["architecture_or_design"]),
                  assistant("Here is the plan.")]
        blocked, reason = run_gate(records, "jev-known-bad", state)
        ok = blocked and "architecture_or_design" in reason
        print(f"{'PASS' if ok else 'FAIL'}  KNOWN-BAD: required facet, no Jev call, no "
              f"refusal -> reopens (blocked={blocked})")
        return ok


def known_good_jev_call_passes():
    with tempfile.TemporaryDirectory(prefix="jev-required-") as state:
        records = [user("design the new seam"),
                  build_advisory_attachment(["architecture_or_design"]),
                  bash("python3 -c \"import sys; sys.path.insert(0,'ops'); "
                      "import typesafe_client as tsc\""),
                  assistant("Asked Jev; here is the plan.")]
        blocked, _ = run_gate(records, "jev-known-good-call", state)
        ok = not blocked
        print(f"{'PASS' if ok else 'FAIL'}  KNOWN-GOOD: a Jev call this turn -> does not reopen")
        return ok


def known_good_named_refusal_passes():
    with tempfile.TemporaryDirectory(prefix="jev-required-") as state:
        records = [user("design the new seam"),
                  build_advisory_attachment(["architecture_or_design"]),
                  assistant("JEV-REFUSED: architecture_or_design Jev unreachable this turn.\n"
                           "Proceeding on my own judgment.")]
        blocked, _ = run_gate(records, "jev-known-good-refusal", state)
        ok = not blocked
        print(f"{'PASS' if ok else 'FAIL'}  KNOWN-GOOD: a named refusal -> does not reopen")
        return ok


def unavailable_advisory_fails_open():
    with tempfile.TemporaryDirectory(prefix="jev-required-") as state:
        records = [user("design the new seam"), unavailable_advisory_attachment(),
                  assistant("Here is the plan.")]
        blocked, _ = run_gate(records, "jev-unavailable", state)
        ok = not blocked
        print(f"{'PASS' if ok else 'FAIL'}  an unavailable advisory fails open (no reopen)")
        return ok


def no_advisory_at_all_fails_open():
    with tempfile.TemporaryDirectory(prefix="jev-required-") as state:
        records = [user("what time is it"), assistant("Not tracked here.")]
        blocked, _ = run_gate(records, "jev-no-advisory", state)
        ok = not blocked
        print(f"{'PASS' if ok else 'FAIL'}  no advisory record at all fails open (no reopen)")
        return ok


def latch_does_not_reopen_twice():
    with tempfile.TemporaryDirectory(prefix="jev-required-") as state:
        records = [user("design the new seam"),
                  build_advisory_attachment(["architecture_or_design"]),
                  assistant("Here is the plan.")]
        first, _ = run_gate(records, "jev-latch", state)
        second, _ = run_gate(records, "jev-latch", state)
        ok = first and not second
        print(f"{'PASS' if ok else 'FAIL'}  latch: the same missing-facet finding does not "
              f"reopen twice (first={first}, second={second})")
        return ok


def main():
    outcomes = [
        lib_reads_required_actions(),
        lib_unavailable_is_not_zero_requirements(),
        lib_missing_facets_logic(),
        lib_refused_facets_parses_named_refusal(),
        lib_typesafe_call_detected(),
        lib_semantic_creation_receipt_gap(),
        evaluate_required_actions_shapes(),
        known_bad_required_with_no_evidence_reopens(),
        known_good_jev_call_passes(),
        known_good_named_refusal_passes(),
        unavailable_advisory_fails_open(),
        no_advisory_at_all_fails_open(),
        latch_does_not_reopen_twice(),
    ]
    print(f"jev-required-actions-selftest: {sum(outcomes)}/{len(outcomes)} passed")
    return 0 if all(outcomes) else 1


if __name__ == "__main__":
    raise SystemExit(main())
