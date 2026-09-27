#!/usr/bin/env python3
"""The Flash code desk runs model-driven code (the agent and the tests it writes). This checks the containment that
makes that safe: the exploit replay the independent review of PR #1324 asked for (a program that reads a home secret
and reaches the network is REFUSED under the real sandbox), plus the timeout that actually bounds a run even when a
child escapes its process group.

The @sandboxed cases run the REAL macOS sandbox on this Mac; on a machine without sandbox-exec (Linux CI) they are
skipped in code, never disabled through the environment. The timeout case needs no sandbox and runs everywhere."""
from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("flash_run", os.path.join(HERE, "flash-run.py"))
assert spec and spec.loader
fr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fr)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "ops"))
from git_env import fixture_env  # noqa: E402

ENV = fixture_env()  # every fixture git call below runs with this: no inherited GIT_DIR, no real config

FAILURES: list[str] = []
SANDBOXED = os.path.exists(fr.SANDBOX_EXEC)
TEMPS: list[str] = []


def check(name, fn):
    try:
        fn()
    except AssertionError as exc:
        FAILURES.append(f"{name}: {exc}")
        print(f"  FAIL  {name}\n          {exc}")
    except Exception as exc:  # noqa: BLE001
        FAILURES.append(f"{name}: unexpected {exc!r}")
        print(f"  FAIL  {name}\n          unexpected {exc!r}")
    else:
        print(f"  ok    {name}")


def sandboxed(fn):
    fn._sandboxed = True
    return fn


def _work():
    d = os.path.realpath(tempfile.mkdtemp(prefix="flash-run-sbx-"))
    TEMPS.append(d)
    return d


def _run(program, work, *, reads=(), execs=(), port=None, timeout=30):
    """Run a python program string under the real sandbox profile for `work`, group-safe."""
    path = os.path.join(work, "prog.py")
    with open(path, "w") as fh:
        fh.write(program)
    argv = fr.sandbox_wrap([sys.executable, path], work, reads=reads, execs=execs, port=port)
    return fr.bounded_run(argv, work, timeout)


# ── the exploit replay ────────────────────────────────────────────────────────────────────────────────────────
# A harmless stand-in secret under a throwaway home the profile does not list. Real key files are not named, because
# the unattended guard blocks them and because the point is the SANDBOX, not any one path.
EXPLOIT = '''
import socket
SECRET = {secret!r}
try:
    data = open(SECRET).read()
    print("READ_OK:" + data)
except Exception as e:
    print("READ_DENIED:" + type(e).__name__)
try:
    s = socket.create_connection(("10.255.255.1", 80), timeout=3)
    s.close()
    print("NET_OK")
except OSError as e:
    print("NET_DENIED:" + type(e).__name__)
'''


def _plant_secret():
    home = os.path.realpath(tempfile.mkdtemp(prefix="flash-fakehome-"))
    TEMPS.append(home)
    secret = os.path.join(home, "stand_in_secret.txt")
    with open(secret, "w") as fh:
        fh.write("TOP-SECRET-STANDIN-VALUE")
    return secret


@sandboxed
def exploit_is_refused_reading_a_home_secret_and_the_network():
    secret = _plant_secret()
    work = _work()
    code, out = _run(EXPLOIT.format(secret=secret), work, port=fr.flash_port())
    assert "READ_OK" not in out, f"the sandbox let the exploit read the stand-in secret: {out}"
    assert "READ_DENIED" in out, out
    assert "TOP-SECRET-STANDIN-VALUE" not in out, out
    assert "NET_OK" not in out and "NET_DENIED" in out, f"the sandbox let the exploit reach the network: {out}"


def exploit_control_reads_the_secret_when_unsandboxed():
    # Proves the test is meaningful: the same program, run WITHOUT the sandbox, does read the stand-in secret and so
    # the refusal above is the sandbox's doing, not the program failing on its own.
    secret = _plant_secret()
    work = _work()
    path = os.path.join(work, "prog.py")
    with open(path, "w") as fh:
        fh.write(EXPLOIT.format(secret=secret))
    out = subprocess.run([sys.executable, path], capture_output=True, text=True, timeout=30).stdout
    assert "READ_OK:TOP-SECRET-STANDIN-VALUE" in out, out


