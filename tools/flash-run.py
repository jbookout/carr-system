#!/usr/bin/env python3
"""flash-run.py — run one coding task on the local Flash-Next model, supervised by Jev.

WHY THIS EXISTS. The 2026-09-23 stress test made Qwen3.8-Flash-Next the everyday
coding workhorse (decision ab0dc622) and the Jev experiment that followed found
what makes it reliable (decision cb56f652): several low-effort attempts, the
tests run against each, and ONE Jev choice over the candidates with that
evidence in named state fields. With evidence the raw best-of-3 went 16/16.
The generic 0.6 confidence gate was wrong for it: it fell back to attempt 1 and
lost. This file is that finding turned into the command Joe actually runs.

THE FLOW for `flash-run "<task>" --test "<command>"`:
  1. intake (ops/jev_intake.py): the ambiguity stop (#3), the escalation router
     (#5), the effort picker (#2), the context picker (#1), the worked-example
     picker (#23), and the mistake notebook's recall (#15).
  2. N attempts (default 3 with a test command, else 1), each in its own copy of
     the working tree, by the `flash` launcher. The in-session checks ride along
     through hooks/jev-supervisor.py in advise mode (~/.claude-local settings).
  3. the tests run in every copy; ops/jev_best_of.py picks one candidate or
     "none". A single passing candidate is taken without asking Jev.
  4. the chosen patch is applied to the real tree and the tests run again there.
     Review triage (#17) scores the risk of the change.
  5. on failure: the mistake is written to the notebook and a handoff pack (#10)
     is written for Claude/Codex; --escalate auto sends it to `claude -p`.

Every run appends one row to out/flash-runs.jsonl, the real-use record the week
of tracking reads (`flash-run stats`).

OTHER SUBCOMMANDS
  flash-run plan <file>        route each step of a plan local/escalate (#18)
  flash-run scorecard          the standing model scorecard (#25)
  flash-run stats              summarize out/flash-runs.jsonl
  flash-run note "<what>" --fix "<fix>"   add to the mistake notebook by hand

Exit codes: 0 applied and tests pass (or no test given and an attempt ran),
3 the task is ambiguous, 4 routed or escalated away from the local model,
5 no candidate was good enough, 2 usage or environment problem.
"""
import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(REPO, "out")
RUNS_LOG = os.path.join(OUT, "flash-runs.jsonl")
EXAMPLES_LOG = os.path.join(OUT, "flash-examples.jsonl")
HANDOFF_DIR = os.path.join(OUT, "flash-handoffs")
FLASH = os.environ.get("FLASH_BIN") or shutil.which("flash") or os.path.expanduser("~/.local/bin/flash")
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".mypy_cache", "out", "dist", "build"}
ATTEMPT_TIMEOUT = 1500
TEST_TIMEOUT = 600


