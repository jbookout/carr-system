#!/usr/bin/env python3
"""Acceptance fixtures for decision 0b11c89b's Stop-side enforcement
(lib/jev_required_actions.py, wired into hooks/completion-evidence-gate.py's
JEV REQUIRED ACTIONS GATE and hooks/executor-tier-gate.py's Agent-prompt
check).

REWRITTEN 2026-09-24 after an Opus review ran the first cut over 7 real local
transcripts (803 real advisory receipts, 572 with required_actions) and found
it never fired: the first cut looked for `attachment.stdout` holding a
hookSpecificOutput-wrapped JSON string, but a real transcript stores injected
hook context as `attachment.type == "hook_additional_context"` with
`attachment.content` a list of already-decoded JSON text, sometimes nested one
level deeper under a `rule-jev-message-delivery/v2` semantic receipt's
`build_receipt` key. Every fixture below uses that real shape (verified
directly against a live ~/.claude/projects/*.jsonl session file), never the
old synthetic one.

KNOWN-BAD / KNOWN-GOOD, per the receipt this decision requires:

  · KNOWN-BAD: a turn whose build advisory required a facet, with no Jev call
    and no named refusal in the transcript, reopens the Stop.
  · KNOWN-GOOD: the same turn, with either a real per-facet Jev call receipt
    (out/jev-calls.jsonl, bound by session and time window) or a named
    `JEV-REFUSED: <facet> <reason>` line, does not.
  · BYPASS: a bare `echo typesafe_client` or a `grep` of ops/typesafe_client.py
    does NOT satisfy a required facet — the old string-matching bypass.
  · A stale advisory from an EARLIER turn never satisfies the CURRENT turn.
  · The SAME missing-facet set recurring in a LATER turn still reopens (the
    latch is per turn, not per session).
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
from datetime import datetime, timedelta, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from lib.jev_required_actions import (  # noqa: E402
    current_turn_slice, evaluate_required_actions, facets_called_this_turn,
    find_build_advisory, is_real_user_turn, is_synthetic_continuation, jev_calls_log_mentions,
    latest_user_turn_index, load_jev_call_receipts, missing_facets,
    prompt_names_facet, prompt_names_not_applicable, refused_facets_in_texts,
    required_facets, semantic_creation_receipt_missing, turn_boundary_timestamp,
    unexplained_receipts,
)

NOW = datetime.now(timezone.utc)


def ts(offset_seconds=0):
    return (NOW + timedelta(seconds=offset_seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")


def user(text, when=0):
    return {"type": "user", "timestamp": ts(when), "message": {"role": "user", "content": text}}


def user_prompt(text, prompt_id, when=0):
    """A genuine human prompt — carries `promptId`, same as a real Claude
    Code transcript's type:"user" record. See lib.jev_required_actions'
    _user_prompt_runs docstring for why this field is load-bearing."""
    return {"type": "user", "promptId": prompt_id, "timestamp": ts(when),
           "message": {"role": "user", "content": text}}


def stop_feedback(prompt_id, when=0, text="Stop hook feedback:\nfix the missing test."):
    """An automated Stop-hook reopen of a prompt: role "user", real
    non-empty text, and — per direct inspection of a live transcript — the
    SAME promptId as the turn it reopened. Must NOT be treated as a new
    turn boundary (round-2 fix #1)."""
    return {"type": "user", "promptId": prompt_id, "timestamp": ts(when),
           "message": {"role": "user", "content": text}}


def assistant(text, when=0):
    return {"type": "assistant", "timestamp": ts(when),
           "message": {"role": "assistant", "content": text}}


def tool_use(name, value=None, when=0):
    return {"type": "assistant", "timestamp": ts(when), "message": {"content": [
        {"type": "tool_use", "name": name, "input": value or {}}
    ]}}


def bash(command_text, when=0):
    return tool_use("Bash", {"command": command_text}, when=when)


def _advisory(facets):
    return {
        "schema": "jev-build-advisory/v1",
        "partner_request_sha256": "0" * 64,
        "model": "jev-1.13.0",
        "facets": {f: 0.9 for f in
                  ("architecture_or_design", "semantic_creation", "diagnosis",
                   "verification_selection", "evidence_matching", "next_action_priority")},
        "guidance": {},
        "required_actions": [{"facet": f, "instruction": f"have Jev judge {f}"} for f in facets],
        "usage": {},
        "authority": "required",
        "deterministic_exclusions": [],
    }


def _build_receipt(facets, receipt_id="r-default"):
    return {
        "schema": "jev-build-turn-receipt/v1",
        "receipt_id": receipt_id,
        "client": "claude",
        "session_id": "s1",
        "turn_id": None,
        "prompt_sha256": "0" * 64,
        "adviser_digest": "0" * 64,
        "configuration_digest": "0" * 64,
        "source_digest": "0" * 64,
        "semantic_rule_delivery": "delivered",
        "advisory": _advisory(facets),
    }


def build_advisory_attachment(facets, receipt_id="r-default", when=1):
    """REAL shape: attachment.type == 'hook_additional_context', content a
    list of raw JSON text — no stdout key, no hookSpecificOutput wrapper."""
    receipt = _build_receipt(facets, receipt_id)
    return {"timestamp": ts(when),
           "attachment": {"type": "hook_additional_context", "content": [json.dumps(receipt)]}}


def build_advisory_nested_in_message_delivery(facets, receipt_id="r-nested", when=1):
    """REAL shape observed in 130/803 sampled receipts: the top-level object
    is a rule-jev-message-delivery/v2 semantic receipt and the build receipt
    is nested under its build_receipt key."""
    build_receipt = _build_receipt(facets, receipt_id)
    wrapper = {
        "schema": "rule-jev-message-delivery/v2", "client": "claude", "session_id": "s1",
        "turn_id": None, "prompt_sha256": "0" * 64, "corpus_digest": "0" * 64,
        "selector_digest": "0" * 64, "map_digest": "0" * 64, "source_digest": "0" * 64,
        "identity": {}, "rule_ids": [], "rules": [], "probabilities": {}, "model_provenance": {},
        "build_receipt": build_receipt,
        "rule_delivery": {"mode": "delivered", "declared_packs": [], "packs_not_found": []},
        "receipt_id": "wrapper-1",
    }
    return {"timestamp": ts(when),
           "attachment": {"type": "hook_additional_context", "content": [json.dumps(wrapper)]}}


def unavailable_advisory_attachment(when=1):
    receipt = {
        "schema": "jev-build-turn-receipt/v1", "receipt_id": "r-unavailable", "client": "claude",
        "session_id": "s1", "turn_id": None, "prompt_sha256": "0" * 64, "adviser_digest": "0" * 64,
        "configuration_digest": "0" * 64, "source_digest": "0" * 64,
        "semantic_rule_delivery": "not_attempted",
        "advisory": {"schema": "jev-build-advisory-unavailable/v1", "status": "unavailable",
                    "effect": "visible_advisory_abstention", "instruction": "Jev was unavailable."},
    }
    return {"timestamp": ts(when),
           "attachment": {"type": "hook_additional_context", "content": [json.dumps(receipt)]}}


def postwrite_attachment(status, path, when=2):
    receipt = {"schema": "jev-post-write-review/v2", "receipt_id": "pw-1", "client": "claude",
              "session_id": "s1", "turn_id": None, "tool_use_id": "t1", "tool_name": "Edit",
              "tool_input_sha256": "0" * 64, "configuration_digest": "0" * 64,
              "reviewer_digest": "0" * 64, "status": status,
              "paths": [{"path": path, "status": status, "reason": "x"}],
              "findings": [], "models": [], "reason": None, "instruction": None}
    return {"timestamp": ts(when),
           "attachment": {"type": "hook_additional_context", "content": [json.dumps(receipt)]}}


def write_jev_calls_file(rows):
    fh = tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False)
    for row in rows:
        fh.write(json.dumps(row) + "\n")
    fh.close()
    return fh.name


