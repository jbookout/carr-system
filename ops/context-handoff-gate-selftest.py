#!/usr/bin/env python3
"""Executable acceptance for session_context_lifecycle_gate_v1."""

from __future__ import annotations

import datetime as dt
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HOOK = REPO / "hooks/context-handoff-gate.py"
RUNNER = REPO / "hooks/hook-meter-run.py"
MANIFEST = REPO / "ops/config/session-context-lifecycle.v1.json"
INSTALLED_REPO = Path("/Users/booko/carr-system")
PASS: list[str] = []
FAIL: list[str] = []


def check(name, condition, detail=""):
    (PASS if condition else FAIL).append(name)
    if not condition:
        print(f"  FAIL {name}: {detail}")


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def write_jsonl(path: Path, rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows),
                    encoding="utf-8")
    return path


def usage_row(total, *, model=None, window=None):
    usage = {
        "input_tokens": 2,
        "cache_creation_input_tokens": 98,
        "cache_read_input_tokens": max(0, total - 200),
        "output_tokens": 100,
    }
    row = {"type": "assistant", "message": {"usage": usage}}
    if model:
        row["message"]["model"] = model
    if window is not None:
        row["model_context_window"] = window
    return row


def base_env(root: Path, **extra):
    env = dict(os.environ)
    env.update({
        "CARR_SESSION_CONTEXT_STATE_DIR": str(root / "state"),
        "CARR_SESSION_CONTEXT_MANIFEST": str(root / "manifest.json"),
        "CARR_CONTEXT_AUDIT": "off",
        "CARR_HOOK_TELEMETRY": str(root / "telemetry.jsonl"),
        "CARR_HOOK_FIXTURE": "1",
    })
    env.pop("CARR_CONTEXT_WINDOW", None)
    env.update({key: str(value) for key, value in extra.items()})
    return env


def run_hook(root: Path, event: str, transcript: Path | None, *,
             session="selftest", payload_extra=None, env_extra=None, wrapped=False):
    payload = {
        "hook_event_name": event,
        "session_id": session,
        "prompt_id": f"prompt-{session}",
        "tool_use_id": f"tool-{session}",
    }
    if transcript:
        payload["transcript_path"] = str(transcript)
    if payload_extra:
        payload.update(payload_extra)
    env = base_env(root, **(env_extra or {}))
    command = [sys.executable, str(HOOK)]
    if wrapped:
        command = [sys.executable, str(RUNNER), str(HOOK)]
    proc = subprocess.run(command, input=json.dumps(payload), text=True,
                          capture_output=True, env=env, cwd=REPO)
    parsed = None
    if proc.stdout.strip():
        try:
            parsed = json.loads(proc.stdout)
        except Exception:
            parsed = {"unparseable": proc.stdout}
    return proc, parsed


def run_cli(root: Path, *args):
    proc = subprocess.run([sys.executable, str(HOOK), *map(str, args)],
                          text=True, capture_output=True, env=base_env(root), cwd=REPO)
    parsed = None
    if proc.stdout.strip():
        try:
            parsed = json.loads(proc.stdout)
        except Exception:
            parsed = {"unparseable": proc.stdout}
    return proc, parsed


def reason(parsed):
    if not isinstance(parsed, dict) or parsed.get("decision") != "block":
        return None
    try:
        return json.loads(parsed["reason"])
    except Exception:
        return None


def fresh_root(prefix="context-lifecycle-"):
    root = Path(tempfile.mkdtemp(prefix=prefix))
    shutil.copy2(MANIFEST, root / "manifest.json")
    return root


