#!/usr/bin/env python3
"""prepush-floor-selftest.py — the acceptance test for what ops/githooks/pre-push
runs, and for the plumbing that carries the answer back.

WHY THIS FILE EXISTS AT ALL. On 2026-08-23 the gates council moved the full
ten-class ops/ci.sh off the push path and left a fast local floor behind. Doing
that required three changes to the hook whose failure modes are silent:

  1. It now selects classes (`--only pushfloor,secret`). Select the wrong ones
     and the push path checks less than anybody thinks, with no symptom.
  2. It now derives CARR_CI_RANGE from the refs on stdin -- which the owner
     check above it already consumed, so stdin had to be captured and replayed.
     Get that wrong and either the owner check stops seeing main, or the range
     is empty and the secret scan silently widens (safe) or misreads (not).
  3. It now TEEs the run so the failure text can be attributed. A shell pipeline
     reports the status of its LAST command, and tee always succeeds, so the
     obvious spelling of this makes EVERY RED PUSH GREEN. That is the single
     worst outcome available in this file and it leaves no trace.

None of those is caught by "the checks pass". They are caught by asserting what
the hook invoked and what it did with the answer, which is what this does.

HOW. The unit under test is the HOOK, not ops/ci.sh, so the fixture installs a
STUB ci.sh that records its arguments and environment and exits with whatever
status the case wants. A real suite here would test ci.sh twice and this file's
actual subject not at all.

The fixture is built outside the checkout with ops/git_env.fixture_env(), per
ops/selftest-git-isolation-check.py -- and pointedly so, because this file is
run by ops/ci.sh which is run by this very hook.
"""
from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "ops"))
from git_env import fixture_env                                # noqa: E402

HOOK = ROOT / "ops" / "githooks" / "pre-push"

passed = 0
failures: list[str] = []


def check(name, cond, detail=""):
    global passed
    if cond:
        passed += 1
        print(f"  ok    {name}")
    else:
        failures.append(name)
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))


def git(repo, *args, env=None, check_rc=False):
    e = dict(env or fixture_env())
    p = subprocess.run(["git", "-C", str(repo), *args],
                       capture_output=True, text=True, env=e)
    if check_rc and p.returncode != 0:
        raise SystemExit(f"fixture setup failed: git {' '.join(args)}\n{p.stderr}")
    return p


STUB = """#!/bin/sh
# Stub ci.sh: record how the hook called us, then exit as the case demands.
{
  echo "args:$*"
  echo "range:${CARR_CI_RANGE-<unset>}"
} >> "$CI_CALL_LOG"
echo "  FAIL  pushfloor   seeded/failure.py is red"
exit "${STUB_CI_RC:-0}"
"""


def build(tmp):
    env = fixture_env()
    bare = tmp / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)],
                   env=env, check=True)
    repo = tmp / "work"
    subprocess.run(["git", "clone", "-q", str(bare), str(repo)], env=env,
                   check=True)
    git(repo, "config", "user.email", "fixture@example.invalid", check_rc=True)
    git(repo, "config", "user.name", "Fixture", check_rc=True)

    hooks = repo / "githooks"
    hooks.mkdir()
    shutil.copy(HOOK, hooks / "pre-push")
    os.chmod(hooks / "pre-push", 0o755)
    git(repo, "config", "core.hooksPath", "githooks", check_rc=True)

    ops = repo / "ops"
    ops.mkdir()
    (ops / "ci.sh").write_text(STUB)
    os.chmod(ops / "ci.sh", 0o755)
    (repo / "seed.txt").write_text("seed\n")
    git(repo, "add", "-A", check_rc=True)
    git(repo, "commit", "-qm", "seed", check_rc=True)
    # The seed push goes to main, which the owner check correctly refuses for a
    # fixture identity -- the hatch is the point of the hatch. It also proves,
    # before any assertion runs, that the owner check still reads its refs.
    seed_env = dict(env)
    seed_env.update(CARR_ALLOW_MAIN_PUSH="1", STUB_CI_RC="0",
                    CI_CALL_LOG="/dev/null")
    git(repo, "push", "-q", "origin", "main", env=seed_env, check_rc=True)
    return repo