def call_row(question_ids=None, facets=None, when=1, session="s1", ok=True):
    return {"ts": ts(when), "session": session, "question_ids": question_ids or [],
           "facets": facets or [], "model": "jev-1.13.0", "ok": ok}


# ---------------------------------------------------------------------------
# unit-level checks on the library
# ---------------------------------------------------------------------------

def lib_reads_real_shape():
    recs = [user("build something", 0), build_advisory_attachment(["architecture_or_design"])]
    receipt = find_build_advisory(recs)
    ok = receipt is not None and required_facets(receipt) == ["architecture_or_design"]
    print(f"{'PASS' if ok else 'FAIL'}  lib: reads the REAL attachment.content shape "
          "(no stdout/hookSpecificOutput wrapper)")
    return ok


def lib_reads_nested_message_delivery_shape():
    recs = [user("build something", 0),
           build_advisory_nested_in_message_delivery(["semantic_creation"])]
    receipt = find_build_advisory(recs)
    ok = receipt is not None and required_facets(receipt) == ["semantic_creation"]
    print(f"{'PASS' if ok else 'FAIL'}  lib: reads a build_receipt nested inside a "
          "rule-jev-message-delivery/v2 wrapper")
    return ok


def lib_old_stdout_shape_no_longer_matches_anything_by_accident():
    """The bug that started this rewrite: assert the OLD synthetic shape
    (which never occurs in real transcripts) is correctly NOT matched, so a
    regression back to it would be caught here."""
    old_shape_receipt = _build_receipt(["architecture_or_design"])
    old_shape_rec = {"attachment": {"type": "hook_success", "hookName": "UserPromptSubmit",
                                    "hookEvent": "UserPromptSubmit",
                                    "stdout": json.dumps({"hookSpecificOutput": {
                                        "hookEventName": "UserPromptSubmit",
                                        "additionalContext": json.dumps(old_shape_receipt)}})}}
    recs = [user("build something", 0), old_shape_rec]
    ok = find_build_advisory(recs) is None
    print(f"{'PASS' if ok else 'FAIL'}  lib: the old (never-real) stdout-wrapped shape "
          "does not match")
    return ok


def lib_current_turn_only_ignores_stale_advisory():
    stale = build_advisory_attachment(["architecture_or_design"], receipt_id="r-old", when=-500)
    recs = [user("first request", -600), stale, assistant("done", -400),
           user("second, unrelated request", 0)]
    # No advisory for the SECOND turn at all.
    receipt = find_build_advisory(recs)
    ok = receipt is None
    print(f"{'PASS' if ok else 'FAIL'}  lib: an earlier turn's advisory never satisfies "
          "a later turn with none of its own")
    return ok


def lib_current_turn_only_picks_latest_not_earliest():
    turn1 = build_advisory_attachment(["architecture_or_design"], receipt_id="r-1", when=-500)
    turn2 = build_advisory_attachment(["semantic_creation"], receipt_id="r-2", when=0)
    recs = [user("first request", -600), turn1, assistant("done", -400),
           user("second request", -10), turn2]
    receipt = find_build_advisory(recs)
    ok = receipt is not None and receipt["receipt_id"] == "r-2"
    print(f"{'PASS' if ok else 'FAIL'}  lib: picks the LATEST turn's advisory, not an "
          "earlier one")
    return ok


def lib_facets_called_this_turn_binds_by_session_and_time():
    rows = [
        call_row(facets=["architecture_or_design"], when=1, session="s1"),
        call_row(facets=["semantic_creation"], when=1, session="OTHER-SESSION"),
        call_row(facets=["evidence_matching"], when=-99999, session="s1"),
    ]
    boundary = NOW
    covered = facets_called_this_turn(
        rows, "s1", boundary,
        ["architecture_or_design", "semantic_creation", "evidence_matching"])
    ok = covered == {"architecture_or_design"}
    print(f"{'PASS' if ok else 'FAIL'}  lib: a call only counts for the right session and "
          f"inside this turn's time window (got {sorted(covered)})")
    return ok


def lib_facets_called_this_turn_per_facet_attribution():
    """A receipt covering 1 of 3 required facets leaves the other 2 missing."""
    rows = [call_row(facets=["architecture_or_design"], when=1)]
    covered = facets_called_this_turn(
        rows, "s1", NOW,
        ["architecture_or_design", "semantic_creation", "verification_selection"])
    missing = missing_facets(
        ["architecture_or_design", "semantic_creation", "verification_selection"],
        set(), covered)
    ok = covered == {"architecture_or_design"} and missing == ["semantic_creation",
                                                                "verification_selection"]
    print(f"{'PASS' if ok else 'FAIL'}  lib: per-facet attribution — one covered facet "
          f"does not clear the other two (missing={missing})")
    return ok


def lib_bypass_bare_call_no_facets_covers_nothing():
    """A call whose question ids/facets name nothing real does not launder a
    requirement — this is the shape a bare echo/grep would leave in the log
    if someone tried to fake a receipt row."""
    rows = [call_row(question_ids=["q1", "q2"], facets=[], when=1)]
    covered = facets_called_this_turn(rows, "s1", NOW, ["architecture_or_design"])
    ok = covered == set()
    print(f"{'PASS' if ok else 'FAIL'}  lib: a call with no matching question id or "
          "facets covers nothing")
    return ok


def lib_refused_facets_ignores_fenced_and_quoted_examples():
    real = "JEV-REFUSED: architecture_or_design Jev unreachable, offline network."
    fenced = "```\nJEV-REFUSED: semantic_creation example only\n```"
    quoted = 'The syntax is "JEV-REFUSED: evidence_matching like this"'
    inline = "Use `JEV-REFUSED: verification_selection` as the format."
    got = refused_facets_in_texts([real, fenced, quoted, inline])
    ok = got == {"architecture_or_design"}
    print(f"{'PASS' if ok else 'FAIL'}  lib: JEV-REFUSED only counts outside fences/"
          f"quotes/inline-code (got {sorted(got)})")
    return ok