def threshold_and_window_cases():
    print("thresholds, density, window tiers")
    root = fresh_root()
    try:
        t = write_jsonl(root / "normal-low.jsonl",
                        [usage_row(549_999, model="claude-opus-4-1")])
        proc, out = run_hook(root, "Stop", t, session="normal-low")
        check("normal 54.9999 percent allows", proc.returncode == 0 and out is None,
              (proc.returncode, out))

        t = write_jsonl(root / "normal-edge.jsonl",
                        [usage_row(550_000, model="claude-opus-4-1")])
        proc, out = run_hook(root, "Stop", t, session="normal-edge")
        why = reason(out)
        check("normal 55 percent refuses", why and why["signal"]["threshold"] == 55, out)

        t = write_jsonl(root / "dense-low.jsonl",
                        [usage_row(499_999, model="claude-opus-4-1")])
        proc, out = run_hook(root, "Stop", t, session="dense-low",
                             payload_extra={"risk": "R3"})
        check("dense below 50 percent allows", out is None, out)
        t = write_jsonl(root / "dense-edge.jsonl",
                        [usage_row(500_000, model="claude-opus-4-1")])
        proc, out = run_hook(root, "Stop", t, session="dense-edge",
                             payload_extra={"risk": "R3"})
        why = reason(out)
        check("R3 makes 50 percent the soft threshold",
              why and why["signal"]["dense"] is True
              and why["signal"]["threshold"] == 50, out)

        t = write_jsonl(root / "hard.jsonl",
                        [usage_row(700_000, model="claude-opus-4-1")])
        proc, out = run_hook(root, "Stop", t, session="hard")
        check("70 percent hard line refuses",
              reason(out) and reason(out)["signal"]["threshold"] == 70, out)

        # Operator/test override wins; a lower conflicting transcript tier is
        # deliberately not compared to the chosen higher tier.
        t = write_jsonl(root / "override.jsonl",
                        [usage_row(300_000, model="claude-opus-4-1", window=200_000)])
        proc, out = run_hook(root, "Stop", t, session="override",
                             env_extra={"CARR_CONTEXT_WINDOW": "500000"})
        why = reason(out)
        check("positive override is highest tier",
              why and why["signal"]["window"] == 500_000
              and why["signal"]["window_tier"] == "override", out)
        proc, out = run_hook(root, "Stop", t, session="bad-override",
                             env_extra={"CARR_CONTEXT_WINDOW": "not-positive"})
        check("invalid override is fail-open before fallback cap",
              proc.returncode == 0 and out is None, out)

        proc, out = run_hook(
            root, "Stop", t, session="missing-manifest",
            env_extra={"CARR_SESSION_CONTEXT_MANIFEST": root / "missing.json"})
        why = reason(out)
        check("Stop fails closed when the manifest is unavailable",
              why and why["reason"] == "WINDOW_CONFIG_INVALID", out)

        corrupt_state = root / "corrupt-state.json"
        corrupt_state.write_text("{broken", encoding="utf-8")
        proc, out = run_hook(
            root, "Stop", t, session="corrupt-state",
            env_extra={"CARR_CONTEXT_STATE": corrupt_state})
        why = reason(out)
        check("Stop fails closed when lifecycle state is corrupt",
              why and why["reason"] == "LIFECYCLE_INVALID", out)

        # Exact known model binding wins over lower explicit windows.
        t = write_jsonl(root / "model.jsonl",
                        [usage_row(600_000, model="claude-opus-4-1",
                                   window=2_000_000)])
        proc, out = run_hook(root, "Stop", t, session="model")
        why = reason(out)
        check("exact model binding is the second tier",
              why and why["signal"]["window"] == 1_000_000
              and why["signal"]["window_tier"] == "model", out)

        # Unknown model is not a default; a usable lower tier may still resolve.
        t = write_jsonl(root / "unknown-explicit.jsonl",
                        [usage_row(110_000, model="claude-unknown", window=200_000)])
        proc, out = run_hook(root, "Stop", t, session="unknown-explicit")
        why = reason(out)
        check("unknown model falls through to explicit window",
              why and why["signal"]["window_tier"] == "transcript", out)

        t = write_jsonl(root / "equal.jsonl",
                        [usage_row(110_000, window=200_000),
                         {"model_context_window": 200_000}])
        proc, out = run_hook(root, "Stop", t, session="equal")
        check("equal positive explicit repeats are accepted",
              reason(out) and reason(out)["signal"]["window"] == 200_000, out)

        t = write_jsonl(root / "unequal.jsonl",
                        [usage_row(150_000, window=200_000),
                         {"context_window": 300_000}])
        proc, out = run_hook(root, "Stop", t, session="unequal")
        check("unequal explicit windows are ambiguous and initially allow",
              proc.returncode == 0 and out is None, out)

        t = write_jsonl(root / "invalid.jsonl",
                        [usage_row(150_000), {"context_window": 0}])
        proc, out = run_hook(root, "Stop", t, session="invalid")
        check("all-invalid explicit windows initially allow", out is None, out)

        # compact_boundary.preTokens is both conservative window fallback and
        # usage high-water; minimum positive boundary wins.
        t = write_jsonl(root / "compact.jsonl", [
            usage_row(38_496),
            {"payload": {"type": "compact_boundary", "preTokens": 1_013_084}},
            {"payload": {"type": "compact_boundary", "preTokens": 1_100_000}},
        ])
        proc, out = run_hook(root, "Stop", t, session="compact")
        why = reason(out)
        check("minimum compact preTokens is final window tier",
              why and why["signal"]["window"] == 1_013_084
              and why["signal"]["used"] == 1_100_000, out)

        # Density from the other three independent predicates.
        for label, payloads in (
            ("tool-call density", [{}] * 20),
            ("mutated-path density",
             [{"tool_name": "Write", "tool_input": {"file_path": f"/tmp/p{i}"}} for i in range(8)]),
            ("worker-start density", [{"tool_name": "Agent"} for _ in range(3)]),
        ):
            session = label.replace(" ", "-")
            transcript = write_jsonl(root / f"{session}.jsonl",
                                     [usage_row(500_000, model="claude-opus-4-1")])
            for extra in payloads:
                run_hook(root, "PostToolUse", transcript, session=session,
                         payload_extra=extra)
            proc, out = run_hook(root, "Stop", transcript, session=session)
            check(label + " selects dense threshold",
                  reason(out) and reason(out)["signal"]["dense"] is True, out)
    finally:
        shutil.rmtree(root)