@sandboxed
def a_worktree_written_binary_cannot_be_executed():
    work = _work()
    payload = os.path.join(work, "payload.sh")
    with open(payload, "w") as fh:
        fh.write("#!/bin/sh\necho pwned\n")
    os.chmod(payload, 0o755)
    prog = f'''
import subprocess
try:
    subprocess.run([{payload!r}], check=True)
    print("EXEC_OK")
except Exception as e:
    print("EXEC_DENIED:" + type(e).__name__)
'''
    code, out = _run(prog, work)
    assert "EXEC_OK" not in out and "EXEC_DENIED" in out, f"a model-written binary in the worktree ran: {out}"


@sandboxed
def writes_outside_the_worktree_are_denied():
    work = _work()
    outside = _work()
    target = os.path.join(outside, "escape.txt")
    prog = f'''
try:
    open({target!r}, "w").write("x")
    print("WRITE_OK")
except Exception as e:
    print("WRITE_DENIED:" + type(e).__name__)
'''
    code, out = _run(prog, work)
    assert "WRITE_OK" not in out and "WRITE_DENIED" in out, out
    assert not os.path.exists(target), "a write escaped the worktree"


@sandboxed
def a_write_inside_the_worktree_is_allowed():
    work = _work()
    prog = 'open("made.txt", "w").write("ok"); print("WROTE")'
    code, out = _run(prog, work)
    assert "WROTE" in out and os.path.exists(os.path.join(work, "made.txt")), out


def no_sandbox_binary_means_the_wrap_refuses():
    saved = fr.SANDBOX_EXEC
    fr.SANDBOX_EXEC = "/nonexistent/sandbox-exec"
    try:
        raised = False
        try:
            fr.sandbox_wrap([sys.executable, "-c", "print(1)"], _work())
        except FileNotFoundError:
            raised = True
        assert raised, "sandbox_wrap must refuse when sandbox-exec is absent"
    finally:
        fr.SANDBOX_EXEC = saved


# ── git escape canaries (re-review of #1324, finding 1) ──────────────────────────────────────────────────────
# A canary is a harmless hook script that only touches a scratch file. If git ever runs it from config the model
# planted, the file appears. Each case asserts it never does; the control proves the canary really fires.
def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True, env=ENV)


def _project():
    p = _work()
    with open(os.path.join(p, "a.txt"), "w") as fh:
        fh.write("one\n")
    _git(p, "init", "-q", "-b", "main")
    _git(p, "add", "a.txt")
    _git(p, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "base")
    return p


def _canary():
    """(hook path, canary file path): running the hook creates the canary."""
    d = _work()
    canary = os.path.join(d, "CANARY")
    hook = os.path.join(d, "hook.sh")
    with open(hook, "w") as fh:
        fh.write(f"#!/bin/sh\ntouch '{canary}'\n")
    os.chmod(hook, 0o755)
    return hook, canary


def _fsmonitor(hook):
    return f"[core]\n\tfsmonitor = {hook}\n"


def canary_control_fires_when_git_trusts_planted_config():
    # Proves the canary works: plain git in a repo whose .git/config names the hook DOES run it.
    project = _project()
    hook, canary = _canary()
    with open(os.path.join(project, ".git", "config"), "a") as fh:
        fh.write(_fsmonitor(hook))
    with open(os.path.join(project, "a.txt"), "a") as fh:
        fh.write("two\n")
    subprocess.run(["git", "status", "--short"], cwd=project, capture_output=True, text=True, env=ENV)
    assert os.path.exists(canary), "control: the canary did not fire, so the escape tests would prove nothing"