def lib_refused_facets_requires_real_reason():
    trivial = "JEV-REFUSED: architecture_or_design x"
    got_trivial = refused_facets_in_texts([trivial])
    real = "JEV-REFUSED: architecture_or_design Jev API key file missing on this host."
    got_real = refused_facets_in_texts([real])
    ok = got_trivial == set() and got_real == {"architecture_or_design"}
    print(f"{'PASS' if ok else 'FAIL'}  lib: a refusal needs a non-trivial reason, not a "
          "single character")
    return ok


def lib_not_applicable_needs_twelve_chars():
    short = "Jev required actions: not applicable — n/a"
    long_ = "Jev required actions: not applicable — this is a read-only lookup task."
    ok = (not prompt_names_not_applicable(short)) and prompt_names_not_applicable(long_)
    print(f"{'PASS' if ok else 'FAIL'}  lib: 'not applicable' needs >= 12 chars of reason")
    return ok


def lib_facet_name_drop_needs_jev_intent_nearby():
    bare = "Also double-check architecture_or_design somewhere in the repo before merging."
    with_intent = "Before picking the seam, have Jev judge the architecture_or_design fit."
    far = ("architecture_or_design " + ("filler word " * 40) + " and elsewhere say jev")
    ok = (not prompt_names_facet(bare, "architecture_or_design")
         and prompt_names_facet(with_intent, "architecture_or_design")
         and not prompt_names_facet(far, "architecture_or_design"))
    print(f"{'PASS' if ok else 'FAIL'}  lib: a bare facet mention with no nearby Jev "
          "intent does not count")
    return ok


def lib_semantic_creation_scoped_to_files_written_this_turn():
    turn_recs = [postwrite_attachment("clear", "hooks/a.py")]
    ok = (semantic_creation_receipt_missing(turn_recs, {"hooks/a.py"}) is False
         and semantic_creation_receipt_missing(turn_recs, {"hooks/b.py"}) is True
         and semantic_creation_receipt_missing(turn_recs, set()) is False)
    turn_recs_unavailable = [postwrite_attachment("unavailable", "hooks/a.py")]
    ok = ok and semantic_creation_receipt_missing(turn_recs_unavailable, {"hooks/a.py"}) is True
    print(f"{'PASS' if ok else 'FAIL'}  lib: semantic_creation is scoped to files actually "
          "written this turn — a review of a DIFFERENT file does not clear it")
    return ok


# ---------------------------------------------------------------------------
# round-2 fixes (2026-09-24, second Opus re-replay of PR #1224)
# ---------------------------------------------------------------------------

def lib_reopen_does_not_start_a_new_turn():
    """BLOCKING fix #1. A genuine prompt (promptId=p1) gets its advisory; a
    Stop-hook-feedback reopen of THAT SAME prompt (also promptId=p1, real
    non-empty "user" text) must NOT be treated as a fresh turn boundary — the
    reviewer reproduced required -> reopen -> unavailable from exactly this
    shape. The advisory must still be found (and be the LATEST one, since the
    hook may also run on the reopen and attach its own) after the reopen."""
    recs = [
        user_prompt("design the new seam", "p1", when=0),
        build_advisory_attachment(["architecture_or_design"], receipt_id="turn1-advisory", when=1),
        assistant("Here is a first pass.", 2),
        stop_feedback("p1", when=3),
        build_advisory_attachment(["architecture_or_design"], receipt_id="turn1-reopen-advisory", when=4),
        assistant("Revised.", 5),
    ]
    boundary = latest_user_turn_index(recs)
    ok = boundary == 0  # still the ORIGINAL prompt, not the reopen at index 3
    receipt = find_build_advisory(recs)
    ok = ok and receipt is not None and receipt.get("receipt_id") == "turn1-reopen-advisory"
    ok = ok and required_facets(receipt) == ["architecture_or_design"]
    print(f"{'PASS' if ok else 'FAIL'}  lib: a Stop-hook-feedback reopen continues the "
          f"current turn, not a new one (boundary={boundary})")
    return ok


def lib_reopen_does_not_start_a_new_turn_even_with_own_promptid():
    """A reopen record has been observed carrying its own promptId in some
    shapes; even then, its TEXT prefix ("Stop hook feedback:") must mark it
    as a synthetic continuation rather than a new-turn boundary."""
    recs = [
        user_prompt("design the new seam", "p1", when=0),
        build_advisory_attachment(["semantic_creation"], receipt_id="only-advisory", when=1),
        assistant("First pass.", 2),
        stop_feedback("p1-reopen-own-id", when=3),
    ]
    boundary = latest_user_turn_index(recs)
    ok = boundary == 0
    print(f"{'PASS' if ok else 'FAIL'}  lib: a reopen is recognized as synthetic by its "
          f"own text even when its promptId differs (boundary={boundary})")
    return ok


def lib_new_genuine_prompt_after_a_reopen_does_start_a_new_turn():
    """The flip side: a genuinely NEW human prompt (a different promptId,
    real content, no synthetic prefix) after a reopened turn DOES move the
    boundary — this must not become "advisories never expire"."""
    recs = [
        user_prompt("first request", "p1", when=-100),
        build_advisory_attachment(["architecture_or_design"], receipt_id="stale", when=-99),
        assistant("done", -98),
        stop_feedback("p1", when=-90),
        user_prompt("second, unrelated request", "p2", when=-10),
        build_advisory_attachment(["semantic_creation"], receipt_id="fresh", when=-9),
        assistant("Here.", 0),
    ]
    receipt = find_build_advisory(recs)
    ok = receipt is not None and receipt.get("receipt_id") == "fresh"
    ok = ok and required_facets(receipt) == ["semantic_creation"]
    print(f"{'PASS' if ok else 'FAIL'}  lib: a genuinely new prompt still moves the turn "
          "boundary forward")
    return ok


def lib_is_synthetic_continuation_covers_task_notifications_and_reminders():
    task_notif = {"type": "user", "message": {"role": "user",
                  "content": "<task-notification>a background task finished</task-notification>"}}
    reminder_only = {"type": "user", "message": {"role": "user",
                     "content": "<system-reminder>context only, no real prompt</system-reminder>"}}
    genuine = {"type": "user", "message": {"role": "user", "content": "please build the seam"}}
    ok = (is_synthetic_continuation(task_notif)
         and is_synthetic_continuation(reminder_only)
         and not is_synthetic_continuation(genuine))
    print(f"{'PASS' if ok else 'FAIL'}  lib: is_synthetic_continuation covers "
          "task-notification and system-reminder-only records")
    return ok


