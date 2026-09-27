"""The flash-local desk: a Model Room desk that answers on the local Flash model itself.

Until 2026-09-24 the queue could reach Claude and Codex desks and Joe, but nothing ran work on Flash: the desk
registered as "flash" was a Claude session with that name. Joe's routing rulings (decisions 81f6bcf7, 79110363) send
direct questions to Flash first, so Flash needs a desk the queue can hand a task to.

This desk runs Flash's direct protocol from ops/config/model-routes.v1.json: one reply, thinking off, a bounded
answer length. It is synchronous like a codex-session desk and returns the same outcome shape, so bridge.deliver and
the queue executor treat it the same way. Script work comes here too (2026-09-26): a task that names its data with
`data: <path>` lines runs tools/flash-script.py, Flash's sandboxed script protocol, instead of one direct reply. Only
data under the policy's `script_data_roots` is accepted, and nothing inside it may be a symbolic link, because the
harness copies what it is given and the answer is posted to the room: a path that could reach a key or a credential
never gets that far.

So does code work (2026-09-26): a task whose body names `project: <path>` and one `test: <command>` runs
tools/flash-run.py, Flash's coding protocol. The project must be the top of a git work tree strictly inside the
policy's `code_project_roots` and not a link. The test line is chat text, so it never reaches a shell as written:
test_argv accepts only pytest, `python3 <script inside the project>.py` or `node --test`, with plain tokens and
paths that stay inside the project. flash-run never touches the project's own tree: it works in a throwaway git
worktree on a new flash/queue-* branch, with --escalate suggest so the desk never dispatches another desk. The desk
re-runs the test on the patch itself, and only then commits on that branch and reports it (success); anything else
drops the worktree and the branch (blocked). Nothing is merged or pushed; the whole run is bounded by CODE_TIMEOUT_S.

A reply with no answer comes back as status "failed" with detail "no_answer", so the task is blocked visibly
rather than completed empty. ops/config/model-routes.v1.json names the route's `then` desk (Opus, via
claude-desktop) as where a hand-off would go next, but the queue path (queue_dispatch.py) does not dispatch
there today: a Hermes queue task's CARR_QUEUE_META.target is fixed in the task body at creation and checked
against the claiming desk's own alias, so moving a task to a different desk needs a new, linked task rather
than a reassignment of this one. Until that lands, a no-answer or malformed-protocol reply here only retries
on the SAME flash-local desk, under its own diagnosable code, and then blocks.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

FLASH_URL = os.environ.get("CARR_FLASH_URL", "http://127.0.0.1:8000")
FLASH_MODEL = os.environ.get("CARR_FLASH_MODEL", "qwen3.8-flash-next")
MAX_TOKENS = 4096
TIMEOUT_S = 600.0
HEALTH_TIMEOUT_S = 2.0
# Under the queue's 900 s claim, so a running script task is never handed out a second time, and no longer than a
# direct turn holds the bridge (TIMEOUT_S).
SCRIPT_TIMEOUT_S = 600.0
REPO = Path(__file__).resolve().parents[2]
POLICY_PATH = REPO / "ops" / "config" / "model-routes.v1.json"
FLASH_SCRIPT = REPO / "tools" / "flash-script.py"
FLASH_RUN = REPO / "tools" / "flash-run.py"
# A code run (flash-run's attempts, the desk's own re-check of the test, the commit) ends within this, under the
# queue's 900 s claim and no longer than a script run.
CODE_TIMEOUT_S = 600.0
DATA_LINE = re.compile(r"^\s*data:\s*(\S.*?)\s*$", re.I | re.M)
PROJECT_LINE = re.compile(r"^\s*project:\s*(\S.*?)\s*$", re.I | re.M)
TEST_LINE = re.compile(r"^\s*test:\s*(\S.*?)\s*$", re.I | re.M)
BOTH_KINDS = "a task names either data (a script task) or a project (a code task), not both"
# test-line grammar (test_argv): every token plain, no shell syntax possible
TOKEN = re.compile(r"[A-Za-z0-9_./:=,+@-]+")
SAFE_TOKEN = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_./:=,+@-]*")
SCRIPT_ARG = re.compile(r"-{0,2}[A-Za-z0-9_][A-Za-z0-9_.,=-]*")
PYTEST_K = re.compile(r"[A-Za-z0-9_]+")
PYTEST_FLAGS = frozenset({"-q", "-qq", "-v", "-vv", "-x", "-s", "--tb=short", "--tb=line", "--tb=no"})
MAX_DIFF = 6000
TASK_LINE = re.compile(r"^\[Hermes queue (t_[A-Za-z0-9_-]+)\] ?(.*)$")
SOURCE_LINE = "[Model Room source"
# queue_dispatch.QueueDeskExecutor._prompt appends the queue's result protocol after this sentence; the script
# question is what comes before it, and this desk writes the result line itself.
PROTOCOL_MARK = "Your final non-empty line must be exactly one JSON object prefixed with CARR_QUEUE_RESULT"
MAX_SUMMARY = 480


def is_up(url: str = FLASH_URL, timeout: float = HEALTH_TIMEOUT_S) -> bool:
    """True when the Flash server answers its model list. Local only: 127.0.0.1."""
    try:
        with urllib.request.urlopen(f"{url}/v1/models", timeout=timeout) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


def run_turn(task: str, *, url: str = FLASH_URL, model: str = FLASH_MODEL, max_tokens: int = MAX_TOKENS,
             timeout: float = TIMEOUT_S, opener=urllib.request.urlopen) -> dict:
    """One direct answer from Flash, thinking off. Returns {"status", "result"?, "detail"?, "finish"?}."""
    body = {"model": model, "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": task}],
            # the ds4 server reads Qwen3.8's chat_template_kwargs per request; thinking off took a direct turn
            # from 230 s to 6 s in the 2026-09-24 tests
            "chat_template_kwargs": {"enable_thinking": False}}
    req = urllib.request.Request(f"{url}/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with opener(req, timeout=timeout) as r:
            reply = json.load(r)
    except TimeoutError:
        return {"status": "timed_out", "detail": f"no answer in {timeout:.0f}s"}
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return {"status": "failed", "detail": f"flash unreachable: {type(exc).__name__}"}
    try:
        choice = reply["choices"][0]
        content = (choice["message"].get("content") or "").strip()
        finish = choice.get("finish_reason")
    except (KeyError, IndexError, TypeError, AttributeError):
        return {"status": "failed", "detail": "flash reply was malformed"}
    if not content:
        return {"status": "failed", "detail": "no_answer", "finish": finish}
    return {"status": "completed", "result": content, "finish": finish}


def data_roots(policy_path: Path = POLICY_PATH) -> list[str]:
    """The folders a script task may name data in, from the routing policy; none when it cannot be read."""
    try:
        roots = json.loads(policy_path.read_text()).get("script_data_roots") or []
    except (OSError, ValueError, AttributeError):
        return []
    return [os.path.realpath(os.path.expanduser(r)) for r in roots if isinstance(r, str) and r.strip()]


def _refusal(path: str, roots: list[str]) -> str | None:
    if os.path.islink(path):
        return f"{path} is a link"
    real = os.path.realpath(path)
    if not any(real != r and real.startswith(r + os.sep) for r in roots):
        return f"{path} is outside the allowed data folders"
    if not os.path.exists(real):
        return f"{path} does not exist"
    for top, dirs, files in os.walk(real):
        for name in dirs + files:
            if os.path.islink(os.path.join(top, name)):
                return f"{os.path.join(top, name)} is a link"
    return None


def script_inputs(text: str, *, roots: list[str] | None = None):
    """(paths, question, refusal) for a task's `data:` lines. No data lines: (None, text, None), not a script task.
    Any path outside the roots, missing, a link or holding a link refuses the whole task, and so does a task that
    also names a project (code_inputs refuses the same task, so routing and the desk agree)."""
    named = DATA_LINE.findall(text or "")
    question = DATA_LINE.sub("", text or "").strip()
    if not named:
        return None, question, None
    if PROJECT_LINE.search(text or ""):
        return None, question, BOTH_KINDS
    roots = data_roots() if roots is None else [os.path.realpath(os.path.expanduser(r)) for r in roots
                                                 if isinstance(r, str) and r.strip()]
    if not roots:
        return None, question, "no script data folders are configured"
    for path in named:
        refusal = _refusal(os.path.expanduser(path), roots)
        if refusal:
            return None, question, refusal
    return [os.path.expanduser(p) for p in named], question, None


def code_roots_from_policy(policy_path: Path = POLICY_PATH) -> list[str]:
    """The folders a code task's project may sit in (`code_project_roots`); none when the policy cannot be read."""
    try:
        roots = json.loads(policy_path.read_text()).get("code_project_roots") or []
    except (OSError, ValueError, AttributeError):
        return []
    return [os.path.realpath(os.path.expanduser(r)) for r in roots if isinstance(r, str) and r.strip()]


