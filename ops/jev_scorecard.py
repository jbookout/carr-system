"""jev_scorecard.py — a repeatable harness for the local flash model (#25).

WHAT THIS IS FOR. Every other check in this family (ops/jev_best_of.py,
ops/jev_notebook.py, and the four already sealed — jev_rule_select.py,
jev_precheck.py, jev_code_review.py, jev_requirements.py) makes a claim about
how well a judgment performs. A claim like that decays the moment the local
model, the prompt, or the judge's own prompts change, unless something reruns
the SAME suite of tasks the SAME way and reports the SAME numbers back. This
module is that something: a library, not a one-off script, so a runner
(written elsewhere, per the brief this was built from) can call it on a
schedule or after a model swap and get a comparable answer each time.

THE SUITE IS THE ONE FROM THE EXPERIMENT THAT PROVED THIS FAMILY OF CHECKS.
ops/config/flash-scorecard-tasks.v1.json converts the 16 held-out coding tasks
in the session scratchpad's jevx/tasks/ — the run that measured ops/jev_best_of
.py's numbers — into data: id, category, lang, prompt, and either a hidden
`test` (most tasks: implement a function, hidden tests check it) or, for the
one write-tests task, `impl` plus `mutants` (write a test suite; it must pass
the correct implementation and kill every mutant). Grading mirrors that
experiment's lib/grade.py harness exactly, because a scorecard that grades
differently from the run it is meant to be comparable to is not a scorecard.

TWO KINDS OF GRADING, AND ONLY ONE OF THEM TOUCHES JEV. run_task() is entirely
deterministic: it calls the local OpenAI-compatible server, runs the
candidate's actual code against the task's actual hidden tests in a temp
directory, and reports a real exit code — the same "keep verifiable facts in
code" doctrine ops/jev_best_of.py's prefilter applies. grade_fuzzy() is for the
DIFFERENT, narrower job of scoring free-text output against qualitative
sub-checks nothing can subprocess-run ("does this explanation mention X", "is
this tone appropriate") — several independent yes/no facts about the SAME
output, which per ops/jev_judge.py's docstring is one Noul per fact, batched
into ONE request, never a request per fact and never one broad question asked
to cover several judgments at once.

IT IS A LIBRARY AND MUST STAY ONE. No shebang and no main guard: either turns a
.py file into a registered script entrypoint in the sealed source inventory,
moves the frontier, and owes a forward-only registry successor. The detector is
a regex over the whole file with no notion of docstrings, so the construct is
described here and never spelled. ops/typesafe_client.py carries the long form.
The runner CLI that drives this library is somebody else's file, not this one.
"""

import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_SUITE = os.path.join(REPO, "ops", "config", "flash-scorecard-tasks.v1.json")

DEFAULT_ENDPOINT = "http://127.0.0.1:8000"
DEFAULT_MODEL = "qwen3.8-flash-next"
DEFAULT_TIMEOUT = 600.0
DEFAULT_REASONING_EFFORT = "low"

# Harnesses copied from the experiment's own lib/grade.py so grading here is
# comparable to the run that measured this family of checks, not a
# reimplementation that happens to look similar.
_PY_HEADER = '''
import sys
_fails = []; _n = [0]
def check(name, fn):
    _n[0] += 1
    try:
        r = fn()
        if r is False: raise AssertionError("returned False")
    except BaseException as e:
        if isinstance(e, KeyboardInterrupt): raise
        _fails.append(f"{name}: {type(e).__name__}: {e}"[:400])
def raises(exc, fn):
    try:
        fn()
    except exc:
        return True
    except Exception as e:
        raise AssertionError(f"expected {exc.__name__}, got {type(e).__name__}: {e}")
    raise AssertionError(f"expected {exc.__name__}, nothing raised")
'''
_PY_FOOTER = '''
print(f"PASSED {_n[0]-len(_fails)}/{_n[0]}")
for f in _fails: print("FAIL", f)
sys.exit(1 if _fails else 0)
'''
_JS_HEADER = '''
let __n = 0; const __fails = [];
function check(name, fn) { __n++; try { const r = fn(); if (r === false) throw new Error("returned false"); } catch (e) { __fails.push(name + ": " + String((e && e.message) || e).slice(0, 300)); } }
function raises(fn) { try { fn(); } catch (e) { return true; } throw new Error("expected throw, nothing thrown"); }
'''
_JS_FOOTER = '''
console.log(`PASSED ${__n - __fails.length}/${__n}`); for (const f of __fails) console.log("FAIL", f); process.exit(__fails.length ? 1 : 0);
'''