def attempt_copy_ignores_an_in_tree_git_config_planted_unsandboxed():
    # Layer (b): even if model code DID write <copy>/.git/config, read_patch uses the copy's trusted git dir.
    project = _project()
    work = _work()
    dest = os.path.join(work, "attempt-1")
    os.makedirs(dest)
    fr.make_copy(project, dest)
    hook, canary = _canary()
    os.makedirs(os.path.join(dest, ".git"), exist_ok=True)
    with open(os.path.join(dest, ".git", "config"), "w") as fh:
        fh.write(_fsmonitor(hook))
    with open(os.path.join(dest, "a.txt"), "a") as fh:
        fh.write("two\n")
    patch = fr.read_patch(dest)
    assert not os.path.exists(canary), "read_patch ran a hook from a git config planted in the tree"
    assert "+two" in patch, patch


def desk_worktree_git_ignores_a_swapped_git_pointer():
    # Layer (b) for the desk: a tree whose .git was swapped for a directory with a hook is never read, because the
    # desk names the worktree's real admin dir (captured before any model code ran).
    project = _project()
    tree = os.path.join(_work(), "tree")
    _git(project, "worktree", "add", "-q", "-b", "flash/queue-t_canary", tree, "HEAD")
    with open(os.path.join(tree, ".git")) as fh:
        admin = os.path.realpath(fh.read().strip()[len("gitdir:"):].strip())
    hook, canary = _canary()
    os.unlink(os.path.join(tree, ".git"))
    os.makedirs(os.path.join(tree, ".git"))
    with open(os.path.join(tree, ".git", "config"), "w") as fh:
        fh.write(_fsmonitor(hook))
    with open(os.path.join(tree, "a.txt"), "a") as fh:
        fh.write("two\n")
    fr.tgit(admin, tree, "add", "-A")
    code, diff = fr.tgit(admin, tree, "diff", "--cached")
    assert not os.path.exists(canary), "desk git ran a hook from a swapped .git in the worktree"
    assert "+two" in diff, diff
    _git(project, "worktree", "prune")


PLANT = '''
import os
results = []
def attempt(label, fn):
    try:
        fn()
        results.append(label + "_OK")
    except Exception as e:
        results.append(label + "_DENIED:" + type(e).__name__)
{body}
open("a.txt", "a").write("two\\n")
print(" ".join(results))
'''


@sandboxed
def sandboxed_agent_cannot_plant_git_config_in_its_attempt_copy():
    # The reviewer's first path, replayed through the real sandbox and the real read_patch.
    project = _project()
    work = _work()
    dest = os.path.join(work, "attempt-1")
    os.makedirs(dest)
    fr.make_copy(project, dest)
    hook, canary = _canary()
    gitdir = fr.gitdir_for(dest)
    body = (f"attempt('TRUSTED_GITDIR', lambda: open({os.path.join(gitdir, 'config')!r}, 'a').write({_fsmonitor(hook)!r}))\n"
            f"attempt('INTREE_GITDIR', lambda: (os.makedirs('.git', exist_ok=True), "
            f"open('.git/config', 'w').write({_fsmonitor(hook)!r})))\n"
            f"attempt('NESTED_GIT', lambda: (os.makedirs('sub/.git', exist_ok=True), "
            f"open('sub/.git/config', 'w').write({_fsmonitor(hook)!r})))\n")
    code, out = _run(PLANT.format(body=body), dest, reads=[gitdir], port=fr.flash_port())
    assert "TRUSTED_GITDIR_OK" not in out and "INTREE_GITDIR_OK" not in out and "NESTED_GIT_OK" not in out, out
    patch = fr.read_patch(dest)
    assert not os.path.exists(canary), f"the canary fired after read_patch: {out}"
    assert "+two" in patch, (out, patch)