def _git_toplevel(path: str) -> str | None:
    try:
        done = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=path, capture_output=True, text=True,
                              timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return os.path.realpath(done.stdout.strip()) if done.returncode == 0 and done.stdout.strip() else None


def _project_refusal(path: str, roots: list[str]) -> str | None:
    if os.path.islink(path.rstrip(os.sep) or path):
        return f"{path} is a link"
    real = os.path.realpath(path)
    if not any(real != r and real.startswith(r + os.sep) for r in roots):
        return f"{path} is outside the allowed project folders"
    if not os.path.isdir(real):
        return f"{path} is not a folder"
    if _git_toplevel(real) != real:
        return f"{path} is not the top of a git work tree"
    return None


def _in_project(project: str, token: str, *, suffix: str | None = None) -> str | None:
    """Refusal for a test argument that must name a file or folder inside the project (pytest's `path::node` too)."""
    if not SAFE_TOKEN.fullmatch(token):
        return f"test argument {token!r} is not a plain project path"
    rel = token.split("::", 1)[0]
    if os.path.isabs(rel) or ".." in rel.split("/"):
        return f"test argument {token!r} leaves the project"
    if suffix and not rel.endswith(suffix):
        return f"test argument {token!r} is not a {suffix} file"
    full = os.path.join(project, rel)
    if os.path.islink(full):
        return f"test argument {token!r} is a link"
    real = os.path.realpath(full)
    if not (real == project or real.startswith(project + os.sep)) or not os.path.exists(real):
        return f"test argument {token!r} is not in the project"
    return None