def lib_refused_facets_handles_each_batched_form():
    """Fix #2's four literal example forms from the review, verbatim."""
    # Round 3: the placeholder word "reason" no longer passes (12-char floor),
    # so each form carries a real reason of the kind the 28 real refusal lines
    # in session f4d5b78a use.
    forms_and_expected = [
        ("JEV-REFUSED: semantic_creation, evidence_matching — TypeSafe returned HTTP 402",
         {"semantic_creation", "evidence_matching"}),
        ("JEV-REFUSED: semantic_creation and evidence_matching. Jev unreachable this turn",
         {"semantic_creation", "evidence_matching"}),
        ("JEV-REFUSED: next_action_priority. the partner named the next step",
         {"next_action_priority"}),
        ("JEV-REFUSED: I made no separate Jev calls for semantic_creation, "
         "diagnosis, verification_selection or evidence_matching this turn "
         "because the advisory arrived after the write was already done.",
         {"semantic_creation", "diagnosis", "verification_selection", "evidence_matching"}),
    ]
    ok = True
    for text, expected in forms_and_expected:
        got = refused_facets_in_texts([text])
        this_ok = got == expected
        ok = ok and this_ok
        print(f"  {'PASS' if this_ok else 'FAIL'}  batched-refusal form {text[:60]!r}... "
              f"-> {sorted(got)}")
    print(f"{'PASS' if ok else 'FAIL'}  lib: refused_facets_in_texts handles every literal "
          "batched-refusal form from the review")
    return ok


def lib_blockquoted_refusal_line_does_not_count():
    quoted = ("> JEV-REFUSED: architecture_or_design not actually refusing, just quoting "
             "the syntax for the teammate reading this PR.")
    ok = refused_facets_in_texts([quoted]) == set()
    print(f"{'PASS' if ok else 'FAIL'}  lib: a blockquoted (>) JEV-REFUSED line never counts")
    return ok


def lib_current_turn_slice_and_boundary_ts():
    recs = [user("hello", -10), assistant("hi", -9), user("do the thing", 0),
           build_advisory_attachment(["architecture_or_design"], when=1)]
    idx = latest_user_turn_index(recs)
    ok = idx == 2 and is_real_user_turn(recs[2])
    boundary = turn_boundary_timestamp(recs)
    ok = ok and boundary is not None
    sliced = current_turn_slice(recs)
    ok = ok and len(sliced) == 3  # one-record lookback + the two after
    print(f"{'PASS' if ok else 'FAIL'}  lib: latest_user_turn_index/current_turn_slice/"
          "turn_boundary_timestamp agree on the current turn's boundary")
    return ok


def evaluate_required_actions_shapes():
    none_case = evaluate_required_actions(
        [user("x", 0), build_advisory_attachment([], when=1)], [], "/nonexistent.jsonl", "s1", [])
    unavailable_case = evaluate_required_actions(
        [user("x", 0), unavailable_advisory_attachment()], [], "/nonexistent.jsonl", "s1", [])
    required_case = evaluate_required_actions(
        [user("x", 0), build_advisory_attachment(["architecture_or_design"])],
        [], "/nonexistent.jsonl", "s1", [])
    ok = (none_case["status"] == "none"
         and unavailable_case["status"] == "unavailable"
         and required_case["status"] == "required"
         and required_case["missing"] == ["architecture_or_design"]
         and required_case["turn_key"] == "r-default")
    print(f"{'PASS' if ok else 'FAIL'}  lib: evaluate_required_actions status shapes and "
          "turn_key")
    return ok


# ---------------------------------------------------------------------------
# real-hook checks (completion-evidence-gate.py)
# ---------------------------------------------------------------------------

def run_gate(records, session, state, env_extra=None):
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as fh:
        for row in records:
            fh.write(json.dumps(row) + "\n")
        path = fh.name
    try:
        hook = os.path.join(REPO, "hooks", "completion-evidence-gate.py")
        env = {**os.environ, "CARR_STOP_LATCH_STATE": state}
        env.update(env_extra or {})
        proc = subprocess.run(
            [sys.executable, hook], text=True, capture_output=True, timeout=30,
            input=json.dumps({"transcript_path": path, "session_id": session,
                              "stop_hook_active": False, "cwd": REPO}),
            env=env)
        body = json.loads(proc.stdout or "{}")
        return body.get("decision") == "block", body.get("reason", "")
    finally:
        os.unlink(path)


def known_bad_required_with_no_evidence_reopens():
    with tempfile.TemporaryDirectory(prefix="jev-required-") as state:
        records = [user("design the new seam", 0),
                  build_advisory_attachment(["architecture_or_design"], receipt_id="known-bad"),
                  assistant("Here is the plan.", 2)]
        blocked, reason = run_gate(records, "jev-known-bad", state)
        ok = blocked and "architecture_or_design" in reason
        print(f"{'PASS' if ok else 'FAIL'}  KNOWN-BAD: required facet, no Jev call, no "
              f"refusal -> reopens (blocked={blocked})")
        return ok


def known_good_real_jev_call_receipt_passes():
    with tempfile.TemporaryDirectory(prefix="jev-required-") as state:
        session = "jev-known-good-call"
        records = [user("design the new seam", 0),
                  build_advisory_attachment(["architecture_or_design"], receipt_id="known-good"),
                  assistant("Asked Jev; here is the plan.", 2)]
        calls_path = write_jev_calls_file([
            call_row(question_ids=["best_fit"], facets=["architecture_or_design"],
                    when=1, session=session)])
        # Point the gate at OUR calls file, not the real out/jev-calls.jsonl.
        env = {"CARR_JEV_CALLS_LOG_OVERRIDE": calls_path}
        blocked, _ = run_gate(records, session, state, env_extra=env)
        os.unlink(calls_path)
        ok = not blocked
        print(f"{'PASS' if ok else 'FAIL'}  KNOWN-GOOD: a real per-facet Jev call "
              "receipt this turn -> does not reopen")
        return ok


def bypass_bash_echo_or_grep_no_longer_satisfies():
    with tempfile.TemporaryDirectory(prefix="jev-required-") as state:
        records = [user("design the new seam", 0),
                  build_advisory_attachment(["architecture_or_design"], receipt_id="bypass-1"),
                  bash("echo typesafe_client", when=1),
                  bash("grep ask ops/typesafe_client.py", when=1),
                  assistant("Done.", 2)]
        blocked, reason = run_gate(records, "jev-bypass", state)
        ok = blocked and "architecture_or_design" in reason
        print(f"{'PASS' if ok else 'FAIL'}  BYPASS CLOSED: a bare echo/grep naming "
              f"typesafe_client no longer satisfies the gate (blocked={blocked})")
        return ok