def fallback_and_hook_sequence():
    print("fallback and Claude event sequence")
    root = fresh_root()
    try:
        # Fresh unavailable signal announces once but does not block.
        proc, first = run_hook(root, "PostToolUse", None, session="fallback")
        proc, second = run_hook(root, "PostToolUse", None, session="fallback")
        check("fresh unavailable signal notices once",
              first is not None and second is None, (first, second))
        proc, out = run_hook(root, "Stop", None, session="fallback")
        check("unavailable below fallback cap allows first Stop", out is None, out)
        for _ in range(22):
            run_hook(root, "PostToolUse", None, session="fallback")
        proc, out = run_hook(root, "Stop", None, session="fallback")
        why = reason(out)
        check("fallback ANY invocation cap refuses",
              why and why["signal"]["fallback_level"] in {
                  "dense_soft", "normal_soft", "hard"}, out)
        check("fallback refusal preserves signal reason",
              why and why["signal"]["signal_reason"] == "CONTEXT_SIGNAL_UNAVAILABLE", why)

        transcript = write_jsonl(root / "sequence.jsonl",
                                 [usage_row(600_000, model="claude-opus-4-1")])
        proc, post = run_hook(root, "PostToolUse", transcript, session="sequence")
        proc, pre = run_hook(root, "PreCompact", transcript, session="sequence")
        proc, stop = run_hook(root, "Stop", transcript, session="sequence")
        check("PostToolUse emits one additionalContext notice",
              isinstance(post, dict)
              and "additionalContext" in post.get("hookSpecificOutput", {}), post)
        check("PreCompact always allows silently", pre is None, pre)
        check("Stop is the sole refusal seam", reason(stop) is not None, stop)

        # Complete the same task's handoff through immutable offer/declaration/
        # receipt/final objects, then prove recursive Stop allows.
        _, status = run_cli(root, "status", "--task-key", "claude:sequence")
        version = status["version"]
        pred_evidence = json.dumps({
            "session_id": "sequence", "transcript_path": str(transcript),
            "controller_callback_id": "callback-1", "status": "active",
        })
        succ_evidence = json.dumps({
            "thread_id": "successor", "project_id": "p", "cwd": str(REPO),
            "status": "active", "pinnedIndex": 1, "event_id": "event-2",
        })
        proc, offer = run_cli(
            root, "handoff-offer-create", "--task-key", "claude:sequence",
            "--predecessor", "sequence", "--predecessor-surface", "claude",
            "--successor", "successor", "--successor-surface", "codex",
            "--evidence-json", pred_evidence, "--generation", "1",
            "--expected-version", str(version),
        )
        proc, declared = run_cli(
            root, "successor-declare", "--task-key", "claude:sequence",
            "--offer-digest", offer["offer_digest"], "--successor", "successor",
            "--evidence-json", succ_evidence,
            "--expected-version", str(offer["state"]["version"]),
        )
        proc, accepted = run_cli(
            root, "successor-accept", "--task-key", "claude:sequence",
            "--offer-digest", offer["offer_digest"], "--successor", "successor",
            "--evidence-json", succ_evidence,
            "--expected-version", str(declared["state"]["version"]),
        )
        task_hash = hashlib.sha256(b"claude:sequence").hexdigest()
        receipt_path = (root / "state/objects" / task_hash / "receipt"
                        / f"{accepted['receipt_digest']}.json")
        final_path = (root / "state/objects" / task_hash / "final"
                      / f"{accepted['final_digest']}.json")
        proc, verified = run_cli(root, "verify-handoff",
                                 "--task-key", "claude:sequence")
        check("offer receipt and final verify as one linked packet",
              proc.returncode == 0
              and verified["receipt_digest"] == accepted["receipt_digest"],
              verified)
        original_receipt = receipt_path.read_text()
        receipt_path.write_text(original_receipt.replace(
            '"successor":"successor"', '"successor":"tampered"', 1))
        proc, tampered = run_cli(root, "verify-handoff",
                                 "--task-key", "claude:sequence")
        check("receipt tamper is refused",
              proc.returncode == 2
              and tampered["reason"] == "HANDOFF_RECEIPT_INVALID", tampered)
        receipt_path.write_text(original_receipt)
        original_final = final_path.read_text()
        final_path.write_text(original_final.replace(
            '"successor":"successor"', '"successor":"tampered"', 1))
        proc, tampered = run_cli(root, "verify-handoff",
                                 "--task-key", "claude:sequence")
        check("final packet tamper is refused",
              proc.returncode == 2
              and tampered["reason"] == "HANDOFF_RECEIPT_INVALID", tampered)
        final_path.write_text(original_final)
        low_transcript = write_jsonl(
            root / "sequence-low.jsonl",
            [usage_row(1_000, model="claude-opus-4-1")],
        )
        receipt_path.write_text(original_receipt.replace(
            '"successor":"successor"', '"successor":"tampered"', 1))
        proc, tampered_stop = run_hook(
            root, "Stop", low_transcript, session="sequence")
        why = reason(tampered_stop)
        check("tampered verified handoff refuses low-signal predecessor Stop",
              proc.returncode == 0
              and why and why["reason"] == "HANDOFF_RECEIPT_INVALID",
              (proc.returncode, tampered_stop, proc.stderr))
        receipt_path.write_text(original_receipt)
        proc, after = run_hook(root, "Stop", transcript, session="sequence")
        check("verified takeover makes recursive Stop allow", after is None, after)
        proc, successor_stop = run_hook(
            root, "Stop", transcript, session="successor",
            payload_extra={"task_key": "claude:sequence"})
        check("prior receipt cannot suppress the successor generation threshold",
              reason(successor_stop) is not None, successor_stop)
        check("offer declaration receipt and final use four distinct digests",
              len({offer["offer_digest"], declared["declaration_digest"],
                   accepted["receipt_digest"], accepted["final_digest"]}) == 4,
              accepted)

        malformed_transcript = write_jsonl(
            root / "malformed-state.jsonl",
            [usage_row(1_000, model="claude-opus-4-1")],
        )
        run_hook(root, "PostToolUse", malformed_transcript, session="malformed")
        malformed_key = hashlib.sha256(b"claude:malformed").hexdigest()
        malformed_path = root / "state" / f"{malformed_key}.json"
        malformed_state = json.loads(malformed_path.read_text())
        malformed_state["signal"]["generation_tool_calls"] = "not-an-int"
        malformed_path.write_text(json.dumps(malformed_state))
        proc, malformed_stop = run_hook(
            root, "Stop", malformed_transcript, session="malformed")
        why = reason(malformed_stop)
        check("semantically malformed lifecycle state refuses Stop",
              proc.returncode == 0 and why
              and why["reason"] == "LIFECYCLE_INVALID"
              and why["signal"]["window_tier"] == "control_error",
              (proc.returncode, malformed_stop, proc.stderr))
    finally:
        shutil.rmtree(root)