def test_argv(line: str, project: str):
    """(argv, refusal) for a task's `test:` line. The line never reaches a shell as written: it is split on
    whitespace, every token must be plain (letters, digits and _ . / : = , + @ -, so no quoting, expansion,
    redirection or chaining can appear), and it must be one of three runners:
      pytest [-q -qq -v -vv -x -s --tb=short|line|no] [-k NAME] [PROJECT-PATH[::NODE] ...]
      python3 PROJECT-SCRIPT.py [PLAIN-ARG ...]   (a .py file inside the project; its args hold no path)
      node --test [PROJECT-PATH ...]
    Paths are relative, stay inside the project, exist, and are not links. Python runs from the project's own
    .venv when it has one. What runs is the project's own test code, which the allowlisted project owns."""
    tokens = (line or "").split()
    if not tokens:
        return None, "the test line is empty"
    bad = next((t for t in tokens if not TOKEN.fullmatch(t)), None)
    if bad is not None:
        return None, f"test token {bad!r} is not allowed"
    python = ".venv/bin/python" if os.path.isfile(os.path.join(project, ".venv", "bin", "python")) else "python3"
    runner, rest = tokens[0], tokens[1:]
    if runner == "pytest":
        argv, i = [python, "-m", "pytest"], 0
        while i < len(rest):
            tok = rest[i]
            if tok == "-k":
                if i + 1 >= len(rest) or not PYTEST_K.fullmatch(rest[i + 1]):
                    return None, "pytest -k takes one plain test name"
                argv += [tok, rest[i + 1]]
                i += 2
                continue
            if tok.startswith("-"):
                if tok not in PYTEST_FLAGS:
                    return None, f"pytest option {tok!r} is not allowed"
            else:
                refusal = _in_project(project, tok)
                if refusal:
                    return None, refusal
            argv.append(tok)
            i += 1
        return argv, None
    if runner in ("python3", "python"):
        if not rest:
            return None, "python3 needs a test script inside the project"
        refusal = _in_project(project, rest[0], suffix=".py")
        if refusal:
            return None, refusal
        bad = next((t for t in rest[1:] if not SCRIPT_ARG.fullmatch(t)), None)
        if bad is not None:
            return None, f"script argument {bad!r} is not allowed"
        return [python, *rest], None
    if runner == "node":
        if rest[:1] != ["--test"]:
            return None, "node runs only as `node --test [paths]`"
        for tok in rest[1:]:
            refusal = _in_project(project, tok)
            if refusal:
                return None, refusal
        return ["node", *rest], None
    return None, f"test runner {runner!r} is not one of pytest, python3 or node --test"


