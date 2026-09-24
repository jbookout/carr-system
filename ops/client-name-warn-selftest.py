#!/usr/bin/env python3
# doctrine: runbook
"""Selftest for ops/githooks/client-name-warn.py, the warn-only pre-push note.

Hermetic: INVENTED names in a temp list, a throwaway git repository built
through ops/git_env.fixture_env, and a `main...feature` range standing in for
`origin/main...HEAD`. It proves:

  * a planted ADDED line is warned with file:line and a kind, never the name;
  * a name only on an unchanged (context) line is not reported;
  * a clean diff prints nothing;
  * with no local list the check is silent;
  * the exit code is 0 in every case, including a range git cannot resolve,
    and the pre-push hook discards it anyway.
"""
from __future__ import annotations

import io
import importlib.util
import os
import subprocess
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from git_env import fixture_env  # noqa: E402

ENV = fixture_env()
spec = importlib.util.spec_from_file_location("client_name_warn", HERE / "githooks" / "client-name-warn.py")
assert spec is not None and spec.loader is not None
warn = importlib.util.module_from_spec(spec)
spec.loader.exec_module(warn)

NAMES = ["Zebulon Quaxmire", "Glimmerstone Dental Arts"]
checks: list[tuple[str, bool]] = []


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, env=ENV,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def run(repo: Path, rng: str, names_file: Path | None) -> tuple[int, str]:
    saved = {k: os.environ.get(k) for k in (warn.NAMES_ENV, warn.ROSTER_ENV)}
    missing = str(repo.parent / "absent")
    os.environ[warn.NAMES_ENV] = str(names_file) if names_file else missing
    os.environ[warn.ROSTER_ENV] = missing
    out, err = io.StringIO(), io.StringIO()
    try:
        with redirect_stdout(out), redirect_stderr(err):
            rc = warn.main(["client-name-warn.py", rng, str(repo)])
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    return rc, out.getvalue() + err.getvalue()


with tempfile.TemporaryDirectory(prefix="client-name-warn-") as tmp:
    root = Path(tmp)
    names_file = root / "names.local.txt"
    names_file.write_text("\n".join(NAMES) + "\n")
    repo = root / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True, env=ENV)
    git(repo, "config", "user.email", "selftest@example.invalid")
    git(repo, "config", "user.name", "selftest")
    (repo / "old.md").write_text("line one\nmet Zebulon Quaxmire long ago\nline three\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "base")
    git(repo, "switch", "-q", "-c", "feature")

    (repo / "clean.md").write_text("a dentist in the panhandle\n")
    (repo / "old.md").write_text("line one\nmet Zebulon Quaxmire long ago\nline three, edited\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "clean change")
    rc, text = run(repo, "main...feature", names_file)
    checks.append(("a clean diff is silent (a name on an untouched line is not reported)",
                   rc == 0 and text == ""))

    (repo / "notes.md").write_text("intro\nfollow up with glimmerstone DENTAL arts friday\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "planted")
    rc, text = run(repo, "main...feature", names_file)
    checks.append(("a planted added line is warned with file:line and a kind",
                   rc == 0 and "notes.md:2" in text and "local list line 2" in text))
    checks.append(("the warning never prints the name",
                   "glimmerstone" not in text.lower() and "quaxmire" not in text.lower()))
    checks.append(("the warning carries the gradual-conversion note", "pseudonym" in text))

    rc, text = run(repo, "main...feature", None)
    checks.append(("no local list: silent, exit 0", rc == 0 and text == ""))

    rc, text = run(repo, "no-such-ref...feature", names_file)
    checks.append(("an unresolvable range: exit 0, nothing printed", rc == 0 and text == ""))

hook = (HERE / "githooks" / "pre-push").read_text()
checks.append(("the pre-push hook discards the note's exit status",
               'client-name-warn.py" "origin/main...HEAD" "$REPO_ROOT" || true' in hook))

failed = [label for label, ok in checks if not ok]
for label, ok in checks:
    print(f"  {'ok  ' if ok else 'FAIL'}  {label}")
print(f"client-name-warn-selftest: {len(checks) - len(failed)}/{len(checks)} passed")
sys.exit(1 if failed else 0)