def codex_evidence(thread, *, status="active", pinned=1, event="e",
                   project="project", cwd=REPO):
    return json.dumps({
        "thread_id": thread, "project_id": project, "cwd": str(cwd),
        "status": status, "pinnedIndex": pinned, "event_id": event,
    })


def lifecycle_cas_and_tamper():
    print("immutable lifecycle, CAS, terminal irreversibility")
    root = fresh_root()
    try:
        proc, state = run_cli(root, "task-init", "--task-key", "task:cas",
                              "--owner", "old", "--surface", "codex",
                              "--evidence-json", codex_evidence("old"),
                              "--expected-version", "-1")
        check("task init creates version zero", proc.returncode == 0 and state["version"] == 0,
              (proc.returncode, state))
        proc, first = run_cli(
            root, "handoff-offer-create", "--task-key", "task:cas",
            "--predecessor", "old", "--predecessor-surface", "codex",
            "--successor", "new", "--successor-surface", "codex",
            "--evidence-json", codex_evidence("old"), "--generation", "1",
            "--expected-version", "0",
        )
        proc2, lost = run_cli(
            root, "handoff-offer-create", "--task-key", "task:cas",
            "--predecessor", "old", "--predecessor-surface", "codex",
            "--successor", "other", "--successor-surface", "codex",
            "--evidence-json", codex_evidence("old"), "--generation", "1",
            "--expected-version", "0",
        )
        check("expected-version CAS has one winner",
              proc.returncode == 0 and proc2.returncode == 2
              and lost["reason"] == "LIFECYCLE_INVALID", lost)

        # Tamper an offer object and prove the declaration validates bytes,
        # never merely the filename/digest reference.
        task_hash = hashlib.sha256(b"task:cas").hexdigest()
        offer_path = (root / "state/objects" / task_hash / "offer"
                      / f"{first['offer_digest']}.json")
        original = offer_path.read_text()
        offer_path.write_text(original.replace('"successor":"new"',
                                               '"successor":"tampered"'))
        proc, rejected = run_cli(
            root, "successor-declare", "--task-key", "task:cas",
            "--offer-digest", first["offer_digest"], "--successor", "new",
            "--evidence-json", codex_evidence("new"), "--expected-version", "1",
        )
        check("tampered immutable offer is refused",
              proc.returncode == 2
              and rejected["reason"] == "HANDOFF_RECEIPT_INVALID", rejected)

        # Surface and native identity are relationships, not caller labels.
        proc, bound = run_cli(root, "task-init", "--task-key", "task:binding",
                              "--owner", "bound-old", "--surface", "codex",
                              "--evidence-json", codex_evidence("bound-old"),
                              "--expected-version", "-1")
        claude_transcript = write_jsonl(root / "bound.jsonl", [usage_row(1)])
        wrong_surface = json.dumps({
            "session_id": "bound-old", "transcript_path": str(claude_transcript),
            "controller_callback_id": "callback", "status": "active",
        })
        proc, rejected = run_cli(
            root, "handoff-offer-create", "--task-key", "task:binding",
            "--predecessor", "bound-old", "--predecessor-surface", "claude",
            "--successor", "bound-new", "--successor-surface", "codex",
            "--evidence-json", wrong_surface, "--generation", "1",
            "--expected-version", str(bound["version"]),
        )
        check("offer surface must match the recorded owner",
              proc.returncode == 2 and rejected["reason"] == "OWNERSHIP_MISMATCH",
              rejected)

        proc, offer = run_cli(
            root, "handoff-offer-create", "--task-key", "task:binding",
            "--predecessor", "bound-old", "--predecessor-surface", "codex",
            "--successor", "bound-new", "--successor-surface", "codex",
            "--evidence-json", codex_evidence("bound-old"), "--generation", "1",
            "--expected-version", str(bound["version"]),
        )
        proc, rejected = run_cli(
            root, "successor-declare", "--task-key", "task:binding",
            "--offer-digest", offer["offer_digest"], "--successor", "bound-new",
            "--evidence-json", codex_evidence("unrelated"),
            "--expected-version", str(offer["state"]["version"]),
        )
        check("native successor id must match the lifecycle successor",
              proc.returncode == 2
              and rejected["reason"] == "SUCCESSOR_SURFACE_INVALID", rejected)
        proc, rejected = run_cli(
            root, "successor-declare", "--task-key", "task:binding",
            "--offer-digest", offer["offer_digest"], "--successor", "bound-new",
            "--evidence-json", codex_evidence("bound-new", project="other-project"),
            "--expected-version", str(offer["state"]["version"]),
        )
        check("Codex successor must stay in the offered project and cwd",
              proc.returncode == 2
              and rejected["reason"] == "OWNERSHIP_MISMATCH", rejected)
        proc, rejected = run_cli(
            root, "successor-declare", "--task-key", "task:binding",
            "--offer-digest", offer["offer_digest"], "--successor", "bound-new",
            "--evidence-json", codex_evidence("bound-new", cwd=root),
            "--expected-version", str(offer["state"]["version"]),
        )
        check("Codex successor must stay in the offered cwd",
              proc.returncode == 2
              and rejected["reason"] == "OWNERSHIP_MISMATCH", rejected)
        proc, declared = run_cli(
            root, "successor-declare", "--task-key", "task:binding",
            "--offer-digest", offer["offer_digest"], "--successor", "bound-new",
            "--evidence-json", codex_evidence("bound-new", event="declare"),
            "--expected-version", str(offer["state"]["version"]),
        )
        proc, rejected = run_cli(
            root, "successor-accept", "--task-key", "task:binding",
            "--offer-digest", offer["offer_digest"], "--successor", "bound-new",
            "--evidence-json", codex_evidence("bound-new", event="accept-different"),
            "--expected-version", str(declared["state"]["version"]),
        )
        check("acceptance evidence must equal the declaration evidence",
              proc.returncode == 2
              and rejected["reason"] == "TAKEOVER_NOT_VERIFIED", rejected)

        # Independent task terminal is evidence-bound and irreversible.
        proc, state = run_cli(root, "task-init", "--task-key", "task:terminal",
                              "--owner", "only", "--surface", "codex",
                              "--evidence-json", codex_evidence("only"),
                              "--expected-version", "-1")
        proc, terminal = run_cli(
            root, "task-terminal", "--task-key", "task:terminal",
            "--owner", "only",
            "--evidence-json", codex_evidence("only", status="terminal"),
            "--expected-version", "0",
        )
        check("task terminal requires native terminal evidence",
              proc.returncode == 0
              and terminal["task_status"] == "TERMINAL"
              and terminal["active_owner"] is None, terminal)
        terminal_version = terminal["version"]
        run_hook(root, "Stop", None, session="intruder",
                 payload_extra={"task_key": "task:terminal"})
        _, unchanged_terminal = run_cli(
            root, "status", "--task-key", "task:terminal")
        check("terminal observations are read-only",
              unchanged_terminal["version"] == terminal_version,
              unchanged_terminal)
        proc, resurrect = run_cli(
            root, "handoff-offer-create", "--task-key", "task:terminal",
            "--predecessor", "only", "--predecessor-surface", "codex",
            "--successor", "again", "--successor-surface", "codex",
            "--evidence-json", codex_evidence("only"), "--generation", "1",
            "--expected-version", "1",
        )
        check("terminal task cannot recover or resurrect",
              proc.returncode == 2
              and resurrect["reason"] == "LIFECYCLE_INVALID", resurrect)
    finally:
        shutil.rmtree(root)