def known_good_named_refusal_passes():
    with tempfile.TemporaryDirectory(prefix="jev-required-") as state:
        records = [user("design the new seam", 0),
                  build_advisory_attachment(["architecture_or_design"], receipt_id="refusal-1"),
                  assistant("JEV-REFUSED: architecture_or_design Jev API key file is "
                           "missing on this host.\nProceeding on my own judgment.", 2)]
        blocked, _ = run_gate(records, "jev-known-good-refusal", state)
        ok = not blocked
        print(f"{'PASS' if ok else 'FAIL'}  KNOWN-GOOD: a real named refusal -> does not "
              "reopen")
        return ok


def unavailable_advisory_fails_open():
    with tempfile.TemporaryDirectory(prefix="jev-required-") as state:
        records = [user("design the new seam", 0), unavailable_advisory_attachment(),
                  assistant("Here is the plan.", 2)]
        blocked, _ = run_gate(records, "jev-unavailable", state)
        ok = not blocked
        print(f"{'PASS' if ok else 'FAIL'}  an unavailable advisory fails open (no reopen)")
        return ok


def no_advisory_at_all_fails_open():
    with tempfile.TemporaryDirectory(prefix="jev-required-") as state:
        records = [user("what time is it", 0), assistant("Not tracked here.", 1)]
        blocked, _ = run_gate(records, "jev-no-advisory", state)
        ok = not blocked
        print(f"{'PASS' if ok else 'FAIL'}  no advisory record at all fails open (no reopen)")
        return ok


def stale_earlier_turn_advisory_does_not_satisfy_current_turn():
    with tempfile.TemporaryDirectory(prefix="jev-required-") as state:
        stale = build_advisory_attachment(["architecture_or_design"], receipt_id="stale",
                                          when=-500)
        records = [user("first, unrelated request", -600), stale, assistant("done", -400),
                  user("second request, needs the seam decided", -10),
                  build_advisory_attachment(["semantic_creation"], receipt_id="fresh", when=-9),
                  assistant("Here is the code.", 0)]
        blocked, reason = run_gate(records, "jev-stale-vs-fresh", state)
        # The current (second) turn's own advisory required semantic_creation,
        # not architecture_or_design — the reopen must name the RIGHT facet.
        ok = blocked and "semantic_creation" in reason and "architecture_or_design" not in reason
        print(f"{'PASS' if ok else 'FAIL'}  a stale earlier-turn advisory never leaks into "
              f"the current turn's check (reason={reason!r})")
        return ok


def latch_is_per_turn_not_per_session():
    """The SAME missing-facet set recurring in a LATER turn (a different
    build-advisory receipt) still reopens — the old per-session-only latch
    would have silenced turn 2 forever after turn 1 fired once."""
    with tempfile.TemporaryDirectory(prefix="jev-required-") as state:
        session = "jev-per-turn-latch"
        turn1 = [user("first request", -600),
                build_advisory_attachment(["architecture_or_design"], receipt_id="turn-1",
                                          when=-590),
                assistant("Plan A.", -580)]
        first, _ = run_gate(turn1, session, state)

        turn2 = turn1 + [user("second, unrelated request", -10),
                        build_advisory_attachment(["architecture_or_design"],
                                                  receipt_id="turn-2", when=-5),
                        assistant("Plan B.", 0)]
        second, _ = run_gate(turn2, session, state)
        ok = first and second
        print(f"{'PASS' if ok else 'FAIL'}  latch: the same missing facet in a LATER turn "
              f"(different receipt id) still reopens (first={first}, second={second})")
        return ok


def post_reopen_turn_still_enforced_end_to_end():
    """Fix #1, exercised through the real hook subprocess (run_gate), not
    just the library function: after a Stop-hook-feedback reopen of the SAME
    prompt, with still no Jev call and no refusal, the gate must STILL
    reopen — this reproduces (and closes) the reviewer's literal repro
    (required -> reopen -> unavailable)."""
    with tempfile.TemporaryDirectory(prefix="jev-required-") as state:
        records = [
            user_prompt("design the new seam", "p-reopen", when=0),
            build_advisory_attachment(["architecture_or_design"], receipt_id="reopen-1", when=1),
            assistant("First pass.", 2),
            stop_feedback("p-reopen", when=3),
        ]
        blocked, reason = run_gate(records, "jev-post-reopen", state)
        ok = blocked and "architecture_or_design" in reason
        print(f"{'PASS' if ok else 'FAIL'}  post-reopen: a Stop-hook-feedback reopen with "
              f"still no evidence still reopens (blocked={blocked}), not 'unavailable'")
        return ok


def post_reopen_turn_with_refusal_after_the_reopen_passes():
    """The companion KNOWN-GOOD: the refusal itself can arrive IN the reopen
    turn (after the Stop-hook-feedback record) and must still satisfy the
    SAME original advisory, since it's still the current turn."""
    with tempfile.TemporaryDirectory(prefix="jev-required-") as state:
        records = [
            user_prompt("design the new seam", "p-reopen-2", when=0),
            build_advisory_attachment(["architecture_or_design"], receipt_id="reopen-2", when=1),
            assistant("First pass.", 2),
            stop_feedback("p-reopen-2", when=3),
            assistant("JEV-REFUSED: architecture_or_design Jev was unreachable this turn.", 4),
        ]
        blocked, _ = run_gate(records, "jev-post-reopen-refusal", state)
        ok = not blocked
        print(f"{'PASS' if ok else 'FAIL'}  post-reopen: a refusal issued AFTER the reopen "
              "still satisfies the original turn's advisory")
        return ok


def latch_does_not_reopen_twice_for_the_same_turn():
    with tempfile.TemporaryDirectory(prefix="jev-required-") as state:
        records = [user("design the new seam", 0),
                  build_advisory_attachment(["architecture_or_design"], receipt_id="same-turn"),
                  assistant("Here is the plan.", 2)]
        first, _ = run_gate(records, "jev-latch-same-turn", state)
        second, _ = run_gate(records, "jev-latch-same-turn", state)
        ok = first and not second
        print(f"{'PASS' if ok else 'FAIL'}  latch: the SAME turn's finding does not reopen "
              f"twice (first={first}, second={second})")
        return ok


# ---------------------------------------------------------------------------
# round 3 (2026-09-24, third Opus review of PR #1224): real-data replay
#
# Every record below is a REDACTED copy of a real record from a local
# transcript (session f4d5b78a): the keys, nesting, origin stamps, isMeta
# flags and message prefixes are verbatim; ids, paths, bodies and hashes are
# replaced. Timestamps are relative to NOW so the call-window arithmetic is
# exercised the way a live Stop would see it.
# ---------------------------------------------------------------------------