def push(repo, branch, rc="0", log=None, extra=None):
    env = dict(fixture_env())
    env["STUB_CI_RC"] = rc
    env["CI_CALL_LOG"] = str(log) if log else "/dev/null"
    if extra:
        env.update(extra)
    return git(repo, "push", "origin", f"HEAD:refs/heads/{branch}", env=env)


def main():
    with tempfile.TemporaryDirectory(prefix="prepush-floor-") as td:
        tmp = pathlib.Path(td).resolve()
        repo = build(tmp)
        log = tmp / "calls.log"

        # 1. A GREEN floor lets the push through, and calls the fast subset.
        (repo / "a.txt").write_text("a\n")
        git(repo, "add", "a.txt", check_rc=True)
        git(repo, "commit", "-qm", "first", check_rc=True)
        r = push(repo, "feature-a", rc="0", log=log)
        check("a green floor allows the push", r.returncode == 0, r.stderr)
        calls = log.read_text() if log.exists() else ""
        check("the hook selected the fast subset, not the whole suite",
              "args:--only pushfloor,secret" in calls, calls)
        check("the hook did NOT run the full ten-class suite",
              "args:\n" not in calls, calls)

        # 2. THE ONE THAT MATTERS. A red floor must refuse, and the tee must not
        #    swallow the status.
        (repo / "b.txt").write_text("b\n")
        git(repo, "add", "b.txt", check_rc=True)
        git(repo, "commit", "-qm", "second", check_rc=True)
        r = push(repo, "feature-b", rc="1", log=log)
        check("a RED floor refuses the push (tee did not mask the status)",
              r.returncode != 0, f"exit {r.returncode}")
        check("the refusal explains the local/hosted split",
              "hosted" in (r.stdout + r.stderr).lower())
        pushed = git(repo, "ls-remote", "origin", "refs/heads/feature-b").stdout
        check("and nothing reached the remote", pushed.strip() == "", pushed)

        # 3. THE RANGE reaches ci.sh, and names the commits being pushed.
        log.write_text("")
        r = push(repo, "feature-b", rc="0", log=log)
        calls = log.read_text()
        check("the pushed range is exported to ci.sh",
              "range:" in calls and "<unset>" not in calls, calls)
        head = git(repo, "rev-parse", "HEAD").stdout.strip()
        check("the range ends at the commit being pushed",
              head[:12] in calls, calls)

        # 4. A SECOND PUSH of the same branch uses remote..local, not the whole
        #    branch: the incremental case is the common one.
        (repo / "c.txt").write_text("c\n")
        git(repo, "add", "c.txt", check_rc=True)
        git(repo, "commit", "-qm", "third", check_rc=True)
        log.write_text("")
        push(repo, "feature-b", rc="0", log=log)
        calls = log.read_text()
        check("an incremental push scopes to remote..local",
              f"range:{head}.." in calls, calls)

        # 5. THE OWNER CHECK ABOVE STILL SEES ITS REFS. Capturing stdin for the
        #    range is exactly the change that could have blinded it.
        env = dict(fixture_env())
        env.update(STUB_CI_RC="0", CI_CALL_LOG="/dev/null",
                   GIT_AUTHOR_EMAIL="stranger@example.invalid")
        r = git(repo, "-c", "user.email=stranger@example.invalid",
                "push", "origin", "HEAD:refs/heads/main", env=env)
        check("a non-owner push to main is still refused",
              r.returncode != 0, f"exit {r.returncode}")
        check("the owner refusal is the one that fired",
              "PUSH TO main REFUSED" in (r.stdout + r.stderr))

        # 6. The escape hatches still work.
        (repo / "d.txt").write_text("d\n")
        git(repo, "add", "d.txt", check_rc=True)
        git(repo, "commit", "-qm", "fourth", check_rc=True)
        log.write_text("")
        r = push(repo, "feature-d", rc="1", log=log,
                 extra={"CARR_SKIP_CI": "1"})
        check("CARR_SKIP_CI=1 still skips a red floor",
              r.returncode == 0, r.stderr)
        check("and the suite was genuinely not invoked",
              log.read_text().strip() == "", log.read_text())

    print(f"\n{passed} passed, {len(failures)} failed")
    if failures:
        print("FAILED: " + ", ".join(failures))
        return 1
    print("PRE-PUSH FLOOR SELFTEST PASSED: the hook runs the fast subset, "
          "exports the pushed range, still refuses a red floor and a non-owner "
          "push to main, and honours its escape hatch.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