def code_inputs(text: str, *, roots: list[str] | None = None):
    """(spec, question, refusal) for a task's `project:` and `test:` lines. No project line: (None, text, None), not
    a code task. spec is {"project", "test_argv", "test"}. One project, one test line: the project must sit strictly
    inside a code_project_roots folder, be the top of a git work tree and not be a link; the test must pass
    test_argv. Anything else refuses the whole task."""
    named = PROJECT_LINE.findall(text or "")
    tests = TEST_LINE.findall(text or "")
    question = TEST_LINE.sub("", PROJECT_LINE.sub("", text or "")).strip()
    if not named:
        return None, question, None
    if DATA_LINE.search(text or ""):
        return None, question, BOTH_KINDS
    if len(named) > 1:
        return None, question, "a code task names one project"
    roots = code_roots_from_policy() if roots is None else [os.path.realpath(os.path.expanduser(r)) for r in roots
                                                             if isinstance(r, str) and r.strip()]
    if not roots:
        return None, question, "no code project folders are configured"
    path = os.path.expanduser(named[0])
    refusal = _project_refusal(path, roots)
    if refusal:
        return None, question, refusal
    if len(tests) != 1:
        return None, question, "a code task names exactly one `test:` line, the command that proves it"
    project = os.path.realpath(path)
    argv, refusal = test_argv(tests[0], project)
    if refusal:
        return None, question, refusal
    return {"project": project, "test_argv": argv, "test": shlex.join(argv)}, question, None


def _git(cwd: str, *args: str, env=None, timeout: float = 120):
    try:
        done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, f"{type(exc).__name__}"
    return done.returncode, (done.stdout + done.stderr)


def _code_fixer() -> str:
    try:
        return json.loads(POLICY_PATH.read_text())["routes"]["code"]["then"]["desk"]
    except (OSError, ValueError, KeyError, TypeError):
        return "the code fixer desk"


def _run_group(argv: list[str], cwd: str, env, timeout: float):
    """(exit code, output) of argv in its own process group, no shell; (None, output) when it outlived timeout, in
    which case the WHOLE group is killed, so nothing it started (Flash attempts, test workers) keeps running."""
    try:
        proc = subprocess.Popen(argv, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, start_new_session=True)
    except OSError as exc:
        return 127, f"could not start {argv[0]}: {type(exc).__name__}"
    try:
        out, _ = proc.communicate(timeout=max(1.0, timeout))
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
        out, _ = proc.communicate()
        return None, out or ""
    return proc.returncode, out or ""


FLASH_RUN_EXITS = {2: "flash-run could not start (launcher or environment)",
                   3: "Flash judged the task ambiguous: say exactly what to change",
                   4: "flash-run routed the task away from Flash as a judgment call",
                   5: "no Flash attempt passed the test"}