@sandboxed
def sandboxed_test_cannot_swap_the_worktree_git_pointer():
    # The reviewer's second path: the final test / re-check (work = the desk's worktree) swaps .git, then the desk's
    # git runs. Replayed through the real sandbox and the desk's trusted git calls.
    project = _project()
    tree = os.path.join(_work(), "tree")
    _git(project, "worktree", "add", "-q", "-b", "flash/queue-t_canary2", tree, "HEAD")
    with open(os.path.join(tree, ".git")) as fh:
        admin = os.path.realpath(fh.read().strip()[len("gitdir:"):].strip())
    hook, canary = _canary()
    body = (f"attempt('POINTER_WRITE', lambda: open('.git', 'w').write('gitdir: evil\\n'))\n"
            f"attempt('POINTER_UNLINK', lambda: os.unlink('.git'))\n"
            f"attempt('GITDIR_SWAP', lambda: (os.makedirs('.git', exist_ok=True), "
            f"open('.git/config', 'w').write({_fsmonitor(hook)!r})))\n")
    code, out = _run(PLANT.format(body=body), tree, port=fr.flash_port())
    assert "POINTER_WRITE_OK" not in out and "POINTER_UNLINK_OK" not in out and "GITDIR_SWAP_OK" not in out, out
    fr.tgit(admin, tree, "add", "-A")
    code, diff = fr.tgit(admin, tree, "diff", "--cached")
    subprocess.run(["git", "status", "--short"], cwd=tree, capture_output=True, text=True, env=ENV)  # even discovery-mode git
    assert not os.path.exists(canary), f"the canary fired after the desk's git: {out}"
    assert "+two" in diff, (out, diff)
    _git(project, "worktree", "remove", "--force", tree)


# ── patch content: only what the model edited (third review of #1324) ────────────────────────────────────────
# The sandboxed run's HOME and TMPDIR must never land in the patch, the committed branch or the room diff: the
# launcher writes its CLAUDE_CONFIG_DIR session logs and caches under HOME, and pytest's tmp_path lives under TMPDIR.
TEST_WITH_TMP_PATH = (
    "import os, sys\n"
    "sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))\n"
    "import calc\n"
    "def test_add(tmp_path):\n"
    "    (tmp_path / 'scratch.txt').write_text('pytest temp file')\n"
    "    assert calc.add(2, 2) == 4\n"
)

LAUNCHER_SIM = '''
import os
home, tmp = os.environ["HOME"], os.environ["TMPDIR"]
os.makedirs(os.path.join(home, ".claude-local", "projects", "p1"), exist_ok=True)
open(os.path.join(home, ".claude-local", "projects", "p1", "session.jsonl"), "w").write("{{}}\\n")
os.makedirs(os.path.join(home, ".cache"), exist_ok=True)
open(os.path.join(home, ".cache", "blob"), "w").write("cache")
open(os.path.join(tmp, "agent-temp.txt"), "w").write("temp")
open("calc.py", "w").write("def add(a, b):\\n    return a + b\\n")
print("SIM_DONE", home, tmp)
'''


def _pytest_project():
    p = _work()
    os.makedirs(os.path.join(p, "tests"))
    with open(os.path.join(p, "calc.py"), "w") as fh:
        fh.write("def add(a, b):\n    return a - b\n")
    with open(os.path.join(p, "tests", "test_calc.py"), "w") as fh:
        fh.write(TEST_WITH_TMP_PATH)
    _git(p, "init", "-q", "-b", "main")
    _git(p, "add", "calc.py", "tests/test_calc.py")
    _git(p, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "base")
    return p


def _patch_files(patch):
    return sorted({line.split(" b/", 1)[1] for line in patch.splitlines() if line.startswith("diff --git a/")})


def sandbox_home_and_tmpdir_sit_beside_the_tree_not_in_it():
    dest = _work()
    env = fr._sandbox_env(os.environ, dest)
    for key in ("HOME", "TMPDIR"):
        path = os.path.realpath(env[key])
        assert not (path == dest or path.startswith(dest + os.sep)), f"{key}={path} is inside the tree {dest}"
        assert path.startswith(fr.scratch_for(dest) + os.sep), (key, path)
    fr.drop_scratch(dest)
    assert not os.path.exists(fr.scratch_for(dest))


