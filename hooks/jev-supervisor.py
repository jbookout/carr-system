#!/usr/bin/env python3
# doctrine: engineering-workflow-sop
"""jev-supervisor.py — the in-session Jev checks from open loop #629, one hook.

WHAT IT IS. Loop #629 listed twenty-five Jev checks for supervising a coding
agent: first the local Qwen workhorse (`flash`), then Claude sessions too,
because every check is model-agnostic. The judgments live in libraries under
ops/ (jev_session_watch, jev_done_checks, jev_intake, jev_best_of, jev_notebook).
This file is only the dispatcher that a Claude Code hook calls: it looks at the
event, runs the cheap deterministic trigger each check owns, and asks Jev only
when one fires.

IT NEVER BLOCKS. Exit status is always 0 and nothing here sets a decision.
Joe's 2026-08-23 Stop-gate rationing leaves exactly three hooks able to reopen
a turn and this is not one of them. Every check is shadow-first per
ops/jev_judge.py: the answer is recorded to out/jev-judge.jsonl beside what the
session actually did, so a threshold can be measured before anything acts.

TWO MODES, chosen by the CARR_JEV_SUPERVISOR environment variable:
  shadow (default) — record only; the session sees nothing. Claude sessions.
  advise           — also hand the session a short advisory line (the likely
                     buggy line, the real path it probably meant, the tests
                     worth running, "you are looping"). The flash sessions run
                     this way: a small local model gains the most from a nudge
                     and costs nothing extra to nudge.

EVENTS
  PostToolUse (any tool):
    #8  stuck/drift watch        #9  runaway-thinking cutoff
    #11 planted-instruction screen on tool output
    #20 wrong path repair on a missing-file error
    #22 failure-type triage and #16 bug locator on a failed command
    #19 "already exists?" and #21 test picker and #13 test quality on an edit
  Stop:
    #14 "done" claim check       #17 review triage of the turn's diff

Budget: each Jev call carries its own short timeout inside the library, and
the whole hook stops starting new checks once BUDGET_SECONDS is spent, so a slow
vendor costs a bounded delay rather than a stalled session.
"""
import importlib.util
import json
import os
import re
import subprocess
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUDGET_SECONDS = 20.0
MODE = os.environ.get("CARR_JEV_SUPERVISOR", "shadow").strip().lower()
MAX_OUTPUT_CHARS = 12000

TEST_COMMAND = re.compile(r"\b(pytest|unittest|selftest|test[-_]\S*\.py|npm (run )?test|node --test|"
                          r"go test|cargo test|jest|vitest|mocha|-selftest\.py)\b")
MISSING_FILE = re.compile(r"(No such file or directory|does not exist|File not found|"
                          r"ENOENT|cannot find the path)", re.I)
MISSING_PATH_TOKEN = re.compile(r"['\"`]?((?:\.{0,2}/)?[\w.\-]+(?:/[\w.\-]+)+|[\w\-]+\.\w{1,5})['\"`]?")
TRACEBACK_FILE = re.compile(r'File "([^"]+)", line (\d+)')
NODE_FRAME = re.compile(r"\(?(/[^\s():]+\.(?:m?js|ts)):(\d+):\d+\)?")
NEW_DEF = re.compile(r"^\s*(?:async\s+)?(?:def|function)\s+([A-Za-z_]\w*)\s*\(", re.M)
TEST_PATH = re.compile(r"(^|/)(tests?/|test[-_][^/]*|[^/]*[-_.]test\.|[^/]*selftest)")


