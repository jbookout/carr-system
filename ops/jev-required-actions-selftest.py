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
    find_build_advisory, is_real_user_turn, latest_user_turn_index,
    load_jev_call_receipts, missing_facets, prompt_names_facet,
    prompt_names_not_applicable, refused_facets_in_texts, required_facets,
    semantic_creation_receipt_missing, turn_boundary_timestamp,
)

NOW = datetime.now(timezone.utc)


def ts(offset_seconds=0):
    return (NOW + timedelta(seconds=offset_seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")


def user(text, when=0):
    return {"type": "user", "timestamp": ts(when), "message": {"role": "user", "content": text}}


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
    ]
    print(f"jev-required-actions-selftest: {sum(outcomes)}/{len(outcomes)} passed")
    return 0 if all(outcomes) else 1


if __name__ == "__main__":
    raise SystemExit(main())