def rollout_resolver_cases():
    print("Codex rollout task resolver")
    root = fresh_root()
    try:
        fixtures = {
            "root": [
                {"type": "session_meta",
                 "payload": {"id": "root", "session_id": "root"}},
            ],
            "subagent": [
                {"type": "session_meta",
                 "payload": {"id": "child", "session_id": "root"}},
            ],
            "legacy": [
                {"type": "session_meta", "payload": {"session_id": "legacy"}},
            ],
            "turn": [
                {"type": "event_msg",
                 "payload": {"type": "task_started", "turn_id": "turn-1"}},
            ],
            "ambiguous": [
                {"type": "session_meta", "payload": {"id": "one"}},
                {"type": "session_meta", "payload": {"id": "two"}},
            ],
            "archived": [
                {"type": "session_meta",
                 "payload": {"id": "archived", "status": "archived"}},
            ],
        }
        expected = {
            "root": ("codex:root", "payload.id"),
            "subagent": ("codex:child", "payload.id"),
            "legacy": ("codex:legacy", "session_id"),
            "turn": ("codex-turn:turn-1", "task_started.turn_id"),
            "archived": ("codex:archived", "payload.id"),
        }
        for name, rows in fixtures.items():
            path = write_jsonl(root / f"{name}.jsonl", rows)
            proc, out = run_cli(root, "codex-task-key", "--rollout", str(path))
            if name == "ambiguous":
                check("multiple authoritative IDs are ambiguous",
                      out.get("ok") is False
                      and out["reason"] == "CONTEXT_SIGNAL_AMBIGUOUS", out)
            else:
                check(f"{name} fixture resolves through authoritative tier",
                      (out.get("task_key"), out.get("resolver")) == expected[name], out)
        path = root / "subagent.jsonl"
        _, out = run_cli(root, "codex-task-key", "--rollout", str(path))
        check("unequal session_id lineage is ignored",
              out.get("ignored_lineage") == ["root"], out)
        _, out = run_cli(root, "task-key", "--surface", "claude",
                         "--session-id", "claude-root")
        check("generic task-key resolver is read-only and stable",
              out.get("task_key") == "claude:claude-root"
              and out.get("resolver") == "session_id", out)
    finally:
        shutil.rmtree(root)