def _lib(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(REPO, "ops", f"{name}.py"))
    if spec is None or spec.loader is None:
        raise ImportError(name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _text(value):
    """A tool response as plain text, whatever shape the harness gave it."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        parts = [value.get(k) for k in ("stdout", "stderr", "output", "content", "error", "result")]
        joined = "\n".join(p if isinstance(p, str) else json.dumps(p) for p in parts if p)
        return joined or json.dumps(value)[:MAX_OUTPUT_CHARS]
    if isinstance(value, list):
        return "\n".join(_text(v) for v in value)
    return str(value)


def _exit_code(response):
    if isinstance(response, dict):
        for key in ("exit_code", "exitCode", "returncode", "code"):
            if isinstance(response.get(key), int):
                return response[key]
        if response.get("interrupted") or response.get("is_error"):
            return 1
    return None


def _git_root(cwd):
    try:
        out = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=cwd or ".",
                             capture_output=True, text=True, timeout=5)
        return out.stdout.strip() or None
    except Exception:
        return None


def _task(transcript):
    if not transcript:
        return ""
    try:
        return _lib("jev_code_review").latest_task(transcript) or ""
    except Exception:
        return ""


def _notable(result):
    """Whether a check's result is worth an advisory line in advise mode."""
    if not isinstance(result, dict):
        return False
    verdict = str(result.get("verdict", ""))
    quiet = {"ok", "clear", "clean", "not_triggered", "skipped", "unavailable", "none",
             "no_claim", "supported", "local", "progressing", "no_match", "low", "solid",
             "picked", "routed", "matched", "none_fits", "not_a_failure", "no_source",
             "not_a_new_function", "no_candidates", "no_tests_found", "none_relevant"}
    return verdict not in quiet


def _line(result):
    check = result.get("check", "?")
    detail = result.get("detail") or {}
    hint = detail.get("advice") or detail.get("hint") or detail.get("summary") or ""
    if not hint:
        compact = {k: v for k, v in detail.items() if isinstance(v, (str, int, float, list)) and v}
        hint = json.dumps(compact)[:400]
    return f"[jev {check}] {result.get('verdict')}: {hint}"[:600]


class Run:
    def __init__(self):
        self.started = time.monotonic()
        self.results = []

    def left(self):
        return BUDGET_SECONDS - (time.monotonic() - self.started)

    def do(self, fn, *args, **kwargs):
        if self.left() <= 1.0:
            return None
        try:
            result = fn(*args, **kwargs)
        except Exception as exc:  # a library bug must never reach the session
            result = {"check": getattr(fn, "__name__", "?"), "verdict": "unavailable",
                      "confidence": None, "escalate": False, "detail": {"error": repr(exc)[:300]}}
        if isinstance(result, dict):
            self.results.append(result)
        return result


def post_tool_use(payload, run):
    tool = payload.get("tool_name") or ""
    ti = payload.get("tool_input") or {}
    response = payload.get("tool_response")
    out = _text(response)[-MAX_OUTPUT_CHARS:]
    transcript = payload.get("transcript_path") or ""
    cwd = payload.get("cwd") or os.getcwd()
    root = _git_root(cwd) or cwd
    task = _task(transcript)
    watch = _lib("jev_session_watch")

    # #11 — anything the agent just read could carry planted instructions.
    if tool in ("Read", "WebFetch", "WebSearch", "Bash", "Grep") and out:
        run.do(watch.screen_tool_output, tool, out, task)

    if tool == "Bash":
        command = str(ti.get("command", ""))
        code = _exit_code(response)
        failed = (code not in (None, 0)) or bool(re.search(r"Traceback \(most recent call last\)|"
                                                           r"AssertionError|FAILED|Error:", out))
        if failed:
            run.do(watch.triage_failure, command, out, code if code is not None else 1)
            if TEST_COMMAND.search(command) or "Traceback (most recent call last)" in out:
                frames = TRACEBACK_FILE.findall(out) or NODE_FRAME.findall(out)
                inside = [(p, n) for p, n in frames
                          if os.path.isfile(p if os.path.isabs(p) else os.path.join(cwd, p))
                          and "site-packages" not in p and "node_modules" not in p]
                if inside:
                    path = inside[-1][0]
                    full = path if os.path.isabs(path) else os.path.join(cwd, path)
                    try:
                        with open(full, encoding="utf-8", errors="replace") as fh:
                            source = fh.read()
                        run.do(watch.locate_bug, source, path, out)
                    except OSError:
                        pass
        if MISSING_FILE.search(out):
            for token in MISSING_PATH_TOKEN.findall(command)[:2]:
                if not os.path.exists(os.path.join(cwd, token)):
                    run.do(watch.repair_path, token, root)
                    break

    if tool in ("Read", "Edit", "Write", "MultiEdit") and MISSING_FILE.search(out):
        bad = str(ti.get("file_path", ""))
        if bad and not os.path.exists(bad):
            run.do(watch.repair_path, os.path.relpath(bad, root) if bad.startswith(root) else bad, root)

    if tool in ("Edit", "Write", "MultiEdit"):
        path = str(ti.get("file_path", ""))
        added = str(ti.get("content") or ti.get("new_string") or "")
        if tool == "MultiEdit":
            added = "\n".join(str(e.get("new_string", "")) for e in ti.get("edits") or [])
        rel = os.path.relpath(path, root) if path.startswith(root) else path
        for name in NEW_DEF.findall(added)[:1]:
            run.do(watch.check_existing, name, added[:4000], root)
        if TEST_PATH.search(rel):
            done = _lib("jev_done_checks")
            run.do(done.check_test_quality, added[:8000], "", task)
        else:
            run.do(watch.pick_tests, [rel], root)

    # #8 and #9 own cheap transcript-tail triggers; they run on every call.
    if transcript:
        run.do(watch.watch_progress, transcript, task)
        run.do(watch.check_thinking, transcript)


def _last_test_evidence(transcript):
    """The most recent test-looking Bash command and its output, from the transcript."""
    evidence = {}
    try:
        with open(transcript, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - 2_000_000))
            lines = fh.read().decode("utf-8", errors="replace").splitlines()
    except OSError:
        return evidence
    commands = {}
    for raw in lines:
        try:
            rec = json.loads(raw)
        except ValueError:
            continue
        content = (rec.get("message") or {}).get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use" and block.get("name") == "Bash":
                cmd = str((block.get("input") or {}).get("command", ""))
                if TEST_COMMAND.search(cmd):
                    commands[block.get("id")] = cmd
            elif block.get("type") == "tool_result" and block.get("tool_use_id") in commands:
                evidence = {"test_command": commands[block["tool_use_id"]],
                            "test_output": _text(block.get("content"))[-6000:],
                            "test_failed": bool(block.get("is_error"))}
    return evidence


