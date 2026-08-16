#!/usr/bin/env python3
"""Fixture for gate-integrity.py's install_repo() — the wiring comparison must
resolve to the checkout the installed adapters actually point at.

WHAT BROKE. Worktree-per-session is the default (rule 4a53ff82), and there is
only ever ONE installed wiring: ~/.claude/settings.json and ~/.codex/hooks.json
name absolute commands in the canonical checkout. gate-integrity.py rendered
{{REPO}} as its own tree, so from any worktree every expected command missed and
all 40 gates reported "found 0" — the full GATE INTEGRITY FAILURE banner on a
machine whose gates were exact. The banner instructs the session to tell Joe the
gates are off, so the default working tree manufactured a false alarm on the one
check guarding every other gate.

Both directions are pinned because the risk cuts both ways: resolve too eagerly
and a genuinely mis-wired machine reads as clean, which is the failure this
whole file exists to prevent.

Real git repositories are built in a tempdir rather than mocked, because the
resolution runs through `git rev-parse --git-common-dir` and a mock would test
the assumption instead of the behaviour.
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
GATE = os.path.join(os.path.dirname(HERE), "hooks", "gate-integrity.py")

spec = importlib.util.spec_from_file_location("gate_integrity", GATE)
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

failures: list[str] = []


def git(*args: str, cwd: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True,
                   capture_output=True, text=True)


def build_repo(root: str) -> str:
    """A real git repo with a hooks/ dir, which is what install_repo probes for."""
    os.makedirs(os.path.join(root, "hooks"), exist_ok=True)
    with open(os.path.join(root, "hooks", "some-gate.py"), "w") as fh:
        fh.write("# gate\n")
    git("init", "-q", cwd=root)
    git("config", "user.email", "t@example.com", cwd=root)
    git("config", "user.name", "t", cwd=root)
    git("add", "-A", cwd=root)
    git("commit", "-qm", "init", cwd=root)
    return root


with tempfile.TemporaryDirectory() as tmp:
    canonical = build_repo(os.path.join(tmp, "canonical"))

    # ------------------------------------------------------------ direction 1
    # The canonical checkout resolves to itself. If this ever changed, every
    # normal run would start comparing against the wrong tree.
    got = mod.install_repo(canonical)
    if os.path.realpath(got) != os.path.realpath(canonical):
        failures.append(f"canonical tree should resolve to itself, got {got}")

    # ------------------------------------------------------------ direction 2
    # A linked worktree resolves to the canonical checkout — the install target —
    # NOT to itself. This is the whole fix.
    wt = os.path.join(canonical, ".claude", "worktrees", "probe")
    os.makedirs(os.path.dirname(wt), exist_ok=True)
    git("worktree", "add", "-q", "-b", "probe", wt, cwd=canonical)
    got = mod.install_repo(wt)
    if os.path.realpath(got) != os.path.realpath(canonical):
        failures.append(f"worktree should resolve to the canonical tree, got {got}")
    if os.path.realpath(got) == os.path.realpath(wt):
        failures.append("worktree resolved to itself — the false-alarm bug is back")

    # ------------------------------------------------------------ direction 3
    # A directory git cannot answer for falls back to itself rather than raising
    # or resolving to something unrelated. A crashing integrity check is a check
    # nobody runs.
    plain = os.path.join(tmp, "not-a-repo")
    os.makedirs(os.path.join(plain, "hooks"), exist_ok=True)
    got = mod.install_repo(plain)
    if os.path.realpath(got) != os.path.realpath(plain):
        failures.append(f"non-repo should fall back to itself, got {got}")

    # ------------------------------------------------------------ direction 4
    # A resolution that does not look like a CARR checkout (no hooks/ directory)
    # is rejected in favour of the fallback, so a stray git dir cannot redirect
    # the comparison at a tree with no gates in it.
    bare = build_repo(os.path.join(tmp, "no-hooks"))
    bare_wt = os.path.join(tmp, "no-hooks-wt")
    git("worktree", "add", "-q", "-b", "probe2", bare_wt, cwd=bare)
    # Strip the marker AFTER the worktree exists, so git still resolves the
    # common dir but the result no longer looks like a CARR checkout.
    for name in os.listdir(os.path.join(bare, "hooks")):
        os.remove(os.path.join(bare, "hooks", name))
    os.rmdir(os.path.join(bare, "hooks"))
    got = mod.install_repo(bare_wt)
    if os.path.realpath(got) != os.path.realpath(bare_wt):
        failures.append(
            f"resolution without a hooks/ dir should fall back to the caller, got {got}")

if failures:
    print("gate-integrity worktree selftest FAILED")
    for f in failures:
        print("  " + f)
    sys.exit(1)
print("gate-integrity worktree selftest passed")