def snapshot(task_key, ownership_digest, state, *, source="event-1",
             observed=None, attention="NONE", recoverable=False,
             capacity=None, worker_id="owner", last_progress=None,
             generation=0):
    observed = observed or dt.datetime.now(dt.timezone.utc).replace(
        microsecond=0).isoformat().replace("+00:00", "Z")
    last_progress = last_progress or observed
    worker = {
        "id": worker_id, "surface": "codex", "generation": generation,
        "normalized_state": state, "last_progress_at": last_progress,
        "task_terminal": state == "TERMINAL", "attention_kind": attention,
        "error_code": "ERR" if state == "ERROR" else None,
        "recoverable": recoverable, "capacity_code": capacity,
        "ownership_digest": ownership_digest,
    }
    value = {
        "task_key": task_key, "observed_at": observed,
        "source_event_id": source, "observer_id": "dispatcher",
        "source_surface": "codex", "evidence_ref": "native:event-1",
        "workers": [worker],
    }
    value["evidence_digest"] = digest(value)
    return value


def dispatcher_cases():
    print("dispatcher schema, predicates, nonce, replay")
    root = fresh_root()
    try:
        _, state = run_cli(root, "task-init", "--task-key", "task:dispatch",
                           "--owner", "owner", "--surface", "codex",
                           "--evidence-json", codex_evidence("owner"),
                           "--expected-version", "-1")
        own = state["owners"]["owner"]["ownership_digest"]

        for label, worker_state, attention, recoverable, capacity, action, why in (
            ("running", "RUNNING", "NONE", False, None, "NOOP", "RECOVERY_ACTIVE"),
            ("waiting authority", "WAITING_ATTENTION", "AUTHORITY", False, None,
             "NOOP", "RECOVERY_WAITING"),
            ("capacity", "CAPACITY_EXHAUSTED", "NONE", False,
             "CONTEXT_EXHAUSTED", "RECOVER_SAME_TASK", "RECOVERY_CAPACITY"),
            ("recoverable error", "ERROR", "NONE", True, None,
             "RECOVER_SAME_TASK", "RECOVERY_ERROR"),
            ("idle", "IDLE", "NONE", False, None,
             "RECOVER_SAME_TASK", "RECOVERY_IDLE"),
            ("terminal", "TERMINAL", "NONE", False, None,
             "NOOP", "RECOVERY_TERMINAL"),
        ):
            snap = snapshot("task:dispatch", own, worker_state,
                            attention=attention, recoverable=recoverable,
                            capacity=capacity, source=f"event-{label}")
            proc, out = run_cli(root, "dispatcher-evaluate",
                                "--task-key", "task:dispatch",
                                "--snapshot-json", json.dumps(snap))
            check(label + " predicate is exact",
                  proc.returncode == 0 and out["action"] == action
                  and out["reason"] == why, out)

        stale = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=121)
                 ).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        snap = snapshot("task:dispatch", own, "RUNNING", observed=stale)
        proc, out = run_cli(root, "dispatcher-evaluate",
                            "--task-key", "task:dispatch",
                            "--snapshot-json", json.dumps(snap))
        check("snapshot older than 120 seconds is refused",
              proc.returncode == 2
              and out["reason"] == "RECOVERY_SNAPSHOT_STALE", out)

        future = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=30)
                  ).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        snap = snapshot("task:dispatch", own, "RUNNING", observed=future)
        proc, out = run_cli(root, "dispatcher-evaluate",
                            "--task-key", "task:dispatch",
                            "--snapshot-json", json.dumps(snap))
        check("future snapshot is invalid", proc.returncode == 2
              and out["reason"] == "RECOVERY_SNAPSHOT_INVALID", out)

        stale_progress = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=61)
                          ).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        snap = snapshot("task:dispatch", own, "RUNNING",
                        last_progress=stale_progress, source="event-stale-heartbeat")
        proc, out = run_cli(root, "dispatcher-evaluate",
                            "--task-key", "task:dispatch",
                            "--snapshot-json", json.dumps(snap))
        check("stale RUNNING heartbeat cannot claim recovery active",
              proc.returncode == 0 and out["action"] == "RECOVER_SAME_TASK"
              and out["reason"] == "RECOVERY_IDLE", out)

        snap = snapshot("task:dispatch", own, "CAPACITY_EXHAUSTED",
                        capacity="CONTEXT_EXHAUSTED", source="event-apply")
        proc, preview = run_cli(root, "dispatcher-evaluate",
                                "--task-key", "task:dispatch",
                                "--snapshot-json", json.dumps(snap))
        check("dispatcher evaluation is read-only without apply",
              proc.returncode == 0 and preview["applied"] is False
              and preview["nonce"], preview)
        _, unchanged = run_cli(root, "status", "--task-key", "task:dispatch")
        check("read-only dispatcher leaves version unchanged",
              unchanged["version"] == 0, unchanged["version"])
        proc, missing_cas = run_cli(root, "dispatcher-evaluate",
                                    "--task-key", "task:dispatch",
                                    "--snapshot-json", json.dumps(snap),
                                    "--apply")
        check("new dispatcher apply requires expected-version CAS",
              proc.returncode == 2
              and missing_cas["reason"] == "LIFECYCLE_INVALID", missing_cas)
        proc, applied = run_cli(root, "dispatcher-evaluate",
                                "--task-key", "task:dispatch",
                                "--snapshot-json", json.dumps(snap),
                                "--apply", "--expected-version", "0")
        check("apply fences old owner and records one intent",
              proc.returncode == 0 and applied["state"]["owners"]["owner"]["state"] == "DRAINING"
              and applied["state"]["recovery_intent"]["nonce"] == preview["nonce"], applied)
        proc, replay = run_cli(root, "dispatcher-evaluate",
                               "--task-key", "task:dispatch",
                               "--snapshot-json", json.dumps(snap),
                               "--apply", "--expected-version", "1")
        check("exact recovery replay returns same nonce",
              proc.returncode == 0 and replay["replay"] is True
              and replay["nonce"] == preview["nonce"]
              and replay["state"]["version"] == 1, replay)
        proc, missing_replay_cas = run_cli(
            root, "dispatcher-evaluate", "--task-key", "task:dispatch",
            "--snapshot-json", json.dumps(snap), "--apply")
        check("applied recovery replay still requires expected-version CAS",
              proc.returncode == 2
              and missing_replay_cas["reason"] == "LIFECYCLE_INVALID",
              missing_replay_cas)
        changed = snapshot("task:dispatch", own, "RUNNING", source="event-apply")
        proc, changed_replay = run_cli(
            root, "dispatcher-evaluate", "--task-key", "task:dispatch",
            "--snapshot-json", json.dumps(changed), "--apply",
            "--expected-version", "1")
        check("recovery replay is bound to the exact snapshot digest",
              proc.returncode == 2
              and changed_replay["reason"] == "LIFECYCLE_INVALID",
              changed_replay)
        malformed = {**snap, "unexpected": True}
        proc, refused = run_cli(root, "dispatcher-evaluate",
                                "--task-key", "task:dispatch",
                                "--snapshot-json", json.dumps(malformed),
                                "--apply", "--expected-version", "1")
        check("recovery replay still validates exact snapshot schema",
              proc.returncode == 2
              and refused["reason"] == "RECOVERY_SNAPSHOT_INVALID", refused)
        other = snapshot("task:dispatch", own, "IDLE", source="event-other")
        proc, refused = run_cli(root, "dispatcher-evaluate",
                                "--task-key", "task:dispatch",
                                "--snapshot-json", json.dumps(other),
                                "--apply", "--expected-version", "1")
        check("second recovery while one is pending is refused",
              proc.returncode == 2
              and refused["reason"] == "LIFECYCLE_INVALID", refused)

        proc, aborted = run_cli(
            root, "recovery-abort", "--task-key", "task:dispatch",
            "--owner", "owner", "--nonce", preview["nonce"],
            "--expected-version", "1")
        check("recovery abort restores the sole owner",
              proc.returncode == 0
              and aborted["owners"]["owner"]["state"] == "ACTIVE"
              and aborted["active_owner"] == "owner"
              and aborted["recovery_intent"]["state"] == "ABORTED", aborted)

        # A separate applied recovery completes through the ordinary, receipt-
        # verified handoff path and leaves one active successor.
        _, recovery = run_cli(
            root, "task-init", "--task-key", "task:recovery-complete",
            "--owner", "failed", "--surface", "codex",
            "--evidence-json", codex_evidence("failed"),
            "--expected-version", "-1")
        recovery_snap = snapshot(
            "task:recovery-complete",
            recovery["owners"]["failed"]["ownership_digest"],
            "CAPACITY_EXHAUSTED", source="event-recovery-complete",
            capacity="CONTEXT_EXHAUSTED", worker_id="failed")
        _, applied_recovery = run_cli(
            root, "dispatcher-evaluate", "--task-key", "task:recovery-complete",
            "--snapshot-json", json.dumps(recovery_snap), "--apply",
            "--expected-version", "0")
        _, recovery_offer = run_cli(
            root, "handoff-offer-create", "--task-key", "task:recovery-complete",
            "--predecessor", "failed", "--predecessor-surface", "codex",
            "--successor", "replacement", "--successor-surface", "codex",
            "--evidence-json", codex_evidence("failed"), "--generation", "1",
            "--expected-version", str(applied_recovery["state"]["version"]))
        _, recovery_declared = run_cli(
            root, "successor-declare", "--task-key", "task:recovery-complete",
            "--offer-digest", recovery_offer["offer_digest"],
            "--successor", "replacement",
            "--evidence-json", codex_evidence("replacement"),
            "--expected-version", str(recovery_offer["state"]["version"]))
        _, recovery_accepted = run_cli(
            root, "successor-accept", "--task-key", "task:recovery-complete",
            "--offer-digest", recovery_offer["offer_digest"],
            "--successor", "replacement",
            "--evidence-json", codex_evidence("replacement"),
            "--expected-version", str(recovery_declared["state"]["version"]))
        proc, recovery_terminal = run_cli(
            root, "predecessor-terminal", "--task-key", "task:recovery-complete",
            "--predecessor", "failed",
            "--evidence-json", codex_evidence("failed", status="terminal"),
            "--expected-version", str(recovery_accepted["state"]["version"]))
        active = [owner for owner in recovery_terminal["owners"].values()
                  if owner["state"] == "ACTIVE"]
        check("applied recovery completes with one verified active successor",
              proc.returncode == 0 and len(active) == 1
              and active[0]["id"] == "replacement"
              and recovery_terminal["recovery_intent"]["state"] == "COMPLETED",
              recovery_terminal)
    finally:
        shutil.rmtree(root)