def real_human_prompt(prompt_id, when=0, content="REDACTED human prompt"):
    return {"parentUuid": "p-0", "isSidechain": False, "promptId": prompt_id, "type": "user",
            "message": {"role": "user", "content": content}, "uuid": f"u-{prompt_id}",
            "timestamp": ts(when), "permissionMode": "auto", "origin": {"kind": "human"},
            "promptSource": "user", "turnOrigin": "user", "userType": "external",
            "entrypoint": "claude-desktop", "cwd": "/REDACTED", "sessionId": "s1",
            "version": "2.1.280", "gitBranch": "main"}


def real_task_notification(prompt_id, when):
    return {"parentUuid": "p-1", "isSidechain": False, "promptId": prompt_id, "type": "user",
            "message": {"role": "user", "content":
                        "<task-notification>\n<task-id>REDACTED</task-id>\n"
                        "<tool-use-id>REDACTED</tool-use-id>\n<status>completed</status>\n"
                        "</task-notification>"},
            "uuid": f"u-{prompt_id}", "timestamp": ts(when), "permissionMode": "auto",
            "origin": {"kind": "task-notification"}, "promptSource": "system",
            "turnOrigin": "task_notification", "queueSkipAttachments": True,
            "userType": "external", "entrypoint": "claude-desktop", "cwd": "/REDACTED",
            "sessionId": "s1", "version": "2.1.280", "gitBranch": "main"}


def real_cross_session_message(prompt_id, when, with_origin=True):
    rec = {"parentUuid": "p-2", "isSidechain": False, "promptId": prompt_id, "type": "user",
           "message": {"role": "user", "content":
                       "Another Claude session sent a message:\n<cross-session-message "
                       "from=\"uds:REDACTED\" from-session=\"local_REDACTED\" "
                       "from-name=\"REDACTED\" from-mode=\"prompting\">\nREDACTED body\n"
                       "</cross-session-message>"},
           "isMeta": True, "uuid": f"u-{prompt_id}", "timestamp": ts(when),
           "permissionMode": "auto", "promptSource": "system", "turnOrigin": "peer",
           "queueSkipAttachments": True, "userType": "external",
           "entrypoint": "claude-desktop", "cwd": "/REDACTED", "sessionId": "s1",
           "version": "2.1.280", "gitBranch": "main"}
    if with_origin:
        rec["origin"] = {"kind": "peer", "from": "uds:REDACTED", "name": "REDACTED"}
    return rec


def real_advisory(facets, receipt_id, when):
    """The real UserPromptSubmit attachment: nested message-delivery shape,
    plus the hookName/hookEvent keys a real one carries."""
    rec = build_advisory_nested_in_message_delivery(facets, receipt_id=receipt_id, when=when)
    rec["attachment"].update({"hookName": "UserPromptSubmit", "hookEvent": "UserPromptSubmit",
                              "toolUseID": "hook-REDACTED"})
    rec.update({"type": "attachment", "isSidechain": False, "uuid": f"a-{receipt_id}"})
    return rec


def replay_notification_with_empty_advisory_cannot_erase_required():
    """FIX 1 (round 4 form). A human prompt requiring two facets, then a
    background task notification carrying its OWN advisory (249 of 255 real
    notifications in f4d5b78a carry one). Only the prompt's advisory binds:
    a notification's advisory can neither erase a facet (round 2's bug) nor
    add one (round 3's bug — Jev rates the notification text itself)."""
    recs = [real_human_prompt("P1", 0),
            real_advisory(["architecture_or_design", "semantic_creation"], "r-human", 1),
            assistant("Starting.", 2),
            real_task_notification("N1", 300),
            real_advisory([], "r-notification", 301),
            assistant("Background task finished.", 302)]
    result = evaluate_required_actions(recs, [], "/nonexistent.jsonl", "s1", [])
    ok = (latest_user_turn_index(recs) == 0
          and result["status"] == "required"
          and result["required"] == ["architecture_or_design", "semantic_creation"]
          and result["missing"] == ["architecture_or_design", "semantic_creation"]
          and result["turn_key"] == "r-human")
    # A notification advisory naming a NEW facet is not consulted either.
    recs2 = recs + [real_task_notification("N2", 400), real_advisory(["diagnosis"], "r-n2", 401)]
    result2 = evaluate_required_actions(recs2, [], "/nonexistent.jsonl", "s1", [])
    ok = ok and result2["required"] == ["architecture_or_design", "semantic_creation"] \
        and result2["turn_key"] == "r-human"
    print(f"{'PASS' if ok else 'FAIL'}  replay: a notification's advisory neither erases nor adds "
          f"a facet (got {result['status']} {result['required']}; after a notification naming "
          f"diagnosis: {result2['required']})")
    return ok


def replay_notification_with_empty_advisory_reopens_end_to_end():
    with tempfile.TemporaryDirectory(prefix="jev-required-") as state:
        records = [real_human_prompt("P1", 0),
                   real_advisory(["architecture_or_design"], "r-e2e-union", 1),
                   assistant("Plan drafted.", 2),
                   real_task_notification("N1", 60),
                   real_advisory([], "r-e2e-notification", 61),
                   assistant("All done here.", 62)]
        blocked, _ = run_gate(records, "s-union", state,
                              env_extra={"CARR_JEV_CALLS_LOG_OVERRIDE": "/nonexistent.jsonl"})
    ok = blocked is True
    print(f"{'PASS' if ok else 'FAIL'}  replay end-to-end: prompt advisory + notification with an "
          f"empty advisory, no evidence -> reopens (blocked={blocked})")
    return ok


def replay_ninety_minute_turn_call_counts():
    """FIX 2. A real call at minute 90 of a turn counts; the window is turn
    start to now, bound to the session id, with no duration cap."""
    boundary = datetime.fromisoformat(ts(-5700).replace("Z", "+00:00"))
    now = datetime.fromisoformat(ts(0).replace("Z", "+00:00"))
    rows = [call_row(facets=["architecture_or_design"], when=-300)]            # minute 90
    late = facets_called_this_turn(rows, "s1", boundary, ["architecture_or_design"], now=now)
    other_session = facets_called_this_turn(
        [call_row(facets=["architecture_or_design"], when=-300, session="s2")],
        "s1", boundary, ["architecture_or_design"], now=now)
    before_turn = facets_called_this_turn(
        [call_row(facets=["architecture_or_design"], when=-5800)],
        "s1", boundary, ["architecture_or_design"], now=now)
    after_now = facets_called_this_turn(
        [call_row(facets=["architecture_or_design"], when=600)],
        "s1", boundary, ["architecture_or_design"], now=now)
    recs = [real_human_prompt("P1", -5700),
            real_advisory(["architecture_or_design"], "r-long", -5699),
            assistant("long work", -3000)]
    path = write_jev_calls_file(rows)
    try:
        result = evaluate_required_actions(recs, [], path, "s1", [], now=now)
    finally:
        os.unlink(path)
    ok = (late == {"architecture_or_design"} and other_session == set()
          and before_turn == set() and after_now == set() and result["missing"] == [])
    print(f"{'PASS' if ok else 'FAIL'}  replay: a call at minute 90 of a 95-minute turn counts; "
          "another session's, a pre-turn, and a future receipt do not")
    return ok