@sandboxed
def patch_content_control_catches_a_home_inside_the_tree():
    # Proves the patch-content check is sensitive: with the OLD layout (scratch inside the tree) the same run leaks
    # the launcher's session log into the patch.
    saved, saved_drop = fr.scratch_for, fr.drop_scratch
    fr.scratch_for = lambda work: os.path.join(os.path.realpath(work), ".home")
    fr.drop_scratch = lambda work: None  # the old layout never removed it
    try:
        patch = _attempt_patch()
    finally:
        fr.scratch_for, fr.drop_scratch = saved, saved_drop
    assert "session.jsonl" in patch, "control: an in-tree HOME did not leak, so the real check would prove nothing"


@sandboxed
def attempt_patch_holds_only_the_model_edit_with_a_real_pytest_tmp_path_suite():
    patch = _attempt_patch()
    assert _patch_files(patch) == ["calc.py"], f"the attempt patch holds more than the model's edit: {_patch_files(patch)}"
    for leaked in (".claude-local", "session.jsonl", ".cache", "agent-temp", "scratch.txt", ".pytest_cache", ".home"):
        assert leaked not in patch, f"{leaked} reached the patch"


def _attempt_patch():
    project = _pytest_project()
    work = _work()
    dest = os.path.join(work, "attempt-1")
    os.makedirs(dest)
    fr.make_copy(project, dest)
    # the agent step, as run_attempt runs it: sandboxed, with the sandbox HOME/TMPDIR
    path = os.path.join(_work(), "sim.py")
    with open(path, "w") as fh:
        fh.write(LAUNCHER_SIM.format())
    argv = fr.sandbox_wrap([sys.executable, path], dest, reads=[path, fr.gitdir_for(dest)], port=fr.flash_port())
    code, out = fr.bounded_run(argv, dest, 60, env=fr._sandbox_env(os.environ, dest))
    assert "SIM_DONE" in out, out
    # the test step, as run_attempt runs it: a real pytest suite that uses tmp_path, sandboxed
    code, out = fr._run_test(f"{sys.executable} -m pytest -q tests", dest, 120, sandbox=True, port=fr.flash_port())
    assert code == 0, out
    return fr.read_patch(dest)


@sandboxed
def desk_commit_holds_only_the_model_edit_with_a_real_pytest_tmp_path_suite():
    sys.path.insert(0, os.path.join(HERE, "room-bridge"))
    import flash_wire
    project = _pytest_project()
    sim = os.path.join(_work(), "sim.py")
    with open(sim, "w") as fh:
        fh.write(LAUNCHER_SIM.format())
    fake = os.path.join(_work(), "fake-flash-run.py")
    with open(fake, "w") as fh:
        fh.write(
            "import importlib.util, os, sys\n"
            f"spec = importlib.util.spec_from_file_location('fr', {os.path.join(HERE, 'flash-run.py')!r})\n"
            "fr = importlib.util.module_from_spec(spec); spec.loader.exec_module(fr)\n"
            "a = sys.argv[1:]; cwd = a[a.index('--cwd') + 1]; test = a[a.index('--test') + 1]\n"
            f"argv = fr.sandbox_wrap([sys.executable, {sim!r}], cwd, reads=[{sim!r}], port=fr.flash_port())\n"
            "code, out = fr.bounded_run(argv, cwd, 60, env=fr._sandbox_env(os.environ, cwd))\n"
            "assert 'SIM_DONE' in out, out\n"
            "code, out = fr._run_test(test, cwd, 120, sandbox=True, port=fr.flash_port())\n"
            "print(out); sys.exit(0 if code == 0 else 5)\n")
    argv = [sys.executable, "-m", "pytest", "-q", "tests"]
    spec = {"project": project, "test_argv": argv, "test": " ".join(argv)}
    row = flash_wire._run_flash_code("Fix add", spec, "t_patch01", command=[sys.executable, fake], sandbox=True)
    assert row["outcome"] == "success", row
    files = subprocess.run(["git", "show", "--name-only", "--format=", row["branch"]], cwd=project,
                           capture_output=True, text=True, env=ENV).stdout.split()
    assert files == ["calc.py"], f"the desk committed more than the model's edit: {files}"
    for leaked in (".claude-local", "session.jsonl", ".cache", "agent-temp", "scratch.txt", ".pytest_cache"):
        assert leaked not in row["text"], f"{leaked} reached the diff posted to the room"