def wrapper_and_historical_defect():
    print("wrapper equivalence and historical compaction defect")
    root = fresh_root()
    try:
        transcript = write_jsonl(root / "wrapped.jsonl",
                                 [usage_row(600_000, model="claude-opus-4-1")])
        bare_root = root / "bare"; bare_root.mkdir()
        wrapped_root = root / "wrapped"; wrapped_root.mkdir()
        shutil.copy2(MANIFEST, bare_root / "manifest.json")
        shutil.copy2(MANIFEST, wrapped_root / "manifest.json")
        bare, bare_out = run_hook(bare_root, "Stop", transcript,
                                  session="equivalent", wrapped=False)
        wrapped, wrapped_out = run_hook(wrapped_root, "Stop", transcript,
                                        session="equivalent", wrapped=True)
        check("bare and wrapped candidate return identical exit",
              bare.returncode == wrapped.returncode, (bare.returncode, wrapped.returncode))
        check("bare and wrapped stdout/stderr are byte-identical",
              bare.stdout == wrapped.stdout and bare.stderr == wrapped.stderr,
              (bare.stdout, wrapped.stdout, bare.stderr, wrapped.stderr))

        # Historical ordering: usage reached 1,013,084, compacted to 38,496,
        # then Stop ran. The installed gate reads only the last assistant usage
        # and silently allows; the candidate preserves compact preTokens.
        historical = write_jsonl(root / "historical.jsonl", [
            usage_row(990_267),
            {"payload": {"type": "compact_boundary", "preTokens": 1_013_084,
                         "postTokens": 38_496,
                         "timestamp": "2026-08-30T13:15:30.668Z"}},
            usage_row(38_496),
        ])
        payload = json.dumps({"hook_event_name": "Stop", "session_id": "selftest",
                              "transcript_path": str(historical)})
        installed_hook = INSTALLED_REPO / "hooks/context-handoff-gate.py"
        installed_runner = INSTALLED_REPO / "hooks/hook-meter-run.py"
        if installed_hook.exists() and installed_runner.exists():
            env = base_env(root, CARR_CONTEXT_WINDOW="1000000",
                           CARR_CONTEXT_STATE=str(root / "installed-state.json"))
            proc = subprocess.run(
                ["/usr/bin/env", "python3", str(installed_runner), str(installed_hook)],
                input=payload, text=True, capture_output=True, env=env,
                cwd=INSTALLED_REPO)
            check("installed exact command shape reproduces silent postcompact allow",
                  proc.returncode == 0 and proc.stdout.strip() == "", proc.stdout)
        candidate, out = run_hook(
            root, "Stop", historical, session="historical", wrapped=True,
            env_extra={"CARR_CONTEXT_WINDOW": "1000000"})
        why = reason(out)
        check("candidate exact wrapped shape catches historical preTokens",
              why and why["signal"]["used"] == 1_013_084, out)
    finally:
        shutil.rmtree(root)


