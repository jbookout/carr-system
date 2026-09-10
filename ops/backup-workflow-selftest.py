#!/usr/bin/env python3
"""Source-contract fixtures for the cloud backup producer and observer.

Provider effects are deliberately outside this suite.  These checks pin the
observable workflow contract that later mocked-provider and live-provider
drills consume: one named Check, exact run identity, disabled cancellation that
cannot reach success, and a separate observer for the terminal provider state.
"""
from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "backup-nightly.yml"
STATUS_HELPER = ROOT / "ops" / "backup-workflow-status.py"
SERVICES = ROOT / "ops" / "config" / "services.json"

passed = 0
failed: list[str] = []


def check(label: str, condition: bool, detail: str) -> None:
    global passed
    if condition:
        passed += 1
        print(f"PASS  {label}")
    else:
        failed.append(label)
        print(f"FAIL  {label}: {detail}")


def executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def fake_gh(path: Path) -> None:
    """A stateful local implementation of the exact gh api boundary."""
    executable(path, f"""#!{sys.executable}
import json, os, pathlib, sys
state_path = pathlib.Path(os.environ['CARR_TEST_GH_STATE'])
log_path = pathlib.Path(os.environ['CARR_TEST_GH_LOG'])
args = sys.argv[1:]
method = 'GET'
for i, arg in enumerate(args):
    if arg in ('-X', '--method') and i + 1 < len(args): method = args[i + 1].upper()
    elif arg.startswith('--method='): method = arg.split('=', 1)[1].upper()
url = next((arg for arg in args if arg.startswith('/repos/')), '')
body = {{}}
if '--input' in args:
    try: body = json.load(sys.stdin)
    except Exception: body = {{}}
with log_path.open('a', encoding='utf-8') as log:
    log.write(json.dumps({{'method': method, 'url': url, 'body': body, 'args': args}}, sort_keys=True) + '\\n')
state = json.loads(state_path.read_text(encoding='utf-8'))
def save():
    tmp = state_path.with_suffix('.next')
    tmp.write_text(json.dumps(state, sort_keys=True), encoding='utf-8')
    tmp.replace(state_path)
def answer(value):
    print(json.dumps(value, sort_keys=True))
if url.endswith('/check-runs') and method == 'POST':
    item = dict(body); item['id'] = state.get('next_check_id', 8001)
    state['next_check_id'] = item['id'] + 1
    state.setdefault('check_runs', []).append(item); save(); answer(item)
elif '/check-runs/' in url and method in ('PATCH', 'POST'):
    check_id = int(url.rsplit('/', 1)[1])
    item = next((x for x in state.get('check_runs', []) if x.get('id') == check_id), None)
    if item is None: print('missing check', file=sys.stderr); raise SystemExit(1)
    item.update(body); save(); answer(item)
elif url.endswith('/check-runs'):
    if state.get('check_list_error'):
        print('synthetic check listing failure', file=sys.stderr); raise SystemExit(1)
    rows = state.get('check_runs', [])
    if state.get('check_requires_filter_all') and 'filter=all' not in args:
        rows = []
    if state.get('check_pagination_exhausted'):
        # A full page every time with a total the cap can never reach: the
        # exact shape that must fail closed rather than answer from a partial
        # view of the head's Checks.
        filler = [{{'id': 90000 + i, 'name': 'other', 'head_sha': 'a' * 40}} for i in range(100)]
        answer({{'total_count': 10 ** 6, 'check_runs': filler}})
    else:
        answer({{'total_count': len(rows), 'check_runs': rows}})
elif '/actions/artifacts/' in url:
    artifact_id = int(url.rsplit('/', 1)[1])
    item = next((x for x in state.get('artifacts', []) if x.get('id') == artifact_id), None)
    if item is None: print('missing artifact', file=sys.stderr); raise SystemExit(1)
    answer(item)
elif url.endswith('/artifacts'):
    if state.get('artifact_list_error'):
        print('synthetic artifact listing failure', file=sys.stderr); raise SystemExit(1)
    answer({{'total_count': len(state.get('artifacts', [])), 'artifacts': state.get('artifacts', [])}})
elif url.endswith('/cancel') and method == 'POST':
    state.setdefault('cancel_calls', []).append(url); save()
    if state.get('cancel_error'):
        print('synthetic cancel failure', file=sys.stderr); raise SystemExit(1)
    answer({{}})
elif '/actions/runs/' in url:
    answer(state.get('run', {{}}))
else:
    print('unexpected synthetic gh call: ' + method + ' ' + url, file=sys.stderr)
    raise SystemExit(2)
""")


def identity() -> dict[str, object]:
    return {
        "repository": "jbookout/carr-system",
        "run_id": 101,
        "run_attempt": 2,
        "head_sha": "a" * 40,
    }


def artifact() -> dict[str, object]:
    return {
        "id": 7001,
        "name": "carr-backup-run-101-attempt-2",
        "size_in_bytes": 2_097_152,
        "digest": "sha256:" + "b" * 64,
        "created_at": "2026-09-06T12:00:00Z",
        "expires_at": "2026-12-05T12:00:00Z",
        "expired": False,
        "workflow_run": {
            "id": 101,
            "head_sha": "a" * 40,
        },
    }


def run_state(*, conclusion: str = "success") -> dict[str, object]:
    return {
        "id": 101,
        "run_attempt": 2,
        "head_sha": "a" * 40,
        "status": "completed",
        "conclusion": conclusion,
    }