def _run_flash_code(question: str, spec: dict, task_id: str | None, *, command: list[str] | None = None,
                    timeout: float | None = None) -> dict:
    """Run tools/flash-run.py on a NEW branch of the project, in a throwaway git worktree, never the project's own
    tree: {"outcome": "success"|"blocked", "summary", "text", "branch"?}. flash-run gets --escalate suggest, so it
    never dispatches another desk. On a pass the desk re-runs the test itself (no shell), commits on the new branch
    and keeps it; nothing is merged or pushed. Anything else drops the worktree AND the branch. The whole run,
    including the re-check, ends within CODE_TIMEOUT_S; a timeout kills flash-run's whole process group."""
    timeout = CODE_TIMEOUT_S if timeout is None else timeout
    deadline = time.monotonic() + timeout
    project = spec["project"]
    branch = f"flash/queue-{task_id or 'room'}-{time.strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6]}"
    fixer = _code_fixer()
    parent = tempfile.mkdtemp(prefix="flash-code-")
    tree = os.path.join(parent, "tree")
    exclude = os.path.join(parent, "exclude")
    with open(exclude, "w") as fh:
        fh.write("/.venv\n/node_modules\n")
    # the project's .venv and node_modules are linked into the worktree so its tests run; this keeps git (the desk's
    # and flash-run's) from ever adding those links, whatever the project's own .gitignore says
    env = dict(os.environ, GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="core.excludesFile", GIT_CONFIG_VALUE_0=exclude)
    keep = False

    def blocked(reason: str, text: str = "") -> dict:
        return {"outcome": "blocked", "summary": f"{reason}; no branch kept, needs {fixer}.",
                "text": (text or reason).strip()}

    code, out = _git(project, "worktree", "add", "-q", "-b", branch, tree, "HEAD", env=env)
    if code != 0:
        shutil.rmtree(parent, ignore_errors=True)
        return blocked(f"could not open a worktree of {project}", out[-600:])
    try:
        for dep in (".venv", "node_modules"):
            if os.path.isdir(os.path.join(project, dep)) and not os.path.lexists(os.path.join(tree, dep)):
                os.symlink(os.path.join(project, dep), os.path.join(tree, dep))
        argv = [*(command or [sys.executable, str(FLASH_RUN)]), "run", "--cwd", tree, "--test", spec["test"],
                "--escalate", "suggest", "--", question]
        rc, log = _run_group(argv, tree, env, deadline - time.monotonic())
        if rc is None:
            return blocked(f"Flash's code run did not finish in {timeout:.0f}s")
        tail = log[-2000:]
        if rc != 0:
            return blocked(FLASH_RUN_EXITS.get(rc, f"flash-run exited {rc}"), tail)
        code, head = _git(tree, "symbolic-ref", "--short", "HEAD", env=env)
        if code != 0 or head.strip() != branch:
            return blocked("the worktree left its new branch", tail)
        if deadline - time.monotonic() < 5:
            return blocked(f"no time left to re-check the test within {timeout:.0f}s", tail)
        rc, check = _run_group(spec["test_argv"], tree, None, deadline - time.monotonic())
        if rc is None:
            return blocked(f"the desk's re-check of `{spec['test']}` did not finish in {timeout:.0f}s", tail)
        if rc != 0:
            return blocked(f"`{spec['test']}` fails on Flash's patch", f"{tail}\n--- re-check ---\n{check[-1500:]}")
        _git(tree, "add", "-A", env=env)
        if _git(tree, "diff", "--cached", "--quiet", env=env)[0] == 0:
            return blocked("flash-run passed but left no change", tail)
        subject = " ".join((question.splitlines() or ["code task"])[0].split())[:72] or "code task"
        code, out = _git(tree, "-c", "user.name=Flash (Model Room queue)", "-c", "user.email=flash@local",
                         "commit", "-q", "--no-verify", "-m", f"Flash queue task {task_id or ''}: {subject}".strip(),
                         env=env)
        if code != 0:
            return blocked("could not commit Flash's change on its branch", out[-600:])
        _, stat = _git(tree, "diff", "--stat", "HEAD~1", "HEAD", env=env)
        _, diff = _git(tree, "diff", "HEAD~1", "HEAD", env=env)
        files = len([ln for ln in stat.splitlines()[:-1] if "|" in ln])
        if len(diff) > MAX_DIFF:
            diff = diff[:MAX_DIFF] + f"\n... [diff truncated; see branch {branch}]"
        keep = True
        return {"outcome": "success", "branch": branch,
                "summary": (f"Flash's change passes `{spec['test']}` on new branch {branch} of {project} "
                            f"({files} file(s)); not merged, not pushed."),
                "text": (f"Flash's change passes `{spec['test']}` on new branch {branch} of {project} "
                         f"(not merged, not pushed).\n{stat.strip()}\n```diff\n{diff}```")}
    finally:
        _git(project, "worktree", "remove", "--force", tree, env=env)
        if not keep:
            _git(project, "branch", "-D", branch, env=env)
        _git(project, "worktree", "prune", env=env)
        shutil.rmtree(parent, ignore_errors=True)