def replay_cross_session_message_folds_into_turn():
    """FIX 3. A real cross-session message ("Another Claude session sent a
    message") is folded into the running turn — with or without the origin
    stamp — and its advisory joins the union."""
    ok = True
    for with_origin in (True, False):
        recs = [real_human_prompt("P1", 0),
                real_advisory(["verification_selection"], "r-p", 1),
                assistant("working", 2),
                real_cross_session_message("X1", 120, with_origin=with_origin),
                real_advisory(["evidence_matching"], "r-x", 121),
                assistant("Ack.", 122)]
        result = evaluate_required_actions(recs, [], "/nonexistent.jsonl", "s1", [])
        this_ok = (is_synthetic_continuation(recs[3])
                   and latest_user_turn_index(recs) == 0
                   and result["required"] == ["verification_selection"])
        ok = ok and this_ok
        print(f"  {'PASS' if this_ok else 'FAIL'}  cross-session message (origin stamp="
              f"{with_origin}) folds: required={result['required']}")
    print(f"{'PASS' if ok else 'FAIL'}  replay: a cross-session message is folded into the turn "
          "and its own advisory is not binding")
    return ok


def replay_refusal_before_notification_still_counts_end_to_end():
    """A JEV-REFUSED line written BEFORE a folded notification must still
    satisfy the turn (the gate reads the library's folded turn, not its own
    human_turns window, which restarts at a notification)."""
    with tempfile.TemporaryDirectory(prefix="jev-required-") as state:
        records = [real_human_prompt("P1", 0),
                   real_advisory(["diagnosis"], "r-refuse-early", 1),
                   assistant("JEV-REFUSED: diagnosis TypeSafe returned HTTP 402, no credits", 2),
                   real_task_notification("N1", 60),
                   real_advisory([], "r-refuse-early-n", 61),
                   assistant("Background task done.", 62)]
        blocked, reason = run_gate(records, "s-refuse-early", state,
                                   env_extra={"CARR_JEV_CALLS_LOG_OVERRIDE": "/nonexistent.jsonl"})
    ok = blocked is False
    print(f"{'PASS' if ok else 'FAIL'}  replay end-to-end: a refusal written before a folded "
          f"notification still satisfies the turn (blocked={blocked})")
    return ok


def refusal_reason_floor_and_stoplist():
    """FIX 4. At least 12 characters, and not a stoplisted placeholder."""
    rejected = ["JEV-REFUSED: diagnosis none", "JEV-REFUSED: diagnosis n/a",
                "JEV-REFUSED: diagnosis na", "JEV-REFUSED: diagnosis skip",
                "JEV-REFUSED: diagnosis not needed", "JEV-REFUSED: diagnosis — not needed here",
                "JEV-REFUSED: diagnosis not applicable", "JEV-REFUSED: diagnosis reason",
                "JEV-REFUSED: diagnosis too short"]
    accepted = ["JEV-REFUSED: diagnosis TypeSafe HTTP 402 billing error",
                "JEV-REFUSED: diagnosis — the partner already diagnosed it"]
    bad = [t for t in rejected if refused_facets_in_texts([t])]
    missed = [t for t in accepted if refused_facets_in_texts([t]) != {"diagnosis"}]
    ok = not bad and not missed
    print(f"{'PASS' if ok else 'FAIL'}  refusal reason needs >=12 chars and no stoplisted "
          f"placeholder (wrongly accepted={bad}, wrongly rejected={missed})")
    return ok


def forge_detection_names_the_ledger():
    """FIX 5b. Any tool call in the turn naming out/jev-calls.jsonl is
    returned for a detection event; a real ask() run never names it."""
    turn = [real_human_prompt("P1", 0),
            bash("echo '{\"session\": \"s1\", \"facets\": [\"diagnosis\"], \"ok\": true}' "
                 ">> out/jev-calls.jsonl", 1),
            bash("tail -3 out/jev-calls.jsonl 2>&1", 2),
            tool_use("Edit", {"file_path": "/repo/out/jev-calls.jsonl"}, 3),
            bash("./.venv/bin/python scratch/ask_jev.py", 4)]
    found = jev_calls_log_mentions(turn)
    ok = ([m["write_like"] for m in found] == [True, False, True]
          and [m["tool"] for m in found] == ["Bash", "Bash", "Edit"])
    print(f"{'PASS' if ok else 'FAIL'}  forge detection: a shell append and an Edit on the ledger "
          f"are write-like, a tail is a read, an ask() script is not named ({found})")
    return ok


def forge_detection_event_recorded_end_to_end():
    session = f"s-forge-{os.getpid()}"
    log = os.path.join(REPO, "out", "jev-required-actions-gate.jsonl")
    with tempfile.TemporaryDirectory(prefix="jev-required-") as state:
        calls = write_jev_calls_file([call_row(facets=["diagnosis"], when=1, session=session)])
        try:
            records = [real_human_prompt("P1", 0),
                       real_advisory(["diagnosis"], "r-forge", 1),
                       bash(f"echo '{{\"session\": \"{session}\", \"facets\": [\"diagnosis\"], "
                            f"\"ok\": true}}' >> out/jev-calls.jsonl", 2),
                       assistant("Diagnosed.", 3)]
            blocked, _ = run_gate(records, session, state,
                                  env_extra={"CARR_JEV_CALLS_LOG_OVERRIDE": calls})
        finally:
            os.unlink(calls)
    events = []
    try:
        with open(log) as fh:
            for line in fh:
                row = json.loads(line)
                if row.get("session") == session and row.get("event") == "jev_calls_log_named":
                    events.append(row)
    except OSError:
        pass
    ok = blocked is False and len(events) == 1 and events[0]["write_like"] is True
    print(f"{'PASS' if ok else 'FAIL'}  forge detection end-to-end: the forged receipt passes the "
          f"verdict but a write-like detection event is recorded (events={len(events)})")
    return ok


def refusal_then_notification_facet_does_not_reopen_end_to_end():
    """THE ROUND-4 BLOCKER, end to end. The prompt requires one facet and the
    turn refuses it; a later notification's advisory names three more facets.
    Round 3 reopened here (and re-keyed the latch per new facet set); the
    prompt-only reader does not."""
    with tempfile.TemporaryDirectory(prefix="jev-required-") as state:
        records = [real_human_prompt("P1", 0),
                   real_advisory(["next_action_priority"], "r-blocker", 1),
                   assistant("JEV-REFUSED: next_action_priority the partner named the next step", 2),
                   real_task_notification("N1", 60),
                   real_advisory(["diagnosis", "evidence_matching", "semantic_creation"],
                                 "r-blocker-n", 61),
                   assistant("Background task done.", 62)]
        blocked, _ = run_gate(records, "s-blocker", state,
                              env_extra={"CARR_JEV_CALLS_LOG_OVERRIDE": "/nonexistent.jsonl"})
    ok = blocked is False
    print(f"{'PASS' if ok else 'FAIL'}  blocker: a refused prompt facet plus a notification advisory "
          f"naming new facets does not reopen (blocked={blocked})")
    return ok