def named_check(conclusion: str, *, bound: bool = True) -> dict[str, object]:
    bound_identity = identity()
    if not bound:
        bound_identity["head_sha"] = "d" * 40
    summary: dict[str, object] = {}
    if conclusion == "failure":
        summary = {"signal": "backup-failure", "reason": "synthetic dump failure"}
    elif conclusion == "success":
        item = artifact()
        summary = {
            "artifact_id": item["id"],
            "artifact_name": item["name"],
            "artifact_digest": item["digest"],
            "artifact_bytes": item["size_in_bytes"],
            "artifact_created_at": item["created_at"],
            "artifact_expires_at": item["expires_at"],
            "required_steps": ["dump", "encrypt", "upload", "readback"],
        }
    return {
        "id": 8001,
        "name": "Backup artifact",
        "head_sha": "a" * 40,
        "status": "completed",
        "conclusion": conclusion,
        "external_id": json.dumps(bound_identity, sort_keys=True, separators=(",", ":")),
        "output": {"title": "Backup artifact", "summary": json.dumps(summary, sort_keys=True)},
    }


def fixture_env(
    root: Path,
    state: dict[str, object],
    overrides: dict[str, str] | None = None,
) -> tuple[dict[str, str], Path, Path]:
    bin_dir = root / "bin"
    bin_dir.mkdir()
    fake_gh(bin_dir / "gh")
    state_path = root / "provider-state.json"
    log_path = root / "provider-calls.jsonl"
    state_path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
    env = {
        **os.environ,
        "PATH": str(bin_dir),
        "GH_TOKEN": "synthetic-fixture-only",
        "GITHUB_REPOSITORY": "jbookout/carr-system",
        "GITHUB_RUN_ID": "101",
        "GITHUB_RUN_ATTEMPT": "2",
        "GITHUB_SHA": "a" * 40,
        # The controlled-failure guards read these four. Every fixture sets
        # them explicitly, including the ones that never touch the proof
        # commands, so a guard can never quietly pass by inheriting the real
        # GitHub Actions environment this suite may itself be running inside.
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_ACTOR": "jbookout",
        "GITHUB_TRIGGERING_ACTOR": "jbookout",
        "GITHUB_REF": "refs/heads/main",
        "CARR_TEST_GH_STATE": str(state_path),
        "CARR_TEST_GH_LOG": str(log_path),
    }
    env.update(overrides or {})
    return env, state_path, log_path


def invoke(env: dict[str, str], *args: str, timeout: float = 8) -> subprocess.CompletedProcess[str]:
    if not STATUS_HELPER.is_file():
        return subprocess.CompletedProcess(args, 127, "", f"missing {STATUS_HELPER}")
    return subprocess.run(
        [sys.executable, str(STATUS_HELPER), *args],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )


def read_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def calls(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def parsed_output(run: subprocess.CompletedProcess[str]) -> dict[str, object]:
    try:
        return json.loads(run.stdout)
    except (json.JSONDecodeError, TypeError):
        return {}


def behavioral_contract() -> None:
    artifact_args = (
        "--artifact-id", "7001",
        "--artifact-name", "carr-backup-run-101-attempt-2",
        "--artifact-digest", "sha256:" + "b" * 64,
        "--artifact-bytes", "2097152",
        "--artifact-created-at", "2026-09-06T12:00:00Z",
        "--artifact-expires-at", "2026-12-05T12:00:00Z",
    )

    with tempfile.TemporaryDirectory(prefix="carr-wr54-status-complete-") as raw:
        env, state_path, log_path = fixture_env(
            Path(raw),
            {
                "run": run_state(),
                "artifacts": [artifact()],
                "check_runs": [],
                "check_requires_filter_all": True,
            },
        )
        started = invoke(env, "producer-start")
        after_start = read_json(state_path)
        start_checks = after_start.get("check_runs", [])
        start_item = start_checks[0] if isinstance(start_checks, list) and start_checks else {}
        try:
            start_identity = json.loads(start_item.get("external_id", ""))
        except (json.JSONDecodeError, TypeError):
            start_identity = {}
        check(
            "producer-start creates one in-progress Check with exact immutable identity",
            started.returncode == 0
            and start_item.get("name") == "Backup artifact"
            and start_item.get("status") == "in_progress"
            and start_identity == identity(),
            started.stderr,
        )

        completed = invoke(env, "producer-complete", *artifact_args)
        after_complete = read_json(state_path)
        complete_checks = after_complete.get("check_runs", [])
        complete_item = (
            complete_checks[0]
            if isinstance(complete_checks, list) and len(complete_checks) == 1
            else {}
        )
        output = complete_item.get("output", {}) if isinstance(complete_item, dict) else {}
        summary_raw = output.get("summary", "{}") if isinstance(output, dict) else "{}"
        try:
            summary = json.loads(summary_raw) if isinstance(summary_raw, str) else summary_raw
        except json.JSONDecodeError:
            summary = {}
        check(
            "producer-complete readbacks exact artifact metadata before one success Check",
            completed.returncode == 0
            and complete_item.get("conclusion") == "success"
            and summary.get("artifact_id") == 7001
            and summary.get("artifact_name") == artifact()["name"]
            and summary.get("artifact_digest") == artifact()["digest"]
            and summary.get("artifact_bytes") == artifact()["size_in_bytes"]
            and summary.get("artifact_created_at") == artifact()["created_at"]
            and summary.get("artifact_expires_at") == artifact()["expires_at"]
            and summary.get("required_steps") == ["dump", "encrypt", "upload", "readback"]
            and any("/actions/artifacts/7001" in str(call.get("url", "")) for call in calls(log_path)),
            completed.stderr,
        )
        check(
            "producer uses all Checks and exact run-attempt readback",
            any(
                isinstance(args := call.get("args"), list) and "filter=all" in args
                for call in calls(log_path)
            )
            and any(
                str(call.get("url", "")).endswith("/actions/runs/101/attempts/2")
                for call in calls(log_path)
            ),
            json.dumps(calls(log_path), sort_keys=True),
        )

    with tempfile.TemporaryDirectory(prefix="carr-wr54-status-mismatch-") as raw:
        wrong = artifact()
        wrong["size_in_bytes"] = 17
        env, state_path, _ = fixture_env(
            Path(raw),
            {"run": run_state(), "artifacts": [wrong], "check_runs": []},
        )
        invoke(env, "producer-start")
        mismatched = invoke(env, "producer-complete", *artifact_args)
        mismatch_checks = read_json(state_path).get("check_runs", [])
        check(
            "artifact metadata mismatch cannot publish backup success",
            STATUS_HELPER.is_file()
            and mismatched.returncode != 0
            and isinstance(mismatch_checks, list)
            and all(item.get("conclusion") != "success" for item in mismatch_checks),
            mismatched.stderr,
        )

    with tempfile.TemporaryDirectory(prefix="carr-wr54-status-attempt-name-") as raw:
        wrong_attempt = artifact()
        wrong_attempt["name"] = "carr-backup-run-101-attempt-20"
        env, state_path, _ = fixture_env(
            Path(raw),
            {"run": run_state(), "artifacts": [wrong_attempt], "check_runs": []},
        )
        invoke(env, "producer-start")
        embedded_attempt = invoke(
            env,
            "producer-complete",
            "--artifact-id", "7001",
            "--artifact-name", "carr-backup-run-101-attempt-20",
            "--artifact-digest", "sha256:" + "b" * 64,
            "--artifact-bytes", "2097152",
            "--artifact-created-at", "2026-09-06T12:00:00Z",
            "--artifact-expires-at", "2026-12-05T12:00:00Z",
        )
        attempt_checks = read_json(state_path).get("check_runs", [])
        check(
            "producer attempt 2 rejects an artifact whose exact name says attempt 20",
            embedded_attempt.returncode != 0
            and isinstance(attempt_checks, list)
            and all(item.get("conclusion") != "success" for item in attempt_checks),
            embedded_attempt.stderr,
        )

    with tempfile.TemporaryDirectory(prefix="carr-wr54-status-fail-") as raw:
        env, state_path, _ = fixture_env(
            Path(raw),
            {"run": run_state(conclusion="failure"), "artifacts": [], "check_runs": []},
        )
        failed_run = invoke(env, "producer-fail", "--reason", "synthetic dump failure")
        failure_checks = read_json(state_path).get("check_runs", [])
        failure_item = failure_checks[0] if isinstance(failure_checks, list) and failure_checks else {}
        try:
            failure_identity = json.loads(failure_item.get("external_id", ""))
            failure_summary = json.loads(failure_item.get("output", {}).get("summary", "{}"))
        except (json.JSONDecodeError, TypeError, AttributeError):
            failure_identity, failure_summary = {}, {}
        check(
            "producer-fail writes only an exact-bound backup-failure Check",
            failed_run.returncode == 0
            and failure_item.get("name") == "Backup artifact"
            and failure_item.get("conclusion") == "failure"
            and failure_identity == identity()
            and failure_summary.get("signal") == "backup-failure",
            failed_run.stderr,
        )

    with tempfile.TemporaryDirectory(prefix="carr-wr54-status-neutral-") as raw:
        env, state_path, log_path = fixture_env(
            Path(raw),
            {"run": run_state(conclusion="cancelled"), "artifacts": [], "check_runs": []},
        )
        proc: subprocess.Popen[str] | None = None
        if STATUS_HELPER.is_file():
            proc = subprocess.Popen(
                [sys.executable, str(STATUS_HELPER), "neutral-cancel"],
                cwd=ROOT,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            deadline = time.monotonic() + 4
            while time.monotonic() < deadline:
                observed = calls(log_path)
                if any(str(call.get("url", "")).endswith("/actions/runs/101/cancel")
                       for call in observed):
                    break
                if proc.poll() is not None:
                    break
                time.sleep(0.05)
            alive_after_cancel = proc.poll() is None
            proc.terminate()
            try:
                proc.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate()
        else:
            alive_after_cancel = False
        neutral_state = read_json(state_path)
        neutral_checks = neutral_state.get("check_runs", [])
        neutral_item = neutral_checks[0] if isinstance(neutral_checks, list) and neutral_checks else {}
        neutral_calls = calls(log_path)
        cancel_urls = [
            str(item.get("url", ""))
            for item in neutral_calls
            if str(item.get("url", "")).endswith("/cancel")
        ]
        create_index = next(
            (i for i, item in enumerate(neutral_calls)
             if item.get("method") == "POST" and str(item.get("url", "")).endswith("/check-runs")),
            -1,
        )
        readback_index = next(
            (i for i, item in enumerate(neutral_calls)
             if i > create_index and item.get("method") == "GET"
             and str(item.get("url", "")).endswith("/check-runs")),
            -1,
        )
        cancel_index = next(
            (i for i, item in enumerate(neutral_calls)
             if str(item.get("url", "")).endswith("/actions/runs/101/cancel")),
            -1,
        )
        check(
            "neutral path creates and reads back neutral, cancels only its run, then waits for kill",
            neutral_item.get("conclusion") == "neutral"
            and create_index >= 0 < readback_index < cancel_index
            and cancel_urls == ["/repos/jbookout/carr-system/actions/runs/101/cancel"]
            and alive_after_cancel,
            json.dumps(neutral_calls, sort_keys=True),
        )

    with tempfile.TemporaryDirectory(prefix="carr-wr54-status-cancel-error-") as raw:
        env, state_path, log_path = fixture_env(
            Path(raw),
            {
                "run": run_state(conclusion="cancelled"),
                "artifacts": [],
                "check_runs": [],
                "cancel_error": True,
            },
        )
        if STATUS_HELPER.is_file():
            proc = subprocess.Popen(
                [sys.executable, str(STATUS_HELPER), "neutral-cancel"],
                cwd=ROOT,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            deadline = time.monotonic() + 4
            while time.monotonic() < deadline and proc.poll() is None:
                if any(str(call.get("url", "")).endswith("/cancel") for call in calls(log_path)):
                    time.sleep(0.1)
                    break
                time.sleep(0.05)
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.communicate(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.communicate()
        cancel_error_checks = read_json(state_path).get("check_runs", [])
        check(
            "cancel API failure cannot turn a neutral Check into success or backup-failure",
            isinstance(cancel_error_checks, list)
            and len(cancel_error_checks) == 1
            and cancel_error_checks[0].get("conclusion") == "neutral"
            and "backup-failure" not in json.dumps(cancel_error_checks),
            json.dumps(cancel_error_checks, sort_keys=True),
        )

    def observe_case(
        label: str,
        state: dict[str, object],
        expected_state: str | None,
        expected_signal: str | None,
        expected_rc: int | None,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="carr-wr54-status-observe-") as raw:
            env, _, log_path = fixture_env(Path(raw), state)
            observed = invoke(env, "observe")
            result = parsed_output(observed)
            ok = (
                (expected_rc is None or observed.returncode == expected_rc)
                and (expected_state is None or result.get("state") == expected_state)
                and result.get("signal") == expected_signal
                and any("/actions/runs/101" in str(call.get("url", ""))
                        for call in calls(log_path))
            )
            check(label, ok, f"rc={observed.returncode} out={observed.stdout!r} err={observed.stderr!r}")

    observe_case(
        "observer accepts exact cancelled neutral run with zero artifacts as disabled",
        {"run": run_state(conclusion="cancelled"), "artifacts": [], "check_runs": [named_check("neutral")]},
        "disabled",
        None,
        0,
    )
    observe_case(
        "observer refuses mismatched neutral provenance without backup-failure",
        {"run": run_state(conclusion="cancelled"), "artifacts": [], "check_runs": [named_check("neutral", bound=False)]},
        "unknown",
        None,
        2,
    )
    observe_case(
        "infrastructure cancellation without neutral Check cannot signal backup-failure",
        {"run": run_state(conclusion="cancelled"), "artifacts": [], "check_runs": []},
        "unknown",
        None,
        2,
    )
    observe_case(
        "observer emits backup-failure for the exact-bound failure Check",
        {"run": run_state(conclusion="cancelled"), "artifacts": [], "check_runs": [named_check("failure")]},
        "failure",
        "backup-failure",
        1,
    )
    observe_case(
        "observer suppresses backup-failure for mismatched failure provenance",
        {"run": run_state(conclusion="failure"), "artifacts": [], "check_runs": [named_check("failure", bound=False)]},
        "unknown",
        None,
        2,
    )
    observe_case(
        "observer accepts success only with exact artifact metadata",
        {"run": run_state(), "artifacts": [artifact()], "check_runs": [named_check("success")]},
        "success",
        None,
        0,
    )


PROOF_ID = "3f1c2d4e-5a6b-4c7d-8e9f-0a1b2c3d4e5f"
PROOF_HEAD = "a" * 40
PROOF_REASON = "approved WR54 controlled failure before dump"
CONTROLLED_FAILURE_EXIT = 9


def proof_args(*, proof_id: str = PROOF_ID, head: str = PROOF_HEAD) -> tuple[str, ...]:
    return ("--proof-id", proof_id, "--expected-head", head)


def spent_proof_check(proof_id: str = PROOF_ID) -> dict[str, object]:
    """A proof Check left on this head by an EARLIER run.

    Its run identity is 99/1, not this fixture's 101/2, on purpose: the reuse
    scan has to find a proof spent by any run on the head, not merely one bound
    to the current run's own external_id.
    """
    earlier = {
        "repository": "jbookout/carr-system",
        "run_id": 99,
        "run_attempt": 1,
        "head_sha": PROOF_HEAD,
    }
    summary = {"signal": "backup-failure", "reason": PROOF_REASON, "proof_id": proof_id}
    return {
        "id": 8500,
        "name": "Backup artifact",
        "head_sha": PROOF_HEAD,
        "status": "completed",
        "conclusion": "failure",
        "external_id": json.dumps(earlier, sort_keys=True, separators=(",", ":")),
        "output": {"title": "Backup artifact", "summary": json.dumps(summary, sort_keys=True)},
    }


def controlled_failure_contract() -> None:
    """The WR54 seam: refuse loudly, or fail exactly once with a bound proof."""

    # ── the read-only validation passes on an exact request, writing nothing ──
    with tempfile.TemporaryDirectory(prefix="carr-wr54-proof-validate-") as raw:
        env, state_path, _ = fixture_env(
            Path(raw), {"run": run_state(), "artifacts": [], "check_runs": []},
        )
        validated = invoke(env, "validate-controlled-failure", *proof_args())
        after_validate = read_json(state_path)
        check(
            "validate-controlled-failure accepts an exact request and writes nothing",
            validated.returncode == 0
            and after_validate.get("check_runs") == []
            and after_validate.get("artifacts") == [],
            f"rc={validated.returncode} err={validated.stderr!r} "
            f"state={json.dumps(after_validate, sort_keys=True)}",
        )

    # ── every wrong guard refuses, and none of them mutates a Check ──────────
    refusals: list[tuple[str, dict[str, object], dict[str, str], tuple[str, ...]]] = [
        (
            "a wrong actor cannot spend a controlled failure",
            {"run": run_state(), "artifacts": [], "check_runs": []},
            {"GITHUB_ACTOR": "someone-else"},
            proof_args(),
        ),
        (
            "a wrong triggering actor cannot spend a controlled failure",
            {"run": run_state(), "artifacts": [], "check_runs": []},
            {"GITHUB_TRIGGERING_ACTOR": "someone-else"},
            proof_args(),
        ),
        (
            "a scheduled event can never reach the controlled failure",
            {"run": run_state(), "artifacts": [], "check_runs": []},
            {"GITHUB_EVENT_NAME": "schedule"},
            proof_args(),
        ),
        (
            "a non-main ref cannot spend a controlled failure",
            {"run": run_state(), "artifacts": [], "check_runs": []},
            {"GITHUB_REF": "refs/heads/topic"},
            proof_args(),
        ),
        (
            "another repository cannot spend a controlled failure",
            {"run": run_state(), "artifacts": [], "check_runs": []},
            {"GITHUB_REPOSITORY": "someone-else/carr-system"},
            proof_args(),
        ),
        (
            "an expected head that is not this run's GITHUB_SHA refuses",
            {"run": run_state(), "artifacts": [], "check_runs": []},
            {},
            proof_args(head="b" * 40),
        ),
        (
            "a short expected head refuses",
            {"run": run_state(), "artifacts": [], "check_runs": []},
            {},
            proof_args(head="abc123"),
        ),
        (
            "an uppercase expected head refuses rather than being normalised",
            {"run": run_state(), "artifacts": [], "check_runs": []},
            {},
            proof_args(head="A" * 40),
        ),
        (
            "a malformed proof ID refuses",
            {"run": run_state(), "artifacts": [], "check_runs": []},
            {},
            proof_args(proof_id="not-a-uuid"),
        ),
        (
            "an uppercase proof ID is not canonical and refuses",
            {"run": run_state(), "artifacts": [], "check_runs": []},
            {},
            proof_args(proof_id=PROOF_ID.upper()),
        ),
        (
            "a proof ID already spent on this head refuses",
            {"run": run_state(), "artifacts": [], "check_runs": [spent_proof_check()]},
            {},
            proof_args(),
        ),
        (
            "an artifact already on this run refuses before any Check write",
            {"run": run_state(), "artifacts": [artifact()], "check_runs": []},
            {},
            proof_args(),
        ),
        (
            "exhausted Check pagination fails closed instead of reporting a clean head",
            {
                "run": run_state(),
                "artifacts": [],
                "check_runs": [],
                "check_pagination_exhausted": True,
            },
            {},
            proof_args(),
        ),
        (
            "a provider Check-listing failure refuses",
            {"run": run_state(), "artifacts": [], "check_runs": [], "check_list_error": True},
            {},
            proof_args(),
        ),
        (
            "a provider artifact-listing failure refuses",
            {"run": run_state(), "artifacts": [], "check_runs": [], "artifact_list_error": True},
            {},
            proof_args(),
        ),
    ]
    for label, state, overrides, args in refusals:
        for command in ("validate-controlled-failure", "controlled-failure"):
            with tempfile.TemporaryDirectory(prefix="carr-wr54-proof-refuse-") as raw:
                env, state_path, _ = fixture_env(Path(raw), dict(state), overrides)
                before = json.dumps(read_json(state_path).get("check_runs"), sort_keys=True)
                refused = invoke(env, command, *args)
                after = json.dumps(read_json(state_path).get("check_runs"), sort_keys=True)
                check(
                    f"{command}: {label}",
                    refused.returncode != 0
                    and refused.returncode != CONTROLLED_FAILURE_EXIT
                    and after == before,
                    f"rc={refused.returncode} err={refused.stderr!r} checks={after}",
                )

    # ── the exact proof: one Check, flipped once, and a failing exit ─────────
    with tempfile.TemporaryDirectory(prefix="carr-wr54-proof-write-") as raw:
        env, state_path, log_path = fixture_env(
            Path(raw),
            {"run": run_state(conclusion="failure"), "artifacts": [], "check_runs": []},
        )
        started = invoke(env, "producer-start")
        proved = invoke(env, "controlled-failure", *proof_args())
        rows = read_json(state_path).get("check_runs", [])
        item = rows[0] if isinstance(rows, list) and len(rows) == 1 else {}
        try:
            summary = json.loads(item.get("output", {}).get("summary", "{}"))
            bound = json.loads(item.get("external_id", ""))
        except (json.JSONDecodeError, TypeError, AttributeError):
            summary, bound = {}, {}
        check(
            "controlled-failure flips only this run's Check to a structured proof failure",
            started.returncode == 0
            and proved.returncode == CONTROLLED_FAILURE_EXIT
            and item.get("name") == "Backup artifact"
            and item.get("status") == "completed"
            and item.get("conclusion") == "failure"
            and bound == identity()
            and summary == {
                "signal": "backup-failure",
                "reason": PROOF_REASON,
                "proof_id": PROOF_ID,
            },
            f"rc={proved.returncode} err={proved.stderr!r} checks={json.dumps(rows, sort_keys=True)}",
        )
        check(
            "the controlled proof reads no credential and creates no artifact",
            read_json(state_path).get("artifacts") == []
            and not any(
                "/actions/artifacts" in str(call.get("url", ""))
                for call in calls(log_path)
            ),
            json.dumps(calls(log_path), sort_keys=True),
        )
        observed = invoke(env, "observe")
        result = parsed_output(observed)
        check(
            "the observer reports the proof failure with its structured proof_id",
            observed.returncode == 1
            and result.get("state") == "failure"
            and result.get("signal") == "backup-failure"
            and result.get("proof_id") == PROOF_ID
            and result.get("detail") == PROOF_REASON,
            f"rc={observed.returncode} out={observed.stdout!r}",
        )
        replayed = invoke(env, "validate-controlled-failure", *proof_args())
        check(
            "the same proof ID cannot be spent twice on the same head",
            replayed.returncode != 0 and replayed.returncode != CONTROLLED_FAILURE_EXIT,
            f"rc={replayed.returncode} err={replayed.stderr!r}",
        )

    # ── the write boundary needs this attempt's own in-progress Check ────────
    with tempfile.TemporaryDirectory(prefix="carr-wr54-proof-no-check-") as raw:
        env, state_path, _ = fixture_env(
            Path(raw), {"run": run_state(), "artifacts": [], "check_runs": []},
        )
        orphan = invoke(env, "controlled-failure", *proof_args())
        check(
            "controlled-failure without a started Check refuses and creates none",
            orphan.returncode != 0
            and orphan.returncode != CONTROLLED_FAILURE_EXIT
            and read_json(state_path).get("check_runs") == [],
            f"rc={orphan.returncode} err={orphan.stderr!r}",
        )

    # A Check that exists but has not reached in_progress is the case the
    # conclusion guard alone does NOT cover: it carries no conclusion, so only
    # the status guard can refuse it. A mutation run found this uncovered.
    with tempfile.TemporaryDirectory(prefix="carr-wr54-proof-queued-") as raw:
        queued = named_check("neutral")
        queued["status"] = "queued"
        queued["conclusion"] = None
        env, state_path, _ = fixture_env(
            Path(raw), {"run": run_state(), "artifacts": [], "check_runs": [queued]},
        )
        premature = invoke(env, "controlled-failure", *proof_args())
        rows = read_json(state_path).get("check_runs", [])
        check(
            "controlled-failure refuses a Check that has not reached in_progress",
            premature.returncode != 0
            and premature.returncode != CONTROLLED_FAILURE_EXIT
            and isinstance(rows, list)
            and all(
                row.get("status") == "queued" and row.get("conclusion") is None
                for row in rows
            ),
            f"rc={premature.returncode} err={premature.stderr!r} checks={json.dumps(rows, sort_keys=True)}",
        )

    with tempfile.TemporaryDirectory(prefix="carr-wr54-proof-success-") as raw:
        env, state_path, _ = fixture_env(
            Path(raw),
            {"run": run_state(), "artifacts": [artifact()], "check_runs": [named_check("success")]},
        )
        over_success = invoke(env, "controlled-failure", *proof_args())
        rows = read_json(state_path).get("check_runs", [])
        check(
            "controlled-failure refuses to overwrite an artifact-backed success Check",
            over_success.returncode != 0
            and over_success.returncode != CONTROLLED_FAILURE_EXIT
            and isinstance(rows, list)
            and all(row.get("conclusion") == "success" for row in rows),
            f"rc={over_success.returncode} err={over_success.stderr!r} checks={json.dumps(rows, sort_keys=True)}",
        )

    with tempfile.TemporaryDirectory(prefix="carr-wr54-proof-ambiguous-") as raw:
        second = named_check("neutral")
        second["id"] = 8002
        env, state_path, _ = fixture_env(
            Path(raw),
            {
                "run": run_state(),
                "artifacts": [],
                "check_runs": [named_check("neutral"), second],
            },
        )
        ambiguous = invoke(env, "controlled-failure", *proof_args())
        rows = read_json(state_path).get("check_runs", [])
        check(
            "two exact Checks make the proof ambiguous and it refuses",
            ambiguous.returncode != 0
            and ambiguous.returncode != CONTROLLED_FAILURE_EXIT
            and isinstance(rows, list)
            and all(row.get("conclusion") == "neutral" for row in rows),
            f"rc={ambiguous.returncode} err={ambiguous.stderr!r}",
        )


def workflow_mode_contract(source: str, backup_job: str) -> None:
    """The gate resolves three modes, and every effect step names exactly one."""
    dispatch = re.search(
        r"(?ms)^  workflow_dispatch:\s*\n(?P<body>.*?)(?=^\S|^concurrency:|\Z)", source
    )
    dispatch_body = dispatch.group("body") if dispatch else ""
    # EACH INPUT IS READ FROM ITS OWN BLOCK. A single regex walking the whole
    # dispatch body with .*? will happily satisfy one input's "required: false"
    # from the OTHER input's, so flipping either one to required stays green —
    # which is exactly what a mutation run caught this assertion doing.
    # NO DOTALL HERE. With (?s) the `.` in `.*\n` matches newlines too, so the
    # first input's block ran straight through the second one and the whole
    # per-input scoping was decorative — the same mutation caught that as well.
    def input_block(name: str) -> str:
        match = re.search(
            rf"(?m)^      {name}:\n(?P<body>(?:^ {{8,}}[^\n]*\n)*)",
            dispatch_body,
        )
        return match.group("body") if match else ""

    optional = {
        name: block
        for name in ("wr54_failure_proof_id", "wr54_failure_proof_expected_head")
        if (block := input_block(name))
        and re.search(r"(?m)^        type: string\s*$", block)
        and re.search(r"(?m)^        required: false\s*$", block)
        and re.search(r"(?m)^        default: \"\"\s*$", block)
    }
    check(
        "both proof inputs are optional and default to empty",
        len(optional) == 2,
        "an ordinary manual dispatch must stay byte-for-behavior unchanged, which "
        "means both inputs optional with an empty default; satisfied: "
        + json.dumps(sorted(optional)),
    )
    check(
        "the gate resolves exactly the three declared modes",
        all(f"MODE={mode}" in backup_job for mode in ("backup", "disabled", "failure-proof"))
        and 'echo "mode=$MODE" >> "$GITHUB_OUTPUT"' in backup_job
        and "gate.outputs.enabled" not in backup_job,
        "the three-mode gate replaces the old boolean enabled output",
    )
    # ORDER IS ASSERTED ON THE INVOCATIONS, NOT ON THE PROSE. Comments name
    # these commands too, so an index into the raw job text would measure where
    # a sentence sits rather than where a step runs.
    start_call = "backup-workflow-status.py producer-start"
    validate_call = "backup-workflow-status.py validate-controlled-failure"
    convert_call = "backup-workflow-status.py controlled-failure"
    check(
        "a half-filled proof request is refused at the gate, before producer-start",
        "refusing before producer-start" in backup_job
        and start_call in backup_job
        and backup_job.index("refusing before producer-start") < backup_job.index(start_call),
        "the refusal must precede the first durable write, so nothing needs unwinding",
    )
    check(
        "a non-dispatch event carrying proof inputs is refused rather than downgraded",
        bool(re.search(
            r'\[ "\$EVENT_NAME" != "workflow_dispatch" \][^\n]*\\\n[^\n]*'
            r'\{ \[ -n "\$PROOF_ID" \] \|\| \[ -n "\$PROOF_EXPECTED_HEAD" \]; \}',
            backup_job,
        )),
        "a scheduled event must never enter failure-proof mode, and must not fall back to backup",
    )

    steps = workflow_steps(backup_job)
    effect_tokens = (
        "secrets.BACKUP_DATABASE_URL",
        "secrets.R2_ACCESS_KEY_ID",
        "secrets.R2_SECRET_ACCESS_KEY",
        "vars.CLOUDFLARE_ACCOUNT_ID",
        "backup-dump.sh",
        "upload-artifact",
        "s3api",
        "producer-complete",
        "setup-python",
        "requirements.lock",
        "postgresql-client-18",
    )
    ungated = [
        step.splitlines()[0].strip()
        for step in steps
        if any(token in step for token in effect_tokens)
        and "steps.gate.outputs.mode == 'backup'" not in step
    ]
    check(
        "every credential, dependency, dump, upload and R2 step is gated to mode=backup",
        bool(steps) and not ungated,
        "ungated effect steps: " + json.dumps(ungated),
    )
    invoking = {
        call: [step for step in steps if call in step]
        for call in (start_call, validate_call, convert_call,
                     "backup-workflow-status.py producer-fail",
                     "backup-workflow-status.py neutral-cancel")
    }
    proof_steps = invoking[validate_call] + invoking[convert_call]
    check(
        "validation runs read-only before producer-start, and the write step after it",
        len(invoking[validate_call]) == 1
        and len(invoking[convert_call]) == 1
        and all(
            step_condition(step) == "steps.gate.outputs.mode == 'failure-proof'"
            for step in proof_steps
        )
        and backup_job.index(validate_call)
        < backup_job.index(start_call)
        < backup_job.index(convert_call),
        "expected exactly one failure-proof-gated validate step and one convert step, "
        "in validate/start/convert order",
    )
    check(
        "producer-start is the one step shared by backup and failure-proof",
        len(invoking[start_call]) == 1
        and step_condition(invoking[start_call][0])
        == "steps.gate.outputs.mode == 'backup' || steps.gate.outputs.mode == 'failure-proof'",
        "the proof needs a real in-progress Check to convert, and nothing else in common",
    )
    recorder = invoking["backup-workflow-status.py producer-fail"]
    check(
        "the generic pipeline-failure recorder stays mode=backup only",
        len(recorder) == 1
        and "steps.gate.outputs.mode == 'backup'" in step_condition(recorder[0])
        and "failure-proof" not in step_condition(recorder[0]),
        "a generic recorder reachable in proof mode could overwrite or duplicate the "
        "proof Check: " + json.dumps([step_condition(s) for s in recorder]),
    )
    canceller = invoking["backup-workflow-status.py neutral-cancel"]
    check(
        "the disabled scheduled branch keeps its neutral self-cancel path",
        len(canceller) == 1
        and step_condition(canceller[0]) == "steps.gate.outputs.mode == 'disabled'"
        and 'CLOUD_BACKUP_ENABLED:-}" = "true"' in backup_job,
        "the reviewed disabled predicate and its neutral/self-cancel semantics are unchanged",
    )


def workflow_steps(job_body: str) -> list[str]:
    """Split a job body into its step blocks on the six-space list markers.

    A run of comment lines directly above a marker belongs to the step it
    introduces, not to the one it follows. Attaching it to the previous block
    would file every explanatory header under the wrong step and quietly
    corrupt any scan that reads a block's text.
    """
    blocks: list[str] = []
    current: list[str] = []
    pending: list[str] = []
    for line in job_body.splitlines():
        if re.match(r"^      - (?:name|uses):", line):
            if current:
                blocks.append("\n".join(current))
            current = [*pending, line]
            pending = []
        elif re.match(r"^      #", line):
            pending.append(line)
        elif current:
            current.extend(pending)
            pending = []
            current.append(line)
    if current:
        current.extend(pending)
        blocks.append("\n".join(current))
    return blocks


def step_condition(step: str) -> str:
    """The step's own `if:` expression, or empty when it has none."""
    match = re.search(r"(?m)^        if: (?P<expr>.*)$", step)
    return match.group("expr").strip() if match else ""


def service_identity_contract() -> None:
    """One cloud-backup service identity, and it aliases neither neighbour."""
    try:
        catalog = json.loads(SERVICES.read_text(encoding="utf-8"))
        services = catalog.get("services", [])
    except (OSError, json.JSONDecodeError) as exc:
        check("service catalog parses", False, str(exc))
        return
    rows = [row for row in services if row.get("key") == "backup-nightly-cloud"]
    row = rows[0] if len(rows) == 1 else {}
    environments = row.get("environments", []) if isinstance(row, dict) else []
    production = [e for e in environments if e.get("environment") == "production"]
    check(
        "the catalog declares exactly one backup-nightly-cloud production identity",
        len(rows) == 1
        and len(environments) == 1
        and len(production) == 1
        and row.get("repo_path") == ".github/workflows/backup-nightly.yml"
        and row.get("runtime") == "github-actions"
        and row.get("owner_actor") == "joe",
        json.dumps(rows, sort_keys=True),
    )
    environment = production[0] if production else {}
    check(
        "the cloud backup declares no CARR cadence and says why in its own notes",
        "expected_cadence_seconds" not in environment
        and "cadence_grace_seconds" not in environment
        and "not continuously ingested into ops.run" in str(environment.get("notes", ""))
        and "does not detect a missed or skipped schedule" in str(environment.get("notes", ""))
        and "health remains unknown" in str(environment.get("notes", "")),
        "a declared cadence would invent continuous ingestion; the notes must carry the "
        "unknown-state limit and must not claim the failure email detects a missed schedule: "
        + json.dumps(environment, sort_keys=True),
    )
    check(
        "the cloud backup never aliases nightly-record-layer or restore-rehearse-weekly",
        row.get("key") == "backup-nightly-cloud"
        and row.get("repo_path") not in ("bin/nightly.sh",)
        and all(
            other.get("repo_path") != row.get("repo_path")
            for other in services
            if other.get("key") != "backup-nightly-cloud"
        ),
        "the Mac-local nightly chain and the weekly restore rehearsal are distinct services",
    )


def main() -> int:
    source = WORKFLOW.read_text(encoding="utf-8")
    status_source = STATUS_HELPER.read_text(encoding="utf-8") if STATUS_HELPER.is_file() else ""
    backup_match = re.search(
        r"(?ms)^  backup:\s*\n(?P<body>.*?)(?=^  [A-Za-z0-9_-]+:\s*\n|\Z)",
        source,
    )
    backup_job = backup_match.group("body") if backup_match else ""

    check(
        "workflow grants only the additional provider permissions its status contract needs",
        bool(re.search(r"(?m)^    permissions:\s*$", backup_job))
        and bool(re.search(r"(?m)^      checks:\s*write\s*$", backup_job))
        and bool(re.search(r"(?m)^      actions:\s*write\s*$", backup_job))
        and bool(re.search(r"(?m)^      contents:\s*read\s*$", backup_job))
        and not bool(re.search(r"(?m)^  (?:checks|actions):\s*write\s*$", source)),
        "expected job-scoped checks:write/actions:write with contents:read",
    )
    check(
        "producer writes the named Backup artifact Check",
        "backup-workflow-status.py" in source and "Backup artifact" in status_source,
        "workflow must call the narrow status adapter and name the Check",
    )
    check(
        "Check identity binds repository, run, attempt and head",
        all(token in status_source for token in (
            "GITHUB_REPOSITORY", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_SHA",
        ))
        and "external_id" in status_source,
        "a green Check without all four immutable producer locators is ambiguous",
    )
    check(
        "successful status carries exact artifact evidence",
        all(token in status_source for token in (
            "artifact_id", "artifact_name", "artifact_digest", "artifact_bytes",
            "artifact_created_at", "artifact_expires_at",
        )),
        "run success alone is not a backup receipt",
    )
    check(
        "disabled scheduled path records neutral before cancelling only itself",
        "neutral" in status_source.lower()
        and "GITHUB_RUN_ID" in status_source
        and bool(re.search(r"actions/runs/[^\n]*GITHUB_RUN_ID[^\n]*/cancel", status_source))
        and "gh run list" not in status_source,
        "disabled flow must not search for or cancel another run",
    )
    check(
        "disabled cancellation has no success continuation",
        bool(re.search(
            r"(?s)neutral.{0,4000}(?:while\s+true|while\s+True|sleep\s*\(.{0,100}(?:\n|$))",
            status_source,
            re.I,
        )),
        "after requesting cancellation the producing job must wait until killed",
    )
    check(
        "producer workflow does not recursively observe its own workflow_run",
        not bool(re.search(r"(?m)^\s*workflow_run:\s*$", source)),
        "post-terminal verification is a separate consumer invocation, not a recursive workflow trigger",
    )
    check(
        "status adapter exposes separate post-terminal cancelled/zero-artifact observation",
        all(token in status_source.lower() for token in ("observer", "cancelled", "artifact")),
        "the terminating producer cannot certify its own terminal provider state",
    )
    check(
        "disabled neutral is excluded from backup-failure alerting",
        "Backup artifact" in status_source
        and "backup-failure" in status_source
        and bool(re.search(r"neutral|cancelled", status_source, re.I))
        and bool(re.search(r"conclusion[^\n]{0,100}(?:failure|failed)", status_source, re.I)),
        "adapter must consume named Check failure and explicitly exclude neutral/cancel infrastructure states; overall red is insufficient",
    )
    check(
        "artifact name binds producer run and attempt",
        "GITHUB_RUN_ID" in source
        and "GITHUB_RUN_ATTEMPT" in source
        and bool(re.search(r"artifact[^\n]{0,200}(?:run|RUN_ID)[^\n]{0,200}(?:attempt|RUN_ATTEMPT)", source, re.I)),
        "reruns must not overwrite or inherit another attempt's artifact identity",
    )
    workflow_mode_contract(source, backup_job)
    service_identity_contract()
    behavioral_contract()
    controlled_failure_contract()

    print(f"\nbackup-workflow-selftest: {passed}/{passed + len(failed)} passed")
    if failed:
        print("missing contract: " + ", ".join(failed), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