def static_contract_cases():
    print("protected contract and explicit exclusions")
    hooks = json.loads((REPO / "ops/config/hooks.json").read_text())
    stop = json.dumps(hooks.get("Stop", []))
    post = json.dumps(hooks.get("PostToolUse", []))
    compact = json.dumps(hooks.get("PreCompact", []))
    check("Claude wiring has context gate on PostToolUse",
          "context-handoff-gate.py" in post, post)
    check("Claude wiring has context gate on PreCompact",
          "context-handoff-gate.py" in compact, compact)
    check("Claude wiring keeps context gate on Stop",
          "context-handoff-gate.py" in stop, stop)
    manifest = json.loads(MANIFEST.read_text())
    check("manifest makes Codex bare-CLI limitation explicit",
          manifest["surface_contracts"]["codex"]["adapter"] == "bare_cli"
          and manifest["surface_contracts"]["codex"]["native_stop_hook"] is False
          and manifest["surface_contracts"]["codex"]["evidence_trust_boundary"]
          == "controller_capture",
          manifest["surface_contracts"]["codex"])
    check("heartbeat cadence is at most 60 seconds",
          manifest["dispatcher"]["heartbeat_max_seconds"] <= 60,
          manifest["dispatcher"]["heartbeat_max_seconds"])

    # These contracts were frozen unchanged by the reviewed plan.
    base = subprocess.check_output(
        ["git", "show", "01c3977580e8d9d490380f6c2135d1c4d7d20fd7:"
         "hooks/stop_latch.py"], cwd=REPO)
    check("stop_latch.py is byte-identical to approved base",
          base == (REPO / "hooks/stop_latch.py").read_bytes())
    for path in ("ops/stop_latch-selftest.py", "ops/config/codex-hooks.json",
                 "ops/config/rule-enforcement-map.json"):
        base = subprocess.check_output(
            ["git", "show",
             f"01c3977580e8d9d490380f6c2135d1c4d7d20fd7:{path}"], cwd=REPO)
        check(path + " is explicitly unchanged", base == (REPO / path).read_bytes())


def main():
    threshold_and_window_cases()
    fallback_and_hook_sequence()
    lifecycle_cas_and_tamper()
    rollout_resolver_cases()
    dispatcher_cases()
    wrapper_and_historical_defect()
    static_contract_cases()
    print()
    if FAIL:
        print(f"FAIL {len(FAIL)} check(s): {', '.join(FAIL)}")
        return 1
    print(f"OK {len(PASS)} checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