def _run_flash_script(question: str, paths: list[str]):
    """tools/flash-script.py --json: (exit code, its result row, or {"detail": stderr} when it printed none). A timeout
    kills the runner's whole process group, not only the runner."""
    proc = subprocess.Popen([sys.executable, str(FLASH_SCRIPT), "--json", "--", question, *paths],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
    try:
        out, err = proc.communicate(timeout=SCRIPT_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, 9)
        except OSError:
            pass
        proc.communicate()
        return None, {"detail": f"no answer in {SCRIPT_TIMEOUT_S:.0f}s"}
    try:
        return proc.returncode, json.loads(out.strip().splitlines()[-1])
    except (IndexError, ValueError):
        return proc.returncode, {"detail": (err.strip().splitlines() or ["no output"])[-1][:300]}


def task_parts(prompt: str):
    """(task_id, title, body) of a queue prompt (queue_dispatch.QueueDeskExecutor._prompt): the header line, the
    source line and the trailing result protocol removed. The protocol is cut at its LAST occurrence, since the queue
    appends it after the body. Not a queue prompt: (None, "", prompt)."""
    head = prompt.rsplit(PROTOCOL_MARK, 1)[0] if PROTOCOL_MARK in prompt else prompt
    lines = head.splitlines()
    m = TASK_LINE.match(lines[0]) if lines else None
    if not m:
        return None, "", prompt
    body = [line for line in lines[1:] if not line.startswith(SOURCE_LINE)]
    return m.group(1), m.group(2).strip(), "\n".join(body).strip()


def _result_line(task_id: str, outcome: str, summary: str) -> str:
    return "CARR_QUEUE_RESULT " + json.dumps({"v": 1, "task_id": task_id, "outcome": outcome,
                                              "summary": summary[:MAX_SUMMARY]}, separators=(",", ":"))


def _code_task(task_id: str | None, title: str, body: str, spec: dict | None, refusal: str | None,
               code_runner) -> dict:
    if refusal or spec is None:
        row = {"outcome": "blocked", "summary": f"Flash's code task was refused: {refusal}.",
               "text": f"Flash's code task was refused: {refusal}."}
    else:
        question = f"{title}\n{body}".strip()
        row = (code_runner or _run_flash_code)(question, spec, task_id)
    text = row.get("text") or row["summary"]
    if not task_id:
        return {"status": "completed", "result": text}
    return {"status": "completed",
            "result": f"{text}\n{_result_line(task_id, row['outcome'], ' '.join(row['summary'].split()))}"}


def run_task(prompt: str, *, roots: list[str] | None = None, runner=None, code_roots: list[str] | None = None,
             code_runner=None, **turn_kwargs) -> dict:
    """The flash-local desk's entry: a queued task whose BODY names a project runs flash-run on a new branch of it
    (code), one whose body names data runs the script protocol, anything else one direct reply (run_turn). Project
    and data lines count in the body only, as route_auto reads them, and are checked again here, whatever routed the
    task. A code task always ends in this desk's own result line: success only when the test passes on the patch."""
    task_id, title, body = task_parts(prompt)
    spec, code_question, code_refusal = code_inputs(body, roots=code_roots)
    if spec or code_refusal:
        return _code_task(task_id, title, code_question, spec, code_refusal, code_runner)
    paths, question, refusal = script_inputs(body, roots=roots)
    if refusal:
        return {"status": "failed", "detail": f"script data refused: {refusal}"}
    if paths is None:
        return run_turn(prompt, **turn_kwargs)
    question = f"{title}\n{question}".strip()
    code, row = (runner or _run_flash_script)(question, paths)
    answer = (row.get("answer") or "").strip()
    if code not in (0, 4) or (code == 0 and not answer):
        return {"status": "failed", "detail": f"flash-script: {row.get('detail') or 'no answer'}"}
    if code == 0:
        text, outcome, summary = answer, "success", f"Flash answered from the named data: {answer.splitlines()[0]}"
    else:
        desk = row.get("handoff_desk") or "the Opus desk"
        text = f"Flash's answer ({row.get('handoff')}, needs {desk}): {answer or '(none)'}"
        outcome, summary = "blocked", f"Flash's script answer was handed off ({row.get('handoff')}); needs {desk}."
    if not task_id:
        return {"status": "completed", "result": text}
    return {"status": "completed", "result": f"{text}\n{_result_line(task_id, outcome, ' '.join(summary.split()))}"}