_CODE_BLOCK = re.compile(r"```([a-zA-Z0-9_+-]*)\n(.*?)```", re.S)
_THINK = re.compile(r"<think>.*?</think>", re.S)


def _sibling(name):
    """Load a sibling ops/ module by path. ops/ is not a package, and the whole
    point of these files is that they carry no entrypoint, so there is nothing
    to import them as. Same plumbing jev_judge uses to reach typesafe_client."""
    path = os.path.join(REPO, "ops", f"{name}.py")
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - import plumbing
        raise RuntimeError(f"cannot load ops/{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_suite(path=DEFAULT_SUITE):
    """The task list from `path`. Returns a plain list of dicts."""
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    return data["tasks"] if isinstance(data, dict) else list(data)


def extract_code(text, lang=None):
    """The largest fenced code block, preferring one tagged with `lang`.

    Same heuristic the experiment's own model_client.extract_code used:
    strip a <think> block, then take the biggest fenced block (a low-effort
    local model sometimes emits a short throwaway snippet before the real
    answer, and length is a cheap, deterministic tiebreak).
    """
    text = _THINK.sub("", text or "")
    blocks = _CODE_BLOCK.findall(text)
    if not blocks:
        return text.strip()
    if lang:
        target = lang.lower()
        # Matched both directions so a task's short lang code ("py", "js")
        # matches a model's full fence tag ("python", "javascript") and a
        # full lang name matches a short tag, without hardcoding either
        # spelling.
        preferred = [body for tag, body in blocks
                     if tag and (tag.lower() in target or target in tag.lower())]
        if preferred:
            return max(preferred, key=len)
    return max((body for _, body in blocks), key=len)


def _chat(messages, *, endpoint, model, temperature, reasoning_effort, max_tokens,
          timeout, opener=None):
    """One call to the local OpenAI-compatible /v1/chat/completions endpoint.

    Never raises: HTTP and connection failures come back as
    {"content": "", "error": "..."} so a caller can grade a missing response
    as a failed attempt instead of crashing the whole suite over one call.
    `opener` is for the offline selftest and is not used in production, same
    convention as ops/typesafe_client.py's `ask(..., opener=...)`.
    """
    import urllib.error
    import urllib.request

    body = {"model": model, "messages": messages, "temperature": temperature,
             "max_tokens": max_tokens, "reasoning_effort": reasoning_effort}
    request = urllib.request.Request(
        endpoint.rstrip("/") + "/v1/chat/completions",
        data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json"})
    send = opener or urllib.request.urlopen
    try:
        with send(request, timeout=timeout) as response:
            resp = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        detail = ""
        try:
            detail = err.read().decode("utf-8", "replace")[:400]
        except Exception:
            pass
        return {"content": "", "usage": {}, "error": f"HTTP {err.code}: {detail}"}
    except Exception as exc:
        return {"content": "", "usage": {}, "error": f"{type(exc).__name__}: {exc}"}
    choice = (resp.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    return {"content": msg.get("content") or "", "usage": resp.get("usage", {}), "error": None}


def _run(cmd, cwd, timeout):
    try:
        result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return result.returncode, (result.stdout + result.stderr)[-3000:]
    except subprocess.TimeoutExpired:
        return -9, "TIMEOUT"


def _grade_impl(task, code, workdir, timeout):
    """Run a candidate implementation against the task's hidden `test` source."""
    lang = task.get("lang", "py")
    if lang == "js":
        with open(os.path.join(workdir, "solution.js"), "w", encoding="utf-8") as handle:
            handle.write(code)
        src = _JS_HEADER + task["test"] + _JS_FOOTER
        with open(os.path.join(workdir, "test_hidden.js"), "w", encoding="utf-8") as handle:
            handle.write(src)
        rc, out = _run(["node", "test_hidden.js"], workdir, timeout)
    else:
        with open(os.path.join(workdir, "solution.py"), "w", encoding="utf-8") as handle:
            handle.write(code)
        src = _PY_HEADER + "from solution import *\n" + task["test"] + _PY_FOOTER
        with open(os.path.join(workdir, "test_hidden.py"), "w", encoding="utf-8") as handle:
            handle.write(src)
        rc, out = _run([sys.executable, "test_hidden.py"], workdir, timeout)
    scoreline = next((line for line in out.splitlines() if line.startswith("PASSED")), None)
    return {"pass": rc == 0, "rc": rc, "subtests": scoreline or "no-score (crash/import error)",
            "detail": out}


def _grade_mutation(task, test_code, workdir, timeout):
    """Run a candidate TEST SUITE against the reference impl and every mutant.

    Passes only if it runs clean against the correct implementation AND kills
    every mutant (fails on it). Mirrors the experiment's mutation grading so a
    write-tests task is scored the same way here as it was when this family of
    checks was measured.
    """
    correct_dir = os.path.join(workdir, "correct")
    os.makedirs(correct_dir, exist_ok=True)
    with open(os.path.join(correct_dir, "solution.py"), "w", encoding="utf-8") as handle:
        handle.write(task["impl"])
    with open(os.path.join(correct_dir, "test_solution.py"), "w", encoding="utf-8") as handle:
        handle.write(test_code)
    rc, out = _run([sys.executable, "-m", "unittest", "-q", "test_solution"], correct_dir, timeout)
    correct_passes = rc == 0

    killed = 0
    mutants = task.get("mutants", [])
    for i, mutant_src in enumerate(mutants):
        mutant_dir = os.path.join(workdir, f"mutant{i}")
        os.makedirs(mutant_dir, exist_ok=True)
        with open(os.path.join(mutant_dir, "solution.py"), "w", encoding="utf-8") as handle:
            handle.write(mutant_src)
        with open(os.path.join(mutant_dir, "test_solution.py"), "w", encoding="utf-8") as handle:
            handle.write(test_code)
        mrc, _ = _run([sys.executable, "-m", "unittest", "-q", "test_solution"], mutant_dir, timeout)
        killed += mrc != 0

    passed = correct_passes and (not mutants or killed == len(mutants))
    return {"pass": passed, "correct_passes": correct_passes,
            "killed": f"{killed}/{len(mutants)}", "detail": out}


def grade_candidate(task, code, *, workdir=None, timeout=60):
    """Grade one candidate against `task`, deterministically. No Jev involved.

    Dispatches on task.get("kind", "impl"): "mutation" tasks grade `code` as a
    test suite (see _grade_mutation); anything else grades it as an
    implementation run against the task's hidden test (see _grade_impl).
    """
    def _do(directory):
        if task.get("kind") == "mutation":
            return _grade_mutation(task, code, directory, timeout)
        return _grade_impl(task, code, directory, timeout)

    if workdir is not None:
        os.makedirs(workdir, exist_ok=True)
        return _do(workdir)
    with tempfile.TemporaryDirectory(prefix="jev-scorecard-") as directory:
        return _do(directory)


def run_task(task, *, endpoint=DEFAULT_ENDPOINT, model=DEFAULT_MODEL, attempts=1,
             timeout=DEFAULT_TIMEOUT, temperature=0.0,
             reasoning_effort=DEFAULT_REASONING_EFFORT, max_tokens=16384,
             chat_opener=None):
    """Run `task` against the local flash server `attempts` times and grade each.

    Each attempt is generated with low reasoning effort at temperature 0 by
    default — the setting the experiment measured this family of checks
    against (jevx/lib/model_client.py) — and graded deterministically by
    running its code against the task's real hidden tests, never by asking
    Jev whether it looks right.

    Returns {"id", "category", "kind", "attempts": [{"pass", ...,
    "chat_error"}], "any_pass": bool, "first_pass": bool}. NEVER raises: a
    chat or grading failure for one attempt is recorded on that attempt
    (pass=False, chat_error/grade_error set) rather than aborting the task.
    """
    attempt_results = []
    for _ in range(max(1, attempts)):
        reply = _chat(
            [{"role": "user", "content": task["prompt"]}],
            endpoint=endpoint, model=model, temperature=temperature,
            reasoning_effort=reasoning_effort, max_tokens=max_tokens,
            timeout=timeout, opener=chat_opener)
        if reply.get("error"):
            attempt_results.append({"pass": False, "chat_error": reply["error"]})
            continue
        code = extract_code(reply["content"], lang=task.get("lang"))
        try:
            grade = grade_candidate(task, code, timeout=min(60, timeout))
        except Exception as exc:
            attempt_results.append({"pass": False, "grade_error": f"{type(exc).__name__}: {exc}",
                                     "usage": reply.get("usage")})
            continue
        attempt_results.append({**grade, "usage": reply.get("usage")})

    return {
        "id": task.get("id"),
        "category": task.get("category", "uncategorized"),
        "kind": task.get("kind", "impl"),
        "attempts": attempt_results,
        "any_pass": any(a.get("pass") for a in attempt_results),
        "first_pass": bool(attempt_results) and bool(attempt_results[0].get("pass")),
    }


def grade_fuzzy(output, subchecks, *, client=None, judge=None):
    """Score free-text `output` against `subchecks`, each an independent yes/no.

    One Noul per sub-check, ALL batched into a single Jev request — several
    independent facts about the same subject, per ops/jev_judge.py's
    doctrine, never one request per sub-check and never a single broad
    question standing in for several.

    Returns {"check": "scorecard_fuzzy", "verdict": bool | "unavailable",
    "confidence": None, "escalate": bool,
    "detail": {"subchecks": {text: probability|None, ...}}}. `verdict` is
    True only when every sub-check clears 0.5; a Noul carries no separate
    confidence (see ops/typesafe_client.py's noul() docstring), so escalate is
    set instead whenever any sub-check lands in the ambiguous middle
    (0.35-0.65) where yes and no are close to equally likely. NEVER raises.
    """
    judge = judge or _sibling("jev_judge")
    subchecks = list(subchecks)
    if not subchecks:
        return {"check": "scorecard_fuzzy", "verdict": True, "confidence": None,
                "escalate": False, "detail": {"subchecks": {}, "reason": "no sub-checks given"}}

    tsc = client or _sibling("typesafe_client")
    questions = {
        f"c{i}": tsc.noul(
            f"Does `state.output` satisfy this: {subcheck}",
            true="state.output clearly satisfies this",
            false="state.output does not satisfy this, or it is absent")
        for i, subcheck in enumerate(subchecks)
    }
    subject = {"output": (output or "")[:8000]}
    try:
        answer = judge.judge(subject, questions, client=client)
    except Exception as exc:
        try:
            judge.record("supervise.scorecard_fuzzy", (output or "")[:200], None, None, error=exc)
        except Exception:
            pass
        return {"check": "scorecard_fuzzy", "verdict": "unavailable", "confidence": None,
                "escalate": True, "detail": {"reason": f"{type(exc).__name__}: {exc}",
                                               "subchecks": {s: None for s in subchecks}}}

    probs = {}
    for i, subcheck in enumerate(subchecks):
        probs[subcheck] = float(answer["answers"][f"c{i}"]["noul"])
    verdict = all(p >= 0.5 for p in probs.values())
    escalate = any(0.35 <= p <= 0.65 for p in probs.values())
    judge.record("supervise.scorecard_fuzzy", (output or "")[:200], answer, existing_decision=None)
    return {"check": "scorecard_fuzzy", "verdict": verdict, "confidence": None,
            "escalate": escalate, "detail": {"subchecks": probs, "model": answer.get("model")}}


def summarize(results):
    """Pass counts by category, plus an overall row. `results` is run_task() output.

    Returns {"by_category": {category: {"n", "any_pass", "first_pass"}},
    "overall": {"n", "any_pass", "first_pass"}}. Pure arithmetic over facts
    run_task already produced — no judgment, no Jev, per the "keep arithmetic
    in code" doctrine ops/typesafe_client.py states directly.
    """
    by_category = {}
    for row in results:
        bucket = by_category.setdefault(row.get("category", "uncategorized"),
                                          {"n": 0, "any_pass": 0, "first_pass": 0})
        bucket["n"] += 1
        bucket["any_pass"] += int(bool(row.get("any_pass")))
        bucket["first_pass"] += int(bool(row.get("first_pass")))
    overall = {"n": sum(b["n"] for b in by_category.values()),
               "any_pass": sum(b["any_pass"] for b in by_category.values()),
               "first_pass": sum(b["first_pass"] for b in by_category.values())}
    return {"by_category": by_category, "overall": overall}