def _lib(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(REPO, "ops", f"{name}.py"))
    if spec is None or spec.loader is None:
        raise ImportError(name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _now():
    return datetime.now(timezone.utc).isoformat()


def _append(path, row):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
    except OSError:
        pass


def _say(msg):
    print(f"flash-run: {msg}", flush=True)


def _sh(cmd, cwd, timeout):
    try:
        done = subprocess.run(cmd, cwd=cwd, shell=isinstance(cmd, str), capture_output=True,
                              text=True, timeout=timeout)
        return done.returncode, (done.stdout + done.stderr)
    except subprocess.TimeoutExpired as exc:
        partial = (exc.stdout or b"") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        return 124, f"timed out after {timeout}s\n{partial if isinstance(partial, str) else ''}"


def _tracked_files(cwd):
    code, out = _sh(["git", "ls-files", "-co", "--exclude-standard", "-z"], cwd, 60)
    if code == 0 and out:
        return [p for p in out.split("\0") if p and os.path.isfile(os.path.join(cwd, p))]
    files = []
    for base, dirs, names in os.walk(cwd):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in names:
            files.append(os.path.relpath(os.path.join(base, name), cwd))
    return files


def make_copy(cwd, dest):
    """A throwaway copy of the working tree with a baseline commit, so the attempt's
    change can be read back as a patch no matter what state the real tree is in."""
    for rel in _tracked_files(cwd):
        src = os.path.join(cwd, rel)
        dst = os.path.join(dest, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst, follow_symlinks=False)
    venv = os.path.join(cwd, ".venv")
    if os.path.isdir(venv):
        os.symlink(venv, os.path.join(dest, ".venv"))
    for args in (["git", "init", "-q"], ["git", "add", "-A"],
                 ["git", "-c", "user.email=flash@local", "-c", "user.name=flash",
                  "commit", "-q", "--no-verify", "-m", "baseline"]):
        _sh(args, dest, 120)


def read_patch(dest):
    _sh(["git", "add", "-A", "--", ".", ":(exclude).venv"], dest, 120)
    _, patch = _sh(["git", "diff", "--cached", "--binary"], dest, 120)
    return patch


def build_prompt(task, test_cmd, context_files, recalled, example):
    parts = [task.strip()]
    if context_files:
        parts.append("Files most likely involved: " + ", ".join(context_files))
    if example:
        parts.append("A similar task solved before:\n" + example)
    if recalled:
        parts.append("Mistakes made on similar tasks before — avoid them:\n" + "\n".join(recalled))
    if test_cmd:
        parts.append(f"Verify with: {test_cmd}\nRun it, fix until it passes, then stop. "
                     "Keep the change small; do not edit the tests to make them pass.")
    else:
        parts.append("Keep the change small and focused on the task.")
    return "\n\n".join(parts)


def run_attempt(n, cwd, prompt, test_cmd, effort, workdir):
    dest = os.path.join(workdir, f"attempt-{n}")
    os.makedirs(dest)
    make_copy(cwd, dest)
    allowed = ["Read", "Edit", "Write", "Glob", "Grep",
               "Bash(ls:*)", "Bash(cat:*)", "Bash(grep:*)", "Bash(rg:*)", "Bash(git diff:*)",
               "Bash(git status:*)", "Bash(python3:*)", "Bash(node:*)"]
    if test_cmd:
        allowed.append(f"Bash({test_cmd})")
    started = time.monotonic()
    code, transcript = _sh([FLASH, "-p", prompt, "--effort", effort or "low",
                            "--permission-mode", "acceptEdits",
                            "--allowedTools", *allowed], dest, ATTEMPT_TIMEOUT)
    elapsed = round(time.monotonic() - started, 1)
    test_code, test_out = (None, "")
    if test_cmd:
        test_code, test_out = _sh(test_cmd, dest, TEST_TIMEOUT)
    patch = read_patch(dest)
    return {"id": f"attempt-{n}", "code_or_diff": patch[:20000], "patch": patch,
            "test_output": test_out[-6000:], "test_exit_code": test_code,
            "probe_results": {"agent_exit_code": code, "patch_lines": patch.count("\n"),
                              "tests_passed": test_code == 0 if test_cmd else None},
            "agent_output": transcript[-3000:], "elapsed_s": elapsed}


def apply_patch(cwd, patch):
    if not patch.strip():
        return False, "empty patch"
    with tempfile.NamedTemporaryFile("w", suffix=".patch", delete=False) as fh:
        fh.write(patch)
        name = fh.name
    try:
        code, out = _sh(["git", "apply", "--whitespace=nowarn", name], cwd, 120)
        return code == 0, out
    finally:
        os.unlink(name)


def escalate(task, cwd, run_id, failure, changed, mode):
    done = _lib("jev_done_checks")
    pack = done.build_handoff(task, "", changed, failure_output=failure)
    text = pack.get("pack") if isinstance(pack, dict) else None
    if not text:
        text = f"Task:\n{task}\n\nLast failure:\n{(failure or '')[-4000:]}"
    os.makedirs(HANDOFF_DIR, exist_ok=True)
    path = os.path.join(HANDOFF_DIR, f"{run_id}.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    _say(f"handoff pack for Claude/Codex: {path}")
    if mode == "auto" and shutil.which("claude"):
        _say("escalating to Claude (claude -p) with the handoff pack")
        subprocess.run(["claude", "-p", text, "--permission-mode", "acceptEdits"], cwd=cwd)
    return path


def cmd_run(a):
    cwd = os.path.abspath(a.cwd)
    task = a.task
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    row = {"run_id": run_id, "at": _now(), "cwd": cwd, "task": task[:2000], "test": a.test}
    intake = _lib("jev_intake")
    notebook = _lib("jev_notebook")

    amb = intake.check_ambiguity(task)
    row["ambiguity"] = amb.get("verdict")
    if amb.get("verdict") == "ambiguous" and not a.force:
        _say("the task looks ambiguous: " + json.dumps(amb.get("detail"))[:600])
        _say("clarify it, or pass --force to run anyway")
        row["outcome"] = "ambiguous"
        _append(RUNS_LOG, row)
        return 3

    files = [f for f in _tracked_files(cwd)][:4000]
    route = intake.route_task(task, files=None, has_tests=bool(a.test))
    row["route"] = route.get("verdict")
    if route.get("verdict") == "escalate" and not a.force:
        _say("routed away from the local model: " + json.dumps(route.get("detail"))[:600])
        row["outcome"] = "routed_escalate"
        row["handoff"] = escalate(task, cwd, run_id, None, [], a.escalate)
        _append(RUNS_LOG, row)
        return 4

    effort = a.effort or intake.pick_effort(task).get("verdict")
    if effort not in ("low", "medium", "high"):
        effort = "low"
    ctx = intake.pick_context(task, cwd)
    context_files = (ctx.get("detail") or {}).get("paths") or ctx.get("paths") or []
    recall = notebook.recall_mistakes(task)
    recalled = (recall.get("detail") or {}).get("lines") or recall.get("lines") or []
    example_text = None
    examples = _read_examples()
    if examples:
        pick = intake.pick_example(task, examples)
        chosen = (pick.get("detail") or {}).get("example_id")
        for ex in examples:
            if ex.get("id") == chosen:
                example_text = f"{ex.get('title')}\n{ex.get('summary')}"
    row.update(effort=effort, context=context_files[:10], recalled=len(recalled))

    attempts = a.attempts or (3 if a.test else 1)
    prompt = build_prompt(task, a.test, context_files[:8], recalled[:3], example_text)
    workdir = tempfile.mkdtemp(prefix=f"flash-run-{run_id}-")
    candidates = []
    try:
        for n in range(1, attempts + 1):
            _say(f"attempt {n}/{attempts} (effort {effort})")
            cand = run_attempt(n, cwd, prompt, a.test, effort, workdir)
            candidates.append(cand)
            status = "no test" if cand["test_exit_code"] is None else (
                "tests pass" if cand["test_exit_code"] == 0 else f"tests fail ({cand['test_exit_code']})")
            _say(f"  attempt {n}: {status}, {cand['probe_results']['patch_lines']} patch lines, "
                 f"{cand['elapsed_s']}s")
            if a.stop_on_pass and cand["test_exit_code"] == 0:
                break
        best = _lib("jev_best_of").select_candidate(
            task, [{k: c[k] for k in ("id", "code_or_diff", "probe_results", "test_output",
                                      "test_exit_code")} for c in candidates])
        chosen_id = best.get("verdict")
        row["selection"] = {"verdict": chosen_id, "confidence": best.get("confidence"),
                            "escalate": best.get("escalate"),
                            "attempts": [{"id": c["id"], "test_exit_code": c["test_exit_code"],
                                          "elapsed_s": c["elapsed_s"]} for c in candidates]}
        chosen = next((c for c in candidates if c["id"] == chosen_id), None)
        if chosen is None and attempts == 1 and candidates and candidates[0]["patch"].strip() \
                and not a.test:
            chosen = candidates[0]
        if chosen is None or not chosen["patch"].strip():
            failure = candidates[-1]["test_output"] if candidates else ""
            _say("no attempt was good enough")
            notebook.record_mistake("no_candidate", task,
                                    f"{attempts} attempts; last failure: {failure[-500:]}",
                                    "escalated", source=f"flash-run {run_id}")
            row["outcome"] = "no_candidate"
            row["handoff"] = escalate(task, cwd, run_id, failure, [], a.escalate)
            _append(RUNS_LOG, row)
            return 5

        if a.dry_run:
            print(chosen["patch"])
            row["outcome"] = "dry_run"
            _append(RUNS_LOG, row)
            return 0
        ok, msg = apply_patch(cwd, chosen["patch"])
        if not ok:
            _say(f"could not apply the chosen patch: {msg[:400]}")
            row["outcome"] = "apply_failed"
            _append(RUNS_LOG, row)
            return 5
        _say(f"applied {chosen['id']}")
        final_code = None
        if a.test:
            final_code, final_out = _sh(a.test, cwd, TEST_TIMEOUT)
            _say("tests pass in the real tree" if final_code == 0
                 else f"tests FAIL in the real tree ({final_code})\n{final_out[-1500:]}")
        review = _lib("jev_done_checks").triage_review(chosen["patch"][:40000], task)
        row["review"] = {"verdict": review.get("verdict"),
                         "advice": (review.get("detail") or {}).get("advice")}
        if review.get("verdict") == "needs_review":
            _say("review triage: this change touches risky code — have Claude review it: "
                 + str((review.get("detail") or {}).get("advice") or ""))
        row["outcome"] = "applied_pass" if final_code in (0, None) else "applied_fail"
        if final_code == 0:
            _append(EXAMPLES_LOG, {"id": run_id, "title": task[:120],
                                   "summary": chosen["patch"][:1500], "at": _now()})
        elif final_code is not None:
            notebook.record_mistake("applied_but_failing", task, final_out[-800:],
                                    "needs follow-up", source=f"flash-run {run_id}")
        _append(RUNS_LOG, row)
        return 0 if final_code in (0, None) else 5
    finally:
        if not a.keep:
            shutil.rmtree(workdir, ignore_errors=True)
        else:
            _say(f"attempt copies kept in {workdir}")


def _read_examples(limit=200):
    rows = []
    try:
        with open(EXAMPLES_LOG, encoding="utf-8") as fh:
            for line in fh:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        return []
    return rows[-limit:]


def cmd_plan(a):
    with open(a.file, encoding="utf-8") as fh:
        steps = [ln.strip().lstrip("-*0123456789. ").strip() for ln in fh
                 if ln.strip() and not ln.lstrip().startswith("#")]
    result = _lib("jev_intake").split_plan(steps)
    for step in (result.get("detail") or {}).get("steps") or result.get("steps") or []:
        print(f"{step.get('route', '?'):9} {step.get('difficulty', '')!s:6} {step.get('step', '')[:100]}")
    return 0


def cmd_scorecard(a):
    sc = _lib("jev_scorecard")
    suite = sc.load_suite(a.suite)
    results = []
    for task in suite:
        if a.only and task.get("id") not in a.only:
            continue
        _say(f"scorecard task {task.get('id')}")
        results.append(sc.run_task(task, attempts=a.attempts))
    summary = sc.summarize(results)
    print(json.dumps(summary, indent=2))
    _append(os.path.join(OUT, "flash-scorecard.jsonl"), {"at": _now(), "summary": summary})
    return 0


def cmd_stats(a):
    rows = []
    try:
        with open(RUNS_LOG, encoding="utf-8") as fh:
            rows = [json.loads(line) for line in fh if line.strip()]
    except OSError:
        pass
    if a.since:
        rows = [r for r in rows if r.get("at", "") >= a.since]
    counts = {}
    for r in rows:
        counts[r.get("outcome", "?")] = counts.get(r.get("outcome", "?"), 0) + 1
    print(json.dumps({"runs": len(rows), "outcomes": counts,
                      "local_success_rate": round(counts.get("applied_pass", 0) / len(rows), 3)
                      if rows else None}, indent=2))
    return 0


def cmd_note(a):
    _lib("jev_notebook").record_mistake(a.kind, a.task or "", a.what, a.fix or "", source="manual")
    _say("noted")
    return 0


def main(argv):
    p = argparse.ArgumentParser(prog="flash-run", description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd")
    r = sub.add_parser("run", help="run one task (default)")
    r.add_argument("task")
    r.add_argument("--cwd", default=".")
    r.add_argument("--test", default=None, help="the command that proves the task is done")
    r.add_argument("--attempts", type=int, default=None)
    r.add_argument("--effort", choices=["low", "medium", "high"], default=None)
    r.add_argument("--escalate", choices=["suggest", "auto"], default="suggest")
    r.add_argument("--stop-on-pass", action="store_true",
                   help="stop at the first attempt whose tests pass (faster, less choice)")
    r.add_argument("--force", action="store_true", help="run despite an ambiguity or routing stop")
    r.add_argument("--dry-run", action="store_true", help="print the chosen patch, do not apply")
    r.add_argument("--keep", action="store_true", help="keep the attempt copies")
    pl = sub.add_parser("plan")
    pl.add_argument("file")
    s = sub.add_parser("scorecard")
    s.add_argument("--suite", default=os.path.join(REPO, "ops", "config", "flash-scorecard-tasks.v1.json"))
    s.add_argument("--attempts", type=int, default=1)
    s.add_argument("--only", nargs="*")
    st = sub.add_parser("stats")
    st.add_argument("--since", default=None)
    n = sub.add_parser("note")
    n.add_argument("what")
    n.add_argument("--fix", default="")
    n.add_argument("--kind", default="manual")
    n.add_argument("--task", default="")
    known = {"run", "plan", "scorecard", "stats", "note", "-h", "--help"}
    if argv and argv[0] not in known:
        argv = ["run", *argv]
    a = p.parse_args(argv)
    if not a.cmd:
        p.print_help()
        return 2
    if a.cmd == "run" and not os.path.exists(FLASH):
        _say(f"the flash launcher is missing ({FLASH})")
        return 2
    return {"run": cmd_run, "plan": cmd_plan, "scorecard": cmd_scorecard,
            "stats": cmd_stats, "note": cmd_note}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
