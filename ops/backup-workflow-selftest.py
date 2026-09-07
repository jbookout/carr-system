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
    rows = state.get('check_runs', [])
    if state.get('check_requires_filter_all') and 'filter=all' not in args:
        rows = []
    answer({{'total_count': len(rows), 'check_runs': rows}})
elif '/actions/artifacts/' in url:
    artifact_id = int(url.rsplit('/', 1)[1])
    item = next((x for x in state.get('artifacts', []) if x.get('id') == artifact_id), None)
    if item is None: print('missing artifact', file=sys.stderr); raise SystemExit(1)
    answer(item)
elif url.endswith('/artifacts'):
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


def fixture_env(root: Path, state: dict[str, object]) -> tuple[dict[str, str], Path, Path]:
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
        "CARR_TEST_GH_STATE": str(state_path),
        "CARR_TEST_GH_LOG": str(log_path),
    }
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
    behavioral_contract()

    print(f"\nbackup-workflow-selftest: {passed}/{passed + len(failed)} passed")
    if failed:
        print("missing contract: " + ", ".join(failed), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