def stop(payload, run):
    transcript = payload.get("transcript_path") or ""
    final = str(payload.get("last_assistant_message") or "")
    cwd = payload.get("cwd") or os.getcwd()
    root = _git_root(cwd)
    task = _task(transcript)
    if not final and transcript:
        try:
            watch = _lib("jev_session_watch")
            final = watch.last_assistant_text(transcript) or ""
        except Exception:
            final = ""
    done = _lib("jev_done_checks")
    evidence = _last_test_evidence(transcript) if transcript else {}
    diff = ""
    if root:
        try:
            diff = subprocess.run(["git", "diff", "HEAD", "-U3"], cwd=root, capture_output=True,
                                  text=True, timeout=10).stdout[:40000]
            evidence["diff_stat"] = subprocess.run(["git", "diff", "HEAD", "--stat"], cwd=root,
                                                   capture_output=True, text=True, timeout=10).stdout[-3000:]
        except Exception:
            pass
    if final:
        run.do(done.check_done_claim, final, evidence)
    if diff.strip():
        run.do(done.triage_review, diff, task)


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    if MODE == "off" or payload.get("session_id") == "selftest":
        return 0
    event = payload.get("hook_event_name") or payload.get("hookEventName") or ""
    run = Run()
    try:
        if event == "PostToolUse":
            post_tool_use(payload, run)
        elif event == "Stop":
            if payload.get("stop_hook_active"):
                return 0
            stop(payload, run)
    except Exception:
        return 0
    if MODE != "advise":
        return 0
    lines = [_line(r) for r in run.results if _notable(r)]
    if not lines:
        return 0
    text = "\n".join(lines[:4])
    if event == "PostToolUse":
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                                 "additionalContext": text}}))
    else:
        print(json.dumps({"systemMessage": text}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