def tool_result(tool_use_id, when):
    return {"type": "user", "timestamp": ts(when), "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": tool_use_id, "content": "ok"}]}}


def tool_use_with_id(name, value, tool_use_id, when):
    return {"type": "assistant", "timestamp": ts(when), "message": {"content": [
        {"type": "tool_use", "id": tool_use_id, "name": name, "input": value}]}}


def receipt_provenance_backstop():
    """BACKSTOP. A credited receipt with no Python tool call in flight (Bash
    running python, or an Agent) within RECEIPT_EXPLAIN_SECONDS is
    unexplained. Covers the indirect forge the mention check misses."""
    recs = [real_human_prompt("P1", 0),
            tool_use_with_id("Bash", {"command": "./.venv/bin/python scratch/ask_jev.py"}, "b1", 10),
            tool_result("b1", 14),
            tool_use_with_id("Bash", {"command": "f=out/jev-calls; echo x >> $f.jsonl"}, "b2", 400),
            tool_result("b2", 401),
            tool_use_with_id("Agent", {"prompt": "x", "run_in_background": True}, "a1", 1000),
            tool_result("a1", 1001),
            {"type": "user", "timestamp": ts(2000), "message": {"role": "user", "content":
             "<task-notification>\n<tool-use-id>a1</tool-use-id>\n</task-notification>"}}]
    by_python = {"ts": ts(13), "facets": ["diagnosis"]}
    just_after = {"ts": ts(120), "facets": ["diagnosis"]}           # 106 s after it finished
    forged = {"ts": ts(401), "facets": ["diagnosis"]}               # only a shell append ran
    by_agent = {"ts": ts(1500), "facets": ["diagnosis"]}            # background Agent in flight
    after_agent = {"ts": ts(2300), "facets": ["diagnosis"]}         # 300 s after it finished
    got = unexplained_receipts(recs, [by_python, just_after, forged, by_agent, after_agent])
    ok = got == [forged, after_agent]
    print(f"{'PASS' if ok else 'FAIL'}  backstop: receipts with no Python tool call or Agent in "
          f"flight are unexplained (got {[r['ts'] for r in got]})")
    return ok


def receipt_unexplained_event_recorded_end_to_end():
    session = f"s-unexplained-{os.getpid()}"
    log = os.path.join(REPO, "out", "jev-required-actions-gate.jsonl")
    with tempfile.TemporaryDirectory(prefix="jev-required-") as state:
        calls = write_jev_calls_file([call_row(facets=["diagnosis"], when=3, session=session)])
        try:
            records = [real_human_prompt("P1", 0),
                       real_advisory(["diagnosis"], "r-unexplained", 1),
                       bash("f=out/jev-calls; echo row >> $f.jsonl", 2),
                       assistant("Diagnosed.", 4)]
            blocked, _ = run_gate(records, session, state,
                                  env_extra={"CARR_JEV_CALLS_LOG_OVERRIDE": calls})
        finally:
            os.unlink(calls)
    events = []
    try:
        with open(log) as fh:
            for line in fh:
                row = json.loads(line)
                if row.get("session") == session and row.get("event") == "jev_receipt_unexplained":
                    events.append(row)
    except OSError:
        pass
    ok = blocked is False and len(events) == 1
    print(f"{'PASS' if ok else 'FAIL'}  backstop end-to-end: an indirect forge the mention check "
          f"misses passes the verdict but records jev_receipt_unexplained (events={len(events)})")
    return ok


def main():
    outcomes = [
        lib_reads_real_shape(),
        lib_reads_nested_message_delivery_shape(),
        lib_old_stdout_shape_no_longer_matches_anything_by_accident(),
        lib_current_turn_only_ignores_stale_advisory(),
        lib_current_turn_only_picks_latest_not_earliest(),
        lib_facets_called_this_turn_binds_by_session_and_time(),
        lib_facets_called_this_turn_per_facet_attribution(),
        lib_bypass_bare_call_no_facets_covers_nothing(),
        lib_refused_facets_ignores_fenced_and_quoted_examples(),
        lib_refused_facets_requires_real_reason(),
        lib_not_applicable_needs_twelve_chars(),
        lib_facet_name_drop_needs_jev_intent_nearby(),
        lib_semantic_creation_scoped_to_files_written_this_turn(),
        lib_current_turn_slice_and_boundary_ts(),
        lib_reopen_does_not_start_a_new_turn(),
        lib_reopen_does_not_start_a_new_turn_even_with_own_promptid(),
        lib_new_genuine_prompt_after_a_reopen_does_start_a_new_turn(),
        lib_is_synthetic_continuation_covers_task_notifications_and_reminders(),
        lib_refused_facets_handles_each_batched_form(),
        lib_blockquoted_refusal_line_does_not_count(),
        evaluate_required_actions_shapes(),
        known_bad_required_with_no_evidence_reopens(),
        known_good_real_jev_call_receipt_passes(),
        bypass_bash_echo_or_grep_no_longer_satisfies(),
        known_good_named_refusal_passes(),
        unavailable_advisory_fails_open(),
        no_advisory_at_all_fails_open(),
        stale_earlier_turn_advisory_does_not_satisfy_current_turn(),
        latch_is_per_turn_not_per_session(),
        latch_does_not_reopen_twice_for_the_same_turn(),
        post_reopen_turn_still_enforced_end_to_end(),
        post_reopen_turn_with_refusal_after_the_reopen_passes(),
        replay_notification_with_empty_advisory_cannot_erase_required(),
        replay_notification_with_empty_advisory_reopens_end_to_end(),
        replay_ninety_minute_turn_call_counts(),
        replay_cross_session_message_folds_into_turn(),
        replay_refusal_before_notification_still_counts_end_to_end(),
        refusal_reason_floor_and_stoplist(),
        forge_detection_names_the_ledger(),
        forge_detection_event_recorded_end_to_end(),
        refusal_then_notification_facet_does_not_reopen_end_to_end(),
        receipt_provenance_backstop(),
        receipt_unexplained_event_recorded_end_to_end(),
    ]
    print(f"jev-required-actions-selftest: {sum(outcomes)}/{len(outcomes)} passed")
    return 0 if all(outcomes) else 1


if __name__ == "__main__":
    raise SystemExit(main())