# ── the timeout actually bounds the run ───────────────────────────────────────────────────────────────────────
def _alive(pid):
    """True when pid is a live (not zombie) process."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    return bool(state) and not state.startswith("Z")


def _wait_gone(pid, seconds=3.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not _alive(pid):
            return True
        time.sleep(0.1)
    return not _alive(pid)


def _grandchild_program(work, *, parent_sleeps):
    """A program that starts a setsid() grandchild (sleeping 30 s, pid written to a file), then sleeps or exits."""
    pidfile = os.path.join(work, "grandchild.pid")
    prog = (
        "import subprocess, sys, time\n"
        f"subprocess.Popen([sys.executable, '-c', \"import os,time; os.setsid(); "
        f"open({pidfile!r},'w').write(str(os.getpid())); time.sleep(30)\"])\n"
        "for _ in range(50):\n"
        f"    import os\n"
        f"    if os.path.exists({pidfile!r}): break\n"
        "    time.sleep(0.05)\n"
        f"time.sleep({parent_sleeps})\n"
    )
    path = os.path.join(work, "prog_gc.py")
    with open(path, "w") as fh:
        fh.write(prog)
    return path, pidfile


def a_two_second_timeout_returns_within_about_three_seconds_and_kills_the_setsid_grandchild():
    # The reviewer's case: a child that calls setsid() leaves the group and holds the output pipe. The run must return
    # in ~3 s for a 2 s timeout, AND the grandchild must be dead afterwards, not abandoned to init.
    work = _work()
    path, pidfile = _grandchild_program(work, parent_sleeps=60)
    started = time.monotonic()
    code, out = fr.bounded_run([sys.executable, path], work, 2)
    elapsed = time.monotonic() - started
    assert code == 124, f"expected a timeout code, got {code}: {out}"
    assert elapsed < 4.0, f"bounded_run held for {elapsed:.1f}s past a 2s timeout (escaped child was waited on)"
    grandchild = int(open(pidfile).read())
    assert _wait_gone(grandchild), f"the setsid grandchild {grandchild} survived the timeout"


def a_daemon_left_behind_by_a_normal_exit_is_killed_too():
    # A run that daemonises a setsid child and then exits 0 must not leave it running (it would keep writing into
    # the tree while the desk's git runs): the run's environment marker finds it after it is reparented.
    work = _work()
    path, pidfile = _grandchild_program(work, parent_sleeps=0)
    code, out = fr.bounded_run([sys.executable, path], work, 20)
    assert code == 0, (code, out)
    grandchild = int(open(pidfile).read())
    assert _wait_gone(grandchild), f"the daemonised grandchild {grandchild} was left running after a normal exit"


def main() -> int:
    if not SANDBOXED:
        print("no macOS sandbox-exec on this machine; @sandboxed exploit-replay cases are skipped (Linux CI)")
    for name, fn in list(globals().items()):
        if not (name and callable(fn) and getattr(fn, "__module__", None) == "__main__"):
            continue
        if name in ("check", "sandboxed", "main"):
            continue
        if name.startswith("_"):
            continue
        if getattr(fn, "_sandboxed", False) and not SANDBOXED:
            print(f"  skip  {name} (needs the macOS sandbox)")
            continue
        check(name.replace("_", " "), fn)
    for d in TEMPS:
        shutil.rmtree(d, ignore_errors=True)
    if FAILURES:
        print(f"{len(FAILURES)} sandbox test(s) failed", file=sys.stderr)
        return 1
    print("flash-run sandbox: every assertion held")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
